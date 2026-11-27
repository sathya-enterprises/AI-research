"""RAG service security: shared-secret authentication, session ownership, anonymous sessions, anonymous-to-user
migration, takeover refusal, concurrency, legacy sessions and log hygiene.

The real main.py endpoints run against an in-memory engine and chunk store: no model, no database."""
import asyncio
import importlib.util
import logging
import sys
import threading
import types
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

TOKEN = "test-only-service-secret-0123456789"
USER_A = "user:" + str(uuid.UUID(int=1, version=4))
USER_B = "user:" + str(uuid.UUID(int=2, version=4))


class FakeStore:
    """chunks[session] = list of gg_owner values (one per chunk; None = legacy chunk without an owner)."""

    def __init__(self):
        self.chunks: dict[str, list] = {}
        self.lock = threading.Lock()


class FakeEngine:
    _model = object()
    _dim = 384
    _reranker = None
    reranker_ready = False
    model_name = "stub-model"

    def __init__(self, store: FakeStore):
        self.store = store
        self.docs: dict[str, list[str]] = {}

    def list_sessions(self):
        return list(self.store.chunks)

    async def ensure_session_loaded(self, sid):
        return "restored_from_pgvector" if self.store.chunks.get(sid) else "empty_or_unavailable"

    async def index(self, sid, documents):
        with self.store.lock:
            for doc in documents:
                self.store.chunks.setdefault(sid, []).append((doc.get("metadata") or {}).get("gg_owner"))
                self.docs.setdefault(sid, []).append(doc["text"])
        return {"chunks_added": len(documents), "total_chunks": len(self.store.chunks[sid])}

    def _result(self, sid):
        texts = self.docs.get(sid, [])
        return {"has_content": bool(texts), "context": "\n".join(texts), "retrieved": len(texts),
                "source_files": ["doc.txt"] if texts else [], "system_prompt": "p", "diagnostics": {}}

    def retrieve(self, session_id, question, top_k, min_score):
        return self._result(session_id)

    def retrieve_for_report(self, session_id, report_spec, report_type):
        return self._result(session_id)

    def get_session_info(self, sid):
        n = len(self.store.chunks.get(sid, []))
        return {"session_id": sid, "chunk_count": n, "exists": bool(n)} if n else {"session_id": sid, "chunk_count": 0, "exists": False}

    async def delete_session(self, sid):
        self.store.chunks.pop(sid, None)
        self.docs.pop(sid, None)


def _load_main(monkeypatch, store, **env):
    for key in ("RAG_SERVICE_TOKEN", "RAG_ALLOW_UNAUTHENTICATED"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    async def bundle(sid):
        return [("doc.md", b"text")] if store.chunks.get(sid) else []
    # main.py loads .env files into the process environment; tests must never pick up a real local secret
    monkeypatch.setitem(sys.modules, "dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None,
                                                                     dotenv_values=lambda *a, **k: {}))
    monkeypatch.setitem(sys.modules, "rag_engine", types.SimpleNamespace(RAGEngine=lambda: FakeEngine(store)))
    monkeypatch.setitem(sys.modules, "okf_store", types.SimpleNamespace(get_session_bundle_files=bundle,
                                                                      storage_status=lambda: {}))
    monkeypatch.setitem(sys.modules, "pgvector_store", types.SimpleNamespace(
        storage_backend=lambda: "stub", health_check=lambda: {"ready": True}))
    here = Path(__file__).parent
    monkeypatch.syspath_prepend(str(here))
    access_spec = importlib.util.spec_from_file_location("session_access", here / "session_access.py")
    access = importlib.util.module_from_spec(access_spec)
    access_spec.loader.exec_module(access)
    monkeypatch.setitem(sys.modules, "session_access", access)

    def read_owners(sid):
        with store.lock:
            return sorted(set(store.chunks.get(sid, [])), key=str)

    def migrate_to(sid, principal):  # atomic like the SQL path: flip only rows still anonymous
        with store.lock:
            state, _ = access.effective_owner(set(store.chunks.get(sid, [])))
            if state == access.ANONYMOUS:
                store.chunks[sid] = [principal if o in (None, "anon") else o for o in store.chunks[sid]]
            return access.effective_owner(set(store.chunks.get(sid, [])))

    monkeypatch.setattr(access, "read_owners", read_owners)
    monkeypatch.setattr(access, "migrate_to", migrate_to)
    spec = importlib.util.spec_from_file_location("rag_service_main_security", here / "main.py")
    main = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(main)
    return main


