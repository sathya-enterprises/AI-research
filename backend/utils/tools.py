"""
Tool Registry
=============
One flat, declarative registry of the capabilities the orchestrator may reach
for. Each entry wraps a capability that ALREADY existed in this codebase —
nothing here is a reimplementation:

  web_search           -> utils.websearch  (Exa Search with configured live-search fallbacks)
  document_search      -> utils.rag_client (Supabase-backed HF Space RAG)
  company_fundamentals -> utils.screener_kb (Screener.in structured KB)
  market_data          -> utils.market_data (Yahoo / Stooq quotes)
  news_headlines       -> routes.chat.load_headlines (cached headline feed)
  stored_web_research  -> utils.research_store (prior crawls and research stored in Supabase)

Design rules this module enforces:

* **Nothing is mandatory.** Answering from the model's own knowledge is the
  implicit "call no tools" option — there is no default tool, and no query
  category that forces a particular tool to run.
* **Tools never raise.** Every runner returns a ``ToolResult``; a failure
  becomes ``ok=False`` plus a human-readable ``error`` string, so one broken
  tool can never take down a chat request.
* **Availability is contextual, not categorical.** ``document_search`` only
  appears in the toolset when the session actually has indexed documents;
  the finance-flavoured tools only appear when their backing store is
  configured. The LLM picks from what is genuinely usable.
* **Sources are preserved verbatim.** Runners never invent a URL; internal
  data stores report themselves with an ``internal://`` URL and a truthful
  title so the answer can distinguish retrieved data from model knowledge.

Adding a tool = write a runner + append one ``Tool(...)`` to ``ALL_TOOLS``.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

log = logging.getLogger("tools")

# Hard ceiling on how much text a single tool may push into the final prompt.
# Prevents one verbose tool from crowding out every other source.
MAX_TOOL_CONTEXT_CHARS = 24_000
# Cap on how much of a tool result is echoed back to the planning LLM. The
# planner only needs enough to decide whether to call another tool.
MAX_TOOL_SUMMARY_CHARS = 1_400


@dataclass
class ToolContext:
    """Per-request state a runner may need but the LLM must not choose."""

    session_id: str = ""
    user_message: str = ""
    has_rag: bool = False
    has_files: bool = False


@dataclass
class ToolResult:
    name: str
    ok: bool
    args: dict = field(default_factory=dict)
    context: str = ""                       # injected into the final system prompt
    summary: str = ""                       # fed back to the planning LLM
    sources: list[dict] = field(default_factory=list)
    error: str | None = None
    elapsed_ms: int = 0
    raw: dict = field(default_factory=dict)  # tool-specific extras for the caller

    def truncated_context(self) -> str:
        return self.context[:MAX_TOOL_CONTEXT_CHARS]

    def as_meta(self) -> dict:
        """Compact, secret-free shape for the SSE meta frame / logs."""
        return {
            "name": self.name,
            "ok": self.ok,
            "args": self.args,
            "elapsedMs": self.elapsed_ms,
            "sourceCount": len(self.sources),
            "error": self.error,
        }


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    runner: Callable[[dict, ToolContext], Awaitable[ToolResult]]
    # Returns False when the backing store isn't configured for this request.
    is_available: Callable[[ToolContext], bool] = lambda _ctx: True

    def schema(self) -> dict:
        """OpenAI/Groq-compatible function-calling schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def _fail(name: str, args: dict, error: str, t0: float) -> ToolResult:
    return ToolResult(
        name=name, ok=False, args=args, error=error,
        summary=f"Tool '{name}' failed: {error}",
        elapsed_ms=int((time.perf_counter() - t0) * 1000),
    )


# ══════════════════════════════════════════════════════════════════════════
# Runners
# ══════════════════════════════════════════════════════════════════════════


