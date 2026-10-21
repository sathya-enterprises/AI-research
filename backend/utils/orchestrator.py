"""
Orchestrator — one bounded LLM tool-calling loop
================================================
This is the single decision point that replaced the old fixed pipeline. The
previous ``/api/chat`` handler always ran the same three fetches (web search +
cached headlines + company-fundamentals lookup) for every non-greeting message,
and only consulted the document index when the *frontend* set a ``hasRag``
flag. Which persona answered was decided by a keyword classifier that could
only ever return "finance" or "general".

Here, instead, Groq is asked — through native OpenAI-style function calling —
which of the registered tools (if any) it actually needs for THIS message. It
may call none (answer from model knowledge), one, or several; it may look at
what came back and call another. There is no query taxonomy anywhere in this
module, and no tool runs unless it was chosen.

Shape:

    plan  ->  execute (concurrently)  ->  optionally plan again  ->  hand the
    gathered context blocks + sources back to the caller for synthesis

Guarantees the caller can rely on:

* ``orchestrate()`` never raises and never returns ``None``.
* If Groq tool-calling is unavailable (no keys, all keys banned, malformed
  response, network failure), it degrades to ``_heuristic_plan`` — a
  deterministic, domain-neutral plan — and says so in ``planner``.
* Every tool failure is recorded in ``limitations`` rather than propagated, so
  a broken tool downgrades the answer instead of breaking the request.
* Synthesis itself is NOT done here — the caller keeps using the existing
  streaming Groq → Gemini path, so the SSE contract is untouched.
"""

from __future__ import annotations

import inspect
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Awaitable, Callable

import httpx

from utils.keys import (get_groq_keys, is_rate_limited, mark_rate_limited, round_robin, iter_reserved_groq_keys, available_groq_key_count)
from utils.tools import (
    ToolContext,
    ToolResult,
    build_toolset,
    execute_tools,
    tool_schemas,
)

log = logging.getLogger("orchestrator")

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# Same model family the streaming path already uses, overridable for
# deployments that enable a different tool-calling model on their Groq tier.
# llama-3.3-70b-versatile was decommissioned by Groq on 2026-08-16 (404
# model_not_found). Default updated to Groq's recommended successor, which
# also supports tool/function calling (required for the planner). Override
# via GROQ_TOOL_MODEL if needed.
PLANNER_MODEL = os.environ.get("GROQ_TOOL_MODEL", "openai/gpt-oss-120b").strip()

# Gemini models tried, in order, when Groq's tool-calling planner is
# unavailable (see _gemini_plan). Small/fast/cheap is what a planning round
# needs — flash-lite first, with the general-purpose flash-preview model as
# a second try if flash-lite's account access or quota is the problem.
GEMINI_PLANNER_MODELS = [
    m.strip() for m in os.environ.get(
        "GEMINI_PLANNER_MODELS", "gemini-3.5-flash-lite,gemini-3-flash-preview"
    ).split(",") if m.strip()
]

# Hard bound on planning rounds. 2 is enough for "search, look, refine once";
# more rounds mostly buy latency. Configurable but clamped.
MAX_ROUNDS = max(1, min(4, int(os.environ.get("ORCHESTRATOR_MAX_ROUNDS", "2") or 2)))
PLANNER_TIMEOUT = httpx.Timeout(connect=4, read=12, write=8, pool=5)


@dataclass
class OrchestrationResult:
    context_blocks: list[str] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)
    results: list[ToolResult] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    rounds: int = 0
    planner: str = "none"          # groq-tool-calling | heuristic | skipped
    planner_note: str = ""

    @property
    def tools_used(self) -> list[str]:
        return [r.name for r in self.results]

    def result_for(self, name: str) -> ToolResult | None:
        return next((r for r in self.results if r.name == name), None)

    def context(self) -> str:
        return "".join(self.context_blocks)

    def as_meta(self) -> list[dict]:
        return [r.as_meta() for r in self.results]



ProgressCallback = Callable[[dict[str, Any]], Awaitable[None] | None]


async def _emit_progress(callback: ProgressCallback | None, event: dict[str, Any]) -> None:
    """Emit progress best-effort; diagnostic/UI callbacks must not break research."""
    if callback is None:
        return
    try:
        result = callback(event)
        if inspect.isawaitable(result):
            await result
    except Exception as exc:
        log.debug("Orchestrator progress callback failed (%s)", type(exc).__name__)


def _planner_status_message(backend: str, round_no: int) -> str:
    provider = "AI planner" if backend == "groq" else "backup planner"
    return f"Deciding which sources to check (step {round_no})…"


