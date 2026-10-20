"""Evidence-backed "Data and Charts" gallery covering the full chart catalog.

Given an :class:`utils.evidence_dataset.EvidenceDataset`, build every requested
chart type the extracted observations can honestly support. Three evidence
bases are used and labelled on every chart:

* ``reported``  – plotted values are the exact source figures; each point
  carries ``evidenceSource``/``evidenceQuote``/``evidenceValue`` so the chart
  audit can verify it against the cited source line.
* ``derived``   – plotted values are computed from reported figures (shares,
  quartiles, histogram counts, normalised scores, peer averages). The inputs
  are listed in ``derivedFrom`` and verified instead of the computed values.
* ``provenance`` – diagrams of the evidence itself (which sources cover which
  entities, reporting-period coverage, the retrieval pipeline).

A type the data cannot support is never faked: it is listed in the coverage
table with the reason (e.g. candlestick needs open/high/low/close sessions).
"""
from __future__ import annotations

import math
import os
import re
import statistics
from dataclasses import dataclass, field
from typing import Any, Callable

from utils.chart_catalog import CHART_TYPE_ALIASES, CHART_TYPES, normalize_chart_type
from utils.evidence_dataset import EvidenceDataset, Observation, _metric_in, period_date_range

# Display order follows the catalog requested for reports.
GALLERY_ORDER: list[str] = [
    "pie", "bar", "line", "area", "scatter", "bubble", "doughnut", "histogram", "boxplot",
    "waterfall", "funnel", "radar", "polar_area", "treemap", "heatmap", "stacked_bar",
    "grouped_bar", "horizontal_bar", "stacked_area", "step_line", "spline", "combo",
    "candlestick", "ohlc", "gauge", "bullet", "pictogram", "pareto", "dot_plot", "lollipop",
    "violin", "density", "ridgeline", "gantt", "timeline", "marimekko", "sunburst", "sankey",
    "chord", "network", "tree", "flowchart", "venn", "funnel_pyramid", "population_pyramid",
    "progress", "kpi", "sparkline", "error_bar", "control_chart",
]
CORE_TYPES: list[str] = [
    "grouped_bar", "bar", "horizontal_bar", "line", "stacked_bar", "pie", "scatter", "heatmap",
    "radar", "boxplot", "error_bar", "kpi",
]
DISPLAY_NAMES: dict[str, str] = {
    "pie": "Pie chart", "bar": "Bar chart", "line": "Line chart", "area": "Area chart",
    "scatter": "Scatter plot", "bubble": "Bubble chart", "doughnut": "Doughnut chart",
    "histogram": "Histogram", "boxplot": "Box plot", "waterfall": "Waterfall chart",
    "funnel": "Funnel chart", "radar": "Radar chart", "polar_area": "Polar area chart",
    "treemap": "Treemap", "heatmap": "Heatmap", "stacked_bar": "Stacked bar chart",
    "grouped_bar": "Grouped bar chart", "horizontal_bar": "Horizontal bar chart",
    "stacked_area": "Stacked area chart", "step_line": "Step line chart", "spline": "Spline chart",
    "combo": "Combo chart", "candlestick": "Candlestick chart", "ohlc": "OHLC chart",
    "gauge": "Gauge chart", "bullet": "Bullet chart", "pictogram": "Pictogram chart",
    "pareto": "Pareto chart", "dot_plot": "Dot plot", "lollipop": "Lollipop chart",
    "violin": "Violin plot", "density": "Density plot", "ridgeline": "Ridgeline plot",
    "gantt": "Gantt chart", "timeline": "Timeline chart", "marimekko": "Marimekko chart",
    "sunburst": "Sunburst chart", "sankey": "Sankey diagram", "chord": "Chord diagram",
    "network": "Network graph", "tree": "Tree diagram", "flowchart": "Flowchart",
    "venn": "Venn diagram", "funnel_pyramid": "Funnel pyramid", "population_pyramid": "Population pyramid",
    "progress": "Progress chart", "kpi": "KPI chart", "sparkline": "Sparkline",
    "error_bar": "Error bar chart", "control_chart": "Control chart",
}
FAMILIES: list[tuple[str, list[str]]] = [
    ("Comparison across entities", ["grouped_bar", "bar", "horizontal_bar", "stacked_bar", "dot_plot", "lollipop",
                                    "bullet", "population_pyramid", "radar", "heatmap"]),
    ("Trends over reported periods", ["line", "spline", "step_line", "area", "stacked_area", "combo", "sparkline",
                                      "control_chart", "error_bar", "candlestick", "ohlc"]),
    ("Composition and ranking", ["pie", "doughnut", "polar_area", "treemap", "sunburst", "marimekko", "pictogram",
                                 "waterfall", "pareto", "funnel", "funnel_pyramid"]),
    ("Distribution and relationships", ["scatter", "bubble", "histogram", "boxplot", "violin", "density", "ridgeline"]),
    ("Headline indicators", ["kpi", "gauge", "progress"]),
    ("Evidence coverage and provenance", ["gantt", "timeline", "sankey", "chord", "network", "tree", "flowchart", "venn"]),
]
_BASIS_TEXT = {
    "reported": "reported source values",
    "derived": "computed from reported values",
    "provenance": "evidence provenance",
    "market": "market price data",
}


class Unsupported(Exception):
    pass


@dataclass
class GalleryChart:
    type: str
    chart: dict
    caption: str
    basis: str


@dataclass
class GalleryResult:
    charts: list[GalleryChart] = field(default_factory=list)
    coverage: list[dict] = field(default_factory=list)
    tables_markdown: str = ""
    evidence_lines: dict[str, list[str]] = field(default_factory=dict)
    observation_count: int = 0

    @property
    def included_types(self) -> list[str]:
        return [c.type for c in self.charts]


# ─────────────────────────────────────────────────────────────────────────────
# Requested chart types
# ─────────────────────────────────────────────────────────────────────────────
_NAME_PATTERNS: list[tuple[str, re.Pattern]] = []
for _alias, _canon in sorted({**CHART_TYPE_ALIASES, **{t.replace("_", " "): t for t in CHART_TYPES}}.items(),
                             key=lambda kv: -len(kv[0])):
    if _canon in {"table", "arrow"}:
        continue
    _NAME_PATTERNS.append((_canon, re.compile(rf"(?<![a-z]){re.escape(_alias).replace(r'\ ', r'[\s_-]+')}(?:\s+(?:chart|plot|diagram|graph))?(?![a-z])", re.I)))
_NAME_PATTERNS.append(("gantt", re.compile(r"(?<![a-z])(?:gynn|gunn|gant)\s+chart(?![a-z])", re.I)))


def requested_chart_types(question: str) -> list[str]:
    """Chart types named in the request, in the order they appear."""
    text = str(question or "")
    found: list[tuple[int, str]] = []
    occupied: list[tuple[int, int]] = []
    for canon, pattern in _NAME_PATTERNS:
        for match in pattern.finditer(text):
            span = match.span()
            if any(not (span[1] <= a or span[0] >= b) for a, b in occupied):
                continue
            occupied.append(span)
            found.append((span[0], canon))
    ordered: list[str] = []
    for _pos, canon in sorted(found):
        if canon in CHART_TYPES and canon not in ordered:
            ordered.append(canon)
    return ordered


def mode_env_off() -> bool:
    return os.getenv("REPORT_CHART_GALLERY", "core").strip().casefold() in {"off", "0", "false", "none"}


def gallery_types_for(question: str, report_tier: str = "detailed") -> list[str]:
    """Resolve which chart types to attempt.

    Explicit catalog requests can exercise all 50 types. Ordinary research
    reports choose a small domain-appropriate candidate set; ``all`` means the
    catalog is available, not that every chart must be rendered in every report.
    """
    named = requested_chart_types(question)
    catalog_audit = bool(re.search(
        r"(?is)\b(?:audit|test|inventory|list|demonstrate)\b.{0,120}\b(?:complete\s+catalog(?:ue)?|all\s+50|50|all)\b.{0,80}\bchart\s+types?\b",
        str(question or ""),
    ))
    if catalog_audit:
        return list(GALLERY_ORDER)
    if len(named) >= 2 or re.search(r"\ball\s+(?:the\s+)?(?:chart|graph|visual)", str(question or ""), re.I):
        if re.search(r"\ball\s+(?:the\s+)?(?:chart|graph|visual)\w*\s+(?:types?|sections?|kinds?)", str(question or ""), re.I):
            return list(GALLERY_ORDER)
        if mode_env_off():
            return named
        return list(dict.fromkeys(named + CORE_TYPES)) if named else list(GALLERY_ORDER)
    mode = os.getenv("REPORT_CHART_GALLERY", "core").strip().casefold()
    if mode in {"off", "0", "false", "none"}:
        return named
    # Default chart selection is domain-aware. The full 50-type catalog remains
    # available when explicitly requested, but should not create fake violin,
    # funnel, network or KPI charts from unrelated numeric fields.
    q = str(question or "").casefold()
    if any(term in q for term in (
        "banking sector", "scheduled commercial bank", "bank credit", "deposit growth",
        "gross npa", "net npa", "digital banking", "indian banks", "credit growth",
    )):
        # Banking research commonly has dated aggregate series plus a few peer
        # comparisons. Do not create 20+ chart variants from one extracted table.
        domain_types = ["line", "bar", "horizontal_bar", "grouped_bar", "heatmap", "scatter"]
    elif any(term in q for term in ("promoter", "pledge", "stake sale", "block deal", "bulk deal")):
        domain_types = ["bar", "horizontal_bar", "grouped_bar", "stacked_bar", "line",
                        "heatmap", "scatter", "dot_plot", "timeline"]
    elif any(term in q for term in ("sector rotation", "sector index", "sector indices", "nifty sector")):
        domain_types = ["heatmap", "line", "area", "bar", "horizontal_bar", "grouped_bar", "stacked_bar",
                        "scatter", "bubble", "combo", "timeline"]
    elif any(term in q for term in ("free trade", "fta", "tariff", "trade agreement", "export market access")):
        domain_types = ["bar", "horizontal_bar", "grouped_bar", "stacked_bar", "line", "area", "heatmap",
                        "scatter", "timeline"]
    elif mode in {"all", "full", "catalog", "catalogue"}:
        # Permit an explicit generic all-catalogue mode for catalogue/gallery
        # tests and deliberate inventory requests. Domain-specific branches above
        # remain narrowly scoped even when this mode is enabled.
        domain_types = list(GALLERY_ORDER)
    elif mode == "core":
        domain_types = CORE_TYPES
    else:
        # Unknown modes fall back to the stable core set.
        domain_types = CORE_TYPES
    return list(dict.fromkeys(named + domain_types))


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _raw_number(raw: str) -> str:
    match = re.search(r"\d[\d,]*(?:\.\d+)?", str(raw or ""))
    return match.group(0) if match else ""


def _fmt(value: float) -> str:
    if value is None or not math.isfinite(value):
        return ""
    if abs(value) >= 1000:
        return f"{value:,.2f}".rstrip("0").rstrip(".")
    if abs(value) >= 100:
        return f"{value:,.2f}".rstrip("0").rstrip(".")
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _unit_label(unit: str) -> str:
    return {"x": "x", "%": "%", "count": "count", "": "as reported"}.get(unit, unit)


def _sid_sort(sid: str) -> int:
    return int(re.sub(r"\D", "", sid) or 0)


