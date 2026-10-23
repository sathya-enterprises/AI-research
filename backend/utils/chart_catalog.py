"""Canonical chart catalog and tier-aware chart-data density policy.

The chart catalog is shared by report generation, validation and HTML/PDF
render paths so a visualization that is advertised to the model is not later
rejected merely because the model used its human-readable name.

Chart observation counts are descriptive. Supported values, source provenance,
labels, units, and readability determine whether a visualization is valid; no
minimum point count or visualization quota is applied.
"""
from __future__ import annotations

import math
import re
from typing import Any

# Internal canonical IDs used by the renderers.
CHART_TYPES: frozenset[str] = frozenset({
    "pie", "bar", "line", "area", "scatter", "bubble", "doughnut",
    "histogram", "boxplot", "waterfall", "funnel", "radar", "polar_area",
    "treemap", "heatmap", "stacked_bar", "grouped_bar", "horizontal_bar",
    "stacked_area", "step_line", "spline", "combo", "candlestick", "ohlc",
    "gauge", "bullet", "pictogram", "pareto", "dot_plot", "lollipop",
    "violin", "density", "ridgeline", "gantt", "timeline", "marimekko",
    "sunburst", "sankey", "chord", "network", "tree", "flowchart", "venn",
    "funnel_pyramid", "population_pyramid", "progress", "kpi", "sparkline",
    "error_bar", "control_chart", "arrow", "table",
})

# Human names supplied by prompts/users/models -> canonical render ID.
CHART_TYPE_ALIASES: dict[str, str] = {
    "pie chart": "pie", "bar chart": "bar", "line chart": "line",
    "area chart": "area", "scatter plot": "scatter", "scatter chart": "scatter",
    "bubble chart": "bubble", "doughnut chart": "doughnut", "donut": "doughnut",
    "donut chart": "doughnut", "histogram chart": "histogram", "box plot": "boxplot",
    "box plot chart": "boxplot", "waterfall chart": "waterfall", "funnel chart": "funnel",
    "radar chart": "radar", "polar area chart": "polar_area", "treemap chart": "treemap",
    "heat map": "heatmap", "heat map chart": "heatmap", "stacked bar chart": "stacked_bar",
    "grouped bar chart": "grouped_bar", "horizontal bar chart": "horizontal_bar",
    "stacked area chart": "stacked_area", "step line chart": "step_line",
    "spline chart": "spline", "combo chart": "combo", "candlestick chart": "candlestick",
    "ohlc chart": "ohlc", "gauge chart": "gauge", "bullet chart": "bullet",
    "pictogram chart": "pictogram", "pareto chart": "pareto", "dot plot": "dot_plot",
    "dot plot chart": "dot_plot", "lollipop chart": "lollipop", "violin plot": "violin",
    "density plot": "density", "ridgeline plot": "ridgeline", "gantt chart": "gantt",
    # "Gunn chart" is not a separate standard chart ID in this app; accept it
    # as a common typo/variant for Gantt and render it using the Gantt layout.
    "gunn chart": "gantt", "gunn": "gantt", "gunn_chart": "gantt",
    "gynn chart": "gantt", "gynn": "gantt", "gynn_chart": "gantt", "gant chart": "gantt",
    "timeline chart": "timeline", "marimekko chart": "marimekko",
    "sunburst chart": "sunburst", "sankey diagram": "sankey", "chord diagram": "chord",
    "network graph": "network", "tree diagram": "tree", "flow chart": "flowchart",
    "venn diagram": "venn", "funnel pyramid": "funnel_pyramid",
    "population pyramid": "population_pyramid", "progress chart": "progress",
    "kpi chart": "kpi", "kpi card": "kpi", "sparkline chart": "sparkline",
    "error bar chart": "error_bar", "control chart": "control_chart",
    "arrow chart": "arrow", "stacked-bar": "stacked_bar", "grouped-bar": "grouped_bar",
    "horizontal-bar": "horizontal_bar", "stacked-area": "stacked_area",
    "step-line": "step_line", "polar-area": "polar_area", "funnel-pyramid": "funnel_pyramid",
    "population-pyramid": "population_pyramid", "error-bar": "error_bar",
    "control-chart": "control_chart", "dot-plot": "dot_plot",
}

AXIS_CHART_TYPES: frozenset[str] = frozenset({
    "bar", "line", "area", "stacked_bar", "grouped_bar", "horizontal_bar",
    "stacked_area", "step_line", "spline", "combo", "arrow", "scatter", "bubble",
    "waterfall", "candlestick", "ohlc", "histogram", "boxplot", "error_bar",
    "control_chart", "dot_plot", "lollipop", "pareto", "violin", "density",
    "ridgeline", "gantt", "timeline", "population_pyramid",
})