def _planner_system_prompt(toolset_names: list[str]) -> str:
    today = date.today().strftime("%A, %B %d, %Y")
    return f"""You are the planning stage of a general-purpose research assistant. Today is {today}.

Your ONLY job right now is to decide which tools — if any — are needed to answer the user's latest message accurately. You must NOT answer the question yourself.

You serve every subject equally: science, technology, programming, history, health, politics, sport, culture, business and finance. Do not assume any particular domain. Decide purely from what the message needs.

How to decide:
- **Call no tools at all** when the answer is timeless, conceptual or self-contained and you are confident from your own knowledge: definitions, explanations of how something works, mathematics, reasoning about code or an error message, writing/editing help, translation, or plain conversation. Retrieval adds latency and noise for these — skip it.
- **Call web_search** when the answer depends on information that changes, is recent, or that you cannot verify from memory: news and current events, "latest"/"today"/"this week"/"{date.today().year}" questions, prices and market levels, who currently holds a position, release versions, statistics, or any claim the user would expect to be up to date. Also call it when you are genuinely unsure whether your knowledge is still current — it is much worse to answer stale than to search. If the user explicitly asks what experts, analysts, or commentators think/say about something, write a query aimed at finding that opinion/commentary specifically — but don't go looking for expert opinions when the user only asked for news, facts, or figures; match the query to what was actually asked, nothing more.
- **Call document_search** when the message refers to the user's own uploaded material ("my file", "the PDF", "this document", "the attachment") or asks about content that would only exist in their upload.
- **Call stored_web_research** when prior website crawls or saved research may help with a market, company, banking, economic, policy, investment, industry, or research question, or when the user asks about saved/crawled sources. Stored data can be stale, so also call web_search for latest/current facts. Respect the result's evidence-scope warnings; RSS summaries are not full articles.
- **Call the data tools** (company_fundamentals, market_data) only when a specific named company's real figures or a live market level actually matter to the answer. For a request for daily price action / OHLC / open-high-low-close per session over a recent period (e.g. "last week"), call market_data with daily_ohlc=true — web_search will not have real per-session bars.
- You may call several tools in one go when they cover genuinely different needs.
- Write short, focused search queries — not the user's whole sentence. Omit `region` unless the question is specifically about one country.

Available tools this turn: {', '.join(toolset_names) or 'none'}.

If no tool is needed, reply with exactly: NONE"""


def _messages_for_planner(messages: list[dict], user_message: str) -> list[dict]:
    """Last few turns only — the planner needs intent, not the whole transcript."""
    trimmed: list[dict] = []
    for m in [m for m in messages if m.get("role") in ("user", "assistant")][-5:]:
        content = str(m.get("content") or "")[:1500]
        if content:
            trimmed.append({"role": m["role"], "content": content})
    if not trimmed or trimmed[-1]["role"] != "user":
        trimmed.append({"role": "user", "content": user_message[:1500]})
    return trimmed


def _parse_tool_calls(message: dict) -> list[tuple[str, dict, str]]:
    """Extract (name, args, call_id) triples from a Groq assistant message."""
    calls = []
    for call in (message.get("tool_calls") or []):
        fn = call.get("function") or {}
        name = fn.get("name") or ""
        if not name:
            continue
        raw_args = fn.get("arguments")
        if isinstance(raw_args, dict):
            args = raw_args
        else:
            try:
                args = json.loads(raw_args or "{}")
            except (TypeError, ValueError):
                log.warning("Planner sent unparseable arguments for %s — using empty args", name)
                args = {}
        calls.append((name, args if isinstance(args, dict) else {}, call.get("id") or name))
    return calls


