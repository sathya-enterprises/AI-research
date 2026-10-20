"""
POST /api/chat  — SSE streaming chat
Body: { messages: [{role, content}], fileContext?: string }
"""
import asyncio
import base64
import hashlib
import itertools
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import AsyncGenerator

import httpx
from urllib.parse import urlparse
from fastapi import APIRouter
from fastapi.requests import Request
from fastapi.responses import JSONResponse, StreamingResponse

from utils.keys import (
    get_gemini_keys, get_groq_keys, get_exa_keys,
    is_rate_limited, mark_rate_limited, pick_key, round_robin,
    reserve_groq_key, iter_reserved_groq_keys, available_groq_key_count,
)

router = APIRouter()
log = logging.getLogger("chat")
from datetime import datetime as _dt
from datetime import timezone as _tz
from email.utils import parsedate_to_datetime as _parse_rfc2822
from utils.rag_client import rag_query as _rag_query
from utils.auth import get_user_id
from utils.screener_kb import get_company_context_with_meta as _get_kb_context_with_meta
from utils.orchestrator import orchestrate, OrchestrationResult

# ─── Supabase persistence ──────────────────────────────────────────────────────
from utils.supabase_env import clean_supabase_url as _clean_sb_url
_SUPABASE_URL  = _clean_sb_url(os.environ.get("SUPABASE_URL"))
_SUPABASE_KEY  = os.environ.get("SUPABASE_ANON_KEY", "")

def _sb_headers() -> dict:
    return {
        "apikey": _SUPABASE_KEY,
        "Authorization": f"Bearer {_SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }

async def _upsert_session(session_id: str, user_id: str | None = None) -> None:
    """Insert session row if absent, otherwise bump last_active (and
    backfill user_id once the row exists, e.g. user logs in mid-session)."""
    if not _SUPABASE_URL or not _SUPABASE_KEY:
        return
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            # Try upsert first
            payload = {"id": session_id, "last_active": _dt.utcnow().isoformat(), "query_count": 1}
            if user_id:
                payload["user_id"] = user_id
            r = await client.post(
                f"{_SUPABASE_URL}/rest/v1/sessions",
                headers={**_sb_headers(), "Prefer": "resolution=merge-duplicates,return=minimal"},
                json=payload,
            )
            if r.status_code in (401, 403):
                log.debug("Supabase sessions: RLS blocked insert (table may need policy) — skipping")
                return
            # Bump last_active (and user_id, in case this session started
            # anonymous and the person logged in partway through)
            patch_body = {"last_active": _dt.utcnow().isoformat()}
            if user_id:
                patch_body["user_id"] = user_id
            await client.patch(
                f"{_SUPABASE_URL}/rest/v1/sessions",
                headers=_sb_headers(),
                params={"id": f"eq.{session_id}"},
                json=patch_body,
            )
    except Exception as exc:
        log.debug("Supabase upsert_session failed (non-critical): %s", exc)


async def _save_messages(session_id: str, user_content: str, assistant_content: str,
                         llm_provider: str, elapsed_ms: int) -> None:
    """Persist user + assistant messages into the messages table."""
    if not _SUPABASE_URL or not _SUPABASE_KEY or not session_id:
        return
    rows = [
        {
            "session_id": session_id,
            "role": "user",
            "content": user_content,
            "llm_provider": llm_provider,
            "tokens_used": 0,
            "response_ms": 0,
        },
        {
            "session_id": session_id,
            "role": "assistant",
            "content": assistant_content,
            "llm_provider": llm_provider,
            "tokens_used": 0,
            "response_ms": elapsed_ms,
        },
    ]
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"{_SUPABASE_URL}/rest/v1/messages",
                headers=_sb_headers(),
                json=rows,
            )
        log.info("Supabase: saved user+assistant messages for session %s", session_id)
    except Exception as exc:
        message = str(exc).lower()
        if "getaddrinfo" in message or "name or service not known" in message or "nodename nor servname" in message:
            log.warning("Supabase message persistence DNS failure: check the hostname in backend SUPABASE_URL and local DNS/network; chat response was already delivered")
        else:
            log.warning("Supabase save_messages failed (%s)", type(exc).__name__)


def _image_hash(data: str) -> str:
    """SHA-256 of the raw base64 string — stable fingerprint for dedup."""
    return hashlib.sha256(data.encode()).hexdigest()


async def _load_cached_extractions(session_id: str, img_hashes: list[str]) -> dict[str, str]:
    """
    Query the existing `files` table for rows matching this session + hash.
    Returns {file_hash: extracted_text} for any already-processed images.
    Falls back to cross-session lookup so the same image is never re-processed
    even across different sessions (e.g. user re-uploads same chart).
    """
    if not _SUPABASE_URL or not _SUPABASE_KEY or not img_hashes:
        return {}
    try:
        hashes_csv = "(" + ",".join(f'"{h}"' for h in img_hashes) + ")"
        async with httpx.AsyncClient(timeout=8) as client:
            # 1. Try session-scoped lookup first (fastest, most relevant)
            params: dict = {
                "file_hash": f"in.{hashes_csv}",
                "select": "file_hash,extracted_text",
                "extracted_text": "neq.",          # non-empty
            }
            if session_id:
                params["session_id"] = f"eq.{session_id}"

            r = await client.get(
                f"{_SUPABASE_URL}/rest/v1/files",
                headers={**_sb_headers(), "Prefer": "return=representation"},
                params=params,
            )
            if r.is_success and r.json():
                result = {row["file_hash"]: row["extracted_text"]
                          for row in r.json() if row.get("extracted_text")}
                if result:
                    log.info("Supabase image cache: %d/%d hit(s) for session %s",
                             len(result), len(img_hashes), (session_id or "?")[:8])
                    return result

            # 2. Cross-session fallback — same image uploaded in any session
            if session_id:
                r2 = await client.get(
                    f"{_SUPABASE_URL}/rest/v1/files",
                    headers={**_sb_headers(), "Prefer": "return=representation"},
                    params={
                        "file_hash": f"in.{hashes_csv}",
                        "select": "file_hash,extracted_text",
                        "extracted_text": "neq.",
                        "limit": "10",
                    },
                )
                if r2.is_success and r2.json():
                    result = {row["file_hash"]: row["extracted_text"]
                              for row in r2.json() if row.get("extracted_text")}
                    if result:
                        log.info("Supabase image cache: %d/%d cross-session hit(s)",
                                 len(result), len(img_hashes))
                    return result
    except Exception as exc:
        log.debug("Supabase _load_cached_extractions failed: %s", exc)
    return {}


async def _store_image_extractions(
    session_id: str,
    images: list[dict],
    extractions: dict[str, str],
) -> None:
    """
    Persist newly extracted image text back into the existing `files` table
    (upsert on file_hash) and upload raw bytes to the `paperly-uploads` bucket
    so the file-service and RAG pipeline can also see them.
    """
    if not _SUPABASE_URL or not _SUPABASE_KEY:
        return

    async with httpx.AsyncClient(timeout=30) as client:
        for img in images:
            h = _image_hash(img["data"])
            text = extractions.get(h, "")
            if not text:
                continue

            mime  = img.get("mimeType", "image/jpeg")
            fname = img.get("name", f"{h[:8]}.jpg")
            ext   = mime.split("/")[-1].replace("jpeg", "jpg")
            bucket_path = f"{session_id}/{h}.{ext}" if session_id else f"chat-images/{h}.{ext}"

            # 1. Upload raw image to paperly-uploads bucket (x-upsert so safe to repeat)
            try:
                raw_bytes = base64.b64decode(img["data"])
                up = await client.post(
                    f"{_SUPABASE_URL}/storage/v1/object/paperly-uploads/{bucket_path}",
                    headers={
                        "apikey": _SUPABASE_KEY,
                        "Authorization": f"Bearer {_SUPABASE_KEY}",
                        "Content-Type": mime,
                        "x-upsert": "true",
                    },
                    content=raw_bytes,
                )
                stored_url = (
                    f"{_SUPABASE_URL}/storage/v1/object/public/paperly-uploads/{bucket_path}"
                    if up.is_success else fname
                )
                if up.is_success:
                    log.info("Supabase Storage: image saved → paperly-uploads/%s", bucket_path)
                else:
                    log.debug("Supabase Storage upload %d: %s", up.status_code, up.text[:80])
            except Exception as exc:
                log.debug("Supabase Storage upload error: %s", exc)
                stored_url = fname
                raw_bytes  = b""

            # 2. Upsert into files table (reuses existing schema — no migration needed)
            if session_id:
                try:
                    row = {
                        "session_id":    session_id,
                        "original_name": fname,
                        "stored_name":   stored_url,
                        "mime_type":     mime,
                        "file_type":     "image",
                        "size_bytes":    len(raw_bytes),
                        "extracted_text": text,
                        "has_text":      True,
                        "ocr_processed": True,
                        "word_count":    len(text.split()),
                        "file_hash":     h,
                    }
                    ins = await client.post(
                        f"{_SUPABASE_URL}/rest/v1/files",
                        headers={**_sb_headers(),
                                 "Prefer": "resolution=merge-duplicates,return=minimal"},
                        json=row,
                    )
                    if ins.is_success:
                        log.info("Supabase files: upserted image record hash=%s session=%s",
                                 h[:8], session_id[:8])
                    else:
                        log.warning("Supabase files upsert %d: %s",
                                    ins.status_code, ins.text[:200])
                except Exception as exc:
                    log.warning("Supabase _store_image_extractions upsert failed: %s", exc)

# ─── Domain filtering ────────────────────────────────────────────────────────
# Search results are chosen dynamically per query instead of being constrained
# to a fixed finance-domain allow-list. Provider results still pass through
# junk-domain exclusion, source deduplication, and downstream relevance checks.
#
# The one thing still filtered out is a small denylist of domains that are
# never a legitimate source for *any* query here — dictionaries, video/
# social platforms, forums. This is a category exclusion, not a finance
# allow-list: it doesn't grant trust to any domain, it just removes the
# specific kinds of noise that caused the original bug.
_JUNK_DOMAIN_SUFFIXES = [
    # dictionaries / language-learning — never a market/finance source
    "merriam-webster.com", "dictionary.com", "cambridge.org",
    "oxfordlearnersdictionaries.com", "langeek.co", "vocabulary.com",
    "thesaurus.com", "wiktionary.org",
    # video / social — never a citable primary source for a research report
    "youtube.com", "youtu.be", "facebook.com", "instagram.com",
    "tiktok.com", "x.com", "twitter.com", "reddit.com", "pinterest.com",
    "quora.com",
]


def _is_junk_domain(url: str) -> bool:
    """True if url's host falls in the never-a-source denylist above."""
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    if not host:
        return False
    return any(host == d or host.endswith("." + d) for d in _JUNK_DOMAIN_SUFFIXES)

FINANCE_TERMS = [
    # Indices & exchanges
    "nifty", "sensex", "bse", "nse", "ipo", "rbi", "sebi", "amfi",
    "nifty 50", "nifty bank", "nifty it", "nifty auto", "nifty fmcg",
    # Core finance words
    "stock", "share", "equity", "fund", "mutual fund", "nav", "sip",
    "market", "trading", "invest", "portfolio", "dividend", "earnings",
    "quarter", "q1", "q2", "q3", "q4", "fy", "balance sheet", "revenue",
    "profit", "loss", "ebitda", "pe ratio", "p/e", "eps", "book value",
    "rupee", "inr", "forex", "crude", "gold", "silver", "commodity",
    "inflation", "gdp", "repo rate", "monetary policy", "cpi", "wpi",
    "bank", "nbfc", "loan", "emi", "interest rate", "credit", "debit",
    # Companies
    "lic", "hdfc", "icici", "sbi", "axis", "kotak", "reliance", "tata",
    "infosys", "wipro", "tcs", "adani", "bajaj", "zerodha", "groww",
    "paytm", "zomato", "ola", "swiggy", "nykaa", "delhivery",
    # Analysis & report trigger words
    "sector", "performance", "analysis", "outlook", "forecast", "report",
    "returns", "rally", "correction", "bearish", "bullish", "momentum",
    "valuation", "fundamental", "technical", "breakout", "support", "resistance",
    "52 week", "all time high", "ath", "volume", "liquidity", "fii", "dii",
    "inflow", "outflow", "net buy", "net sell", "derivative", "futures", "options",
    "fno", "f&o", "expiry", "index", "benchmark", "mid cap", "small cap", "large cap",
    "bluechip", "penny stock", "etf", "reit", "aif", "pms", "demat",
    "ipo allotment", "listing", "grey market", "gmp", "buyback", "split",
    "bonus", "rights issue", "qip", "ofs", "block deal", "bulk deal",
    "results", "quarterly", "annual", "fy25", "fy26", "capex", "debt",
    "leverage", "margin", "return on equity", "roe", "roce", "cash flow",
    "npa", "provision", "slippage", "credit growth", "deposit",
    # Macro
    "rate cut", "rate hike", "policy", "budget", "fiscal", "trade deficit",
    "current account", "fdi", "fpi", "rupee depreciation", "dollar",
    "yield", "bond", "gilt", "treasury", "g-sec",
    # News triggers
    "latest", "today", "news", "update", "this week", "this month",
    "recent", "current", "now", "live", "trend",
]

# Terms that, on their own, unambiguously mean "Indian stock/investing markets" —
# no other business/agency/personal-finance context realistically uses them.
# Classification hinges on these; the generic FINANCE_TERMS list above is too
# broad on its own (see STRONG_FINANCE_TERMS note below).
STRONG_FINANCE_TERMS = [
    "nifty", "sensex", "bse", "nse", "ipo", "rbi", "sebi", "amfi",
    "nifty 50", "nifty bank", "nifty it", "nifty auto", "nifty fmcg",
    "stock", "share", "equity", "mutual fund", "nav", "sip", "dividend",
    "demat", "etf", "reit", "aif", "pms", "fii", "dii", "gmp",
    "ipo allotment", "grey market", "buyback", "rights issue", "qip", "ofs",
    "block deal", "bulk deal", "bond", "gilt", "treasury", "g-sec",
    "repo rate", "monetary policy", "rupee depreciation", "trade deficit",
    "lic", "hdfc", "icici", "sbi", "axis", "kotak", "reliance", "tata",
    "infosys", "wipro", "tcs", "adani", "bajaj", "zerodha", "groww",
    "paytm", "zomato", "ola", "swiggy", "nykaa", "delhivery",
    "penny stock", "mid cap", "small cap", "large cap", "bluechip",
    "52 week", "all time high", "ath", "f&o", "fno", "derivative",
    "futures", "options", "expiry", "bearish", "bullish", "breakout",
    "support", "resistance", "roe", "roce", "npa", "credit growth",
]

# Words that strongly signal "this is about running MY business/agency/service,
# not the stock market" — even though they overlap vocabulary-wise with generic
# finance words like "revenue," "profit," or "market." If present, treat the
# query as general regardless of how many weak FINANCE_TERMS matched, since
# these are decisive context clues a stock-market persona has no business
# answering (e.g. hiring plans, client counts, service pricing).
BUSINESS_OPS_OVERRIDE_TERMS = [
    "client", "clients", "customer", "customers", "employee", "employees",
    "hire", "hiring", "hired", "staff", "team of", "my team", "my agency",
    "my business", "my company", "my startup", "startup idea", "business idea",
    "freelance", "freelancer", "agency", "digital marketing", "app development",
    "website development", "web development", "saas", "retainer", "clientele",
    "service business", "consulting business", "my clients",
]


