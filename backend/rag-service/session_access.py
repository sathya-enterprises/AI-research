"""Who may use an uploaded-document session (RAG service side).

The backend tells the service who is asking in ``X-RAG-Principal``: ``user:<verified Supabase user id>`` or
``anon``. The header is trusted only on requests that carry the backend's shared secret (main.py rejects
everything else), and the backend sets it only from a verified access token.

Ownership is recorded in the existing ``rag_chunks.source_metadata`` JSON (key ``gg_owner``) of every chunk
of the session - no new table:

* a session's owner is the distinct ``gg_owner`` values of its chunks: one ``user:<id>`` -> that user;
  ``anon`` and/or none (chunks written before ownership existed) -> an anonymous session; no chunks -> no
  session; two different users -> a conflict, refused to everyone (fail closed);
* a signed-in user's session is usable only by that user; an anonymous (or legacy) session by whoever holds its
  id - the browser's random UUID is the credential, as before;
* the first upload stamps its principal; a signed-in user uploading into an anonymous session moves it to their
  account. A user-owned session is never moved: another account (or an anonymous caller) is refused.

Moving ownership is atomic when the service can reach Postgres directly (RESEARCH_DATABASE_URL): one transaction
takes a per-session advisory lock and flips only rows still anonymous, so of two concurrent claims exactly one
wins. Through the Supabase REST API it is done row by row, each row conditional on still being anonymous; a race
between two accounts there can at worst leave both owners recorded, which is a conflict and refused to both.
"""
from __future__ import annotations

import json
import re
from typing import Iterable, Optional

OWNER_KEY = "gg_owner"
ANON = "anon"
_USER_RE = re.compile(r"^user:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

NONE, ANONYMOUS, USER, CONFLICT = "none", "anonymous", "user", "conflict"


def parse_principal(value: object) -> str:
    text = str(value or "").strip()
    return text if _USER_RE.match(text) else ANON


def effective_owner(values: Iterable[Optional[str]]) -> tuple[str, Optional[str]]:
    """(state, owner) from the ``gg_owner`` values of a session's chunks (None for a chunk without one)."""
    values = list(values)
    if not values:
        return NONE, None
    users = {v for v in values if v and v.startswith("user:")}
    if len(users) > 1:
        return CONFLICT, None
    if users:
        return USER, next(iter(users))
    return ANONYMOUS, ANON


def may_read(state: str, owner: Optional[str], principal: str) -> bool:
    """Query, report, info, download and delete. A nonexistent session is not readable (callers answer it and
    a refused one identically)."""
    if state == USER:
        return principal == owner
    return state == ANONYMOUS


def index_plan(state: str, owner: Optional[str], principal: str) -> str:
    """'stamp' (write chunks with the caller as owner), 'migrate' (move an anonymous session to the signed-in
    caller first) or 'deny'."""
    if state == NONE:
        return "stamp"
    if state == ANONYMOUS:
        return "migrate" if principal != ANON else "stamp"
    if state == USER:
        return "stamp" if principal == owner else "deny"
    return "deny"


# ── storage ──────────────────────────────────────────────────────────────────────────────────────────────
def _store():
    import pgvector_store
    return pgvector_store


def _direct_db() -> bool:
    return bool(getattr(_store(), "_DATABASE_URL", ""))


def read_owners(session_id: str) -> list[Optional[str]]:
    """The distinct gg_owner values of the session's chunks ([] when it has none)."""
    store = _store()
    if _direct_db():
        with store._database_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT DISTINCT source_metadata->>%s FROM public.rag_chunks WHERE session_id = %s",
                               (OWNER_KEY, str(session_id)))
                return [row[0] for row in cursor.fetchall()]
    owners: set = set()
    start, page = 0, 1000
    while True:
        response = store._request("GET", store._TABLE, params={
            "select": f"owner:source_metadata->>{OWNER_KEY}", "session_id": f"eq.{session_id}", "limit": str(page)},
            range_header=f"{start}-{start + page - 1}")
        batch = response.json()
        if not isinstance(batch, list):
            raise store.PgVectorStoreError("invalid ownership response")
        owners.update(row.get("owner") if isinstance(row, dict) else None for row in batch)
        if len(batch) < page:
            break
        start += page
    return list(owners)


def migrate_to(session_id: str, principal: str) -> tuple[str, Optional[str]]:
    """Move an anonymous session to ``principal`` (a verified user). Returns the owner state afterwards; the
    caller proceeds only if it is (USER, principal)."""
    if not _USER_RE.match(principal):
        raise ValueError("only a verified user can take ownership")
    store = _store()
    if _direct_db():
        with store._database_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"gg_rag_owner:{session_id}",))
                cursor.execute("SELECT DISTINCT source_metadata->>%s FROM public.rag_chunks WHERE session_id = %s",
                               (OWNER_KEY, str(session_id)))
                state, owner = effective_owner([row[0] for row in cursor.fetchall()])
                if state == ANONYMOUS:
                    cursor.execute(
                        """UPDATE public.rag_chunks
                           SET source_metadata = jsonb_set(coalesce(source_metadata, '{}'::jsonb), %s, to_jsonb(%s::text))
                           WHERE session_id = %s AND coalesce(source_metadata->>%s, %s) = %s""",
                        ("{" + OWNER_KEY + "}", principal, str(session_id), OWNER_KEY, ANON, ANON))
                    cursor.execute("SELECT DISTINCT source_metadata->>%s FROM public.rag_chunks WHERE session_id = %s",
                                   (OWNER_KEY, str(session_id)))
                    state, owner = effective_owner([row[0] for row in cursor.fetchall()])
                return state, owner
    # REST: each row is changed only if it is still anonymous at that moment
    start, page = 0, 500
    rows: list[dict] = []
    while True:
        response = store._request("GET", store._TABLE, params={
            "select": "chunk_id,source_metadata", "session_id": f"eq.{session_id}", "limit": str(page)},
            range_header=f"{start}-{start + page - 1}")
        batch = response.json() if isinstance(response.json(), list) else []
        rows.extend(r for r in batch if isinstance(r, dict))
        if len(batch) < page:
            break
        start += page
    for row in rows:
        meta = row.get("source_metadata") if isinstance(row.get("source_metadata"), dict) else {}
        current = meta.get(OWNER_KEY)
        if current not in (None, ANON):
            continue
        guard = "is.null" if current is None else f"eq.{ANON}"
        store._request("PATCH", store._TABLE, params={
            "session_id": f"eq.{session_id}", "chunk_id": f"eq.{row.get('chunk_id')}",
            f"source_metadata->>{OWNER_KEY}": guard},
            json={"source_metadata": {**meta, OWNER_KEY: principal}}, prefer="return=minimal")
    return effective_owner(read_owners(session_id))


def stamp_documents(documents: list[dict], owner: str) -> list[dict]:
    """Copy of the documents with the owner in each document's metadata (it becomes every chunk's
    source_metadata.gg_owner)."""
    out = []
    for doc in documents:
        meta = dict(doc.get("metadata") or {})
        meta[OWNER_KEY] = owner
        out.append({**doc, "metadata": meta})
    return out


def dumps_owner(owner: str) -> str:  # for tests and diagnostics: never includes the session id
    return json.dumps({OWNER_KEY: owner})


def tag(session_id: object) -> str:
    """A one-way tag for logs - never the session id or a prefix of it."""
    import hashlib
    return "s:" + hashlib.sha256(f"gg-rag-session:{session_id}".encode("utf-8")).hexdigest()[:10]