@pytest.fixture()
def svc(monkeypatch):
    store = FakeStore()
    main = _load_main(monkeypatch, store, RAG_SERVICE_TOKEN=TOKEN)
    return TestClient(main.app), store, main


def H(principal="anon", token=TOKEN):
    headers = {"X-RAG-Principal": principal}
    if token is not None:
        headers["X-RAG-Service-Token"] = token
    return headers


def sid():
    return str(uuid.uuid4())


def index(http, session, principal="anon", text="Electric two-wheeler registrations rose 20% in FY25."):
    return http.post("/index", json={"session_id": session, "documents": [{"id": "d1", "name": "doc.txt", "text": text}]},
                     headers=H(principal))


def query(http, session, principal="anon"):
    return http.post("/query", json={"session_id": session, "question": "registrations"}, headers=H(principal))


# ── service authentication ───────────────────────────────────────────────────────────────────────────────
def test_missing_or_incorrect_service_token_is_refused_and_the_correct_one_accepted(svc):
    http, _, _ = svc
    s = sid()
    for path, method, body in (("/query", "post", {"session_id": s, "question": "q"}), ("/index", "post", {"session_id": s, "documents": []}),
                               (f"/session/{s}/info", "get", None), (f"/session/{s}/okf", "get", None),
                               (f"/session/{s}", "delete", None), ("/diagnostics", "get", None), ("/rerank", "post", {"query": "q", "passages": []}),
                               ("/docs", "get", None)):
        call = getattr(http, method)
        kwargs = {"json": body} if body is not None else {}
        assert call(path, headers={"X-RAG-Principal": "anon"}, **kwargs).status_code == 401, path
        assert call(path, headers=H(token="wrong-secret"), **kwargs).status_code == 401, path
    assert query(http, s).status_code == 200


def test_without_a_configured_token_the_service_fails_closed(monkeypatch):
    main = _load_main(monkeypatch, FakeStore())
    http = TestClient(main.app)
    assert http.post("/query", json={"session_id": sid(), "question": "q"}, headers=H(token="anything")).status_code == 503
    assert http.get("/ping").status_code == 200                                          # liveness still answers


def test_local_development_can_opt_out_explicitly(monkeypatch):
    main = _load_main(monkeypatch, FakeStore(), RAG_ALLOW_UNAUTHENTICATED="1")
    http = TestClient(main.app)
    assert http.post("/query", json={"session_id": sid(), "question": "q"}).status_code == 200


def test_public_health_endpoints_expose_nothing_sensitive(svc):
    http, store, _ = svc
    index(http, sid())
    ping = http.get("/ping").json()
    health = http.get("/health").json()
    assert ping == {"pong": True}
    assert set(health) == {"status", "ready"}
    detailed = http.get("/health", headers=H()).json()                                   # the backend, with the secret
    assert "active_sessions" in detailed and "model" in detailed


# ── ownership ────────────────────────────────────────────────────────────────────────────────────────────
def test_a_signed_in_user_uses_their_own_session(svc):
    http, store, _ = svc
    s = sid()
    assert index(http, s, USER_A).status_code == 200 and store.chunks[s] == [USER_A]
    assert query(http, s, USER_A).json()["has_content"] is True
    assert http.post("/report", json={"session_id": s, "report_spec": "x"}, headers=H(USER_A)).json()["has_content"]
    assert http.get(f"/session/{s}/info", headers=H(USER_A)).json()["chunk_count"] == 1
    assert http.get(f"/session/{s}/okf", headers=H(USER_A)).status_code == 200
    assert http.delete(f"/session/{s}", headers=H(USER_A)).status_code == 200 and s not in store.chunks


