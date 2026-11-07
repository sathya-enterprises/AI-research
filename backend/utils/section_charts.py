"""Charts planned with the section text (REPORT_PIPELINE_V2).

Charts are built deterministically from table / calculated passages in the
evidence store, so every plotted value is a cell copied from a stored passage
(with the exact row as its evidence quote). A chart is assigned to the outline
section whose queries match it best, is handed to that section's writer as a
numbered figure with its data, and records the claim the prose makes about it.

A candidate whose data cannot be verified (too few points, non-numeric cells,
mixed units, values not found in the passage) is dropped *before* writing, so
the writer never sees it and no text can refer to it.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

_NUM_CELL_RE = re.compile(r"^\(?([+\-−–]?)\s*(?:₹|\$|rs\.?\s*)?([\d,]*\.?\d+)\s*(%|x|pts|bps|cr|crore|bn|mn)?\)?$", re.I)
_PERIOD_RE = re.compile(
    r"^(?:Q[1-4](?:\s*FY\s*\d{2,4}|\s*CY?\s*\d{2,4}|\s*\d{4})?|FY\s*\d{2,4}(?:-\d{2,4})?|(?:19|20)\d{2}(?:-\d{2})?|"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*'?\d{2,4}|H[12]\s*FY\s*\d{2,4})$", re.I)
MAX_BAR_POINTS = 15
MAX_LINE_SERIES = 8


@dataclass
class PlannedChart:
    chart_id: str
    spec: dict
    passage_ids: list[str]
    source_ids: list[str]
    points: list[tuple[str, str, float]]          # (series/row label, column label, value)
    section_id: str = ""
    figure_number: int = 0
    claim: str = ""
    status: str = "planned"                       # planned | referenced | dropped
    drop_reason: str = ""
    match_text: str = ""

    def brief(self) -> str:
        """Compact data listing for the section writer."""
        unit = self.spec.get("unit") or ""
        head = f"Figure {self.figure_number}: {self.spec.get('title')} (unit: {unit or 'as labelled'}; evidence: {', '.join(self.passage_ids)})"
        rows: dict[str, list[str]] = {}
        for series, column, value in self.points:
            rows.setdefault(series, []).append(f"{column}={value:g}{unit if unit == '%' else ''}")
        body = "\n".join(f"  - {name}: {', '.join(cells)}" for name, cells in list(rows.items())[:20])
        return head + "\n" + body


def _parse_number(cell: str) -> tuple[float, str] | None:
    raw = str(cell or "").strip().replace("−", "-")
    match = _NUM_CELL_RE.match(raw.replace(" ", ""))
    if not match:
        return None
    sign, digits, unit = match.groups()
    try:
        value = float(digits.replace(",", ""))
    except ValueError:
        return None
    if sign in {"-", "−", "–"} or (raw.startswith("(") and raw.endswith(")")):
        value = -value
    if not math.isfinite(value):
        return None
    return value, (unit or "").lower()


def _split_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def charts_from_passage(passage, chart_index: int) -> list[PlannedChart]:
    """0–1 chart from one table passage (the best numeric view of the table)."""
    lines = [ln for ln in str(passage.text or "").splitlines() if " | " in ln]
    if len(lines) < 3:
        return []
    heading_line = next((ln for ln in str(passage.text).splitlines() if ln.strip() and " | " not in ln), "")
    heading = re.sub(r"^TABLE \d+\s*[—-]\s*", "", heading_line.strip()).replace("(continued)", "").strip()
    header = _split_row(lines[0])
    rows = [_split_row(ln) for ln in lines[1:]]
    rows = [r for r in rows if len(r) == len(header) and r[0]]
    if len(rows) < 2 or len(header) < 2:
        return []
    numeric_cols, col_units = [], {}
    for col in range(1, len(header)):
        parsed = [_parse_number(r[col]) for r in rows]
        good = [p for p in parsed if p]
        if len(good) >= max(2, int(0.7 * len(rows))):
            numeric_cols.append(col)
            units = {u for _, u in good} - {""}
            if "%" in header[col] or re.search(r"(?i)\breturns?\b.*\(%\)", header[col]):
                units.add("%")
            col_units[col] = units
    if not numeric_cols:
        return []

    def unit_of(cols: list[int]) -> str | None:
        """Unit shared by the plotted columns; None when they disagree (not one chart)."""
        merged = set().union(*(col_units[c] for c in cols)) if cols else set()
        if len(merged) > 1:
            return None
        return next(iter(merged)) if merged else ""

    period_cols = [c for c in numeric_cols if _PERIOD_RE.match(header[c])]
    if len(period_cols) >= 2 and unit_of(period_cols) is None:
        period_cols = []
    if len(period_cols) >= 2:
        unit = unit_of(period_cols) or ""
    else:
        single = next((c for c in numeric_cols if unit_of([c]) is not None), None)
        if single is None:
            return []
        numeric_cols = [single] + [c for c in numeric_cols if c != single]
        unit = unit_of([single]) or ""
    # FAQ-style or very long headings make poor chart titles.
    if len(heading) >= 6 and not heading.rstrip().endswith("?") and len(heading) <= 90:
        title = heading
    else:
        first = period_cols[0] if len(period_cols) >= 2 else numeric_cols[0]
        title = f"{'Values' if len(period_cols) >= 2 else header[first]} by {header[0] or 'item'}"
        if passage.publisher:
            title += f" ({passage.publisher})"
    evidence_rows = {r[0]: " | ".join(r) for r in rows}

    def point(label: str, col_label: str, value: float, raw: str, row_label: str) -> dict:
        return {"label": col_label if col_label else label, "value": value, "evidenceSource": passage.source_id,
                "evidenceQuote": evidence_rows.get(row_label, ""), "evidenceValue": raw}

    points: list[tuple[str, str, float]] = []
    spec: dict
    if len(period_cols) >= 3 and len(rows) > MAX_LINE_SERIES:
        matrix, labels = [], []
        for r in rows:
            vals = [(_parse_number(r[c]) or (None, ""))[0] for c in period_cols]
            if sum(v is not None for v in vals) >= 2:
                labels.append(r[0])
                matrix.append(vals)
                points += [(r[0], header[c], v) for c, v in zip(period_cols, vals) if v is not None]
        spec = {"type": "heatmap", "title": title, "xLabel": "Period", "yLabel": header[0] or "Item", "unit": unit,
                "rows": labels, "columns": [header[c] for c in period_cols], "values": matrix}
    elif len(period_cols) >= 2:
        series = []
        for r in rows[:MAX_LINE_SERIES]:
            data = []
            for c in period_cols:
                parsed = _parse_number(r[c])
                if parsed:
                    data.append(point(r[0], header[c], parsed[0], r[c], r[0]))
                    points.append((r[0], header[c], parsed[0]))
            if len(data) >= 2:
                series.append({"name": r[0], "data": data})
        spec = {"type": "line" if len(period_cols) >= 3 else "grouped_bar", "title": title, "xLabel": "Period",
                "yLabel": unit or "Value", "unit": unit, "series": series}
    else:
        col = numeric_cols[0]
        data = []
        for r in rows[:MAX_BAR_POINTS]:
            parsed = _parse_number(r[col])
            if parsed:
                data.append(point(r[0], r[0], parsed[0], r[col], r[0]))
                points.append((header[col], r[0], parsed[0]))
        spec = {"type": "bar", "title": f"{title} — {header[col]}" if header[col] and header[col] not in title else title, "xLabel": header[0] or "Item",
                "yLabel": header[col] or unit or "Value", "unit": unit, "series": [{"name": header[col] or "Value", "data": data}]}
    spec.update({"source": [passage.source_id], "sourceIds": [passage.source_id], "evidenceBasis":
                 "calculated" if passage.kind == "calculated" else "reported",
                 "basisNote": f"Values copied from evidence passage {passage.passage_id}."})
    chart = PlannedChart(chart_id=f"c{chart_index}", spec=spec, passage_ids=[passage.passage_id],
                         source_ids=[passage.source_id], points=points,
                         match_text=" ".join([title, header[0], *[r[0] for r in rows[:20]], *header[1:]]))
    return [chart]


def verify_chart(chart: PlannedChart, passage_text: str) -> str:
    """Return "" when verified, else the reason to drop the chart."""
    if len(chart.points) < 3:
        return "fewer than 3 data points"
    labels = {p[0] for p in chart.points} | {p[1] for p in chart.points}
    if len(labels) < 3:
        return "fewer than 3 distinct labels"
    values = [p[2] for p in chart.points]
    if all(v == 0 for v in values):
        return "all values are zero"
    text = str(passage_text or "")
    for series in chart.spec.get("series") or []:
        for item in series.get("data") or []:
            raw = str(item.get("evidenceValue") or "")
            if raw and raw not in text:
                return f"value {raw!r} not found in passage"
    return ""


def _outside_window(chart: PlannedChart, min_year: int | None) -> str:
    if not min_year:
        return ""
    labels = " ".join([str(chart.spec.get("title") or ""), *[str(a) for a, _, _ in chart.points], *[str(b) for _, b, _ in chart.points]])
    years = {int(y) for y in re.findall(r"\b((?:19|20)\d{2})\b", labels)}
    years |= {2000 + int(y) for y in re.findall(r"\bFY\s*'?(\d{2})\b", labels, flags=re.I)}
    if years and max(years) < min_year:
        return f"outside the requested window (latest period {max(years)}, window starts {min_year})"
    return ""


def build_chart_candidates(store, *, max_charts: int = 40, min_year: int | None = None,
                           min_reliability: float = 0.5) -> tuple[list[PlannedChart], list[dict]]:
    """Charts from every table/calculated passage; returns (verified charts, dropped log).

    Dropped before writing: unverifiable data, periods entirely before the
    requested window, and data from sources below ``min_reliability``.
    """
    charts, dropped, seen = [], [], set()
    for passage in store.passages:
        if passage.kind not in {"table", "calculated"}:
            continue
        for chart in charts_from_passage(passage, len(charts) + len(dropped) + 1):
            signature = tuple(sorted((a, b, round(v, 4)) for a, b, v in chart.points))
            if signature in seen:
                continue
            seen.add(signature)
            reason = verify_chart(chart, passage.text) or _outside_window(chart, min_year)
            if not reason and passage.kind != "calculated" and passage.reliability < min_reliability:
                reason = f"source reliability {passage.reliability:.2f} below {min_reliability:.2f}"
            if reason:
                chart.status, chart.drop_reason = "dropped", reason
                dropped.append({"chart": chart.chart_id, "title": chart.spec.get("title"), "passage": passage.passage_id, "reason": reason})
                continue
            charts.append(chart)
            if len(charts) >= max_charts:
                return charts, dropped
    return charts, dropped


def _terms(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]{3,}", str(text or "").casefold())}


def assign_charts(charts: list[PlannedChart], sections: list, *, per_section: int = 2) -> None:
    """Give each chart to the section whose queries/passages fit it best (passage overlap first)."""
    load: dict[str, int] = {}
    ranked = []
    for chart in charts:
        best = None
        for section in sections:
            score = 3.0 * len(set(chart.passage_ids) & set(section.passage_ids))
            score += len(_terms(chart.match_text) & _terms(" ".join([section.title, section.goal, *section.queries])))
            if best is None or score > best[0]:
                best = (score, section)
        if best and best[0] >= 2:
            ranked.append((best[0], chart, best[1]))
    for score, chart, section in sorted(ranked, key=lambda x: -x[0]):
        if load.get(section.section_id, 0) >= per_section:
            continue
        chart.section_id = section.section_id
        load[section.section_id] = load.get(section.section_id, 0) + 1
