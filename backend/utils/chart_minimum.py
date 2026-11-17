"""Reaching a report's chart minimum without loosening the evidence rules.

Live 30-prompt run: detailed reports averaged 1-3 charts against a target of 8, mostly because 40% of the
candidate charts held data outside the question's years. Two sources fill the gap, both still checked by
the same chart verification as every other chart:

* context charts - charts dropped only for the question's window, shown with a label saying their data is
  outside the requested period (``spec["contextOutsideWindow"]``, which the window checks honour);
* report-number charts - a sentence that compares two or more inserted table facts of one measure becomes a
  small chart of those exact source rows, placed after the paragraph that discusses them.
"""
from __future__ import annotations

import copy
import re

CONTEXT_NOTE = "Shown for context: this data falls outside the period the question asks about."


def _years(chart) -> list[int]:
    from utils.section_charts import _period_end_year
    years = []
    for series in chart.spec.get("series") or []:
        for point in series.get("data") or []:
            year = _period_end_year(str(point.get("label") or ""))
            if year is not None:
                years.append(year)
    if not years:
        years = [int(y) for y in re.findall(r"\b((?:19|20)\d{2})\b", str(chart.spec.get("title") or ""))]
    return years


def _distance(chart, min_year: int | None, max_year: int | None) -> int:
    """Years between the chart's data and the question's window (0 when they touch)."""
    years = _years(chart)
    if not years:
        return 50
    lo, hi = min(years), max(years)
    if min_year is not None and hi < min_year:
        return min_year - hi
    if max_year is not None and lo > max_year:
        return lo - max_year
    return 0


def context_charts(pool: list, existing: list, *, min_year: int | None, max_year: int | None,
                   limit: int) -> list:
    """Up to ``limit`` labelled context charts from ``pool``, nearest to the window first, none repeating the
    data of a chart already in the report."""
    if limit <= 0 or not pool:
        return []
    seen = [{round(v, 4) for _, _, v in c.points} for c in existing]
    out = []
    for chart in sorted(pool, key=lambda c: (_distance(c, min_year, max_year), -len(c.points))):
        values = {round(v, 4) for _, _, v in chart.points}
        if len(values) < 3 or any(len(values & other) >= 0.6 * min(len(values), len(other) or 1) for other in seen):
            continue
        chart = copy.deepcopy(chart)
        chart.spec["contextOutsideWindow"] = True
        chart.spec["title"] = f"{chart.spec.get('title')} (context: outside the requested period)"
        chart.spec["basisNote"] = f"{chart.spec.get('basisNote') or ''} {CONTEXT_NOTE}".strip()
        chart.chart_id = f"x{chart.chart_id}"
        chart.status, chart.drop_reason, chart.section_id, chart.figure_number = "planned", "", "", 0
        out.append(chart)
        seen.append(values)
        if len(out) >= limit:
            break
    return out


def _table_part(passage_text: str, labels: list[str]) -> list[str]:
    """The lines of the one table (caption, header, rows) whose rows carry all ``labels``."""
    lines = passage_text.splitlines()
    caption = next((ln for ln in lines if ln.strip() and " | " not in ln), "")
    blocks, current = [], []
    for line in lines:
        if " | " in line:
            current.append(line)
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    for block in blocks:
        # Stacked tables inside one block: start a new table at each header-like row.
        from utils.chart_evidence_audit import _is_header_row
        tables, table = [], []
        for line in block:
            if table and _is_header_row(line) and len(table) > 1:
                tables.append(table)
                table = []
            table.append(line)
        tables.append(table)
        for table in tables:
            firsts = {line.split("|")[0].strip() for line in table[1:]}
            if all(label in firsts for label in labels):
                return [caption, table[0], *[ln for ln in table[1:] if ln.split("|")[0].strip() in labels]]
    return []


def report_number_charts(drafts: list, store, *, start_index: int, limit: int) -> list:
    """Charts of the table facts one sentence compares (two or more rows, one measure, one table)."""
    if limit <= 0:
        return []
    from types import SimpleNamespace

    from utils.section_charts import charts_from_passage
    out = []
    for draft in drafts:
        groups: dict[tuple, list] = {}
        for entry in getattr(draft, "fact_refs_used", []) or []:
            fact, sentence = entry.get("_fact"), entry.get("_sentence")
            if fact is None or not sentence or getattr(fact, "kind", "") != "table" or not fact.label:
                continue
            probe = re.sub(r"\s+", " ", sentence.replace("⁠", "")).strip()[:60]
            if probe and probe not in re.sub(r"\s+", " ", (getattr(draft, "body", "") or "").replace("⁠", "")):
                continue  # the sentence was removed since (number policy or claim check): no chart for it
            groups.setdefault((sentence, fact.passage_id, fact.metric), []).append(fact)
        for (sentence, passage_id, metric), facts in groups.items():
            labels = list(dict.fromkeys(f.label for f in facts))
            if len(labels) < 2:
                continue
            passage = store.get(passage_id) if store is not None else None
            if passage is None:
                continue
            part = _table_part(passage.text, labels)
            if len(part) < 4:
                continue
            header = [c.strip() for c in part[1].strip().strip("|").split("|")]
            if metric not in header:
                continue
            shadow = SimpleNamespace(**{k: getattr(passage, k) for k in (
                "passage_id", "source_id", "kind", "reliability", "publisher", "title")})
            shadow.text = "\n".join(part)
            charts = charts_from_passage(shadow, start_index + len(out), _only_cols=[header.index(metric)])
            if not charts:
                continue
            chart = charts[0]
            chart.chart_id = f"r{start_index + len(out)}"
            chart.passage_ids = [passage_id]
            chart.section_id = draft.section_id
            chart.spec["fromReportText"] = True
            chart.claim = re.sub(r"\s+", " ", sentence.replace("⁠", "")).strip()[:400]
            out.append(chart)
            if len(out) >= limit:
                return out
    return out


def place_after_sentence(body: str, sentence: str, figure_number: int, reading: str) -> str:
    """Put "[[FIGURE n]]" and its reading after the paragraph that holds ``sentence`` (end of section if not
    found)."""
    marker = f"[[FIGURE {figure_number}]]"
    text = f"{marker}\n\nAs Figure {figure_number} shows, {reading[0].lower() + reading[1:] if reading else ''}".rstrip()
    probe = re.sub(r"\s+", " ", sentence.replace("⁠", "")).strip()[:60]
    paragraphs = re.split(r"\n\s*\n", body)
    for i, paragraph in enumerate(paragraphs):
        if probe and probe in re.sub(r"\s+", " ", paragraph):
            paragraphs.insert(i + 1, text)
            return "\n\n".join(paragraphs)
    return f"{body.rstrip()}\n\n{text}"