@pytest.mark.parametrize("intruder", [USER_B, "anon"])
def test_another_caller_cannot_read_query_download_or_delete_and_sees_not_found(svc, intruder):
    http, store, _ = svc
    owned, missing = sid(), sid()
    index(http, owned, USER_A)
    q_owned, q_missing = query(http, owned, intruder), query(http, missing, intruder)
    strip = lambda r: {k: v for k, v in r.json().items() if k not in {"session_id", "elapsed_ms"}}
    assert q_owned.status_code == q_missing.status_code == 200 and strip(q_owned) == strip(q_missing)
    assert q_owned.json()["has_content"] is False and q_owned.headers.get("x-rag-lookup") == "miss"
    r_owned = http.post("/report", json={"session_id": owned, "report_spec": "x"}, headers=H(intruder))
    r_missing = http.post("/report", json={"session_id": missing, "report_spec": "x"}, headers=H(intruder))
    assert strip(r_owned) == strip(r_missing)
    i_owned, i_missing = (http.get(f"/session/{x}/info", headers=H(intruder)).json() for x in (owned, missing))
    assert {k: v for k, v in i_owned.items() if k != "session_id"} == {k: v for k, v in i_missing.items() if k != "session_id"}
    d_owned, d_missing = (http.get(f"/session/{x}/okf", headers=H(intruder)) for x in (owned, missing))
    assert d_owned.status_code == d_missing.status_code == 404 and d_owned.json() == d_missing.json()
    x_owned, x_missing = (http.delete(f"/session/{x}", headers=H(intruder)) for x in (owned, missing))
    assert x_owned.status_code == x_missing.status_code == 404 and x_owned.json() == x_missing.json()
    assert store.chunks[owned] == [USER_A]                                                # nothing deleted


def test_another_account_cannot_take_over_an_owned_session(svc):
    http, store, _ = svc
    s = sid()
    index(http, s, USER_A)
    assert index(http, s, USER_B).status_code == 404
    assert index(http, s, "anon").status_code == 404
    assert store.chunks[s] == [USER_A]


def test_an_anonymous_session_works_for_whoever_holds_its_id(svc):
    http, store, _ = svc
    s = sid()
    assert index(http, s, "anon").status_code == 200 and store.chunks[s] == ["anon"]
    assert query(http, s, "anon").json()["has_content"] is True
    assert index(http, s, "anon").status_code == 200                                     # more uploads, still anonymous
    assert http.delete(f"/session/{s}", headers=H("anon")).status_code == 200


def test_signing_in_and_uploading_moves_an_anonymous_session_to_that_account(svc):
    http, store, _ = svc
    s = sid()
    index(http, s, "anon")
    assert index(http, s, USER_A).status_code == 200
    assert set(store.chunks[s]) == {USER_A}
    assert query(http, s, "anon").json()["has_content"] is False                         # now private
    assert query(http, s, USER_A).json()["has_content"] is True
    assert index(http, s, USER_B).status_code == 404                                     # and never moves again