async def _run_web_search(args: dict, ctx: ToolContext) -> ToolResult:
    """Live web search through the configured provider (see utils/websearch.py)."""
    t0 = time.perf_counter()
    from utils.websearch import search_web

    raw_queries = args.get("queries") or args.get("query") or ctx.user_message
    if isinstance(raw_queries, str):
        queries = [raw_queries]
    else:
        queries = [str(q) for q in raw_queries if str(q).strip()][:2]
    # Normal chat uses a lean evidence set. Research reports have a separate,
    # deeper source-acquisition pipeline and are not limited by this chat cap.
    if not queries:
        return _fail("web_search", args, "no search query supplied", t0)

    recency = (args.get("recency") or "").strip().lower()
    time_range = recency if recency in ("day", "week", "month", "year") else None
    region = (args.get("region") or "").strip().lower() or None

    try:
        requested_results = int(args.get("max_results") or 8)
        chat_max_results = max(4, min(requested_results, 8))
        out = await search_web(
            queries,
            max_results=chat_max_results,
            topic=args.get("topic"),
            region=region,
            time_range=time_range,
            historical_intent=bool(args.get("historical")),
        )
    except Exception as exc:  # defence in depth — search_web already swallows
        return _fail("web_search", args, f"{type(exc).__name__}: {exc}", t0)

    results = out.get("results") or []
    sources = [
        {
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "snippet": (r.get("snippet") or "")[:180],
            "published": r.get("published"),
            "origin": "web",
        }
        for r in results
        if r.get("url")
    ]

    blocks = []
    for r in results[:7]:
        body = r.get("fullContent") or ""
        if len(body) <= len(r.get("snippet") or ""):
            body = r.get("snippet") or ""
        pub = f" ({r['published']})" if r.get("published") else ""
        blocks.append(f"- {r.get('title', '')}\nSource: {r.get('url', '')}{pub}\n{body[:750]}")

    if results:
        context = (
            f"\n\n---\n🌐 WEB SEARCH RESULTS — {len(results)} page(s) retrieved live "
            f"for: {'; '.join(queries)}\n\n" + "\n\n".join(blocks) + "\n---"
        )
        summary = f"{len(results)} web result(s). Top titles: " + "; ".join(
            (r.get("title") or "")[:90] for r in results[:6]
        )
    else:
        context = (
            f"\n\n---\n🌐 WEB SEARCH — ran for: {'; '.join(queries)} — but returned NO "
            "usable results. There is no retrieved web evidence for this question. Do not "
            "present remembered or guessed current information as if it were retrieved.\n---"
        )
        summary = "Web search returned 0 results."

    return ToolResult(
        name="web_search", ok=bool(results) or out.get("error") is None,
        args={"queries": queries, "topic": args.get("topic"), "region": region, "recency": time_range},
        context=context, summary=summary[:MAX_TOOL_SUMMARY_CHARS], sources=sources,
        error=out.get("error"),
        elapsed_ms=int((time.perf_counter() - t0) * 1000),
        raw={"results": results, "provider": out.get("provider")},
    )


async def _run_document_search(args: dict, ctx: ToolContext) -> ToolResult:
    """Retrieve from the user's own uploaded/indexed documents (existing RAG service)."""
    t0 = time.perf_counter()
    from utils.rag_client import rag_query

    if not ctx.session_id:
        return _fail("document_search", args, "no session id — cannot query the document index", t0)

    query = (args.get("query") or ctx.user_message or "").strip()
    if not query:
        return _fail("document_search", args, "no query supplied", t0)

    try:
        res = await rag_query(
            session_id=ctx.session_id,
            question=query,
            top_k=int(args.get("top_k") or 8),
            min_score=0.15,
        )
    except Exception as exc:  # rag_client already swallows, belt-and-braces
        return _fail("document_search", args, f"{type(exc).__name__}: {exc}", t0)

    has_content = bool(res.get("has_content"))
    files = res.get("source_files") or []
    sources = [
        {"title": str(f), "url": f"internal://documents/{f}", "snippet": "User-uploaded document",
         "origin": "documents"}
        for f in files
    ]

    if has_content:
        body = res.get("context") or res.get("system_prompt") or ""
        context = (
            "\n\n---\n📄 USER'S UPLOADED DOCUMENTS — retrieved passages "
            f"({res.get('retrieved', 0)} chunk(s) from: {', '.join(map(str, files)) or 'session index'}). "
            "These are the user's own files; treat them as authoritative for their content "
            "and quote/reference them by filename:\n\n" + body + "\n---"
        )
        summary = f"Retrieved {res.get('retrieved', 0)} passage(s) from {len(files) or 1} document(s)."
    else:
        context = (
            "\n\n---\n📄 USER'S UPLOADED DOCUMENTS — a retrieval attempt ran but matched no "
            "relevant passages. Do not claim the documents say something you did not retrieve.\n---"
        )
        summary = "Document index returned no relevant passages."

    return ToolResult(
        name="document_search", ok=has_content, args={"query": query},
        context=context, summary=summary[:MAX_TOOL_SUMMARY_CHARS], sources=sources,
        error=None if has_content else "no relevant passages found",
        elapsed_ms=int((time.perf_counter() - t0) * 1000),
        raw={"rag_system_prompt": res.get("system_prompt") or "", "retrieved": res.get("retrieved", 0),
             "source_files": files},
    )