def classify_query(msg: str) -> str:
    """Classify clearly financial-market queries for downstream search hints.

    Match terms on token boundaries. A substring check treated the `lic` in
    "policy" as the stock-market acronym LIC, which misclassified sector-policy
    research as financial-market research.
    Generic research language must remain general unless a genuine financial
    signal is present.
    """
    m = (msg or "").casefold()

    def has_term(term: str) -> bool:
        # Multiword terms allow flexible whitespace; punctuation in terms such as
        # f&o or g-sec remains literal. Neighbouring letters/digits cannot turn
        # part of a word into a false acronym hit.
        pattern = re.escape(term.casefold()).replace(r"\ ", r"\s+")
        return re.search(rf"(?<![a-z0-9]){pattern}(?![a-z0-9])", m, re.I) is not None

    if any(has_term(t) for t in BUSINESS_OPS_OVERRIDE_TERMS):
        return "general"
    if any(has_term(t) for t in STRONG_FINANCE_TERMS):
        return "finance"
    finance_specific = {
        "financial results", "quarterly earnings", "annual earnings", "balance sheet",
        "cash flow", "ebitda", "pe ratio", "p/e", "eps", "valuation", "portfolio",
        "dividend", "mutual fund", "repo rate", "interest rate", "loan book",
        "credit growth", "deposit growth", "npa", "roe", "roce", "market cap",
        "share price", "stock price", "forex", "crude oil price", "bond yield",
        "fii", "dii", "fdi inflow", "fpi inflow", "financial statements",
    }
    specific_hits = {term for term in finance_specific if has_term(term)}
    return "finance" if len(specific_hits) >= 2 else "general"

SKIP_SEARCH_PREFIXES = [
    "what is ", "define ", "explain how ", "how does ",
    "what are the basics", "difference between",
]

# Phrases that always need a web search regardless of length
ALWAYS_SEARCH_PATTERNS = [
    r"\b(latest|recent|current|today|now|this week|this month|live)\b",
    r"\b(news|update|report|analysis|outlook|performance|returns?|rally|correction)\b",
    r"\b(sector|market|stock|nifty|sensex|bse|nse|ipo|rbi|sebi)\b",
    r"\b(result|quarter|earnings|profit|revenue|forecast|prediction)\b",
    r"\b(price|rate|yield|index|fii|dii|inflow|outflow)\b",
]

SKIP_PAGE_FETCH = [".pdf", "bloomberg.com", "wsj.com", "ft.com", "economist.com"]


# ─── Helpers ───────────────────────────────────────────────────────────────────

# Pronouns / references that signal a follow-up needing rewrite
_FOLLOWUP_RE = re.compile(
    r"\b(it|its|their|they|them|that|this|those|these|he|she|him|her|the company|"
    r"the stock|the fund|the bank|the same|above|mentioned|previous)\b",
    re.IGNORECASE,
)

# ─── Deterministic query optimizer (no LLM call) ────────────────────
# Converts a verbose prompt into one or more concise, Google-style search
# queries. Only genuinely ambiguous conversational follow-ups still use the
# Groq rewrite further below — everything else goes through here.

_FILLER_PHRASES = [
    # multi-word phrases listed first so they're stripped whole
    "support with data",
    "easy to understand",
    "detailed analysis",
    "in detail",
    "explain",
    "analyze",
    "analyse",
    "compare",
    "detailed",
    "report",
    "reports",
    "charts",
    "chart",
    "uhni",
    "reasoning",
]
_FILLER_RE = re.compile(
    r"\b(" + "|".join(re.escape(p) for p in _FILLER_PHRASES) + r")\b",
    re.IGNORECASE,
)

# Strip report/task framing before query optimization. If the intent LLM is
# unavailable, the old optimizer left phrases such as "Prepare a deep scientific
# research report on" in the actual web query. Those instruction words consume
# the 120-character budget and reduce provider ranking for the real subject.
_SEARCH_TASK_PREFIX_RE = re.compile(
    r"^\s*(?:please\s+)?"
    r"(?:(?:prepare|create|generate|write|make|produce|provide|conduct|perform|compile|develop|"
    r"give(?:\s+me)?|show(?:\s+me)?|tell(?:\s+me)?|find|search\s+for|look\s+up)\s+)+"
    r"(?:(?:a|an|the)\s+)?"
    r"(?:(?:brief|basic|standard|detailed|deep|in[- ]depth|comprehensive|scientific|research|"
    r"industry|market|financial|strategic|competitive|sector)\s+)*"
    r"(?:(?:research|market|industry|financial|scientific|sector)\s+)?"
    r"(?:report|study|analysis|assessment|review|investigation|outlook|overview)\s+"
    r"(?:on|about|of|for)\s+",
    re.IGNORECASE,
)
_SEARCH_ACTION_PREFIX_RE = re.compile(
    r"^\s*(?:please\s+)?(?:analyze|analyse|assess|evaluate|summarize|summarise|examine)\s+"
    r"(?:(?:the|a|an)\s+)?",
    re.IGNORECASE,
)

# Split "A and B" into two independent queries by default — EXCEPT when the
# phrase ends in a word that ties both halves into one combined ask (a
# comparison, or a shared trailing noun that describes both entities
# together). That's the signal that distinguishes:
#   "Promoter exits and sector rotation"      -> 2 independent topics, split
#   "FTAs and exports"                        -> 2 independent topics, split
#   "Reliance and Tata Motors comparison"     -> 1 joint comparison, no split
#   "Infosys and TCS financial results"       -> 1 joint ask, no split
#   "Nifty IT and Banking sectors"            -> 1 joint ask, no split
_MULTI_INTENT_SPLIT_RE = re.compile(r"^(.*?)\s+and\s+(.*)$", re.IGNORECASE)

_JOINT_SUFFIX_RE = re.compile(
    r"\b(comparison|compared to|vs\.?|versus|financial results|results|"
    r"performance|earnings|outlook|sector|sectors|stocks|shares|"
    r"valuation|review)\s*$",
    re.IGNORECASE,
)

# Only these words justify adding "latest" — don't add it speculatively.
_EXPLICIT_RECENCY_RE = re.compile(
    r"\b(latest|recent|recently|current|currently|now|today)\b", re.IGNORECASE,
)
_HAS_LATEST_RE = re.compile(r"\blatest\b", re.IGNORECASE)
_THIS_YEAR_RE = re.compile(r"\b(this year|current year)\b", re.IGNORECASE)
_THIS_WEEK_RE = re.compile(r"\b(this week|past week|last week)\b", re.IGNORECASE)
_THIS_MONTH_RE = re.compile(r"\b(this month|past month|last month)\b", re.IGNORECASE)

# Same signal as report.py's _HISTORICAL_INTENT_RE, duplicated locally to
# avoid a circular import (report.py already imports from chat.py).
_QUERY_HISTORICAL_RE = re.compile(
    r"\b(quarter|qtr|q[1-4]\b|quarterly|past \d+ (?:quarters?|months?|years?)|"
    r"since (?:19|20)\d{2}|yoy|qoq|year[- ]on[- ]year|year[- ]over[- ]year|"
    r"quarter[- ]on[- ]quarter|quarter[- ]over[- ]quarter|trend|historical|history|"
    r"over time|comparison|compare[ds]?|"
    r"last \d+ (?:quarters?|months?|years?))\b",
    re.IGNORECASE,
)

_MAX_QUERY_LEN = 120
_HISTORICAL_SUFFIX = " quarter-wise comparison historical data"


def _clean_query_text(text: str) -> str:
    """Strip filler words/phrases and collapse leftover whitespace/punctuation."""
    cleaned = _FILLER_RE.sub(" ", text)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" .,:;-")
    return cleaned


def _finalize_query(text: str, current_year: int | None = None) -> str:
    """Strip task framing, then apply freshness/history rules and length budget."""
    raw = str(text or "").strip()
    task_stripped = _SEARCH_TASK_PREFIX_RE.sub("", raw)
    if task_stripped == raw:
        task_stripped = _SEARCH_ACTION_PREFIX_RE.sub("", raw)
    cleaned = _clean_query_text(task_stripped) or task_stripped.strip() or raw

    if _THIS_YEAR_RE.search(text):
        year = current_year or _dt.now().year
        cleaned = (
            _THIS_YEAR_RE.sub(str(year), cleaned)
            if _THIS_YEAR_RE.search(cleaned) else f"{cleaned} {year}"
        )

    if _EXPLICIT_RECENCY_RE.search(text) and not _HAS_LATEST_RE.search(cleaned):
        cleaned = f"{cleaned} latest"

    if _QUERY_HISTORICAL_RE.search(text) and "historical" not in cleaned.lower():
        cleaned = f"{cleaned}{_HISTORICAL_SUFFIX}"

    if len(cleaned) > _MAX_QUERY_LEN:
        cleaned = cleaned[:_MAX_QUERY_LEN].rsplit(" ", 1)[0]

    return cleaned


def optimize_search_query(text: str, current_year: int | None = None, qtype: str = "general") -> list[str]:
    """
    Deterministic, LLM-free conversion of a user prompt into one or more
    concise provider-neutral search queries.

    - Strips instructional filler (explain, analyze, compare, detailed,
      report, support with data, charts, UHNI, reasoning, easy to
      understand). Entities (companies, sectors, countries, years, quarters,
      financial terms) are untouched since none overlap the filler list.
    - Adds "latest" only when the ORIGINAL text explicitly said
      latest/recent/current/now/today.
    - Replaces "this year"/"current year" with the actual year.
    - Appends a short historical-comparison hint when quarter/YoY/"past N
      quarters"-style language is present.
    - Targets 50-120 chars; truncates on a word boundary if it runs long
      (never pads a short query just to hit a floor).
    - Splits "A and B" into two queries UNLESS the phrase ends in a shared
      trailing word/phrase (comparison, results, sectors, vs, etc.) that
      ties both halves into one combined ask — that's the difference
      between "Promoter exits and sector rotation" (2 independent topics)
      and "Reliance and Tata Motors comparison" (1 joint comparison).
      Splitting is only attempted for finance queries (qtype="finance") —
      its examples and design (comparing named entities/sectors) don't
      generalize to a general-purpose narrative message. A general query
      describing one continuous situation ("I'm at 1L/month and I hired
      two people and my profit is...") isn't two search intents just
      because it contains the word "and"; splitting it at the first "and"
      produces one truncated fragment and one garbled grab-bag of the
      remaining unrelated clauses, neither of which is a usable search
      query. See the finance-only guard below.

    NOTE: being rule-based, this won't paraphrase/reorder as fluently as an
    LLM would (e.g. it won't expand "FTAs" to "free trade agreements" or
    reorder "List FTAs signed by India" into "India free trade agreements
    signed") — it only removes filler and applies the rules above. That
    trade-off is the point: it's free and instant instead of a Groq call.
    """
    text = text.strip()
    if not text:
        return [text]

    stripped_end = text.rstrip(" ?.!")
    if qtype == "finance":
        m = _MULTI_INTENT_SPLIT_RE.match(text)
        if m and not _JOINT_SUFFIX_RE.search(stripped_end):
            first, second = m.group(1).strip(), m.group(2).strip()
            # Guard against a plain/Oxford-comma list ("open, high, low, and
            # close") being misread as two independent intents — a comma
            # immediately before "and" signals enumeration within ONE ask,
            # not a joiner between two separate asks. Without this guard,
            # "give me the open, high, low, and close for each session"
            # split into "...the open, high, low," + "close for each
            # session" — two useless, truncated search queries.
            if first.rstrip().endswith(","):
                first = None
            if first and second:
                return [
                    _finalize_query(first, current_year),
                    _finalize_query(second, current_year),
                ]

    return [_finalize_query(text, current_year)]


# Only these short, context-dependent follow-ups justify the extra Groq
# call — a normal prompt (however long) should never match this.
_AMBIGUOUS_FOLLOWUP_PHRASES_RE = re.compile(
    r"^\s*(what about|how about|what changed|what's changed|any update|"
    r"what next|what happened)\b",
    re.IGNORECASE,
)


def _is_ambiguous_followup(msg: str) -> bool:
    """
    True only for short, context-dependent follow-ups such as "What about
    Infosys?", "How about its competitors?", "What changed?" — these can't
    be turned into a standalone query by rule-based cleanup because the
    actual subject lives in the prior turn, not in this message.
    """
    if len(msg) > 100 or len(msg.split()) > 10:
        return False
    return bool(_AMBIGUOUS_FOLLOWUP_PHRASES_RE.match(msg) or _FOLLOWUP_RE.search(msg))


# ─── Freshness: sort newer sources first ────────────────────────────────────
# Recency filtering now happens at search time via the active provider's
# publication-date constraint (see _detect_recency_time_range below), so results are no longer
# discarded here just for being older than a fixed cutoff — that risked
# dropping perfectly relevant results whenever the query's recency intent
# wasn't caught by the pre-filter, or whenever a source's date was simply
# stale-parsed. Post-processing is now sort-only.


def _parse_published_date(value: str | None):
    """Best-effort parse of provider publication-date fields. Never raises —
    returns None for missing/unrecognized formats, which callers treat as
    'unknown age', not 'old'."""
    if not value:
        return None
    for parser in (_parse_rfc2822, _dt.fromisoformat):
        try:
            dt = parser(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=_tz.utc)
            return dt
        except Exception:
            continue
    return None


def _sort_and_filter_by_freshness(results: list[dict], keep_old: bool = True) -> list[dict]:
    """
    Sort results newest-first by published date. Results are never dropped
    for being old — recency filtering is requested upstream from the provider where supported
    `time_range` param (see _detect_recency_time_range), which is a more
    precise signal than an arbitrary fixed-age cutoff applied after the
    fact. Results with no parseable date are kept (unknown age isn't
    treated as "old") and placed after the dated ones, in their original
    order.

    `keep_old` is accepted for call-site compatibility (existing callers
    pass historical_intent here) but no longer changes behaviour — it's
    kept purely so callers don't need updating.
    """
    dated: list[tuple] = []
    undated: list[dict] = []
    for r in results:
        published_at = _parse_published_date(r.get("published"))
        if published_at is None:
            undated.append(r)
            continue
        dated.append((published_at, r))
    dated.sort(key=lambda pair: pair[0], reverse=True)
    return [r for _, r in dated] + undated


