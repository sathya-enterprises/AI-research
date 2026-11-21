"""Choosing which verified charts a report shows (any topic).

A live one-month market report shipped 14 verified charts, but five were the same "Change (%): 5 stock names"
daily-movers view from one publisher, and one compared index LEVELS across indices (Sensex 82,960 beside Nifty
Midcap 150), which says nothing: index levels have different bases. Verification only asks "is every value in the
source"; this module asks "is this chart worth a slot":

* index levels of different indices are not compared in one bar chart (their % changes are);
* at most ``MAX_SAME_VIEW`` charts of one measure from one publisher (the rest are the same view again).
Charts are kept in their incoming order, which already puts the charts that prove the report's points first.
"""
from __future__ import annotations

import re
from collections import Counter

MAX_SAME_VIEW = 2

_INDEX_LABEL_RE = re.compile(r"(?i)\b(?:nifty|sensex|bankex|index|indices|nasdaq|dow|s&p|nikkei|kospi|hang seng|ftse|dax)\b")
_LEVEL_MEASURE_RE = re.compile(r"(?i)\b(?:value|close|closing|level|ltp|last traded|last|current|price|points?)\b")
_RATIO_MEASURE_RE = re.compile(r"(?i)\b(?:p/?e|p/?b|ratio|yield|weight|multiple)\b")
_CHANGE_MEASURE_RE = re.compile(r"(?i)%|\bchange\b|\breturn|\bgain|\bgrowth\b")


def measure_stem(title: object) -> str:
    """The measure a chart shows, without figure numbers, units, counts or dates: "change"."""
    text = re.sub(r"(?i)^\s*figure\s+\d+\s*[:.]\s*", "", str(title or ""))
    text = text.split(":", 1)[0]
    text = re.sub(r"\([^)]*\)", " ", text)
    text = re.sub(r"[^a-z ]+", " ", text.casefold())
    text = re.sub(r"\b(?:chg|chng|pct change|per cent change|percent change)\b", "change", text)
    return re.sub(r"\s+", " ", text).strip()


def _labels(spec: dict) -> list[str]:
    labels = []
    for series in spec.get("series") or []:
        for point in (series.get("data") or []) if isinstance(series, dict) else []:
            if isinstance(point, dict):
                labels.append(str(point.get("label") or ""))
    return labels


def _values(spec: dict) -> list[float]:
    values = []
    for series in spec.get("series") or []:
        for point in (series.get("data") or []) if isinstance(series, dict) else []:
            try:
                values.append(abs(float(point.get("value"))))
            except (TypeError, ValueError, AttributeError):
                continue
    return values


def index_level_comparison(spec: dict) -> bool:
    """A bar chart of the LEVELS of different indices (not their changes)."""
    if str(spec.get("type") or "").casefold() not in {"bar", "horizontal_bar", "column"}:
        return False
    measure = " ".join(str(spec.get(k) or "") for k in ("title", "yLabel", "unit"))
    measure += " " + " ".join(str(s.get("name") or "") for s in spec.get("series") or [] if isinstance(s, dict))
    # Only LEVEL measures (close, value, points): ratios such as P/E or P/B are comparable across indices.
    if _CHANGE_MEASURE_RE.search(measure) or _RATIO_MEASURE_RE.search(measure) or not _LEVEL_MEASURE_RE.search(measure):
        return False
    labels = _labels(spec)
    indices = [label for label in labels if _INDEX_LABEL_RE.search(label)]
    values = [v for v in _values(spec) if v > 0]
    return len(indices) >= 2 and len(indices) >= 0.6 * len(labels) and bool(values) and max(values) / min(values) > 2


def index_point_change_comparison(spec: dict) -> bool:
    """A bar chart of the POINT changes of different indices ("Chg: Nifty Bank 1,572, Nifty IT 403"): a point
    move depends on the index level, so it is no more comparable across indices than the level is."""
    if str(spec.get("type") or "").casefold() not in {"bar", "horizontal_bar", "column"}:
        return False
    measure = " ".join(str(spec.get(k) or "") for k in ("title", "yLabel", "unit"))
    measure += " " + " ".join(str(s.get("name") or "") for s in spec.get("series") or [] if isinstance(s, dict))
    if "%" in measure or re.search(r"(?i)per ?cent|return", measure) or not re.search(r"(?i)chg|change|points?", measure):
        return False
    labels = _labels(spec)
    indices = [label for label in labels if _INDEX_LABEL_RE.search(label)]
    return len(indices) >= 2 and len(indices) >= 0.6 * len(labels)


def numeric_labels(spec: dict) -> bool:
    """Most category labels are numbers ("-2.36", "1.38"): a misread table, not a readable chart."""
    labels = [label.strip() for label in _labels(spec) if label.strip()]
    # years ("2015", "2024") are period labels of a time series, not misread numbers
    # "1336 343 455 292 -425": a whole data row read as one label is a misread table too
    numeric = [label for label in labels
               if re.fullmatch(r"(?:[-+−]?[0-9][0-9,]*(?:[.][0-9]+)?%?)(?:[ ]+[-+−]?[0-9][0-9,]*(?:[.][0-9]+)?%?)*", label)
               and not re.fullmatch(r"(?:19|20)[0-9]{2}", label)]
    return len(labels) >= 3 and len(numeric) >= 0.5 * len(labels)