async def _groq_plan(payload_messages: list[dict], schemas: list[dict]) -> dict | None:
    """
    One non-streaming Groq call with tools attached. Returns the assistant
    message dict, or None when every key/attempt is exhausted. Never raises.
    """
    keys = get_groq_keys()
    if not keys:
        log.info("Planner: no Groq keys configured — using heuristic plan")
        return None

    if not available_groq_key_count(keys):
        log.info("Planner: all Groq keys are cooling down or rate-limited; using fallback planner")
        return None
    for key in iter_reserved_groq_keys(keys, max_keys=2):
        try:
            async with httpx.AsyncClient(timeout=PLANNER_TIMEOUT) as client:
                res = await client.post(
                    GROQ_URL,
                    headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
                    json={
                        "model": PLANNER_MODEL,
                        "messages": payload_messages,
                        "tools": schemas,
                        "tool_choice": "auto",
                        "temperature": 0.1,
                        "max_tokens": 512,
                    },
                )
            if res.status_code == 429:
                retry_ms = 30_000
                try:
                    retry_ms = int(float(res.headers.get("retry-after", "30")) * 1000)
                except (TypeError, ValueError):
                    pass
                log.warning("Planner: Groq 429 on key ...%s — backing off %dms", key[-4:], retry_ms)
                mark_rate_limited(key, retry_ms)
                log.warning("Planner: Groq rate limit; switching to Gemini/heuristic rather than retrying same-org keys")
                return None
            if res.status_code in (401, 403):
                log.warning("Planner: Groq auth error on key ...%s — banning 24h", key[-4:])
                mark_rate_limited(key, 24 * 60 * 60_000)
                continue
            if not res.is_success:
                detail = re.sub(r"(?i)(api[_-]?key|token|authorization)\s*[:=]\s*[^,\s]+", r"\1=[REDACTED]", res.text[:240])
                log.warning("Planner: Groq HTTP %d — %s", res.status_code, detail)
                if res.status_code in (400, 413):
                    # Same model/payload fails regardless of credential; avoid wasting
                    # round-trip latency by trying another key with the same request.
                    return None
                continue
            choices = (res.json().get("choices") or [])
            if not choices:
                log.warning("Planner: Groq returned no choices")
                continue
            return choices[0].get("message") or {}
        except Exception as exc:
            log.warning("Planner: Groq call failed on key ...%s: %s", key[-4:], type(exc).__name__)
            if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError)):
                log.info("Planner: Groq network failure; stop serial retries and use backup/fallback planner")
                return None
            continue
    return None


def _openai_schemas_to_gemini(schemas: list[dict]) -> list[dict]:
    """Convert the OpenAI/Groq-style {"type":"function","function":{...}}
    tool schemas (see Tool.schema() in utils/tools.py) into Gemini's
    functionDeclarations shape."""
    decls = []
    for s in schemas:
        fn = s.get("function") or {}
        if not fn.get("name"):
            continue
        decls.append({
            "name": fn["name"],
            "description": fn.get("description", ""),
            "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
        })
    return [{"functionDeclarations": decls}] if decls else []


def _openai_messages_to_gemini(payload_messages: list[dict]) -> tuple[str, list[dict]]:
    """Split an OpenAI-style message list (system/user/assistant/tool) into
    a Gemini system-instruction string plus a `contents` list, translating
    assistant tool_calls into functionCall parts and tool results into
    functionResponse parts. This is what lets _gemini_plan step into a
    conversation that orchestrate()'s round loop already built for the
    Groq/OpenAI protocol, including on round 2+ after tools have already run.
    """
    system_parts: list[str] = []
    contents: list[dict] = []
    for m in payload_messages:
        role = m.get("role")
        if role == "system":
            system_parts.append(str(m.get("content") or ""))
        elif role == "user":
            contents.append({"role": "user", "parts": [{"text": str(m.get("content") or "")}]})
        elif role == "assistant":
            parts = []
            content = m.get("content") or ""
            if content:
                parts.append({"text": content})
            for call in (m.get("tool_calls") or []):
                fn = call.get("function") or {}
                raw_args = fn.get("arguments")
                if isinstance(raw_args, str):
                    try:
                        raw_args = json.loads(raw_args or "{}")
                    except (TypeError, ValueError):
                        raw_args = {}
                parts.append({"functionCall": {"name": fn.get("name", ""), "args": raw_args or {}}})
            if parts:
                contents.append({"role": "model", "parts": parts})
        elif role == "tool":
            # Gemini's generateContent API accepts only user/model roles in
            # contents. Function results must be sent as functionResponse parts
            # on a user turn; a literal role="function" causes HTTP 400 and
            # disables the Gemini planner fallback after Groq is exhausted.
            function_response = {
                "functionResponse": {
                    "name": m.get("name", ""),
                    "response": {"content": str(m.get("content") or "")},
                },
            }
            # Consecutive OpenAI tool messages are one logical tool-result turn
            # in Gemini. Group them to keep the conversation role sequence valid.
            if (
                contents
                and contents[-1].get("role") == "user"
                and contents[-1].get("parts")
                and all("functionResponse" in part for part in contents[-1]["parts"])
            ):
                contents[-1]["parts"].append(function_response)
            else:
                contents.append({"role": "user", "parts": [function_response]})
    return "\n\n".join(p for p in system_parts if p), contents