async def _run_company_fundamentals(args: dict, ctx: ToolContext) -> ToolResult:
    """Structured company financials from the existing Screener.in knowledge base."""
    t0 = time.perf_counter()
    from utils.screener_kb import get_company_context_with_meta

    probe = args.get("companies")
    if isinstance(probe, list) and probe:
        probe_text = " ".join(str(c) for c in probe)
    else:
        probe_text = str(args.get("company") or "").strip() or ctx.user_message

    try:
        kb_context, kb_company = await get_company_context_with_meta(probe_text)
    except Exception as exc:
        return _fail("company_fundamentals", args, f"{type(exc).__name__}: {exc}", t0)

    if not kb_context:
        return ToolResult(
            name="company_fundamentals", ok=False, args={"probe": probe_text[:120]},
            context="", summary="No company matched in the fundamentals knowledge base.",
            error="no company matched", elapsed_ms=int((time.perf_counter() - t0) * 1000),
        )

    context = (
        "\n\n---\n📊 COMPANY FUNDAMENTALS DATABASE — verified structured data sourced from "
        "Screener.in filings. Treat every figure in this block as ground truth and "
        "higher-confidence than web snippets for this company's financials, ratios and "
        "shareholding. Attribute it as \"per Screener.in data\":\n\n" + kb_context + "\n---"
    )
    name = (kb_company or {}).get("name") or "matched company"
    return ToolResult(
        name="company_fundamentals", ok=True, args={"probe": probe_text[:120]},
        context=context,
        summary=f"Structured fundamentals found for {name} ({len(kb_context)} chars).",
        sources=[{
            "title": f"{name} — fundamentals (Screener.in)",
            "url": f"internal://fundamentals/{(kb_company or {}).get('id', '')}",
            "snippet": "Structured filings data from the fundamentals knowledge base",
            "origin": "fundamentals",
        }],
        elapsed_ms=int((time.perf_counter() - t0) * 1000),
        raw={"kb_context": kb_context, "kb_company": kb_company},
    )


