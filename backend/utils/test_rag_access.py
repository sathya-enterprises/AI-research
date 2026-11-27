"""Backend side of RAG session security: the principal comes only from a verified token, session ids are
validated, failed lookups are rate-limited, the shared secret is sent, and nothing sensitive is logged.
Mocked HTTP only."""
import asyncio
import contextvars
import logging
import uuid

import httpx
import pytest

from utils import rag_access as A
from utils import rag_client as C

USER = str(uuid.UUID(int=7, version=4))
SECRET = "test-only-service-secret"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    A.LOOKUPS.reset()
    monkeypatch.setenv("RAG_SERVICE_TOKEN", SECRET)
    monkeypatch.setattr(C, "_wake_rag_service", _awake)
    yield
    A.LOOKUPS.reset()


async def _awake(*a, **k):
    return True


def _mock(monkeypatch, handler):
    seen = []
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        def wrapped(request):
            seen.append(request)
            return handler(request)
        kwargs["transport"] = httpx.MockTransport(wrapped)
        return real(*args, **kwargs)

    monkeypatch.setattr(C.httpx, "AsyncClient", factory)
    return seen


def _no_content(request, miss=True):
    return httpx.Response(200, json={"has_content": False, "retrieved": 0, "source_files": [], "context": "",
                                     "system_prompt": ""}, headers={"X-RAG-Lookup": "miss"} if miss else {})


def test_the_principal_is_anonymous_unless_a_verified_user_id_is_set():
    ctx = contextvars.copy_context()
    assert ctx.run(lambda: (A.set_request_identity(None, "1.2.3.4"), A.current_principal())[1]) == "anon"
    assert ctx.run(lambda: (A.set_request_identity("not-a-uuid", "x"), A.current_principal())[1]) == "anon"
    assert ctx.run(lambda: (A.set_request_identity(USER, "x"), A.current_principal())[1]) == f"user:{USER}"


def test_calls_carry_the_shared_secret_and_principal_and_count_misses(monkeypatch):
    seen = _mock(monkeypatch, _no_content)
    sid = str(uuid.uuid4())

    async def go():
        A.set_request_identity(USER, "client")
        return await C.rag_query(sid, "q")

    result = asyncio.run(go())
    req = seen[0]
    assert req.headers["x-rag-service-token"] == SECRET and req.headers["x-rag-principal"] == f"user:{USER}"
    assert result["has_content"] is False
    assert len(A.LOOKUPS._failures[f"user:{USER}"]) == 1                     # the service's miss was counted


def test_malformed_session_ids_never_reach_the_service(monkeypatch):
    seen = _mock(monkeypatch, _no_content)
    result = asyncio.run(C.rag_query("../../etc", "q"))
    assert seen == [] and result["http_status"] == 404 and result["has_content"] is False
    assert asyncio.run(C.rag_index("short", [{"id": "d", "name": "n", "text": "t" * 40}]))["http_status"] == 400
    assert seen == []


def test_failed_lookups_are_rate_limited_per_caller(monkeypatch):
    monkeypatch.setenv("GG_RAG_FAILED_LOOKUPS_PER_10MIN", "5")
    seen = _mock(monkeypatch, _no_content)

    async def guess(n):
        A.set_request_identity(None, "203.0.113.9")
        return [await C.rag_query(str(uuid.uuid4()), "q") for _ in range(n)]

    results = asyncio.run(guess(8))
    assert len(seen) == 5                                                          # then refused without a call
    assert [r.get("http_status") for r in results[5:]] == [429, 429, 429]

    async def other_client():
        A.set_request_identity(None, "198.51.100.4")
        return await C.rag_query(str(uuid.uuid4()), "q")

    assert asyncio.run(other_client()).get("http_status") != 429                  # limits are per caller
    limiter = A.LookupLimiter(window=10)
    for _ in range(5):
        limiter.failure("k", now=100.0)
    assert limiter.blocked("k", now=105.0) and not limiter.blocked("k", now=111.0)  # the window expires


def test_delete_maps_not_found_and_not_yours_to_the_same_404(monkeypatch):
    _mock(monkeypatch, lambda r: httpx.Response(404, json={"detail": "session not found"}))
    assert asyncio.run(C.rag_delete_session(str(uuid.uuid4()))) == {"deleted": False, "http_status": 404}


def test_logs_never_contain_session_ids_or_the_secret(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    _mock(monkeypatch, lambda r: httpx.Response(200, json={"has_content": True, "retrieved": 1, "chunks_added": 1,
                                                           "total_chunks": 1, "source_files": ["a"]}))
    sid = str(uuid.uuid4())
    asyncio.run(C.rag_query(sid, "q"))
    asyncio.run(C.rag_index(sid, [{"id": "d", "name": "n", "text": "t" * 40}]))
    asyncio.run(C.rag_delete_session(sid))
    assert sid not in caplog.text and sid[:8] not in caplog.text and SECRET not in caplog.text
    assert A.session_tag(sid) in caplog.text


def test_the_backend_derives_the_principal_from_the_verified_token_only(monkeypatch):
    """End to end through main.py: a forged principal in the body or headers is ignored."""
    from fastapi.testclient import TestClient

    import main
    import utils.auth as auth
    seen = _mock(monkeypatch, lambda r: _no_content(r, miss=False))

    async def verify(request):
        return USER if request.headers.get("authorization") == "Bearer good-token" else None

    monkeypatch.setattr(auth, "get_user_id", verify)
    http = TestClient(main.app)
    body = {"session_id": str(uuid.uuid4()), "question": "q", "principal": "user:" + str(uuid.uuid4())}
    http.post("/api/rag/query", json=body, headers={"X-RAG-Principal": body["principal"]})
    http.post("/api/rag/query", json=body, headers={"Authorization": "Bearer good-token"})
    http.post("/api/rag/query", json=body, headers={"Authorization": "Bearer forged"})
    assert [r.headers["x-rag-principal"] for r in seen] == ["anon", f"user:{USER}", "anon"]


def test_backend_routes_answer_like_an_unknown_session_and_rate_limit(monkeypatch):
    from fastapi.testclient import TestClient

    import main
    monkeypatch.setenv("GG_RAG_FAILED_LOOKUPS_PER_10MIN", "3")
    _mock(monkeypatch, lambda r: httpx.Response(404, json={"detail": "session not found"})
          if r.method == "DELETE" else _no_content(r))
    http = TestClient(main.app)
    sid = str(uuid.uuid4())
    assert http.delete(f"/api/rag/session/{sid}").status_code == 404
    assert http.post("/api/rag/query", json={"session_id": "bad", "question": "q"}).json()["has_content"] is False
    assert http.post("/api/rag/index", json={"session_id": "bad", "documents": []}).status_code == 400
    assert http.post("/api/rag/query", json={"session_id": sid, "question": "q"}).status_code == 200  # 3rd miss
    assert http.post("/api/rag/query", json={"session_id": sid, "question": "q"}).status_code == 429
    assert http.delete(f"/api/rag/session/{sid}").status_code == 429


def test_report_jobs_keep_the_requesters_principal_on_their_thread(monkeypatch):
    import routes.report as report
    seen = []
    monkeypatch.setattr(report, "_run_report_job_in_thread", lambda job_id, body: seen.append(A.current_principal()))

    async def start():
        A.set_request_identity(USER, "client")
        await report._run_report_job("job-1", {})

    asyncio.run(start())
    assert seen == [f"user:{USER}"]
