"""
Growth Gradual — In The Money  |  FastAPI Backend
Serves:
  POST /api/chat              — SSE streaming chat (Groq → Gemini fallback)
  POST /api/chat/report       — Multi-source research report (JSON)
  POST /api/chat/report/pdf   — PDF from report data (binary; the only report output format)
  GET  /api/stocks/search     — Search the Screener.in fundamentals knowledge base
  GET  /api/stocks/{id}       — Full fundamentals snapshot for a company
  POST /api/datasearch        — Free-text query -> structured data points (Data Search Engine)
"""
import asyncio
import logging
import os
import re
import time
import uuid
from pathlib import Path

# Playwright needs asyncio subprocess support. Set this before any ASGI server
# creates its loop; Windows' selector policy raises NotImplementedError there.
if os.name == "nt" and hasattr(asyncio, "WindowsProactorEventLoopPolicy"):
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

# Load .env from backend directory — must happen before any route imports
try:
    from dotenv import load_dotenv
    _env = Path(__file__).parent / ".env"
    load_dotenv(_env if _env.exists() else None)
except ImportError:
    pass  # no dotenv installed — use real env vars (Render/prod)

# ─── Logging setup ────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [GG-Backend] %(levelname)-8s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("main")

# HTTP clients may include complete URLs or authorization details in log
# records. Gemini calls in this backend use the x-goog-api-key header; retain
# query-string and credential redaction as defense in depth for other clients
# and any future provider integration.
_KEY_PARAM_RE = re.compile(
    r"([?&](?:(?:x[-_]?(?:goog|amz|ms)[-_])?(?:api[_-]?key|credential|security[-_]?token|signature|sig)|"
    r"(?:x[-_]?(?:goog[-_]?)?)?api[_-]?key|(?:x[-_]?)?key|awsaccesskeyid|googleaccessid|"
    r"access[_-]?token|auth(?:orization)?|token|secret|client[_-]?secret|"
    r"password|credentials?|signature|sig|bearer)=)[^&\s\"']+",
    re.IGNORECASE,
)
_KEY_SUFFIX_RE = re.compile(r"(\bkey(?:\s*=\s*|\s+)\.\.\.)[A-Za-z0-9_-]+", re.IGNORECASE)
_KEY_POOL_DETAIL_RE = re.compile(r"(\bkey detail:\s*).+", re.IGNORECASE)
_CREDENTIAL_ASSIGNMENT_RE = re.compile(
    r"(?i)(\b(?:x[-_]?(?:goog|amz|ms)[-_](?:api[-_]?key|security[-_]?token)|"
    r"api[_-]?key|key|access[_-]?token|token|secret|password|authorization)\b\s*[:=]\s*)"
    r"(?:\"[^\"]*\"|'[^']*'|[^,\s&]+)"
)
_BEARER_TOKEN_RE = re.compile(r"(?i)\bBearer\s+[^\s,;]+")
_SENSITIVE_ENV_NAME_RE = re.compile(
    r"(?i)(?:^|_)(?:API[_-]?KEYS?|KEYS?|ACCESS[_-]?TOKEN|AUTH[_-]?TOKEN|TOKEN|SECRET|PASSWORD|CREDENTIALS?)(?:_?\d+)?$"
    r"|(?:^|_)(?:DATABASE|DB|SUPABASE)_?(?:URL|URI)$|(?:^|_)DSN$"
)


def _configured_log_secrets() -> list[str]:
    """Return configured credential values for exact-match log redaction only."""
    values = set()
    for name, raw_value in os.environ.items():
        if not _SENSITIVE_ENV_NAME_RE.search(name):
            continue
        for candidate in re.split(r"[,;\r\n]+", str(raw_value or "")):
            value = candidate.strip().strip("\"'").strip()
            if len(value) >= 6:
                values.add(value)
    return sorted(values, key=len, reverse=True)


