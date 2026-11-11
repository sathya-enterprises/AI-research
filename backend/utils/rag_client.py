"""
RAG Service Client
==================
Thin async wrapper around the Paperly RAG Service running on HuggingFace Spaces.
URL: RAG_SERVICE_URL env (default http://127.0.0.1:7860, the local backend/rag-service)

Endpoints used:
  POST /index   — index extracted text from uploaded files
  POST /query   — retrieve grounded context for a chat question
  POST /report  — full-coverage retrieval for report generation
  DELETE /session/{id} — clean up on new chat
"""

import asyncio
import logging
import os

import httpx

from utils.observability import get_trace_id, record_event

log = logging.getLogger("rag_client")

RAG_URL = os.environ.get(
    "RAG_SERVICE_URL",
    "http://127.0.0.1:7860",
).rstrip("/")

# HF Spaces cold-start can take 60-90s to come up from a sleeping state.
# connect=15 was too tight for that (the TCP handshake itself can stall
# while the Space container is still booting) and read=60 was cutting it
# close too — both bumped with headroom below.
_TIMEOUT = httpx.Timeout(connect=30, read=90, write=90, pool=10)


def _exc_str(exc: Exception) -> str:
    """
    httpx's timeout exceptions (ConnectTimeout, ReadTimeout, PoolTimeout) are
    raised with NO message — str(exc) is just ''. Every "...failed: %s"
    log line hit by one of these ends up with nothing after the colon,
    which makes it impossible to tell a timeout apart from any other error
    at a glance. Always include the exception type so the log is never
    silently blank.
    """
    msg = str(exc)
    return f"{type(exc).__name__}: {msg}" if msg else f"{type(exc).__name__} (no message — likely a connect/read timeout, e.g. HF Space cold start)"


def _safe_index_http_error(response: httpx.Response) -> str:
    """Turn a RAG service error into an actionable message without echoing its body."""
    try:
        payload = response.json()
    except Exception:
        payload = {}
    detail = payload.get("detail") if isinstance(payload, dict) else None
    detail = detail if isinstance(detail, str) else ""
    normalized = " ".join(detail.split()).casefold()

    if "server-only supabase_secret_key" in normalized or "server-only supabase_service_role_key" in normalized:
        return "RAG pgvector persistence is not configured with a server-only Supabase key."
    if "supabase_url" in normalized and ("not configured" in normalized or "set supabase_url" in normalized):
        return "RAG pgvector persistence is not configured with a database URL."
    if "embedding dimension mismatch" in normalized:
        return "RAG pgvector schema does not match the configured embedding model."
    if "supabase pgvector returned http 401" in normalized or "supabase pgvector returned http 403" in normalized:
        return "RAG pgvector rejected the server credentials. Check the Supabase key and permissions."
    if "supabase pgvector returned http 404" in normalized:
        return "RAG pgvector could not find its table or RPC. Check the database migration and service permissions."
    return f"RAG service returned HTTP {response.status_code}. Check RAG service health diagnostics."


async def _wake_rag_service(max_wait: float = 100.0, poll_interval: float = 5.0) -> bool:
    """
    A sleeping HF Space's wake-up proxy will accept a TCP connection for the
    *real* request (e.g. /index) but not hand it to the container until the
    container has finished booting — so the real call just sits there and
    eventually hits a write/read timeout with nothing logged on either side
    (confirmed: the RAG service's own container log had no entry at all for
    a request that timed out from our side after 30+s).

    Rather than let every cold-start request burn its full write/read
    timeout budget blind, poll the cheap /ping endpoint first with a short
    per-attempt timeout. Each attempt is logged, so if this *is* a cold
    start you'll see it waking up in real time in this service's own logs
    instead of a single opaque timeout 30-90s later. Returns True once
    /ping responds, False if it never comes up within max_wait.
    """
    deadline = asyncio.get_event_loop().time() + max_wait
    attempt = 0
    while True:
        attempt += 1
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=10, write=10, pool=10)) as c:
                res = await c.get(f"{RAG_URL}/ping")
            if res.is_success:
                if attempt > 1:
                    log.info("RAG service woke up after %d ping(s)", attempt)
                return True
        except Exception as exc:
            log.info("RAG wake-up ping %d failed (%s) — Space likely still booting", attempt, _exc_str(exc))
        if asyncio.get_event_loop().time() >= deadline:
            log.warning("RAG service did not wake within %.0fs after %d ping(s)", max_wait, attempt)
            return False
        await asyncio.sleep(poll_interval)


