"""Chart titles and chart/text coherence for V2 charts (Stage C2).

A chart's title is generated from the chart's own data -- metric (column header
and unit), the plotted entities (row labels) and the period -- never from the
page heading above the table, which often names a different subject (an "IT"
article heading over a table of consumer companies). A page heading is used
only as a source of the period when it names no entity that is absent from
the data.

The same helpers check that the paragraph interpreting a chart talks about the
chart's own entities and values (used by the pipeline after writing and by
eval_report.py as the "chart/title mismatch" check).
"""
from __future__ import annotations

import re

from utils.entity_tiers import SYNONYMS, canonical

# "1 Data Processing Services" is a rank; "1 Week" / "3 Months" is a period and "31 Oct 2026" a date.
_RANK_PREFIX_RE = re.compile(
    r"^\s*(?:#?\d{1,3}[.)]?\s+)(?=[A-Za-z])(?!(?:w|wk|weeks?|m|mths?|months?|y|yrs?|years?|d|days?|quarters?|qtrs?)\b)"
    r"(?!(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\b)", re.I)
_TICKER_RE = re.compile(r"[A-Z][A-Z0-9&\-]{1,11}")
# Price/level columns of different instruments are not comparable in one bar chart.
NON_COMPARABLE_METRIC_RE = re.compile(
    r"(?i)^\s*(?:last|ltp|cmp|price|close|closing(?:\s+price|\s+value)?|open|high|low|index\s+level|level|"
    r"current\s+(?:price|value|level)|value|mkt\s+price|market\s+price)\s*(?:\((?:rs|₹|inr|pts)\.?\)|\(₹\))?\s*$")
_PERIOD_TOKEN_RE = re.compile(
    r"(?i)\b(?:Q[1-4]\s*(?:FY|CY)?\s*'?\d{2,4}(?:\s*[-–/]\s*\d{2,4})?|H[12]\s*FY\s*'?\d{2,4}|FY\s*'?\d{2,4}(?:\s*[-–/]\s*\d{2,4})?|"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+'?\d{2,4}|"
    r"\d{1,2}\s*(?:w|wk|week|weeks|m|month|months|y|yr|year|years|d|day|days)\b|(?:19|20)\d{2})")
_NUMERIC_HEADER_RE = re.compile(r"^\s*[+\-−–]?\s*(?:₹|\$|rs\.?\s*)?[\d,]*\.?\d+\s*(?:%|x|cr|crore|bn|mn)?\s*$", re.I)
def _alias_alt(name: str) -> str:
    # Short upper-case names ("IT") match case-sensitively so the pronoun "it" is not a hit.
    return re.escape(name) if name.isupper() and len(name) <= 4 else f"(?i:{re.escape(name)})"


_SECTOR_ALIAS_RE = {
    canon: re.compile(r"(?<![A-Za-z])(?:" + "|".join(_alias_alt(a) for a in sorted({canon, *aliases}, key=len, reverse=True)) + r")(?![A-Za-z])")
    for canon, aliases in SYNONYMS.items()
}


def clean_label(label: str) -> str:
    """Display label: no markdown emphasis, no leading rank number ("1 Data Processing Services")."""
    text = re.sub(r"[*_`]", "", str(label or "")).strip()
    # flags / pictographs in a header ("🇨🇳 China") are decoration, not part of the name
    text = re.sub("[\U0001F000-\U0001FAFF\U0001F1E6-\U0001F1FF☀-➿️]+", "", text).strip()
    return _RANK_PREFIX_RE.sub("", text).strip() or text


def header_is_data(header: str) -> bool:
    """A 'header' that is a number ("5.99%", "19.86", "+2,599 Cr") means the table has no real header row."""
    return bool(_NUMERIC_HEADER_RE.match(str(header or "").replace("−", "-")))


def non_comparable_metric(metric: str) -> bool:
    return bool(NON_COMPARABLE_METRIC_RE.match(str(metric or "")))


def row_kind(label: str) -> str:
    """'sector' (a known sector index/benchmark), 'ticker' (exchange symbol) or 'other' (company / item name)."""
    text = clean_label(label)
    canon = canonical(text)
    if canon in SYNONYMS or re.match(r"(?i)^(?:nifty|bse|s&p bse|sensex)\b", text):
        return "sector"
    if _TICKER_RE.fullmatch(text):
        return "ticker"
    return "other"