def _gemini_tool_calls_to_openai(function_calls: list[dict]) -> list[dict]:
    """Wrap Gemini functionCall parts as OpenAI-style tool_calls so the rest
    of the orchestrator (built around _parse_tool_calls) needs no second
    code path for a Gemini-planned round."""
    out = []
    for i, fc in enumerate(function_calls):
        out.append({
            "id": f"gemini-call-{i}",
            "type": "function",
            "function": {"name": fc.get("name", ""), "arguments": json.dumps(fc.get("args") or {})},
        })
    return out


async def _gemini_plan(payload_messages: list[dict], schemas: list[dict]) -> dict | None:
    """
    Gemini function-calling fallback for the planner — used only when Groq's
    tool-calling planner is unavailable (no keys, all keys banned/rate-limited,
    malformed response, network failure). Mirrors _groq_plan's contract:
    returns an OpenAI-shaped assistant message dict (content + tool_calls) so
    orchestrate()'s round loop works unchanged regardless of which backend
    actually planned this round. Never raises; returns None when every
    key/model is exhausted (the caller then falls back to the heuristic
    plan), same as _groq_plan.
    """
    from utils.keys import get_gemini_keys

    keys = [k for k in get_gemini_keys() if k.startswith("AIzaSy") or k.startswith("AQ.")]
    if not keys:
        log.info("Planner: no Gemini keys configured — cannot use as Groq fallback")
        return None

    tools = _openai_schemas_to_gemini(schemas)
    system_text, contents = _openai_messages_to_gemini(payload_messages)
    if not contents:
        return None

    candidates = [k for k in round_robin(keys) if not is_rate_limited(k)]
    if not candidates:
        log.info("Planner: all Gemini planner keys are currently rate-limited; using heuristic plan")
        return None
    # Keep fallback bounded: planner requests are small and do not justify
    # serially probing many keys after request-shape/model errors.
    for key in candidates[:2]:
        for model in GEMINI_PLANNER_MODELS[:2]:
            if is_rate_limited(f"{key}:{model}"):
                continue
            try:
                payload = {
                    "contents": contents,
                    "tools": tools,
                    "generationConfig": {"temperature": 0.1, "maxOutputTokens": 512},
                }
                if system_text:
                    payload["system_instruction"] = {"parts": [{"text": system_text}]}
                async with httpx.AsyncClient(timeout=PLANNER_TIMEOUT) as client:
                    res = await client.post(
                        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}",
                        json=payload,
                    )
                if res.status_code == 429:
                    log.warning("Planner: Gemini 429 on model=%s key=...%s", model, key[-4:])
                    mark_rate_limited(f"{key}:{model}", 60_000)
                    continue
                if res.status_code in (401, 403):
                    log.warning("Planner: Gemini auth error on key ...%s — banning 24h", key[-4:])
                    mark_rate_limited(key, 24 * 60 * 60_000)
                    break
                if not res.is_success:
                    detail = res.text[:240] if res.status_code in (400, 413) else "provider request failed"
                    detail = re.sub(r"(?i)(api[_-]?key|token|authorization)\s*[:=]\s*[^,\s]+", r"\1=[REDACTED]", detail)
                    log.warning("Planner: Gemini HTTP %d on model=%s key=...%s — %s", res.status_code, model, key[-4:], detail)
                    if res.status_code in (400, 413):
                        # The same function-declaration payload is unlikely to work
                        # with another key. Let deterministic intent rules take over.
                        return None
                    continue
                cand = (res.json().get("candidates") or [{}])[0]
                parts = cand.get("content", {}).get("parts", []) or []
                function_calls = [p["functionCall"] for p in parts if "functionCall" in p]
                text = "".join(p.get("text", "") for p in parts if "text" in p)
                log.info(
                    "Planner: Gemini plan succeeded model=%s key=...%s (%d tool call(s))",
                    model, key[-4:], len(function_calls),
                )
                return {"content": text, "tool_calls": _gemini_tool_calls_to_openai(function_calls)}
            except Exception as exc:
                log.warning("Planner: Gemini call failed on model=%s key=...%s: %s: %s",
                            model, key[-4:], type(exc).__name__, exc)
                continue
    return None