def _cite(ids) -> str:
    ids = sorted({i for i in ids if i}, key=_sid_sort)
    return f" [{', '.join(ids)}]" if ids else ""


_COMPONENT_METRICS = {"demand_deposits", "savings_deposits", "term_deposits", "term_loans"}


def _metric_family(metric: str) -> str:
    key = str(metric or "").casefold()
    for family, pattern in (("credit", r"credit|advances|loan"), ("deposits", r"deposit|casa"),
                            ("asset_quality", r"npa|slippage|provision"), ("profitability", r"roa|roe|profit|nim|interest"),
                            ("capital", r"car\b|^car$|capital"), ("digital", r"upi|digital"), ("rates", r"repo|rate")):
        if re.search(pattern, key):
            return family
    return key


class _Builder:
    def __init__(self, ds: EvidenceDataset, question: str, extra: dict | None = None):
        self.ds = ds
        self.question = question or ""
        self.extra = extra or {}
        self.best = ds.best()
        self.usage: dict[str, int] = {}
        self.requested_metrics = self._requested_metric_keys()
        # Pre-compute cross-sections for every metric.
        self.sections: dict[str, tuple[str, list[Observation]]] = {}
        for metric in ds.metrics():
            period, obs = ds.cross_section(metric)
            if obs:
                self.sections[metric] = (period, obs)

    # ── metric ranking ───────────────────────────────────────────────────
    def _requested_metric_keys(self) -> list[str]:
        keys: list[str] = []
        q = self.question
        for _s, _e, key in _metric_in(q):
            window = re.split(r"[,;.]|\band\b", q[_s:_e + 24].casefold())[0]
            if key in {"gross_npa", "net_npa"}:
                keys.append(f"{key}_ratio" if "ratio" in window or "%" in window else f"{key}_amount")
                keys.append(f"{key}_ratio")
            elif key.endswith("_growth"):
                # "credit growth" is already the growth metric; appending
                # another suffix produced credit_growth_growth, so requested
                # metrics never matched and the gallery charted levels instead.
                keys.append(key)
            elif "growth" in window:
                keys.append(f"{key}_growth")
            else:
                keys.append(key)
        # Equivalent keys produced by table vs prose extraction, and broad
        # phrases that name a group of metrics.
        synonyms = {
            "credit_growth": ["advances_growth"], "deposit_growth": ["deposits_growth"],
            "advances_growth": ["credit_growth"], "deposits_growth": ["deposit_growth"],
        }
        for key in list(keys):
            keys.extend(synonyms.get(key, []))
        lowered = q.casefold()
        if re.search(r"gross\s+(?:and|&)\s+net\s+npas?|\bnpa\s+trends?|asset\s+quality", lowered):
            keys.extend(["gross_npa_ratio", "net_npa_ratio", "provision_coverage", "slippage_ratio"])
        if re.search(r"\bprofitability\b", lowered):
            keys.extend(["roa", "roe", "nim", "net_profit"])
        if re.search(r"capital\s+(?:adequacy|buffers?|position)", lowered):
            keys.append("car")
        return list(dict.fromkeys(keys))

    def unit(self, metric: str) -> str:
        return self.ds.metric_unit(metric)

    def label(self, metric: str) -> str:
        return self.ds.metric_label(metric)

    def rank_metrics(self, *, units: set[str] | None = None, min_entities: int = 2, targets_only: bool = False,
                     prefer_unused: bool = True, exclude: set[str] | None = None) -> list[str]:
        out = []
        for metric, (period, obs) in self.sections.items():
            if exclude and metric in exclude:
                continue
            if units is not None and self.unit(metric) not in units:
                continue
            pool = [o for o in obs if o.is_target] if targets_only else obs
            if len({o.entity_key for o in pool}) < min_entities:
                continue
            out.append(metric)

        def score(metric: str):
            period, obs = self.sections[metric]
            targets = sum(1 for o in obs if o.is_target)
            requested = 0 if metric in self.requested_metrics else 1
            req_index = self.requested_metrics.index(metric) if metric in self.requested_metrics else 99
            used = self.usage.get(metric, 0) if prefer_unused else 0
            # Requested metrics may be reused a few times before rotation moves
            # on; source-specific custom columns come last.
            effective_used = max(0, used - 2) if requested == 0 else used
            custom = 1 if metric.startswith("x_") else 0
            # Totals before their components (deposits before demand/savings
            # deposits, advances before term loans) for comparison charts.
            component = 1 if metric in _COMPONENT_METRICS else 0
            return (custom, component, effective_used, requested, req_index, -targets, -len(obs))
        return sorted(out, key=score)

    def mark(self, *metrics: str) -> None:
        for m in metrics:
            self.usage[m] = self.usage.get(m, 0) + 1

    def series_name(self, metric: str, period: str) -> str:
        return f"{self.label(metric)} ({period})" if period else self.label(metric)

    def y_label(self, metric: str) -> str:
        unit = self.unit(metric)
        return f"{self.label(metric)} ({_unit_label(unit)})"[:60]

    @staticmethod
    def point(label: str, obs: Observation, **extra) -> dict:
        p = {"label": label, "value": obs.value, "evidenceSource": obs.source_id,
             "evidenceQuote": obs.quote[:400], "evidenceValue": _raw_number(obs.raw_value)}
        p.update(extra)
        return p

    @staticmethod
    def derived_inputs(observations: list[Observation]) -> list[dict]:
        seen = set()
        out = []
        for o in observations:
            sig = (o.source_id, o.quote, o.value)
            if sig in seen:
                continue
            seen.add(sig)
            out.append({"sourceId": o.source_id, "quote": o.quote[:400], "value": o.value,
                        "evidenceValue": _raw_number(o.raw_value), "label": f"{o.entity} · {o.metric_label}"})
        return out

    def base(self, ctype: str, title: str, obs: list[Observation], *, x_label: str = "", y_label: str = "",
             unit: str = "", basis: str = "reported", note: str = "") -> dict:
        chart = {
            "type": ctype, "title": title[:120], "unit": unit,
            "source": sorted({o.source_id for o in obs}, key=_sid_sort),
            "evidenceBasis": basis, "gallery": True,
            "basisNote": note or _BASIS_TEXT.get(basis, basis),
        }
        if x_label:
            chart["xLabel"] = x_label[:60]
        if y_label:
            chart["yLabel"] = y_label[:60]
        return chart

    # Cross-section restricted for shared-axis legibility (the renderer and
    # validator reject a >100× spread on one linear axis).
    @staticmethod
    def legible(obs: list[Observation], limit: float = 90.0) -> list[Observation]:
        kept = sorted(obs, key=lambda o: (not o.is_target, -abs(o.value)))
        while len(kept) > 2:
            values = [abs(o.value) for o in kept if o.value]
            if not values or max(values) / max(min(values), 1e-9) <= limit:
                break
            smallest = min((o for o in kept if not o.is_target), key=lambda o: abs(o.value), default=None)
            if smallest is None:
                smallest = min(kept, key=lambda o: abs(o.value))
            kept.remove(smallest)
        return kept

    def cross(self, metric: str, *, targets_only: bool = False, nonneg: bool = False) -> tuple[str, list[Observation]]:
        period, obs = self.sections.get(metric, ("", []))
        if targets_only:
            obs = [o for o in obs if o.is_target]
        if nonneg:
            obs = [o for o in obs if o.value >= 0]
        return period, obs

    def money_metrics(self, **kw) -> list[str]:
        return self.rank_metrics(units={"₹ crore", "US$ million"}, **kw)

    def ratio_metrics(self, **kw) -> list[str]:
        return self.rank_metrics(units={"%"}, **kw)

    # Multi-period panels: entities × periods with complete coverage.
    def panel(self, metric: str, *, min_periods: int = 2, min_entities: int = 1, max_periods: int = 12):
        series = self.ds.time_series(metric)
        if not series:
            return [], {}
        all_periods: dict[str, float] = {}
        for items in series.values():
            for o in items:
                all_periods[o.period] = o.period_sort
        ordered = [p for p, _ in sorted(all_periods.items(), key=lambda kv: kv[1])][-max_periods:]
        best_choice = ([], {})
        best_score = -1
        # Choose the period subset (contiguous by sort) maximising entities×periods.
        for i in range(len(ordered)):
            for j in range(i + min_periods, len(ordered) + 1):
                window = ordered[i:j]
                ents = {k: {o.period: o for o in v if o.period in window} for k, v in series.items()}
                ents = {k: v for k, v in ents.items() if len(v) == len(window)}
                if len(ents) < min_entities:
                    continue
                score = len(ents) * len(window) + 0.1 * len(window)
                if score > best_score:
                    best_score, best_choice = score, (window, ents)
        if best_score < 0:
            # Fall back to the single entity with the most periods.
            key, items = max(series.items(), key=lambda kv: len(kv[1]))
            if len(items) >= min_periods and min_entities <= 1:
                window = [o.period for o in items][-max_periods:]
                return window, {key: {o.period: o for o in items if o.period in window}}
            return [], {}
        return best_choice

    def trend_metrics(self, min_periods: int = 2, min_entities: int = 1) -> list[tuple[str, list[str], dict]]:
        out = []
        for metric in self.ds.metrics():
            periods, ents = self.panel(metric, min_periods=min_periods, min_entities=min_entities)
            if periods and ents:
                out.append((metric, periods, ents))

        def score(item):
            metric, periods, ents = item
            requested = 0 if metric in self.requested_metrics else 1
            used = self.usage.get(metric, 0)
            return (1 if metric.startswith("x_") else 0, max(0, used - 2) if requested == 0 else used, requested,
                    -(len(periods) * len(ents)))
        return sorted(out, key=score)

    # ── chart builders (each returns chart, caption) ─────────────────────
    def build(self, ctype: str) -> GalleryChart:
        fn: Callable[[], tuple[dict, str]] = getattr(self, f"_b_{ctype}", None)
        if fn is None:
            raise Unsupported("no builder for this type")
        chart, caption = fn()
        return GalleryChart(ctype, chart, caption, chart.get("evidenceBasis", "reported"))

    # Comparison
    def _single_metric_bar(self, ctype: str, *, prefer_many: bool = False, units: set[str] | None = None,
                           sort_desc: bool = True, title_tpl: str = "{metric} by entity") -> tuple[dict, str]:
        ranked = self.rank_metrics(units=units, min_entities=2)
        if prefer_many:
            ranked = sorted(ranked, key=lambda m: (m.startswith("x_"), self.usage.get(m, 0), -len(self.sections[m][1])))
        # A requested metric reported for one subject over several periods
        # makes a meaningful bar ("Gross NPA ratio by period"); prefer that to
        # an unrequested metric that merely has two or more entities.
        if self.requested_metrics and not any(m in self.requested_metrics for m in ranked):
            period_bar = self._requested_period_bar(ctype, units)
            if period_bar:
                return period_bar
        for metric in ranked:
            period, obs = self.cross(metric)
            obs = self.legible(obs)
            if len(obs) < 2:
                continue
            if sort_desc:
                obs = sorted(obs, key=lambda o: -o.value)
            name = self.series_name(metric, period)
            chart = self.base(ctype, title_tpl.format(metric=name), obs, x_label="Entity",
                              y_label=self.y_label(metric), unit=self.unit(metric))
            chart["series"] = [{"name": name, "data": [self.point(o.entity, o) for o in obs]}]
            self.mark(metric)
            omitted = len(self.sections[metric][1]) - len(obs)
            note = f" {omitted} smaller peer{'s' if omitted != 1 else ''} omitted for axis legibility." if omitted > 0 else ""
            return chart, f"{name} across {len(obs)} entities, plotted from reported figures.{note}{_cite(chart['source'])}"
        raise Unsupported("no metric reported for two or more entities")

    def _requested_period_bar(self, ctype: str, units: set[str] | None = None) -> tuple[dict, str] | None:
        trends = self.trend_metrics(min_periods=2, min_entities=1)
        # The line chart takes the top-ranked trend; prefer a requested metric
        # from a different family so the two visuals are not redundant.
        line_family = _metric_family(trends[0][0]) if trends else ""
        trends = sorted(trends, key=lambda item: (_metric_family(item[0]) == line_family,))
        for metric, periods, ents in trends:
            if metric not in self.requested_metrics or metric.startswith("x_"):
                continue
            if units is not None and self.unit(metric) not in units:
                continue
            key = max(ents, key=lambda k: len(ents[k]))
            obs = [ents[key][p] for p in periods if p in ents[key]]
            if len(obs) < 2:
                continue
            entity = obs[0].entity
            title = f"{self.label(metric)} by period — {entity}"
            chart = self.base(ctype, title, obs, x_label="Period", y_label=self.y_label(metric), unit=self.unit(metric))
            chart["series"] = [{"name": entity, "data": [self.point(o.period, o) for o in obs]}]
            self.mark(metric)
            return chart, (f"{self.label(metric)} for {entity} across {len(obs)} reported periods "
                           f"({obs[0].period} to {obs[-1].period}).{_cite(chart['source'])}")
        return None

    def _b_bar(self):
        return self._single_metric_bar("bar")

    def _b_horizontal_bar(self):
        return self._single_metric_bar("horizontal_bar", prefer_many=True, title_tpl="{metric} — ranked")

    def _b_dot_plot(self):
        return self._single_metric_bar("dot_plot", units={"%", "x"}, title_tpl="{metric} — dot plot")

    def _b_lollipop(self):
        return self._single_metric_bar("lollipop", prefer_many=True, title_tpl="{metric} — lollipop ranking")

    def _b_grouped_bar(self):
        # Preferred: one metric, entities × periods.
        for metric, periods, ents in self.trend_metrics(min_periods=2, min_entities=2):
            periods = periods[-4:]
            obs = [ents[k][p] for k in ents for p in periods if p in ents[k]]
            chart = self.base("grouped_bar", f"{self.label(metric)} by entity and period", obs, x_label="Entity",
                              y_label=self.y_label(metric), unit=self.unit(metric))
            chart["series"] = [{"name": p, "data": [self.point(ents[k][p].entity, ents[k][p]) for k in ents if p in ents[k]]}
                               for p in periods]
            self.mark(metric)
            return chart, f"{self.label(metric)} for {len(ents)} entities across {len(periods)} reported periods ({', '.join(periods)}).{_cite(chart['source'])}"
        # Fallback: two or more metrics sharing a unit across the same entities.
        for unit in ("%", "₹ crore", "x"):
            metrics = self.rank_metrics(units={unit}, min_entities=2, targets_only=True)[:4]
            if len(metrics) < 2:
                continue
            common = None
            for m in metrics:
                ents = {o.entity_key for o in self.cross(m, targets_only=True)[1]}
                common = ents if common is None else common & ents
            if not common or len(common) < 2:
                continue
            obs_all = []
            series = []
            for m in metrics:
                period, obs = self.cross(m, targets_only=True)
                obs = [o for o in obs if o.entity_key in common]
                obs_all += obs
                series.append({"name": self.series_name(m, period), "data": [self.point(o.entity, o) for o in obs]})
            chart = self.base("grouped_bar", f"Peer comparison of {len(metrics)} metrics ({_unit_label(unit)})", obs_all,
                              x_label="Entity", y_label=_unit_label(unit), unit=unit)
            chart["series"] = series
            self.mark(*metrics)
            return chart, f"Side-by-side comparison of {len(metrics)} reported metrics sharing one unit.{_cite(chart['source'])}"
        raise Unsupported("needs one metric over two periods, or two metrics in one unit, for two or more entities")

    def _b_stacked_bar(self):
        for metric, periods, ents in self.trend_metrics(min_periods=2, min_entities=2):
            if self.unit(metric) not in {"₹ crore", "US$ million"}:
                continue
            periods = periods[-6:]
            obs = [ents[k][p] for k in ents for p in periods]
            chart = self.base("stacked_bar", f"{self.label(metric)} — combined peer total by period", obs,
                              x_label="Period", y_label=self.y_label(metric), unit=self.unit(metric))
            chart["series"] = [{"name": next(iter(ents[k].values())).entity,
                                "data": [self.point(p, ents[k][p]) for p in periods]} for k in ents]
            self.mark(metric)
            return chart, f"Each bar stacks the reported {self.label(metric).lower()} of {len(ents)} entities for a period.{_cite(chart['source'])}"
        metrics = self.money_metrics(min_entities=2, targets_only=True)[:4]
        if len(metrics) >= 1:
            ents = None
            for m in metrics:
                keys = {o.entity_key for o in self.cross(m, targets_only=True, nonneg=True)[1]}
                ents = keys if ents is None else ents & keys
            metrics = [m for m in metrics if ents]
            if ents and len(ents) >= 2:
                obs_all, series = [], []
                for ek in sorted(ents):
                    data = []
                    for m in metrics:
                        period, obs = self.cross(m, targets_only=True)
                        o = next(x for x in obs if x.entity_key == ek)
                        obs_all.append(o)
                        data.append(self.point(self.series_name(m, period), o))
                    series.append({"name": self.ds.entity_display(ek), "data": data})
                chart = self.base("stacked_bar", "Combined peer totals by metric", obs_all, x_label="Metric",
                                  y_label="₹ crore", unit="₹ crore")
                chart["series"] = series
                self.mark(*metrics)
                return chart, f"Each bar stacks the reported amount of every entity for one metric.{_cite(chart['source'])}"
        raise Unsupported("needs an additive amount metric for two or more entities")

    def _b_bullet(self):
        for metric in self.rank_metrics(min_entities=3, targets_only=True):
            period, obs = self.cross(metric, targets_only=True)
            if len(obs) < 3:
                continue
            avg = statistics.fmean(o.value for o in obs)
            chart = self.base("bullet", f"{self.series_name(metric, period)} vs peer average", obs,
                              unit=self.unit(metric), basis="reported",
                              note="bars are reported values; the target marker is the simple average of the plotted entities")
            chart["series"] = [{"name": self.series_name(metric, period),
                                "data": [self.point(o.entity, o, target=round(avg, 4)) for o in obs]}]
            self.mark(metric)
            return chart, f"Each bar is a reported value; the marker is the peer average of the {len(obs)} entities shown.{_cite(chart['source'])}"
        raise Unsupported("needs one metric reported for three or more requested entities")

    def _b_population_pyramid(self):
        # Two periods of one metric (older on the left, newer on the right).
        for metric, periods, ents in self.trend_metrics(min_periods=2, min_entities=2):
            older, newer = periods[-2], periods[-1]
            rows = [(k, v) for k, v in ents.items() if older in v and newer in v]
            if len(rows) < 2:
                continue
            obs = [v[older] for _, v in rows] + [v[newer] for _, v in rows]
            chart = self.base("population_pyramid", f"{self.label(metric)}: {older} (left) vs {newer} (right)", obs,
                              x_label=self.y_label(metric), y_label="Entity", unit=self.unit(metric))
            chart["series"] = [{"name": f"{older} | {newer}", "data": [
                {"label": v[newer].entity, "left": v[older].value, "right": v[newer].value,
                 "evidenceSource": v[newer].source_id, "evidenceQuote": v[newer].quote[:400]} for _, v in rows]}]
            self.mark(metric)
            return chart, f"Mirrored bars compare {older} (left) with {newer} (right) for each entity.{_cite(chart['source'])}"
        # Two related growth/ratio metrics in the same unit.
        metrics = self.ratio_metrics(min_entities=2, targets_only=True)
        for i, a in enumerate(metrics):
            for b in metrics[i + 1:]:
                pa, oa = self.cross(a, targets_only=True)
                pb, ob = self.cross(b, targets_only=True)
                common = {o.entity_key for o in oa} & {o.entity_key for o in ob}
                if len(common) < 2:
                    continue
                ma = {o.entity_key: o for o in oa}
                mb = {o.entity_key: o for o in ob}
                obs = [ma[k] for k in common] + [mb[k] for k in common]
                chart = self.base("population_pyramid", f"{self.label(a)} (left) vs {self.label(b)} (right)", obs,
                                  x_label="%", y_label="Entity", unit="%")
                chart["series"] = [{"name": f"{self.label(a)} | {self.label(b)}", "data": [
                    {"label": ma[k].entity, "left": ma[k].value, "right": mb[k].value,
                     "evidenceSource": ma[k].source_id, "evidenceQuote": ma[k].quote[:400]} for k in sorted(common)]}]
                self.mark(a, b)
                return chart, f"Mirrored bars contrast two reported percentage metrics per entity.{_cite(chart['source'])}"
        raise Unsupported("needs two comparable measures (two periods or two metrics) for two or more entities")

    def _b_radar(self):
        metrics = self.rank_metrics(units={"%"}, min_entities=2, targets_only=True)
        chosen, common = [], None
        for m in metrics:
            ents = {o.entity_key for o in self.cross(m, targets_only=True)[1]}
            nxt = ents if common is None else common & ents
            if len(nxt) >= 2:
                chosen.append(m)
                common = nxt
            if len(chosen) >= 7:
                break
        basis = "reported"
        if len(chosen) < 3:
            # Normalised profile across mixed-unit metrics (derived, 0–100).
            metrics = self.rank_metrics(min_entities=2, targets_only=True)
            chosen, common = [], None
            for m in metrics:
                ents = {o.entity_key for o in self.cross(m, targets_only=True)[1]}
                nxt = ents if common is None else common & ents
                if len(nxt) >= 2:
                    chosen.append(m)
                    common = nxt
                if len(chosen) >= 7:
                    break
            basis = "derived"
        if len(chosen) < 3 or not common:
            raise Unsupported("needs three or more metrics reported for the same two or more entities")
        obs_all = []
        series = []
        for ek in sorted(common):
            data = []
            for m in chosen:
                period, obs = self.cross(m, targets_only=True)
                o = next(x for x in obs if x.entity_key == ek)
                obs_all.append(o)
                if basis == "reported":
                    data.append(self.point(self.label(m), o))
                else:
                    vals = [x.value for x in obs if x.entity_key in common]
                    lo, hi = min(vals), max(vals)
                    score = 50.0 if hi == lo else (o.value - lo) / (hi - lo) * 100
                    data.append({"label": self.label(m), "value": round(score, 2), "derived": True})
            series.append({"name": self.ds.entity_display(ek), "data": data})
        title = "Multi-metric profile (percent metrics)" if basis == "reported" else "Multi-metric profile (min–max scaled 0–100)"
        chart = self.base("radar", title, obs_all, unit="%" if basis == "reported" else "score", basis=basis,
                          note="reported percentages" if basis == "reported" else "each metric rescaled 0–100 across the plotted entities")
        chart["series"] = series
        if basis == "derived":
            chart["derivedFrom"] = self.derived_inputs(obs_all)
        self.mark(*chosen)
        return chart, f"Profile of {len(common)} entities on {len(chosen)} metrics ({'reported values' if basis == 'reported' else 'scaled 0–100 from reported values'}).{_cite(chart['source'])}"

    def _b_heatmap(self):
        for basis, units in (("reported", {"%"}), ("derived", None)):
            metrics = self.rank_metrics(units=units, min_entities=2, targets_only=False)
            chosen, rows = [], None
            for m in metrics:
                ents = {o.entity_key for o in self.cross(m)[1] if not o.is_aggregate}
                nxt = ents if rows is None else rows & ents
                if len(nxt) >= 2:
                    chosen.append(m)
                    rows = nxt
                if len(chosen) >= 8:
                    break
            if len(chosen) < 2 or not rows:
                continue
            entities = sorted(rows, key=lambda k: self.ds.entity_display(k))
            values, obs_all = [], []
            for ek in entities:
                row = []
                for m in chosen:
                    obs = self.cross(m)[1]
                    o = next(x for x in obs if x.entity_key == ek)
                    obs_all.append(o)
                    if basis == "reported":
                        row.append(o.value)
                    else:
                        vals = [x.value for x in obs if x.entity_key in rows]
                        lo, hi = min(vals), max(vals)
                        row.append(round(50.0 if hi == lo else (o.value - lo) / (hi - lo) * 100, 1))
                values.append(row)
            flat = [v for r in values for v in r]
            if len(set(flat)) <= 1:
                continue
            chart = self.base("heatmap", "Entity × metric heatmap" + (" (percent metrics)" if basis == "reported" else " (scaled 0–100)"),
                              obs_all, unit="%" if basis == "reported" else "score", basis=basis,
                              note="cell values are reported percentages" if basis == "reported" else "each column rescaled 0–100 across entities")
            chart["rows"] = [self.ds.entity_display(k) for k in entities]
            chart["columns"] = [self.label(m) for m in chosen]
            chart["values"] = values
            if basis == "derived":
                chart["derivedFrom"] = self.derived_inputs(obs_all)
            self.mark(*chosen)
            return chart, f"{len(entities)} entities × {len(chosen)} metrics.{_cite(chart['source'])}"
        raise Unsupported("needs two or more metrics covering the same two or more entities")

    # Trends
    def _trend(self, ctype: str, *, additive: bool = False, title_suffix: str = "trend") -> tuple[dict, str]:
        for metric, periods, ents in self.trend_metrics(min_periods=2, min_entities=1):
            if additive and self.unit(metric) not in {"₹ crore", "US$ million"}:
                continue
            obs = [ents[k][p] for k in ents for p in periods]
            chart = self.base(ctype, f"{self.label(metric)} — {title_suffix}", obs, x_label="Period",
                              y_label=self.y_label(metric), unit=self.unit(metric))
            chart["series"] = [{"name": next(iter(ents[k].values())).entity,
                                "data": [self.point(p, ents[k][p]) for p in periods]} for k in ents]
            self.mark(metric)
            return chart, f"{self.label(metric)} over {len(periods)} reported periods ({periods[0]} to {periods[-1]}) for {len(ents)} entit{'y' if len(ents) == 1 else 'ies'}.{_cite(chart['source'])}"
        raise Unsupported("needs a metric reported for two or more periods")

    def _b_line(self):
        return self._trend("line")

    def _b_spline(self):
        return self._trend("spline", title_suffix="smoothed trend")

    def _b_step_line(self):
        return self._trend("step_line", title_suffix="period-to-period steps")

    def _b_area(self):
        return self._trend("area", title_suffix="area trend")

    def _b_stacked_area(self):
        try:
            return self._trend("stacked_area", additive=True, title_suffix="stacked peer total")
        except Unsupported:
            raise Unsupported("needs an additive amount metric reported for two or more periods")

    def _b_sparkline(self):
        for metric, periods, ents in self.trend_metrics(min_periods=4, min_entities=1):
            key = next(iter(ents))
            obs = [ents[key][p] for p in periods]
            chart = self.base("sparkline", f"{obs[0].entity} {self.label(metric)} sparkline", obs, x_label="Period",
                              y_label=self.y_label(metric), unit=self.unit(metric))
            chart["series"] = [{"name": obs[0].entity, "data": [self.point(p, ents[key][p]) for p in periods]}]
            self.mark(metric)
            return chart, f"Compact trend of {len(periods)} reported periods.{_cite(chart['source'])}"
        raise Unsupported("needs four or more reported periods for one entity")

    def _b_combo(self):
        bars = self.money_metrics(min_entities=2, targets_only=True)
        lines = self.rank_metrics(units={"%", "x"}, min_entities=2, targets_only=True)
        for b in bars:
            pb, ob = self.cross(b, targets_only=True)
            for l in lines:
                pl, ol = self.cross(l, targets_only=True)
                common = [o.entity_key for o in ob if o.entity_key in {x.entity_key for x in ol}]
                if len(common) < 2:
                    continue
                mb = {o.entity_key: o for o in ob}
                ml = {o.entity_key: o for o in ol}
                obs = [mb[k] for k in common] + [ml[k] for k in common]
                chart = self.base("combo", f"{self.label(b)} (bars) with {self.label(l)} (line)", obs, x_label="Entity",
                                  y_label=self.y_label(b), unit=self.unit(b))
                chart["secondaryUnit"] = self.unit(l)
                chart["secondaryLabel"] = self.y_label(l)
                chart["series"] = [
                    {"name": self.series_name(b, pb), "data": [self.point(mb[k].entity, mb[k]) for k in common]},
                    {"name": self.series_name(l, pl), "data": [self.point(ml[k].entity, ml[k]) for k in common]},
                ]
                self.mark(b, l)
                return chart, f"Bars show {self.label(b).lower()}; the line (right axis) shows {self.label(l).lower()}.{_cite(chart['source'])}"
        raise Unsupported("needs an amount metric and a ratio metric for the same two or more entities")

    def _b_control_chart(self):
        for metric, periods, ents in self.trend_metrics(min_periods=3, min_entities=1):
            key = next(iter(ents))
            obs = [ents[key][p] for p in periods]
            vals = [o.value for o in obs]
            mean, sd = statistics.fmean(vals), statistics.pstdev(vals)
            chart = self.base("control_chart", f"{obs[0].entity} {self.label(metric)} — control limits", obs,
                              x_label="Period", y_label=self.y_label(metric), unit=self.unit(metric),
                              note="points are reported values; limits are mean ± 2σ of the plotted points")
            chart["series"] = [{"name": obs[0].entity, "data": [
                self.point(p, ents[key][p], ucl=round(mean + 2 * sd, 4), lcl=round(mean - 2 * sd, 4)) for p in periods]}]
            self.mark(metric)
            return chart, f"Reported values with mean ± 2σ control limits computed from the same points.{_cite(chart['source'])}"
        for metric in self.rank_metrics(min_entities=4):
            period, obs = self.cross(metric)
            if len(obs) < 4:
                continue
            vals = [o.value for o in obs]
            mean, sd = statistics.fmean(vals), statistics.pstdev(vals)
            chart = self.base("control_chart", f"{self.series_name(metric, period)} — peer control limits", obs,
                              x_label="Entity", y_label=self.y_label(metric), unit=self.unit(metric),
                              note="points are reported values; limits are mean ± 2σ across the plotted peers")
            chart["series"] = [{"name": self.series_name(metric, period), "data": [
                self.point(o.entity, o, ucl=round(mean + 2 * sd, 4), lcl=round(mean - 2 * sd, 4)) for o in obs]}]
            self.mark(metric)
            return chart, f"Each entity's reported value against peer mean ± 2σ limits.{_cite(chart['source'])}"
        raise Unsupported("needs three or more periods, or four or more peers, for one metric")

    def _b_error_bar(self):
        groups = self.ds.grouped()
        by_metric: dict[str, list[tuple[Observation, float, float]]] = {}
        for (ek, metric, period), items in groups.items():
            items = [o for o in items if not o.suspect]
            values = {round(o.value, 6) for o in items}
            if len(values) < 2:
                continue
            best = max(items, key=EvidenceDataset._rank)
            by_metric.setdefault(metric, []).append((best, min(values), max(values)))
        for metric, rows in sorted(by_metric.items(), key=lambda kv: (self.usage.get(kv[0], 0), -len(kv[1]))):
            if len(rows) < 2:
                continue
            obs = [r[0] for r in rows]
            chart = self.base("error_bar", f"{self.label(metric)} — range across sources", obs, x_label="Entity",
                              y_label=self.y_label(metric), unit=self.unit(metric),
                              note="point = preferred reported value; whiskers = lowest and highest value reported by different sources")
            chart["series"] = [{"name": self.label(metric), "data": [
                self.point(o.entity, o, low=lo, high=hi) for o, lo, hi in rows]}]
            chart["derivedFrom"] = self.derived_inputs([o for (ek, m, p), items in groups.items() if m == metric for o in items if not o.suspect])
            self.mark(metric)
            return chart, f"Whiskers span the different values that separate sources report for the same entity and metric.{_cite(chart['source'])}"
        # Range over reported periods instead.
        for metric, periods, ents in self.trend_metrics(min_periods=2, min_entities=2):
            rows = []
            for k, v in ents.items():
                vals = [v[p].value for p in periods]
                rows.append((v[periods[-1]], min(vals), max(vals)))
            obs = [o for v in ents.values() for o in v.values()]
            chart = self.base("error_bar", f"{self.label(metric)} — latest value and period range", obs, x_label="Entity",
                              y_label=self.y_label(metric), unit=self.unit(metric),
                              note="point = latest reported period; whiskers = min/max across reported periods")
            chart["series"] = [{"name": self.label(metric), "data": [self.point(o.entity, o, low=lo, high=hi) for o, lo, hi in rows]}]
            self.mark(metric)
            return chart, f"Latest value with the range across {len(periods)} reported periods.{_cite(chart['source'])}"
        raise Unsupported("needs conflicting source values or multiple periods for two or more entities")

    def _ohlc(self, ctype: str, index: int):
        rows = [r for r in (self.extra.get("ohlc") or []) if isinstance(r, dict) and len(r.get("bars") or []) >= 2]
        if not rows:
            raise Unsupported("needs open/high/low/close price sessions; none were retrieved for these entities")
        row = rows[min(index, len(rows) - 1)]
        bars = row["bars"][-26:]
        chart = {
            "type": ctype, "title": f"{row.get('label', 'Price')} — {'weekly ' if row.get('interval') == '1wk' else ''}price sessions",
            "xLabel": "Session", "yLabel": "Price (₹)", "unit": "₹", "evidenceBasis": "market", "gallery": True,
            "basisNote": f"market price data ({row.get('provider', 'Yahoo Finance chart API')}, symbol {row.get('symbol', '')})",
            "source": [row["sourceId"]] if row.get("sourceId") else [],
            "series": [{"name": row.get("label", "Price"), "data": [
                {"label": b["date"], "open": b["open"], "high": b["high"], "low": b["low"], "close": b["close"]} for b in bars]}],
        }
        return chart, f"{len(bars)} {'weekly' if row.get('interval') == '1wk' else ''} sessions of open, high, low and close prices for {row.get('label')}.{_cite(chart['source'])}"

    def _b_candlestick(self):
        return self._ohlc("candlestick", 0)

    def _b_ohlc(self):
        return self._ohlc("ohlc", 1)

    # Composition / ranking
    def _composition(self, ctype: str, *, min_items: int = 2, prefer_many: bool = False, title_tpl: str = "Share of combined {metric}") -> tuple[dict, str]:
        metrics = self.money_metrics(min_entities=min_items)
        if not metrics:
            metrics = self.rank_metrics(units={"count"}, min_entities=min_items)
        if prefer_many:
            metrics = sorted(metrics, key=lambda m: (m.startswith("x_"), self.usage.get(m, 0), m in _COMPONENT_METRICS, -len(self.sections[m][1])))
        for metric in metrics:
            period, obs = self.cross(metric, nonneg=True)
            obs = [o for o in obs if not o.is_aggregate and o.value > 0]
            if not prefer_many:
                tgt = [o for o in obs if o.is_target]
                obs = tgt if len(tgt) >= min_items else obs
            obs = sorted(obs, key=lambda o: -o.value)[:12]
            if len(obs) < min_items:
                continue
            name = self.series_name(metric, period)
            chart = self.base(ctype, title_tpl.format(metric=name), obs, unit=self.unit(metric))
            chart["series"] = [{"name": name, "data": [self.point(o.entity, o) for o in obs]}]
            self.mark(metric)
            return chart, f"Slices are the reported {self.label(metric).lower()} of {len(obs)} entities; shares are of their combined total.{_cite(chart['source'])}"
        raise Unsupported(f"needs an additive amount reported for {min_items} or more entities")

    def _b_pie(self):
        return self._composition("pie")

    def _b_doughnut(self):
        return self._composition("doughnut")

    def _b_polar_area(self):
        return self._composition("polar_area", title_tpl="{metric} — polar comparison")

    def _b_treemap(self):
        return self._composition("treemap", min_items=3, prefer_many=True, title_tpl="{metric} — treemap")

    def _b_sunburst(self):
        return self._composition("sunburst", title_tpl="{metric} — sunburst share")

    def _b_pictogram(self):
        chart, caption = self._composition("pictogram", title_tpl="{metric} — pictogram")
        chart["xLabel"] = "Icons scaled to the largest entity"
        return chart, caption

    def _b_pareto(self):
        chart, caption = self._composition("pareto", min_items=3, prefer_many=True, title_tpl="{metric} — Pareto ranking")
        chart["xLabel"], chart["yLabel"] = "Entity", self.y_label(next(m for m in self.sections if self.series_name(m, self.sections[m][0]) == chart["series"][0]["name"]))
        return chart, caption.replace("Slices are", "Bars are") + " The line is the cumulative share."

    def _b_funnel(self):
        chart, caption = self._composition("funnel", min_items=3, prefer_many=True, title_tpl="{metric} — ranked funnel")
        return chart, caption.replace("Slices are", "Bands are")

    def _b_funnel_pyramid(self):
        chart, caption = self._composition("funnel_pyramid", min_items=3, title_tpl="{metric} — ranked pyramid")
        chart["series"][0]["data"] = list(reversed(chart["series"][0]["data"]))
        return chart, caption.replace("Slices are", "Tiers are")

    def _b_waterfall(self):
        for metric in self.money_metrics(min_entities=2):
            period, obs = self.cross(metric, nonneg=True)
            tgt = [o for o in obs if o.is_target and not o.is_aggregate]
            obs = tgt if len(tgt) >= 2 else [o for o in obs if not o.is_aggregate]
            obs = sorted(obs, key=lambda o: -o.value)[:8]
            if len(obs) < 2:
                continue
            total = sum(o.value for o in obs)
            name = self.series_name(metric, period)
            chart = self.base("waterfall", f"{name} — build-up to the combined total", obs, x_label="Entity",
                              y_label=self.y_label(metric), unit=self.unit(metric),
                              note="steps are reported values; the final bar is their sum")
            chart["series"] = [{"name": name, "data": [self.point(o.entity, o) for o in obs] +
                                [{"label": "Combined total", "value": round(total, 2), "isTotal": True, "derived": True}]}]
            chart["derivedFrom"] = self.derived_inputs(obs)
            self.mark(metric)
            return chart, f"Each step adds one entity's reported {self.label(metric).lower()}; the last bar is the computed combined total.{_cite(chart['source'])}"
        raise Unsupported("needs an additive amount reported for two or more entities")

    def _b_marimekko(self):
        widths = self.money_metrics(min_entities=2, targets_only=True)
        heights = self.rank_metrics(units={"%", "x"}, min_entities=2, targets_only=True)
        for w in widths:
            pw, ow = self.cross(w, targets_only=True, nonneg=True)
            for h in heights:
                ph, oh = self.cross(h, targets_only=True)
                mh = {o.entity_key: o for o in oh}
                rows = [(o, mh[o.entity_key]) for o in ow if o.entity_key in mh and o.value > 0]
                if len(rows) < 2:
                    continue
                obs = [a for a, _ in rows] + [b for _, b in rows]
                chart = self.base("marimekko", f"Width = {self.label(w)}, height = {self.label(h)}", obs,
                                  unit=self.unit(h))
                chart["series"] = [{"name": self.series_name(h, ph), "data": [
                    self.point(b.entity, b, width=a.value) for a, b in rows]}]
                self.mark(w, h)
                return chart, f"Column width is reported {self.label(w).lower()}; height is reported {self.label(h).lower()}.{_cite(chart['source'])}"
        raise Unsupported("needs an amount metric and a ratio metric for the same two or more entities")

    # Distribution / relationship
    def _pairs(self, k: int, min_common: int = 4):
        metrics = [m for m in self.rank_metrics(min_entities=min_common, prefer_unused=False)]
        best = None
        for i, a in enumerate(metrics):
            for b in metrics[i + 1:]:
                for c in (metrics if k == 3 else [None]):
                    if c in {a, b}:
                        continue
                    sets = [{o.entity_key for o in self.cross(m)[1] if not o.is_aggregate} for m in (a, b, c) if m]
                    common = set.intersection(*sets)
                    if len(common) < min_common:
                        continue
                    score = (len(common), -(self.usage.get(a, 0) + self.usage.get(b, 0)))
                    if best is None or score > best[0]:
                        best = (score, [m for m in (a, b, c) if m], common)
                if k == 2 and best and best[0][0] >= 8:
                    break
        return best

    def _b_scatter(self):
        found = self._pairs(2)
        if not found:
            raise Unsupported("needs two metrics reported for the same four or more entities")
        _, (a, b), common = found
        series, obs_all = [], []
        for m in (a, b):
            period, obs = self.cross(m)
            obs = sorted([o for o in obs if o.entity_key in common], key=lambda o: o.entity)
            obs_all += obs
            series.append({"name": self.series_name(m, period), "data": [self.point(o.entity, o) for o in obs]})
        chart = self.base("scatter", f"{self.label(a)} vs {self.label(b)}", obs_all, x_label=self.y_label(a),
                          y_label=self.y_label(b), unit=self.unit(b))
        chart["series"] = series
        self.mark(a, b)
        return chart, f"Each point is one of {len(common)} entities, positioned by two reported metrics.{_cite(chart['source'])}"

    def _b_bubble(self):
        found = self._pairs(3)
        if not found:
            raise Unsupported("needs three metrics reported for the same four or more entities")
        _, metrics, common = found
        series, obs_all = [], []
        for m in metrics:
            period, obs = self.cross(m)
            obs = sorted([o for o in obs if o.entity_key in common], key=lambda o: o.entity)
            obs_all += obs
            series.append({"name": self.series_name(m, period), "data": [self.point(o.entity, o) for o in obs]})
        chart = self.base("bubble", f"{self.label(metrics[0])} vs {self.label(metrics[1])} (size: {self.label(metrics[2])})",
                          obs_all, x_label=self.y_label(metrics[0]), y_label=self.y_label(metrics[1]), unit=self.unit(metrics[1]))
        chart["series"] = series
        self.mark(*metrics)
        return chart, f"Position shows two reported metrics; bubble area shows a third, for {len(common)} entities.{_cite(chart['source'])}"

    def _distribution_values(self, min_values: int = 4) -> list[tuple[str, list[Observation]]]:
        out = []
        for metric in self.ds.metrics():
            obs = [o for o in self.best.values() if o.metric == metric and not o.is_aggregate and o.unit == self.unit(metric)]
            if len({round(o.value, 6) for o in obs}) >= min_values:
                out.append((metric, obs))
        return sorted(out, key=lambda kv: (1 if kv[0].startswith("x_") else 0, self.usage.get(kv[0], 0),
                                           0 if kv[0] in self.requested_metrics else 1, -len(kv[1])))

    def _b_histogram(self):
        for metric, obs in self._distribution_values(5):
            vals = sorted(o.value for o in obs)
            bins = max(4, min(8, int(math.ceil(math.log2(len(vals)) + 1))))
            lo, hi = vals[0], vals[-1]
            if hi == lo:
                continue
            width = (hi - lo) / bins
            counts = [0] * bins
            for v in vals:
                counts[min(bins - 1, int((v - lo) / width))] += 1
            if len(set(counts)) <= 1:
                continue
            data = [{"label": f"{_fmt(lo + i * width)}–{_fmt(lo + (i + 1) * width)}", "value": c, "derived": True}
                    for i, c in enumerate(counts)]
            chart = self.base("histogram", f"Distribution of {self.label(metric)} observations", obs,
                              x_label=f"{self.label(metric)} ({_unit_label(self.unit(metric))})", y_label="Observation count",
                              unit="count", basis="derived", note="bar heights count reported observations in each value band")
            chart["series"] = [{"name": "Observations", "data": data}]
            chart["derivedFrom"] = self.derived_inputs(obs)
            self.mark(metric)
            return chart, f"{len(vals)} reported observations grouped into {bins} equal-width bands.{_cite(chart['source'])}"
        raise Unsupported("needs five or more distinct observations of one metric")

    def _b_boxplot(self):
        rows = []
        inputs = []
        for metric, obs in self._distribution_values(4)[:8]:
            vals = sorted(o.value for o in obs)
            q = statistics.quantiles(vals, n=4, method="inclusive")
            rows.append({"label": f"{self.label(metric)} ({_unit_label(self.unit(metric))})", "min": vals[0], "q1": round(q[0], 4),
                         "median": round(q[1], 4), "q3": round(q[2], 4), "max": vals[-1], "derived": True})
            inputs += obs
            self.mark(metric)
            if len(rows) >= 6:
                break
        if not rows:
            raise Unsupported("needs four or more distinct observations of one metric")
        chart = self.base("boxplot", "Spread of reported observations by metric", inputs, x_label="Metric",
                          y_label="Value (unit per metric)", basis="derived",
                          note="five-number summaries (min, quartiles, max) of reported observations")
        chart["series"] = [{"name": "Five-number summary", "data": rows}]
        chart["derivedFrom"] = self.derived_inputs(inputs)
        return chart, f"Quartiles of {len(inputs)} reported observations across {len(rows)} metrics.{_cite(chart['source'])}"

    def _density_like(self, ctype: str) -> tuple[dict, str]:
        candidates = self._distribution_values(3)
        if ctype == "ridgeline":
            groups = [(m, obs) for m, obs in candidates if self.unit(m) == "%"][:5] or candidates[:5]
            if len(groups) < 2:
                raise Unsupported("needs two or more metrics with three or more observations each")
            obs_all = [o for _, obs in groups for o in obs]
            chart = self.base("ridgeline", "Ridgeline of reported distributions", obs_all, x_label="Value range",
                              y_label="Density", basis="derived", note="kernel-free density bands of reported observations")
            chart["series"] = [{"name": self.label(m), "data": [{"label": self.label(m), "values": [o.value for o in obs]}]}
                               for m, obs in groups]
            chart["derivedFrom"] = self.derived_inputs(obs_all)
            self.mark(*[m for m, _ in groups])
            return chart, f"Distribution shapes for {len(groups)} metrics from {len(obs_all)} reported observations.{_cite(chart['source'])}"
        if not candidates:
            raise Unsupported("needs three or more distinct observations of one metric")
        metric, obs = candidates[0]
        chart = self.base(ctype, f"{self.label(metric)} — {'violin' if ctype == 'violin' else 'density'} of observations", obs,
                          x_label="Value range", y_label="Density", unit=self.unit(metric), basis="derived",
                          note="shape estimated from binned reported observations")
        chart["series"] = [{"name": self.label(metric), "data": [{"label": self.label(metric), "values": [o.value for o in obs]}]}]
        chart["derivedFrom"] = self.derived_inputs(obs)
        self.mark(metric)
        return chart, f"Shape of {len(obs)} reported {self.label(metric).lower()} observations.{_cite(chart['source'])}"

    def _b_violin(self):
        return self._density_like("violin")

    def _b_density(self):
        return self._density_like("density")

    def _b_ridgeline(self):
        return self._density_like("ridgeline")

    # Indicators
    def _headline(self) -> tuple[str, str, Observation, list[Observation]]:
        for metric in self.rank_metrics(min_entities=1, targets_only=True, prefer_unused=False):
            period, obs = self.cross(metric, targets_only=True)
            if obs:
                lead = max(obs, key=lambda o: o.value)
                return metric, period, lead, self.cross(metric)[1]
        raise Unsupported("no reported metric for the requested entities")

    def _b_kpi(self):
        metric, period, lead, peers = self._headline()
        top = max(o.value for o in peers)
        chart = self.base("kpi", f"KPI — {lead.entity} {self.series_name(metric, period)}", [lead], unit=self.unit(metric) if self.unit(metric) in {"%", "x"} else "",
                          note="reported value; bar scaled to the highest peer value")
        chart["min"], chart["max"] = 0, round(top, 4) if top > 0 else 1
        chart["series"] = [{"name": self.series_name(metric, period), "data": [self.point(lead.entity, lead)]}]
        return chart, f"Headline figure for the leading entity on the most-covered requested metric.{_cite(chart['source'])}"

    def _b_gauge(self):
        for metric in self.rank_metrics(units={"%"}, min_entities=2, targets_only=True):
            period, obs = self.cross(metric, targets_only=True)
            lead = obs[0]
            top = max(o.value for o in self.cross(metric)[1])
            scale = 10 ** math.floor(math.log10(top)) if top > 0 else 1
            maximum = math.ceil(top / scale) * scale if top > 0 else 100
            chart = self.base("gauge", f"{lead.entity} {self.series_name(metric, period)}", [lead], unit="%",
                              note="reported value on a scale up to the rounded-up peer maximum")
            chart["min"], chart["max"] = 0, maximum
            chart["series"] = [{"name": self.series_name(metric, period), "data": [self.point(lead.entity, lead)]}]
            self.mark(metric)
            return chart, f"Gauge scale runs to the rounded-up highest peer value.{_cite(chart['source'])}"
        raise Unsupported("needs a percentage metric for the requested entities")

    def _b_progress(self):
        for metric in self.money_metrics(min_entities=2, targets_only=True):
            period, obs = self.cross(metric, targets_only=True, nonneg=True)
            total = sum(o.value for o in obs)
            if total <= 0 or len(obs) < 2:
                continue
            lead = max(obs, key=lambda o: o.value)
            share = round(lead.value / total * 100, 1)
            chart = self.base("progress", f"{lead.entity}'s share of combined {self.series_name(metric, period).lower()}", obs,
                              unit="%", basis="derived", note="share computed from reported values of the plotted entities")
            chart["min"], chart["max"] = 0, 100
            chart["series"] = [{"name": "Share of combined total", "data": [{"label": lead.entity, "value": share, "derived": True}]}]
            chart["derivedFrom"] = self.derived_inputs(obs)
            self.mark(metric)
            return chart, f"Share of the combined total across {len(obs)} requested entities.{_cite(chart['source'])}"
        raise Unsupported("needs an additive amount for two or more requested entities")

    # Provenance / structure
    def _b_gantt(self):
        periods: dict[str, set[str]] = {}
        obs_for: dict[str, list[Observation]] = {}
        for o in self.best.values():
            if not o.period:
                continue
            rng = period_date_range(o.period)
            if not rng:
                continue
            periods.setdefault(o.period, set()).add(o.metric_label)
            obs_for.setdefault(o.period, []).append(o)
        if not periods:
            raise Unsupported("no observation carries a fiscal or calendar period")
        events = []
        for p in sorted(periods, key=lambda x: period_date_range(x)[0]):
            start, end = period_date_range(p)
            events.append({"label": p, "start": start, "end": end,
                           "description": f"{len(obs_for[p])} values · {', '.join(sorted(periods[p]))[:48]}"})
        chart = {"type": "gantt", "title": "Reporting periods covered by the extracted data", "xLabel": "Time",
                 "yLabel": "Reporting period", "events": events, "evidenceBasis": "provenance", "gallery": True,
                 "basisNote": "period spans follow the Indian fiscal year (April–March)",
                 "source": sorted({o.source_id for v in obs_for.values() for o in v}, key=_sid_sort)}
        return chart, f"Bars span each reporting period present in the evidence ({len(events)} periods).{_cite(chart['source'])}"

    def _b_timeline(self):
        events = []
        for sid, meta in sorted(self.ds.source_meta.items(), key=lambda kv: _sid_sort(kv[0])):
            published = str(meta.get("published") or "")
            if not re.match(r"\d{4}-\d{2}-\d{2}", published):
                continue
            if not any(o.source_id == sid for o in self.ds.observations):
                continue
            title = re.sub(r"\s+", " ", str(meta.get("title") or sid))[:60]
            events.append({"label": f"{sid} · {title[:28]}", "start": published[:10], "end": published[:10],
                           "description": title})
        if len(events) >= 2:
            chart = {"type": "timeline", "title": "Publication dates of the data sources", "xLabel": "Time",
                     "yLabel": "Source", "events": sorted(events, key=lambda e: e["start"]), "evidenceBasis": "provenance",
                     "gallery": True, "basisNote": "dates are each source's publication or page date",
                     "source": sorted({e["label"].split(" ")[0] for e in events}, key=_sid_sort)}
            return chart, f"When each of the {len(events)} dated sources was published.{_cite(chart['source'])}"
        # Fall back to period end-dates.
        ends = []
        for o in self.best.values():
            rng = period_date_range(o.period) if o.period else None
            if rng:
                ends.append((rng[1], o))
        uniq = {}
        for end, o in ends:
            uniq.setdefault(end, []).append(o)
        if len(uniq) < 2:
            raise Unsupported("needs two or more dated sources or reporting periods")
        events = [{"label": f"{os_[0].period} close", "start": end, "end": end,
                   "description": f"{len(os_)} values reported"} for end, os_ in sorted(uniq.items())]
        chart = {"type": "timeline", "title": "Reporting-period close dates in the evidence", "xLabel": "Time",
                 "yLabel": "Period", "events": events, "evidenceBasis": "provenance", "gallery": True,
                 "basisNote": "period end dates (Indian fiscal calendar)",
                 "source": sorted({o.source_id for _e, os_ in uniq.items() for o in os_}, key=_sid_sort)}
        return chart, f"Close dates of the {len(events)} reporting periods present in the data.{_cite(chart['source'])}"

    def _source_entity_links(self) -> dict[tuple[str, str], int]:
        links: dict[tuple[str, str], int] = {}
        for o in self.ds.observations:
            if not o.is_target:
                continue
            links[(o.source_id, o.entity)] = links.get((o.source_id, o.entity), 0) + 1
        return links

    def _b_sankey(self):
        links = self._source_entity_links()
        if len(links) < 2:
            raise Unsupported("needs observations for the requested entities from at least one source")
        top = sorted(links.items(), key=lambda kv: -kv[1])[:16]
        nodes = list(dict.fromkeys([s for (s, _e), _ in top] + [e for (_s, e), _ in top]))
        chart = {"type": "sankey", "title": "Observations flowing from each source to each entity",
                 "nodes": [{"id": n, "label": n} for n in nodes],
                 "links": [{"source": s, "target": e, "value": v} for (s, e), v in top],
                 "evidenceBasis": "provenance", "gallery": True, "basisNote": "link width = number of extracted values",
                 "source": sorted({s for (s, _e), _ in top}, key=_sid_sort)}
        return chart, f"Link width is the number of extracted values each source contributes per entity.{_cite(chart['source'])}"

    def _b_network(self):
        links = self._source_entity_links()
        if len(links) < 2:
            raise Unsupported("needs observations for the requested entities from at least one source")
        top = sorted(links.items(), key=lambda kv: -kv[1])[:20]
        nodes = list(dict.fromkeys([e for (_s, e), _ in top] + [s for (s, _e), _ in top]))
        chart = {"type": "network", "title": "Source–entity evidence network",
                 "nodes": [{"id": n, "label": n} for n in nodes],
                 "links": [{"source": s, "target": e, "value": v} for (s, e), v in top],
                 "evidenceBasis": "provenance", "gallery": True, "basisNote": "edges connect sources to the entities they report on",
                 "source": sorted({s for (s, _e), _ in top}, key=_sid_sort)}
        return chart, f"Which sources supply data on which entities.{_cite(chart['source'])}"

    def _b_chord(self):
        per_source: dict[str, set[str]] = {}
        for o in self.ds.observations:
            if o.is_target:
                per_source.setdefault(o.source_id, set()).add(o.entity)
        pairs: dict[tuple[str, str], int] = {}
        for ents in per_source.values():
            ents = sorted(ents)
            for i, a in enumerate(ents):
                for b in ents[i + 1:]:
                    pairs[(a, b)] = pairs.get((a, b), 0) + 1
        if not pairs:
            raise Unsupported("needs a source that reports on two or more requested entities")
        nodes = sorted({x for pair in pairs for x in pair})
        chart = {"type": "chord", "title": "Entities reported together by the same sources",
                 "nodes": [{"id": n, "label": n} for n in nodes],
                 "links": [{"source": a, "target": b, "value": v} for (a, b), v in pairs.items()],
                 "evidenceBasis": "provenance", "gallery": True, "basisNote": "chord weight = number of sources covering both entities",
                 "source": sorted(per_source, key=_sid_sort)}
        return chart, f"Chords join entities that the same source reports on, which is what makes them directly comparable.{_cite(chart['source'])}"

    def _b_tree(self):
        families = {
            "Profitability": ("profit", "income", "roe", "roa", "margin", "eps", "spread"),
            "Balance sheet": ("assets", "deposit", "advances", "loan", "provision"),
            "Asset quality": ("npa", "coverage"),
            "Valuation": ("market", "p/e", "p/b", "price", "dividend"),
            "Capital & liquidity": ("capital", "casa", "credit-deposit", "liquid"),
        }
        tree: dict[str, set[str]] = {}
        for o in self.best.values():
            low = o.metric_label.casefold()
            fam = next((f for f, keys in families.items() if any(k in low for k in keys)), "Other metrics")
            tree.setdefault(fam, set()).add(o.metric_label)
        if not tree:
            raise Unsupported("no metrics were extracted")
        root = "Extracted dataset"
        links = [{"source": root, "target": fam, "value": len(ms)} for fam, ms in tree.items()]
        for fam, ms in tree.items():
            links += [{"source": fam, "target": m, "value": 1} for m in sorted(ms)[:4]]
        nodes = list(dict.fromkeys([root] + [l["target"] for l in links]))[:24]
        links = [l for l in links if l["source"] in nodes and l["target"] in nodes]
        chart = {"type": "tree", "title": "Metric families in the extracted data", "nodes": [{"id": n, "label": n} for n in nodes],
                 "links": links, "evidenceBasis": "provenance", "gallery": True,
                 "basisNote": "grouping of the metrics found in the sources", "source": self.ds.source_ids()}
        return chart, f"How the {len({o.metric for o in self.best.values()})} extracted metrics group into families.{_cite(chart['source'])}"

    def _b_flowchart(self):
        stats = self.extra.get("pipeline") or {}
        steps = []
        if stats.get("queries"):
            steps.append(f"{stats['queries']} search queries")
        if stats.get("retrieved"):
            steps.append(f"{stats['retrieved']} results retrieved")
        if stats.get("cited"):
            steps.append(f"{stats['cited']} sources kept")
        steps.append(f"{len(self.ds.source_ids())} sources with figures")
        steps.append(f"{len(self.ds.observations)} values extracted")
        steps.append(f"{len(self.best)} comparable observations")
        if stats.get("charts"):
            steps.append(f"{stats['charts']} charts built")
        nodes = [{"id": s, "label": s} for s in steps]
        links = [{"source": a, "target": b, "value": 1} for a, b in zip(steps, steps[1:])]
        chart = {"type": "flowchart", "title": "How the chart data was assembled", "nodes": nodes, "links": links,
                 "evidenceBasis": "provenance", "gallery": True, "basisNote": "retrieval and extraction steps for this report",
                 "source": self.ds.source_ids()}
        return chart, "From search to charted observations; every plotted value traces back to a quoted source line."

    def _b_venn(self):
        per_entity: dict[str, set[str]] = {}
        for o in self.best.values():
            if o.is_target:
                per_entity.setdefault(o.entity, set()).add(o.metric_label)
        ranked = sorted(per_entity.items(), key=lambda kv: -len(kv[1]))
        if len(ranked) < 2:
            raise Unsupported("needs metric coverage for two or more requested entities")
        (a, ma), (b, mb) = ranked[0], ranked[1]
        shared = ma & mb
        chart = {"type": "venn", "title": f"Metric coverage: {a} vs {b}",
                 "sets": [{"label": f"{a} ({len(ma)} metrics)", "size": len(ma)}, {"label": f"{b} ({len(mb)} metrics)", "size": len(mb)}],
                 "overlap": f"{len(shared)} shared", "evidenceBasis": "provenance", "gallery": True,
                 "basisNote": "sets of metrics with at least one reported value", "source": self.ds.source_ids()}
        return chart, f"Metrics available for both entities ({len(shared)}) are the ones that can be compared like for like."