STRUCTURED_CHART_TYPES: frozenset[str] = frozenset({
    "heatmap", "sankey", "chord", "network", "tree", "flowchart", "venn", "gantt", "timeline",
})

# Retain the historical helper shape for stored report compatibility; its
# cardinality limits are intentionally unset.
CHART_POINT_BANDS: dict[str, dict[str, Any]] = {
    "brief": {"name": "Brief", "minimum": None, "maximum": None, "target": None},
    "detailed": {"name": "In-Detail", "minimum": None, "maximum": None, "target": None},
    "in_depth": {"name": "Deep Research", "minimum": None, "maximum": None, "target": None},
}


def normalize_chart_type(value: Any) -> str:
    """Normalize a chart type label/alias to a canonical chart ID."""
    raw = str(value or "").strip().lower()
    raw = re.sub(r"\s+", " ", raw)
    if raw in CHART_TYPE_ALIASES:
        return CHART_TYPE_ALIASES[raw]
    normalized = raw.replace("-", "_").replace(" ", "_")
    if normalized in CHART_TYPES:
        return normalized
    # Allow an extra generic suffix where it is unambiguous.
    for suffix in ("_chart", "_diagram", "_plot", "_graph"):
        if normalized.endswith(suffix):
            base = normalized[:-len(suffix)]
            if base in CHART_TYPE_ALIASES:
                return CHART_TYPE_ALIASES[base]
            if base in CHART_TYPES:
                return base
    # Alias lookups also accept underscore-normalized input.
    for alias, canonical in CHART_TYPE_ALIASES.items():
        if alias.replace(" ", "_").replace("-", "_") == normalized:
            return canonical
    return normalized


def _normalize_report_tier(tier: str | None) -> str:
    key = re.sub(r"[\s-]+", "_", str(tier or "detailed").strip().lower())
    aliases = {
        "brief_report": "brief", "basic": "brief",
        "detailed_report": "detailed", "in_detail": "detailed", "in_detail_report": "detailed", "standard": "detailed",
        "deep": "in_depth", "deep_research": "in_depth", "deep_research_report": "in_depth", "in_depth_report": "in_depth",
        "advanced": "in_depth",
    }
    return aliases.get(key, key)


def chart_point_band(tier: str | None) -> dict[str, Any]:
    """Return a descriptive tier label with no chart-point minimum or quota."""
    key = _normalize_report_tier(tier)
    return dict(CHART_POINT_BANDS.get(key, CHART_POINT_BANDS["detailed"]))


def chart_density_contract(tier: str | None) -> str:
    """Prompt-ready visual guidance without cardinality quotas."""
    tier_key = _normalize_report_tier(tier)
    if tier_key == "in_depth":
        depth_rule = "When the source contains useful granular history or segments, preserve and analyze that detail; otherwise explain the evidence limit."
        profile = "Deep Research"
    elif tier_key == "detailed":
        depth_rule = "Use a comparison or trend visualization when additional sourced observations materially improve the analysis."
        profile = "In-Detail"
    else:
        depth_rule = "Prefer only the most useful evidence-backed visual; a short comparison is valid when it answers the question."
        profile = "Brief"
    return f"""CHART GUIDANCE: {profile}
- Charts are MANDATORY: every section that contains sourced numbers must include at least one chart, and the report should carry as many distinct, evidence-backed charts as the sources support. Prefer charts and data tables over prose; keep prose to short interpretive notes (2–3 sentences) that state what each chart shows, citing sources, with no generic background or filler.
- Use every relevant observation actually supported by the source, while keeping labels readable; there is no minimum point count or visual quota.
- {depth_rule}
- Type-aware exceptions are mandatory: pie/doughnut/polar-area should normally stay at 2–8 meaningful categories; radar should use a small set of comparable dimensions; KPI/gauge/progress/bullet charts should show only the real metrics/targets; funnels should use real stages; Venn charts use sets; Gantt/timeline use actual events; Sankey/chord/network/tree/flowchart count meaningful nodes and links, not scalar observations.
- For many-category composition, prefer treemap/Marimekko or a ranked bar over a crowded pie.
- Count unique observed categories/periods/records (or matrix cells/events for their native form). Do not create repeated labels, fake dates, padded bins, or synthetic records.
- If evidence supports only a few points, render those points or use a table/narrative; do not reject a useful comparison or invent data.
- VARIETY: do not default to bar/pie/line for everything. Where sourced data fits, pick the chart type that matches the data shape from the full catalog, and use a different type for each distinct analytical point:
  * entities x metrics -> grouped_bar / radar / heatmap (sector x period, bank x metric); ranked single metric -> horizontal_bar / lollipop / dot_plot
  * change over periods -> line / spline / step_line / area / stacked_area; two metrics on different scales -> combo; volatility range -> candlestick / ohlc
  * composition of a total -> doughnut / treemap / stacked_bar (many categories: treemap or Marimekko, never a crowded pie); bridge from A to B -> waterfall; ranked contributors with cumulative share -> pareto
  * flows between groups (FPI/DII flows, capital allocation, trade partners) -> sankey; dated events (policy steps, agreements signed, deals) -> timeline / gantt
  * single headline figures vs target -> kpi / gauge / bullet / progress; spread across companies -> histogram / boxplot; relationship of two metrics -> scatter / bubble
  * a table is the right answer when exact figures matter more than shape.
  When several of these are supportable from the same sourced observations, include several different types rather than repeating one; when the data cannot support a type, skip it. Never invent, pad or interpolate values to make a particular type possible."""


