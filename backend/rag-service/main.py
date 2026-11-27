"""Paperly RAG Service v2 — FastAPI app."""

import asyncio
import os, logging, time, re, uuid, threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from dotenv import load_dotenv, dotenv_values

_service_env = Path(__file__).resolve().with_name(".env")
load_dotenv(_service_env, override=False)
# Also load backend/.env for local setups where API and RAG settings are kept there.
# Fill only unset/blank values so non-empty service-specific/process values win.
_parent_env = Path(__file__).resolve().parents[1] / ".env"
for _key, _value in dotenv_values(_parent_env).items():
    if _value is not None and not os.environ.get(_key):
        os.environ[_key] = _value
logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [RAG] %(levelname)s %(message)s", datefmt="%H:%M:%S")
# httpx/httpcore INFO logs include complete Storage object paths. Keep provider
# and document identifiers out of logs while retaining warnings and failures.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
log = logging.getLogger("rag-service")

app = FastAPI(title="Paperly RAG Service v2", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

from rag_engine import RAGEngine
import okf_store
import pgvector_store
import session_access
engine = RAGEngine()



_RAG_EVENTS = deque(maxlen=200)
_RAG_EVENTS_LOCK = threading.Lock()

def _record_rag_event(request: Request, stage: str, status: str, duration_ms: float, details: dict | None = None):
    safe = {}
    allowed = {"documents", "input_chars", "chunks_added", "total_chunks", "mode", "candidate_count", "eligible_count",
               "retrieved_count", "source_count", "context_chars", "reranker_ran", "scope", "error_type",
               "embedding_dimension", "vector_store", "persistence_status", "http_status", "endpoint"}
    for key, value in (details or {}).items():
        if key in allowed and isinstance(value, (str, int, float, bool, type(None))):
            safe[key] = value if not isinstance(value, str) else value[:80]
        elif key == "stages" and isinstance(value, dict):
            safe[key] = {str(k)[:40]: round(float(v), 2) for k, v in value.items() if isinstance(v, (int, float))}
    event = {"timestamp": datetime.now(timezone.utc).isoformat(),
             "traceId": getattr(request.state, "trace_id", ""), "service": "rag-service",
             "stage": stage, "status": status, "durationMs": round(float(duration_ms), 2), "details": safe}
    with _RAG_EVENTS_LOCK:
        _RAG_EVENTS.append(event)

@app.middleware("http")
async def rag_request_trace(request: Request, call_next):
    incoming = request.headers.get("x-request-id", "")
    trace_id = incoming if re.fullmatch(r"[A-Za-z0-9_-]{8,80}", incoming or "") else uuid.uuid4().hex
    request.state.trace_id = trace_id
    started = time.perf_counter()
    response_status = 500
    try:
        response = await call_next(request)
        response_status = response.status_code
        response.headers["X-Request-ID"] = trace_id
        return response
    finally:
        elapsed = (time.perf_counter() - started) * 1000
        safe_path = re.sub(r"(?i)(/session/)[^/]+", r"\1{session_id}", request.url.path)
        _record_rag_event(request, f"http_{safe_path.strip('/').replace('/', '_') or 'root'}",
                          "ok" if response_status < 400 else "error", elapsed,
                          {"mode": request.method, "http_status": response_status, "endpoint": safe_path})

@app.get("/diagnostics")
async def diagnostics():
    with _RAG_EVENTS_LOCK:
        events = list(_RAG_EVENTS)[-100:]
    return {"service": "rag-service", "recentEvents": list(reversed(events)),
            "bufferLimit": 200, "note": "Operational metadata only; no prompts or document text."}


# ── Models ────────────────────────────────────────────────────

class DocInput(BaseModel):
    id: str
    name: str
    text: str
    source_type: str = "file"
    file_type: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = {}

class IndexReq(BaseModel):
    session_id: str
    documents: List[DocInput]

class QueryReq(BaseModel):
    session_id: str
    question: str
    top_k: int = Field(default=8, ge=1, le=30)
    min_score: float = Field(default=0.15, ge=0.0, le=1.0)
    file_count: int = 1
    is_general: bool = False

class ReportReq(BaseModel):
    session_id: str
    report_spec: str          # what kind of report to generate
    report_type: str = "comprehensive"  # comprehensive | summary | comparison | technical


class RerankPassage(BaseModel):
    id: str = Field(min_length=1, max_length=40)
    text: str = Field(max_length=4000)


class RerankReq(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    passages: List[RerankPassage] = Field(max_length=64)


def _configured_token() -> str:
    return os.getenv("RAG_SERVICE_TOKEN", "").strip()


def _unauthenticated_allowed() -> bool:
    """Local development only: RAG_ALLOW_UNAUTHENTICATED=1 with no token configured. Without either, every
    data endpoint refuses requests (fail closed) - authentication is never silently off."""
    return not _configured_token() and os.getenv("RAG_ALLOW_UNAUTHENTICATED", "").strip() == "1"


def service_token_ok(request: Request) -> bool:
    """Backend-to-RAG shared secret in X-RAG-Service-Token, compared in constant time. Never logged."""
    import hmac
    expected = _configured_token()
    if not expected:
        return _unauthenticated_allowed()
    supplied = request.headers.get("x-rag-service-token", "")
    return hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))