# ─────────────────────────────────────────────────────────────────────────────
# Tables
# ─────────────────────────────────────────────────────────────────────────────
def _tables_markdown(ds: EvidenceDataset, max_metrics: int = 40) -> str:
    groups = ds.grouped()
    metrics = sorted({m for (_e, m, _p) in groups}, key=lambda m: (
        -sum(1 for (e, mm, p), items in groups.items() if mm == m and any(o.is_target for o in items)),
        ds.metric_label(m)))[:max_metrics]
    parts: list[str] = []
    for metric in metrics:
        unit = ds.metric_unit(metric)
        cells = {(e, p): items for (e, m, p), items in groups.items() if m == metric}
        if not cells:
            continue
        periods = sorted({p for (_e, p) in cells}, key=lambda p: next(
            (o.period_sort for (e, pp), items in cells.items() if pp == p for o in items), 0))
        entities = sorted({e for (e, _p) in cells}, key=lambda e: (
            not any(o.is_target for (ee, _p), items in cells.items() if ee == e for o in items), ds.entity_display(e)))
        header = ["Entity"] + [p or "As reported" for p in periods] + ["Sources"]
        unit_text = _unit_label(unit)
        parts.append(f"#### {ds.metric_label(metric)} ({unit_text})")
        parts.append("")
        parts.append("| " + " | ".join(header) + " |")
        parts.append("| " + " | ".join(["---"] * len(header)) + " |")
        notes = []
        for e in entities:
            row = [ds.entity_display(e)]
            ids = set()
            for p in periods:
                items = cells.get((e, p), [])
                values = []
                for o in sorted(items, key=EvidenceDataset._rank, reverse=True):
                    text = f"{_fmt(o.value)}"
                    if o.suspect:
                        text += "†"
                        notes.append(f"† {o.entity}, {ds.metric_label(metric)}: {o.suspect} [{o.source_id}].")
                    tagged = f"{text} ({o.source_id})" if len({x.source_id for x in items}) > 1 else text
                    if tagged not in values:
                        values.append(tagged)
                    ids.add(o.source_id)
                row.append("; ".join(values) if values else "—")
            row.append(", ".join(f"[{i}]" for i in sorted(ids, key=_sid_sort)))
            parts.append("| " + " | ".join(row) + " |")
        parts.append("")
        if notes:
            parts.extend(dict.fromkeys(notes))
            parts.append("")
    return "\n".join(parts).strip()


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────
def build_chart_gallery(ds: EvidenceDataset, question: str, *, types: list[str] | None = None,
                        validator: Callable[[dict], bool] | None = None,
                        normalizer: Callable[[dict], dict] | None = None,
                        extra: dict | None = None) -> GalleryResult:
    result = GalleryResult(observation_count=len(ds))
    wanted = [normalize_chart_type(t) for t in (types or GALLERY_ORDER)]
    wanted = [t for t in dict.fromkeys(wanted) if t in CHART_TYPES and t not in {"table", "arrow"}]
    if not ds.observations:
        for t in wanted:
            result.coverage.append({"type": t, "status": "Not included", "basis": "—",
                                    "note": "no numeric observations could be extracted from the sources"})
        return result
    builder = _Builder(ds, question, extra)
    # Build in family order so the most informative comparisons claim the
    # requested metrics first and later types rotate onto other metrics.
    family_order = [t for _f, ts in FAMILIES for t in ts]
    build_order = [t for t in family_order if t in wanted] + [t for t in wanted if t not in family_order]
    built: dict[str, GalleryChart] = {}
    reasons: dict[str, str] = {}
    for ctype in build_order:
        try:
            item = builder.build(ctype)
        except Unsupported as exc:
            reasons[ctype] = str(exc)
            continue
        except Exception as exc:  # pragma: no cover - a single type must not break the gallery
            reasons[ctype] = f"could not be built ({type(exc).__name__})"
            continue
        chart = item.chart
        if callable(normalizer):
            try:
                chart = normalizer(dict(chart))
            except Exception:
                pass
        if callable(validator):
            try:
                ok = validator(dict(chart))
            except Exception:
                ok = False
            if not ok:
                reasons[ctype] = "the extracted data did not meet this chart's shape requirements"
                continue
        item.chart = chart
        built[ctype] = item
    for _family, members in FAMILIES:
        for ctype in members:
            if ctype in built:
                result.charts.append(built[ctype])
    for ctype in wanted:
        if ctype in built:
            item = built[ctype]
            result.coverage.append({"type": ctype, "status": "Included", "basis": _BASIS_TEXT.get(item.basis, item.basis),
                                    "note": item.chart.get("basisNote", "")})
        else:
            result.coverage.append({"type": ctype, "status": "Not included", "basis": "—",
                                    "note": reasons.get(ctype, "not supported by the extracted data")})
    result.tables_markdown = _tables_markdown(ds)
    result.evidence_lines = ds.evidence_lines_by_source()
    return result