def homogeneous_rows(rows: list[list[str]]) -> tuple[list[list[str]], str]:
    """Keep one kind of row when a table mixes sector indices with stock tickers.

    A "sector flow" table that also lists DIXON/HAVELLS rows is two tables in
    one; plotting them together lets the text describe the stocks while the
    chart shows the sectors. Returns (rows, note).
    """
    kinds = [row_kind(r[0]) for r in rows]
    sectors = [r for r, k in zip(rows, kinds) if k == "sector"]
    tickers = [r for r, k in zip(rows, kinds) if k == "ticker"]
    if sectors and tickers:
        keep = sectors if len(sectors) >= len(tickers) else tickers
        return keep, f"kept {len(keep)} {'sector' if keep is sectors else 'stock'} rows of {len(rows)} (mixed table)"
    return rows, ""


def entities_named(text: str) -> set[str]:
    """Known sector entities named in a text (alias-aware)."""
    return {canon for canon, pattern in _SECTOR_ALIAS_RE.items() if pattern.search(str(text or ""))}


def label_entities(labels) -> set[str]:
    out = set()
    for label in labels:
        canon = canonical(clean_label(label))
        if canon in SYNONYMS:
            out.add(canon)
        out |= entities_named(label)
    return out


def foreign_entities(text: str, labels) -> set[str]:
    """Sector entities a title/heading names that are not among the chart's own labels."""
    named = entities_named(text)
    if not named:
        return set()
    return named - label_entities(labels)


def period_of(metric_headers: list[str], heading: str, labels) -> str:
    """Period from the column headers first; else from a heading that names no foreign entity."""
    for header in metric_headers:
        match = _PERIOD_TOKEN_RE.search(header or "")
        if match:
            return ""  # the metric header already carries its period ("Revenue Q4 FY26", "vs Nifty 50, 13w")
    if heading and not foreign_entities(heading, labels):
        match = _PERIOD_TOKEN_RE.search(heading)
        if match and not re.fullmatch(r"\d{1,2}\s*\w+", match.group(0)):
            return match.group(0).strip()
    return ""


def _subject(labels: list[str]) -> str:
    names = [clean_label(x) for x in labels if str(x).strip()]
    if not names:
        return ""
    if len(names) <= 3:
        return ", ".join(names[:-1]) + (" and " if len(names) > 1 else "") + names[-1]
    return f"{', '.join(names[:3])} and {len(names) - 3} other{'s' if len(names) - 3 > 1 else ''}"


def data_title(*, metric: str, labels: list[str], period: str = "", periods: list[str] | None = None,
               unit: str = "", max_len: int = 120) -> str:
    """'<metric> (<unit>): <entities>, <period>' from the chart's own data."""
    metric = re.sub(r"\s+", " ", clean_label(metric)).strip(" -–—:|") or "Value"
    if unit and unit not in metric and unit != "%":
        metric = f"{metric} ({unit})"
    elif unit == "%" and "%" not in metric:
        metric = f"{metric} (%)"
    if periods and len(periods) >= 2:
        period = f"{clean_label(periods[0])} to {clean_label(periods[-1])}"
    title = f"{metric}: {_subject(labels)}" if labels else metric
    if period and period.casefold() not in title.casefold():
        title += f", {period}"
    return title if len(title) <= max_len else title[: max_len - 1].rstrip(" ,;:") + "…"


_MEASURE_WORDS = re.compile(
    r"(?i)\b(?:share|capacity|value|values|rate|ratio|growth|index|price|prices|volume|number|count|total|amount|"
    r"revenue|sales|production|generation|consumption|flows?|inflows?|outflows?|coverage|percent|percentage|change|"
    r"returns?|yield|demand|supply|exports?|imports?|cost|costs|spend|spending|investment|gdp|income|profit|margin|"
    r"population|emissions|output|level|balance|debt|deficit|surplus|assets|aum|units|tonnes|mw|gw|twh|usd|inr|"
    r"crore|lakh|million|billion|trillion|average|median|mean|score|per|cagr|yoy|qoq|mom|y-o-y)\b")


def looks_like_entity(header: str) -> bool:
    """A column header that names WHO is measured ("India", "Tata Power", "Andhra Pradesh"), not WHAT.

    Short, no digits, no unit or %, and none of the generic measure words above."""
    text = str(header or "").strip()
    if not text or len(text) > 40 or re.search(r"[\d%()$€£₹/]", text):
        return False
    words = text.split()
    return 1 <= len(words) <= 4 and not _MEASURE_WORDS.search(text)


_TRAILING_PERIOD = re.compile(r"[\s,;:–—-]*\(?(?:(?:19|20)\d{2}(?:\s*[–—-]\s*(?:19|20)?\d{2,4})?|FY\s*\d{2,4})\)?\s*$")


def caption_metric(caption: str) -> str:
    """The measure named by a table's own caption, without its trailing period ("..., 2021-2025")."""
    text = re.sub(r"\s+", " ", str(caption or "")).strip(" .:-–—")
    previous = None
    while previous != text:
        previous, text = text, _TRAILING_PERIOD.sub("", text).strip(" .,:-–—")
    return text