async def _run_market_data(args: dict, ctx: ToolContext) -> ToolResult:
    """Live index levels and/or stock quotes from the existing market-data helpers."""
    t0 = time.perf_counter()
    from utils import market_data as md

    symbols = args.get("symbols") or args.get("companies") or []
    if isinstance(symbols, str):
        symbols = [symbols]
    symbols = [str(s).strip() for s in symbols if str(s).strip()][:8]
    want_indices = bool(args.get("include_indices", not symbols))
    # "daily OHLC / price action / open-high-low-close for each session"
    # style asks need actual per-session bars, not just a single live quote —
    # see fetch_index_daily_ohlc(). Only applies to indices for now.
    want_daily_ohlc = bool(args.get("daily_ohlc", False))

    blocks: list[str] = []
    sources: list[dict] = []
    errors: list[str] = []

    async def _indices():
        try:
            return md.format_quotes_as_source(await md.fetch_index_quotes())
        except Exception as exc:
            errors.append(f"indices: {type(exc).__name__}")
            return None

    async def _daily_ohlc():
        try:
            return md.format_ohlc_history_as_source(await md.fetch_index_daily_ohlc())
        except Exception as exc:
            errors.append(f"daily_ohlc: {type(exc).__name__}")
            return None

    async def _stocks():
        try:
            return md.format_stock_fundamentals_as_source(await md.fetch_stock_fundamentals(symbols))
        except Exception as exc:
            errors.append(f"quotes: {type(exc).__name__}")
            return None

    tasks = []
    if want_daily_ohlc:
        tasks.append(_daily_ohlc())
    elif want_indices:
        tasks.append(_indices())
    if symbols:
        tasks.append(_stocks())
    if not tasks:
        return _fail("market_data", args, "nothing requested (no symbols, indices disabled)", t0)

    for src in await asyncio.gather(*tasks):
        if not src:
            continue
        blocks.append(f"### {src.get('title', 'Market data')}\n{src.get('fullContent') or src.get('snippet') or ''}")
        sources.append({
            "title": src.get("title", "Market data"),
            "url": src.get("url", "internal://market-data"),
            "snippet": (src.get("snippet") or "")[:180],
            "origin": "market-data",
        })

    if not blocks:
        return _fail("market_data", args, "; ".join(errors) or "no market data returned", t0)

    return ToolResult(
        name="market_data", ok=True,
        args={"symbols": symbols, "include_indices": want_indices},
        context="\n\n---\n📈 LIVE MARKET DATA (fetched now):\n\n" + "\n\n".join(blocks) + "\n---",
        summary=f"Fetched {len(blocks)} market-data block(s)."[:MAX_TOOL_SUMMARY_CHARS],
        sources=sources, error="; ".join(errors) or None,
        elapsed_ms=int((time.perf_counter() - t0) * 1000),
    )


async def _run_stored_web_research(args: dict, ctx: ToolContext) -> ToolResult:
    """Retrieve previously crawled and researched web sources from Supabase Postgres."""
    t0 = time.perf_counter()
    query = str(args.get("query") or ctx.user_message or "").strip()[:1000]
    if not query:
        return _fail("stored_web_research", args, "no query supplied", t0)

    try:
        from utils.research_store import retrieve_web_research, format_stored_web_research

        limit = max(1, min(6, int(args.get("limit") or 4)))
        age_arg = args.get("max_age_days")
        max_age_days = None if age_arg in (None, "", 0, "0") else max(1, min(3650, int(age_arg)))
        results = await asyncio.to_thread(retrieve_web_research, query, limit, max_age_days)
        if not results:
            return ToolResult(
                name="stored_web_research", ok=False,
                args={"query": query, "limit": limit, "max_age_days": max_age_days},
                summary="No matching stored web research was found in the Supabase research library.",
                error="no relevant stored sources found",
                elapsed_ms=int((time.perf_counter() - t0) * 1000),
            )

        # Keep saved-source retrieval useful without crowding the model context;
        # detailed reports have a larger evidence budget of their own.
        dossier = format_stored_web_research(results, max_chars=8_500)
        sources = []
        for item in results:
            url = str(item.get("url") or "").strip()
            if not url:
                continue
            sources.append({
                "title": str(item.get("title") or "Stored web source"),
                "url": url,
                "snippet": str(item.get("fullContent") or "")[:220],
                "published": item.get("published_at"),
                "retrievedAt": item.get("retrieved_at"),
                "origin": "stored_web_research",
                "evidenceOrigin": item.get("evidenceOrigin"),
                "sourceType": item.get("source_type"),
            })
        if not dossier:
            return ToolResult(
                name="stored_web_research", ok=False,
                args={"query": query, "limit": limit, "max_age_days": max_age_days},
                summary="Matching stored records had no usable text to pass to the answer model.",
                error="stored sources contained no usable text",
                sources=sources,
                elapsed_ms=int((time.perf_counter() - t0) * 1000),
            )

        # The research store labels RSS summaries and full publisher text distinctly.
        # Keep that provenance warning in the system context to avoid overstating evidence.
        context = (
            "\n\n---\n📚 PREVIOUSLY STORED WEB RESEARCH FROM SUPABASE (retrieved for this query). "
            "Use these stored items as reusable evidence, not as a guarantee that facts remain current. "
            "Respect each item's evidence-scope warning. RSS summaries are discovery leads, not proof "
            "for detailed or quantitative claims. For latest/current questions, also use live web search.\n\n"
            + dossier + "\n---"
        )
        titles = "; ".join(str(x.get("title") or "Untitled")[:80] for x in results[:5])
        return ToolResult(
            name="stored_web_research", ok=True,
            args={"query": query, "limit": limit, "max_age_days": max_age_days},
            context=context,
            summary=f"Retrieved {len(results)} stored source(s) from Supabase. Top titles: {titles}"[:MAX_TOOL_SUMMARY_CHARS],
            sources=sources,
            elapsed_ms=int((time.perf_counter() - t0) * 1000),
            raw={"result_count": len(results)},
        )
    except Exception as exc:
        log.warning("Stored web research lookup failed (%s)", type(exc).__name__)
        return _fail("stored_web_research", args, f"stored research unavailable ({type(exc).__name__})", t0)