def gallery_markdown(result: GalleryResult, first_chart_index: int, *, heading_level: int = 2,
                     heading: str = "Data and Charts") -> tuple[str, list[dict]]:
    """Render the gallery section with [CHART_n] placeholders starting at first_chart_index."""
    h = "#" * max(1, min(4, heading_level))
    sub = "#" * max(2, min(5, heading_level + 1))
    lines: list[str] = []
    charts: list[dict] = []
    if heading:
        lines += [f"{h} {heading}", ""]
    included = len(result.charts)
    lines.append(
        f"This section is generated deterministically from {result.observation_count} figures extracted from the cited sources. "
        f"{included} chart type{'s' if included != 1 else ''} could be drawn from that evidence; each chart notes whether it plots reported "
        "values, values computed from them, or the provenance of the evidence. Types the data cannot support are listed in the coverage table "
        "rather than approximated."
    )
    lines.append("")
    index = first_chart_index
    by_type = {c.type: c for c in result.charts}
    figure = 1
    for family, members in FAMILIES:
        present = [by_type[t] for t in members if t in by_type]
        if not present:
            continue
        lines += [f"{sub} {family}", ""]
        for item in present:
            charts.append(item.chart)
            lines.append(f"**Figure {figure} — {DISPLAY_NAMES.get(item.type, item.type)}: {item.chart.get('title', '')}.** {item.caption}")
            lines.append("")
            lines.append(f"[CHART_{index}]")
            lines.append("")
            index += 1
            figure += 1
    if result.tables_markdown:
        lines += [f"{sub} Extracted figures (all comparable observations)", "",
                  "Values are shown exactly as reported (unit in each table heading). Where sources disagree, every reported value is listed with its source.",
                  "", result.tables_markdown, ""]
    if result.coverage:
        lines += [f"{sub} Chart coverage", "", "| Chart type | Status | Data basis | Note |", "| --- | --- | --- | --- |"]
        for row in result.coverage:
            note = re.sub(r"\|", "/", str(row.get("note") or ""))[:160]
            lines.append(f"| {DISPLAY_NAMES.get(row['type'], row['type'])} | {row['status']} | {row['basis']} | {note} |")
        lines.append("")
    return "\n".join(lines).strip() + "\n", charts