def _requires_mandatory_web_research(message: str) -> bool:
    """Return True for explicit research/analysis requests where live evidence is part of the job.

    The planner remains useful for ordinary conceptual questions, but it must not be allowed to
    answer a research brief from model memory merely because it chose ``NONE``.
    """
    m = (message or "").strip().lower()
    # Compact, time-sensitive market/policy questions still need live evidence.
    # Keep these on the direct-search path rather than paying for saved-research
    # retrieval plus an LLM planning round before the live search starts.
    if _is_short_live_lookup(m):
        return True
    if re.search(r"\b(upcoming ipos?|latest ipos?|sector performance|market performance|top gainers|top losers|nifty today|sensex today|latest market news|current market news)\b", m):
        return True
    if len(m) < 20:
        return False
    patterns = (
        r"\b(research|research report|research brief|deep dive|due diligence)\b",
        r"\b(assess|evaluate|examine|investigate|analy[sz]e|analyse|review)\b.{0,90}\b(market|industry|sector|company|outlook|growth|competition|profitab|earnings|margin|capex|policy|trend|forecast)\b",
        r"\b(growth outlook|market outlook|industry outlook|competitive landscape|market dynamics)\b",
        r"\b(management commentary|analyst (views|perspectives)|institutional (views|research)|company results)\b",
        r"\b(through|by|over the next)\s+\d+\s+years?\b",
    )
    return any(re.search(p, m, re.IGNORECASE | re.DOTALL) for p in patterns)


def _should_search_stored_web_research(message: str) -> bool:
    """Keep cross-query retrieval relevant to Growth Gradual's finance/research domain."""
    value = str(message or "").strip()
    if len(value) < 12:
        return False
    if re.search(r"\b(previous|previously|saved|stored|crawled|scraped)\b.{0,70}\b(research|article|source|website|crawl|data|report)s?\b", value, re.I):
        return True
    if re.search(r"\b(what did we store|what is stored|search our database|in our knowledge base|from the knowledge base)\b", value, re.I):
        return True
    domain_or_research = re.compile(
        r"\b(finance|financial|market|markets|stock|stocks|share|shares|equity|equities|"
        r"bank|banking|economy|economic|inflation|interest rate|monetary policy|rbi|sebi|"
        r"nifty|sensex|ipo|mutual fund|investment|investor|company|companies|sector|industry|"
        r"earnings|revenue|profit|valuation|dividend|bond|bonds|fund|funds|business|trade|"
        r"gdp|forecast|outlook|growth|competition|competitor|financial report|annual report)\b",
        re.I,
    )
    analytic_intent = re.compile(
        r"\b(analy[sz]e|analysis|assess|assessment|evaluate|examine|compare|outlook|trend|"
        r"latest|current|recent|what happened|explain|report|research|impact|risk|risks|"
        r"opportunit(?:y|ies)|performance|prospects|update|summary|summari[sz]e)\b", re.I
    )
    return bool(domain_or_research.search(value) and (analytic_intent.search(value) or len(value.split()) >= 7))


_KNOWN_COMPANY_MENTION_RE = re.compile(
    r"\b(?:SBI|State Bank of India|HDFC Bank|ICICI Bank|Axis Bank|Kotak Mahindra Bank|"
    r"Bank of Baroda|Canara Bank|Punjab National Bank|IndusInd Bank|IDFC First Bank|Yes Bank|"
    r"TCS|Tata Consultancy Services|Infosys|Wipro|HCLTech|Reliance Industries|Bharti Airtel|"
    r"Larsen(?: &| and) Toubro|L&T|ITC|Tata Motors|Maruti Suzuki|Bajaj Finance|"
    r"Hindustan Unilever|HUL|Adani Enterprises|Asian Paints|Titan Company|ONGC|NTPC|"
    r"Power Grid|Coal India|Sun Pharma|HDFC|ICICI|Reliance|Infosys|Tata Steel)\b",
    re.IGNORECASE,
)


def _mentions_specific_company(message: str) -> bool:
    return bool(_KNOWN_COMPANY_MENTION_RE.search(str(message or "")))