def _publisher(chart, domain_of: dict | None = None) -> str:
    """The publishing site of a chart's first source (several pages of one site are one publisher)."""
    spec = getattr(chart, "spec", chart) or {}
    ids = list(getattr(chart, "source_ids", None) or spec.get("sourceIds") or spec.get("source") or [])
    sid = str(ids[0]).upper() if ids else ""
    return (domain_of or {}).get(sid) or sid


def domains_from_manifest(manifest: list | None) -> dict:
    from urllib.parse import urlparse
    out = {}
    for row in manifest or []:
        if not isinstance(row, dict):
            continue
        host = (urlparse(str(row.get("url") or "")).hostname or str(row.get("domain") or "")).casefold()
        host = host[4:] if host.startswith("www.") else host
        if row.get("id") and host:
            out[str(row["id"]).upper()] = host
    return out


_LONG_HORIZON_RE = re.compile(
    r"(?i)\b(?:[1-9]\d?)\s*-?\s*(?:y|yr|yrs|year|years)\b|\bfull history\b|\bsince inception\b|\bcagr\b")
_SHORT_WINDOW_RE = re.compile(
    r"(?i)\b(?:last|past|previous|this|current|recent)\s+(?:(?:one|two|three|four|six|1|2|3|4|6)\s+)?"
    r"(?:days?|weeks?|months?|fortnight)\b|\b(?:today|this week|this month|mtd|month[- ]to[- ]date)\b")


def short_window_question(question: str) -> bool:
    """A question about the last days / weeks / up to six months (not a multi-year trend)."""
    return bool(_SHORT_WINDOW_RE.search(str(question or "")))


def long_horizon_chart(spec: dict) -> bool:
    """A chart whose own measure spans a year or more (1Y / 2Y returns, CAGR, full-history medians)."""
    text = " ".join([str(spec.get("title") or ""), str(spec.get("yLabel") or ""),
                     *[str(s.get("name") or "") for s in spec.get("series") or [] if isinstance(s, dict)]])
    return bool(_LONG_HORIZON_RE.search(text))


def latest_year(spec: dict) -> int | None:
    """The latest year a chart's data is dated by (its point labels, else its title and series names)."""
    from utils.section_charts import _period_end_year
    years = [y for y in (_period_end_year(label) for label in _labels(spec)) if y]
    if not years:
        texts = [str(spec.get("title") or "")] + [str(s.get("name") or "") for s in spec.get("series") or []
                                                  if isinstance(s, dict)]
        years = [y for y in (_period_end_year(t) for t in texts) if y]
    return max(years) if years else None


def select_charts(charts: list, domain_of: dict | None = None, question: str = "",
                  today: str | None = None) -> tuple[list, list[dict]]:
    """Keep the charts worth a slot; returns (kept, dropped log). Works on PlannedChart or spec dicts."""
    short_window = short_window_question(question)
    stale_before = None
    if short_window:
        from datetime import date, timedelta
        try:
            now = date.fromisoformat(str(today or date.today().isoformat())[:10])
        except ValueError:
            now = date.today()
        stale_before = (now - timedelta(days=183)).year  # a "last month" question is not answered by 2024 data
    kept, dropped = [], []
    seen: Counter = Counter()
    for chart in charts:
        spec = getattr(chart, "spec", chart) or {}
        title = str(spec.get("title") or "")
        measure_part = re.sub(r"(?i)^\s*figure\s+\d+\s*[:.]\s*", "", title).split(":", 1)[0]
        if title and not re.search(r"[A-Za-z]", measure_part):
            dropped.append({"title": title, "reason": "its title names no measure (a run of numbers)"})
            continue
        year = latest_year(spec) if stale_before else None
        if year is not None and year < stale_before:
            dropped.append({"title": title, "reason": f"its data is from {year}; the question asks about the last few weeks or months"})
            continue
        if short_window and long_horizon_chart(spec):
            dropped.append({"title": title, "reason": "measures a year or more; the question asks about a short recent period"})
            continue
        if numeric_labels(spec):
            dropped.append({"title": title, "reason": "its category labels are numbers (a misread table)"})
            continue
        if index_point_change_comparison(spec):
            dropped.append({"title": title, "reason": "compares point changes across indices (their % changes are comparable)"})
            continue
        if index_level_comparison(spec):
            dropped.append({"title": title, "reason": "compares index levels across different indices (their % changes are comparable, their levels are not)"})
            continue
        stem = measure_stem(title)
        # Short-window market news repeats one view across sites (each site's top movers): the cap counts the
        # view, whichever site it comes from. Long-horizon questions never reach the cap (see below).
        key = (stem,) if short_window else (stem, _publisher(chart, domain_of))
        # Short-window questions draw on market news pages that repeat one view (each day's top movers); a
        # long-horizon question's repeated measure is usually distinct data (registrations per vehicle segment).
        if short_window and stem and seen[key] >= MAX_SAME_VIEW:
            dropped.append({"title": title, "reason": f"the same '{stem}' view is already shown {MAX_SAME_VIEW} times"})
            continue
        seen[key] += 1
        kept.append(chart)
    return kept, dropped
