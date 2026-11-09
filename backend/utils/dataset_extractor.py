"""Domain-generic dataset extraction (Stage J): structured series from evidence passages, verified in code.

The stock-market path has a dedicated data layer (``market_data`` -> fetched dataset -> data-first charts).
Every other domain only had whatever pipe tables a page happened to contain, so data printed as a PDF
chart's text ("0.23% 0.38% ... 2016 2017 ...") or a table flattened onto one line was never charted.

Here the model PROPOSES series from the most data-dense passages, and code ACCEPTS a series only when:
  * every value is a number that literally appears in the passage (same digits, same scale);
  * every point label (a year / period / category) appears in the passage;
  * it has >= 3 points, >= 3 distinct values, and multi-series datasets share their labels;
  * a period later than today is labelled a forecast / projection / target in the series name or title.
Accepted datasets become ordinary PlannedCharts (with a deterministic interpretation), so they go through
the same chart verification, CHART LINT and rendered checks as every other chart.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date

from utils.section_charts import PlannedChart

log = logging.getLogger("dataset_extractor")

_NUM_RE = re.compile(r"(?<![\w.])[+\-−]?\d[\d,]*(?:\.\d+)?")
_PERIOD_RE = re.compile(r"\b(?:19|20)\d{2}\b|\b(?:FY|CY)\s?'?\d{2,4}\b|\bQ[1-4]\s?(?:FY)?\s?\d{2,4}\b|\bH[12]\s?FY\d{2}\b")
_FORECAST_RE = re.compile(r"(?i)forecast|projection|projected|estimate|target|outlook|expected|\bE\b|\(e\)")

SYSTEM = (
    "You extract numeric data series from one evidence passage. Return JSON only. Copy numbers exactly as "
    "printed (no rounding, no unit conversion, no calculation). Use labels exactly as printed in the passage "
    "(years, periods, company or category names). If the passage does not clearly say which number belongs "
    "to which label and series, return no dataset for it. Never invent data."
)
PROMPT = """QUESTION: {question}

PASSAGE {pid} (source: {source}):
<<<
{text}
>>>

Return JSON: {{"datasets": [{{"metric": "<what is measured, e.g. 'EV penetration rate'>", "unit": "<%, units, Rs crore, ...>",
 "x": "time" | "category", "period": "<the period or as-of date the data covers, '' if none>",
 "series": [{{"name": "<series name, e.g. 'India'>", "points": [{{"label": "<as printed>", "value": "<number as printed>"}}]}}]}}]}}
