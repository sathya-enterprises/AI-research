"""Choose the chart form that shows a verified chart's data best (any topic).

The chart builders produce line, horizontal-bar, grouped-bar and heatmap specs only, so every report looked the
same. ``choose_form`` reads the data's shape (periods or categories, how many series, units, whether values are
parts of a whole, stages, opposing pairs, a target) and picks one of the allowed forms. It never changes the
plotted numbers: series and points stay as verified, so every evidence check reads the same values; a form that
needs a different layout (histogram bins, the two sides of a pyramid, a gauge's target) is derived at render time
or carried in extra keys.

Allowed forms (from the client's chart list, excluding scatter, bubble, lollipop, dot plot, slope, dumbbell,
bullet, treemap, sunburst, violin, density, polar area, marimekko, streamgraph, cumulative, choropleth, network
and waffle): column, bar, line, area, stacked column, 100% stacked, grouped column, pie/donut, histogram, radar,
waterfall, step line, stacked area, gauge, funnel, heatmap, candlestick, combo, Pareto, population pyramid.
"""
from __future__ import annotations

import math
import re

ALLOWED_FORMS = frozenset({
    "bar", "horizontal_bar", "line", "area", "stacked_bar", "grouped_bar", "doughnut", "pie", "histogram",
    "radar", "waterfall", "step_line", "stacked_area", "gauge", "funnel", "heatmap", "candlestick", "combo",
    "pareto", "population_pyramid",
})