_PUBLIC_PATHS = {"/ping", "/health"}
if not _configured_token() and not _unauthenticated_allowed():
    log.error("RAG_SERVICE_TOKEN is not set: every endpoint except /ping and /health refuses requests")


@app.middleware("http")
async def require_service_token(request: Request, call_next):
    if request.method == "OPTIONS" or request.url.path in _PUBLIC_PATHS:
        return await call_next(request)
    if not _configured_token() and not _unauthenticated_allowed():
        return JSONResponse({"detail": "service authentication is not configured"}, status_code=503)
    if not service_token_ok(request):
        return JSONResponse({"detail": "unauthorized"}, status_code=401)
    return await call_next(request)


# ── Session ownership (session_access.py) ────────────────────
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
# Never indexed (index refuses it): answers for refused sessions are produced from this empty session, so a
# refused session and a nonexistent one get the same response.
_EMPTY_SESSION = "00000000-0000-4000-8000-000000000000"
_SESSION_LOCKS: Dict[tuple, asyncio.Lock] = {}
_MISS = "miss"


def _session_lock(session_id: str) -> asyncio.Lock:
    """Serialises uploads and deletes of one session within this event loop. Across processes the ownership
    move itself is atomic (session_access.migrate_to), which is what decides a race."""
    return _SESSION_LOCKS.setdefault((id(asyncio.get_running_loop()), session_id), asyncio.Lock())


def _usable_id(session_id: str) -> bool:
    return bool(_UUID_RE.match(session_id or "")) and session_id != _EMPTY_SESSION


async def _access(session_id: str, request: Request) -> tuple:
    """(state, owner, principal). The principal header is trusted only because the middleware has already
    verified the backend's shared secret."""
    principal = session_access.parse_principal(request.headers.get("x-rag-principal"))
    if not _usable_id(session_id):
        return session_access.CONFLICT, None, principal  # malformed: refused like an unknown session
    owners = await asyncio.to_thread(session_access.read_owners, session_id)
    state, owner = session_access.effective_owner(owners)
    return state, owner, principal


def _readable(state: str, owner, principal: str) -> bool:
    # NONE proceeds as before: nothing to read, or a legacy OKF-only session that is restored on demand
    return state == session_access.NONE or session_access.may_read(state, owner, principal)


# ── Routes ────────────────────────────────────────────────────


@app.post("/rerank")
async def rerank(req: RerankReq, request: Request):
    """Cross-encoder scores for report evidence candidates (stateless; nothing stored or logged but counts)."""
    if not service_token_ok(request):
        raise HTTPException(401, "unauthorized")
    if not engine.reranker_ready:
        raise HTTPException(503, "reranker unavailable")
    t0 = time.time()
    texts = [p.text for p in req.passages]
    scores = await asyncio.to_thread(engine.rerank_scores, req.query, texts)
    elapsed = round((time.time() - t0) * 1000)
    _record_rag_event(request, "rerank", "ok", elapsed, {"candidate_count": len(texts)})
    return {"scores": {p.id: s for p, s in zip(req.passages, scores)}, "model": os.getenv(
        "HF_RERANK_MODEL", "cross-encoder/ms-marco-MiniLM-L6-v2"), "elapsed_ms": elapsed}

@app.get("/ping")
@app.head("/ping")
async def ping():
    """Public liveness endpoint: no configuration, session or document information."""
    return {"pong": True}