def test_concurrent_claims_by_two_accounts_leave_exactly_one_owner(svc):
    http, store, main = svc
    s = sid()
    index(http, s, "anon")
    results = []

    def claim(principal):
        results.append((principal, index(http, s, principal).status_code))

    threads = [threading.Thread(target=claim, args=(p,)) for p in (USER_A, USER_B, USER_A, USER_B)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    owners = set(store.chunks[s])
    assert len(owners) == 1 and owners <= {USER_A, USER_B}
    winner = next(iter(owners))
    assert all(code == (200 if p == winner else 404) for p, code in results)


def test_legacy_sessions_without_owner_metadata_still_work(svc):
    http, store, main = svc
    s = sid()
    store.chunks[s] = [None, None]                                                       # written before ownership
    main.engine.docs[s] = ["legacy text"]
    assert query(http, s, "anon").json()["has_content"] is True
    assert query(http, s, USER_B).json()["has_content"] is True                          # readable as before
    assert index(http, s, USER_A).status_code == 200 and set(store.chunks[s]) == {USER_A}  # claimed on upload


def test_a_session_claimed_by_two_accounts_is_refused_to_everyone(svc):
    http, store, main = svc
    s = sid()
    store.chunks[s] = [USER_A, USER_B]
    main.engine.docs[s] = ["text"]
    for who in (USER_A, USER_B, "anon"):
        assert query(http, s, who).json()["has_content"] is False


def test_malformed_ids_and_the_reserved_empty_id_are_refused(svc):
    http, _, main = svc
    for bad in ("../etc", "short", main._EMPTY_SESSION):
        assert index(http, bad).status_code == 404
        assert query(http, bad).json()["has_content"] is False


def test_a_forged_principal_without_the_secret_is_rejected(svc):
    http, store, _ = svc
    s = sid()
    index(http, s, USER_A)
    forged = http.post("/query", json={"session_id": s, "question": "q"}, headers={"X-RAG-Principal": USER_A})
    assert forged.status_code == 401


def test_logs_never_contain_session_ids_secrets_or_principals(svc, caplog):
    http, _, _ = svc
    caplog.set_level(logging.DEBUG)
    s = sid()
    index(http, s, USER_A)
    query(http, s, USER_A)
    query(http, s, USER_B)
    index(http, s, USER_B)
    http.delete(f"/session/{s}", headers=H(USER_A))
    text = caplog.text
    assert s not in text and s[:8] not in text and TOKEN not in text and USER_A not in text


# ── pure ownership rules ─────────────────────────────────────────────────────────────────────────────────
def test_ownership_rules(monkeypatch):
    main = _load_main(monkeypatch, FakeStore(), RAG_SERVICE_TOKEN=TOKEN)
    import session_access as A
    assert A.effective_owner([]) == (A.NONE, None)
    assert A.effective_owner([None, "anon"]) == (A.ANONYMOUS, "anon")
    assert A.effective_owner([None, USER_A]) == (A.USER, USER_A)
    assert A.effective_owner([USER_A, USER_B])[0] == A.CONFLICT
    assert A.parse_principal("user:not-a-uuid") == "anon" and A.parse_principal(USER_A) == USER_A
    assert A.index_plan(A.ANONYMOUS, "anon", USER_A) == "migrate"
    assert A.index_plan(A.USER, USER_A, USER_B) == "deny"
    assert A.index_plan(A.USER, USER_A, "anon") == "deny"


def test_the_sql_ownership_move_locks_the_session_and_flips_only_anonymous_rows(monkeypatch):
    """The direct-Postgres path: advisory lock first, then a conditional update, all in one transaction."""
    here = Path(__file__).parent
    spec = importlib.util.spec_from_file_location("session_access_sql", here / "session_access.py")
    A = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(A)
    statements = []

    class Cursor:
        def __init__(self):
            self.rows = [[None], ["anon"]]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params=()):
            statements.append((" ".join(sql.split()), params))
            if sql.strip().startswith("UPDATE"):
                self.rows = [[USER_A]]

        def fetchall(self):
            return self.rows

    class Conn:
        cursor_obj = Cursor()

        def cursor(self):
            return self.cursor_obj

    from contextlib import contextmanager

    @contextmanager
    def connection():
        yield Conn()

    fake = types.SimpleNamespace(_DATABASE_URL="postgres://x", _database_connection=connection)
    monkeypatch.setattr(A, "_store", lambda: fake)
    s = sid()
    assert A.migrate_to(s, USER_A) == (A.USER, USER_A)
    assert statements[0][0].startswith("SELECT pg_advisory_xact_lock")
    update = next(sql for sql, _ in statements if sql.startswith("UPDATE"))
    assert "coalesce(source_metadata->>%s, %s) = %s" in update                            # only still-anonymous rows
    with pytest.raises(ValueError):
        A.migrate_to(s, "anon")