# ─────────────────────────────────────────────────────────────────────────────
# Inline placement: blend charts into the report's own sections
# ─────────────────────────────────────────────────────────────────────────────
_REFERENCE_HEADING = re.compile(
    r"\b(?:methodology|appendix|sources?|references?|bibliography|disclaimer|data\s+sources?|notes?)\b", re.I)
_LIMITS_HEADING = re.compile(r"\b(?:risks?|limitations?|caveats?)\b", re.I)
_SUMMARY_HEADING = re.compile(r"\b(?:executive\s+summary|summary|overview|key\s+takeaways|highlights?)\b", re.I)
_PROVENANCE_TYPES = {"gantt", "timeline", "sankey", "chord", "network", "tree", "flowchart", "venn"}
_HEADLINE_TYPES = {"kpi", "gauge", "progress"}
# Topic vocabulary: a chart about one of these metrics belongs where the
# report discusses the topic, even when the exact metric name is not repeated.
_TOPIC_TERMS: list[tuple[tuple[str, ...], tuple[str, ...]]] = [
    (("npa", "provision", "coverage"), ("asset quality", "npa", "non-performing", "slippage", "provision", "bad loan", "credit cost")),
    (("profit", "income", "earnings", "eps", "operating"), ("profit", "profitability", "earnings", "income", "bottom line")),
    (("return on", "roe", "roa", "margin", "nim", "spread", "cost-to-income"), ("return", "roe", "roa", "margin", "efficiency", "profitability", "nim", "spread")),
    (("deposit", "casa"), ("deposit", "casa", "liabilit", "funding")),
    (("advances", "loan", "credit-deposit", "credit"), ("advances", "loan", "credit growth", "lending", "balance sheet")),
    (("total assets", "asset", "branches"), ("total assets", "balance sheet", "scale", "size", "branch")),
    (("market cap", "p/e", "p/b", "price", "dividend", "valuation"), ("valuation", "market cap", "p/e", "multiple", "price", "dividend", "investor")),
    (("share", "promoter", "fii", "dii"), ("shareholding", "promoter", "institutional", "fii", "dii")),
]


