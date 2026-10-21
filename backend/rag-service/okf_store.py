"""
OKF (Open Knowledge Format) storage layer for Paperly — Supabase Storage backed.

Paperly's rag-service runs on Hugging Face Spaces, which has NO durable disk
by default (the container filesystem resets on every restart/redeploy). The
FAISS vector index is already in-memory-only and accepted as session-scoped,
but OKF concept files are meant to be a durable, inspectable record of what
was indexed — so they're written to Supabase Storage (the same project/bucket
already used elsewhere in this codebase for uploaded chat images) instead of
local disk.

Bucket layout (bucket: paperly-uploads, same bucket the backend already
writes chat images to — see backend/routes/chat.py _store_image_extractions):
  paperly-okf/<session_id>/bundle.md
  paperly-okf/<session_id>/documents/<slug>-<doc_id>.md

Frontmatter schema matches backend/utils/okf.py (Google Cloud OKF v0.1):
  type, title, description, resource, tags, timestamp

If SUPABASE_URL / SUPABASE_ANON_KEY aren't configured, every function here
degrades to a no-op (logged once) so indexing/retrieval still works locally
or in dev without Supabase configured — only the OKF persistence is skipped.
"""
import hashlib
import json
import os
import re
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx

log = logging.getLogger("rag-okf")

_SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
_SUPABASE_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
_BUCKET = os.environ.get("OKF_SUPABASE_BUCKET", "paperly-uploads")
_PRIVATE_BUCKET = os.environ.get("OKF_PRIVATE_BUCKET", "rag-knowledge-private")
_SERVICE_ROLE_KEY = (
    os.environ.get("SUPABASE_SECRET_KEY", "").strip()
    or os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
)
_ALLOW_LEGACY_PUBLIC_WRITES = os.environ.get("OKF_ALLOW_LEGACY_PUBLIC_WRITES", "0").strip().lower() in {"1", "true", "yes", "on"}
_PREFIX = "paperly-okf"

_warned_unconfigured = False


def _configured() -> bool:
    global _warned_unconfigured
    if _SUPABASE_URL and _SUPABASE_KEY:
        return True
    if not _warned_unconfigured:
        log.warning(
            "OKF: SUPABASE_URL/SUPABASE_ANON_KEY not set — OKF bundle "
            "persistence disabled (FAISS indexing/retrieval still works)."
        )
        _warned_unconfigured = True
    return False


def _private_storage_configured() -> bool:
    return bool(_SUPABASE_URL and _PRIVATE_BUCKET and _SERVICE_ROLE_KEY)


def storage_status() -> Dict[str, Any]:
    """Return safe configuration state without exposing credential values."""
    private_ready = _private_storage_configured()
    legacy_ready = bool(_configured() and _ALLOW_LEGACY_PUBLIC_WRITES)
    mode = "private" if private_ready else "legacy_public" if legacy_ready else "read_only_legacy"
    return {
        "readAvailable": bool(_SUPABASE_URL and _SUPABASE_KEY),
        "writeAvailable": private_ready or legacy_ready,
        "mode": mode,
        "privateBucketConfigured": bool(_PRIVATE_BUCKET),
        "privateBucketReachable": None,
        "privateBucketStatus": "not_checked",
        "serverCredentialConfigured": bool(_SERVICE_ROLE_KEY),
        "legacyPublicWritesEnabled": _ALLOW_LEGACY_PUBLIC_WRITES,
        "legacyPublicBucket": _BUCKET,
        "privateBucket": _PRIVATE_BUCKET,
        "warning": None if private_ready else "Private OKF writes require SUPABASE_SECRET_KEY (or legacy SUPABASE_SERVICE_ROLE_KEY) and the private bucket; existing legacy resources remain readable.",
    }


def _is_jwt_credential(key: str) -> bool:
    """Return true only for legacy JWT-shaped Supabase keys.

    New ``sb_secret_`` and ``sb_publishable_`` keys are API keys, not JWTs,
    and must never be copied into Authorization as bearer tokens.
    """
    return key.startswith("eyJ") and key.count(".") == 2


