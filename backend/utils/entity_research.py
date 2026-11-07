"""Per-entity search before writing (Stage C1).

Entities below the deep-dive evidence threshold get targeted searches
(earnings outlook, valuation history, flow commentary). Results go through the
same Stage A path as the main search: relevance gate -> publisher fetch ->
post-fetch relevance -> source quality (denylist, reliability) -> evidence store.

The search, filter, fetch and quality steps are injected so the logic is
testable without network calls; report.py binds them to the live functions.
A per-report cap bounds search calls (GG_V2_ENTITY_SEARCH_CAP, default 24).
"""
from __future__ import annotations

import logging
import os
from collections import Counter, defaultdict

log = logging.getLogger("entity_research")

QUERY_TEMPLATES = (
    "{name} sector earnings outlook India {window}",
    "{name} sector valuation P/E versus history India",
    "{name} sector FPI DII flows India {window}",
)


def search_cap(tier: str) -> int:
    if tier != "in_depth":
        return 0
    try:
        return max(0, int(os.getenv("GG_V2_ENTITY_SEARCH_CAP", "24")))
    except ValueError:
        return 24


def plan_entity_queries(tiers: dict, *, cap: int, window: str = "") -> list[tuple[str, str]]:
    """(entity, query) pairs: compact entities first (most table evidence), then the weakest deep-dives."""
    if cap <= 0 or not tiers:
        return []
    compact = sorted(tiers.get("compact", []), key=lambda e: (-len(e.table_passages), -len(e.good_text_sources), e.name))
    weak_deep = sorted((e for e in tiers.get("deep_dive", []) if len(e.good_text_sources) < 5),
                       key=lambda e: len(e.good_text_sources))
    plan = []
    for entity in compact + weak_deep:
        for template in QUERY_TEMPLATES:
            if len(plan) >= cap:
                return plan
            plan.append((entity.name, " ".join(template.format(name=entity.name, window=window).split())))
    return plan


async def run_entity_search(plan: list[tuple[str, str]], *, search_fn, filter_fn, enrich_fn, quality_fn,
                            existing_urls: set[str]) -> tuple[list[dict], dict]:
    """Run the planned queries; return (new evidence rows, stats per entity)."""
    stats: dict = {"queries": len(plan), "results": 0, "new_urls": 0, "after_relevance": 0, "fetched_ok": 0,
                   "after_post_fetch_relevance": 0, "kept": 0, "perEntity": {}}
    if not plan:
        return [], stats
    entity_for_query = {q: e for e, q in plan}
    rows = await search_fn([q for _, q in plan])
    stats["results"] = len(rows)
    seen = set(existing_urls)
    fresh = []
    for row in rows:
        url = str(row.get("url") or "")
        if url and url not in seen:
            seen.add(url)
            row["entitySearch"] = entity_for_query.get(str(row.get("searchQuery") or ""), "")
            fresh.append(row)
    stats["new_urls"] = len(fresh)
    relevant = filter_fn(fresh, require_context=False)
    stats["after_relevance"] = len(relevant)
    fetched = await enrich_fn(relevant)
    stats["fetched_ok"] = sum(1 for r in fetched if r.get("pageFetchStatus") == "ok")
    post = filter_fn([r for r in fetched if r.get("pageFetchStatus") == "ok"], require_context=True)
    stats["after_post_fetch_relevance"] = len(post)
    kept, quality = quality_fn(post)
    stats["kept"] = len(kept)
    stats["quality"] = {k: v for k, v in quality.items() if k != "denylisted_hosts"}
    per = defaultdict(Counter)
    for entity, _ in plan:
        per[entity]["queries"] += 1
    for row in fresh:
        per[row.get("entitySearch") or "?"]["results"] += 1
    for row in kept:
        per[row.get("entitySearch") or "?"]["kept"] += 1
    stats["perEntity"] = {k: dict(v) for k, v in per.items()}
    log.info("Entity search: queries=%d results=%d new=%d relevant=%d fetched=%d kept=%d",
             stats["queries"], stats["results"], stats["new_urls"], stats["after_relevance"], stats["fetched_ok"], stats["kept"])
    return kept, stats
