"""The report's storyline: the points it must make, and the chart that proves each one.

The measure plan (utils.measure_plan) already says, before any search, what the report must show and why
("quarterly returns of sector indices - which sectors led or lagged each quarter"; "net FPI flows by sector -
the capital movement that drives rotation"). Nothing after it remembered that plan: charts were built from
whatever tables existed and counted against a number, so a sector-rotation report proved its first point
three times and its other two not at all.

Each planned measure is a POINT. A chart PROVES a point when it plots that measure (its subject words), for
the point's entities (when it names any), in the form the point needs (a trend needs a time axis). Charts
that prove a point come first; the points no chart proves are reported, never padded.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_GENERIC = {"the", "and", "for", "from", "with", "into", "over", "by", "of", "in", "on", "to", "a", "an",
            "change", "changes", "trend", "trends", "level", "levels", "value", "values", "data", "total",
            "annual", "quarterly", "monthly", "yearly", "share", "rate", "growth", "number", "net", "average",
            "india", "indian", "national", "country", "sector", "sectors", "sectoral", "index", "indices",
            # time words say WHEN, not WHAT: "quarterly profit growth" matched "1-Year Volatility" on "year"
            "year", "years", "quarter", "quarters", "month", "months", "period", "on", "per", "grown", "move",
            "what", "how", "which", "did", "has", "have", "been", "its", "their", "between"}
_TIME_LABEL_RE = re.compile(
    r"(?i)^(?:q[1-4]|h[12]|fy|cy)\b|^(?:19|20)\d{2}|^(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b")


def _stems(text: str) -> set[str]:
    words = re.findall(r"[a-z][a-z0-9&]{2,}", str(text or "").casefold())
    return {w[:5] for w in words if w not in _GENERIC}


@dataclass
class Point:
    """One thing the report must show: a measure, whose it is, how it is shown, and why."""
    measure: str
    why: str = ""
    entities: list[str] = field(default_factory=list)
    chart: str = "trend"
    proof: str = ""        # chart_id of the chart that proves it ("" when none does)
    proof_title: str = ""
    score: float = 0.0

    def to_dict(self) -> dict:
        return {"measure": self.measure, "why": self.why, "entities": self.entities, "chart": self.chart,
                "provedBy": self.proof_title or None, "score": round(self.score, 2)}


def points_from_plan(plan) -> list[Point]:
    """Points from a MeasurePlan (or its dict form)."""
    measures = getattr(plan, "measures", None)
    if measures is None and isinstance(plan, dict):
        measures = plan.get("measures") or []
    out = []
    for m in measures or []:
        get = (lambda k, d="": m.get(k, d)) if isinstance(m, dict) else (lambda k, d="": getattr(m, k, d))
        measure = str(get("measure") or "").strip()
        if measure:
            out.append(Point(measure=measure, why=str(get("why") or "").strip(),
                             entities=[str(e) for e in (get("entities", []) or []) if str(e).strip()],
                             chart=str(get("chart") or "trend")))
    return out


def _chart_text(spec: dict) -> str:
    return " ".join([str(spec.get(k) or "") for k in ("title", "metric", "yLabel", "unit")])


def _labels(spec: dict) -> list[str]:
    return [str(d.get("label") or "") for s in spec.get("series") or [] for d in s.get("data") or []]


def _series_names(spec: dict) -> list[str]:
    return [str(s.get("name") or "") for s in spec.get("series") or []]


def proof_score(point: Point, spec: dict) -> float:
    """How well a chart proves a point (0 = not at all). Subject words of the measure must be in the chart's
    title/metric; named entities and a time axis (for a trend) add to the score."""
    measure_words = _stems(point.measure)
    if not measure_words:
        return 0.0
    chart_words = _stems(_chart_text(spec))
    subject = len(measure_words & chart_words) / len(measure_words)
    if subject < 0.5:
        return 0.0  # the chart does not plot this measure (half its subject words at least)
    # A measure word the chart qualifies into another quantity is not the measure: "capacity" is not
    # "capacity factor", "export value" is not "export share".
    raw_measure = {w[:5] for w in re.findall(r"[a-z]{3,}", point.measure.casefold())}
    raw_chart = {w[:5] for w in re.findall(r"[a-z]{3,}", _chart_text(spec).casefold())}
    qualifiers = {"facto", "share", "ratio", "volat", "hhi", "sprea", "margi", "yield"} - raw_measure
    if qualifiers & raw_chart:
        return 0.0
    score = subject
    plotted = _stems(" ".join(_labels(spec) + _series_names(spec) + [str(spec.get("title") or "")]))
    if point.entities:
        named = sum(1 for e in point.entities if _stems(e) and _stems(e) <= plotted)
        score += 0.5 * named / len(point.entities)
    labels = _labels(spec)
    time_axis = sum(1 for lab in labels if _TIME_LABEL_RE.search(lab.strip())) >= max(3, len(labels) // 2)
    if point.chart == "trend":
        if not (time_axis or spec.get("type") == "heatmap"):
            return 0.0  # a trend needs a time axis: a one-day snapshot does not show how it changed
        score += 0.4
    elif point.chart in {"comparison", "ranking", "share"}:
        score += 0.2 if not time_axis or len(_series_names(spec)) > 1 else 0.0
    return max(0.0, score)


def match_points(points: list[Point], charts: list, *, threshold: float = 0.6) -> dict[str, str]:
    """Assign each point the best chart that proves it (a chart proves at most one point). Returns
    {chart_id: point measure} for the charts that prove a point; each Point records its proof."""
    pairs = []
    for pi, point in enumerate(points):
        for chart in charts:
            score = proof_score(point, getattr(chart, "spec", chart) or {})
            if score >= threshold:
                pairs.append((score, pi, chart))
    pairs.sort(key=lambda x: -x[0])
    used_points, used_charts, proving = set(), set(), {}
    for score, pi, chart in pairs:
        cid = getattr(chart, "chart_id", id(chart))
        if pi in used_points or cid in used_charts:
            continue
        point = points[pi]
        point.proof, point.proof_title, point.score = str(cid), str((getattr(chart, "spec", {}) or {}).get("title")), score
        used_points.add(pi)
        used_charts.add(cid)
        proving[str(cid)] = point.measure
    return proving


def storyline_note(points: list[Point]) -> str:
    """The storyline for the outline: the points the report must make, in order, so its sections follow them."""
    if not points:
        return ""
    lines = [f"{i}. {pt.why or pt.measure} (shown by: {pt.measure})" for i, pt in enumerate(points, 1)]
    return ("\n\nSTORYLINE - the points the report must make, in this order (give each its own section or "
            "subsection, built around the evidence that shows it):\n" + "\n".join(lines))