_TEMPORAL_RE = re.compile(
    r"(?i)^\s*(?:(?:19|20)\d{2}(?:\s*[-/–]\s*\d{2,4})?(?:\s*\([a-z]+\))?|fy\s?'?\d{2,4}(?:\s*[-/–]\s*\d{2,4})?|"
    r"q[1-4]\s*(?:fy)?\s*'?\d{2,4}|[1-4]q\s*'?\d{2,4}|h[12]\s*(?:fy)?\s*'?\d{2,4}|cy\s?'?\d{2,4}|"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?[\s'’-]*\d{2,4}|"
    r"\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{4}-\d{2}(?:-\d{2})?)\s*$")
_TOTAL_RE = re.compile(r"(?i)\b(?:total|overall|all india|aggregate|grand total|sum)\b")
_COMPOSITION_RE = re.compile(
    r"(?i)\bby (?:source|type|segment|category|fuel|mode|component|product|technology|channel|sector|instrument|"
    r"asset class|use|end use|region)\b|\bmix\b|\bcomposition\b|\bbreak-?down\b|\bsplit\b|\bcontribution\b|\bshare of\b")
_STEP_MEASURE_RE = re.compile(r"(?i)\b(?:rate|duty|tariff|price|msp|repo|policy|limit|cap|ceiling|threshold|slab|"
                              r"levy|cess|subsidy|incentive|benchmark)\b")
_STOCK_MEASURE_RE = re.compile(r"(?i)\b(?:cumulative|installed|stock|outstanding|holdings?|reserves?|aum|assets under|"
                               r"subscribers?|users|fleet|base|capacity|network length|accounts)\b")
_RATIO_NAME_RE = re.compile(r"(?i)%|\bper ?cent|\bshare\b|\brate\b|\bratio\b|\bgrowth\b|\byoy\b|\by-o-y\b|\bmargin\b|\bcagr\b")
_STAGE_RE = re.compile(r"(?i)\b(?:appl(?:ied|ications?)|sanction(?:ed)?|approv(?:ed|als?)|disburs(?:ed|ements?)|"
                       r"install(?:ed|ations?)|regist(?:ered|rations?)|enrol(?:led|ments?)|shortlist(?:ed)?|"
                       r"select(?:ed|ions?)|complet(?:ed|ions?)|commission(?:ed|ing)|award(?:ed|s)?|bids?|"
                       r"tender(?:ed|s)?|received|processed|issued|operational|pipeline|stage)\b")
_OPPOSING_PAIRS = (
    ("export", "import"), ("inflow", "outflow"), ("buy", "sell"), ("purchase", "sale"), ("male", "female"),
    ("men", "women"), ("boys", "girls"), ("urban", "rural"), ("receipt", "payment"), ("credit", "debit"),
    ("gross purchase", "gross sale"), ("addition", "retirement"), ("gain", "loss"),
)
_ACTUAL_RE = re.compile(r"(?i)\b(?:achieved|actual|installed|current|so far|till date|progress|status)\b")
_TARGET_RE = re.compile(r"(?i)\b(?:target|goal|aim|plan(?:ned)?|commitment)\b")


def _series(spec: dict) -> list[dict]:
    return [s for s in spec.get("series") or [] if isinstance(s, dict)]


def _points(series: dict) -> list[dict]:
    return [p for p in series.get("data") or [] if isinstance(p, dict)]


def _num(point: dict) -> float | None:
    try:
        value = float(point.get("value"))
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _is_temporal(labels: list[str]) -> bool:
    return bool(labels) and sum(1 for l in labels if _TEMPORAL_RE.match(str(l))) >= max(2, math.ceil(len(labels) * 0.7))


def _same_labels(series: list[dict]) -> bool:
    first = [str(p.get("label")) for p in _points(series[0])]
    return all([str(p.get("label")) for p in _points(s)] == first for s in series[1:])


def _pair_kind(a: str, b: str) -> bool:
    a, b = a.casefold(), b.casefold()
    return any((x in a and y in b) or (y in a and x in b) for x, y in _OPPOSING_PAIRS)


def choose_form(spec: dict) -> tuple[str, dict, str]:
    """(form, extra spec keys, reason) for a verified chart; the current type when nothing fits better."""
    current = str(spec.get("type") or "bar")
    if current in {"heatmap", "table", "candlestick", "ohlc", "boxplot", "waterfall"}:
        return current, {}, ""
    series = _series(spec)
    if not series or not _points(series[0]):
        return current, {}, ""
    title = str(spec.get("title") or "")
    unit = str(spec.get("unit") or "")
    names = [str(s.get("name") or "") for s in series]
    labels = [str(p.get("label") or "") for p in _points(series[0])]
    values = [[_num(p) for p in _points(s)] for s in series]
    flat = [v for vs in values for v in vs if v is not None]
    if len(flat) < 2:
        return current, {}, ""
    non_negative = all(v >= 0 for v in flat)
    n_series, n_labels = len(series), len(labels)
    temporal = _is_temporal(labels)
    is_percent = "%" in unit or bool(re.search(r"(?i)\(%\)|\bper ?cent\b", title))
    composition_hint = bool(_COMPOSITION_RE.search(title)) and not any(_TOTAL_RE.search(n) for n in names)

    names_l = {n.casefold() for n in names}
    if {"open", "high", "low", "close"} <= names_l:
        return "candlestick", {}, "open, high, low and close for each period"

    if temporal:
        if n_series == 1:
            vs = [v for v in values[0] if v is not None]
            repeats = sum(1 for a, b in zip(vs, vs[1:]) if a == b)
            if (len(vs) >= 4 and repeats >= 1 and len(set(vs)) <= math.ceil(len(vs) / 2)
                    and _STEP_MEASURE_RE.search(f"{title} {names[0]}")):
                return "step_line", {}, "a set level that changes in steps"
            if len(vs) >= 4 and non_negative and _STOCK_MEASURE_RE.search(f"{title} {names[0]}"):
                return "area", {}, "a stock or cumulative quantity over time"
            return "line", {}, "one measure over time"
        if n_series == 2 and _same_labels(series):
            # an amount and a rate (GW and %) are never stacked or drawn on one axis
            ratio = [bool(_RATIO_NAME_RE.search(n)) for n in names]
            if ratio[0] != ratio[1]:
                extra = {"secondaryUnit": "%"}
                if ratio[0]:
                    extra["swapSeries"] = True  # bars first, the rate as the line on its own axis
                return "combo", extra, "an amount and a rate over time"
        if 2 <= n_series <= 6 and _same_labels(series):
            totals = [sum(v for v in col if v is not None) for col in zip(*values)]
            if is_percent and non_negative and totals and all(95 <= t <= 105 for t in totals):
                return ("stacked_bar" if n_labels <= 8 else "stacked_area"), {"percentStacked": True}, \
                    "shares that make up 100% in each period"
            if non_negative and composition_hint and n_series >= 2:
                return ("stacked_area" if n_labels >= 6 else "stacked_bar"), {}, "the parts of a total over time"
            return "line", {}, "several measures over time"
        return current, {}, ""

    # categories
    if n_series == 1:
        vs = values[0]
        if n_labels == 2 and any(_ACTUAL_RE.search(l) for l in labels) and any(_TARGET_RE.search(l) for l in labels):
            actual_i = next(i for i, l in enumerate(labels) if _ACTUAL_RE.search(l))
            target_i = 1 - actual_i
            if vs[actual_i] is not None and vs[target_i] and vs[target_i] > 0:
                return "gauge", {"target": vs[target_i], "max": vs[target_i], "min": 0,
                                 "gaugeIndex": actual_i}, "progress towards a target"
        if (3 <= n_labels <= 7 and non_negative and sum(1 for l in labels if _STAGE_RE.search(l)) >= 2
                and all(a is not None and b is not None and a > b for a, b in zip(vs, vs[1:]))):
            return "funnel", {}, "numbers falling stage by stage"
        total = sum(v for v in vs if v is not None)
        if non_negative and 2 <= n_labels <= 7 and not any(_TOTAL_RE.search(l) for l in labels) and (
                (is_percent and 95 <= total <= 105) or (composition_hint and n_labels <= 6)):
            return "doughnut", {}, "parts of one whole"
        if non_negative and n_labels >= 30 and not is_percent:
            return "histogram", {}, "how the values are distributed across many items"
        # Pareto: an additive quantity (not an average, rate, price or ratio) where a few items hold most of it
        additive = not re.search(r"(?i)\b(?:average|avg|mean|median|rate|ratio|price|tariff|per|growth|yield|index|"
                                 r"margin|cagr|score|density|intensity|efficiency|utili[sz]ation)\b|/", f"{title} {names[0]} {unit}")
        if non_negative and additive and 8 <= n_labels <= 20 and not is_percent and total > 0:
            top = sorted((v for v in vs if v is not None), reverse=True)
            if sum(top[:max(1, math.ceil(n_labels / 4))]) >= 0.7 * total:
                return "pareto", {}, "a few items account for most of the total"
        if n_labels <= 7 and max((len(l) for l in labels), default=0) <= 14:
            return "bar", {}, "a few categories compared"
        return "horizontal_bar", {}, "many categories or long names compared"
    if n_series == 2 and _same_labels(series) and _pair_kind(names[0], names[1]) and non_negative:
        return "population_pyramid", {"leftName": names[0], "rightName": names[1]}, "two opposite sides per category"
    if (2 <= n_series <= 4 and 4 <= n_labels <= 8 and _same_labels(series) and is_percent and non_negative
            and not any(_TOTAL_RE.search(l) for l in labels)):
        return "radar", {}, "several entities profiled on the same measures"
    if 2 <= n_series <= 6 and _same_labels(series) and non_negative and composition_hint:
        return "stacked_bar", {}, "the parts of each category's total"
    if 2 <= n_series <= 6 and _same_labels(series):
        return "grouped_bar", {}, "categories compared side by side"
    return current, {}, ""


def apply_form(spec: dict) -> dict:
    """The spec with its chosen form (a new dict; data unchanged except the series order for a combo)."""
    form, extra, reason = choose_form(spec)
    if form not in ALLOWED_FORMS or form == spec.get("type"):
        return spec
    out = dict(spec)
    out["type"] = form
    swap = extra.pop("swapSeries", False)
    out.update(extra)
    if swap and len(out.get("series") or []) == 2:
        out["series"] = list(reversed(out["series"]))
    if reason:
        out["formReason"] = reason
    return out
