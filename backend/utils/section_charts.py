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

# Stage N: units of any domain ("67.7 yrs", "12.5 GW", "1,234 MW", "€ 40 bn"), not only market units
_NUM_CELL_RE = re.compile(
    r"^\(?\s*([+\-\u2212\u2013]?)\s*"
    r"(?:(US\$|USD|INR|EUR|GBP|JPY|Rs\.?|rupees?|dollars?|euros?|pounds?|yen|\$|\u20b9|\u20ac|\u00a3|\u00a5)\s*)?"
    r"([\d,]*\.?\d+)\s*"
    r"(million\s+tonnes?|billion\s+tonnes?|thousand\s+tonnes?|"
    r"%|x|pts|bps|cr|crore|bn|mn|lakh|million|billion|trillion|thousands?|yrs?|years?|"
    r"gw|mw|kw|gwh|mwh|twh|kwh|bu|tonnes?|metric\s+tons?|mt|kt|kg|km|ha|\u2030|[BMKT])?\s*\)?$", re.I)
# "$462.6B", "1.2T": a capital magnitude letter right after the number (a lower-case "m"/"k" is a unit, not a scale)
_MAGNITUDE_LETTER = {"B": "billion", "M": "million", "K": "thousand", "T": "trillion"}
_HEADER_UNIT_RE = re.compile(
    r"(?i)(?<![a-z0-9])((?:(?:US\$|USD|INR|EUR|GBP|JPY|Rs\.?|rupees?|dollars?|euros?|pounds?|yen|\$|\u20b9|\u20ac|\u00a3|\u00a5)\s*"
    r"(?:lakh\s+crores?|lakh\s+crs?\.?|crores?|crs?\.?|lakhs?|million|billion|trillion|thousand|bn|mn|cr|lakh|m|b|t|k)?)|"
    r"(?:million|billion|thousand)\s+tonnes?|%|per\s?cent|x|pts|bps|cr|crore|bn|mn|lakh|million|"
    r"billion|trillion|thousands?|yrs?|years?|gwh|mwh|twh|kwh|gw|mw|kw|bu|tonnes?|metric\s+tons?|"
    r"mt|kt|kg|km|ha|\u2030)(?![a-z0-9])")