def _chart_terms(chart: dict) -> set[str]:
    parts = [str(chart.get("title") or ""), str(chart.get("xLabel") or ""), str(chart.get("yLabel") or "")]
    for s in chart.get("series") or []:
        if isinstance(s, dict):
            parts.append(str(s.get("name") or ""))
    parts += [str(c) for c in (chart.get("columns") or [])]
    text = " ".join(parts).casefold()
    terms: set[str] = set()
    for triggers, vocab in _TOPIC_TERMS:
        if any(t in text for t in triggers):
            terms.update(vocab)
    return terms


def _chart_topic_hint(chart: dict) -> str:
    """Classify a chart's dominant topic to prevent broad headings winning by keyword volume."""
    parts = [str(chart.get("title") or ""), str(chart.get("xLabel") or ""), str(chart.get("yLabel") or "")]
    parts.extend(str(s.get("name") or "") for s in (chart.get("series") or []) if isinstance(s, dict))
    text = " ".join(parts).casefold()
    has_deposit = bool(re.search(r"\b(?:deposit|deposits|casa|savings bank)\b", text))
    has_credit = bool(re.search(r"\b(?:credit|advances?|loans?|lending)\b", text))
    if has_deposit and has_credit:
        return "credit_deposit"
    if re.search(r"\b(?:npa|gnpa|nnpa|non[- ]performing|slippage|provision coverage)\b", text):
        return "asset_quality"
    if re.search(r"\b(?:profitability|profit|earnings|net interest|nim|nii|roe|roa|margin)\b", text):
        return "profitability"
    if re.search(r"\b(?:capital adequacy|crar|cet[- ]?1|capital ratio)\b", text):
        return "capital"
    if has_deposit:
        return "deposits"
    if has_credit:
        return "credit"
    if re.search(r"\b(?:asset scale|total assets|balance sheet|branch)\b", text):
        return "scale"
    return "general"