def unique_titles(specs: list[dict]) -> int:
    """Make chart titles unique (same title -> add the passage it came from). Returns titles changed."""
    seen: dict[str, int] = {}
    changed = 0
    for spec in specs:
        title = str(spec.get("title") or "")
        key = re.sub(r"^\s*Figure\s+\d+\s*:\s*", "", title).casefold()
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            passage = ",".join(spec.get("evidencePassages") or []) or f"set {seen[key]}"
            spec["title"] = f"{title} ({passage})"
            changed += 1
    return changed


# ── chart / text coherence ───────────────────────────────────────────────────
_NUM_RE = re.compile(r"(?<![\w.])[-+−]?\d[\d,]*(?:\.\d+)?")


def chart_labels(spec: dict) -> list[str]:
    labels: list[str] = []
    for series in spec.get("series") or []:
        if not isinstance(series, dict):
            continue
        if series.get("name"):
            labels.append(str(series["name"]))
        for point in series.get("data") or []:
            if isinstance(point, dict) and point.get("label") is not None:
                labels.append(str(point["label"]))
    labels += [str(x) for x in spec.get("rows") or []] + [str(x) for x in spec.get("columns") or []]
    return list(dict.fromkeys(x for x in labels if x.strip()))


def chart_values(spec: dict) -> set[float]:
    values: set[float] = set()
    for series in spec.get("series") or []:
        for point in (series.get("data") if isinstance(series, dict) else None) or []:
            try:
                values.add(round(float(point.get("value")), 4))
            except (TypeError, ValueError, AttributeError):
                continue
    for row in spec.get("values") or []:
        for v in row or []:
            try:
                values.add(round(float(v), 4))
            except (TypeError, ValueError):
                continue
    return values


def _label_in(label: str, text: str) -> bool:
    name = clean_label(label)
    name = re.sub(r"(?i)^(?:nifty|s&p bse|bse)\s+", "", name).strip()
    if len(name) < 2:
        return False
    if len(name) <= 4 or name.isupper():
        return bool(re.search(rf"(?<![A-Za-z0-9]){re.escape(name)}(?![A-Za-z0-9])", text))
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z&'.-]{2,}", name)]
    if re.search(rf"(?<![A-Za-z]){re.escape(name)}(?![A-Za-z])", text, re.I):
        return True
    # Multi-word company names: the first distinctive word is enough ("Nestle" for "Nestle India").
    return bool(words) and len(words[0]) >= 4 and bool(re.search(rf"(?<![A-Za-z]){re.escape(words[0])}(?![A-Za-z])", text, re.I))


def _text_numbers(text: str) -> list[float]:
    stripped = re.sub(r"\[[^\]]*\]|\b(?:Figure|Fig\.)\s+\d+\b", " ", text)
    stripped = re.sub(r"(?i)\b(?:Q[1-4]|H[12])\s*(?:FY|CY)?\s*'?\d{2,4}\b|\bFY\s*'?\d{2,4}\b|\b(?:19|20)\d{2}\b", " ", stripped)
    stripped = re.sub(r"(?i)\b\d{1,2}[- ]?(?:w|wk|weeks?|months?|m|years?|y|days?|d)\b", " ", stripped)
    out = []
    for token in _NUM_RE.findall(stripped):
        try:
            out.append(float(token.replace(",", "").replace("−", "-").lstrip("+")))
        except ValueError:
            continue
    return out


def _value_match(number: float, values: set[float]) -> bool:
    for v in values:
        for candidate in (v, -v):
            if abs(candidate - number) <= max(0.051, abs(candidate) * 0.0005):
                return True
    return False


def text_chart_mismatch(text: str, spec: dict) -> str:
    """'' when the interpreting text is about this chart's data, else the reason.

    The text must name at least one plotted entity or quote at least one plotted
    value; and when it quotes numbers, at least one must be a plotted value.
    """
    text = str(text or "")
    labels = chart_labels(spec)
    values = chart_values(spec)
    label_hit = any(_label_in(label, text) for label in labels)
    numbers = [n for n in _text_numbers(text) if abs(n) not in {0, 1, 2, 3, 4, 5}]
    value_hit = any(_value_match(n, values) for n in numbers)
    if numbers and not value_hit:
        return "the text quotes values that are not in the chart"
    if not label_hit and not value_hit:
        return "the text names none of the chart's entities"
    return ""


def title_data_mismatch(spec: dict) -> str:
    """'' when the title describes the chart's own entities, else the reason."""
    title = re.sub(r"^\s*Figure\s+\d+\s*:\s*", "", str(spec.get("title") or ""))
    foreign = foreign_entities(title, chart_labels(spec))
    if foreign:
        return f"title names {', '.join(sorted(foreign))}, which the chart does not plot"
    return ""
