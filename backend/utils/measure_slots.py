"""Measure slots: which (measure x entity) pairs of the measure plan already have data, and searches for the rest.

A slot is covered when a fetched page or data file holds a data block (a ``TABLE`` block, or at least three
year-number pairs) whose text carries the measure's words and, for an entity slot, the entity's name. Empty
slots get one more round of data-seeking searches before writing: the measure with the scope and entity,
phrased for statistics tables and report files, and once with the name of an official publisher this run has
already reached (one that published a covered measure, or any official host). Nothing here knows a topic.

    slots = slot_coverage(plan, rows)
    queries = gap_queries(slots, plan, years=..., official_hosts=..., cap=6)
"""
from __future__ import annotations

import re

from utils.primary_sources import host_of, is_primary

_STOP = {"the", "and", "for", "from", "with", "into", "over", "under", "per", "all", "total", "annual", "rate",
         "rates", "number", "share", "level", "levels", "size", "value", "values"}
_YEAR_NUMBER = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)[^\n\d]{0,60}?\d[\d,.]*")


def _content_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z0-9]{2,}", str(text or "").casefold()) if w not in _STOP}


def _data_blocks(text: str) -> list[str]:
    """TABLE blocks of a page (a table caption plus rows), or the whole text when it holds a year-number series."""
    text = str(text or "")
    blocks = re.split(r"(?m)^(?=TABLE \d+)", text)
    tables = [b for b in blocks if b.startswith("TABLE ") and b.count(" | ") >= 4]
    if tables:
        return tables
    return [text] if len(_YEAR_NUMBER.findall(text)) >= 3 else []


def _covers(block: str, measure_words: set[str], entity: str) -> bool:
    hay = block.casefold()
    words = _content_words(hay)
    need = max(1, (len(measure_words) + 1) // 2)
    if len(measure_words & words) < need:
        return False
    return not entity or entity.casefold() in hay


def slot_coverage(plan, rows: list[dict], *, max_entities: int = 3) -> list[dict]:
    """One dict per slot: measure, entity ('' for the scope as a whole), covered, sources (urls)."""
    pages = []
    for row in rows or []:
        if not isinstance(row, dict) or row.get("pageFetchStatus") != "ok":
            continue
        blocks = _data_blocks(row.get("pageContent") or "")
        if blocks:
            pages.append((str(row.get("url") or ""), blocks))
    slots = []
    for m in getattr(plan, "measures", None) or []:
        words = _content_words(m.measure)
        for entity in (m.entities[:max_entities] or [""]):
            sources = [url for url, blocks in pages if any(_covers(b, words, entity) for b in blocks)]
            slots.append({"measure": m.measure, "entity": entity, "covered": bool(sources), "sources": sources[:5]})
    return slots


def official_hosts(slots: list[dict]) -> list[str]:
    """Official hosts that already published data for one of the measures (most slots first)."""
    counts: dict[str, int] = {}
    for s in slots:
        for url in s["sources"]:
            if is_primary(url):
                counts[host_of(url)] = counts.get(host_of(url), 0) + 1
    return [h for h, _ in sorted(counts.items(), key=lambda kv: -kv[1]) if h]


def gap_queries(slots: list[dict], plan, *, years: list[int] | None = None, hosts: list[str] | None = None,
                cap: int = 6) -> list[str]:
    """Searches for empty slots, round-robin over measures (most important first) so one measure with many
    entities cannot take every search: a statistics-table query per empty slot (the measure for the whole
    scope first when none of its slots has data), then a report-file query, then one naming an official
    publisher this run already reached."""
    scope = str(getattr(plan, "scope", "") or "")
    span = f"{years[0]} to {years[-1]}" if years and len(years) >= 2 else ""
    by_measure: dict[str, list[dict]] = {}
    for s in slots:
        by_measure.setdefault(s["measure"], []).append(s)

    def subject(measure: str, entity: str) -> str:
        said = f"{measure} {entity}".casefold()
        where = scope if scope and scope.casefold() not in said else ""
        return " ".join(x for x in (measure, where, entity) if x)

    waves: list[list[str]] = []
    for measure, group in by_measure.items():
        empty = [s for s in group if not s["covered"]]
        if not empty:
            continue
        subjects = ([subject(measure, "")] if len(empty) == len(group) and empty[0]["entity"] else []) + \
                   [subject(measure, s["entity"]) for s in empty]
        queries = [f"{x} statistics table {span}".strip() for x in subjects]
        queries.append(f"{subjects[0]} annual report data xlsx OR pdf")
        queries += [f"{h} {subjects[0]} {span}".strip() for h in (hosts or [])[:1]]
        waves.append(queries)
    out: list[str] = []
    for i in range(max((len(w) for w in waves), default=0)):
        out += [w[i] for w in waves if i < len(w)]
    out = list(dict.fromkeys(re.sub(r"\s+", " ", q) for q in out))
    return out[: max(0, cap)]


def slot_limit(report_tier: str) -> int:
    return {"detailed": 3, "in_depth": 6}.get(str(report_tier or "detailed"), 3)