def _split_chunks(text: str) -> list[dict]:
    """Split Markdown into heading-led chunks; each keeps its heading chain."""
    lines = text.split("\n")
    chunks: list[dict] = []
    chain: list[tuple[int, str]] = []
    current = {"start": 0, "heading": "", "chain": "", "level": 0, "lines": []}
    in_fence = False
    for i, line in enumerate(lines):
        if line.strip().startswith("```"):
            in_fence = not in_fence
        m = None if in_fence else re.match(r"^(#{1,4})\s+(.+?)\s*$", line)
        if m:
            chunks.append(current)
            level = len(m.group(1))
            chain = [c for c in chain if c[0] < level] + [(level, m.group(2))]
            current = {"start": i, "heading": m.group(2), "chain": " / ".join(h for _l, h in chain),
                       "level": level, "lines": [line]}
        else:
            current["lines"].append(line)
    chunks.append(current)
    return [c for c in chunks if c["lines"]]


def _chunk_kind(chunk: dict) -> str:
    chain = chunk["chain"]
    if not chunk["heading"]:
        return "preamble"
    if _REFERENCE_HEADING.search(chain):
        return "reference"
    if _LIMITS_HEADING.search(chunk["heading"]):
        return "limits"
    if _SUMMARY_HEADING.search(chunk["heading"]):
        return "summary"
    return "analysis"