def _research_store_available(_ctx: ToolContext) -> bool:
    """Expose the stored-source search only when a backend DB URL is configured."""
    import os
    return bool((os.getenv("RESEARCH_DATABASE_URL") or os.getenv("DATABASE_URL") or os.getenv("SUPABASE_DATABASE_URL") or "").strip())


async def _run_news_headlines(args: dict, ctx: ToolContext) -> ToolResult:
    """The cached headline feed. Opt-in only — it used to be injected into every prompt."""
    t0 = time.perf_counter()
    from routes.chat import load_headlines

    try:
        text = await load_headlines(int(args.get("limit") or 30))
    except Exception as exc:
        return _fail("news_headlines", args, f"{type(exc).__name__}: {exc}", t0)

    if not text:
        return ToolResult(
            name="news_headlines", ok=False, args=args, context="",
            summary="No cached headline feed available.", error="headline cache empty",
            elapsed_ms=int((time.perf_counter() - t0) * 1000),
        )
    return ToolResult(
        name="news_headlines", ok=True, args=args, context=text,
        summary=f"Loaded cached headline feed ({len(text)} chars).",
        sources=[{"title": "Cached headline feed", "url": "internal://headlines",
                  "snippet": "Recently cached news headlines", "origin": "headlines"}],
        elapsed_ms=int((time.perf_counter() - t0) * 1000),
    )


# ══════════════════════════════════════════════════════════════════════════
# Registry
# ══════════════════════════════════════════════════════════════════════════


def _rag_available(ctx: ToolContext) -> bool:
    """Only offer document retrieval when this session actually has documents."""
    return bool(ctx.session_id) and (ctx.has_rag or ctx.has_files)


def _kb_available(_ctx: ToolContext) -> bool:
    try:
        from utils.screener_kb import kb_configured

        return bool(kb_configured())
    except Exception:
        return False


def _web_available(_ctx: ToolContext) -> bool:
    try:
        from utils.websearch import web_search_available

        return web_search_available()
    except Exception:
        return False