def _is_short_live_lookup(message: str) -> bool:
    value = str(message or "").strip()
    words = value.split()
    if not value or len(words) > 7:
        return False
    # Explicit, common compact finance/news lookups.
    known_lookup = re.search(
        r"\b(upcoming ipos?|latest ipos?|sector performance|market performance|top gainers|top losers|"
        r"nifty( outlook| analysis| performance)? (today|now|latest)|"
        r"sensex( outlook| analysis| performance)? (today|now|latest)|"
        r"latest market news|current market news|rbi rate cut impact|repo rate impact)\b",
        value, re.IGNORECASE,
    ) or re.search(
        # Ranking tiles imply a current shortlist even when their label omits
        # words like "today" or "right now". Keep this bounded to compact
        # queries and finance categories so it doesn't hijack broader analysis.
        r"\b(top|best)\s+(?:(?:indian|india|large[- ]cap|mid[- ]cap|small[- ]cap|"
        r"public sector|private sector|banking|bank|it|technology|pharma|auto|energy)\s+){0,2}"
        r"(stocks?|shares?|equities?|banking stocks?|bank stocks?|mutual funds?|index funds?|"
        r"equity funds?|debt funds?|sips?|systematic investment plans?)\b",
        value, re.IGNORECASE,
    ) or re.search(
        # Common finance suggestion tiles should not incur an extra planner
        # round before live evidence is retrieved. Examples: "Best SIPs right
        # now", "top mutual funds today", and "best index funds currently".
        r"\b(best|top)\s+(sips?|systematic investment plans?|mutual funds?|"
        r"equity funds?|index funds?|debt funds?)\b.{0,32}"
        r"\b(right now|now|today|latest|current|currently)\b",
        value, re.IGNORECASE,
    )
    if known_lookup:
        return True
    # Generic compact market/policy query, e.g. "RBI rate cut impact" or
    # "banking stocks outlook". Longer questions stay on the full planner path.
    market_subject = re.search(
        r"\b(rbi|repo rate|rate cut|nifty|sensex|stock market|markets?|banking stocks|stocks?|ipo|inflation|gdp|"
        r"sips?|systematic investment plans?|mutual funds?|equity funds?|index funds?|debt funds?)\b",
        value, re.IGNORECASE,
    )
    live_intent = re.search(
        r"\b(today|now|latest|current|recent|outlook|impact|performance|news|analysis|forecast|update|rate cut|repo rate)\b",
        value, re.IGNORECASE,
    )
    return bool(market_subject and live_intent)


def _heuristic_plan(ctx: ToolContext, toolset_names: list[str], search_queries: list[str]) -> list[tuple[str, dict]]:
    """
    Deterministic fallback used only when the planner LLM is unreachable.

    Deliberately domain-neutral: it asks the existing, subject-agnostic
    ``needs_web_search`` helper whether the message looks like it needs
    external information, and offers the document index when the session has
    documents. Company fundamentals is included only when the user explicitly
    names a recognized company; never probe that financial store with an empty
    companies list for generic sector, news, or conceptual questions.
    """
    from routes.chat import needs_web_search

    calls: list[tuple[str, dict]] = []
    if "document_search" in toolset_names:
        calls.append(("document_search", {"query": search_queries[0] if search_queries else ctx.user_message}))
    if "stored_web_research" in toolset_names and _should_search_stored_web_research(ctx.user_message):
        fresh = bool(re.search(r"\b(latest|current|today|recent|this week|this month)\b", ctx.user_message, re.I))
        calls.append(("stored_web_research", {"query": ctx.user_message, "limit": 6, **({"max_age_days": 30} if fresh else {})}))
    if "web_search" in toolset_names and needs_web_search(ctx.user_message, has_files=ctx.has_files):
        calls.append(("web_search", {"queries": search_queries[:3] or [ctx.user_message]}))
    if "company_fundamentals" in toolset_names and _mentions_specific_company(ctx.user_message):
        calls.append(("company_fundamentals", {"companies": [ctx.user_message[:160]]}))
    if "market_data" in toolset_names and re.search(
        r"\b(ohlc|open[,\s]+high[,\s]+low|daily price action|price action)\b",
        ctx.user_message, re.IGNORECASE,
    ):
        calls.append(("market_data", {"daily_ohlc": True}))
    return calls