def _blocks(lines: list[str]) -> list[tuple[int, int, str]]:
    """(start, end, kind) paragraph blocks inside a chunk's lines (index 0 = heading)."""
    out = []
    i = 1 if lines and lines[0].lstrip().startswith("#") else 0
    while i < len(lines):
        if not lines[i].strip():
            i += 1
            continue
        j = i
        while j < len(lines) and lines[j].strip():
            j += 1
        first = lines[i].lstrip()
        kind = "table" if first.startswith("|") else "chart" if re.fullmatch(r"\[CHART_\d+\]", first.strip()) else \
            "list" if re.match(r"([-*+]|\d+[.)])\s", first) else "fence" if first.startswith("```") else "para"
        out.append((i, j, kind))
        i = j
    return out


def blend_gallery_into_report(report_text: str, result: GalleryResult, first_chart_index: int,
                              max_per_section: int = 3) -> tuple[str, list[dict]]:
    """Place each gallery chart inside the section that discusses its metric.

    No standalone charts section is created. Analytical charts go after the
    paragraph in the best-matching section; headline indicators join the
    executive summary; evidence-provenance diagrams and the chart-coverage note
    go to Methodology/Appendix (or the limitations section).
    """
    text = str(report_text or "").rstrip()
    chunks = _split_chunks(text)
    kinds = [_chunk_kind(c) for c in chunks]
    analytic = [i for i, k in enumerate(kinds) if k == "analysis"]
    summary = next((i for i, k in enumerate(kinds) if k == "summary"), None)
    reference = next((i for i, k in enumerate(kinds) if k == "reference" and re.search(r"methodology|appendix|notes?", chunks[i]["heading"], re.I)), None)
    limits = next((i for i, k in enumerate(kinds) if k == "limits"), None)
    if not analytic:
        analytic = [i for i, k in enumerate(kinds) if k in {"summary", "preamble"} and "".join(chunks[i]["lines"]).strip()] or [len(chunks) - 1]
    home_for_meta = reference if reference is not None else limits if limits is not None else analytic[-1]

    placements: dict[int, list[tuple[GalleryChart, int]]] = {}
    load: dict[int, int] = {}
    rr = 0
    for item in result.charts:
        if item.type in _PROVENANCE_TYPES:
            target = home_for_meta
        elif item.type in _HEADLINE_TYPES and summary is not None:
            target = summary
        else:
            terms = _chart_terms(item.chart)
            scored = []
            topic_hint = _chart_topic_hint(item.chart)
            for idx in analytic:
                heading = chunks[idx]["chain"].casefold()
                body = "\n".join(chunks[idx]["lines"][1:]).casefold()
                # Section headings carry stronger intent than repeated keywords in
                # narrative paragraphs. Without this bias, a deposit/loan-ratio
                # subsection can attract several deposit charts while the actual
                # credit-growth subsection receives none.
                score = sum(10 * min(1, heading.count(t)) + min(2, body.count(t)) for t in terms)
                if topic_hint == "deposits":
                    if re.search(r"\b(?:deposit composition|deposit growth|deposit expansion|deposit dynamics|funding|liabilit)\w*\b", heading):
                        score += 24
                    if re.search(r"\b(?:deposit|loan)[-– ]to[-– ](?:loan|deposit) ratio|\bdlr\b|\bratio\b", heading):
                        score -= 22
                elif topic_hint == "credit":
                    if re.search(r"\b(?:credit growth|loan growth|credit dynamics|sectoral credit|lending growth|advances)\b", heading):
                        score += 28
                    if re.search(r"\b(?:deposit|loan)[-– ]to[-– ](?:loan|deposit) ratio|\bdlr\b", heading):
                        score -= 18
                elif topic_hint == "credit_deposit":
                    if re.search(r"\b(?:credit|loan|deposit) growth\b|\bcredit and deposit\b|\bdeposit and credit\b", heading):
                        score += 25
                elif topic_hint == "asset_quality" and re.search(r"asset quality|npa|non-performing|provision", heading):
                    score += 28
                elif topic_hint == "profitability" and re.search(r"profit|earnings|margin|return on assets|return on equity|nim|nii", heading):
                    score += 28
                elif topic_hint == "capital" and re.search(r"capital adequacy|capital structure|capital buffer|crar|cet", heading):
                    score += 28
                scored.append((score, -idx, idx))
            scored.sort(reverse=True)
            target = None
            for score, _neg, idx in scored:
                if score > 0 and load.get(idx, 0) < max_per_section:
                    target = idx
                    break
            if target is None:  # nothing topical: spread across analytic sections
                ordered = sorted(analytic, key=lambda i: load.get(i, 0))
                target = ordered[rr % len(ordered)] if ordered else analytic[0]
                rr += 1
        load[target] = load.get(target, 0) + 1
        placements.setdefault(target, []).append((item, 0))

    charts: list[dict] = []
    index = first_chart_index
    new_chunk_lines: list[list[str]] = []
    for ci, chunk in enumerate(chunks):
        lines = list(chunk["lines"])
        items = placements.get(ci, [])
        if items:
            blocks = [b for b in _blocks(lines) if b[2] in {"para", "list", "table"}]
            inserts: dict[int, list[str]] = {}
            used: dict[int, int] = {}
            for item, _ in items:
                terms = _chart_terms(item.chart)
                anchor = len(lines)
                if blocks:
                    # Prefer the paragraph that discusses the metric, but spread
                    # several charts over different paragraphs when possible.
                    best = max(blocks, key=lambda b: (
                        sum("\n".join(lines[b[0]:b[1]]).casefold().count(t) for t in terms) - 3 * used.get(b[1], 0), -b[0]))
                    anchor = best[1]
                    used[anchor] = used.get(anchor, 0) + 1
                charts.append(item.chart)
                inserts.setdefault(anchor, []).extend(["", f"[CHART_{index}]", "", f"*{item.caption}*"])
                index += 1
            rebuilt: list[str] = []
            for li, line in enumerate(lines + [""]):
                if li in inserts:
                    rebuilt.extend(inserts[li])
                    rebuilt.append("")
                if li < len(lines):
                    rebuilt.append(line)
            lines = rebuilt
        # The per-type coverage table is an audit artefact. Show it only for an
        # explicit chart-catalogue audit (dozens of requested types), never in
        # an ordinary client report.
        if ci == home_for_meta and result.coverage and len(result.coverage) >= 20:
            missing = [r for r in result.coverage if r["status"] != "Included"]
            lines += ["", f"**Charts and data.** {len(result.charts)} chart types in this report are drawn from "
                      f"{result.observation_count} figures extracted from the cited sources; each chart states whether it "
                      "plots reported values, values computed from them, or evidence provenance."]
            if missing:
                lines += ["", "| Chart type | Why it is not shown |", "| --- | --- |"]
                lines += [f"| {DISPLAY_NAMES.get(r['type'], r['type'])} | {re.sub(r'[|]', '/', r['note'])[:150]} |" for r in missing]
        new_chunk_lines.append(lines)

    body = "\n".join("\n".join(lines) for lines in new_chunk_lines)
    body = re.sub(r"\n{3,}", "\n\n", body).strip() + "\n"
    return body, charts