class _RedactApiKeysFilter(logging.Filter):
    def __init__(self) -> None:
        super().__init__()
        self._secrets = _configured_log_secrets()

    def filter(self, record: logging.LogRecord) -> bool:
        # HTTPX formats request method/URL as arguments; checking only msg or
        # string arguments misses httpx.URL instances and leaks query API keys.
        try:
            rendered = record.getMessage()
        except Exception:
            rendered = str(record.msg)
        safe = _KEY_PARAM_RE.sub(r"\1***REDACTED***", rendered)
        safe = _KEY_SUFFIX_RE.sub(r"\1***REDACTED***", safe)
        safe = _KEY_POOL_DETAIL_RE.sub(r"\1***REDACTED***", safe)
        safe = _BEARER_TOKEN_RE.sub("Bearer ***REDACTED***", safe)
        safe = _CREDENTIAL_ASSIGNMENT_RE.sub(r"\1***REDACTED***", safe)
        for secret in self._secrets:
            safe = safe.replace(secret, "***REDACTED***")
        if safe != rendered:
            record.msg = safe
            record.args = ()
        return True


logging.getLogger("httpx").addFilter(_RedactApiKeysFilter())
for _handler in logging.getLogger().handlers:
    _handler.addFilter(_RedactApiKeysFilter())

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from routes.chat import router as chat_router
from routes.report import router as report_router
from routes.pdf import router as pdf_router
from routes.email import router as email_router
from routes.rag import router as rag_router
from routes.stocks import router as stocks_router
from routes.datasearch import router as datasearch_router
from routes.knowledge import router as knowledge_router
from utils.keys import get_gemini_keys, get_groq_keys, get_exa_keys, get_serpapi_keys, load_persisted_bans
from utils.observability import get_trace_id, set_trace_id, reset_trace_id, record_event, recent_events