async def rewrite_query_for_search(last_msg: str, history: list[dict], qtype: str = "general") -> list[str]:
    """
    Turns `last_msg` into one or more provider-neutral search queries.

    - Genuinely ambiguous conversational follow-ups ("What about Infosys?",
      "How about its competitors?", "What changed?") get a single
      lightweight Groq call, grounded in the last 4 turns of history — the
      ONLY case that costs an extra LLM call.
    - Everything else (a normal prompt, however long) is handled
      deterministically by optimize_search_query() — no LLM call, and no
      change in per-request latency/cost from before.

    Falls back to optimize_search_query(last_msg, qtype=qtype) on any error.
    """
    if not _is_ambiguous_followup(last_msg):
        return optimize_search_query(last_msg, qtype=qtype)

    # Build a compact context from the last 4 turns (2 user + 2 assistant)
    recent = [m for m in history if m.get("role") in ("user", "assistant")][-4:]
    if not recent:
        return optimize_search_query(last_msg, qtype=qtype)

    context_lines = "\n".join(
        f"{m['role'].upper()}: {str(m['content'])[:300]}" for m in recent
    )

    prompt = (
        "You are a search query rewriter for a financial assistant. "
        "Given the recent conversation and a follow-up message, rewrite the follow-up "
        "into a concise, self-contained web search query (max 10 words). "
        "Output ONLY the rewritten query — no explanation, no quotes, no punctuation at the end.\n\n"
        f"CONVERSATION:\n{context_lines}\n\n"
        f"FOLLOW-UP: {last_msg}\n\n"
        "REWRITTEN QUERY:"
    )

    # Use one eligible Groq key without reusing a key selected in the last minute.
    keys = get_groq_keys()
    key = reserve_groq_key(keys)
    if not key:
        return optimize_search_query(last_msg, qtype=qtype)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=5, read=15, write=5, pool=5)) as client:
            res = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
                json={
                    "model": "openai/gpt-oss-120b",  # llama-3.3-70b-versatile decommissioned by Groq 2026-08-16
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 30,
                    "temperature": 0.1,
                    "stream": False,
                },
            )
        if res.is_success:
            rewritten = res.json()["choices"][0]["message"]["content"].strip().strip('"').strip("'")
            if rewritten and len(rewritten) > 3:
                log.info("Query rewrite (LLM, ambiguous follow-up): %r → %r", last_msg[:60], rewritten[:80])
                return [rewritten]
        elif res.status_code in (401, 403):
            mark_rate_limited(key, 24 * 60 * 60_000)
            log.warning("Query rewrite Groq HTTP %d; key banned for 24h", res.status_code)
        elif res.status_code == 429:
            retry_ms = 60_000
            try:
                retry_ms = max(60_000, int(float(res.headers.get("retry-after", "60")) * 1000))
            except (TypeError, ValueError):
                pass
            mark_rate_limited(key, retry_ms)
            log.warning("Query rewrite Groq 429; key cooled down for %dms", retry_ms)
    except Exception as exc:
        log.debug("Query rewrite failed (non-critical): %s", exc)

    return optimize_search_query(last_msg, qtype=qtype)


# ─── Query geography detection ──────────────────
# Country hints are kept as provider-neutral geographic context. Exa does not
# accept the legacy provider-specific country parameter in its Search API, so
# explicit geography should be included in the generated query itself.
GLOBAL_TERMS = [
    "globally", "global", "worldwide", "world", "international",
    "around the world", "across the world", "all countries", "every country",
]

# country name -> provider country hint. Add more as needed; keep keys lowercase.
COUNTRY_ALIASES: dict[str, list[str]] = {
    "india":           ["india", "indian", "bharat", "rupee", "inr", " rbi", "sebi", "nse", "bse", "nifty", "sensex"],
    "united kingdom":  ["uk", "u.k.", "united kingdom", "britain", "british", "england", "scotland",
                         "wales", "pound sterling", "gbp", "ftse", "lse", "bank of england"],
    "united states":   ["usa", "u.s.", "u.s.a.", "united states", "america", "american",
                         "nasdaq", "dow jones", "s&p 500", "wall street", "federal reserve", " fed ", "dollar"],
    "canada":          ["canada", "canadian", "tsx"],
    "australia":       ["australia", "australian", "asx"],
    "singapore":       ["singapore", "sgx"],
    "japan":           ["japan", "japanese", "nikkei", "yen"],
    "china":           ["china", "chinese", "yuan", "shanghai composite", "shenzhen"],
    "germany":         ["germany", "german", "dax"],
    "france":          ["france", "french", "cac 40"],
    "united arab emirates": ["uae", "dubai", "abu dhabi", "emirates"],
}


def detect_country(query: str, default: str | None = "india") -> str | None:
    """
    explicit country mentioned   -> normalized country hint
    "global"/"world"/"worldwide" -> None (no country bias — search everything)
    no mention                   -> `default`

    `default` used to be unconditionally "india", which geo-biased every
    single search — including questions with nothing to do with any country
    ("what is quantum computing?"). Callers that genuinely want an
    India-first search (the legacy finance flows) keep the old behaviour by
    not passing `default`; the orchestrator passes default=None so an
    unqualified question searches worldwide.
    """
    q = f" {query.lower()} "
    if any(term in q for term in GLOBAL_TERMS):
        return None
    for country, keywords in COUNTRY_ALIASES.items():
        if any(kw in q for kw in keywords):
            return country
    return default


_FILE_INTENT_RE = re.compile(
    r"""\b(explain|summaris[e]?|summariz[e]?|describe|analys[e]?|analyz[e]?|read|
    translate|what.s|tell\sme|extract|find|list|show|convert|interpret|transcribe|
    what\s(can|do)\syou\s(see|say)|ocr|what\sdoes\sit\ssay)\b""",
    re.IGNORECASE | re.VERBOSE,
)