def _headers(
    key: str,
    content_type: str = "application/json",
    *,
    bearer_token: Optional[str] = None,
) -> dict:
    """Build Storage headers without treating modern API keys as JWTs.

    A legacy JWT is accepted as both apikey and bearer for backwards
    compatibility. Callers may provide a separate legacy bearer token when
    using a modern apikey; modern secret keys themselves are never bearer
    credentials.
    """
    headers = {"apikey": key, "Content-Type": content_type}
    if bearer_token and _is_jwt_credential(bearer_token):
        headers["Authorization"] = f"Bearer {bearer_token}"
    elif _is_jwt_credential(key):
        headers["Authorization"] = f"Bearer {key}"
    return headers


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slugify(text: str, fallback: str = "doc") -> str:
    s = (text or "").strip().lower()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s).strip("-")
    return s[:80] or fallback


def _yaml_scalar(v: Any) -> str:
    if v is None:
        return '""'
    s = str(v).replace('"', '\\"').replace("\n", " ").strip()
    return f'"{s}"'


def _frontmatter(fields: Dict[str, Any]) -> str:
    lines = ["---", f"type: {_yaml_scalar(fields.get('type', 'concept'))}"]
    for key in (
        "title", "description", "resource", "timestamp", "document_id",
        "source_sha256", "content_chars", "content_truncated", "storage_version",
        "source_type", "file_type",
    ):
        val = fields.get(key)
        if val:
            lines.append(f"{key}: {_yaml_scalar(val)}")
    tags = fields.get("tags")
    if tags:
        lines.append("tags:")
        for t in tags:
            if t:
                lines.append(f"  - {_yaml_scalar(t)}")
    lines.append("---")
    return "\n".join(lines)


async def _put_object(
    path: str,
    text: str,
    *,
    bucket: Optional[str] = None,
    key: Optional[str] = None,
) -> bool:
    """Upload one object; an RLS denial never triggers a destructive delete."""
    target_bucket = bucket or _BUCKET
    credential = key if key is not None else _SUPABASE_KEY
    if not _SUPABASE_URL or not target_bucket or not credential:
        return False
    url = f"{_SUPABASE_URL}/storage/v1/object/{target_bucket}/{path}"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                url,
                headers={**_headers(credential, "text/markdown; charset=utf-8"), "x-upsert": "true"},
                content=text.encode("utf-8"),
            )
            if resp.is_success:
                return True
            category = _storage_error_class(resp.status_code, resp.text)
            log.warning("OKF: %s upload rejected (HTTP %d, class=%s); object was not deleted", category, resp.status_code, category)
            return False
    except Exception as exc:
        log.warning("OKF: Storage upload failed (%s); object contents and credentials omitted", type(exc).__name__)
        return False


async def _get_object(
    path: str,
    *,
    bucket: Optional[str] = None,
    key: Optional[str] = None,
) -> Optional[bytes]:
    target_bucket = bucket or _BUCKET
    credential = key if key is not None else _SUPABASE_KEY
    if not _SUPABASE_URL or not target_bucket or not credential:
        return None
    url = f"{_SUPABASE_URL}/storage/v1/object/{target_bucket}/{path}"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                url,
                headers=_headers(credential),
            )
            if resp.is_success:
                return resp.content
            log.warning("OKF: Storage read failed (HTTP %d, class=%s)", resp.status_code, _storage_error_class(resp.status_code, resp.text))
            return None
    except Exception as exc:
        log.warning("OKF: Storage read failed (%s); object path and credentials omitted", type(exc).__name__)
        return None