@app.get("/health")
@app.head("/health")
async def health(request: Request):
    """Public: status and readiness only. Details (model, storage, session counts) only for the backend."""
    if not (_configured_token() or _unauthenticated_allowed()) or not service_token_ok(request):
        ready = engine._model is not None
        return {"status": "ok" if ready else "degraded", "ready": ready}
    sessions = engine.list_sessions()
    vector_storage = pgvector_store.health_check()
    ready = engine._model is not None and bool(vector_storage.get("ready"))
    return {
        "service": "rag-service",
        "status": "ok" if ready else "degraded",
        "ready": ready,
        "port": int(os.getenv("RAG_PORT", 7860)),
        "model": engine.model_name,
        "embedding_dimension": engine._dim,
        "index_type": f"{vector_storage.get('backend', 'Postgres pgvector')} (persistent; no local vector index)",
        "vector_storage": vector_storage,
        "reranker": {
            "enabled": engine._reranker is not None,
            "model": os.getenv("HF_RERANK_MODEL", "cross-encoder/ms-marco-MiniLM-L6-v2") if engine._reranker is not None else None,
        },
        "storage": okf_store.storage_status(),
        "active_sessions": len(sessions),
        "total_chunks": sum(
            engine.get_session_info(s).get("chunk_count", 0) for s in sessions
        ),
    }


@app.post("/index")
async def index_docs(req: IndexReq, request: Request):
    t0 = time.time()
    not_found = JSONResponse({"detail": "session not found"}, status_code=404, headers={"X-RAG-Lookup": _MISS})
    if not _usable_id(req.session_id):
        return not_found
    try:
        async with _session_lock(req.session_id):
            state, owner, principal = await _access(req.session_id, request)
            plan = session_access.index_plan(state, owner, principal)
            if plan == "deny":
                log.info("Index refused: %s is not this caller's session", session_access.tag(req.session_id))
                return not_found
            if plan == "migrate":
                state, owner = await asyncio.to_thread(session_access.migrate_to, req.session_id, principal)
                if (state, owner) != (session_access.USER, principal):
                    log.warning("Ownership move refused for %s (state %s)", session_access.tag(req.session_id), state)
                    return not_found
                log.info("Anonymous session %s moved to its signed-in user", session_access.tag(req.session_id))
            documents = session_access.stamp_documents([d.dict() for d in req.documents], principal)
            result = await engine.index(req.session_id, documents)
        elapsed = round((time.time() - t0) * 1000)
        diag = result.get("diagnostics") if isinstance(result.get("diagnostics"), dict) else {}
        _record_rag_event(request, "index_complete", "ok" if (result.get("chunks_added", 0) or result.get("total_chunks", 0)) else "no_match", elapsed, {"mode": "index", "documents": len(req.documents), "input_chars": diag.get("input_chars", 0), "chunks_added": result.get("chunks_added", 0), "total_chunks": result.get("total_chunks", 0), "stages": diag.get("stages", {}), "embedding_dimension": diag.get("embedding_dimension"), "vector_store": pgvector_store.storage_backend(), "persistence_status": (result.get("persistence") or {}).get("status")})
        log.info(f"Indexed session={session_access.tag(req.session_id)} docs={len(req.documents)} "
                 f"chunks={result['chunks_added']} ({elapsed}ms)")
        return {**result, "session_id": req.session_id, "elapsed_ms": elapsed}
    except Exception as e:
        log.error(f"Index error: {e}")
        raise HTTPException(500, str(e))


@app.post("/query")
async def query(req: QueryReq, request: Request, response: Response):
    t0 = time.time()
    try:
        state, owner, principal = await _access(req.session_id, request)
        readable = _readable(state, owner, principal)
        target = req.session_id if readable else _EMPTY_SESSION
        restore_status = await engine.ensure_session_loaded(target)
        # Let rag_engine's scope detection handle top_k — only override if
        # caller explicitly set a high value or is_general is forced True
        top_k = req.top_k
        min_score = req.min_score

        if req.is_general:
            # Caller explicitly flagged as general — pull everything
            top_k = max(1, min(30, engine.get_session_info(target).get("chunk_count", 30) or 30))
            min_score = 0.0

        result = await asyncio.to_thread(
            engine.retrieve,
            session_id=target,
            question=req.question,
            top_k=top_k,
            min_score=min_score,
        )
        elapsed = round((time.time() - t0) * 1000)
        diag = result.get("diagnostics") if isinstance(result.get("diagnostics"), dict) else {}
        _record_rag_event(request, "query_complete", "ok" if result.get("has_content") else "no_match", elapsed, {"mode": "query", "candidate_count": diag.get("candidate_count", 0), "eligible_count": diag.get("eligible_count", 0), "retrieved_count": result.get("retrieved", 0), "source_count": len(result.get("source_files") or []), "context_chars": diag.get("context_chars", 0), "reranker_ran": result.get("reranker_ran", False), "scope": diag.get("scope"), "stages": diag.get("stages", {})})
        log.info(f"Q&A session={session_access.tag(req.session_id)} chunks={result['retrieved']} "
                 f"found={result['has_content']} ({elapsed}ms)")
        if not result.get("has_content") and (not readable or state == session_access.NONE):
            response.headers["X-RAG-Lookup"] = _MISS
        return {
            **result,
            "restore_status": restore_status,
            "session_id": req.session_id,
            "question": req.question,
            "grounded_system_prompt": result["system_prompt"],
            "retrieved_chunks": result["retrieved"],
            "elapsed_ms": elapsed,
        }
    except Exception as e:
        log.error(f"Query error: {e}")
        raise HTTPException(500, str(e))