# Greetings and self-introductions — "hi", "hey", "hi i am albin", "hello my
# name is priya", "hi i am albin, nice to meet you" — none of these need a web
# search or a data-heavy persona. Uses fullmatch so a greeting attached to an
# actual question ("hi can you tell me the latest nifty level") is correctly
# treated as a real query, not smalltalk.
_SMALLTALK_RE = re.compile(
    r"""^\s*
    (hi+|hey+|hello+|yo+|hiya|sup|good\s?(morning|afternoon|evening|day)|namaste|howdy)
    \s*[,!.\-]?\s*
    (
        (i\s*(a|')?m|i\s+am|my\s+name\s+is|this\s+is|here)\s+
        [a-zA-Z][a-zA-Z'\-]*(\s+[a-zA-Z][a-zA-Z'\-]*){0,2}
    )?
    \s*[,!.\-]*\s*
    (nice\s+to\s+meet\s+you|there|all|everyone)?
    \s*[!.]*\s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Pure self-introductions with no greeting word at all — "my name is albin",
# "this is albin" — also count as smalltalk.
_SELF_INTRO_RE = re.compile(
    r"^\s*(my\s+name\s+is|i\s*(a|')?m|i\s+am|this\s+is)\s+"
    r"[a-zA-Z][a-zA-Z'\-]*(\s+[a-zA-Z][a-zA-Z'\-]*){0,2}\s*[.!]?\s*$",
    re.IGNORECASE,
)


def is_smalltalk(msg: str) -> bool:
    """True for greetings / self-introductions that deserve a short, simple
    reply rather than a full data-driven analyst response — e.g. 'hi',
    'hi i am albin', 'hello, my name is priya'. Uses fullmatch so it never
    fires on a real question that merely starts with a greeting word."""
    m = msg.strip()
    if not m or len(m) > 60:
        return False
    return bool(_SMALLTALK_RE.fullmatch(m) or _SELF_INTRO_RE.fullmatch(m))


def needs_web_search(msg: str, has_files: bool = False) -> bool:
    m = msg.lower().strip()
    if len(m) < 4:
        return False
    TRIVIAL = {"hi", "hey", "hello", "ok", "okay", "thanks", "thank you", "bye", "yes", "no", "sure", "great"}
    if m in TRIVIAL or is_smalltalk(msg):
        return False
    # If files are attached, skip web search for file-directed queries
    if has_files:
        if len(m) <= 80:
            return False
        if _FILE_INTENT_RE.search(m):
            return False
    return True


async def fetch_page_content(url: str, max_chars: int = 8000) -> str:
    """Fetch readable HTML/PDF/text content with the shared source extractor."""
    if any(s in str(url or "") for s in SKIP_PAGE_FETCH):
        return ""
    from utils.source_extractor import fetch_source_content
    return await fetch_source_content(url, max_chars=max_chars, include_script_data=False, logger=log)


_AI_OVERVIEW_PATTERNS = [
    r"^\s*\*{0,2}as of [a-z]+,?\s+[a-z]+\s+\d{1,2},?\s+\d{4}.{0,40}here are the latest",
    r"^\s*\*{0,2}latest .{0,60}\*{0,2}\s*as of\b",
    r"^\s*here(?:'s| is) (?:a |an )?(?:summary|overview|update) of",
]

def _looks_like_ai_overview(text: str) -> bool:
    if not text:
        return False
    head = text.strip()[:300].lower()
    return any(re.search(p, head, re.IGNORECASE) for p in _AI_OVERVIEW_PATTERNS)


def _emit_exa_image_candidates(results: list | None, images_out: list | None) -> None:
    """Expose each result's lead image as a credited image candidate."""
    if images_out is None:
        return
    seen = {str(item.get("url") or "") for item in images_out if isinstance(item, dict)}
    for row in results or []:
        if not isinstance(row, dict):
            continue
        image = str(row.get("image") or "").strip()
        page = str(row.get("url") or "").strip()
        if not image.startswith(("http://", "https://")) or not page.startswith(("http://", "https://")) or image in seen:
            continue
        seen.add(image)
        try:
            from urllib.parse import urlparse as _urlparse
            publisher = (_urlparse(page).hostname or "").replace("www.", "")
        except Exception:
            publisher = ""
        images_out.append({
            "url": image, "description": str(row.get("title") or "")[:200],
            "sourceUrl": page, "sourceTitle": str(row.get("title") or "")[:240],
            "publisher": publisher, "publicationDate": str(row.get("published") or "")[:40],
            "sourceType": "Publisher page image",
        })


def _clean_result_content(r: dict) -> dict:
    """Drop snippet/fullContent fields that look like injected AI-overview text."""
    if _looks_like_ai_overview(r.get("snippet", "")):
        r["snippet"] = ""
    if _looks_like_ai_overview(r.get("fullContent", "")):
        r["fullContent"] = ""
    return r


# ─── Intent-aware time_range selection ──────────────────────────────────────
# Maps recency language to a normalized publication-date filter ("day"/"week"/
# "month"/"year") so the active provider can filter results at search time,
# instead of unrestricted results being fetched and then
# discarded by age in post-processing. Reuses the same regex constants as
# the query-rewriting/historical-suffix logic above (_EXPLICIT_RECENCY_RE,
# _THIS_YEAR_RE, _QUERY_HISTORICAL_RE) so "recent"/"historical" mean the
# same thing everywhere in this file.
#
# Rules, checked in this order (most specific / highest-priority first):
#   1. Historical / time-series language (quarters, "since 2019", "trend",
#      "YoY", "QoQ", "comparison", "over time", "last N quarters", ...)
#      -> None. These queries want the full history, so time_range is left
#      unset and the provider returns its normal, unrestricted relevance ranking.
#      This always wins even if a recency word also appears in the same
#      query (e.g. "current trend" is still historical/unrestricted).
#   2. this week / past week / last week          -> "week"
#   3. this month / past month / last month        -> "month"
#   4. this year / current year                    -> "year"
#   5. latest / recent / current / today (and the
#      close synonyms "recently"/"currently"/"now") -> "day"
_FISCAL_RANGE_RE = re.compile(
    r"\b(?:fy\s?\d{2,4}|fiscal\s+years?|annual\s+reports?|from\s+(?:19|20)\d{2}\s+to)\b",
    re.IGNORECASE,
)


def _detect_recency_time_range(query: str) -> str | None:
    """
    Return the normalized recency window that matches the query's recency
    intent, or None if there isn't one / the query is historical in nature.
    Only post-processing left downstream is sorting by publication date —
    this lets the active provider restrict results at search time where supported.
    """
    if _QUERY_HISTORICAL_RE.search(query):
        return None
    # Fiscal-year ranges / annual-report requests ("from FY2020 to the latest")
    # need multi-year history; "latest" there must not become a 1-day filter.
    if _FISCAL_RANGE_RE.search(query):
        return None
    if _THIS_WEEK_RE.search(query):
        return "week"
    if _THIS_MONTH_RE.search(query):
        return "month"
    if _THIS_YEAR_RE.search(query):
        return "year"
    if _EXPLICIT_RECENCY_RE.search(query):
        return "day"
    return None


async def _exa_one_call(
    key: str, query: str, max_results: int, topic: str = "general",
    country: str | None = None, images_out: list | None = None,
    time_range: str | None = None, request_timeout: float | None = None,
    search_type: str = "auto",
) -> list[dict] | None:
    """Call Exa Search once and normalize its response to the report pipeline schema.

    Returns a list (including an empty list for a successful zero-result search),
    or None when the provider request failed so the caller may try another key or
    configured secondary search provider. Credentials are never logged or captured.
    """
    if is_rate_limited(key):
        return None

    query = str(query or "").strip()[:2000]
    if not query:
        return []
    limit = max(1, min(int(max_results or 10), 100))
    # Exa supports `instant` for user-facing chat/search paths and `auto` for
    # broader retrieval. Keep report/deep-research calls on the default `auto`.
    selected_search_type = str(search_type or "auto").strip().lower()
    if selected_search_type not in {"auto", "fast", "instant"}:
        selected_search_type = "auto"
    payload: dict = {
        "query": query,
        "type": selected_search_type,
        "numResults": limit,
        "contents": {"highlights": True},
    }
    # Report/deep-research retrieval (type=auto) also requests the provider's
    # indexed page text. Many publisher pages block direct fetching (paywalls,
    # JS rendering, PDFs skipped by policy); without text the evidence was a
    # ~1.8k-character highlight and most tables/figures were never seen.
    text_chars = 0
    if selected_search_type == "auto":
        try:
            text_chars = max(0, min(int(os.getenv("EXA_REPORT_TEXT_CHARS", "12000") or 0), 50000))
        except ValueError:
            text_chars = 12000
        if text_chars:
            payload["contents"] = {"highlights": True, "text": {"maxCharacters": text_chars}}

    # Exa uses an absolute publication-date bound for recency filtering.
    # Historical/time-series requests deliberately omit this filter.
    if time_range and not _QUERY_HISTORICAL_RE.search(query):
        from datetime import datetime, timedelta, timezone
        days = {"day": 1, "week": 7, "month": 30, "year": 365}.get(str(time_range).lower())
        if days:
            published_after = datetime.now(timezone.utc) - timedelta(days=days)
            payload["startPublishedDate"] = published_after.isoformat(timespec="seconds").replace("+00:00", "Z")

    # Exa categories are optional search constraints. Only use the explicit
    # news category when requested; don't narrow ordinary research to financial
    # filings just because a query mentions a market, sector, or investment.
    if str(topic or "").strip().lower() == "news":
        payload["category"] = "news"

    endpoint = "https://api.exa.ai/search"
    t0 = time.perf_counter()
    try:
        if request_timeout is None:
            request_timeout_config = httpx.Timeout(connect=8, read=30, write=15, pool=8)
        else:
            bounded_timeout = max(3.0, min(float(request_timeout), 15.0))
            request_timeout_config = httpx.Timeout(
                connect=min(5.0, bounded_timeout), read=bounded_timeout,
                write=min(8.0, bounded_timeout), pool=min(5.0, bounded_timeout),
            )
        async with httpx.AsyncClient(
            timeout=request_timeout_config,
            follow_redirects=True,
        ) as client:
            response = await client.post(
                endpoint,
                headers={"x-api-key": key, "Content-Type": "application/json"},
                json=payload,
            )
        if response.status_code == 429:
            mark_rate_limited(key, 60_000)
            log.warning("Exa rate limit HTTP 429 for query=%r; temporary backoff applied", query[:80])
            return None
        if response.status_code in (401, 403):
            mark_rate_limited(key, 24 * 60 * 60_000)
            log.error("Exa authentication/permission failure HTTP %d for query=%r; check EXA_API_KEY / EXA_API_KEY_2 / EXA_API_KEY_3 / EXA_API_KEYS configuration", response.status_code, query[:80])
            return None
        if response.status_code == 402:
            log.error("Exa returned HTTP 402 (credits/billing) for query=%r; check Exa dashboard usage", query[:80])
            return None
        if response.status_code == 432:
            mark_rate_limited(key, 60 * 60_000)
            log.error("Exa returned HTTP 432 (plan/usage restriction) for query=%r; trying another configured key/provider", query[:80])
            return None
        if not response.is_success:
            request_id = ""
            try:
                request_id = str((response.json() or {}).get("requestId") or "")[:100]
            except Exception:
                pass
            log.warning("Exa HTTP %d for query=%r request_id=%s", response.status_code, query[:80], request_id or "unavailable")
            return None

        data = response.json()
        raw_results = data.get("results") or []
        try:
            from utils.research_capture import record_exa_raw_response
            record_exa_raw_response(
                query,
                {k: v for k, v in payload.items() if k not in {"api_key", "key", "headers"}},
                raw_results,
                transport="exa-rest",
                request_id=str(data.get("requestId") or ""),
            )
        except Exception:
            pass

        results: list[dict] = []
        for row in raw_results:
            if not isinstance(row, dict):
                continue
            url = str(row.get("url") or "").strip()
            if not url or _is_junk_domain(url):
                continue
            highlights = row.get("highlights") or []
            if isinstance(highlights, str):
                highlights = [highlights]
            highlights_text = "\n\n".join(str(x).strip() for x in highlights if str(x).strip())
            summary = str(row.get("summary") or "").strip()
            text = str(row.get("text") or "").strip()
            snippet = highlights_text or summary or text
            full_content = text or highlights_text or summary
            results.append({
                "title": str(row.get("title") or "").strip(),
                "url": url,
                "snippet": snippet[:1600],
                "fullContent": full_content[:max(5000, text_chars)],
                "score": row.get("score"),
                "published": row.get("publishedDate") or row.get("published_date") or "",
                "author": row.get("author") or "",
                "searchProvider": "exa",
                "trusted_source": True,
                "pageFetchStatus": "not_fetched",
                # Exa returns the page's lead image when one exists. It is an
                # illustration tied to this exact publisher page (credited via
                # sourceUrl), never evidence for a number.
                "image": str(row.get("image") or "").strip() if str(row.get("image") or "").strip().startswith(("http://", "https://")) else "",
            })

        # Search does not always include page-image candidates. Keep this output
        # parameter compatible with callers; market-news independently extracts
        # og:image from the actual publisher page if this list remains empty.
        elapsed = int((time.perf_counter() - t0) * 1000)
        provider_search_time = data.get("searchTime")
        log.info(
            "Exa search request ok: query=%r mode=%s results=%d provider_search_time=%s elapsed_ms=%d request_id=%s",
            query[:80], selected_search_type,
            len(results), provider_search_time if provider_search_time is not None else "unreported",
            elapsed, str(data.get("requestId") or "")[:100] or "unavailable",
        )
        cleaned_results = [_clean_result_content(item) for item in results]
        _emit_exa_image_candidates(cleaned_results, images_out)
        return cleaned_results
    except Exception as exc:
        log.warning("Exa request failed for query=%r: %s: %s", query[:80], type(exc).__name__, str(exc)[:180] or "no error message")
        return None


async def _enrich_thin_results(
    results: list[dict], *, limit: int = 2, per_page_timeout: float = 7.0,
) -> list[dict]:
    """Enrich only a few top-ranked thin results, with a strict per-page timeout.

    Full publisher-page extraction is useful for report evidence, but doing it for
    every search result turns normal chat into a serial-looking crawl. Reports run
    their own evidence-enrichment stage; ordinary chat only needs a small number of
    publisher pages beyond the provider's search extracts.
    """
    async def _bounded_fetch(url: str) -> str:
        try:
            return await asyncio.wait_for(fetch_page_content(url, 3000), timeout=per_page_timeout)
        except Exception as exc:
            log.debug("Search-result enrichment skipped (%s)", type(exc).__name__)
            return ""

    to_enrich = [
        i for i, row in enumerate(results[:max(0, int(limit))])
        if str(row.get("url") or "").strip() and len(str(row.get("fullContent") or "")) < 500
    ]
    if not to_enrich:
        return results
    tasks = [_bounded_fetch(str(results[i]["url"])) for i in to_enrich]
    extra_contents = await asyncio.gather(*tasks, return_exceptions=True)
    for index, extra in zip(to_enrich, extra_contents):
        current = str(results[index].get("fullContent") or "")
        if isinstance(extra, str) and len(extra) > len(current):
            results[index]["fullContent"] = extra
    log.info("Search enrichment: fetched %d top-ranked page(s) (limit=%d)", len(to_enrich), limit)
    return results


async def _search_secondary_providers(
    query: str, *, max_results: int, images_out: list | None,
    historical_intent: bool, region: str | None = None,
) -> tuple[list[dict], str]:
    """Try configured alternatives sequentially and stop at the first useful set."""
    try:
        from utils.websearch import SerperProvider, SerpAPIProvider, GoogleNewsRssProvider
    except Exception:
        return [], ""
    providers = (
        ("serper", SerperProvider),
        ("serpapi", SerpAPIProvider),
        ("google_news_rss", GoogleNewsRssProvider),
    )
    time_range = _detect_recency_time_range(query)
    for provider_name, provider_type in providers:
        try:
            provider = provider_type()
            if not provider.available():
                continue
            candidates = await provider.search(
                [query], max_results=max_results, region=region,
                time_range=time_range, images_out=images_out,
                historical_intent=historical_intent,
            )
            if not isinstance(candidates, list) or not candidates:
                continue
            from utils.source_identity import source_identity_key
            seen: set[str] = set()
            useful = []
            for row in candidates:
                if not isinstance(row, dict):
                    continue
                identity = source_identity_key(row)
                if not identity or identity in seen:
                    continue
                seen.add(identity)
                row.setdefault("pageFetchStatus", "not_fetched")
                row["searchProvider"] = provider_name
                useful.append(row)
            if useful:
                result = _sort_and_filter_by_freshness(useful, keep_old=historical_intent)
                log.info("Secondary web search via %s: %d usable source(s)", provider_name, len(result))
                return result, provider_name
        except Exception as exc:
            # Provider exceptions can contain request URLs or credential values.
            log.warning("Secondary web search via %s failed: %s", provider_name, type(exc).__name__)
    return [], ""




_WEB_PERSIST_TASKS: set[asyncio.Task] = set()


def _schedule_web_research_persistence(query: str, results: list[dict] | None, provider: str = "exa") -> None:
    """Persist real web-search results without making search latency depend on Supabase."""
    rows = [r for r in (results or []) if isinstance(r, dict) and str(r.get("url") or "").strip()]
    if not rows:
        return
    async def _worker():
        try:
            from utils.research_store import persist_web_search_results
            stats = await asyncio.to_thread(persist_web_search_results, query, rows, provider=provider)
            log.info("Web research library: stored %d sources / %d new versions for query=%r", stats.get("sources", 0), stats.get("versions", 0), str(query or "")[:80])
        except Exception as exc:
            # The research cache is intentionally non-blocking: a Supabase outage,
            # missing DATABASE_URL, or slow connection must never break search/report/chat.
            log.warning("Web research library persistence skipped: %s: %s", type(exc).__name__, str(exc)[:160])
    try:
        task = asyncio.create_task(_worker())
        _WEB_PERSIST_TASKS.add(task)
        task.add_done_callback(_WEB_PERSIST_TASKS.discard)
    except Exception as exc:
        log.debug("Web research persistence task could not be scheduled: %s", exc)

# Exact-query cache: a process-local 24-hour cache lowers repeated provider calls,
# while preventing a result set from one topic from leaking into another topic.
_EXA_SEARCH_CACHE: dict[tuple[str, int, str, str, str], tuple[float, list[dict]]] = {}
_EXA_SEARCH_CACHE_TTL_SECONDS = 24 * 60 * 60
_EXA_SEARCH_CACHE_MAX_ENTRIES = 400


def _exa_cache_key(query: str, max_results: int, time_range: str | None, topic: str | None, region: str | None) -> tuple[str, int, str, str, str]:
    normalized = " ".join(str(query or "").casefold().split())
    return (normalized, max(1, min(int(max_results or 10), 100)), str(time_range or ""), str(topic or ""), str(region or ""))


def _exa_cache_get(key: tuple[str, int, str, str, str]) -> list[dict] | None:
    now = time.time()
    entry = _EXA_SEARCH_CACHE.get(key)
    if not entry:
        return None
    created, rows = entry
    if now - created >= _EXA_SEARCH_CACHE_TTL_SECONDS:
        _EXA_SEARCH_CACHE.pop(key, None)
        return None
    return [dict(row) for row in rows]


def _exa_cache_put(key: tuple[str, int, str, str, str], rows: list[dict]) -> None:
    if not rows:
        return  # Never cache empty/error responses as if they were evidence.
    now = time.time()
    # Opportunistically remove expired entries, then cap the in-memory cache.
    for old_key, (created, _rows) in list(_EXA_SEARCH_CACHE.items()):
        if now - created >= _EXA_SEARCH_CACHE_TTL_SECONDS:
            _EXA_SEARCH_CACHE.pop(old_key, None)
    while len(_EXA_SEARCH_CACHE) >= _EXA_SEARCH_CACHE_MAX_ENTRIES:
        oldest = min(_EXA_SEARCH_CACHE, key=lambda item: _EXA_SEARCH_CACHE[item][0])
        _EXA_SEARCH_CACHE.pop(oldest, None)
    _EXA_SEARCH_CACHE[key] = (now, [dict(row) for row in rows])


async def exa_search(
    query: str, max_results: int = 20, min_results: int = 3, images_out: list | None = None,
    historical_intent: bool = False, max_keys: int | None = None,
    topic: str | None = None, region: str | None = None, time_range: str | None = None,
    page_enrich_limit: int = 2, fast_chat: bool = False,
) -> list[dict]:
    """Search with Exa first, normalize source metadata, then use configured secondary providers.

    ``min_results`` and ``max_keys`` remain accepted for caller compatibility. Unlike
    the previous implementation, the same query is not fanned out across every API
    key; a single Exa request is made and another key is tried only after a failure.
    Empty successful results may use a configured alternate search provider, but never
    silently substitute arbitrary stored sources from a different topic.
    """
    query = str(query or "").strip()
    if not query:
        return []
    time_range = time_range if time_range is not None else _detect_recency_time_range(query)
    cache_key = _exa_cache_key(query, max_results, time_range, topic, region)
    cached = _exa_cache_get(cache_key)
    if cached is not None:
        log.info("Exa search cache hit: query=%r results=%d age_limit_hours=24", query[:80], len(cached))
        # Cached results keep each page's lead image, so a cache hit no longer
        # leaves the report without image candidates.
        _emit_exa_image_candidates(cached, images_out)
        return cached

    t0 = time.perf_counter()
    keys = get_exa_keys()
    results: list[dict] | None = None
    used_provider = "exa"
    if not keys:
        log.warning("Exa search has no configured Exa API keys; trying configured secondary providers")
    else:
        # Balance successful requests across configured Exa keys, but retain
        # an ordered failover chain if the selected key is rate-limited or fails.
        # pick_key increments the in-process usage counter, so subsequent
        # searches prefer the least-used healthy key rather than always burning
        # the first configured key.
        if fast_chat:
            # Ordinary chat should not serially wait on multiple Exa keys. Use
            # one healthy key, then a tightly bounded alternate-provider fallback.
            healthy_keys = [key for key in round_robin(keys) if not is_rate_limited(key)]
            ordered_keys = healthy_keys[:1]
            if not ordered_keys:
                log.warning("Fast chat search: all Exa keys rate-limited; bypassing Exa key retries")
        else:
            try:
                preferred_key = pick_key(keys, "Exa")
            except ValueError:
                preferred_key = None
            ordered_keys = ([preferred_key] if preferred_key else []) + [
                key for key in round_robin(keys) if key != preferred_key
            ]
        for key in ordered_keys:
            candidate = await _exa_one_call(
                key, query, max_results, topic or classify_query(query),
                country=None, images_out=images_out, time_range=time_range,
                request_timeout=10.0 if fast_chat else None,
                search_type="instant" if fast_chat else "auto",
            )
            if candidate is None:
                continue
            results = candidate
            break

    # Only fall back if Exa is unavailable/failed or returned no matching rows.
    # Secondary providers are genuine fresh search providers; this is not a
    # fallback to a database's unrelated historical source pool.
    if not results:
        try:
            fallback_coro = _search_secondary_providers(
                query, max_results=max_results, images_out=images_out,
                historical_intent=historical_intent, region=region,
            )
            if fast_chat:
                secondary, secondary_name = await asyncio.wait_for(fallback_coro, timeout=4.0)
            else:
                secondary, secondary_name = await fallback_coro
        except asyncio.TimeoutError:
            log.warning("Fast chat alternate search timed out after 4s for query=%r", query[:80])
            secondary, secondary_name = [], ""
        except Exception as exc:
            log.warning("Alternate search failed for query=%r (%s)", query[:80], type(exc).__name__)
            secondary, secondary_name = [], ""
        if secondary:
            results, used_provider = secondary, secondary_name
        elif results is None:
            results = []

    results = results or []
    if results:
        # Page extraction is bounded independently of result count; reports use
        # their own deeper evidence-fetch stage after search. Normal chat gets
        # one top result and a 3s page limit; report research retains full limits.
        results = await _enrich_thin_results(
            results[:max(1, min(int(max_results or 10), 100))],
            limit=min(page_enrich_limit, 1) if fast_chat else page_enrich_limit,
            per_page_timeout=3.0 if fast_chat else 7.0,
        )
        results = _sort_and_filter_by_freshness(results, keep_old=historical_intent)
        for item in results:
            item.setdefault("searchProvider", used_provider)
        _schedule_web_research_persistence(query, results, provider=used_provider)
        _exa_cache_put(cache_key, results)
    elapsed = int((time.perf_counter() - t0) * 1000)
    log.info("Web search complete: provider=%s query=%r results=%d elapsed_ms=%d",
             used_provider, query[:80], len(results), elapsed)
    return results


async def exa_search_multi(
    queries: list[str], max_results: int = 20, min_results: int = 3,
    images_out: list | None = None, historical_intent: bool = False,
    max_keys_per_query: int | None = None, topic: str | None = None,
    region: str | None = None, time_range: str | None = None,
    page_enrich_limit: int = 2, fast_chat: bool = False,
) -> list[dict]:
    """Run distinct planned search angles in parallel and round-robin merge by rank."""
    unique_queries: list[str] = []
    seen_queries: set[str] = set()
    for raw in queries or []:
        q = " ".join(str(raw or "").split()).strip()
        key = q.casefold()
        if q and key not in seen_queries:
            seen_queries.add(key)
            unique_queries.append(q)
    if not unique_queries:
        return []

    # Do not multiply calls across API keys. One fresh provider request per distinct
    # planned query is sufficient; the per-query 24-hour cache handles repeats.
    result_lists = await asyncio.gather(*[
        exa_search(
            q, max_results=max_results, min_results=min_results,
            images_out=images_out, historical_intent=historical_intent,
            topic=topic, region=region, time_range=time_range,
            page_enrich_limit=page_enrich_limit,
            fast_chat=fast_chat,
        ) for q in unique_queries
    ])
    # Preserve which actual query returned each URL; the source-audit mode uses
    # this metadata to distinguish true targeted searches from generic fan-out.
    for actual_query, rows in zip(unique_queries, result_lists):
        for row in rows:
            if isinstance(row, dict):
                row.setdefault("searchQuery", actual_query)
    try:
        from utils.research_capture import record_search_call
        record_search_call(unique_queries, result_lists, max_results=max_results)
    except Exception:
        pass

    seen_urls: set[str] = set()
    merged: list[dict] = []
    from utils.source_identity import source_identity_key
    for round_items in itertools.zip_longest(*result_lists):
        for row in round_items:
            identity = source_identity_key(row) if isinstance(row, dict) else ""
            if row and row.get("url") and identity and identity not in seen_urls:
                seen_urls.add(identity)
                merged.append(row)
    return merged


async def load_headlines(limit: int = 30) -> str:
    candidates = [
        Path(os.getcwd()) / "growth_gradual_cache.json",
        Path("/tmp/growth_gradual_cache.json"),
    ]
    raw = None
    for p in candidates:
        try:
            raw = p.read_text(encoding="utf-8")
            log.debug("Headlines loaded from %s", p)
            break
        except Exception as e:
            log.debug("Headlines cache not found at %s (%s)", p, e)
    if not raw:
        log.debug("No headline cache found — skipping headlines injection")
        return ""
    try:
        data = json.loads(raw, strict=False)
        articles = (data.get("articles") or [])[:limit]
        if not articles:
            return ""
        from datetime import datetime, timezone
        fetched_ts = data.get("fetchedAt")
        try:
            fetched_dt = datetime.fromtimestamp(fetched_ts / 1000, tz=timezone.utc).strftime("%d %b %Y, %I:%M %p")
        except Exception:
            fetched_dt = "recently"
        log.debug("Injecting %d live headlines (fetched %s)", len(articles), fetched_dt)
        lines = "\n".join(f"• [{a['source']}] {a['title']} ({a['time']})" for a in articles)
        return f"\n\n---\n📰 LIVE HEADLINES (fetched {fetched_dt} IST):\n{lines}\n---"
    except Exception as exc:
        log.warning("Failed to parse headline cache: %s", exc)
        return ""


def _is_follow_up_generation_request(body: dict) -> bool:
    """Recognize UI chip generation so it never launches full research orchestration."""
    return bool(isinstance(body, dict) and body.get("_followUpMode", False))


def build_system(context_blocks: str = "", smalltalk: bool = False,
                  limitations: list[str] | None = None) -> str:
    """
    Single domain-neutral persona for /api/chat.

    ``context_blocks`` is pre-formatted text handed back by
    ``utils.orchestrator.orchestrate()`` (``OrchestrationResult.context()``) —
    each tool (web_search, document_search, company_fundamentals, market_data,
    news_headlines) already wraps its own output in a labelled block, so this
    function's only job is to state the persona/voice/grounding rules once and
    append whatever context the orchestrator actually gathered. There is no
    finance/general branch here — the same assistant answers everything, and
    which (if any) tools ran is decided upstream, per request, by the planner.
    """
    from datetime import date
    today = date.today().strftime("%A, %B %d, %Y")

    if smalltalk:
        # Greetings / self-introductions get a short, warm, human reply —
        # no context, no data-dump instructions. This is what keeps "hi" or
        # "hi i am albin" simple instead of triggering a full response.
        return f"""You are **Growth Gradual Assistant**, built into the Growth Gradual platform. Today is {today}.

The user just sent a greeting or introduced themselves — nothing else.

**Behaviour:**
- Reply in 1-3 short, warm sentences. No headers, no bullet lists, no tables, no markdown formatting.
- If they gave their name, use it naturally.
- Briefly mention you can help with all kinds of things — general questions, current events, programming, documents, and finance/markets — in one short sentence, not a list.
- Do NOT pull in market data, headlines, statistics, or citations. Do NOT add a "verify live prices" disclaimer — there's no data here to verify.
- Keep it conversational, like a person saying hello back — not a report."""

    persona = f"""You are **Growth Gradual Assistant** — a general-purpose, knowledgeable AI assistant built into the Growth Gradual platform. Today is {today}.

You help with anything the user brings: general knowledge, current events, science and technology, programming and debugging, companies and business questions, Indian and global financial markets, personal finance, the user's own uploaded documents, writing and analysis, and everyday conversation. You are not limited to any one domain — read what the question actually needs and answer it directly, whether or not any retrieved context is attached below.

**Voice — sound like a smart, helpful person, not a report generator:**
- Write the way a knowledgeable person actually talks — natural sentence rhythm, varied lengths, the occasional "but," "so," or "honestly" where it fits. Avoid stiff openers like "Based on the search results" or "According to the data provided" — just say the thing.
- Lead with the answer, then back it up. Don't pad with throat-clearing before getting to the point.
- Bullets and tables are tools for genuinely list-like or numeric content, not the default shape of every answer — a couple of well-written paragraphs often reads better than a wall of bullet points.
- Have a point of view where the evidence supports one. Flag genuine uncertainty plainly instead of hedging everything.

**When no retrieved context is attached below:** that means the planning step decided this question doesn't need live data — e.g. it's timeless, conceptual, or something you can answer confidently yourself (definitions, explanations, math, code, writing help, general conversation). Just answer directly from your own knowledge; you don't need to apologize for or flag the absence of search results on a question that never needed them.

**Grounding rules — apply to every domain, not just finance:**
- Any context blocks below (web search results, the user's own uploaded documents, company/market data, cached headlines) come from tools that ran before this reply, specifically for this question. Treat them as your primary evidence for anything they cover, and clearly keep retrieved facts separate from your own reasoning or general knowledge — don't blur the two.
- **HARD GROUNDING RULE — no fabricated figures:** any specific number you state as a retrieved fact (%, currency amount, index level, date, quarter-by-quarter figure, statistic, etc.) MUST appear verbatim in a context block below. Never invent, estimate, interpolate, or "reconstruct" a plausible-sounding number and attach a source name to it — that is a fabricated citation and is strictly forbidden even if it makes the answer look more complete. If the context doesn't contain a figure the user wants, say plainly that it isn't available in current sources rather than manufacturing one. A shorter, honest answer beats a detailed fabricated one.
- **When a COMPANY FUNDAMENTALS DATABASE block is present**, it is the authoritative source for that company's ratios, quarterly/annual financials, growth CAGR, shareholding pattern, peer comparison, and pros/cons — prefer it over web search snippets for those specific datapoints, and cite it inline as "per Screener.in data" or similar rather than naming a random web source for a number that actually came from that block.
- **The same rule applies to the user's own figures — business, personal, or otherwise:** when a user shares their own numbers in conversation (revenue, clients, salary, expenses, etc.) and later asks you to "add more graphs," "add more data points," or "expand" something, do NOT invent a plausible-looking history to fill that request — e.g. a "revenue over the past 6 months" table, a "client acquisition trend," or any other time series the user never gave you. You only have the figures they actually stated, at the point in time they stated them. If they ask for more data points than you actually have:
  - Break down or recombine the real numbers they gave you (e.g. revenue ÷ clients = ARPU, income − costs = margin) — that's fine, it's derived from what they told you, not invented.
  - For anything forward-looking (targets, projections, a growth plan), label it clearly as a projection/plan/estimate, not as if it already happened.
  - If they're asking for something you don't have the inputs for, say so and ask for the missing numbers, or explain what a realistic estimate would need to assume — never present a fabricated "historical" table as fact.
- **PERIOD-BY-PERIOD BREAKDOWNS — a specific, easy failure mode of the rule above:** a request like "sector rotation over the past 8 quarters" invites a tidy Q1/Q2/Q3... table even when no source actually contains one. Before writing ANY per-quarter or per-period row, check that BOTH the period label AND the figure next to it appear together, verbatim, in a context block below. If they don't, do NOT construct one anyway — instead give the current/most-recent figures the context DOES contain, explain the situation conceptually, and state plainly that a granular historical breakdown isn't available in current sources.
- **"Past N quarters/years/weeks" is relative to TODAY ({today}), not to your training data.** Before referencing any period as "recent" or "the past N quarters," work out the actual calendar span backward from today's date. Do not default to a period that merely feels recent from training.
- When you use retrieved web content, name the publication inline (e.g. "Reuters reports...", "Moneycontrol notes...", "per the RBI bulletin...") rather than bracket-style citation markers such as [1], [2], or [1, 2] anywhere in your reply — never use those, it's a hard rule.
- If retrieval genuinely came back empty or a tool failed for something the question needs, say so plainly instead of guessing or silently working around it.
- Use **bold** for key terms and figures so they're easy to scan.
- For finance/market-specific answers, close out with a quick, natural reminder to verify live prices before trading — phrase it like a person would, not a fixed disclaimer line repeated verbatim every time. Don't add this to non-financial answers.

**Charts and tables:**
- Default to a markdown table — not prose paragraphs — whenever the answer is a set of 2+ comparable items each with the same few numeric fields: market summaries, top gainers/losers, multiple quotes, earnings figures across companies, or any other comparable numeric list. This applies even if the user didn't explicitly say "table." Add 1-2 sentences of context above or below the table, not instead of it.
- If the user explicitly asks for a chart, graph, plot, or to "visualize" something, give the underlying numbers as a clean markdown table (proper header row + separator row) so it can be rendered as a chart — don't just describe the trend in prose. Use real, distinct numeric values across at least 2 rows/columns; a chart needs actual data points to plot.
- If the user explicitly asks for a table, give exactly one markdown table — don't also restate the same numbers as a bullet list right after it. Pick one format.
- Don't repeat a table's figures again in a separate paragraph below it; reference the table instead (e.g. "as the numbers above show...").
- Write markdown tables as plain markdown (header row, separator row, data rows) — never wrap a table in triple-backtick code fences. A fenced table renders as a literal unstyled text block instead of a real table, and mismatched/unclosed fences break the rest of the message."""

    limitations_block = ""
    if limitations:
        items = "\n".join(f"- {l}" for l in limitations[:6])
        limitations_block = (
            "\n\n---\n⚠️ RETRIEVAL LIMITATIONS THIS TURN (a tool failed, found nothing, or "
            "wasn't available — don't silently paper over these; say so plainly if they affect "
            "what you can answer):\n" + items + "\n---"
        )

    return persona + (context_blocks or "") + limitations_block


# ─── Groq streaming ────────────────────────────────────────────────────────────
_GROQ_MODELS = [
    # llama-3.3-70b-versatile was decommissioned by Groq on 2026-08-16
    # (returns 404 model_not_found). openai/gpt-oss-120b is Groq's
    # recommended successor and is confirmed live and reachable on this
    # account/key (verified via production logs: 200 on tool-calling and
    # on streaming). qwen/qwen3-32b was tried as a second fallback but
    # 404'd — "does not exist or you do not have access to it" — on this
    # same account/key, so it isn't actually usable here; removed rather
    # than left in as a fallback that only ever wastes a request before
    # falling through to Gemini. Re-add it (or another model) here once
    # it's confirmed reachable — check available models in the Groq
    # console for this account rather than guessing at IDs.
    "openai/gpt-oss-120b",
]

def _compact_context_section(section: str, budget: int) -> str:
    """Reduce one labelled evidence block while preserving its heading and top sources."""
    if len(section) <= budget:
        return section
    budget = max(500, budget)
    # Tool contexts conventionally separate source entries with a blank line and
    # a dash. Keep the tool label and best-ranked entries instead of chopping an
    # arbitrary character window through the middle of a URL or claim.
    chunks = re.split(r"(?=\n\n- )", section)
    if len(chunks) > 1:
        heading = chunks[0]
        if len(heading) > min(1200, budget // 3):
            heading = heading[:min(1200, budget // 3)].rstrip() + " …"
        remaining = budget - len(heading) - 80
        kept = [heading]
        candidates = chunks[1:]
        for idx, chunk in enumerate(candidates):
            if remaining <= 80:
                break
            share = max(250, remaining // max(1, len(candidates) - idx))
            piece = chunk[:min(len(chunk), share)]
            if len(piece) < len(chunk):
                piece = piece.rsplit(" ", 1)[0].rstrip() + " …[source excerpt shortened]"
            kept.append(piece)
            remaining -= len(piece) + 2
        return "\n\n".join(kept) + "\n\n[Context shortened to fit the model's request budget.]"

    # Some tools return a prose block rather than a list. Keep its opening
    # definition/metadata and ending caveats/source notes.
    marker = "\n\n[Middle of retrieved context shortened to fit the model's request budget.]\n\n"
    available = max(100, budget - len(marker))
    head = int(available * 0.72)
    tail = available - head
    return section[:head].rstrip() + marker + section[-tail:].lstrip()


GROQ_PROACTIVE_COMPACT_CHAR_BUDGET = 22_000


def _compact_chat_prompt_for_groq(system_prompt: str, messages: list[dict],
                                  max_system_chars: int = 12_000,
                                  max_history_chars: int = 1_500) -> tuple[str, list[dict]]:
    """Make one evidence-preserving, bounded retry after Groq rejects an oversized request."""
    separator = "\n\n---\n"
    sections = system_prompt.split(separator)
    persona = sections[0]
    blocks = sections[1:]
    if not blocks:
        # No explicit tool context; keep the start and end of the system prompt.
        compact_system = system_prompt[:max_system_chars]
    else:
        compact_system = persona
        remaining = max(1200, max_system_chars - len(persona))
        for index, block in enumerate(blocks):
            separator_cost = len(separator)
            blocks_left = len(blocks) - index
            allotment = max(700, (remaining - separator_cost * blocks_left) // blocks_left)
            trimmed = _compact_context_section(block, allotment)
            compact_system += separator + trimmed
            remaining -= len(trimmed) + separator_cost
            if remaining <= 0:
                break
        if len(compact_system) > max_system_chars:
            compact_system = compact_system[:max_system_chars - 70].rstrip() + "\n\n[Final context tail omitted to satisfy provider token limits.]"

    # The current question is mandatory; history is only useful when room allows.
    normalized = [m for m in messages if isinstance(m, dict) and m.get("role") in ("user", "assistant")]
    selected = normalized[-8:]
    compacted: list[dict] = []
    remaining_history = max_history_chars
    for idx, item in enumerate(selected):
        content = str(item.get("content") or "")
        is_latest_user = item.get("role") == "user" and not any(
            later.get("role") == "user" for later in selected[idx + 1:]
        )
        per_message = min(1800 if is_latest_user else 420, remaining_history)
        if per_message <= 0:
            if is_latest_user:
                per_message = 1800
            else:
                continue
        if len(content) > per_message:
            if is_latest_user:
                content = content[:per_message] + " …[query shortened for provider budget]"
            else:
                content = content[:per_message].rstrip() + " …[older conversation shortened]"
        compacted.append({"role": item["role"], "content": content})
        remaining_history -= len(content)
    if normalized and not any(m.get("role") == "user" for m in compacted):
        latest = next((m for m in reversed(normalized) if m.get("role") == "user"), None)
        if latest:
            compacted.append({"role": "user", "content": str(latest.get("content") or "")[:1800]})
    return compact_system, compacted or messages[-2:]


async def stream_groq(system_prompt: str, messages: list[dict]) -> AsyncGenerator[str, None] | None:
    """Return a true Groq SSE generator; on HTTP 413 retry once with bounded context."""
    keys = get_groq_keys()
    if not keys:
        log.warning("Groq: no keys configured")
        return None

    available_count = available_groq_key_count(keys)
    if not available_count:
        # Do not reuse a key during its 60-second cooldown. This is shared with
        # planner/report/chart calls; Gemini is the immediate fallback.
        log.warning("Groq: all keys are cooling down or rate-limited — handing off to Gemini")
        return None

    compact_prompt, compact_messages = _compact_chat_prompt_for_groq(system_prompt, messages)
    emergency_prompt, emergency_messages = _compact_chat_prompt_for_groq(
        system_prompt, messages, max_system_chars=8_500, max_history_chars=700,
    )
    can_compact = compact_prompt != system_prompt or compact_messages != messages
    can_emergency_compact = emergency_prompt != compact_prompt or emergency_messages != compact_messages
    log.info("Groq: up to %d eligible key(s) × %d model(s); prompt=%d chars, history=%d messages",
             available_count, len(_GROQ_MODELS), len(system_prompt), len(messages))

    # A 413 on this account is often the 8k TPM request budget, not a bad key.
    # Do not send the same oversized prompt to every key: immediately retry once
    # with less history/evidence and a smaller output reservation.
    rejected_after_compaction = False
    estimated_input_chars = len(system_prompt) + sum(len(str(m.get("content") or "")) for m in messages if isinstance(m, dict))
    # For large retrieved contexts, start with the bounded version instead of
    # knowingly spending a provider request on a likely 413. A later 413 still
    # receives exactly one compaction retry when the original was sent first.
    proactively_compact = estimated_input_chars > GROQ_PROACTIVE_COMPACT_CHAR_BUDGET and can_compact
    if proactively_compact:
        log.info("Groq: proactively compacting large request (%d chars estimated input)", estimated_input_chars)

    for key in iter_reserved_groq_keys(keys):
        key_429 = False
        key_auth_failed = False
        for model in _GROQ_MODELS:
            combo = f"{key}:{model}"
            if is_rate_limited(combo):
                continue
            compact_attempt = proactively_compact
            emergency_attempt = False
            while True:
                request_system = emergency_prompt if emergency_attempt else compact_prompt if compact_attempt else system_prompt
                request_messages = emergency_messages if emergency_attempt else compact_messages if compact_attempt else messages
                max_tokens = 850 if emergency_attempt else 1400 if compact_attempt else 2048
                client = httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=90, write=30, pool=10))
                try:
                    req = client.build_request(
                        "POST",
                        "https://api.groq.com/openai/v1/chat/completions",
                        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
                        json={
                            "model": model,
                            "messages": [{"role": "system", "content": request_system}] + request_messages,
                            "stream": True,
                            "max_tokens": max_tokens,
                            "temperature": 0.5,
                            "frequency_penalty": 0.4,
                            "presence_penalty": 0.3,
                        },
                    )
                    provider_request_started = time.perf_counter()
                    res = await client.send(req, stream=True)
                    if res.status_code == 413:
                        body = await res.aread()
                        await res.aclose()
                        await client.aclose()
                        detail = body.decode(errors="replace")[:220]
                        if not compact_attempt and can_compact:
                            compact_attempt = True
                            log.warning(
                                "Groq HTTP 413 model=%s — retrying compact prompt (%d→%d system chars, %d→%d messages); %s",
                                model, len(system_prompt), len(compact_prompt), len(messages), len(compact_messages), detail,
                            )
                            continue
                        if compact_attempt and not emergency_attempt and can_emergency_compact:
                            emergency_attempt = True
                            log.warning(
                                "Groq HTTP 413 persisted after normal compaction; retrying emergency prompt (%d system chars, %d history chars)",
                                len(emergency_prompt), sum(len(str(m.get("content") or "")) for m in emergency_messages),
                            )
                            continue
                        log.warning("Groq HTTP 413 persisted after compaction model=%s: %s", model, detail)
                        mark_rate_limited(combo, 15_000)
                        rejected_after_compaction = True
                        break
                    if res.status_code == 429:
                        retry_after = int(res.headers.get("retry-after", "60"))
                        await res.aclose(); await client.aclose()
                        wait_ms = max(retry_after * 1000, 60_000)
                        log.warning("Groq 429 model=%s key=...%s — RL for %ds", model, key[-4:], retry_after)
                        mark_rate_limited(combo, wait_ms)
                        mark_rate_limited(key, wait_ms)
                        key_429 = True
                        # If first attempt hit the organization's TPM ceiling,
                        # avoid resending the same large context through another
                        # key; alternate-key retry will start compacted.
                        proactively_compact = True
                        break
                    if res.status_code in (401, 403):
                        await res.aclose(); await client.aclose()
                        log.warning("Groq %d key=...%s — invalid key", res.status_code, key[-4:])
                        mark_rate_limited(key, 24 * 60 * 60_000)
                        key_auth_failed = True
                        break
                    if res.status_code in (400, 404, 503):
                        body = await res.aread()
                        await res.aclose(); await client.aclose()
                        log.warning("Groq HTTP %d model=%s key=...%s — %s", res.status_code, model, key[-4:], body.decode(errors="replace")[:220])
                        if res.status_code in (404, 503):
                            mark_rate_limited(combo, 30_000)
                        break
                    if not res.is_success:
                        body = await res.aread()
                        await res.aclose(); await client.aclose()
                        log.warning("Groq HTTP %d model=%s key=...%s — %s", res.status_code, model, key[-4:], body.decode(errors="replace")[:220])
                        break

                    log.info("Groq: streaming started model=%s key=...%s compact=%s", model, key[-4:], compact_attempt)

                    async def _stream_and_close(
                        _res: httpx.Response,
                        _client: httpx.AsyncClient,
                        _key: str, _model: str, _combo: str,
                        _provider_started: float = provider_request_started,
                    ) -> AsyncGenerator[str, None]:
                        first_text_logged = False
                        sse_buffer = ""
                        try:
                            async for chunk in _res.aiter_bytes():
                                decoded = chunk.decode(errors="replace")
                                if '"rate_limit_exceeded"' in decoded or '"tokens_exhausted"' in decoded:
                                    log.warning("Groq: mid-stream quota hit model=%s", _model)
                                    mark_rate_limited(_combo, 60_000)
                                    mark_rate_limited(_key, 60_000)
                                    return
                                # Measure the first actual model text without modifying
                                # or buffering the bytes passed downstream.
                                if not first_text_logged:
                                    sse_buffer += decoded
                                    while "\n" in sse_buffer:
                                        line, sse_buffer = sse_buffer.split("\n", 1)
                                        if not line.startswith("data:"):
                                            continue
                                        payload = line[5:].strip()
                                        if not payload or payload == "[DONE]":
                                            continue
                                        try:
                                            event = json.loads(payload)
                                            delta = (event.get("choices") or [{}])[0].get("delta") or {}
                                            if delta.get("content"):
                                                first_text_logged = True
                                                log.info("Groq first text delta in %dms model=%s", int((time.perf_counter() - _provider_started) * 1000), _model)
                                                break
                                        except Exception:
                                            continue
                                yield decoded
                        finally:
                            await _res.aclose()
                            await _client.aclose()

                    return _stream_and_close(res, client, key, model, combo)
                except httpx.ConnectError as exc:
                    await client.aclose()
                    log.warning("Groq connect error model=%s: %s", model, exc)
                    mark_rate_limited(combo, 15_000)
                    break
                except Exception as exc:
                    await client.aclose()
                    log.warning("Groq exception model=%s key=...%s: %s", model, key[-4:], type(exc).__name__)
                    break
            if rejected_after_compaction or key_429 or key_auth_failed:
                break
        if rejected_after_compaction:
            break
        if key_429:
            # TPM/RPM limits are commonly organization/model scoped. Retrying
            # another key in the same organization just adds latency; let the
            # Gemini streaming fallback handle this request now.
            log.warning("Groq 429: switching to Gemini instead of retrying other keys/models")
            break
        if key_auth_failed:
            continue

    log.error("Groq unavailable after streaming/compact retry; falling back to streaming Gemini")
    return None


_GEMINI_CHAT_MODELS = [
    # Prioritize models that have passed the user's ordinary-generation checks and
    # that are better candidates for low-latency chat. A successful generateContent
    # test does NOT guarantee that streamGenerateContent will have the same latency,
    # so first-token timing is logged per request and must be monitored in production.
    "gemini-3.5-flash-lite",
    "gemini-3.6-flash",
    "gemini-3-flash-preview",
    # Keep the previous workhorse as a last-resort fallback: it has recently shown
    # repeated 503s on the actual streaming endpoint in this project.
    "gemini-3.1-flash-lite",
]

# Models that need explicit thinking suppression to avoid wasting output tokens.
# gemini-3.1-flash-lite defaults to minimal thinking — no param needed.
# Mixing thinkingBudget (2.5) and thinkingLevel (3.x) in one request → HTTP 400.
_GEMINI_THINKING_BUDGET_MODELS = set()  # no 2.5-family models left in this list
_GEMINI_THINKING_LEVEL_MODELS  = {"gemini-3-flash-preview", "gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite"}


async def gemini_sse(system_prompt: str, messages: list[dict]) -> AsyncGenerator[str, None]:
    """Stream Gemini text as it is generated via streamGenerateContent SSE.

    This deliberately does not call generateContent and split the completed text
    into words: the first text delta is forwarded as soon as Google's stream emits it.
    """
    keys = get_gemini_keys()
    if not keys:
        raise ValueError("No Gemini keys configured")

    available_keys = [k for k in round_robin(keys) if not is_rate_limited(k)]
    if not available_keys:
        log.warning("Gemini: all keys rate-limited — refusing another request and returning provider exhaustion")
        raise RuntimeError("All Gemini streaming keys are rate-limited")
    # Bound failover work: a long retry matrix delays the chat without improving
    # the answer when many keys/models share the same quota or request-shape error.
    available_keys = available_keys[:3]
    # Put one explicit retry immediately after the first key/model attempt. This
    # retry is only activated by an HTTP 503. If that retry is also overloaded,
    # skip the remaining keys for that model and fail over: keys in the same Google
    # project generally do not bypass a model-side service overload.
    attempts: list[tuple[str, str, bool]] = []
    for model in _GEMINI_CHAT_MODELS:
        for key_index, key in enumerate(available_keys):
            attempts.append((key, model, False))
            if key_index == 0:
                attempts.append((key, model, True))
    log.info(
        "Gemini streaming: %d healthy key(s), %d prioritized model(s); bounded 503 retry enabled",
        len(available_keys), len(_GEMINI_CHAT_MODELS),
    )

    # Keep enough recent history for follow-up questions while avoiding the same
    # large conversation being replayed in every provider attempt.
    contents = []
    eligible = [m for m in messages if isinstance(m, dict) and m.get("role") in ("user", "assistant")]
    for message in eligible[-8:]:
        text = str(message.get("content") or "")
        if not text.strip():
            continue
        role = "model" if message.get("role") == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": text[:6000]}]})
    if not contents or contents[-1].get("role") != "user":
        latest_user = next((str(m.get("content") or "") for m in reversed(eligible) if m.get("role") == "user"), "")
        if latest_user:
            contents.append({"role": "user", "parts": [{"text": latest_user[:6000]}]})

    consecutive_exceptions: dict[str, int] = {}
    dead_models: set[str] = set()
    retry_pending_models: set[str] = set()
    first_text_emitted = False
    request_started = time.perf_counter()

    for key, model, is_503_retry in attempts:
        if model in dead_models:
            continue
        if is_503_retry:
            # This tuple is a dormant retry slot unless the previous attempt for
            # this model returned 503. Do not duplicate successful/429/auth attempts.
            if model not in retry_pending_models:
                continue
            retry_pending_models.discard(model)
        combo = f"{key}:{model}"
        if is_rate_limited(combo):
            continue
        gen_cfg: dict = {"maxOutputTokens": 2048}
        if model in _GEMINI_THINKING_BUDGET_MODELS:
            gen_cfg["thinkingConfig"] = {"thinkingBudget": 0}
        elif model in _GEMINI_THINKING_LEVEL_MODELS:
            gen_cfg["thinkingConfig"] = {"thinkingLevel": "minimal"}

        client = httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=60, write=30, pool=10))
        response = None
        attempt_had_text = False
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent?alt=sse"
            request = client.build_request(
                "POST", url,
                headers={"Content-Type": "application/json", "x-goog-api-key": key, "Accept": "text/event-stream"},
                json={
                    "system_instruction": {"parts": [{"text": system_prompt}]},
                    "contents": contents,
                    "generationConfig": gen_cfg,
                },
            )
            response = await client.send(request, stream=True)
            if response.status_code == 429:
                retry_after = int(response.headers.get("retry-after", "60"))
                body = (await response.aread()).decode(errors="replace")[:160]
                await response.aclose(); await client.aclose()
                wait_ms = max(retry_after * 1000, 60_000)
                log.warning("Gemini 429 model=%s key=...%s — RL for %ds %s", model, key[-4:], retry_after, body)
                mark_rate_limited(combo, wait_ms); mark_rate_limited(key, wait_ms)
                continue
            if response.status_code in (401, 403):
                body = (await response.aread()).decode(errors="replace")[:160]
                await response.aclose(); await client.aclose()
                log.warning("Gemini %d key=...%s — invalid key: %s", response.status_code, key[-4:], body)
                mark_rate_limited(key, 24 * 60 * 60_000)
                continue
            if response.status_code == 503:
                status_ms = int((time.perf_counter() - request_started) * 1000)
                body = (await response.aread()).decode(errors="replace")[:220]
                await response.aclose(); await client.aclose()
                if not is_503_retry:
                    retry_pending_models.add(model)
                    log.warning(
                        "Gemini stream HTTP 503 model=%s key=...%s after %dms; retrying this model once after 500ms: %s",
                        model, key[-4:], status_ms, body,
                    )
                    await asyncio.sleep(0.5)
                else:
                    # Repeated overload is model-side for this request. Avoid
                    # spending another long connection timeout rotating API keys.
                    dead_models.add(model)
                    mark_rate_limited(combo, 30_000)
                    log.warning(
                        "Gemini stream HTTP 503 repeated for model=%s after %dms; switching models instead of rotating keys: %s",
                        model, status_ms, body,
                    )
                continue
            if response.status_code in (400, 404, 413):
                body = (await response.aread()).decode(errors="replace")[:220]
                await response.aclose(); await client.aclose()
                log.warning("Gemini stream HTTP %d model=%s key=...%s — %s", response.status_code, model, key[-4:], body)
                # The same request payload will fail the same way on another key.
                dead_models.add(model)
                if response.status_code == 404:
                    mark_rate_limited(combo, 30_000)
                continue
            if not response.is_success:
                body = (await response.aread()).decode(errors="replace")[:220]
                await response.aclose(); await client.aclose()
                log.warning("Gemini stream HTTP %d model=%s key=...%s — %s", response.status_code, model, key[-4:], body)
                continue

            log.info("Gemini stream started model=%s key=...%s", model, key[-4:])
            async for line in response.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if not raw or raw == "[DONE]":
                    continue
                try:
                    data = json.loads(raw)
                except Exception:
                    continue
                if isinstance(data, dict) and data.get("error"):
                    error = data.get("error") or {}
                    raise RuntimeError(f"Gemini stream error {error.get('code', '')}: {error.get('message', 'provider error')}")
                candidates = data.get("candidates") or []
                for candidate in candidates:
                    parts = ((candidate.get("content") or {}).get("parts") or [])
                    for part in parts:
                        text = part.get("text")
                        if isinstance(text, str) and text:
                            attempt_had_text = True
                            if not first_text_emitted:
                                first_text_emitted = True
                                first_ms = int((time.perf_counter() - request_started) * 1000)
                                log.info("Gemini first text delta in %dms model=%s", first_ms, model)
                            yield f'data: {json.dumps({"choices": [{"delta": {"content": text}}]})}\n\n'
            await response.aclose(); await client.aclose()
            if attempt_had_text:
                elapsed = int((time.perf_counter() - request_started) * 1000)
                log.info("Gemini stream complete model=%s in %dms", model, elapsed)
                yield "data: [DONE]\n\n"
                return
            log.warning("Gemini stream returned no text model=%s key=...%s", model, key[-4:])
        except Exception as exc:
            if response is not None:
                try:
                    await response.aclose()
                except Exception:
                    pass
            try:
                await client.aclose()
            except Exception:
                pass
            log.warning("Gemini stream exception model=%s key=...%s: %s%s", model, key[-4:], type(exc).__name__, f": {exc}" if str(exc) else "")
            if attempt_had_text:
                # Never splice a second model's unrelated completion onto a partial
                # response. End the partial stream cleanly; the caller will close SSE.
                yield "data: [DONE]\n\n"
                return
            consecutive_exceptions[model] = consecutive_exceptions.get(model, 0) + 1
            if consecutive_exceptions[model] >= 2:
                dead_models.add(model)
            continue
        finally:
            if response is not None and not response.is_closed:
                try:
                    await response.aclose()
                except Exception:
                    pass
            if not client.is_closed:
                try:
                    await client.aclose()
                except Exception:
                    pass

    raise RuntimeError("All Gemini streaming key×model combinations failed or were rate-limited")



# ─── Citation-marker stripping (streaming-safe) ────────────────────────────────
# The system prompts tell the model never to use bracket citation numbers like
# [1] or [1, 2] — but models occasionally slip one in anyway. This filter is a
# belt-and-suspenders backstop that removes them from the live token stream,
# the same way report.py strips them from generated reports. It buffers a
# small tail of text so a marker split across two network chunks (e.g. one
# chunk ending in "[" and the next starting with "1]") still gets caught.
_CITATION_RE = re.compile(r"\[\s*\d+(?:\s*,\s*\d+)*\s*\]")

# A repetition-loop collapse streams as a long run of short chunks that are
# purely digits/punctuation ("6", ": ", "6:", "-") with no letters at all —
# real prose never looks like this even mid-word. 10 in a row is well past
# anything a normal response would produce (numbers in real text are always
# interleaved with words), so it's a safe threshold for catching a genuine
# collapse without false-triggering on a report full of percentages.
_DEGENERATE_TOKEN_RE = re.compile(r"^[\d\s:,.\-]{1,6}$")
_DEGENERATE_WINDOW = 10


class _CitationStripper:
    _MAX_HOLD = 24  # generous upper bound for something like "[12, 34, 56]"

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, text: str) -> str:
        self._buf += text
        out = []
        i, n = 0, len(self._buf)
        while i < n:
            if self._buf[i] == "[":
                m = _CITATION_RE.match(self._buf, i)
                if m:
                    i = m.end()
                    continue
                rest = self._buf[i:]
                if len(rest) <= self._MAX_HOLD and re.match(r"^\[[\d,\s]*$", rest):
                    # Could still become a citation marker once more text
                    # arrives — hold this tail back and wait for the next chunk.
                    break
            out.append(self._buf[i])
            i += 1
        self._buf = self._buf[i:]
        return "".join(out)

    def flush(self) -> str:
        rest, self._buf = self._buf, ""
        return rest


async def _sanitize_and_forward(
    raw_gen, assistant_chunks: list[str], *, request_started_at: float | None = None,
) -> AsyncGenerator[bytes, None]:
    """
    Wrap an upstream OpenAI-style SSE generator (Groq's raw passthrough or
    Gemini's word-by-word stream), strip bracket-citation markers out of the
    text as it streams, and yield clean `data: {...}\\n\\n` bytes shaped the
    way the frontend expects. Also appends the cleaned text to
    `assistant_chunks` so the saved transcript matches exactly what the user saw.

    Also guards against a repetition-loop collapse (seen with Groq's Llama
    models on numerically-dense, search-heavy context): output degenerates
    into short repeated tokens like "6: 6: 6: 6:" instead of real text. We
    hold back a small window of chunks before flushing them downstream, so a
    collapse can be caught and the whole stream aborted (the caller already
    falls back Groq→Gemini on any exception here) BEFORE the garbage ever
    reaches the client — rather than the user watching it stream in live.
    """
    stripper = _CitationStripper()
    leftover = ""
    pending: list[str] = []
    degenerate_streak = 0
    first_upstream_text_logged = False
    first_client_text_logged = False
    async for chunk in raw_gen:
        raw = chunk if isinstance(chunk, str) else chunk.decode(errors="replace")
        leftover += raw
        lines = leftover.split("\n")
        leftover = lines.pop()  # keep a possibly-incomplete trailing line for next round
        for line in lines:
            line = line.rstrip("\r")
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload in ("[DONE]", ""):
                continue
            try:
                obj = json.loads(payload, strict=False)
            except Exception:
                continue
            if obj.get("type") in ("meta", "status"):
                yield f"data: {payload}\n\n".encode()
                continue
            delta = (obj.get("choices") or [{}])[0].get("delta", {})
            content = delta.get("content", "")
            if not content:
                continue
            if not first_upstream_text_logged and request_started_at is not None:
                first_upstream_text_logged = True
                log.info("Chat end-to-end first provider text delta in %dms", int((time.perf_counter() - request_started_at) * 1000))
            cleaned = stripper.feed(content)
            if not cleaned:
                continue

            degenerate_streak = degenerate_streak + 1 if _DEGENERATE_TOKEN_RE.match(cleaned) else 0
            if degenerate_streak >= _DEGENERATE_WINDOW:
                log.warning(
                    "Chat: detected repetition-loop collapse (%d numeric/punct-only "
                    "chunks in a row) — aborting before it reaches the client",
                    degenerate_streak,
                )
                raise RuntimeError("degenerate repetition-loop output detected")

            pending.append(cleaned)
            # Only flush once a chunk has survived past the detection window,
            # so a collapse caught above never includes content already sent.
            if len(pending) > _DEGENERATE_WINDOW:
                flushed = pending.pop(0)
                assistant_chunks.append(flushed)
                if not first_client_text_logged and request_started_at is not None:
                    first_client_text_logged = True
                    log.info("Chat end-to-end first client text chunk in %dms", int((time.perf_counter() - request_started_at) * 1000))
                yield f"data: {json.dumps({'choices': [{'delta': {'content': flushed}}]})}\n\n".encode()

    for flushed in pending:
        assistant_chunks.append(flushed)
        if not first_client_text_logged and request_started_at is not None:
            first_client_text_logged = True
            log.info("Chat end-to-end first client text chunk in %dms", int((time.perf_counter() - request_started_at) * 1000))
        yield f"data: {json.dumps({'choices': [{'delta': {'content': flushed}}]})}\n\n".encode()
    tail = stripper.flush()
    if tail:
        assistant_chunks.append(tail)
        if not first_client_text_logged and request_started_at is not None:
            first_client_text_logged = True
            log.info("Chat end-to-end first client text chunk in %dms", int((time.perf_counter() - request_started_at) * 1000))
        yield f"data: {json.dumps({'choices': [{'delta': {'content': tail}}]})}\n\n".encode()



# ─── Inline chart generation ──────────────────────────────────────────────────
_CHART_SYSTEM_PROMPT = """You are a data extraction specialist for a financial analytics platform.
Given a user question and the assistant's text reply, extract ALL numeric data that can be visualised
as a chart. Return ONLY valid JSON in this exact shape — no markdown fences, no explanation:

{
  "charts": [
    {
      "type": "bar" | "line" | "pie",
      "title": "<specific descriptive title e.g. 'HDFC Bank Net Profit 2020–2024'>",
      "unit": "%" | "₹" | "Cr" | "x" | "",
      "series": [{ "name": "<series name>", "data": [{ "label": "<label>", "value": <number> }] }]
    }
  ]
}

RULES — read carefully:
- Extract ONLY numbers that ACTUALLY APPEAR in the reply text. NEVER invent or estimate values.
- "bar": 3+ named items compared on one metric (e.g. ROE across 5 banks)
- "line": 3+ chronological time points showing a trend (years, quarters, months)
- "pie": 3+ parts that together form a whole (portfolio allocation, segment share)
- Every label within a chart MUST be unique. Every value MUST be a real number (not a string).
- Max 4 charts total. Return {"charts": []} if nothing chartable found.
- For year-on-year data (Net Profit 2020, 2021, 2022, 2023, 2024) → "line" chart
- For cross-sectional comparisons (NPA % of HDFC vs ICICI vs SBI) → "bar" chart
- Minimum data points: line ≥ 3, bar ≥ 3, pie ≥ 3. Never output fewer.
- NEVER duplicate a label within the same series.
- UNIT MUST MATCH THE ACTUAL VALUES BEING CHARTED — this is a hard rule:
  • "unit": "%" is ONLY for values that are themselves percentages (e.g. "6.6% growth", "20% upside") — never apply
    "%" to a rupee figure, a price, a target price, or any other absolute quantity.
  • "unit": "₹" is for rupee amounts (stock prices, target prices, revenue in Rs). NEVER chart a target price
    (e.g. "target price of ₹1,800") with unit "%" just because a percentage also appears nearby in the text.
  • If the reply mentions BOTH a price/target-price AND a percentage for the same items (e.g. "target price ₹1,800,
    20% upside" per stock), pick ONE metric for the chart — prefer the percentage (upside/return/change), since
    that's what's actually comparable across items of different price scales — and set unit="%" accordingly.
    Do not chart the absolute target prices unless the percentage is unavailable; if you do chart prices, unit
    must be "₹", never "%".
  • Before finalizing, re-check every value against its label in the source text: if a number was written as
    "₹1,800" or "Rs. 1,800" in the reply, it is NOT a percentage — it must not end up in a unit="%" chart.
"""

@router.post("/charts")
async def generate_inline_charts(request: Request):
    """
    POST /api/chat/charts
    Body: { question: str, reply: str, queryType?: str }
    Returns: { charts: ChartSpec[] }

    Called by the frontend after each finance bot reply to extract
    and publish charts to Datawrapper (with SVG fallback if token absent).
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"charts": []})

    question: str = (body.get("question") or "").strip()
    reply:    str = (body.get("reply")    or "").strip()

    # The user may have explicitly asked for a chart/graph/plot — in that case
    # we should try much harder to produce one, even if the reply text doesn't
    # happen to hit the finance keyword list below and even if it's a bit short.
    explicit_visual_request = bool(re.search(
        r"\b(chart|graph|plot|visuali[sz]e|trend\s*line|pie\s*chart|bar\s*chart)\b",
        question.lower(),
    )) or bool(body.get("wantsVisual"))

    min_len = 40 if explicit_visual_request else 100
    if not reply or len(reply) < min_len:
        return JSONResponse({"charts": []})

    # Only generate charts for finance/data-heavy replies — unless the user
    # explicitly asked for a visual, in which case skip this gate entirely
    # and let the chart-extraction LLM decide what's chartable.
    if not explicit_visual_request:
        q_lower = question.lower() + " " + reply.lower()
        data_keywords = [
            "net profit", "revenue", "roe", "npa", "nim", "market cap", "pe", "eps",
            "return", "growth", "crore", "billion", "%", "percent", "quarter",
            "fy2", "q1", "q2", "q3", "q4", "2020", "2021", "2022", "2023", "2024", "2025",
            "₹", "inr", "basis point", "ratio", "margin", "yield", "nav", "cagr",
        ]
        if not any(k in q_lower for k in data_keywords):
            return JSONResponse({"charts": []})

    user_prompt = (
        f"USER QUESTION: {question[:500]}\n\n"
        f"ASSISTANT REPLY (extract charts from this):\n{reply[:4000]}\n\n"
        "Return ONLY a JSON array of chart specs. Return [] if nothing chartable."
    )

    keys = get_groq_keys()
    raw_json = ""
    for key in iter_reserved_groq_keys(keys):
        try:
            async with httpx.AsyncClient(timeout=35) as client:
                res = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
                    json={
                        "model": "openai/gpt-oss-120b",  # llama-3.3-70b-versatile decommissioned by Groq 2026-08-16
                        "messages": [
                            {"role": "system", "content": _CHART_SYSTEM_PROMPT},
                            {"role": "user", "content": user_prompt},
                        ],
                        "max_tokens": 2500,
                        "temperature": 0.1,
                        # json_object mode: works with our object-shaped prompt {"charts":[...]}
                        "response_format": {"type": "json_object"},
                    },
                )
            if res.is_success:
                raw_json = res.json()["choices"][0]["message"]["content"]
                break
            elif res.status_code in (401, 403):
                mark_rate_limited(key, 24 * 60 * 60_000)
                log.warning("Chart generation Groq HTTP %d; key banned for 24h", res.status_code)
            elif res.status_code == 429:
                retry_ms = 60_000
                try:
                    retry_ms = max(60_000, int(float(res.headers.get("retry-after", "60")) * 1000))
                except (TypeError, ValueError):
                    pass
                mark_rate_limited(key, retry_ms)
        except Exception as exc:
            log.debug("Chart gen Groq error: %s", exc)

    if not raw_json:
        return JSONResponse({"charts": []})

    # Parse — LLM returns {"charts": [...]} due to json_object + our prompt shape
    try:
        parsed = json.loads(raw_json, strict=False)
        if isinstance(parsed, dict):
            # Primary: {"charts": [...]}
            charts = parsed.get("charts") or parsed.get("data") or []
            # Fallback: dict is itself a single chart spec
            if not charts and parsed.get("series"):
                charts = [parsed]
        elif isinstance(parsed, list):
            # Shouldn't happen with json_object mode, but handle it safely
            charts = parsed
        else:
            charts = []
    except Exception as exc:
        log.debug("Chart JSON parse failed: %s — raw: %s", exc, raw_json[:200])
        return JSONResponse({"charts": []})

    # Validate each chart — drop obviously bad ones
    valid = []
    for c in charts:
        if not isinstance(c, dict):
            continue
        series = c.get("series") or []
        all_pts = [pt for s in series for pt in (s.get("data") or [])]
        if len(all_pts) < 2:
            continue
        vals = [pt.get("value") for pt in all_pts if pt.get("value") is not None]
        labels = [pt.get("label", "") for pt in all_pts]
        if len(set(vals)) < 2 or len(set(labels)) < 2:
            continue
        # Sanity check: unit="%" with values far outside any plausible
        # percentage range (e.g. a ₹1,800 target price mislabeled as "%")
        # means the LLM almost certainly mismatched a price column with the
        # percentage unit. A single-day/period % move or upside figure is
        # essentially never above ~200% — bail on the chart rather than ship
        # a misleading "+1800.00%" bar.
        if c.get("unit") == "%" and any(abs(v) > 300 for v in vals if isinstance(v, (int, float))):
            log.warning(
                "Inline chart dropped — unit='%%' but values look like prices, not percentages: %r",
                vals,
            )
            continue
        valid.append(c)

    if not valid:
        return JSONResponse({"charts": []})

    # Publish to Datawrapper (no-op if token absent — returns unchanged)
    try:
        from utils.datawrapper import attach_datawrapper_charts
        valid = await attach_datawrapper_charts(valid, fetch_png_bytes=False)
    except Exception as exc:
        log.debug("Datawrapper attach failed (non-critical): %s", exc)

    log.info("Inline charts: generated %d chart(s) for question=%r", len(valid), question[:60])
    return JSONResponse({"charts": valid})


# ─── Main handler ──────────────────────────────────────────────────────────────
@router.post("")
async def chat(request: Request):
    t0 = time.perf_counter()
    try:
        body = await request.json()
    except Exception:
        body = {}

    messages: list[dict] = body.get("messages", [])
    file_context: str = body.get("fileContext", "")
    file_images: list[dict] = body.get("fileImages", [])
    has_rag:      bool = bool(body.get("hasRag", False))
    # UI-generated contextual follow-up chips are constrained generation from
    # the just-finished Q&A, not independent research requests. Honor this flag
    # to avoid a needless Exa search, database retrieval, and planner round.
    follow_up_mode: bool = _is_follow_up_generation_request(body)
    # sessionId is optional — only persists when provided
    session_id: str = (body.get("sessionId") or "").strip()

    if not messages:
        return JSONResponse({"error": "No messages"}, status_code=400)

    last_user_msg = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    # classify_query() is kept only as metadata (meta_event.queryType) and as
    # input to the deterministic query-rewrite rules below — it no longer
    # picks which persona/system-prompt the request gets. Tool selection for
    # /api/chat now happens entirely inside utils.orchestrator.orchestrate().
    qtype = classify_query(last_user_msg)
    # Only treat as smalltalk when there's no file/RAG context riding along —
    # a greeting attached to an uploaded file still needs the normal flow.
    is_smalltalk_msg = (
        is_smalltalk(last_user_msg)
        and not file_context and not file_images and not has_rag
    )

    # One queue carries actual orchestration milestones from the task that runs
    # research into the SSE generator. The browser now sees tool start/completion
    # events rather than only rotating, synthetic status text.
    research_progress_queue: asyncio.Queue[dict] = asyncio.Queue()

    async def queue_research_progress(event: dict) -> None:
        await research_progress_queue.put(event)

    async def prepare_context() -> tuple[str, str]:
        local_file_context = file_context
        await queue_research_progress({
            "phase": "query_rewrite", "status": "started",
            "message": "Optimizing your question for research…",
        })
        # ── Query optimization: deterministic rules for normal prompts, LLM
        # rewrite reserved for genuinely ambiguous follow-ups (see
        # rewrite_query_for_search / _is_ambiguous_followup). These queries are
        # passed to the orchestrator as a *hint* — the planner LLM writes its own
        # search queries when it decides to call web_search/document_search; this
        # list is only used by the orchestrator's heuristic fallback path (when
        # Groq tool-calling itself is unavailable) and for logging/RAG defaults.
        search_queries = [last_user_msg]
        if not is_smalltalk_msg and not follow_up_mode:
            # Pass history excluding the current (last) user message so context is prior turns.
            # Follow-up-chip generation already includes the previous Q&A in its prompt;
            # query rewriting would be wasted work and can consume another Groq key.
            prior_history = messages[:-1] if messages and messages[-1].get("role") == "user" else messages
            search_queries = await rewrite_query_for_search(last_user_msg, prior_history, qtype=qtype)
        search_query = search_queries[0]  # representative query, used for logging
        await queue_research_progress({
            "phase": "query_rewrite", "status": "completed",
            "message": "Question ready. Checking relevant sources…",
        })

        log.info(
            "Chat request: msg=%r  search_query=%r  type=%s  file_ctx=%s  rag=%s  smalltalk=%s  session=%s",
            last_user_msg[:80], search_query[:80] if search_query != last_user_msg else "(unchanged)",
            qtype, bool(local_file_context), has_rag, (is_smalltalk_msg or follow_up_mode), session_id or "none",
        )
        if len(search_queries) > 1:
            log.info("Chat: multi-intent prompt split into %d queries: %r",
                      len(search_queries), [q[:60] for q in search_queries])

        # Upsert the session row upfront (fire-and-forget — don't block the stream)
        if session_id:
            user_id = await get_user_id(request)
            asyncio.ensure_future(_upsert_session(session_id, user_id))

        # ── Orchestration: one bounded LLM tool-calling loop decides which (if
        # any) of web_search / document_search / company_fundamentals /
        # market_data / news_headlines this message actually needs, runs them,
        # and hands back gathered context + sources + limitations. Never raises;
        # degrades to a deterministic heuristic plan if Groq tool-calling itself
        # is unavailable, and to "no tools" (model-knowledge-only) if nothing is
        # configured at all. See utils/orchestrator.py + utils/tools.py.
        try:
            orch = await orchestrate(
                user_message=last_user_msg,
                messages=messages,
                session_id=session_id,
                has_rag=has_rag,
                has_files=bool(local_file_context or file_images),
                search_queries=search_queries,
                skip=(is_smalltalk_msg or follow_up_mode),
                progress_callback=queue_research_progress,
            )
        except Exception as exc:
            # orchestrate() is documented to never raise, but this is the one
            # request-critical call standing between "answer the question" and
            # "500 the whole chat request" — belt-and-braces so a bug there
            # degrades to a plain model-knowledge answer instead of an outage.
            log.error("Chat: orchestrate() raised unexpectedly — continuing without tool context: %s", exc)
            orch = OrchestrationResult(
                limitations=[f"Tool orchestration failed unexpectedly ({type(exc).__name__}) — answering from model knowledge only."],
                planner="error",
            )
        log.info(
            "Chat: orchestrator planner=%s rounds=%d tools=%s sources=%d limitations=%d",
            orch.planner, orch.rounds, orch.tools_used or "none", len(orch.sources), len(orch.limitations),
        )

        # Persist the latest live research context for a short period so a report follow-up
        # can reuse it even if the frontend source metadata is missing from the request.
        if session_id and orch.sources:
            try:
                from utils.research_context import save_research_context
                save_research_context(
                    session_id,
                    topic=search_query,
                    sources=orch.sources,
                    context=orch.context(),
                    query=last_user_msg,
                )
                log.info("Chat: saved research context for session %s (%d sources)", session_id[:8], len(orch.sources))
            except Exception as exc:
                log.warning("Chat: could not cache research context: %s", exc)

        base_prompt = build_system(orch.context(), smalltalk=is_smalltalk_msg, limitations=orch.limitations)

        # ── Image vision: extract content from attached images via Gemini Vision ─
        # ── Image vision: check cache first, then Gemini Vision for new images ──
        if file_images:
            try:
                from routes.report import extract_data_from_images

                # Check Supabase cache first — avoid re-running Vision on same image
                img_hashes = [_image_hash(img["data"]) for img in file_images]
                cached = await _load_cached_extractions(session_id, img_hashes) if session_id else {}

                cached_texts  = [cached[h] for h in img_hashes if h in cached]
                uncached_imgs = [img for img, h in zip(file_images, img_hashes) if h not in cached]

                image_context_parts = cached_texts[:]

                if uncached_imgs:
                    fresh_text = await extract_data_from_images(last_user_msg, uncached_imgs)
                    if fresh_text:
                        image_context_parts.append(fresh_text)
                        # Map each uncached image hash → the fresh extraction text,
                        # then persist to Supabase in the background
                        fresh_extractions = {_image_hash(img["data"]): fresh_text for img in uncached_imgs}
                        asyncio.ensure_future(
                            _store_image_extractions(session_id, uncached_imgs, fresh_extractions)
                        )

                image_context = "\n\n".join(image_context_parts)
                if image_context:
                    src = ("cache+vision" if cached_texts and uncached_imgs
                           else "cache" if cached_texts else "vision")
                    local_file_context += f"\n\n━━ IMAGE CONTENT ({src}) ━━\n{image_context}\n━━ END ━━"
                    log.info("Chat: image context %s — %d chars, %d image(s), %d cached",
                             src, len(image_context), len(file_images), len(cached_texts))
            except Exception as exc:
                log.warning("Chat: image extraction failed: %s", exc)

        # ── Document grounding now runs INSIDE the orchestrator (document_search
        # tool, backed by the same utils.rag_client.rag_query call this block used
        # to make directly) whenever the planner decides this message needs it, or
        # via the heuristic fallback when hasRag/has_files is set. Its retrieved
        # passages already arrived above as one of orch.context()'s labelled
        # blocks, folded into base_prompt like every other tool's output — so
        # there's no separate RAG branch here anymore, and no case where an
        # externally-authored system prompt replaces this persona.
        system_prompt = base_prompt + local_file_context if local_file_context else base_prompt
        await queue_research_progress({
            "phase": "answer_generation", "status": "started",
            "message": "Research context ready. Starting answer generation…",
        })

        # ── Metadata for the SSE meta frame — same field names/shapes the
        # frontend already reads (searchPerformed, resultCount, queryType,
        # kbMatched, kbCompany, sources), now sourced from the orchestrator's
        # results instead of the old fixed pipeline, plus a few additive fields
        # (toolsUsed, limitations, planner) the frontend can ignore safely.
        _web_result = orch.result_for("web_search")
        _web_raw_results = ((_web_result.raw or {}).get("results") or []) if _web_result else []

        _kb_result = orch.result_for("company_fundamentals")
        kb_context = (_kb_result.raw or {}).get("kb_context") if (_kb_result and _kb_result.ok) else None
        kb_company = (_kb_result.raw or {}).get("kb_company") if (_kb_result and _kb_result.ok) else None
        if kb_context:
            log.info("Chat: fundamentals KB matched a company for this query (%d chars)", len(kb_context))

        meta_event = (
            "data: "
            + json.dumps({
                "type": "meta",
                "searchPerformed": bool(_web_result and _web_result.ok),
                "resultCount": len(_web_raw_results),
                "queryType": qtype,
                "kbMatched": bool(kb_context),
                "kbCompany": (
                    {
                        "id": kb_company["id"],
                        "ticker": kb_company["ticker"],
                        "name": kb_company["name"],
                        "downloadUrl": f"/api/stocks/{kb_company['id']}/export",
                    }
                    if kb_company else None
                ),
                "sources": [
                    {"title": s.get("title", ""), "url": s.get("url", ""), "snippet": (s.get("snippet") or "")[:180]}
                    for s in orch.sources
                ],
                "toolsUsed": orch.tools_used,
                "limitations": orch.limitations,
                "planner": orch.planner,
            })
            + "\n\n"
        )


        return system_prompt, meta_event

    async def generate() -> AsyncGenerator[bytes, None]:
        assistant_chunks: list[str] = []
        provider_used = "unknown"
        # Emit a first byte immediately, then forward genuine progress events
        # from query rewriting, tool start/completion, planning and source collection.
        yield ("data: " + json.dumps({"type": "status", "phase": "connecting", "message": "Connecting to the research engine…"}) + "\n\n").encode()
        prep_task = asyncio.create_task(prepare_context(), name="gg-chat-context-prep")
        last_progress_at = time.monotonic()
        try:
            while not prep_task.done() or not research_progress_queue.empty():
                try:
                    event = await asyncio.wait_for(research_progress_queue.get(), timeout=8.0)
                    last_progress_at = time.monotonic()
                    yield ("data: " + json.dumps({"type": "status", **event}) + "\n\n").encode()
                except asyncio.TimeoutError:
                    # Keep the stream alive honestly without pretending a source
                    # search finished. Real start/finish statuses come from tools.
                    if not prep_task.done():
                        yield ("data: " + json.dumps({
                            "type": "status", "phase": "heartbeat",
                            "message": "Research is still in progress…",
                        }) + "\n\n").encode()
                        last_progress_at = time.monotonic()
            system_prompt, meta_event = await prep_task
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error("Chat context preparation failed: %s", type(exc).__name__)
            yield ("data: " + json.dumps({"error": "The chat could not prepare its research context. Please retry."}) + "\n\n").encode()
            yield b"data: [DONE]\n\n"
            return
        finally:
            if not prep_task.done():
                prep_task.cancel()
        yield meta_event.encode()

        # ── Try Groq (all keys × models, round-robin) ─────────────────────────
        groq_gen = await stream_groq(system_prompt, messages)
        if groq_gen is not None:
            provider_used = "groq"
            try:
                async for out_bytes in _sanitize_and_forward(groq_gen, assistant_chunks, request_started_at=t0):
                    yield out_bytes
                yield b"data: [DONE]\n\n"
                elapsed = int((time.perf_counter() - t0) * 1000)
                log.info("Chat complete via Groq in %dms", elapsed)
                if session_id:
                    full_reply = "".join(assistant_chunks)
                    asyncio.ensure_future(
                        _save_messages(session_id, last_user_msg, full_reply, provider_used, elapsed)
                    )
                return
            except Exception as exc:
                log.warning("Groq stream broke mid-way: %s — falling back to Gemini", exc)

        # ── Gemini fallback (all keys × models, round-robin) ──────────────────
        gemini_keys = get_gemini_keys()
        if gemini_keys:
            provider_used = "gemini"
            try:
                async for out_bytes in _sanitize_and_forward(gemini_sse(system_prompt, messages), assistant_chunks, request_started_at=t0):
                    yield out_bytes
                yield b"data: [DONE]\n\n"
                elapsed = int((time.perf_counter() - t0) * 1000)
                log.info("Chat complete via Gemini in %dms", elapsed)
                if session_id:
                    full_reply = "".join(assistant_chunks)
                    asyncio.ensure_future(
                        _save_messages(session_id, last_user_msg, full_reply, provider_used, elapsed)
                    )
                return
            except Exception as e:
                log.error("All LLM providers failed: %s", e)

        # ── All providers exhausted — tell user clearly ────────────────────────
        log.error("Chat: all providers rate-limited or failed")
        err_msg = (
            "⚠️ All AI providers are currently rate-limited. "
            "This is a temporary limit on the free tier — please wait 60 seconds and try again. "
            "If this keeps happening, additional API keys can be added to GROQ_API_KEYS / GEMINI_API_KEY in your environment."
        )
        err_chunk = json.dumps({"choices": [{"delta": {"content": err_msg}}]})
        yield f"data: {err_chunk}\n\n".encode()
        yield b"data: [DONE]\n\n"
        return

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