async def _list_objects(
    prefix: str,
    *,
    bucket: Optional[str] = None,
    key: Optional[str] = None,
) -> List[dict]:
    """List objects under a prefix via Supabase Storage's list endpoint."""
    target_bucket = bucket or _BUCKET
    credential = key if key is not None else _SUPABASE_KEY
    if not _SUPABASE_URL or not target_bucket or not credential:
        return []
    url = f"{_SUPABASE_URL}/storage/v1/object/list/{target_bucket}"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                url,
                headers={
                    **_headers(credential),
                },
                json={"prefix": prefix, "limit": 1000},
            )
            if resp.is_success:
                return resp.json()
            log.warning("OKF: Storage list failed (HTTP %d, class=%s); prefix omitted", resp.status_code, _storage_error_class(resp.status_code, resp.text))
            return []
    except Exception as exc:
        log.warning("OKF: Storage list failed (%s); prefix and credentials omitted", type(exc).__name__)
        return []


async def _delete_objects(paths: List[str], *, bucket: Optional[str] = None, key: Optional[str] = None) -> None:
    target_bucket = bucket or _BUCKET
    credential = key if key is not None else _SUPABASE_KEY
    if not _SUPABASE_URL or not target_bucket or not credential or not paths:
        return
    url = f"{_SUPABASE_URL}/storage/v1/object/{target_bucket}"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.request(
                "DELETE",
                url,
                headers={
                    **_headers(credential),
                },
                json={"prefixes": paths},
            )
            if not resp.is_success:
                log.warning("OKF: Storage delete rejected (HTTP %d)", resp.status_code)
    except Exception as exc:
        log.warning("OKF: Storage delete failed (%s)", type(exc).__name__)


# ── Public API ──────────────────────────────────────────────────────────────

async def write_document_concept(session_id: str, doc: Dict[str, Any]) -> Optional[str]:
    """Write one OKF concept file for an indexed document to Supabase Storage.

    Returns the relative path (within the session's OKF folder) of the
    written concept, or None if the doc had no usable text or the upload
    failed (callers should treat this as non-fatal — FAISS indexing
    already succeeded by the time this is called).
    """
    doc_id = str(doc.get("id", "")).strip()
    name = doc.get("name", "Unknown")
    text = (doc.get("text") or "").strip()
    if not doc_id or not text:
        return None

    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    private_write = _private_storage_configured()
    legacy_write = bool(_configured() and _ALLOW_LEGACY_PUBLIC_WRITES)
    if not private_write and not legacy_write:
        log.warning("OKF: source persistence disabled; configure the private bucket and server-side Storage credential")
        return None
    excerpt = text if private_write else text[:6000].rstrip()
    content_truncated = len(excerpt) < len(text)
    if content_truncated:
        excerpt += "\n\n…[truncated legacy public preview; full source is not stored in this bucket]"

    fm = _frontmatter({
        "type": "source-document",
        "title": name,
        "description": f"Indexed document: {name}",
        "resource": f"document/{doc_id}",
        "document_id": doc_id,
        "source_sha256": digest,
        "content_chars": len(text),
        "content_truncated": str(content_truncated).lower(),
        "storage_version": "2",
        "source_type": str(doc.get("source_type") or "file"),
        "file_type": str(doc.get("file_type") or ""),
        "tags": ["source-document", "paperly", doc.get("file_type", "")],
        "timestamp": _now_iso(),
    })
    safe_id = hashlib.sha256(doc_id.encode("utf-8")).hexdigest()[:20]
    slug = _slugify(name, safe_id)
    rel_path = f"documents/{slug}-{safe_id}.md"
    full_path = f"{_PREFIX}/{session_id}/{rel_path}"
    body = f"## Extracted Content\n\n{excerpt}\n"

    target_bucket = _PRIVATE_BUCKET if private_write else _BUCKET
    credential = _SERVICE_ROLE_KEY if private_write else _SUPABASE_KEY
    ok = await _put_object(full_path, f"{fm}\n\n{body}", bucket=target_bucket, key=credential)
    return rel_path if ok else None