async def rag_index(session_id: str, documents: list[dict]) -> dict:
    """Index extracted attachment text in RAG and record safe stage metadata."""
    started = asyncio.get_running_loop().time()
    input_chars = sum(len(str(doc.get("text") or "")) for doc in documents)
    headers = {"X-Request-ID": get_trace_id()} if get_trace_id() else {}
    try:
        woke = await _wake_rag_service()
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            res = await c.post(f"{RAG_URL}/index", json={
                "session_id": session_id,
                "documents": documents,
            }, headers=headers)
        elapsed = (asyncio.get_running_loop().time() - started) * 1000
        if res.is_success:
            data = res.json()
            diag = data.get("diagnostics") if isinstance(data.get("diagnostics"), dict) else {}
            record_event("rag-client", "index", "ok" if (data.get("chunks_added", 0) or data.get("total_chunks", 0)) else "no_match", duration_ms=elapsed, details={
                "documents": len(documents), "input_chars": input_chars,
                "chunks_added": data.get("chunks_added", 0), "total_chunks": data.get("total_chunks", 0),
                "cold_start_wake_ok": woke, "rag_stages": diag.get("stages", {}),
                "persistence_status": (data.get("persistence") or {}).get("status"),
            })
            log.info("RAG index: session=%s docs=%d chunks_added=%s total=%s duration_ms=%.0f",
                     session_id[:8], len(documents), data.get("chunks_added"), data.get("total_chunks"), elapsed)
            return data
        record_event("rag-client", "index", "error", duration_ms=elapsed, details={
            "documents": len(documents), "input_chars": input_chars,
            "http_status": res.status_code, "error_type": "HTTPError", "cold_start_wake_ok": woke,
        })
        safe_error = _safe_index_http_error(res)
        log.warning("RAG index HTTP %d: %s", res.status_code, safe_error)
        return {"error": safe_error, "chunks_added": 0, "http_status": res.status_code}
    except Exception as exc:
        elapsed = (asyncio.get_running_loop().time() - started) * 1000
        record_event("rag-client", "index", "error", duration_ms=elapsed, details={
            "documents": len(documents), "input_chars": input_chars, "error_type": type(exc).__name__,
        })
        log.warning("RAG index failed (non-critical): %s", _exc_str(exc))
        return {"error": _exc_str(exc), "chunks_added": 0, "error_type": type(exc).__name__}


async def rag_query(
    session_id: str,
    question:   str,
    top_k:      int   = 8,
    min_score:  float = 0.15,
    is_general: bool  = False,
) -> dict:
    """
    Retrieve grounded context + system prompt for a chat question.
    Returns dict with: system_prompt, context, retrieved, source_files, has_content
    """
    started = asyncio.get_running_loop().time()
    headers = {"X-Request-ID": get_trace_id()} if get_trace_id() else {}
    try:
        woke = await _wake_rag_service()
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            res = await c.post(f"{RAG_URL}/query", json={
                "session_id": session_id,
                "question": question,
                "top_k": top_k,
                "min_score": min_score,
                "is_general": is_general,
            }, headers=headers)
        elapsed = (asyncio.get_running_loop().time() - started) * 1000
        if res.is_success:
            data = res.json()
            diag = data.get("diagnostics") if isinstance(data.get("diagnostics"), dict) else {}
            record_event("rag-client", "query", "ok" if data.get("has_content") else "no_match", duration_ms=elapsed, details={
                "query_chars": len(question), "top_k": top_k, "min_score": min_score,
                "retrieved": data.get("retrieved", 0), "source_count": len(data.get("source_files") or []),
                "has_content": bool(data.get("has_content")), "reranker_ran": bool(data.get("reranker_ran")),
                "cold_start_wake_ok": woke, "rag_stages": diag.get("stages", {}),
                "candidate_count": diag.get("candidate_count"), "scope": diag.get("scope"),
            })
            log.info("RAG query: session=%s retrieved=%s has_content=%s duration_ms=%.0f",
                     session_id[:8], data.get("retrieved"), data.get("has_content"), elapsed)
            return data
        record_event("rag-client", "query", "error", duration_ms=elapsed, details={
            "query_chars": len(question), "http_status": res.status_code, "error_type": "HTTPError", "cold_start_wake_ok": woke,
        })
        log.warning("RAG query HTTP %d: %s", res.status_code, res.text[:200])
        return {"has_content": False, "system_prompt": "", "context": "", "retrieved": 0, "source_files": [], "http_status": res.status_code}
    except Exception as exc:
        elapsed = (asyncio.get_running_loop().time() - started) * 1000
        record_event("rag-client", "query", "error", duration_ms=elapsed, details={
            "query_chars": len(question), "error_type": type(exc).__name__,
        })
        log.warning("RAG query failed (non-critical): %s", _exc_str(exc))
        return {"has_content": False, "system_prompt": "", "context": "", "retrieved": 0, "source_files": [], "error_type": type(exc).__name__}


