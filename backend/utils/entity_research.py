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


GENERIC_QUERY_TEMPLATES = (
    "{name} {topic} {window}",
    "{name} {topic} statistics data {window}",
    "{name} {topic} policy drivers challenges",
)


def plan_entity_queries(tiers: dict, *, cap: int, window: str = "", question: str = "") -> list[tuple[str, str]]:
    """(entity, query) pairs: compact entities first (most table evidence), then the weakest deep-dives."""
    if cap <= 0 or not tiers:
        return []
    # Stage N: publishers / governing bodies are where data comes from, not subjects to research; the
    # entities the measure plan named come first (the planner expects numbers for them).
    compact = sorted((e for e in tiers.get("compact", []) + tiers.get("coverage_note", [])
                      if getattr(e, "role", "") not in {"body", "out_of_scope"}
                      and (e.tier == "compact" or getattr(e, "seeded", False))),
                     key=lambda e: (0 if getattr(e, "seeded", False) else 1, -len(e.table_passages),
                                    -len(e.good_text_sources), e.name))
    weak_deep = sorted((e for e in tiers.get("deep_dive", []) if len(e.good_text_sources) < 5),
                       key=lambda e: len(e.good_text_sources))
    templates, topic = QUERY_TEMPLATES, ""
    market_topic = True
    if question:
        from utils.report_outline import is_market_topic
        market_topic = is_market_topic(question)
        if not market_topic:
            from utils.data_angles import subject_terms
            templates = GENERIC_QUERY_TEMPLATES
            topic = " ".join(t for t in subject_terms(question, limit=5))
    if not market_topic:
        # Table-only row labels remain available to deterministic charts, but without any text support they
        # should not each trigger three broad web searches (especially labels from metadata tables).
        compact = [e for e in compact if getattr(e, "seeded", False) or getattr(e, "text_passages", None)]
    plan = []
    for entity in compact + weak_deep:
        for template in templates:
            if len(plan) >= cap:
                return plan
            text = template.format(name=entity.name, window=window, topic=topic.replace(entity.name, "").strip())
            plan.append((entity.name, " ".join(text.split())))
    return plan


_EXTRACT_MIN_CHARS = 600


def _labelled_extracts(rows: list[dict]) -> list[dict]:
    """Rows whose page fetch failed but whose search-provider excerpt is substantial.

    They stay labelled ``search_extract_only`` (set by the enrichment step), the same treatment the main
    search gives a failed fetch, so an entity is not left with no evidence because its pages were slow."""
    out = []
    for row in rows:
        if row.get("pageFetchStatus") == "ok":
            continue
        if len(str(row.get("fullContent") or row.get("snippet") or "")) < _EXTRACT_MIN_CHARS:
            continue
        row.setdefault("evidenceOrigin", "search_extract_only")
        out.append(row)
    return out


async def run_entity_search(plan: list[tuple[str, str]], *, search_fn, filter_fn, enrich_fn, quality_fn,
                            existing_urls: set[str], rescue_fn=None,
                            keep_extracts: bool = True) -> tuple[list[dict], dict]:
    """Run the planned queries; return (new evidence rows, stats per entity).

    ``rescue_fn(rows)`` (optional, async) re-fetches blocked / timed-out rows in place (the browser tier).
    With ``keep_extracts`` a row whose page could not be fetched keeps its labelled search excerpt."""
    from utils.fetch_tiers import status_counts
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
    stats["fetchStatus"] = status_counts(fetched)
    if rescue_fn is not None and any(r.get("pageFetchStatus") != "ok" for r in fetched):
        try:
            stats["browser"] = await rescue_fn(fetched)
        except Exception as exc:  # the browser tier is optional
            stats["browser"] = {"error": type(exc).__name__}
    stats["fetched_ok"] = sum(1 for r in fetched if r.get("pageFetchStatus") == "ok")
    post = filter_fn([r for r in fetched if r.get("pageFetchStatus") == "ok"], require_context=True)
    stats["after_post_fetch_relevance"] = len(post)
    if keep_extracts:
        extracts = filter_fn(_labelled_extracts(fetched), require_context=True)
        stats["search_extracts_kept"] = len(extracts)
        post = post + extracts
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
    log.info("Entity search: queries=%d results=%d new=%d relevant=%d fetched=%d extracts=%d kept=%d status=%s",
             stats["queries"], stats["results"], stats["new_urls"], stats["after_relevance"], stats["fetched_ok"],
             stats.get("search_extracts_kept", 0), stats["kept"], stats.get("fetchStatus"))
    return kept, stats