ALL_TOOLS: list[Tool] = [
    Tool(
        name="web_search",
        description=(
            "Search the live web and read the top pages. Use this for anything that changes "
            "over time or that you cannot verify from your own knowledge: current events, "
            "news, prices, recent releases, 'latest'/'today'/'2026' questions, who currently "
            "holds a role, statistics, or any factual claim the user would expect to be "
            "up to date. Also use it when you are unsure whether your knowledge is current. "
            "Works for every subject — science, technology, politics, sport, health, "
            "programming, business — not just finance."
        ),
        parameters={
            "type": "object",
            "properties": {
                "queries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "One to three short, focused search queries (not the user's full "
                        "sentence). Use several only when the question genuinely has "
                        "distinct parts."
                    ),
                },
                "topic": {
                    "type": "string",
                    "enum": ["general", "news", "finance"],
                    "description": (
                        "Search vertical. 'news' for breaking events, 'finance' for markets "
                        "and company financials, 'general' for everything else. Default general."
                    ),
                },
                "region": {
                    "type": "string",
                    "description": (
                        "Optional country name to bias results towards, e.g. 'india', "
                        "'united states'. OMIT THIS unless the question is specifically about "
                        "one country — omitting it searches worldwide."
                    ),
                },
                "recency": {
                    "type": "string",
                    "enum": ["day", "week", "month", "year", "any"],
                    "description": "Restrict results to this freshness window. Use 'any' for timeless topics.",
                },
            },
            "required": ["queries"],
        },
        runner=_run_web_search,
        is_available=_web_available,
    ),
    Tool(
        name="document_search",
        description=(
            "Search the documents THIS USER uploaded in this session (PDFs, spreadsheets, "
            "notes) and return the relevant passages. Use it whenever the question refers to "
            "'my file', 'the PDF', 'this document', 'the attachment', or asks about content "
            "that would only live in their upload. Do not use it for general world knowledge."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to look for inside the user's documents.",
                },
                "top_k": {
                    "type": "integer",
                    "description": "How many passages to retrieve (default 8).",
                },
            },
            "required": ["query"],
        },
        runner=_run_document_search,
        is_available=_rag_available,
    ),
    Tool(
        name="company_fundamentals",
        description=(
            "Look up verified structured financials for a specific listed company — "
            "ratios, quarterly and annual results, growth CAGR, shareholding pattern, "
            "peers, pros and cons — from an internal filings database. Use it when a "
            "named company's actual numbers matter. Returns nothing if the company "
            "isn't in the database, which is fine."
        ),
        parameters={
            "type": "object",
            "properties": {
                "companies": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Company names or tickers mentioned in the question.",
                },
            },
            "required": ["companies"],
        },
        runner=_run_company_fundamentals,
        is_available=_kb_available,
    ),
    Tool(
        name="market_data",
        description=(
            "Fetch verified market data: live index levels/current price data for specific "
            "listed companies, or (set daily_ohlc=true) the last few trading sessions' real "
            "open/high/low/close bars for major Indian indices. Use daily_ohlc=true whenever "
            "the question asks for daily price action, OHLC, or open/high/low/close 'for each "
            "session' over a recent period — web_search will not have this. Use the plain "
            "quote mode only when the question needs a single up-to-the-minute market level."
        ),
        parameters={
            "type": "object",
            "properties": {
                "symbols": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Company names or tickers to quote. Omit for indices only.",
                },
                "include_indices": {
                    "type": "boolean",
                    "description": "Include broad index levels. Defaults to true when no symbols are given.",
                },
                "daily_ohlc": {
                    "type": "boolean",
                    "description": (
                        "Set true to fetch the last ~5 trading sessions' real open/high/low/close "
                        "for major indices (NIFTY 50, SENSEX, NIFTY BANK, etc.) instead of a single "
                        "live quote. Use for 'daily price action', 'OHLC', or per-session breakdown asks."
                    ),
                },
            },
        },
        runner=_run_market_data,
    ),
    Tool(
        name="news_headlines",
        description=(
            "Read the platform's cached headline feed — a broad snapshot of recent news. "
            "Prefer web_search for anything specific; use this only when the user wants a "
            "general 'what's happening' roundup."
        ),
        parameters={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many headlines to load (default 30)."},
            },
        },
        runner=_run_news_headlines,
    ),
    Tool(
        name="stored_web_research",
        description=(
            "Search previously scraped website articles and saved web-research sources in the shared "
            "Supabase/Postgres research library. Use this when answering market, company, banking, "
            "economy, sector, investment, policy, or research questions that may overlap earlier "
            "website crawls or reports, and when the user asks about saved/stored research. This is "
            "a lexical/full-text search of stored source content, not vector-semantic search. Stored "
            "items can be stale; for latest/current facts also call web_search. Respect evidence scope: "
            "RSS summaries are not full articles."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Focused terms to find in previously stored source content."},
                "limit": {"type": "integer", "description": "Number of stored sources to retrieve (default 6, maximum 10)."},
                "max_age_days": {"type": "integer", "description": "Optional freshness limit in days. Omit for historical/reusable research."},
            },
            "required": ["query"],
        },
        runner=_run_stored_web_research,
        is_available=_research_store_available,
    ),
]

