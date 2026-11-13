"""Paperly RAG Service v2 — FastAPI app."""

import asyncio
import os, logging, time, re, uuid, threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
import uvicorn
from fastapi import FastAPI, HTTPException, Request
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


# ── Routes ────────────────────────────────────────────────────

@app.get("/ping")
@app.head("/ping")
async def ping():
    """Lightweight liveness/readiness endpoint."""
    return {
        "pong": True,
        "model": engine.model_name,
        "embedding_dimension": engine._dim,
        "reranker_loaded": engine._reranker is not None,
        "vector_store": pgvector_store.storage_backend(),
    }

@app.get("/health")
@app.head("/health")
async def health():
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
    try:
        result = await engine.index(req.session_id, [d.dict() for d in req.documents])
        elapsed = round((time.time() - t0) * 1000)
        diag = result.get("diagnostics") if isinstance(result.get("diagnostics"), dict) else {}
        _record_rag_event(request, "index_complete", "ok" if (result.get("chunks_added", 0) or result.get("total_chunks", 0)) else "no_match", elapsed, {"mode": "index", "documents": len(req.documents), "input_chars": diag.get("input_chars", 0), "chunks_added": result.get("chunks_added", 0), "total_chunks": result.get("total_chunks", 0), "stages": diag.get("stages", {}), "embedding_dimension": diag.get("embedding_dimension"), "vector_store": pgvector_store.storage_backend(), "persistence_status": (result.get("persistence") or {}).get("status")})
        log.info(f"Indexed session={req.session_id[:8]} docs={len(req.documents)} "
                 f"chunks={result['chunks_added']} ({elapsed}ms)")
        return {**result, "session_id": req.session_id, "elapsed_ms": elapsed}
    except Exception as e:
        log.error(f"Index error: {e}")
        raise HTTPException(500, str(e))


@app.post("/query")
async def query(req: QueryReq, request: Request):
    t0 = time.time()
    try:
        restore_status = await engine.ensure_session_loaded(req.session_id)
        # Let rag_engine's scope detection handle top_k — only override if
        # caller explicitly set a high value or is_general is forced True
        top_k = req.top_k
        min_score = req.min_score

        if req.is_general:
            # Caller explicitly flagged as general — pull everything
            top_k = min(30, engine.get_session_info(req.session_id).get("chunk_count", 30))
            min_score = 0.0

        result = await asyncio.to_thread(
            engine.retrieve,
            session_id=req.session_id,
            question=req.question,
            top_k=top_k,
            min_score=min_score,
        )
        elapsed = round((time.time() - t0) * 1000)
        diag = result.get("diagnostics") if isinstance(result.get("diagnostics"), dict) else {}
        _record_rag_event(request, "query_complete", "ok" if result.get("has_content") else "no_match", elapsed, {"mode": "query", "candidate_count": diag.get("candidate_count", 0), "eligible_count": diag.get("eligible_count", 0), "retrieved_count": result.get("retrieved", 0), "source_count": len(result.get("source_files") or []), "context_chars": diag.get("context_chars", 0), "reranker_ran": result.get("reranker_ran", False), "scope": diag.get("scope"), "stages": diag.get("stages", {})})
        log.info(f"Q&A session={req.session_id[:8]} chunks={result['retrieved']} "
                 f"found={result['has_content']} ({elapsed}ms)")
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
async def generate_report(req: ReportReq, request: Request):
    """Full-coverage retrieval for report generation — no threshold filtering."""
    t0 = time.time()
    try:
        restore_status = await engine.ensure_session_loaded(req.session_id)
        result = await asyncio.to_thread(
            engine.retrieve_for_report,
            session_id=req.session_id,
            report_spec=req.report_spec,
            report_type=req.report_type,
        )
        elapsed = round((time.time() - t0) * 1000)
        diag = result.get("diagnostics") if isinstance(result.get("diagnostics"), dict) else {}
        _record_rag_event(request, "report_retrieval_complete", "ok" if result.get("has_content") else "no_match", elapsed, {"mode": "report", "candidate_count": diag.get("candidate_count", 0), "eligible_count": diag.get("eligible_count", 0), "retrieved_count": result.get("retrieved", 0), "source_count": len(result.get("source_files") or []), "context_chars": diag.get("context_chars", 0), "reranker_ran": result.get("reranker_ran", False), "stages": diag.get("stages", {})})
        log.info(f"Report session={req.session_id[:8]} chunks={result['retrieved']} ({elapsed}ms)")
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
async def delete_session(session_id: str):
    await engine.delete_session(session_id)
    return {"success": True, "session_id": session_id, "vector_store": pgvector_store.storage_backend()}


@app.get("/session/{session_id}/info")
async def session_info(session_id: str):
    restore_status = await engine.ensure_session_loaded(session_id)
    return {**engine.get_session_info(session_id), "restore_status": restore_status}


@app.get("/session/{session_id}/okf")
async def session_okf_bundle(session_id: str):
    """Download the session's OKF bundle (all indexed documents as markdown
    concept files with YAML frontmatter) as a zip. Files are fetched from
    Supabase Storage — the rag-service's own container filesystem on HF
    Spaces is ephemeral, so nothing is read from local disk here."""
    import io, zipfile
    from fastapi.responses import Response
    import okf_store

    files = await okf_store.get_session_bundle_files(session_id)
    if not files:
        raise HTTPException(404, "No OKF bundle found for this session")

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
