"""Self-description built from the final report (Stage I).

The methodology said "21 sources are cited" while Data Sources listed 23: the count was taken
before the final edits and from a different set. The methodology now carries a placeholder that is
resolved here, after every gate, from exactly the set the renderer lists in Data Sources: source ids
cited in the final text, in key statistics and in chart source fields, that exist in the manifest.
"""
from __future__ import annotations

import re

PLACEHOLDER = "{{CITED_SOURCE_COUNT}}"


def listed_source_ids(report: str, charts: list | None, key_stats: list | None, manifest: list | None) -> set[str]:
    from routes.html_report import _source_ids
    ids = set(_source_ids(report))
    for stat in key_stats or []:
        if isinstance(stat, dict):
            ids.update(_source_ids(stat.get("source") or stat.get("sources") or stat.get("sourceIds")))
    for chart in charts or []:
        if isinstance(chart, dict):
            ids.update(_source_ids(chart.get("source") or chart.get("sources") or chart.get("sourceIds")))
    known = {str(s.get("id") or "").strip().upper() for s in manifest or [] if isinstance(s, dict)}
    return {i.upper() for i in ids if i.upper() in known} if known else {i.upper() for i in ids}


def resolve_source_count(report: str, charts: list | None, key_stats: list | None, manifest: list | None) -> str:
    if PLACEHOLDER not in (report or ""):
        return report
    count = len(listed_source_ids(report.replace(PLACEHOLDER, ""), charts, key_stats, manifest))
    return report.replace(PLACEHOLDER, str(count))


NAVIGATION_SECTION_RE = re.compile(r"(?i)^\s*(?:sector scorecard|how to read|contents|table of contents)\b")