async def rag_report(
    session_id:  str,
    report_spec: str,
    report_type: str = "comprehensive",
) -> dict:
    """
    Full-coverage retrieval for report generation.
    Returns dict with: system_prompt, context, retrieved, source_files, has_content
    """
    started = asyncio.get_running_loop().time()
    headers = {"X-Request-ID": get_trace_id()} if get_trace_id() else {}
    try:
        woke = await _wake_rag_service()
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            res = await c.post(f"{RAG_URL}/report", json={
                "session_id": session_id,
                "report_spec": report_spec,
                "report_type": report_type,
            }, headers=headers)
        elapsed = (asyncio.get_running_loop().time() - started) * 1000
        if res.is_success:
            data = res.json()
            diag = data.get("diagnostics") if isinstance(data.get("diagnostics"), dict) else {}
            record_event("rag-client", "report_retrieval", "ok" if data.get("has_content") else "no_match", duration_ms=elapsed, details={
                "retrieved": data.get("retrieved", 0), "source_count": len(data.get("source_files") or []),
                "reranker_ran": bool(data.get("reranker_ran")), "cold_start_wake_ok": woke,
                "rag_stages": diag.get("stages", {}), "candidate_count": diag.get("candidate_count"),
            })
            log.info("RAG report: session=%s retrieved=%s duration_ms=%.0f", session_id[:8], data.get("retrieved"), elapsed)
            return data
        record_event("rag-client", "report_retrieval", "error", duration_ms=elapsed, details={"http_status": res.status_code, "error_type": "HTTPError", "cold_start_wake_ok": woke})
        log.warning("RAG report HTTP %d: %s", res.status_code, res.text[:200])
        return {"has_content": False, "system_prompt": "", "context": "", "retrieved": 0, "source_files": [], "http_status": res.status_code}
    except Exception as exc:
        elapsed = (asyncio.get_running_loop().time() - started) * 1000
        record_event("rag-client", "report_retrieval", "error", duration_ms=elapsed, details={"error_type": type(exc).__name__})
        log.warning("RAG report failed (non-critical): %s", _exc_str(exc))
        return {"has_content": False, "system_prompt": "", "context": "", "retrieved": 0, "source_files": [], "error_type": type(exc).__name__}


async def rag_delete_session(session_id: str) -> None:
    """Clean up RAG index when user starts a new chat."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10)) as c:
            await c.delete(f"{RAG_URL}/session/{session_id}")
        log.info("RAG session deleted: %s", session_id[:8])
    except Exception as exc:
        log.debug("RAG delete session (non-critical): %s", _exc_str(exc))


async def rag_health() -> bool:
    """Check if RAG service is reachable."""
    result = await rag_health_details()
    return bool(result.get("reachable") and result.get("ready", True))


async def rag_health_details() -> dict:
    """Return sanitized readiness details; never echo the configured URL."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10)) as c:
            res = await c.get(f"{RAG_URL}/health")
        if not res.is_success:
            try:
                payload = res.json()
            except Exception:
                payload = {}
            if isinstance(payload, dict):
                safe_keys = {
                    "service", "status", "ready", "port", "model", "embedding_dimension",
                    "index_type", "vector_storage", "reranker", "active_sessions", "total_chunks",
                }
                safe_payload = {key: value for key, value in payload.items() if key in safe_keys}
                return {"reachable": True, **safe_payload, "httpStatus": res.status_code}
            return {"reachable": True, "ready": False, "status": "http_error", "httpStatus": res.status_code}
        payload = res.json()
        if not isinstance(payload, dict):
            return {"reachable": True, "ready": False, "status": "invalid_response"}
        return {"reachable": True, **payload}
    except Exception as exc:
        return {"reachable": False, "ready": False, "status": "unreachable", "errorType": type(exc).__name__}


async def rag_diagnostics() -> dict:
    """Fetch recent RAG-service operational events without exposing its base URL."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(8)) as c:
            res = await c.get(f"{RAG_URL}/diagnostics")
        if not res.is_success:
            return {"reachable": True, "status": "http_error", "httpStatus": res.status_code, "recentEvents": []}
        payload = res.json()
        return payload if isinstance(payload, dict) else {"reachable": True, "status": "invalid_response", "recentEvents": []}
    except Exception as exc:
        return {"reachable": False, "status": "unreachable", "errorType": type(exc).__name__, "recentEvents": []}