_PERIOD_RE = re.compile(
    r"^(?:Q[1-4](?:\s*FY\s*\d{2,4}|\s*CY?\s*\d{2,4}|\s*\d{4})?|FY\s*\d{2,4}(?:-\d{2,4})?|(?:19|20)\d{2}(?:-\d{2})?|"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*'?\d{2,4}|H[12]\s*FY\s*\d{2,4})$", re.I)
MAX_BAR_POINTS = 15
MAX_LINE_SERIES = 8
MAX_COLUMN_SERIES = 4
_ROW_UNIT_RE = re.compile(r"\s*\((?:₹|rs\.?|inr|us\$|\$|€|£)?\s*(?:crore|cr|lakh|million|mn|billion|bn|trillion|thousand|'000)\b[^)]*\)", re.I)
_MONTH_WORD = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*"
_YEAR_ATTRIBUTE_RE = re.compile(
    r"(?i)^\s*(?:(?:latest|most recent|current|first|last|reported|available|observed)\s+)?"
    r"(?:year|date|period)(?:\s+(?:available|reported|observed))?\s*$")
_SCHEMA_FIELD_RE = re.compile(
    r"(?i)(?:\b(?:id|identifier|code|status|flag|published|active)\b|_(?:id|identifier|code|status|flag)(?:_|$))")
_MACHINE_ID_RE = re.compile(r"(?i)^(?=.*[a-z])(?=.*\d)[a-z0-9_-]{9,}$")


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
        if self.spec.get("storyPoint"):
            # The storyline point this figure proves: the paragraph around the figure makes that point with it.
            head += f"\n  PROVES THE REPORT'S POINT: {self.spec['storyPoint']}"
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
    raw = str(cell or "").strip().replace("\u2212", "-")
    match = _NUM_CELL_RE.match(raw)
    if not match:
        return None
    sign, currency, digits, unit = match.groups()
    if unit and len(unit) == 1 and unit.upper() in _MAGNITUDE_LETTER:
        if unit != unit.upper():
            return None  # "5m", "3k": metres / a lower-case label, not a magnitude
        unit = _MAGNITUDE_LETTER[unit]
    try:
        value = float(digits.replace(",", ""))
    except ValueError:
        return None
    if sign in {"-", "\u2212", "\u2013"} or (raw.startswith("(") and raw.endswith(")")):
        value = -value
    if not math.isfinite(value):
        return None
    return value, _canonical_unit(" ".join(part for part in (currency or "", unit or "") if part))


def _canonical_unit(unit: str) -> str:
    """Canonical display form for units read from a cell or column header."""
    value = re.sub(r"\s+", " ", str(unit or "")).strip().casefold()
    currencies = {
        "us$": "USD", "$": "USD", "usd": "USD", "dollar": "USD", "dollars": "USD",
        "inr": "INR", "rs": "INR", "rupee": "INR", "rupees": "INR", "\u20b9": "INR",
        "eur": "EUR", "euro": "EUR", "euros": "EUR", "\u20ac": "EUR",
        "gbp": "GBP", "pound": "GBP", "pounds": "GBP", "\u00a3": "GBP",
        "jpy": "JPY", "yen": "JPY", "\u00a5": "JPY",
    }
    parts = value.split()
    if parts and parts[0].rstrip(".") in currencies:
        currency = currencies[parts[0].rstrip(".")]
        scale = " ".join(parts[1:])
        scale = {
            "cr": "crore", "crores": "crore", "bn": "billion", "mn": "million",
            "lakhs": "lakh", "thousands": "thousand",
        }.get(scale, scale)
        return f"{currency} {scale}".strip()
    aliases = {
        "cr": "crore", "crore": "crore", "bn": "billion", "billion": "billion",
        "mn": "million", "million": "million", "yr": "years", "yrs": "years",
        "year": "years", "years": "years", "tonne": "tonnes", "tonnes": "tonnes",
        "metric ton": "tonnes", "metric tons": "tonnes", "thousands": "thousand",
        "million tonnes": "million tonnes", "billion tonnes": "billion tonnes",
        "thousand tonnes": "thousand tonnes",
        "pts": "points", "point": "points", "points": "points", "kw": "kW", "mw": "MW",
        "gw": "GW", "kwh": "kWh", "mwh": "MWh", "gwh": "GWh", "twh": "TWh",
        "bu": "BU", "mt": "Mt", "kt": "kt", "kg": "kg", "km": "km", "ha": "ha",
        "bps": "bps", "lakh": "lakh", "trillion": "trillion", "x": "x", "%": "%",
        "\u2030": "\u2030", "per cent": "%",
    }
    return aliases.get(value, value)


_YEAR_PREFIX_RE = re.compile(r"(?i)^\s*(?:calendar|financial|fiscal)\s+year\s*[:\-]?\s*")


def _period_header(header: str) -> str:
    """A column named for a period: "Calendar Year 2022" / "Financial Year 2023-24" is the period itself."""
    return _YEAR_PREFIX_RE.sub("", str(header or "")).strip()


def _header_units(header: str) -> set[str]:
    """Explicit units in column labels, including units absent from the cell values."""
    # "Calendar Year 2022" names a period; its "Year" is not a unit of the values.
    # "1-Year Returns" / "3Yr Change %" name a horizon; the values are not in years.
    text = re.sub(r"(?i)\b(?:calendar|financial|fiscal|academic)\s+years?\b|\byears?\s+(?=(?:19|20)\d{2}\b)"
                  r"|\b\d+\s*-?\s*(?:years?|yrs?|months?|weeks?|days?)\b", " ", str(header or ""))
    return {_canonical_unit(unit) for unit in _HEADER_UNIT_RE.findall(text)}


def _strip_header_units(header: str) -> str:
    """Remove a parenthetical unit from a legend label after recording it on the axis."""
    return re.sub(r"\s*\(([^()]*)\)", lambda match: "" if _header_units(match.group(1)) else match.group(0),
                  str(header or "")).strip()


def _heading_unit(heading: str) -> str:
    """Read one unambiguous explicit unit from a table caption."""
    text = str(heading or "")
    magnitude = re.search(r"(?i)\(\s*['\u2019]?000(?:\s+[^)]*)?\s*\)|\bthousands?\b", text)
    if magnitude:
        return "thousands"
    if re.search(r"(?i)\bpercent(?:age)?\b", text) and not _header_units(text):
        return "%"
    units = _header_units(text)
    return next(iter(units)) if len(units) == 1 else ""


def _header_measure_key(value: str) -> str:
    """Normalize a table-header label without dropping measure-specific words."""
    text = _HEADER_UNIT_RE.sub(" ", str(value or ""))
    text = re.sub(r"\([^)]*\)", " ", text)
    text = re.sub(r"(?i)\b(?:capacity|installed|volume|amount|value|generation|production|output|"
                  r"estimate|observed|reported|total|number|data)\b", " ", text)
    return " ".join(re.findall(r"[a-z0-9]+", text.casefold()))


def _parent_header_unit(parent_context: str, column_header: str) -> str:
    """Inherit a unit only from an exact, unambiguous parent header or caption."""
    column_key = _header_measure_key(column_header)
    if not column_key:
        return ""
    context_lines = [line.strip() for line in str(parent_context or "").splitlines() if line.strip()]
    exact_units: set[str] = set()
    context_units: set[str] = set()
    for line in context_lines:
        context_units.update(_header_units(line))
        if "|" not in line:
            continue
        for cell in _split_row(line):
            cell_units = _header_units(cell)
            if _header_measure_key(cell) == column_key and len(cell_units) == 1:
                exact_units.update(cell_units)
    if len(exact_units) == 1:
        return next(iter(exact_units))
    if exact_units:
        return ""
    return next(iter(context_units)) if len(context_units) == 1 else ""


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


_LONG_PERIOD_HEADER_RE = re.compile(r"(?i)^\s*(?:year|period|date|fiscal[ _]year|fy|time)\s*$")
_LONG_CODE_HEADER_RE = re.compile(r"(?i)^\s*(?:code|iso(?:3|[ _-]?code)?|country[ _]code|id)\s*$")


def _charts_from_long_table(passage, chart_index: int, qtd_start, header: list[str], rows: list[list[str]],
                            heading: str, focus: list[str] | None = None) -> list[PlannedChart] | None:
    """A long-format table ("Entity | Code | Year | Value", one row per entity and year) as a time series.

    The wide builder keyed rows by their first cell, so repeated entities overwrote each other: the
    internet-use chart plotted each country's 2015 value with its 2024 row as the quote (rejected). The table
    is pivoted to Year x entity, charted by the usual builder, and every point quotes its own source row.
    Returns None when the table is not long-format."""
    period_col = next((i for i, h in enumerate(header) if i > 0 and _LONG_PERIOD_HEADER_RE.match(h)), None)
    if period_col is None or len(rows) < 6:
        return None
    entities = list(dict.fromkeys(r[0] for r in rows))
    if len(entities) < 2 or len(entities) == len(rows) or any(_PERIOD_RE.match(e.strip()) for e in entities):
        return None
    measures = [i for i in range(1, len(header)) if i != period_col and not _LONG_CODE_HEADER_RE.match(header[i])
                and sum(bool(_parse_number(r[i])) for r in rows) >= 0.7 * len(rows)]
    if len(measures) != 1:
        return None  # several measures per row: no single series to pivot safely
    measure = measures[0]
    if focus:
        wanted = {str(f).casefold() for f in focus}
        named = [e for e in entities if e.casefold() in wanted]
        if named:
            entities = named  # the question's countries, not the first eight of the table
    entities = entities[:MAX_LINE_SERIES]
    by_key: dict[tuple[str, str], list[str]] = {}
    periods: list[str] = []
    for r in rows:
        if r[0] in entities and _parse_number(r[measure]):
            by_key.setdefault((r[0], r[period_col]), r)
            periods.append(r[period_col])
    periods = list(dict.fromkeys(periods))
    if len(periods) < 3:
        return None
    wide = [" | ".join([header[period_col], *entities])]
    for p in periods:
        wide.append(" | ".join([p, *[(by_key[(e, p)][measure] if (e, p) in by_key else "") for e in entities]]))
    import copy
    shadow = copy.copy(passage)
    shadow.text = f"TABLE 1 — {clean_label(header[measure])}\n" + "\n".join(wide)
    # The entity columns are compared as they are (as for focus entities): related_columns() keeps headers that
    # share words, and country names share none.
    charts = charts_from_passage(shadow, chart_index, qtd_start, _only_cols=list(range(1, len(entities) + 1)))
    kept = []
    for chart in charts:
        ok = True
        for series in chart.spec.get("series") or []:
            for point in series.get("data") or []:
                entity, period = str(point.get("evidenceColumn") or ""), str(point.get("evidenceRowLabel") or "")
                source_row = by_key.get((entity, period))
                if source_row is None:
                    ok = False
                    continue
                point.update({"evidenceQuote": " | ".join(source_row), "evidenceValue": source_row[measure],
                              "evidenceColumn": header[measure], "evidenceEntity": entity,
                              "evidenceRowLabel": entity, "evidencePeriod": point.get("evidencePeriod") or period})
        if ok:
            chart.passage_ids = list(getattr(passage, "merged_ids", None) or [passage.passage_id])
            kept.append(chart)
    return kept


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
    from utils.fact_sheet import is_column_numbering, is_schema_table
    if is_schema_table(header):
        return []
    rows = [r for r in rows if len(r) == len(header) and r[0] and not is_column_numbering(r)]
    if len(rows) < 2 or len(header) < 2:
        return []
    if any(header_is_data(h) for h in header[1:]):
        return []  # the first row is data, not a header: metric names are unknown
    if _only_rows is None and _only_cols is None and not _qtd and not _future:
        long_charts = _charts_from_long_table(passage, chart_index, qtd_start, header, rows, heading)
        if long_charts is not None:
            return long_charts
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
    parent_source_id = str(getattr(passage, "parent_source_id", "") or "").strip().upper()
    parent_context = str(getattr(passage, "parent_data_context", "") or "")
    parent_unit_columns: dict[int, str] = {}
    for col in range(1, len(header)):
        parsed = [_parse_number(r[col]) for r in rows]
        good = [p for p in parsed if p]
        if len(good) >= max(2, int(0.7 * len(rows))):
            numeric_cols.append(col)
            units = {_canonical_unit(u) for _, u in good if u}
            units.update(_header_units(header[col]))
            if not units and parent_source_id and parent_context:
                inherited_unit = _parent_header_unit(parent_context, header[col])
                if inherited_unit:
                    units.add(inherited_unit)
                    parent_unit_columns[col] = inherited_unit
            if re.search(r"(?i)\breturns?\b.*\(%\)", header[col]):
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

    def add_unit_provenance(spec: dict, cols: list[int]) -> list[str]:
        ids = [passage.source_id]
        if parent_source_id and any(col in parent_unit_columns for col in cols):
            ids.append(parent_source_id)
            spec["unitEvidenceSource"] = parent_source_id
        spec["source"] = ids
        spec["sourceIds"] = ids
        return ids

    period_cols = [c for c in numeric_cols if _PERIOD_RE.match(_period_header(header[c]))]
    if len(period_cols) >= 2 and unit_of(period_cols) is None:
        period_cols = []
    cross_section_cols = []
    if len(period_cols) < 2 and _only_cols is not None:
        selected = [column for column in numeric_cols if column in _only_cols]
        from utils.chart_titles import looks_like_entity
        if (len(selected) >= 2 and all(looks_like_entity(header[column]) for column in selected)
                and unit_of(selected) is not None):
            cross_section_cols = selected[:MAX_COLUMN_SERIES]
    # Stage L: rows are periods and several columns share a unit ("FPI Flows Secondary", "FPI Flows
    # Primary" by year): one series per column over time - the comparison the table exists for.
    from utils.chart_lint import is_time_axis
    row_periods = [row_period_label(r[0]) for r in rows]
    if _only_cols is None and len(numeric_cols) > 1 and is_time_axis(row_periods):
        # A wide time-series table can carry several measures with different units in its headers
        # (for example, capacity in GW beside market share in %). Keep those measures in separate
        # charts instead of inheriting one unit for every plotted column.
        columns_by_unit: dict[str, list[int]] = {}
        for column in numeric_cols:
            column_unit = unit_of([column])
            if column_unit is not None:
                columns_by_unit.setdefault(column_unit, []).append(column)
        if len(columns_by_unit) > 1:
            split_charts: list[PlannedChart] = []
            for columns in columns_by_unit.values():
                split_charts.extend(charts_from_passage(
                    passage, chart_index + len(split_charts), qtd_start, _only_cols=columns,
                    _only_rows=_only_rows, _qtd=_qtd, _future=_future))
            if split_charts:
                return split_charts
    if len(period_cols) < 2 and is_time_axis(row_periods):
        cols = [c for c in numeric_cols if unit_of([c]) is not None and not non_comparable_metric(header[c])
                and not re.search(r"(?i)\b(?:total|overall|aggregate|net)\b", header[c])]
        # entity columns chosen by the question (utils.table_focus) are compared as they are
        cols = cols if _only_cols is not None else related_columns(cols, header, rows)
        if cols and unit_of(cols) is not None:
            cols = cols[:MAX_COLUMN_SERIES]
            unit = unit_of(cols) or ""
            # Stage N: newest-first source tables are plotted oldest -> newest
            from utils.chart_lint import time_key as _time_key
            try:
                if _time_key(row_period_label(rows[0][0])) > _time_key(row_period_label(rows[-1][0])):
                    rows = list(reversed(rows))
            except Exception:
                pass
            if not unit:
                hdr_unit = _ROW_UNIT_RE.search(rows[0][0] or "")
                unit = hdr_unit.group(0).strip(" ()") if hdr_unit else ""
            evidence_rows = {r[0]: " | ".join(r) for r in rows}
            series, points = [], []
            for c in cols:
                series_name = clean_label(_strip_header_units(header[c])) or clean_label(header[c])
                data = []
                for r in rows:
                    parsed = _parse_number(r[c])
                    if parsed:
                        _period_label = row_period_label(r[0])
                        data.append({"label": row_period_label(r[0]), "value": parsed[0], "evidenceSource": passage.source_id,
                                     "evidenceQuote": evidence_rows.get(r[0], ""), "evidenceValue": r[c],
                                     "evidenceColumn": header[c], "evidenceEntity": series_name,
                                     "evidenceRowLabel": r[0], "evidencePeriod": _period_label})
                        points.append((series_name, _period_label, parsed[0]))
                if len(data) >= 3:
                    series.append({"name": series_name, "data": data})
            # the shared words become the metric; series keep only what tells them apart
            _metric = series_metric([s_["name"] for s_ in series]).split(":")[0] if series else ""
            if _metric and ":" in series_metric([s_["name"] for s_ in series]):
                for s_ in series:
                    rest = s_["name"][len(_metric):].strip()
                    s_["name"] = rest[:1].upper() + rest[1:] if rest else s_["name"]
            if series:
                labels = [row_period_label(r[0]) for r in rows]
                # Stage N: the series are entities (countries, companies): the measure comes from the table's
                # caption, or the page title when the caption is generic ("The data", "Full data table by year")
                from utils.chart_titles import caption_metric, looks_like_entity
                if all(looks_like_entity(_strip_header_units(clean_label(header[c]))) for c in cols):
                    _caption = caption_metric(heading)
                    if not _caption or re.search(r"(?i)^(?:the\s+)?(?:full\s+)?data(?:\s+table)?\b|^table\b|^information$",
                                                 _caption):
                        _caption = caption_metric(re.split(r"\s+[|–—:]\s+|:\s", str(getattr(passage, "title", "") or ""))[0])
                    _caption = re.sub(r"(?i)\s+by\s+country\b.*$", "", _caption).strip()
                    if _caption and not looks_like_entity(_caption) or (_caption and len(_caption.split()) >= 2):
                        title = data_title(metric=_caption, labels=[s_["name"] for s_ in series],
                                           period=f"{labels[0]} to {labels[-1]}", unit=unit)
                        _metric = _caption
                    else:
                        title = data_title(metric=(_metric or series_metric([s_["name"] for s_ in series])),
                                           labels=labels[:MAX_BAR_POINTS], period=f"{labels[0]} to {labels[-1]}", unit=unit)
                else:
                    title = data_title(metric=(_metric or series_metric([s_["name"] for s_ in series])), labels=labels[:MAX_BAR_POINTS],
                                       period=f"{labels[0]} to {labels[-1]}", unit=unit)
                if _qtd:
                    title = f"{title} ({QTD_LABEL})"
                spec = {"type": "line" if len(series) == 1 or len(rows) >= 6 else "grouped_bar", "title": title, "xLabel": "Period",
                        "yLabel": unit or "Value", "unit": unit, "series": series,
                        "metric": _metric or " vs ".join(s_["name"] for s_ in series),
                        "source": [passage.source_id], "sourceIds": [passage.source_id],
                        "evidenceBasis": "calculated" if passage.kind == "calculated" else "reported",
                        "basisNote": f"Values copied from evidence passage {passage.passage_id}."}
                chart_source_ids = add_unit_provenance(spec, cols)
                passage_ids = list(getattr(passage, "merged_ids", None) or [passage.passage_id])
                return [PlannedChart(chart_id=f"c{chart_index}{'q' if _qtd else ''}", spec=spec, passage_ids=passage_ids,
                                     source_ids=chart_source_ids, points=points,
                                     match_text=" ".join([title, header[0], *labels[:20], *[header[c] for c in cols]]),
                                     heading=heading)]
    if len(period_cols) >= 2:
        unit = unit_of(period_cols) or ""
    elif cross_section_cols:
        unit = unit_of(cross_section_cols) or _heading_unit(heading)
    else:
        # Price / index-level columns of different instruments are not comparable: plot the first comparable column.
        # Counts of constituents ("Stocks", "No. of companies") are not a metric to chart either.
        single = next((c for c in numeric_cols if unit_of([c]) is not None and not non_comparable_metric(header[c])
                       and not _YEAR_ATTRIBUTE_RE.fullmatch(header[c].strip())
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
            r"(?i)sectors?|index|indices|name|company|stock|item|particulars|industry|categor(?:y|ies)|segments?|"
            r"types?|states?(?:\s*/\s*uts?)?|description", header[0].strip()) else ""
        title_metric = metric_header
        if not metric_header or re.fullmatch(r"(?i)regions?|country|countries|economy|economies|markets?|areas?|"
                                             r"zones?|entity|entities", metric_header.strip()):
            # The first column names the rows ("Region"), not the measure: the title takes the table's caption
            # or the page title ("Region: World Total, Americas, ..." told the reader nothing). The points keep
            # the header as their declared measure: that is what the source row itself can show.
            from utils.chart_titles import caption_metric
            # A page's name is not a measure: "Press Release Page: 2 Wheelers, ..." gave two different PIB
            # charts one title, and the duplicate check dropped one of them.
            _generic = (r"(?i)^(?:the\s+)?(?:full\s+)?data(?:\s+table)?\b|^table\b|^information$|^all reported values|"
                        r"press\s+(?:release|information)|^home\b|^page\b|^untitled|^report\b|^news\b|^document\b|"
                        r"^pdf\b|^dashboard\b|^statistics?$|^overview$|^summary$")
            title_metric = caption_metric(heading) if heading and not re.search(_generic, heading) else ""
            if not title_metric:
                _page = re.split(r"\s+[|–—:]\s+|:\s", str(getattr(passage, "title", "") or ""))[0]
                title_metric = caption_metric(_page) if _page and not re.search(_generic, _page) else ""
            title_metric = title_metric or metric_header
        title = data_title(metric=title_metric or (f"Values ({unit})" if unit else "Values"),
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

    def point(label: str, col_label: str, value: float, raw: str, row_label: str, *,
              column_label: str = "", metric_label: str = "", entity_label: str = "") -> dict:
        period = (col_label if _PERIOD_RE.match(_period_header(col_label))
                  else (row_period_label(row_label) if _PERIOD_RE.match(row_label) else ""))
        return {"label": col_label if col_label else label, "value": value, "evidenceSource": passage.source_id,
                "evidenceQuote": evidence_rows.get(row_label, ""), "evidenceValue": raw,
                "evidenceColumn": column_label or col_label, "evidenceRowLabel": row_label,
                **({"evidenceMetric": metric_label} if metric_label else {}),
                **({"evidenceEntity": entity_label} if entity_label else {}),
                **({"evidencePeriod": period} if period else {})}

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
                    data.append(point(r[0], header[c], parsed[0], r[c], r[0],
                                      column_label=header[c], metric_label=metric_header,
                                      entity_label=r[0]))
                    points.append((r[0], header[c], parsed[0]))
            if len(data) >= 2:
                series.append({"name": r[0], "data": data})
        spec = {"type": "line" if len(period_cols) >= 3 else "grouped_bar", "title": title, "xLabel": "Period",
                "yLabel": unit or "Value", "unit": unit, "series": series}
    elif cross_section_cols:
        from utils.chart_titles import caption_metric
        heading_parts = re.split(r"\s+-\s+", str(heading or ""), maxsplit=1)
        caption = caption_metric(heading_parts[-1] if len(heading_parts) > 1 else heading)
        metric = re.split(r"(?i)\s+by\s+", caption, maxsplit=1)[0].strip()
        metric = re.sub(r"(?i)\s*\(\s*['’]?000\s*\)", "", metric).strip()
        if not metric or re.fullmatch(r"(?i)(?:table\s*\d+|(?:full\s+)?data(?:\s+table)?|information)", metric):
            metric = clean_label(header[0]) or "Value"
        sheet_context = clean_label(heading_parts[0]) if len(heading_parts) > 1 else ""
        if re.search(r"(?i)\bdeaths?\b", metric) and re.match(r"(?i)^deaths?\b", sheet_context):
            sheet_context = re.sub(r"(?i)^deaths?\s*", "", sheet_context).strip()
        if sheet_context and sheet_context.casefold() not in metric.casefold():
            metric = f"{metric}, {sheet_context}"
        entity_labels = [clean_label(header[column]) for column in cross_section_cols]
        title = data_title(metric=metric, labels=entity_labels, unit=unit)
        series = []
        for column in cross_section_cols:
            data = []
            for row in rows[:MAX_BAR_POINTS]:
                parsed = _parse_number(row[column])
                if not parsed:
                    continue
                label = clean_label(row[0])
                data.append({"label": label, "value": parsed[0], "evidenceSource": passage.source_id,
                             "evidenceQuote": " | ".join(row), "evidenceValue": row[column],
                             "evidenceColumn": header[column], "evidenceRowLabel": row[0],
                             "evidenceCategory": label, "evidenceMetric": metric,
                             "evidenceEntity": clean_label(header[column])})
                points.append((clean_label(header[column]), label, parsed[0]))
            if len(data) >= 3:
                series.append({"name": clean_label(header[column]), "data": data})
        if len(series) < 2:
            return []
        spec = {"type": "grouped_bar", "title": title, "xLabel": header[0] or "Category",
                "yLabel": unit or "Value", "unit": unit, "series": series, "metric": metric,
                "source": [passage.source_id], "sourceIds": [passage.source_id],
                "evidenceBasis": "calculated" if passage.kind == "calculated" else "reported",
                "basisNote": f"Values copied from evidence passage {passage.passage_id}."}
        chart_source_ids = add_unit_provenance(spec, cross_section_cols)
        passage_ids = list(getattr(passage, "merged_ids", None) or [passage.passage_id])
        return [PlannedChart(chart_id=f"c{chart_index}{'q' if _qtd else ''}", spec=spec,
                             passage_ids=passage_ids, source_ids=chart_source_ids, points=points,
                             match_text=" ".join([title, header[0], *[row[0] for row in rows[:20]],
                                                  *[header[column] for column in cross_section_cols]]),
                             heading=heading)]
    else:
        col = numeric_cols[0]
        data = []
        for r in rows[:MAX_BAR_POINTS]:
            parsed = _parse_number(r[col])
            if parsed:
                shown = row_period_label(r[0])
                data.append(point(shown, shown, parsed[0], r[col], r[0],
                                  column_label=header[col], metric_label=entity_metric or header[col],
                                  entity_label=header[col]))
                points.append((header[col], shown, parsed[0]))
        spec = {"type": "bar", "title": title, "xLabel": header[0] or "Item",
                "yLabel": header[col] or unit or "Value", "unit": unit, "series": [{"name": header[col] or "Value", "data": data}]}
        if entity_metric:
            spec.update({"metric": entity_metric, "yLabel": unit or entity_metric})
    plotted_cols = cross_section_cols or (period_cols if len(period_cols) >= 2 else numeric_cols[:1])
    chart_source_ids = add_unit_provenance(spec, plotted_cols)
    spec.update({"evidenceBasis":
                 "calculated" if passage.kind == "calculated" else "reported",
                 "basisNote": f"Values copied from evidence passage {passage.passage_id}."})
    passage_ids = list(getattr(passage, "merged_ids", None) or [passage.passage_id])
    if len(passage_ids) > 1:
        spec["basisNote"] = f"Values copied from evidence passages {', '.join(passage_ids)}."
    chart = PlannedChart(chart_id=f"c{chart_index}{'q' if _qtd else ''}", spec=spec, passage_ids=passage_ids,
                         source_ids=chart_source_ids, points=points,
                         # Placement uses the data only: the page heading can be about something else.
                         match_text=" ".join([title, header[0], *[r[0] for r in rows[:20]], *header[1:]]),
                         heading=heading)
    return [chart]


def _table_header(passage) -> str:
    lines = [ln for ln in str(passage.text or "").splitlines() if " | " in ln]
    return lines[0].strip() if lines else ""


def _table_title_line(passage) -> str:
    return next((line.strip() for line in str(passage.text or "").splitlines()
                 if line.strip() and " | " not in line), "")


def _same_continued_table(previous, current) -> bool:
    current_title = _table_title_line(current)
    if not re.search(r"(?i)\(continued\)", current_title):
        return False

    def key(value: str) -> str:
        value = re.sub(r"(?i)\s*\(continued\)", "", value)
        value = re.sub(r"(?i)^TABLE\s+\d+\b", "", value)
        return re.sub(r"\s+", " ", value).strip(" -:—–").casefold()

    previous_words = key(_table_title_line(previous)).split()
    current_words = key(current_title).split()
    if not previous_words and not current_words:
        # Untitled tables ("TABLE 1" / "TABLE 1 (continued)"): the same table number is the same table.
        number = re.compile(r"(?i)^TABLE\s+(\d+)\b")
        before, after = number.match(_table_title_line(previous)), number.match(current_title)
        return bool(before and after and before.group(1) == after.group(1))
    if not previous_words or not current_words:
        return False
    common = []
    for previous_word, current_word in zip(previous_words, current_words):
        if previous_word != current_word:
            break
        common.append(previous_word)
    return bool(common) and (previous_words == current_words or
                             (len(common) >= 2 and sum(map(len, common)) >= 12))


def merge_continuations(passages: list) -> list:
    """Join explicitly continued tables of one source when they share a header row.

    Long tables are split across passages ("TABLE 5 (continued)"); charting each
    piece gives several near-identical charts with the same title. The merged
    passage keeps every piece's id as evidence. Separate workbook sheets often
    share the same columns, so a matching header alone is not enough to join them.
    """
    out: list = []
    for passage in passages:
        header = _table_header(passage)
        prev = out[-1] if out else None
        if (prev is not None and header and passage.kind == prev.kind and passage.source_id == prev.source_id
                and _table_header(prev) == header and _same_continued_table(prev, passage)):
            rows = [ln for ln in str(passage.text).splitlines() if " | " in ln][1:]
            merged = SimpleNamespace(
                passage_id=prev.passage_id, source_id=prev.source_id, kind=prev.kind,
                text=str(prev.text).rstrip() + "\n" + "\n".join(rows), reliability=prev.reliability,
                publisher=getattr(prev, "publisher", ""), title=getattr(prev, "title", ""),
                url=getattr(prev, "url", ""), published=getattr(prev, "published", ""),
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


def _chart_data_problem(chart: PlannedChart) -> str:
    """Reject flat attributes and code/status catalogs that can parse as numeric charts.

    Structured exports often contain fields such as "latest year" or a Boolean publish-status column.
    Those are useful metadata, but they do not describe a measure to compare. The checks use the field and
    label shapes rather than any publisher's column names.
    """
    values = {round(float(point[2]), 8) for point in chart.points}
    if values and len(values) == 1:
        return "plotted values do not vary"
    if str(chart.spec.get("type") or "") != "bar":
        return ""
    labels = [str(point[1] or "").strip() for point in chart.points]
    if len(labels) < 3:
        return ""
    opaque = sum(bool(_MACHINE_ID_RE.fullmatch(label)) for label in labels) / len(labels)
    fields = [chart.spec.get("xLabel"), chart.spec.get("yLabel"), chart.spec.get("metric")]
    fields.extend(series.get("name") for series in chart.spec.get("series") or [] if isinstance(series, dict))
    schema_field = any(_SCHEMA_FIELD_RE.search(str(field or "")) for field in fields)
    if opaque >= 0.6 and schema_field:
        return "machine-coded labels and identifier/status fields describe a data catalog, not a measure"
    return ""


def _outside_window(chart: PlannedChart, min_year: int | None, max_year: int | None = None) -> str:
    if min_year is None and max_year is None:
        return ""
    point_years = {year for _series, label, _value in chart.points
                   if (year := _period_end_year(str(label))) is not None}
    years = point_years
    if not years:  # the page heading can date a categorical table without dating its points
        labels = " ".join([str(chart.spec.get("title") or ""), chart.heading])
        years = {int(y) for y in re.findall(r"\b((?:19|20)\d{2})\b", labels)}
        years |= {2000 + int(y) for y in re.findall(r"\bFY\s*'?(\d{2})\b", labels, flags=re.I)}
    if years and min_year is not None and max(years) < min_year:
        return f"outside the requested window (latest period {max(years)}, window starts {min_year})"
    if years and max_year is not None and min(years) > max_year:
        return f"outside the requested window (earliest period {min(years)}, window ends {max_year})"
    return ""


def _period_end_year(label: str) -> int | None:
    """The year a period label ends in: "2023" -> 2023, "FY 2014-15" -> 2015, "FY24" -> 2024, else None."""
    text = str(label or "")
    # Indian fiscal quarters Q1-Q3 end in the calendar year before the FY
    # label; Q4 ends in the FY's named year. For example, Q2 FY27 is Jul-Sep
    # 2026, while Q4 FY27 is Jan-Mar 2027.
    quarter_fy_range = re.search(
        r"(?i)\bQ([1-4])\s*FY\s*'?((?:19|20)\d{2})\s*[-–/]\s*(\d{2})(?!\d)", text,
    )
    if quarter_fy_range:
        quarter = int(quarter_fy_range.group(1))
        fy_end = int(quarter_fy_range.group(2)[:2] + quarter_fy_range.group(3))
        return fy_end if quarter == 4 else fy_end - 1
    quarter_fy = re.search(r"(?i)\bQ([1-4])\s*FY\s*'?((?:19|20)\d{2}|\d{2})\b", text)
    if quarter_fy:
        quarter = int(quarter_fy.group(1))
        year_text = quarter_fy.group(2)
        fy_end = int(year_text) if len(year_text) == 4 else 2000 + int(year_text)
        return fy_end if quarter == 4 else fy_end - 1
    iso = re.search(r"\b((?:19|20)\d{2})[-/](\d{1,2})[-/](\d{1,2})\b", text)
    if iso:
        return int(iso.group(1))  # "2013-01-29" is a date in 2013, not the fiscal year "2013-01" (read as 2101)
    m = re.search(r"\b((?:19|20)\d{2})\s*[-–/]\s*(\d{2})(?!\d)(?!\s*[-/]\s*\d)", text)
    if m:
        end = int(m.group(1)[:2] + m.group(2))
        return end + 100 if end < int(m.group(1)) else end  # "1999-00" ends in 2000, not 1900
    years = re.findall(r"\b((?:19|20)\d{2})\b", text)
    if years:
        return int(years[-1])
    m = re.search(r"(?i)\bFY\s*'?(\d{2})\b", text)
    return 2000 + int(m.group(1)) if m else None


def _title_with_kept_periods(title: str, kept: list[str], outside: set[str]) -> str:
    # A whole span, including split fiscal-year endpoints ("2019-20 to 2024-25"): matching only "2019-20"
    # as the range left "... 2020-21 to 2024-25 to 2024-25" in the title.
    endpoint = r"(?:FY\s*)?(?:19|20)\d{2}(?:\s*[-\u2013]\s*\d{2}(?!\d))?"
    period_range = re.compile(rf"\b{endpoint}\s*(?:to|through|[-\u2013\u2014])\s*(?:{endpoint}|\d{{2}})\b", re.I)
    rewritten, replacements = period_range.subn(f"{kept[0]} to {kept[-1]}", str(title or ""), count=1)
    for label in sorted(outside, key=len, reverse=True):
        rewritten = rewritten.replace(label, "")
    rewritten = re.sub(r"\s+", " ", rewritten)
    rewritten = re.sub(r"\s+,\s*", ", ", rewritten)
    rewritten = re.sub(r",\s*,+", ",", rewritten)
    rewritten = re.sub(r":\s*,", ":", rewritten)
    rewritten = re.sub(r",\s*:", ":", rewritten)
    rewritten = rewritten.strip(" ,;:-")
    return rewritten if replacements else f"{rewritten}: {kept[0]} to {kept[-1]}"


def clip_to_window(chart: PlannedChart, min_year: int | None, max_year: int | None = None) -> str:
    """Keep only dated series observations inside the requested bounds when at least three remain."""
    if min_year is None and max_year is None or chart.spec.get("type") not in {"line", "grouped_bar", "bar"}:
        return ""
    series = chart.spec.get("series") or []
    labels = [str(d.get("label")) for s in series for d in s.get("data") or []]
    ends = {lab: _period_end_year(lab) for lab in labels}
    if not labels or any(v is None for v in ends.values()):
        return ""  # not a time axis (or not every label is a dated period): nothing to clip
    outside = {lab for lab, y in ends.items()
               if (min_year is not None and y < min_year) or (max_year is not None and y > max_year)}
    if not outside:
        return ""
    for s in series:
        s["data"] = [d for d in s.get("data") or [] if str(d.get("label")) not in outside]
    series = [s for s in series if len(s.get("data") or []) >= 3]
    if not series:
        return f"fewer than 3 periods inside the requested window ({min_year or 'start'} to {max_year or 'latest'})"
    chart.spec["series"] = series
    chart.points = [(a, b, v) for a, b, v in chart.points if a not in outside and b not in outside]
    title = str(chart.spec.get("title") or "")
    if any(lab in title for lab in outside):
        kept = [str(d.get("label")) for d in series[0]["data"]]
        chart.spec["title"] = _title_with_kept_periods(title, kept, outside)
    chart.spec["basisNote"] = (str(chart.spec.get("basisNote") or "") +
                               f" Periods outside {min_year or 'start'} to {max_year or 'latest'} are left out "
                               "(outside the requested window).").strip()
    return ""


def clip_to_question_window(chart: PlannedChart, question: str) -> str:
    """Drop points whose own period label the chart verifier would call outside the question's window.

    clip_to_window works in calendar years ("2019-20" ends in 2020, inside 2020-2025); the verifier reads
    Indian fiscal years (FY2019-20 is before FY2021) and rejected the whole chart for that one point. Using
    the verifier's rule here makes the two agree. Labels that are not periods are left alone.
    """
    if not question:
        return ""
    from utils.chart_evidence_audit import _periods_outside_question_window
    series = chart.spec.get("series") or []
    labels = {str(d.get("label")) for s in series for d in s.get("data") or []}
    outside = {label for label in labels if _periods_outside_question_window(question, [label], [])}
    if not outside:
        return ""
    for s in series:
        s["data"] = [d for d in s.get("data") or [] if str(d.get("label")) not in outside]
    kept = [s for s in series if len(s.get("data") or []) >= 3]
    if not kept:
        return "fewer than 3 periods inside the requested window"
    chart.spec["series"] = kept
    chart.points = [(a, b, v) for a, b, v in chart.points if a not in outside and b not in outside]
    title = str(chart.spec.get("title") or "")
    if any(label in title for label in outside):
        # "…, 2019-20 to 2024-25" would keep naming the removed period (the verifier reads the title)
        chart.spec["title"] = _title_with_kept_periods(title, [str(d.get("label")) for d in kept[0]["data"]], outside)
    chart.spec["basisNote"] = (str(chart.spec.get("basisNote") or "")
                               + " Periods outside the requested window are left out.").strip()
    return ""


_RANK_HEADER = re.compile(r"(?i)^(?:#|rank(?:ing)?|s\.?\s?no\.?|sl\.?\s?no\.?|sr\.?\s?no\.?|no\.?|serial(?: no\.?)?)$")


def _without_rank_column(passage):
    """A "# | Country | ..." table charted by its name column: the leading rank / serial column is dropped.
    Each remaining row is a substring of the source row, so evidence quotes still match the passage."""
    lines = str(passage.text or "").splitlines()
    table = [ln for ln in lines if " | " in ln]
    if len(table) < 3:
        return passage
    header = _split_row(table[0])
    if len(header) < 3 or not _RANK_HEADER.match(header[0].strip()):
        return passage
    out = [(" | ".join(_split_row(ln)[1:]) if " | " in ln else ln) for ln in lines]
    import copy
    shadow = copy.copy(passage)
    shadow.text = "\n".join(out)
    return shadow


_UNIT_ROW_LABEL = re.compile(r"(?i)^\(?\s*(?:units?|unit of measure|measure|in|values? in|figures? in)\s*\)?:?$")


def _is_numeric_cell(cell: str) -> bool:
    return _parse_number(cell) is not None and not _PERIOD_RE.match(str(cell or "").strip())


def _with_normalized_header(passage):
    """Tables whose column names span two rows are charted with one combined header row.

    Two shapes are common across publishers (any topic), and both used to give no chart at all:
      * a unit row under the header - "Country | 1990 | 2000" then "unit | Mton CO2 | Mton CO2";
      * a grouped header - "Crop | Cost | MSP" then "2021-22 | 2025-26 | Difference | 2021-22 | ...",
        where each group name spans several period columns (optionally with "Sl." / "No." split too).
    Only header rows change; every data row stays a substring of the source row, so evidence quotes
    still match the passage.
    """
    lines = str(passage.text or "").splitlines()
    table_idx = [i for i, ln in enumerate(lines) if " | " in ln]
    if len(table_idx) < 4:
        return passage
    h_i, s_i, d_i = table_idx[0], table_idx[1], table_idx[2]
    header, second, data = _split_row(lines[h_i]), _split_row(lines[s_i]), _split_row(lines[d_i])
    if any(_is_numeric_cell(c) for c in second):
        return passage  # the second row is data
    new_header: list[str] | None = None
    caption_unit = ""
    if len(second) == len(header) and len(header) >= 3 and (
            _UNIT_ROW_LABEL.match(second[0].strip())
            or (not second[0].strip() and len({c.casefold() for c in second[1:] if c}) == 1)):
        units = second[1:]
        if all(len(u) <= 30 for u in units) and any(u for u in units):
            distinct = {u.casefold() for u in units if u}
            if len(distinct) == 1:
                # one unit for the whole table: it belongs on the caption, so period headers
                # ("1990 | 2000") stay periods and the table can become a time series
                new_header, caption_unit = list(header), next(u for u in units if u)
            else:
                new_header = [header[0]] + [f"{h} ({u})" if u and u.casefold() not in h.casefold() else h
                                            for h, u in zip(header[1:], units)]
    elif len(data) > len(second) >= 2 and len(data) > len(header):
        sub = list(second)
        lead = list(header)
        label_cols = len(data) - len(sub)
        if sub and not _PERIOD_RE.match(sub[0].strip()) and label_cols + 1 <= len(lead) and \
                len(data) - (len(sub) - 1) == label_cols + 1 and len(sub) - 1 >= 2:
            # "Sl. | Crop | Cost | MSP" over "No. | 2021-22 | ...": the first cell continues the first header
            lead[0] = f"{lead[0]} {sub[0]}".strip()
            sub = sub[1:]
            label_cols = len(data) - len(sub)
        groups = lead[label_cols:]
        if label_cols >= 1 and groups and len(sub) % len(groups) == 0:
            span = len(sub) // len(groups)
            if span >= 2 and sum(bool(_PERIOD_RE.match(c.strip())) for c in sub) >= len(groups):
                new_header = lead[:label_cols] + [f"{groups[i // span]} {sub[i]}" for i in range(len(sub))]
    if not new_header or len(new_header) != len(data):
        return passage
    out = list(lines)
    out[h_i] = " | ".join(new_header)
    del out[s_i]
    if caption_unit:
        caption_at = next((i for i in range(h_i) if out[i].strip() and " | " not in out[i]), None)
        if caption_at is None:
            out.insert(h_i, f"({caption_unit})")
        elif caption_unit.casefold() not in out[caption_at].casefold():
            out[caption_at] = f"{out[caption_at].rstrip()} ({caption_unit})"
    import copy
    shadow = copy.copy(passage)
    shadow.text = "\n".join(out)
    return shadow


def _split_stacked_tables(passage) -> list:
    """Two tables printed one under the other without a caption between them are two tables.

    telecom live run: "S. No. | Period ending | Wireless Subscribers" rows were followed by "State | Number of
    Districts ... | Number of villages ..." and 28 state rows; read as one table, the subscriber chart had state
    district counts as periods and was rejected. A row of non-empty text cells (no digits) after data rows
    starts the next table."""
    from utils.evidence_text import stacked_table_starts  # the shared header rule
    lines = str(passage.text or "").splitlines()
    starts = stacked_table_starts(lines)
    if not starts:
        return [passage]
    import copy
    caption = next((ln for ln in lines if ln.strip() and " | " not in ln), "")
    parts, bounds = [], [0, *starts, len(lines)]
    for a, b in zip(bounds, bounds[1:]):
        block = lines[a:b]
        if a and caption:
            block = [caption, *block]  # the later table keeps the passage's caption line for its heading
        shadow = copy.copy(passage)
        shadow.text = "\n".join(block)
        parts.append(shadow)
    return parts


def _focused_charts(passage, index: int, qtd_start, focus: list[str] | None, min_year: int | None,
                    max_year: int | None = None) -> list[PlannedChart]:
    """Stage N: a wide table is narrowed to the question's entities and window (utils.table_focus) before
    charting; any other table is charted as before. A leading rank column is never the label."""
    # a unit / grouped second header row is part of the header, not the start of a stacked table
    passage = _with_normalized_header(passage)
    stacked = _split_stacked_tables(passage)
    if len(stacked) > 1:
        charts: list[PlannedChart] = []
        for part in stacked:
            charts += _focused_charts(part, index + len(charts), qtd_start, focus, min_year, max_year)
        return charts
    passage = _without_rank_column(passage)
    lines = [ln for ln in str(passage.text or "").splitlines() if " | " in ln]
    if len(lines) >= 3:
        # A long-format table ("Entity | Year | Value") is pivoted first, with the question's entities first.
        header = _split_row(lines[0])
        rows = [r for r in (_split_row(ln) for ln in lines[1:]) if len(r) == len(header) and r[0]]
        heading_line = next((ln for ln in str(passage.text).splitlines() if ln.strip() and " | " not in ln), "")
        long_charts = _charts_from_long_table(passage, index, qtd_start, header, rows, heading_line, focus=focus)
        if long_charts is not None:
            return long_charts
    if focus or min_year is not None or max_year is not None:
        if len(lines) >= 3:
            from utils.table_focus import plan as _focus_plan
            header = _split_row(lines[0])
            rows = [r for r in (_split_row(ln) for ln in lines[1:]) if len(r) == len(header) and r[0]]
            narrowed = _focus_plan(header, rows, entities=list(focus or []), min_year=min_year, max_year=max_year)
            if narrowed["cols"] is not None or narrowed["rows"] is not None:
                charts = charts_from_passage(passage, index, qtd_start, _only_cols=narrowed["cols"],
                                             _only_rows=narrowed["rows"])
                for chart in charts:
                    chart.spec["basisNote"] = (f"{chart.spec.get('basisNote') or ''} Shown: the "
                                               f"{narrowed['reason']} of a larger source table.").strip()
                if charts:
                    return charts
    return charts_from_passage(passage, index, qtd_start)


def build_chart_candidates(store, *, max_charts: int = 40, min_year: int | None = None, max_year: int | None = None,
                           min_reliability: float = 0.5, today: str | None = None,
                           focus: list[str] | None = None,
                           context_out: list | None = None) -> tuple[list[PlannedChart], list[dict]]:
    """Charts from every table/calculated passage; returns (verified charts, dropped log).

    ``context_out`` collects untouched copies of charts dropped only for the question's window: a report
    below its chart minimum shows them as labelled context charts.

    Dropped before writing: unverifiable data, periods entirely before the
    requested window, and data from sources below ``min_reliability``.
    """
    charts, dropped, seen, titles = [], [], set(), {}
    for passage in merge_continuations([p for p in store.passages if p.kind in {"table", "calculated"}]):
        for chart in _focused_charts(passage, len(charts) + len(dropped) + 1, qtd_start_month(today),
                                     focus, min_year, max_year):
            signature = tuple(sorted((a, b, round(v, 4)) for a, b, v in chart.points))
            if signature in seen:
                continue
            seen.add(signature)
            reason = verify_chart(chart, passage.text) or _chart_data_problem(chart)
            if not reason:
                import copy as _copy
                original = _copy.deepcopy(chart)  # clip_to_window trims the series in place
                reason = _outside_window(chart, min_year, max_year) or clip_to_window(chart, min_year, max_year)
                if reason and context_out is not None and (
                        passage.kind == "calculated" or passage.reliability >= min_reliability):
                    context_out.append(original)
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


def assign_charts(charts: list[PlannedChart], sections: list, *, per_section: int = 2,
                  fill_to: int | None = None) -> None:
    """Give each chart to the section whose queries/passages fit it best (passage overlap first).

    Charts already assigned (fetched-dataset charts, utils.dataset_charts.preassign) keep their
    section and count towards its limit. A chart whose best section is full goes to its next-best fitting
    section; while the report has fewer than ``fill_to`` charts, a weaker fit (score >= 1) is accepted too
    (live run: markets verified 11 charts but placed 6)."""
    load: dict[str, int] = {}
    for chart in charts:
        if chart.section_id:
            load[chart.section_id] = load.get(chart.section_id, 0) + 1
    ranked = []
    for chart in charts:
        if chart.section_id:
            continue
        plotted = label_entities([a for a, _, _ in chart.points] + [b for _, b, _ in chart.points])
        options = []
        for section in sections:
            # A sector deep-dive only takes charts that plot that sector.
            if (getattr(section, "role", "") == "deep_dive" and getattr(section, "entity", "")
                    and section.entity not in plotted):
                continue
            score = 3.0 * len(set(chart.passage_ids) & set(section.passage_ids))
            score += len(_terms(chart.match_text) & _terms(" ".join([section.title, section.goal, *section.queries])))
            options.append((score, section))
        options.sort(key=lambda x: -x[0])
        if options:
            ranked.append((options[0][0], chart, options))
    ranked.sort(key=lambda x: -x[0])
    for _score, chart, options in ranked:
        for score, section in options:
            if score < 2:
                break
            if load.get(section.section_id, 0) < per_section:
                chart.section_id = section.section_id
                load[section.section_id] = load.get(section.section_id, 0) + 1
                break
    if fill_to:
        # Below the report's chart target, a section may take one chart over its limit: the data for a
        # question often clusters on one section (UPI: the overall-volume section held the only two
        # placed charts while a verified FY2019-20 to FY2024-25 volume chart for it was left out).
        for _score, chart, options in ranked:
            if sum(load.values()) >= fill_to:
                break
            if chart.section_id:
                continue
            for score, section in options:
                if score >= 1 and load.get(section.section_id, 0) < per_section + 1:
                    chart.section_id = section.section_id
                    load[section.section_id] = load.get(section.section_id, 0) + 1
                    break