app = FastAPI(title="Growth Gradual API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat_router,   prefix="/api/chat",                tags=["chat"])
app.include_router(report_router, prefix="/api/chat/report",         tags=["report"])
app.include_router(pdf_router,    prefix="/api/chat/report/pdf",     tags=["pdf"])
app.include_router(email_router,  prefix="/api/chat/report/email",   tags=["email"])
app.include_router(rag_router,    prefix="/api/rag",                  tags=["rag"])
app.include_router(stocks_router, prefix="/api/stocks",                tags=["stocks"])
app.include_router(datasearch_router, prefix="/api/datasearch",        tags=["datasearch"])
app.include_router(knowledge_router, prefix="/api/knowledge", tags=["knowledge-base"])


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    method = request.method
    path = request.url.path
    safe_path = re.sub(r"(?i)(/session/)[^/]+", r"\1{session_id}", path)
    # Keep a valid caller-supplied trace id, otherwise create one at the edge.
    supplied = request.headers.get("x-request-id", "")
    trace_id = supplied if re.fullmatch(r"[A-Za-z0-9_-]{8,80}", supplied or "") else uuid.uuid4().hex
    request.state.trace_id = trace_id
    token = set_trace_id(trace_id)
    log.info("→ %s %s trace=%s", method, safe_path, trace_id[:12])
    try:
        response = await call_next(request)
        elapsed = (time.perf_counter() - start) * 1000
        response.headers["X-Request-ID"] = trace_id
        record_event(
            "api-backend", "http_request", "ok" if response.status_code < 400 else "error",
            duration_ms=elapsed,
            details={"method": method, "endpoint": safe_path, "http_status": response.status_code},
            trace_id=trace_id,
        )
        log.info("← %s %s  %d  %.0fms trace=%s", method, safe_path, response.status_code, elapsed, trace_id[:12])
        return response
    except Exception as exc:
        elapsed = (time.perf_counter() - start) * 1000
        record_event("api-backend", "http_request", "error", duration_ms=elapsed,
                     details={"method": method, "endpoint": safe_path, "error_type": type(exc).__name__}, trace_id=trace_id)
        log.exception("Request failed: %s %s trace=%s", method, safe_path, trace_id[:12])
        raise
    finally:
        reset_trace_id(token)


async def _probe_pdf_browser():
    """Probe PDF validation and rendering dependencies without launching Chromium."""
    import importlib

    try:
        try:
            importlib.import_module("pymupdf")
        except ImportError:
            importlib.import_module("fitz")
        log.info("  PyMuPDF import probe passed (final PDF text-layer audit enabled)")
    except Exception as exc:
        # This is recoverable because routes/pdf.py has a pypdf fallback. Keep the
        # warning prominent so deployments can distinguish the fallback from the
        # stronger primary validator.
        log.warning(
            "  PyMuPDF unavailable at startup (%s); final PDF validation will use "
            "the pypdf fallback. Ensure backend/requirements.txt installs PyMuPDF.",
            type(exc).__name__,
        )

    try:
        importlib.import_module("weasyprint").HTML
        log.info("  WeasyPrint import probe passed")
        return
    except Exception as exc:
        log.warning(
            "  WeasyPrint unavailable at startup: %s. On Windows install MSYS2 UCRT64 "
            "Pango and set WEASYPRINT_DLL_DIRECTORIES to C:\\msys64\\ucrt64\\bin.",
            exc,
        )

    # Do not invoke Playwright from the FastAPI event-loop thread or a startup worker
    # merely to prove it exists. The PDF route handles it safely and has a system-
    # Chromium fallback.
    log.info("  Browser PDF startup probe skipped; PDF route will use its runtime fallbacks")


async def _keepalive_rag():
    """Ping the RAG service every 10 min to prevent HF Space cold-starts."""
    import os
    import httpx

    rag_url = os.environ.get(
        "RAG_SERVICE_URL", "http://127.0.0.1:7860"
    ).rstrip("/")
    ping_url = f"{rag_url}/ping"
    interval = 10 * 60  # seconds

    # Wait a bit after startup before first ping
    await asyncio.sleep(30)

    while True:
        try:
            # 15s was too tight for a Space that's actually cold (boot can take
            # 60-90s) — this ping's whole purpose is to catch that case, so it
            # needs to outlast it rather than time out and get logged as a
            # generic failure.
            async with httpx.AsyncClient(timeout=httpx.Timeout(connect=15, read=90, write=30, pool=10)) as c:
                res = await c.get(ping_url)
            if res.is_success:
                log.info("RAG keepalive ✓  %s  →  %s", ping_url, res.json())
            else:
                log.warning("RAG keepalive HTTP %d", res.status_code)
        except Exception as exc:
            # httpx timeout exceptions carry no message (str(exc) == ''), so
            # bare "%s" % exc silently logs nothing after the colon. Always
            # include the exception type so a timeout is distinguishable from
            # a DNS failure, connection refusal, etc.
            msg = str(exc) or "no message — likely a connect/read timeout"
            log.warning("RAG keepalive failed: %s: %s", type(exc).__name__, msg)
        await asyncio.sleep(interval)


@app.on_event("startup")
async def on_startup():
    # Use the same loader the request-path code actually calls (utils.keys
    # ._load_pool) instead of a separate inline os.environ.split(",") here —
    # two independent parsers drift apart silently (e.g. this one wouldn't
    # reflect a newline/semicolon parsing fix made in utils/keys.py), which
    # makes this startup log lie about what's really loaded.
    from utils.keys import describe_pool
    groq_keys, exa_keys, gemini_keys, serpapi_keys = get_groq_keys(), get_exa_keys(), get_gemini_keys(), get_serpapi_keys()
    log.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    log.info("  Growth Gradual — In The Money  |  Backend v1.0.0")
    log.info("  Groq keys: %d  |  Exa keys: %d  |  Gemini keys: %d  |  SerpAPI keys: %d",
              len(groq_keys), len(exa_keys), len(gemini_keys), len(serpapi_keys))
    # Key values stay out of logs; only pool counts are useful for startup
    # diagnostics and safe to ship to external log collectors.
    log.info("  Groq key detail: %s", describe_pool(groq_keys))
    log.info("  SerpAPI key detail: %s", describe_pool(serpapi_keys))
    log.info("  FastAPI app initialized; uvicorn CLI controls the listening port")
    log.info("  Chart gallery: %s (layout=%s)", os.getenv("REPORT_CHART_GALLERY", "core"), os.getenv("REPORT_CHART_GALLERY_LAYOUT", "inline"))
    # One plain-language diagnosis of Supabase env problems (typo, paused project,
    # stale SCREENER_SUPABASE_URL, DNS) instead of a cryptic getaddrinfo error later.
    try:
        from utils.supabase_env import diagnose_supabase_env
        await asyncio.to_thread(diagnose_supabase_env)
    except Exception as _sb_exc:
        log.debug("Supabase env diagnostic skipped: %s", type(_sb_exc).__name__)
    log.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    # Restore any still-active 24h key bans from before this restart — see
    # utils/keys.py module docstring for why this matters on frequent redeploys.
    await load_persisted_bans(get_gemini_keys(), get_groq_keys(), get_exa_keys(), get_serpapi_keys())
    await _probe_pdf_browser()
    # Start background RAG keepalive (supplements external cron)
    asyncio.create_task(_keepalive_rag())
    log.info("  RAG keepalive task started (interval: 10 min)")


@app.get("/ping")
@app.head("/ping")
def ping():
    return {"pong": True, "service": "growth-gradual-backend"}


@app.get("/")
@app.head("/")
def root():
    return {"service": "Growth Gradual Backend", "status": "ok"}


@app.get("/health")
@app.head("/health")
def health():
    return {
        "status": "ok",
        "groq_keys":   len(get_groq_keys()),
        "exa_keys": len(get_exa_keys()),
        "gemini_keys": len(get_gemini_keys()),
        "serpapi_keys": len(get_serpapi_keys()),
    }


@app.post("/api/diagnostics/client-event")
async def diagnostics_client_event(request: Request):
    """Accept a tiny allow-listed set of browser processing metrics; never raw file text."""
    try:
        raw_body = await request.body()
        if len(raw_body) > 2048:
            return {"accepted": False, "reason": "payload_too_large"}
        import json as _json
        payload = _json.loads(raw_body or b"{}")
    except Exception:
        return {"accepted": False, "reason": "invalid_json"}
    if not isinstance(payload, dict) or payload.get("stage") != "browser_file_extraction":
        return {"accepted": False, "reason": "unsupported_event"}
    status = payload.get("status") if payload.get("status") in {"ok", "failed", "partial"} else "unknown"
    def _bounded_number(name: str, maximum: int) -> int:
        value = payload.get(name, 0)
        try:
            return max(0, min(int(value), maximum))
        except (TypeError, ValueError):
            return 0
    record_event("browser", "file_extraction", status, duration_ms=_bounded_number("duration_ms", 600000), details={
        "file_type": str(payload.get("file_type") or "unknown")[:24],
        "input_bytes": _bounded_number("input_bytes", 2_000_000_000),
        "extracted_chars": _bounded_number("extracted_chars", 50_000_000),
        "sheets": _bounded_number("sheets", 500),
        "pages": _bounded_number("pages", 10000),
    }, trace_id=getattr(request.state, "trace_id", ""))
    return {"accepted": True}


@app.get("/api/diagnostics/overview")
async def diagnostics_overview():
    """Safe operational view of the services used by the current chat/RAG path."""
    from utils.rag_client import rag_health_details, rag_diagnostics
    from utils.websearch import provider_name, web_search_available
    from utils.keys import get_groq_keys, get_gemini_keys, get_exa_keys, get_serpapi_keys, get_serper_keys

    rag_started = time.perf_counter()
    health_result, diagnostics_result = await asyncio.gather(
        rag_health_details(), rag_diagnostics(), return_exceptions=True
    )
    rag_probe_ms = round((time.perf_counter() - rag_started) * 1000, 2)
    if isinstance(health_result, Exception):
        rag = {"reachable": False, "ready": False, "status": "probe_failed", "errorType": type(health_result).__name__}
    else:
        rag = health_result
    if isinstance(diagnostics_result, Exception):
        rag_diag = {"reachable": False, "status": "unreachable", "recentEvents": [], "errorType": type(diagnostics_result).__name__}
    else:
        rag_diag = diagnostics_result
    vector = rag.get("vector_storage") if isinstance(rag.get("vector_storage"), dict) else {}
    storage = rag.get("storage") if isinstance(rag.get("storage"), dict) else {}
    groq_count = len(get_groq_keys())
    gemini_count = len(get_gemini_keys())
    exa_count = len(get_exa_keys())
    serpapi_count = len(get_serpapi_keys())
    serper_count = len(get_serper_keys())
    search_provider = provider_name()
    try:
        search_ready = bool(web_search_available())
    except Exception:
        search_ready = False

    services = [
        {"id": "frontend", "name": "Next.js frontend", "role": "Chat UI, browser-side file parsing, stream rendering", "status": "connected", "mode": "request_origin"},
        {"id": "api_backend", "name": "FastAPI backend", "role": "Orchestration, tool selection, prompt assembly, LLM streaming", "status": "healthy", "mode": "active", "probeMs": 0},
        {"id": "rag_service", "name": "RAG service", "role": "Chunking, embeddings, pgvector search, optional reranking", "status": "healthy" if rag.get("reachable") and rag.get("ready") else "degraded", "mode": "active", "probeMs": rag_probe_ms, "model": rag.get("model"), "embeddingDimension": rag.get("embedding_dimension"), "rerankerEnabled": bool((rag.get("reranker") or {}).get("enabled"))},
        {"id": "supabase_pgvector", "name": "Supabase Postgres / pgvector", "role": "Persistent document chunks and vector similarity search", "status": "healthy" if vector.get("ready") else "degraded", "mode": "active_dependency", "configured": bool(vector.get("configured")), "table": vector.get("table", "rag_chunks")},
        {"id": "supabase_storage", "name": "Supabase Storage (OKF archive)", "role": "Original extracted text and inspectable document bundle", "status": "healthy" if storage.get("readAvailable") and storage.get("writeAvailable") else "degraded", "mode": "active_optional", "readAvailable": bool(storage.get("readAvailable")), "writeAvailable": bool(storage.get("writeAvailable")), "storageMode": storage.get("mode")},
        {"id": "llm_providers", "name": "LLM providers", "role": "Answer planning and streamed response generation from FastAPI", "status": "healthy" if (groq_count + gemini_count) else "degraded", "mode": "active", "groqKeysConfigured": groq_count, "geminiKeysConfigured": gemini_count, "primaryPath": "Groq", "fallbackPath": "Gemini"},
        {"id": "web_search", "name": "Web search provider", "role": "Live web research tools when selected by orchestrator", "status": "healthy" if search_ready else "degraded", "mode": "active_optional", "provider": search_provider, "exaKeysConfigured": exa_count, "serpapiKeysConfigured": serpapi_count, "serperKeysConfigured": serper_count},
    ]
    legacy = [
        {"id": "api_gateway", "name": "Old Paperly API gateway", "status": "not_in_active_chat_path", "reason": "Current Next.js routes proxy directly to Growth Gradual FastAPI."},
        {"id": "session_service", "name": "Old Paperly session service", "status": "not_in_active_chat_path", "reason": "Current chat session persistence is handled by FastAPI/Supabase helpers."},
        {"id": "file_service", "name": "Old Paperly file service", "status": "not_in_active_chat_path", "reason": "Current attachment flow parses files in the browser and indexes text through the RAG endpoint."},
        {"id": "extraction_service", "name": "Old Paperly extraction service", "status": "not_in_active_chat_path", "reason": "Current chat attachments are extracted in the browser; image vision is handled by the backend."},
        {"id": "llm_service", "name": "Old Paperly LLM service", "status": "not_in_active_chat_path", "reason": "Current chat route calls Groq and Gemini directly from FastAPI; it does not proxy through this service."},
    ]
    return {
        "generatedAt": time.time(),
        "traceId": get_trace_id(),
        "services": services,
        "legacyServices": legacy,
        "rag": {
            "ready": bool(rag.get("reachable") and rag.get("ready")),
            "reachable": bool(rag.get("reachable")),
            "probeMs": rag_probe_ms,
            "sessionCountInProcess": rag.get("active_sessions"),
            "chunkCountInProcess": rag.get("total_chunks"),
            "restoreModel": rag.get("model"),
            "vectorStorage": {"ready": bool(vector.get("ready")), "configured": bool(vector.get("configured")), "reason": vector.get("reason")},
            "reranker": rag.get("reranker", {}),
        },
        "recentEvents": recent_events(160),
        "ragRecentEvents": rag_diag.get("recentEvents", []) if isinstance(rag_diag.get("recentEvents"), list) else [],
        "notes": [
            "The event buffer is in-memory and bounded to the most recent 500 backend events; it resets when the backend restarts.",
            "LLM/search provider checks report configuration, not a paid live test call.",
            "Only operational counts and timings are recorded; prompt text and document contents are not stored in this dashboard.",
        ],
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)