async def write_bundle_manifest(session_id: str, doc_concepts: List[Dict[str, str]]) -> bool:
    """(Re)write the session-level bundle.md that links every document concept."""
    links = "\n".join(f"- [{d['name']}]({d['rel_path']})" for d in doc_concepts)
    fm = _frontmatter({
        "type": "concept-bundle",
        "title": f"Paperly Session {session_id[:8]}",
        "description": "OKF bundle of all documents indexed in this Paperly session.",
        "tags": ["paperly", "session-bundle"],
        "timestamp": _now_iso(),
    })
    body = f"## Documents\n\n{links}\n" if links else "## Documents\n\n_No documents indexed yet._\n"
    private_write = _private_storage_configured()
    legacy_write = bool(_configured() and _ALLOW_LEGACY_PUBLIC_WRITES)
    if not private_write and not legacy_write:
        return False
    return await _put_object(
        f"{_PREFIX}/{session_id}/bundle.md", f"{fm}\n\n{body}",
        bucket=_PRIVATE_BUCKET if private_write else _BUCKET,
        key=_SERVICE_ROLE_KEY if private_write else _SUPABASE_KEY,
    )


def _storage_error_class(status_code: int, response_text: str = "") -> str:
    """Classify Storage failures without echoing provider response bodies."""
    body = (response_text or "").lower()
    if status_code in (401, 403) or "row-level security" in body or "permission" in body:
        return "authentication_or_permission"
    if status_code == 404:
        return "bucket_or_object_not_found"
    if status_code == 429:
        return "rate_limited"
    if status_code >= 500:
        return "provider_error"
    return "storage_error"


def _parse_frontmatter_document(raw: bytes, rel_path: str) -> Optional[Dict[str, Any]]:
    text = raw.decode("utf-8", "replace")
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end < 0:
        return None
    metadata: Dict[str, Any] = {}
    for line in text[3:end].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        value = value.strip()
        if value.startswith('"') and value.endswith('"'):
            try:
                value = json.loads(value)
            except Exception:
                value = value[1:-1].replace('\\"', '"')
        metadata[key.strip()] = value
    body = text[end + 4:].strip()
    body = re.sub(r"^\[.*?\]\(\.\./bundle\.md\)\s*", "", body, flags=re.S).strip()
    body = re.sub(r"^## Extracted Content\s*", "", body, flags=re.I).strip()
    truncated = str(metadata.get("content_truncated") or "").lower() in {"true", "1", "yes"} or "[truncated" in body.lower()
    if "\n\n…[truncated" in body:
        body = body.split("\n\n…[truncated", 1)[0].rstrip()
    document_id = str(metadata.get("document_id") or "").strip()
    if not document_id:
        basename = rel_path.rsplit("/", 1)[-1]
        suffix = re.search(r"-([a-f0-9]{8,64})\.md$", basename, re.I)
        document_id = f"legacy:{suffix.group(1).lower() if suffix else hashlib.sha256(rel_path.encode()).hexdigest()[:20]}"
    if len(body) < 20:
        return None
    return {
        "id": document_id,
        "name": str(metadata.get("title") or "Restored OKF document"),
        "text": body,
        "source_type": str(metadata.get("source_type") or "uploaded_document"),
        "file_type": str(metadata.get("file_type") or ""),
        "metadata": {
            "okf_path": rel_path,
            "source_sha256": str(metadata.get("source_sha256") or ""),
            "content_chars": int(metadata.get("content_chars") or len(body)) if str(metadata.get("content_chars") or "").isdigit() else len(body),
            "content_truncated": truncated,
            "storage_version": str(metadata.get("storage_version") or "legacy"),
            "timestamp": str(metadata.get("timestamp") or ""),
        },
    }


async def _read_documents_from_bucket(session_id: str, bucket: str, key: str) -> List[Dict[str, Any]]:
    prefix = f"{_PREFIX}/{session_id}/documents"
    entries = await _list_objects(prefix, bucket=bucket, key=key)
    documents: List[Dict[str, Any]] = []
    for entry in entries:
        name = str(entry.get("name") or "")
        if not name.lower().endswith(".md"):
            continue
        relative_path = f"{prefix}/{name}"
        raw = await _get_object(relative_path, bucket=bucket, key=key)
        if not raw:
            continue
        parsed = _parse_frontmatter_document(raw, relative_path)
        if parsed:
            documents.append(parsed)
    return documents