At most 2 datasets, at most 4 series each, only data relevant to the QUESTION. Every series needs at least 3 points
(single figures, two-way splits and growth rates are not datasets). Return {{"datasets": []}} when unsure."""


def _numbers(text: str) -> set[float]:
    out = set()
    for raw in _NUM_RE.findall(text or ""):
        try:
            out.add(round(float(raw.replace(",", "").replace("−", "-")), 6))
        except ValueError:
            continue
    return out


def _value(raw) -> float | None:
    try:
        return round(float(str(raw).replace(",", "").replace("%", "").replace("−", "-").strip()), 6)
    except ValueError:
        return None


def _data_profile(text: str, window_year: int | None) -> tuple[int, int, int] | None:
    """(numbers, periods, in-window periods) or None when the passage is not data or is all before the window."""
    nums = len(_NUM_RE.findall(text))
    period_labels = set(_PERIOD_RE.findall(text))
    years = {int(y) for lab in period_labels for y in re.findall(r"(?:19|20)\d{2}", lab)}
    if window_year and years and max(years) < window_year:
        return None  # every period it names is before the question's window
    this_year = date.today().year
    in_window = sum(1 for y in years if (not window_year or y >= window_year) and y <= this_year)
    # recent prose with yearly figures ("2.27 million in CY2025, 1.95 million in CY2024") qualifies too
    if nums < 10 and not (nums >= 6 and in_window >= 3):
        return None
    return nums, len(period_labels), in_window


def candidate_passages(store, question: str, *, limit: int = 6, window_year: int | None = None,
                       queries: list[str] | None = None) -> list:
    """Relevant (BM25 x reliability, against the question and the outline's sections), data-dense passages
    with periods inside the question's window first; at most 2 per source."""
    relevance: dict[str, float] = {}
    for query in [question, *(queries or [])]:
        for passage, score in store.search(query, k=40, max_per_source=4):
            relevance[passage.passage_id] = max(relevance.get(passage.passage_id, 0.0), float(score))
    top = max(relevance.values(), default=1.0) or 1.0
    scored = []
    for pid, rel in relevance.items():
        p = store.get(pid)
        if p is None or p.kind in {"calculated", "search_excerpt"} or str(p.title or "").startswith(
                ("World Bank: ", "International Monetary Fund: ", "Our World in Data: ")):  # official data has its own charts
            continue
        profile = _data_profile(str(p.text or ""), window_year)
        if profile is None:
            continue
        nums, periods, in_window = profile
        score = rel / top * 2 + min(nums, 40) / 40 * 0.5 + min(periods, 8) / 8 * 0.5 + min(in_window, 4) / 4
        scored.append((score, p))
    scored.sort(key=lambda sp: -sp[0])
    picked, seen_sources = [], {}
    for _, p in scored:
        if seen_sources.get(p.source_id, 0) >= 2:
            continue
        seen_sources[p.source_id] = seen_sources.get(p.source_id, 0) + 1
        picked.append(p)
        if len(picked) >= limit:
            break
    return picked


def _mapping_verified(points: list[dict], text: str) -> bool:
    """The label -> value mapping is checkable in code: either the values form a contiguous run of numbers
    in the passage in the stated order (a chart's data printed as text), or each value sits on the same
    line as its own label (a table row)."""
    tokens = []
    for raw in _NUM_RE.findall(text or ""):
        v = _value(raw)
        if v is not None:
            tokens.append(v)
    values = [p["value"] for p in points]
    labels = [p["label"].strip() for p in points]
    n = len(values)
    # a chart printed as text: the values are a contiguous run AND the labels are a printed run of
    # exactly n period labels ("2014 2015 ... 2024"), so the value -> period alignment is certain
    period_runs = [run.split() for run in re.findall(r"(?:\b(?:19|20)\d{2}\b[ \t]+){2,}\b(?:19|20)\d{2}\b", text or "")]
    for i in range(0, max(0, len(tokens) - n) + 1):
        if [round(t, 4) for t in tokens[i:i + n]] == [round(v, 4) for v in values]:
            if any(run == labels for run in period_runs):
                return True
    # a table row or a sentence: every significant word of the label is within ~120 characters of the
    # value, and that window is prose / a row (not a dump of numbers, where words and values are far apart)
    body = text or ""
    for p in points:
        words = [w for w in re.findall(r"[a-z0-9]+", p["label"].casefold())
                 if len(w) >= 3 and w not in _LABEL_STOP] or re.findall(r"[a-z0-9]+", p["label"].casefold())
        ok = False
        for m in _NUM_RE.finditer(body):
            if _value(m.group(0)) != p["value"] or m.group(0).strip() == p["label"]:
                continue
            window = body[max(0, m.start() - 120):m.end() + 120]
            numeric_share = len(_NUM_RE.findall(window)) / max(1, len(window.split()))
            if numeric_share > 0.6:
                continue  # a run of numbers: a value is not tied to a word here
            low = window.casefold()
            if all(re.search(rf"\b{re.escape(w)}", low) for w in words):
                ok = True
                break
        if not ok:
            return False
    return True


_LABEL_STOP = {"the", "and", "for", "with", "from", "total", "sales", "share", "units"}


def verify_dataset(ds: dict, text: str, today: date) -> tuple[dict | None, str]:
    """(clean dataset, '') or (None, reason)."""
    numbers = _numbers(text)
    low = text.casefold()
    series = [s for s in ds.get("series") or [] if isinstance(s, dict)][:4]
    if not series:
        return None, "no series"
    clean = []
    for s in series:
        pts = []
        for p in s.get("points") or []:
            if not isinstance(p, dict):
                continue
            v = _value(p.get("value"))
            label = str(p.get("label") or "").strip()
            if v is None or not label:
                return None, "a point without a value or label"
            if v not in numbers and round(v, 2) not in {round(n, 2) for n in numbers}:
                return None, f"value {p.get('value')} is not in the passage"
            if label.casefold() not in low and not all(tok in low for tok in re.findall(r"[a-z0-9]+", label.casefold())):
                return None, f"label {label!r} is not in the passage"
            pts.append({"label": label, "value": v, "raw": str(p.get("value"))})
        if len(pts) < 3:
            return None, "fewer than 3 points"
        if not _mapping_verified(pts, text):
            return None, f"cannot verify which value belongs to which label in series {str(s.get('name'))[:40]!r}"
        clean.append({"name": str(s.get("name") or ds.get("metric") or "Value").strip(), "points": pts})
    labels = [p["label"] for p in clean[0]["points"]]
    if any([p["label"] for p in s["points"]] != labels for s in clean[1:]):
        return None, "series do not share their labels"
    values = [p["value"] for s in clean for p in s["points"]]
    if len(set(values)) < 3:
        return None, "fewer than 3 distinct values"
    years = [int(y) for lab in labels for y in re.findall(r"\b(20\d{2})\b", lab)]
    forecast = any(y > today.year for y in years)
    if forecast and not _FORECAST_RE.search(" ".join([str(ds.get("metric")), str(ds.get("period")), *[s["name"] for s in clean]])):
        # unlabelled future periods are dropped; the observed periods remain a chart when >= 3 are left
        keep = [i for i, lab in enumerate(labels)
                if not any(int(y) > today.year for y in re.findall(r"\b(20\d{2})\b", lab))]
        if len(keep) < 3:
            return None, "future periods not labelled as a forecast (fewer than 3 observed periods left)"
        clean = [{**s, "points": [s["points"][i] for i in keep]} for s in clean]
        labels = [labels[i] for i in keep]
        years = [int(y) for lab in labels for y in re.findall(r"\b(20\d{2})\b", lab)]
        forecast = False
    unit = str(ds.get("unit") or "").strip()
    if unit and unit != "%" and unit.casefold() not in low:
        unit = "as reported"  # a unit the passage does not state is never asserted
    return {"metric": str(ds.get("metric") or "").strip(), "unit": unit,
            "x": "time" if ds.get("x") == "time" or len(years) >= len(labels) * 0.8 else "category",
            "period": str(ds.get("period") or "").strip(), "series": clean, "forecast": forecast}, ""


def _quote_line(text: str, raw: str, label: str) -> str:
    """The passage line holding the value (the export audit checks the quoted line contains the value)."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    with_both = [ln for ln in lines if raw in ln and label.casefold() in ln.casefold()]
    with_value = [ln for ln in lines if raw in ln]
    return (with_both or with_value or [text[:300]])[0][:600]


def _interpretation(ds: dict) -> str:
    unit = ds["unit"]
    pct = unit.strip() == "%"

    def f(v):
        shown = unit if unit and unit != "as reported" else ""
        return f"{v:g}%" if pct else f"{v:,.2f}".rstrip("0").rstrip(".") + (f" {shown}" if shown else "")
    parts = []
    for s in ds["series"]:
        pts = s["points"]
        if ds["x"] == "time":
            first, last = pts[0], pts[-1]
            parts.append(f"{s['name']} went from {f(first['value'])} in {first['label']} to {f(last['value'])} in {last['label']}")
        else:
            hi = max(pts, key=lambda p: p["value"])
            lo = min(pts, key=lambda p: p["value"])
            parts.append(f"{s['name']}: highest {hi['label']} ({f(hi['value'])}), lowest {lo['label']} ({f(lo['value'])})")
    text = "; ".join(parts)
    return text + ("" if not ds.get("forecast") else " (later years are forecasts)") + "."  # names keep their casing


def to_chart(ds: dict, passage, source_title: str, index: int) -> PlannedChart:
    text = str(passage.text or "")
    series, points = [], []
    for s in ds["series"]:
        data = []
        for p in s["points"]:
            data.append({"label": p["label"], "value": p["value"], "evidenceSource": passage.source_id,
                         "evidenceQuote": _quote_line(text, p["raw"], p["label"]), "evidenceValue": p["raw"]})
            points.append((s["name"], p["label"], p["value"]))
        series.append({"name": s["name"], "data": data})
    multi = len(series) > 1
    kind = ("line" if not multi else "grouped_bar") if ds["x"] == "time" else ("grouped_bar" if multi else "horizontal_bar")
    unit = ds["unit"]
    metric = ds["metric"] or series[0]["name"]
    title = f"{metric}" + (f" ({unit})" if unit else " (as reported)") + (" (forecast)" if ds.get("forecast") else "")
    spec = {"type": kind, "title": title, "xLabel": "Year" if ds["x"] == "time" else "Category",
            "yLabel": f"{metric}" + (f" ({unit})" if unit else " (as reported)"), "unit": "%" if "%" in unit else "",
            "unitLabel": unit if unit and unit != "as reported" else "",
            "series": series, "source": [passage.source_id], "sourceIds": [passage.source_id],
            "evidenceBasis": "reported", "provenance": "extracted dataset",
            "basisNote": f"Values copied from evidence passage {passage.passage_id} ({source_title[:80]}); every value checked against the passage."}
    chart = PlannedChart(chart_id=f"x{index}", spec=spec, passage_ids=[passage.passage_id], source_ids=[passage.source_id],
                         points=points, match_text=f"{metric} {' '.join(s['name'] for s in series)} {ds.get('period', '')}",
                         heading=ds.get("period") or "")
    chart.interpretation = _interpretation(ds)
    return chart


def clip_to_window(ds: dict, window_year: int | None, question: str = "") -> tuple[dict | None, str]:
    """A time series keeps only the periods inside the question's window (>= 3 points or it is dropped).
    The window test is the chart verifier's own, so a kept point is never rejected later."""
    if ds.get("x") != "time":
        return ds, ""
    try:
        from utils.chart_evidence_audit import _periods_outside_question_window

        def inside(label: str) -> bool:
            return bool(re.search(r"\b(?:19|20)\d{2}\b", label)) and not _periods_outside_question_window(question, [label], {})
    except ImportError:  # pragma: no cover
        def inside(label: str) -> bool:
            years = re.findall(r"\b((?:19|20)\d{2})\b", label)
            return bool(years) and all(not window_year or int(y) >= window_year for y in years)
    keep = [i for i, p in enumerate(ds["series"][0]["points"]) if inside(p["label"])]
    if len(keep) < 3:
        return None, f"fewer than 3 points inside the question's window (from {window_year})"
    clipped = dict(ds)
    clipped["series"] = [{**s, "points": [s["points"][i] for i in keep]} for s in ds["series"]]
    return clipped, ""


async def extract_dataset_charts(store, gateway, *, question: str, today: str, limit: int = 6,
                                 queries: list[str] | None = None) -> tuple[list[PlannedChart], dict]:
    """Propose (LLM) + verify (code). Returns (charts, funnel note)."""
    note = {"passages": 0, "proposed": 0, "accepted": 0, "rejected": []}
    today_d = date.fromisoformat(today[:10])
    charts: list[PlannedChart] = []
    seen_values: list[list[float]] = []
    import asyncio
    try:  # the question's time window: series are clipped to it (never mixed with older periods)
        from utils.source_quality import window_start
        window_year = window_start(question, today_d).year
    except Exception:
        window_year = None
    passages = candidate_passages(store, question, limit=limit, window_year=window_year, queries=queries)
    note["passages"] = len(passages)

    def _title(passage) -> str:
        return str(passage.title or passage.publisher or passage.source_id)

    results = await asyncio.gather(*[
        gateway.generate(purpose="dataset_extract", section_id=passage.passage_id, system=SYSTEM,
                         prompt=PROMPT.format(question=question, pid=passage.passage_id, source=_title(passage)[:120],
                                              text=str(passage.text)[:6000]),
                         max_tokens=2048, temperature=0.0, json_mode=True, timeout=60, retries=1)
        for passage in passages])
    for passage, result in zip(passages, results):
        title = _title(passage)
        data = result.json() if getattr(result, "ok", False) else None
        for ds in (data or {}).get("datasets") or [] if isinstance(data, dict) else []:
            note["proposed"] += 1
            clean, reason = verify_dataset(ds if isinstance(ds, dict) else {}, str(passage.text), today_d)
            if clean:
                clean, reason = clip_to_window(clean, window_year, question)
            if not clean:
                note["rejected"].append({"passage": passage.passage_id, "metric": str((ds or {}).get("metric"))[:80], "reason": reason})
                continue
            charts.append(to_chart(clean, passage, title, len(charts) + 1))
            note["accepted"] += 1
    # a dataset whose every series repeats a series of a richer dataset is dropped (keep the richer one)
    def _series_values(chart):
        return [tuple(round(p[2], 4) for p in chart.points if p[0] == name)
                for name in dict.fromkeys(p[0] for p in chart.points)]
    kept: list[PlannedChart] = []
    for chart in sorted(charts, key=lambda c: -len(_series_values(c))):
        mine = _series_values(chart)
        if any(all(sv in _series_values(other) for sv in mine) for other in kept):
            note["rejected"].append({"passage": ",".join(chart.passage_ids), "metric": str(chart.spec.get("title"))[:80],
                                     "reason": "repeats a series of a richer dataset"})
            note["accepted"] -= 1
            continue
        kept.append(chart)
    charts = [c for c in charts if c in kept]
    log.info("Dataset extractor: %d passage(s), %d proposed, %d accepted", note["passages"], note["proposed"], note["accepted"])
    return charts, note


def assign_to_sections(charts: list[PlannedChart], sections: list) -> None:
    """Each extracted chart goes to the section whose title/goal/queries best match its metric and labels."""
    for chart in charts:
        if chart.section_id:
            continue
        words = set(re.findall(r"[a-z]{4,}", (chart.match_text + " " + str(chart.spec.get("title"))).casefold()))
        best, best_score = None, 0
        for s in sections:
            text = " ".join([s.title, getattr(s, "goal", ""), *getattr(s, "queries", [])]).casefold()
            score = len(words & set(re.findall(r"[a-z]{4,}", text)))
            if score > best_score:
                best, best_score = s, score
        if best is not None and best_score >= 2:
            chart.section_id = best.section_id