def _finite_chart_number(value: Any) -> bool:
    """True only for a finite numeric observation, not nulls/placeholders/bools."""
    if isinstance(value, bool) or value is None:
        return False
    try:
        number = float(str(value).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return False
    return math.isfinite(number)


def _series_point_count(series: dict, chart_type: str) -> int:
    data = series.get("data") or []
    if not isinstance(data, list):
        return 0
    # Raw observations packed into an explicit values array (for example a
    # distribution input) are counted as real records only when numeric.
    raw_values = [
        value
        for point in data if isinstance(point, dict)
        for value in (point.get("values") or [])
        if _finite_chart_number(value)
    ]
    if raw_values:
        return len(raw_values)

    labels = set()
    for point in data:
        if not isinstance(point, dict):
            continue
        value = point.get("value", point.get("y"))
        if not _finite_chart_number(value):
            continue
        label = point.get("label") or point.get("date") or point.get("period") or point.get("category") or point.get("x")
        if label is None or not str(label).strip():
            # Scatter/bubble charts can encode observations as numeric x/y pairs
            # without category labels; the two finite coordinates are the label.
            if chart_type in {"scatter", "bubble", "candlestick", "ohlc"} and _finite_chart_number(point.get("x")):
                label = f"{point.get('x')}:{value}"
            else:
                continue
        normalized_label = re.sub(r"\s+", " ", str(label)).strip().casefold()
        synthetic_sequence_label = bool(re.fullmatch(r"(?:observation|point|item|category)?\s*#?\d+", normalized_label))
        numeric_label = re.fullmatch(r"\s*(\d{4})\s*", normalized_label)
        valid_year_label = bool(numeric_label and 1900 <= int(numeric_label.group(1)) <= 2100) or bool(re.fullmatch(r"fy\s*\d{2,4}", normalized_label))
        if normalized_label and (not synthetic_sequence_label or valid_year_label):
            labels.add(normalized_label)
    return len(labels)


def count_chart_points(chart: dict) -> int:
    """Count distinct, labelled, finite observations using the native chart shape."""
    if not isinstance(chart, dict):
        return 0
    ctype = normalize_chart_type(chart.get("type") or chart.get("chart_type") or chart.get("chartType"))
    if ctype == "heatmap":
        rows, cols, values = chart.get("rows") or [], chart.get("columns") or [], chart.get("values") or []
        if not rows or not cols or len(values) != len(rows):
            return 0
        return sum(
            1 for row in values if isinstance(row, list)
            for value in row if _finite_chart_number(value)
        )
    if ctype in {"sankey", "chord", "network", "tree", "flowchart"}:
        return sum(1 for link in (chart.get("links") or []) if isinstance(link, dict)
                   and str(link.get("source") or link.get("from") or "").strip()
                   and str(link.get("target") or link.get("to") or "").strip()
                   and _finite_chart_number(link.get("value", link.get("weight", 1))))
    if ctype in {"gantt", "timeline"}:
        return sum(1 for event in (chart.get("events") or []) if isinstance(event, dict)
                   and str(event.get("label") or event.get("name") or event.get("title") or "").strip()
                   and (event.get("start") or event.get("date") or event.get("time")))
    if ctype == "venn":
        return sum(1 for item in (chart.get("sets") or []) if isinstance(item, dict)
                   and str(item.get("label") or item.get("name") or "").strip())
    series = chart.get("series") or []
    if not isinstance(series, list) or not series:
        # Accept compact single-series schemas only when they have an explicit
        # labels axis and an equal-length numeric dataset.
        labels = chart.get("labels") or chart.get("x_labels") or chart.get("categories") or []
        values = chart.get("data") or chart.get("values") or []
        if isinstance(labels, list) and isinstance(values, list) and len(labels) == len(values):
            return len({str(label).strip().casefold() for label, value in zip(labels, values)
                        if label is not None and str(label).strip() and _finite_chart_number(value)})
        return 0
    counts = [
        _series_point_count(item, ctype)
        for item in series if isinstance(item, dict)
    ]
    return max(counts, default=0)


def chart_density_assessment(chart: dict, tier: str | None) -> dict[str, Any]:
    """Report chart cardinality for QA without treating counts as a quality target."""
    ctype = normalize_chart_type((chart or {}).get("type"))
    band = chart_point_band(tier)
    # Tables are structured report content, not plots. Their row count remains
    # descriptive and is never compared with a point-count band.
    if ctype == "table":
        rows = (chart or {}).get("rows") or []
        return {
            "type": ctype, "count": len(rows) if isinstance(rows, list) else 0,
            "minimum": None, "maximum": None,
            "profile": band["name"], "applicable": False,
            "within_band": None, "series_counts": [],
        }
    n = count_chart_points(chart or {})
    applicable = True

    series_counts = []
    for index, item in enumerate((chart or {}).get("series") or []):
        if not isinstance(item, dict):
            continue
        series_counts.append({
            "name": str(item.get("name") or f"Series {index + 1}"),
            "count": _series_point_count(item, ctype),
        })
    return {
        "type": ctype, "count": n, "minimum": None, "maximum": None,
        "profile": band["name"], "applicable": applicable,
        "within_band": None,
        "series_counts": series_counts,
    }


# Slice-based charts stay readable up to roughly ten slices. Report data keeps
# every scraped observation; renderers call fold_slice_tail_for_display() so
# the smallest remainder is drawn as one "Other" slice whose value is the exact
# sum of the folded observations (shares of the total stay correct).
SLICE_CHART_TYPES: frozenset[str] = frozenset({"pie", "doughnut", "polar_area", "pictogram", "sunburst", "funnel"})
MAX_DISPLAY_SLICES = 10


def _slice_value(point: dict) -> float | None:
    for key in ("value", "y", "count", "amount"):
        if key in point:
            raw = point.get(key)
            if not _finite_chart_number(raw):
                return None
            return float(str(raw).replace(",", "").replace("%", "").strip())
    return None


def fold_slice_tail_for_display(chart: dict, limit: int = MAX_DISPLAY_SLICES) -> dict:
    """Return a display copy of a slice chart keeping its ``limit`` largest
    slices (in their original order) plus one derived "Other (n)" slice at the
    end; other charts pass through unchanged."""
    if not isinstance(chart, dict):
        return chart
    ctype = normalize_chart_type(chart.get("type") or chart.get("chart_type") or chart.get("chartType"))
    series = chart.get("series")
    if ctype not in SLICE_CHART_TYPES or not isinstance(series, list) or len(series) != 1 or not isinstance(series[0], dict):
        return chart
    data = series[0].get("data")
    if not isinstance(data, list):
        return chart
    rows = [(point, _slice_value(point)) for point in data if isinstance(point, dict)]
    numeric = [(point, value) for point, value in rows if value is not None]
    # Fold only when one more slice than the limit exists and every slice has a
    # number; otherwise "Other" would be a single item or drop a missing value.
    if len(numeric) <= limit + 1 or len(numeric) != len(rows):
        return chart
    ranked = sorted(range(len(numeric)), key=lambda i: -abs(numeric[i][1]))
    keep = set(ranked[:limit])
    head = [row for i, row in enumerate(numeric) if i in keep]
    tail = [row for i, row in enumerate(numeric) if i not in keep]
    other = {
        "label": f"Other ({len(tail)})",
        "value": round(sum(value for _point, value in tail), 6),
        "derived": True,
        "foldedLabels": [str(point.get("label") or point.get("name") or "") for point, _value in tail],
    }
    display = dict(chart)
    display["series"] = [{**series[0], "data": [point for point, _value in head] + [other]}]
    display["foldedSliceCount"] = len(tail)
    return display