async def list_session_ids() -> List[str]:
    """List only session folder identifiers; never log or return source titles."""
    session_ids: set[str] = set()
    candidates = []
    if _private_storage_configured():
        candidates.append((_PRIVATE_BUCKET, _SERVICE_ROLE_KEY))
    if _configured():
        candidates.append((_BUCKET, _SUPABASE_KEY))
    for bucket, key in candidates:
        entries = await _list_objects(_PREFIX, bucket=bucket, key=key)
        for entry in entries:
            name = str(entry.get("name") or "").strip()
            if name and entry.get("id") is None and "/" not in name:
                session_ids.add(name)
    return sorted(session_ids)


async def load_session_documents(session_id: str) -> List[Dict[str, Any]]:
    """Load private resources first, then fill missing identities from legacy OKF."""
    by_id: Dict[str, Dict[str, Any]] = {}
    if _private_storage_configured():
        for doc in await _read_documents_from_bucket(session_id, _PRIVATE_BUCKET, _SERVICE_ROLE_KEY):
            by_id[doc["id"]] = doc
    if _configured():
        for doc in await _read_documents_from_bucket(session_id, _BUCKET, _SUPABASE_KEY):
            by_id.setdefault(doc["id"], doc)
    # A session can contain several uploaded versions with the same filename.
    # Restore only the most recently stored version of each name for retrieval.
    latest: Dict[str, Dict[str, Any]] = {}
    for doc in by_id.values():
        name_key = re.sub(r"[^a-z0-9]+", "", str(doc.get("name") or "").casefold())
        prior = latest.get(name_key)
        if prior is None or str((doc.get("metadata") or {}).get("timestamp") or "") >= str((prior.get("metadata") or {}).get("timestamp") or ""):
            latest[name_key] = doc
    return list(latest.values())


async def get_session_bundle_files(session_id: str) -> List[Tuple[str, bytes]]:
    """Fetch every OKF file for a session as (relative_path, bytes) — used to
    build a downloadable zip on demand."""
    prefix = f"{_PREFIX}/{session_id}"
    out: List[Tuple[str, bytes]] = []

    candidates = []
    if _private_storage_configured():
        candidates.append((_PRIVATE_BUCKET, _SERVICE_ROLE_KEY))
    if _configured():
        candidates.append((_BUCKET, _SUPABASE_KEY))
    seen: set[str] = set()
    for bucket, key in candidates:
        bundle_bytes = await _get_object(f"{prefix}/bundle.md", bucket=bucket, key=key)
        if bundle_bytes and "bundle.md" not in seen:
            out.append(("bundle.md", bundle_bytes)); seen.add("bundle.md")
        doc_entries = await _list_objects(f"{prefix}/documents", bucket=bucket, key=key)
        for entry in doc_entries:
            name = str(entry.get("name") or "")
            if not name.lower().endswith(".md") or name in seen:
                continue
            content = await _get_object(f"{prefix}/documents/{name}", bucket=bucket, key=key)
            if content:
                out.append((f"documents/{name}", content)); seen.add(name)

    return out


async def delete_session_bundle(session_id: str) -> None:
    """Remove all OKF concept files for a session from Supabase Storage
    (mirrors FAISS index deletion on session cleanup)."""
    prefix = f"{_PREFIX}/{session_id}"
    # This helper is intentionally explicit and is not called by normal chat
    # cleanup. Persisted knowledge is not deleted when an in-memory session ends.
    paths = [f"{prefix}/bundle.md"]
    doc_entries = await _list_objects(f"{prefix}/documents")
    for entry in doc_entries:
        name = entry.get("name", "")
        if name:
            paths.append(f"{prefix}/documents/{name}")
    await _delete_objects(paths)