async def orchestrate(
    *,
    user_message: str,
    messages: list[dict],
    session_id: str = "",
    has_rag: bool = False,
    has_files: bool = False,
    search_queries: list[str] | None = None,
    max_rounds: int | None = None,
    skip: bool = False,
    progress_callback: ProgressCallback | None = None,
) -> OrchestrationResult:
    """
    Decide which tools this message needs, run them, and return the gathered
    context. Never raises.

    ``skip=True`` short-circuits everything (used for greetings/small talk):
    no planner call, no tools, no latency.

    ``search_queries`` is only a hint for the heuristic fallback — when the
    planner LLM is working it writes its own queries.
    """
    out = OrchestrationResult()
    ctx = ToolContext(
        session_id=session_id,
        user_message=user_message,
        has_rag=has_rag,
        has_files=has_files,
    )

    if skip:
        await _emit_progress(progress_callback, {
            "phase": "research", "status": "completed", "message": "No external research needed for this message."
        })
        out.planner = "skipped"
        out.planner_note = "conversational turn — no retrieval attempted"
        log.info("Orchestrator: skipped (conversational turn)")
        return out

    toolset = build_toolset(ctx)
    if not toolset:
        out.planner = "skipped"
        out.planner_note = "no tools are configured/available for this request"
        out.limitations.append(
            "No retrieval tools were available for this request — the answer relies on model knowledge only."
        )
        log.warning("Orchestrator: no tools available — answering from model knowledge")
        return out

    toolset_names = [t.name for t in toolset]
    schemas = tool_schemas(toolset)
    # Take the direct-search path only when the live web-search tool is genuinely
    # available. Without it, let the planner/heuristic select another useful path.
    short_live_lookup = (
        _is_short_live_lookup(user_message)
        and not has_files and not has_rag
        and "web_search" in toolset_names
        and _requires_mandatory_web_research(user_message)
    )
    rounds = 0 if short_live_lookup else max(1, min(4, max_rounds or MAX_ROUNDS))
    queries = [q for q in (search_queries or []) if q and q.strip()] or [user_message]

    # Explicit research briefs require live evidence. Finance/business questions also
    # retrieve matching saved crawl/report content so stored web sources are reusable
    # across separate chat turns rather than only during report generation.
    mandatory_research = "web_search" in toolset_names and _requires_mandatory_web_research(user_message)
    stored_research_prefetch = (
        not short_live_lookup
        and "stored_web_research" in toolset_names
        and _should_search_stored_web_research(user_message)
    )
    used_names: set[str] = set()
    prefetch_calls: list[tuple[str, dict]] = []
    if mandatory_research:
        prefetch_calls.append(("web_search", {
            "queries": queries[:2],
            "topic": "finance" if re.search(r"\b(stock|shares|earnings|valuation|market cap|investor|financials?)\b", user_message, re.I) else "general",
            "max_results": 8,
        }))
    if stored_research_prefetch:
        fresh_query = bool(re.search(r"\b(latest|current|today|recent|this week|this month)\b", user_message, re.I))
        prefetch_calls.append(("stored_web_research", {
            "query": user_message,
            "limit": 4,
            **({"max_age_days": 30} if fresh_query else {}),
        }))
    if prefetch_calls:
        log.info("Orchestrator: prefetching %s before optional planning", ", ".join(name for name, _ in prefetch_calls))
        await _emit_progress(progress_callback, {
            "phase": "research", "status": "started", "tools": [name for name, _ in prefetch_calls],
            "message": "Starting live and saved-source research…",
        })
        for result in await execute_tools(prefetch_calls, ctx, progress_callback=progress_callback):
            out.results.append(result)
            used_names.add(result.name)
            if result.context:
                out.context_blocks.append(result.truncated_context())
            out.sources.extend(result.sources)
            if not result.ok and result.error:
                out.limitations.append(f"{result.name}: {result.error}")
        out.planner_note = "live and/or stored research retrieval executed before optional planning"

    if short_live_lookup and prefetch_calls:
        out.rounds = 1
        out.planner = "direct-search"
        out.planner_note = "short, time-sensitive lookup served from bounded live/stored retrieval without an extra planner round"
        log.info("Orchestrator: direct-search fast path for short live lookup")

    convo: list[dict] = [{"role": "system", "content": _planner_system_prompt(toolset_names)}]
    convo += _messages_for_planner(messages, user_message)

    t_plan = time.perf_counter()
    planner_failed = False
    # Which backend actually planned this request — starts on Groq every
    # time; if Groq is down (no keys / all banned / network failure) round 1
    # falls back to Gemini and every subsequent round sticks with Gemini too,
    # rather than re-probing Groq each round: if it was down, it's very
    # unlikely to have recovered a few hundred ms later, so retrying it per
    # round only adds latency without changing the outcome.
    active_backend = "groq"

    for round_no in range(1, rounds + 1):
        await _emit_progress(progress_callback, {
            "phase": "planning", "status": "started", "round": round_no,
            "message": _planner_status_message(active_backend, round_no),
        })
        if active_backend == "groq":
            assistant_msg = await _groq_plan(convo, schemas)
            if assistant_msg is None:
                log.warning("Planner: Groq unavailable — falling back to Gemini as planner")
                active_backend = "gemini"
                await _emit_progress(progress_callback, {
                    "phase": "planning", "status": "fallback", "provider": "gemini",
                    "message": "Switching to the backup planner…",
                })
                assistant_msg = await _gemini_plan(convo, schemas)
        else:
            assistant_msg = await _gemini_plan(convo, schemas)
        await _emit_progress(progress_callback, {
            "phase": "planning", "status": "completed", "round": round_no,
            "provider": active_backend,
            "message": "Research plan ready; checking selected sources…" if assistant_msg and _parse_tool_calls(assistant_msg) else "Research plan ready; preparing the answer…",
        })

        if assistant_msg is None:
            planner_failed = True
            break

        planner_label = "groq-tool-calling" if active_backend == "groq" else "gemini-tool-calling"

        calls = _parse_tool_calls(assistant_msg)
        if not calls:
            note = (assistant_msg.get("content") or "").strip()[:200]
            log.info(
                "Orchestrator round %d: planner (%s) chose no tools (%s) — answering from model knowledge",
                round_no, planner_label, note or "no note",
            )
            out.planner = planner_label + ("+mandatory-web" if mandatory_research else "") + ("+stored-web" if stored_research_prefetch else "")
            out.rounds = round_no
            if round_no == 1 and not mandatory_research and not stored_research_prefetch:
                out.planner_note = "planner decided no external retrieval was needed"
            break

        # Drop repeats of a tool already run this request — the planner
        # occasionally re-requests an identical call after seeing its output.
        fresh_calls = [
            c for c in calls
            if c[0] not in used_names
            and not (c[0] == "company_fundamentals" and not _mentions_specific_company(user_message))
        ]
        if any(c[0] == "company_fundamentals" and not _mentions_specific_company(user_message) for c in calls):
            log.info("Orchestrator: ignored company_fundamentals for a query without a named company")
        if not fresh_calls:
            log.info("Orchestrator round %d: planner (%s) only re-requested already-run tools — stopping",
                     round_no, planner_label)
            out.planner = planner_label
            out.rounds = round_no
            break

        log.info(
            "Orchestrator round %d: planner selected %s",
            round_no, ", ".join(f"{n}({', '.join(sorted(a))})" for n, a, _ in fresh_calls),
        )

        results = await execute_tools(
            [(n, a) for n, a, _ in fresh_calls], ctx,
            progress_callback=progress_callback,
        )

        # Record the assistant's tool_calls turn, then one tool message per
        # call — required by the OpenAI/Groq protocol for the next round.
        convo.append({
            "role": "assistant",
            "content": assistant_msg.get("content") or "",
            "tool_calls": assistant_msg.get("tool_calls") or [],
        })
        # Any call we filtered out still needs a tool message, or the protocol
        # breaks on the next round (every tool_call id must be answered).
        answered: dict[str, str] = {}
        for (name, _args, call_id), result in zip(fresh_calls, results):
            used_names.add(name)
            out.results.append(result)
            if result.context:
                out.context_blocks.append(result.truncated_context())
            out.sources.extend(result.sources)
            if not result.ok and result.error:
                out.limitations.append(f"{name}: {result.error}")
            answered[call_id] = result.summary or (result.error or "no output")
        for name, _args, call_id in calls:
            convo.append({
                "role": "tool",
                "tool_call_id": call_id,
                "name": name,
                "content": answered.get(call_id, "Skipped — this tool already ran for this request."),
            })

        out.planner = planner_label
        out.rounds = round_no

        # Nothing usable came back and we still have a round left — let the
        # planner see that and try a different angle.
        if round_no >= rounds:
            break

    if planner_failed:
        out.planner = "heuristic"
        out.planner_note = "Groq and Gemini planners both unavailable — used deterministic fallback plan"
        out.limitations.append(
            "Tool selection fell back to a heuristic plan because no planning model was available."
        )
        calls = [(name, args) for name, args in _heuristic_plan(ctx, toolset_names, queries) if name not in used_names]
        log.warning(
            "Orchestrator: planner unavailable — heuristic plan = %s",
            ", ".join(n for n, _ in calls) or "no tools",
        )
        if calls:
            for result in await execute_tools(calls, ctx, progress_callback=progress_callback):
                out.results.append(result)
                if result.context:
                    out.context_blocks.append(result.truncated_context())
                out.sources.extend(result.sources)
                if not result.ok and result.error:
                    out.limitations.append(f"{result.name}: {result.error}")
            out.rounds = 1

    # Dedupe sources by URL, preserving first-seen order.
    seen: set[str] = set()
    deduped: list[dict] = []
    for s in out.sources:
        url = s.get("url") or ""
        if url and url in seen:
            continue
        if url:
            seen.add(url)
        deduped.append(s)
    out.sources = deduped

    log.info(
        "Orchestrator done: planner=%s rounds=%d tools=[%s] sources=%d context=%dch "
        "limitations=%d in %dms",
        out.planner, out.rounds, ", ".join(out.tools_used) or "none",
        len(out.sources), len(out.context()), len(out.limitations),
        int((time.perf_counter() - t_plan) * 1000),
    )
    await _emit_progress(progress_callback, {
        "phase": "research", "status": "completed", "sourceCount": len(out.sources),
        "tools": out.tools_used,
        "message": f"Research complete: {len(out.sources)} source(s) available. Preparing your answer…" if out.sources else "Research check complete. Preparing your answer…",
    })
    return out

