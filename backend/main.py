"""
Growth Gradual — In The Money  |  FastAPI Backend
Serves:
  POST /api/chat              — SSE streaming chat (Groq → Gemini fallback)
  POST /api/chat/report       — Multi-source research report (JSON)
  POST /api/chat/report/okf   — Open Knowledge Format bundle from report data (binary .zip)
  POST /api/chat/report/pdf   — PDF from report data (binary, legacy)
  POST /api/chat/report/docx  — Editable Word report from the same report payload
  POST /api/chat/report/html  — Animated/interactive HTML report from report data (binary, text/html)
  GET  /api/stocks/search     — Search the Screener.in fundamentals knowledge base
  GET  /api/stocks/{id}       — Full fundamentals snapshot for a company
  POST /api/datasearch        — Free-text query -> structured data points (Data Search Engine)
"""
import asyncio
import logging
import re
import time
from pathlib import Path

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

# httpx logs the full request URL at INFO level for every call it makes.
# Several providers (Gemini's REST API in particular) pass the API key as a
# `?key=...` query-string param rather than a header, so those full-URL log
# lines were leaking live, usable API keys into plaintext logs — anywhere
# those logs get shipped (aggregators, support tickets, etc.) is effectively
# a credential leak. Redact any `key=` query param before the record is
# emitted rather than silencing httpx's request logging entirely.
_KEY_PARAM_RE = re.compile(r"([?&]key=)[^&\s\"]+", re.IGNORECASE)
_KEY_SUFFIX_RE = re.compile(r"(\bkey(?:\s*=\s*|\s+)\.\.\.)[A-Za-z0-9_-]+", re.IGNORECASE)
_KEY_POOL_DETAIL_RE = re.compile(r"(\bkey detail:\s*).+", re.IGNORECASE)


class _RedactApiKeysFilter(logging.Filter):
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
from routes.docx_report import router as docx_report_router
from routes.html_report import router as html_report_router
from routes.okf import router as okf_router
from routes.email import router as email_router
from routes.rag import router as rag_router
from routes.stocks import router as stocks_router
from routes.datasearch import router as datasearch_router
from routes.knowledge import router as knowledge_router
from utils.keys import get_gemini_keys, get_groq_keys, get_exa_keys, get_serpapi_keys, load_persisted_bans

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
app.include_router(okf_router,    prefix="/api/chat/report/okf",     tags=["okf"])
app.include_router(pdf_router,    prefix="/api/chat/report/pdf",     tags=["pdf"])
app.include_router(docx_report_router, prefix="/api/chat/report/docx", tags=["docx-report"])
app.include_router(html_report_router, prefix="/api/chat/report/html", tags=["html-report"])
app.include_router(email_router,  prefix="/api/chat/report/email",   tags=["email"])
app.include_router(rag_router,    prefix="/api/rag",                  tags=["rag"])
app.include_router(stocks_router, prefix="/api/stocks",                tags=["stocks"])
app.include_router(datasearch_router, prefix="/api/datasearch",        tags=["datasearch"])
app.include_router(knowledge_router, prefix="/api/knowledge", tags=["knowledge-base"])


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    method = request.method
    path   = request.url.path
    log.info("→ %s %s", method, path)
    response = await call_next(request)
    elapsed = (time.perf_counter() - start) * 1000
    log.info("← %s %s  %d  %.0fms", method, path, response.status_code, elapsed)
    return response


async def _probe_pdf_browser():
    """Probe PDF validation and rendering dependencies without launching Chromium."""
    try:
        try:
            import pymupdf as _pymupdf  # noqa: F401
        except ImportError:
            import fitz as _pymupdf  # noqa: F401
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
        from weasyprint import HTML  # noqa: F401
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
    # Masked (length + last 4 chars only, never the key itself) — lets you
    # tell "0 loaded" / "keys got merged into one blob" / "duplicate key"
    # apart from "key is genuinely revoked" from the Render logs alone.
    log.info("  Groq key detail: %s", describe_pool(groq_keys))
    log.info("  SerpAPI key detail: %s", describe_pool(serpapi_keys))
    log.info("  FastAPI app initialized; uvicorn CLI controls the listening port")
    import os as _os
    log.info("  Chart gallery: %s (layout=%s)", _os.getenv("REPORT_CHART_GALLERY", "core"), _os.getenv("REPORT_CHART_GALLERY_LAYOUT", "inline"))
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
