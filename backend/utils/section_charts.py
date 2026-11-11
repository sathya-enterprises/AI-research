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
from types import SimpleNamespace

from utils.chart_titles import (clean_label, data_title, header_is_data, homogeneous_rows, label_entities,
                                non_comparable_metric, period_of)

_NUM_CELL_RE = re.compile(r"^\(?([+\-−–]?)\s*(?:₹|\$|rs\.?\s*)?([\d,]*\.?\d+)\s*(%|x|pts|bps|cr|crore|bn|mn)?\)?$", re.I)
_PERIOD_RE = re.compile(
    r"^(?:Q[1-4](?:\s*FY\s*\d{2,4}|\s*CY?\s*\d{2,4}|\s*\d{4})?|FY\s*\d{2,4}(?:-\d{2,4})?|(?:19|20)\d{2}(?:-\d{2})?|"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*'?\d{2,4}|H[12]\s*FY\s*\d{2,4})$", re.I)
MAX_BAR_POINTS = 15
MAX_LINE_SERIES = 8
MAX_COLUMN_SERIES = 4
_ROW_UNIT_RE = re.compile(r"\s*\((?:₹|rs\.?|inr|us\$|\$|€|£)?\s*(?:crore|cr|lakh|million|mn|billion|bn|trillion|thousand|'000)\b[^)]*\)", re.I)
_MONTH_WORD = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*"


def row_period_label(label: str) -> str:
    """Stage L: a period row label without its unit / footnote noise: "Calendar 2022 (₹ Crore)" -> "2022",
    "Dec-2025 (₹ Crore) #" -> "Dec 2025". Other labels are returned cleaned only."""
    text = clean_label(label)
    text = _ROW_UNIT_RE.sub("", text)
    text = re.sub(r"\s*[#*†‡]+\s*$", "", text).strip()
    text = re.sub(r"(?i)^(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?,?\s+(?=\d)", "", text)  # "Tue, 10 Nov 2026"
    text = re.sub(r"(?i)^(?:calendar(?:\s+year)?|cy|year)\s+(?=(?:19|20)\d{2}\b)", "", text)
    text = re.sub(rf"(?i)^({_MONTH_WORD})[-/ ]'?(\d{{2,4}})$", r"\1 \2", text)
    return text.strip()


_STOP = {"the", "of", "and", "in", "for", "to", "a", "rs", "inr", "crore", "cr", "x", "%", "value", "values"}


def _words(header: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", str(header or "").casefold()) if w not in _STOP and len(w) > 1}


def related_columns(cols: list[int], header: list[str], rows: list[list[str]]) -> list[int]:
    """Columns that measure the same thing: they share a word with the first one ("FPI Flows Secondary",
    "FPI Flows Primary"), and none is the sum of two others (a derived total next to its parts)."""
    if not cols:
        return []
    base = _words(header[cols[0]])
    keep = [c for c in cols if c == cols[0] or (_words(header[c]) & base)]

    def vals(c: int) -> list[float | None]:
        return [(_parse_number(r[c]) or (None, ""))[0] for r in rows]

    table = {c: vals(c) for c in keep}
    derived = set()
    for c in keep:
        for a in keep:
            for b in keep:
                if len({a, b, c}) < 3 or a > b:
                    continue
                pairs = [(x, y, z) for x, y, z in zip(table[a], table[b], table[c]) if None not in (x, y, z)]
                if len(pairs) >= 3 and sum(1 for x, y, z in pairs if abs((x + y) - z) <= 0.01 * max(1.0, abs(z))) >= 0.8 * len(pairs):
                    derived.add(c)
    return [c for c in keep if c not in derived]


def series_metric(names: list[str]) -> str:
    """"FPI Flows Secondary", "FPI Flows Primary" -> "FPI Flows: Secondary and Primary"."""
    split = [n.split() for n in names]
    common = []
    for words in zip(*split):
        if len({w.casefold() for w in words}) == 1:
            common.append(words[0])
        else:
            break
    rest = [" ".join(w[len(common):]) or " ".join(w) for w in split]
    tail = ", ".join(rest[:-1]) + " and " + rest[-1] if len(rest) > 1 else rest[0]
    return f"{' '.join(common)}: {tail}" if common else " vs ".join(names)


def period_granularity(label: str) -> str:
    """'month', 'quarter', 'half', 'year' or '' for a (cleaned) period label."""
    text = row_period_label(label)
    if re.search(rf"(?i)\b{_MONTH_WORD}\b", text) and re.search(r"\d{2,4}", text):
        return "month"
    if re.search(r"(?i)\bQ[1-4]\b", text):
        return "quarter"
    if re.search(r"(?i)\bH[12]\b", text):
        return "half"
    if re.fullmatch(r"(?i)(?:FY\s*'?)?(?:19|20)?\d{2}(?:-\d{2,4})?E?|(?:19|20)\d{2}", text):
        return "year"
    return ""


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
    heading: str = ""                             # page heading above the table (period context only)
    interpretation: str = ""                      # deterministic paragraph (fetched-dataset charts, Stage F)
    reading: str = ""                             # Stage L: deterministic reading of any verified chart (fallback text)

    def brief(self) -> str:
        """Compact data listing for the section writer."""
        unit = self.spec.get("unit") or ""
        head = f"Figure {self.figure_number}: {self.spec.get('title')} (unit: {unit or 'as labelled'}; evidence: {', '.join(self.passage_ids)})"
        rows: dict[str, list[str]] = {}
        for series, column, value in self.points:
            rows.setdefault(series, []).append(f"{column}={plain_number(value)}{unit if unit == '%' else ''}")
        body = "\n".join(f"  - {name}: {', '.join(cells)}" for name, cells in list(rows.items())[:20])
        return head + "\n" + body


def plain_number(value: float) -> str:
    """2821760.0 -> '2,821,760'; 0.000123 -> '0.000123'; never scientific notation (Stage J)."""
    value = float(value)
    if value.is_integer():
        return f"{int(value):,}"
    return f"{value:,.6f}".rstrip("0").rstrip(".")


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


QTD_LABEL = "QTD, partial"


class _QtdStart(int):
    """The current quarter's first month ordinal, carrying today's month ordinal (``current``)."""
    current: int | None = None


def qtd_start_month(today: str | None) -> int | None:
    """Month ordinal of the first month of the current calendar quarter (None without a date)."""
    try:
        from datetime import date as _date
        from utils.chart_evidence_audit import _calendar_quarter_months, _month_ordinal
        day = _date.fromisoformat(str(today)[:10])
    except (ValueError, TypeError, ImportError):
        return None
    start = _QtdStart(_calendar_quarter_months((day.month - 1) // 3 + 1, day.year)[0])
    start.current = _month_ordinal(day.year, day.month)
    return start


def _is_qtd(text: str, qtd_start: int | None) -> bool:
    """Every period named starts inside the CURRENT quarter (Stage J: later periods are forecasts, not QTD).

    Stage L: a period that has already ended (a finished month or fortnight, e.g. "16-31 Oct 2026" on
    10 Nov) is complete data, not "QTD, partial"; only a period still running today is partial."""
    if not qtd_start:
        return False
    from utils.chart_evidence_audit import _period_ranges
    ranges = _period_ranges(text)
    if not (bool(ranges) and all(qtd_start <= start < qtd_start + 3 for start, _end, _label in ranges)):
        return False
    current = getattr(qtd_start, "current", None)
    return current is None or any(end >= current for _start, end, _label in ranges)


def _is_future(text: str, qtd_start: int | None) -> bool:
    """Every period named starts after the current quarter: the data can only be a forecast or target."""
    if not qtd_start:
        return False
    from utils.chart_evidence_audit import _period_ranges
    ranges = _period_ranges(text)
    return bool(ranges) and all(start >= qtd_start + 3 for start, _end, _label in ranges)


def charts_from_passage(passage, chart_index: int, qtd_start: int | None = None, *,
                        _only_cols: list[int] | None = None, _only_rows: set[str] | None = None,
                        _qtd: bool = False, _future: bool = False) -> list[PlannedChart]:
    """0–2 charts from one table passage (the best numeric view of the table).

    Current-quarter-to-date data (``qtd_start``) is valid evidence but partial: a
    table that mixes completed periods with QTD periods (columns or rows) becomes
    two charts, the QTD one titled "(QTD, partial)".
    """
    lines = [ln for ln in str(passage.text or "").splitlines() if " | " in ln]
    if len(lines) < 3:
        return []
    heading_line = next((ln for ln in str(passage.text).splitlines() if ln.strip() and " | " not in ln), "")
    heading = re.sub(r"^TABLE \d+\b\s*(?:\(continued\))?\s*(?:[—-]\s*)?", "", heading_line.strip()).replace("(continued)", "").strip()
    header = _split_row(lines[0])
    rows = [_split_row(ln) for ln in lines[1:]]
    rows = [r for r in rows if len(r) == len(header) and r[0]]
    if len(rows) < 2 or len(header) < 2:
        return []
    if any(header_is_data(h) for h in header[1:]):
        return []  # the first row is data, not a header: metric names are unknown
    rows, _mixed_note = homogeneous_rows(rows)
    # Stage L: a Total / aggregate row is not plotted beside its components; the chart keeps the
    # components (it used to be rejected as a whole: "a Total row is plotted with its components").
    from utils.chart_lint import TOTAL_RE
    if sum(1 for r in rows if not TOTAL_RE.search(r[0] if r else "")) >= 3 and not re.search(
            r"(?i)total|overall|aggregate", (header[0] if header else "")):
        rows = [r for r in rows if not TOTAL_RE.search(r[0] if r else "") and not re.match(r"(?i)^\s*total\b", r[0] or "")]
    # Stage L: yearly rows and monthly rows of one table are two different charts (a year bar next to
    # a month bar reads as a crash or a spike); each granularity with >= 3 rows gets its own chart.
    if _only_rows is None and _only_cols is None and not _qtd and not _future:
        groups: dict[str, set] = {}
        for r in rows:
            groups.setdefault(period_granularity(r[0]), set()).add(r[0])
        timed = {g: labels for g, labels in groups.items() if g}
        if len(timed) >= 2 and not groups.get(""):
            charts = []
            for g in ("year", "half", "quarter", "month"):
                if len(timed.get(g, ())) >= 3:
                    charts += charts_from_passage(passage, chart_index + len(charts), qtd_start, _only_rows=timed[g])
            return charts
    if _only_rows is not None:
        rows = [r for r in rows if r[0] in _only_rows]
    if len(rows) < 2:
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
    if _only_cols is not None:
        numeric_cols = [c for c in numeric_cols if c in _only_cols]
    if not numeric_cols:
        return []
    if qtd_start and not _qtd and not _future and _only_cols is None and _only_rows is None:
        # future periods (forecasts / targets) are never mixed with observed data
        future_rows = {r[0] for r in rows if _is_future(r[0], qtd_start)}
        past_rows = {r[0] for r in rows if r[0] not in future_rows}
        if future_rows and past_rows:
            return (charts_from_passage(passage, chart_index, qtd_start, _only_rows=past_rows)
                    + (charts_from_passage(passage, chart_index, qtd_start, _only_rows=future_rows, _future=True)
                       if len(future_rows) >= 2 else []))
        qtd_cols = [c for c in numeric_cols if _is_qtd(header[c], qtd_start)]
        done_cols = [c for c in numeric_cols if c not in qtd_cols]
        if qtd_cols and done_cols:
            return (charts_from_passage(passage, chart_index, qtd_start, _only_cols=done_cols)
                    + charts_from_passage(passage, chart_index, qtd_start, _only_cols=qtd_cols, _qtd=True))
        qtd_rows = {r[0] for r in rows if _is_qtd(r[0], qtd_start)}
        done_rows = {r[0] for r in rows if r[0] not in qtd_rows}
        if len(qtd_rows) >= 2 and len(done_rows) >= 2:
            return (charts_from_passage(passage, chart_index, qtd_start, _only_rows=done_rows)
                    + charts_from_passage(passage, chart_index, qtd_start, _only_rows=qtd_rows, _qtd=True))

    def unit_of(cols: list[int]) -> str | None:
        """Unit shared by the plotted columns; None when they disagree (not one chart)."""
        merged = set().union(*(col_units[c] for c in cols)) if cols else set()
        if len(merged) > 1:
            return None
        return next(iter(merged)) if merged else ""

    period_cols = [c for c in numeric_cols if _PERIOD_RE.match(header[c])]
    if len(period_cols) >= 2 and unit_of(period_cols) is None:
        period_cols = []
    # Stage L: rows are periods and several columns share a unit ("FPI Flows Secondary", "FPI Flows
    # Primary" by year): one series per column over time - the comparison the table exists for.
    from utils.chart_lint import is_time_axis
    if len(period_cols) < 2 and is_time_axis([row_period_label(r[0]) for r in rows]):
        cols = [c for c in numeric_cols if unit_of([c]) is not None and not non_comparable_metric(header[c])
                and not re.search(r"(?i)\b(?:total|overall|aggregate|net)\b", header[c])]
        cols = related_columns(cols, header, rows)
        if len(cols) >= 2 and unit_of(cols) is not None:
            cols = cols[:MAX_COLUMN_SERIES]
            unit = unit_of(cols) or ""
            if not unit:
                hdr_unit = _ROW_UNIT_RE.search(rows[0][0] or "")
                unit = hdr_unit.group(0).strip(" ()") if hdr_unit else ""
            evidence_rows = {r[0]: " | ".join(r) for r in rows}
            series, points = [], []
            for c in cols:
                data = []
                for r in rows:
                    parsed = _parse_number(r[c])
                    if parsed:
                        data.append({"label": row_period_label(r[0]), "value": parsed[0], "evidenceSource": passage.source_id,
                                     "evidenceQuote": evidence_rows.get(r[0], ""), "evidenceValue": r[c]})
                        points.append((header[c], row_period_label(r[0]), parsed[0]))
                if len(data) >= 3:
                    series.append({"name": clean_label(header[c]), "data": data})
            # the shared words become the metric; series keep only what tells them apart
            _metric = series_metric([s_["name"] for s_ in series]).split(":")[0] if series else ""
            if _metric and ":" in series_metric([s_["name"] for s_ in series]):
                for s_ in series:
                    rest = s_["name"][len(_metric):].strip()
                    s_["name"] = rest[:1].upper() + rest[1:] if rest else s_["name"]
            if len(series) >= 2:
                labels = [row_period_label(r[0]) for r in rows]
                title = data_title(metric=(_metric or series_metric([s_["name"] for s_ in series])), labels=labels[:MAX_BAR_POINTS],
                                   period=f"{labels[0]} to {labels[-1]}", unit=unit)
                if _qtd:
                    title = f"{title} ({QTD_LABEL})"
                spec = {"type": "line" if len(rows) >= 6 else "grouped_bar", "title": title, "xLabel": "Period",
                        "yLabel": unit or "Value", "unit": unit, "series": series,
                        "metric": _metric or " vs ".join(s_["name"] for s_ in series),
                        "source": [passage.source_id], "sourceIds": [passage.source_id],
                        "evidenceBasis": "calculated" if passage.kind == "calculated" else "reported",
                        "basisNote": f"Values copied from evidence passage {passage.passage_id}."}
                passage_ids = list(getattr(passage, "merged_ids", None) or [passage.passage_id])
                return [PlannedChart(chart_id=f"c{chart_index}{'q' if _qtd else ''}", spec=spec, passage_ids=passage_ids,
                                     source_ids=[passage.source_id], points=points,
                                     match_text=" ".join([title, header[0], *labels[:20], *[header[c] for c in cols]]),
                                     heading=heading)]
    if len(period_cols) >= 2:
        unit = unit_of(period_cols) or ""
    else:
        # Price / index-level columns of different instruments are not comparable: plot the first comparable column.
        # Counts of constituents ("Stocks", "No. of companies") are not a metric to chart either.
        single = next((c for c in numeric_cols if unit_of([c]) is not None and not non_comparable_metric(header[c])
                       and not re.match(r"(?i)^\s*(?:stocks?|constituents?|count|no\.?\s*of\b|number of\b|#|companies|members|rank)",
                                        header[c])), None)
        if single is None:
            return []
        numeric_cols = [single] + [c for c in numeric_cols if c != single]
        unit = unit_of([single]) or ""
    # The title comes from the chart's own data (metric, entities, period), never
    # from the page heading, which can name a different subject than the table.
    labels = [r[0] for r in rows]
    if len(period_cols) >= 2:
        metric_header = header[0] if header[0] and not re.fullmatch(
            r"(?i)sectors?|index|indices|name|company|stock|item|particulars|industry", header[0]) else ""
        title = data_title(metric=metric_header or (f"Values ({unit})" if unit else "Values"),
                           labels=labels[:MAX_LINE_SERIES], periods=[header[c] for c in period_cols])
    else:
        metric = header[numeric_cols[0]]
        # Stage N: "Year | India" - the column names the entity; the table's own caption names the measure
        # (titles were "India: 2021, 2022, 2023 and 2 others" and every such chart looked like a duplicate).
        from utils.chart_titles import caption_metric, looks_like_entity
        entity_metric = caption_metric(heading) if (looks_like_entity(metric) and heading and is_time_axis(
            [row_period_label(r[0]) for r in rows])) else ""
        if entity_metric:
            shown = [row_period_label(r[0]) for r in rows]
            title = data_title(metric=entity_metric, labels=[metric], period=f"{shown[0]} to {shown[-1]}",
                               unit="" if unit and unit in entity_metric else unit)
        else:
            title = data_title(metric=metric, labels=labels[:MAX_BAR_POINTS],
                               period=period_of([metric], heading, labels), unit=unit)
    plotted_periods = [header[c] for c in (period_cols if len(period_cols) >= 2 else numeric_cols[:1])]
    if _future or (qtd_start and plotted_periods and all(_is_future(h, qtd_start) for h in plotted_periods)):
        title = f"{title} (forecast)"
    elif _qtd or (qtd_start and (_is_qtd(title, qtd_start) or all(_is_qtd(h, qtd_start) for h in plotted_periods))):
        title = f"{title} ({QTD_LABEL})"
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
                shown = row_period_label(r[0])
                data.append(point(shown, shown, parsed[0], r[col], r[0]))
                points.append((header[col], shown, parsed[0]))
        spec = {"type": "bar", "title": title, "xLabel": header[0] or "Item",
                "yLabel": header[col] or unit or "Value", "unit": unit, "series": [{"name": header[col] or "Value", "data": data}]}
        if entity_metric:
            spec.update({"metric": entity_metric, "yLabel": unit or entity_metric})
    spec.update({"source": [passage.source_id], "sourceIds": [passage.source_id], "evidenceBasis":
                 "calculated" if passage.kind == "calculated" else "reported",
                 "basisNote": f"Values copied from evidence passage {passage.passage_id}."})
    passage_ids = list(getattr(passage, "merged_ids", None) or [passage.passage_id])
    if len(passage_ids) > 1:
        spec["basisNote"] = f"Values copied from evidence passages {', '.join(passage_ids)}."
    chart = PlannedChart(chart_id=f"c{chart_index}{'q' if _qtd else ''}", spec=spec, passage_ids=passage_ids,
                         source_ids=[passage.source_id], points=points,
                         # Placement uses the data only: the page heading can be about something else.
                         match_text=" ".join([title, header[0], *[r[0] for r in rows[:20]], *header[1:]]),
                         heading=heading)
    return [chart]


def _table_header(passage) -> str:
    lines = [ln for ln in str(passage.text or "").splitlines() if " | " in ln]
    return lines[0].strip() if lines else ""


def merge_continuations(passages: list) -> list:
    """Join consecutive table passages of one source that share a header row.

    Long tables are split across passages ("TABLE 5 (continued)"); charting each
    piece gives several near-identical charts with the same title. The merged
    passage keeps every piece's id as evidence.
    """
    out: list = []
    for passage in passages:
        header = _table_header(passage)
        prev = out[-1] if out else None
        if (prev is not None and header and passage.kind == prev.kind and passage.source_id == prev.source_id
                and _table_header(prev) == header):
            rows = [ln for ln in str(passage.text).splitlines() if " | " in ln][1:]
            merged = SimpleNamespace(
                passage_id=prev.passage_id, source_id=prev.source_id, kind=prev.kind,
                text=str(prev.text).rstrip() + "\n" + "\n".join(rows), reliability=prev.reliability,
                publisher=getattr(prev, "publisher", ""),
                merged_ids=list(getattr(prev, "merged_ids", None) or [prev.passage_id]) + [passage.passage_id])
            out[-1] = merged
            continue
        out.append(passage)
    return out


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
    # The page heading ("Sectoral Indices performance in April, 2024") dates a table whose
    # data title no longer carries the year.
    labels = " ".join([str(chart.spec.get("title") or ""), chart.heading, *[str(a) for a, _, _ in chart.points],
                       *[str(b) for _, b, _ in chart.points]])
    years = {int(y) for y in re.findall(r"\b((?:19|20)\d{2})\b", labels)}
    years |= {2000 + int(y) for y in re.findall(r"\bFY\s*'?(\d{2})\b", labels, flags=re.I)}
    if years and max(years) < min_year:
        return f"outside the requested window (latest period {max(years)}, window starts {min_year})"
    return ""


def _period_end_year(label: str) -> int | None:
    """The year a period label ends in: "2023" -> 2023, "FY 2014-15" -> 2015, "FY24" -> 2024, else None."""
    text = str(label or "")
    m = re.search(r"\b((?:19|20)\d{2})\s*[-–/]\s*(\d{2})(?!\d)", text)
    if m:
        return int(m.group(1)[:2] + m.group(2))
    years = re.findall(r"\b((?:19|20)\d{2})\b", text)
    if years:
        return int(years[-1])
    m = re.search(r"(?i)\bFY\s*'?(\d{2})\b", text)
    return 2000 + int(m.group(1)) if m else None


def clip_to_window(chart: PlannedChart, min_year: int | None) -> str:
    """Stage N: a time series that starts before the requested window keeps its in-window periods (>= 3)
    instead of failing the window audit as a whole; '' when kept (possibly clipped), else the drop reason."""
    if not min_year or chart.spec.get("type") not in {"line", "grouped_bar", "bar"}:
        return ""
    series = chart.spec.get("series") or []
    labels = [str(d.get("label")) for s in series for d in s.get("data") or []]
    ends = {lab: _period_end_year(lab) for lab in labels}
    if not labels or any(v is None for v in ends.values()):
        return ""  # not a time axis (or not every label is a dated period): nothing to clip
    old = {lab for lab, y in ends.items() if y < min_year}
    if not old:
        return ""
    for s in series:
        s["data"] = [d for d in s.get("data") or [] if str(d.get("label")) not in old]
    series = [s for s in series if len(s.get("data") or []) >= 3]
    if not series:
        return f"fewer than 3 periods inside the requested window (from {min_year})"
    chart.spec["series"] = series
    chart.points = [(a, b, v) for a, b, v in chart.points if a not in old and b not in old]
    title = str(chart.spec.get("title") or "")
    if any(lab in title for lab in old):  # the title listed the clipped periods: rebuild it from what is plotted
        kept = [str(d.get("label")) for d in series[0]["data"]]
        chart.spec["title"] = f"{title.split(':', 1)[0].strip()}: {kept[0]} to {kept[-1]}"
    chart.spec["basisNote"] = (str(chart.spec.get("basisNote") or "") +
                               f" Periods before {min_year} are left out (outside the requested window).").strip()
    return ""


def build_chart_candidates(store, *, max_charts: int = 40, min_year: int | None = None,
                           min_reliability: float = 0.5, today: str | None = None) -> tuple[list[PlannedChart], list[dict]]:
    """Charts from every table/calculated passage; returns (verified charts, dropped log).

    Dropped before writing: unverifiable data, periods entirely before the
    requested window, and data from sources below ``min_reliability``.
    """
    charts, dropped, seen, titles = [], [], set(), {}
    for passage in merge_continuations([p for p in store.passages if p.kind in {"table", "calculated"}]):
        for chart in charts_from_passage(passage, len(charts) + len(dropped) + 1, qtd_start_month(today)):
            signature = tuple(sorted((a, b, round(v, 4)) for a, b, v in chart.points))
            if signature in seen:
                continue
            seen.add(signature)
            reason = verify_chart(chart, passage.text) or _outside_window(chart, min_year)
            if not reason:
                reason = clip_to_window(chart, min_year)
            if not reason and passage.kind != "calculated" and passage.reliability < min_reliability:
                reason = f"source reliability {passage.reliability:.2f} below {min_reliability:.2f}"
            # Stage N: a repeated title is a duplicate only when the values repeat too; otherwise it is another
            # subject that happens to share a generic title (unique_titles tells them apart later).
            values = {round(v, 4) for _, _, v in chart.points}
            earlier = titles.get(str(chart.spec.get("title") or "").casefold(), [])
            if not reason and any(len(values & e) >= 0.6 * max(1, min(len(values), len(e))) for e in earlier):
                reason = "same title and values as an earlier chart (duplicate view)"
            if reason:
                chart.status, chart.drop_reason = "dropped", reason
                dropped.append({"chart": chart.chart_id, "title": chart.spec.get("title"), "passage": passage.passage_id, "reason": reason})
                continue
            charts.append(chart)
            titles.setdefault(str(chart.spec.get("title") or "").casefold(), []).append(
                {round(v, 4) for _, _, v in chart.points})
            if len(charts) >= max_charts:
                return charts, dropped
    return charts, dropped


def _terms(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]{3,}", str(text or "").casefold())}


def assign_charts(charts: list[PlannedChart], sections: list, *, per_section: int = 2) -> None:
    """Give each chart to the section whose queries/passages fit it best (passage overlap first).

    Charts already assigned (fetched-dataset charts, utils.dataset_charts.preassign) keep their
    section and count towards its limit."""
    load: dict[str, int] = {}
    for chart in charts:
        if chart.section_id:
            load[chart.section_id] = load.get(chart.section_id, 0) + 1
    ranked = []
    for chart in charts:
        if chart.section_id:
            continue
        best = None
        plotted = label_entities([a for a, _, _ in chart.points] + [b for _, b, _ in chart.points])
        for section in sections:
            # A sector deep-dive only takes charts that plot that sector.
            if (getattr(section, "role", "") == "deep_dive" and getattr(section, "entity", "")
                    and section.entity not in plotted):
                continue
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