@app.post("/report")
async def generate_report(req: ReportReq, request: Request, response: Response):
    """Full-coverage retrieval for report generation — no threshold filtering."""
    t0 = time.time()
    try:
        state, owner, principal = await _access(req.session_id, request)
        readable = _readable(state, owner, principal)
        target = req.session_id if readable else _EMPTY_SESSION
        restore_status = await engine.ensure_session_loaded(target)
        result = await asyncio.to_thread(
            engine.retrieve_for_report,
            session_id=target,
            report_spec=req.report_spec,
            report_type=req.report_type,
        )
        elapsed = round((time.time() - t0) * 1000)
        diag = result.get("diagnostics") if isinstance(result.get("diagnostics"), dict) else {}
        _record_rag_event(request, "report_retrieval_complete", "ok" if result.get("has_content") else "no_match", elapsed, {"mode": "report", "candidate_count": diag.get("candidate_count", 0), "eligible_count": diag.get("eligible_count", 0), "retrieved_count": result.get("retrieved", 0), "source_count": len(result.get("source_files") or []), "context_chars": diag.get("context_chars", 0), "reranker_ran": result.get("reranker_ran", False), "stages": diag.get("stages", {})})
        log.info(f"Report session={session_access.tag(req.session_id)} chunks={result['retrieved']} ({elapsed}ms)")
        if not result.get("has_content") and (not readable or state == session_access.NONE):
            response.headers["X-RAG-Lookup"] = _MISS
        return {
            **result,
            "restore_status": restore_status,
            "session_id": req.session_id,
            "report_spec": req.report_spec,
            "report_type": req.report_type,
            "grounded_system_prompt": result["system_prompt"],
            "retrieved_chunks": result["retrieved"],
            "elapsed_ms": elapsed,
        }
    except Exception as e:
        log.error(f"Report error: {e}")
        raise HTTPException(500, str(e))


@app.delete("/session/{session_id}")
async def delete_session(session_id: str, request: Request):
    """Only the owner deletes (an anonymous session: whoever holds its id). An unknown session and another
    account's session get the same 404, and nothing is deleted."""
    not_found = JSONResponse({"detail": "session not found"}, status_code=404, headers={"X-RAG-Lookup": _MISS})
    if not _usable_id(session_id):
        return not_found
    async with _session_lock(session_id):
        state, owner, principal = await _access(session_id, request)
        if not session_access.may_read(state, owner, principal):
            return not_found
        await engine.delete_session(session_id)
    return {"success": True, "session_id": session_id, "vector_store": pgvector_store.storage_backend()}


@app.get("/session/{session_id}/info")
async def session_info(session_id: str, request: Request, response: Response):
    state, owner, principal = await _access(session_id, request)
    target = session_id if _readable(state, owner, principal) else _EMPTY_SESSION
    restore_status = await engine.ensure_session_loaded(target)
    info = {**engine.get_session_info(target), "session_id": session_id, "restore_status": restore_status}
    if not info.get("chunk_count"):
        response.headers["X-RAG-Lookup"] = _MISS
    return info


@app.get("/session/{session_id}/okf")
async def session_okf_bundle(session_id: str, request: Request):
    """Download the session's OKF bundle (all indexed documents as markdown
    concept files with YAML frontmatter) as a zip. Files are fetched from
    Supabase Storage — the rag-service's own container filesystem on HF
    Spaces is ephemeral, so nothing is read from local disk here."""
    import io, zipfile
    from fastapi.responses import Response
    import okf_store

    state, owner, principal = await _access(session_id, request)
    files = await okf_store.get_session_bundle_files(session_id) if _readable(state, owner, principal) else []
    if not files:
        return JSONResponse({"detail": "No OKF bundle found for this session"}, status_code=404,
                            headers={"X-RAG-Lookup": _MISS})

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel_path, content in files:
            zf.writestr(rel_path, content)
    buf.seek(0)
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="paperly-okf-{session_id[:8]}.zip"'},
    )


if __name__ == "__main__":
    port = int(os.getenv("RAG_PORT", 7860))
    host = os.getenv("RAG_HOST", "127.0.0.1")
    log.info(f"🐍 RAG Service v2 starting on :{port}")
    uvicorn.run(app, host=host, port=port, log_level="warning")