TOOLS_BY_NAME: dict[str, Tool] = {t.name: t for t in ALL_TOOLS}


def build_toolset(ctx: ToolContext) -> list[Tool]:
    """The tools genuinely usable for this request, in registry order."""
    toolset = []
    for tool in ALL_TOOLS:
        try:
            if tool.is_available(ctx):
                toolset.append(tool)
        except Exception as exc:
            log.debug("Tool %s availability check failed (%s) — excluding", tool.name, exc)
    return toolset


def tool_schemas(toolset: list[Tool]) -> list[dict]:
    return [t.schema() for t in toolset]


async def execute_tool(name: str, args: dict, ctx: ToolContext) -> ToolResult:
    """
    Run one tool by name. Never raises: an unknown name, bad arguments or an
    exploding runner all come back as ``ok=False`` with an error string, which
    is what keeps a single tool failure from breaking the chat request.
    """
    t0 = time.perf_counter()
    tool = TOOLS_BY_NAME.get(name)
    if tool is None:
        log.warning("Tool dispatch: unknown tool %r requested", name)
        return _fail(name, args, f"unknown tool '{name}'", t0)
    if not isinstance(args, dict):
        args = {}
    try:
        result = await tool.runner(args, ctx)
    except Exception as exc:
        log.warning("Tool %s raised %s: %s", name, type(exc).__name__, exc)
        return _fail(name, args, f"{type(exc).__name__}: {exc}", t0)

    log.info(
        "Tool %s: ok=%s sources=%d ctx=%dch in %dms%s",
        result.name, result.ok, len(result.sources), len(result.context),
        result.elapsed_ms, f" error={result.error}" if result.error else "",
    )
    return result


async def _notify_progress(progress_callback, event: dict) -> None:
    """Best-effort progress callback; UI telemetry must never break a tool."""
    if progress_callback is None:
        return
    try:
        result = progress_callback(event)
        if inspect.isawaitable(result):
            await result
    except Exception as exc:
        log.debug("Tool progress callback failed (%s)", type(exc).__name__)


async def execute_tools(
    calls: list[tuple[str, dict]],
    ctx: ToolContext,
    progress_callback: Callable[[dict], Awaitable[None] | None] | None = None,
) -> list[ToolResult]:
    """Run tool calls concurrently and emit truthful start/completion events.

    Event order reflects actual execution milestones; returned results still match
    the original calls order for the planner protocol.
    """
    if not calls:
        return []

    async def run_one(index: int, name: str, args: dict) -> ToolResult:
        friendly = {
            "web_search": "web search",
            "stored_web_research": "saved research",
            "document_search": "uploaded documents",
            "company_fundamentals": "company fundamentals",
            "market_data": "market data",
            "news_headlines": "news headlines",
        }.get(name, name.replace("_", " "))
        await _notify_progress(progress_callback, {
            "phase": "research_tool", "status": "started", "tool": name,
            "message": f"Searching {friendly}…",
        })
        result = await execute_tool(name, args, ctx)
        if result.ok:
            if result.sources:
                message = f"Finished {friendly}: {len(result.sources)} source(s) found."
            elif result.context:
                message = f"Finished searching {friendly}."
            else:
                message = f"Finished checking {friendly}."
        else:
            message = f"{friendly.capitalize()} could not be fully retrieved; continuing with available evidence."
        await _notify_progress(progress_callback, {
            "phase": "research_tool", "status": "completed", "tool": name,
            "ok": bool(result.ok), "sourceCount": len(result.sources),
            "elapsedMs": int(result.elapsed_ms or 0), "message": message,
        })
        return result

    return list(await asyncio.gather(*[
        run_one(index, name, args) for index, (name, args) in enumerate(calls)
    ]))
