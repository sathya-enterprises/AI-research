"""Stage L: the data-gap agent (Gemini API Antigravity managed agent), OFF by default.

When a deep-dive entity has no data table in the evidence (so its section would have no chart), one
bounded agent task looks for the official / primary pages that publish the missing year-by-year series:

    gaps  = data_gaps(tiers, question)                      # deep-dives with no table evidence
    pages, note = await find_data_pages(gaps, question, window=...)

The agent's answer is treated as LEADS ONLY: the report fetches every returned URL itself, and the
pages pass the same relevance gate, table extraction and chart / number verification as any search
result. Nothing the agent writes is quoted in the report.

Switch on with GG_ANTIGRAVITY_AGENT=1. Budget: one interaction per report (GG_ANTIGRAVITY_MAX_GAPS gaps,
default 4), ``max_total_tokens`` GG_ANTIGRAVITY_TOKEN_BUDGET (default 200000), timeout
GG_ANTIGRAVITY_TIMEOUT seconds (default 300). Model: GG_ANTIGRAVITY_MODEL (default: the agent's own).
A 429 / quota error or a timeout skips the step; the report continues without it.
"""
from __future__ import annotations

import json
import logging
import os
import re

log = logging.getLogger("gap_agent")

AGENT_ID = os.getenv("GG_ANTIGRAVITY_AGENT_ID", "antigravity-preview-09-2026")
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/interactions"
_URL_RE = re.compile(r"https?://[^\s<>\"')\]|]+")

PROMPT = """You are a data scout for a research report. Question: {question}
Time window: {window}

For each item below, find the OFFICIAL or PRIMARY web page (regulator, statistics office, exchange,
depository, industry association, company filing) that publishes the numbers year by year (or month by
month) within the window. Use search and open the pages to confirm the numbers are on them.

{items}

Answer with one line per page found, exactly in this form and nothing else:
ITEM <n> | <url> | <one exact line copied from the page that contains a number>
Skip an item when no such page exists. Never invent a URL or a number."""


def data_gaps(tiers: dict | None, question: str, *, limit: int | None = None) -> list[dict]:
    """Deep-dive entities with no table evidence: {entity, need}."""
    limit = limit if limit is not None else int(os.getenv("GG_ANTIGRAVITY_MAX_GAPS", "4") or 4)
    out = []
    for entity in (tiers or {}).get("deep_dive", []):
        if entity.table_passages:
            continue
        out.append({"entity": entity.name,
                    "need": f"year-by-year data on {entity.name} for the question's main measure"})
    return out[:limit]


def parse_leads(text: str, gaps: list[dict]) -> list[dict]:
    """ITEM n | url | quote lines -> [{url, entity, quote}] (unique URLs; malformed lines ignored)."""
    leads, seen = [], set()
    for line in str(text or "").splitlines():
        m = re.match(r"\s*ITEM\s*(\d+)\s*\|\s*(\S+)\s*\|\s*(.+)$", line)
        if not m:
            continue
        url = _URL_RE.match(m.group(2))
        if not url:
            continue
        u = url.group(0).rstrip(".,;")
        if u.casefold() in seen:
            continue
        seen.add(u.casefold())
        n = int(m.group(1))
        entity = gaps[n - 1]["entity"] if 1 <= n <= len(gaps) else ""
        leads.append({"url": u, "entity": entity, "quote": m.group(3).strip()[:300]})
    return leads


async def find_data_pages(gaps: list[dict], question: str, *, window: str = "", api_key: str | None = None,
                          client=None) -> tuple[list[dict], dict]:
    """One agent interaction; returns (leads, note). Never raises."""
    note: dict = {"enabled": os.getenv("GG_ANTIGRAVITY_AGENT", "0") == "1", "gaps": [g["entity"] for g in gaps]}
    if not note["enabled"] or not gaps:
        return [], note
    if api_key is None:
        try:
            from utils.keys import get_gemini_keys
            api_key = (get_gemini_keys() or [""])[0]
        except Exception:
            api_key = ""
    if not api_key:
        note["skipped"] = "no Gemini API key"
        return [], note
    items = "\n".join(f"ITEM {i}: {g['entity']} - {g['need']}" for i, g in enumerate(gaps, 1))
    body: dict = {
        "agent": AGENT_ID,
        "input": PROMPT.format(question=str(question)[:1500], window=window or "the period the question asks about", items=items),
        "environment": "remote",
        "agent_config": {"type": "antigravity",
                         "max_total_tokens": int(os.getenv("GG_ANTIGRAVITY_TOKEN_BUDGET", "200000") or 200000)},
        "tools": [{"type": "google_search"}, {"type": "url_context"}],
    }
    if os.getenv("GG_ANTIGRAVITY_MODEL"):
        body["agent_config"]["model"] = os.getenv("GG_ANTIGRAVITY_MODEL")
    timeout = float(os.getenv("GG_ANTIGRAVITY_TIMEOUT", "300") or 300)
    try:
        import httpx
        own = client is None
        client = client or httpx.AsyncClient(timeout=timeout)
        try:
            resp = await client.post(ENDPOINT, headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                                     content=json.dumps(body))
        finally:
            if own:
                await client.aclose()
        note["status"] = resp.status_code
        if resp.status_code != 200:
            note["skipped"] = f"agent returned HTTP {resp.status_code}"  # 429 = quota: the report goes on
            log.warning("Gap agent: HTTP %s - step skipped", resp.status_code)
            return [], note
        data = resp.json()
    except Exception as exc:
        note["skipped"] = f"agent call failed ({type(exc).__name__})"
        log.warning("Gap agent: call failed (%s) - step skipped", type(exc).__name__)
        return [], note
    text = str(data.get("output_text") or "")
    if not text:  # fall back to the last text output in the steps / outputs
        for out in reversed(data.get("outputs") or data.get("steps") or []):
            if isinstance(out, dict) and out.get("text"):
                text = str(out["text"])
                break
    usage = data.get("usage") or {}
    note["tokens"] = usage.get("total_tokens") or usage.get("totalTokenCount")
    leads = parse_leads(text, gaps)
    note["leads"] = len(leads)
    log.info("Gap agent: %d gap(s) -> %d lead page(s)", len(gaps), len(leads))
    return leads, note
