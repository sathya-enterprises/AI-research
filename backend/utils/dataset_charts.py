"""Data-first charts from fetched datasets (Stage F).

Charts are planned BEFORE writing from structured data the pipeline fetched itself
(utils.market_data: NSE sector index month-end closes -> completed calendar-quarter
returns, the Nifty 50 benchmark row, the current quarter to date), not only from
tables scraped off web pages. Each chart:

  * has provenance "fetched dataset" (evidenceBasis "calculated"): every point quotes
    the dataset row it came from, so the same evidence checks apply;
  * carries a deterministic interpretation paragraph built from the same rows (the
    numbers in it are the plotted values, each with its sign and %), used when the
    section writer does not interpret the figure itself;
  * keeps the current quarter-to-date (QTD) values in a chart of their own, labelled
    "(QTD, partial)", never mixed with completed quarters.

Deep-dive sections get the chart of their own sector (sector vs Nifty 50); the
cross-sector charts go to the section about rotation across sectors.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from utils.section_charts import QTD_LABEL, PlannedChart

log = logging.getLogger("dataset_charts")

BENCHMARK = "NIFTY 50"
_QUARTER = r"Q[1-4] \d{4}"
_HEAD_RE = re.compile(rf"^\s*Sector\s*\|\s*({_QUARTER}(?:\s*\|\s*{_QUARTER})+)\s*$")
_VALUE_RE = re.compile(r"^\s*([+\-−]?\d+(?:\.\d+)?)%\s*$")
_COMPOUND_RE = re.compile(rf"^\s*(.+?) compounded return across ({_QUARTER}) to ({_QUARTER}) = ([+\-−]?\d+(?:\.\d+)?)%\s*$")
_QTD_HEAD_RE = re.compile(r"^\s*Sector\s*\|\s*QTD return to (.+?) \(%\)\s*$")
_COMPOUND_HEAD_RE = re.compile(rf"^\s*Sector\s*\|\s*Compounded return {_QUARTER} to {_QUARTER} \(%\)\s*$")
_UPPER_WORDS = {"IT", "PSU", "FMCG", "PSE", "CPSE", "MNC", "50"}


@dataclass
class Row:
    label: str
    line: str
    passage_id: str
    raw: list[str] = field(default_factory=list)
    values: list[float | None] = field(default_factory=list)


@dataclass
class QuarterlyDataset:
    source_id: str
    quarters: list[str]
    rows: dict[str, Row]
    compounded: dict[str, tuple[float, str, str, str]]  # label -> (value, raw, line, passage)
    qtd_as_of: str
    qtd: dict[str, tuple[float, str, str, str]]          # label -> (value, raw, line, passage)


def pretty(label: str) -> str:
    words = str(label or "").split()
    return " ".join(w if w in _UPPER_WORDS else w.capitalize() for w in words)


def fy_label(calendar_quarter: str) -> str:
    """'Q4 2025' (Oct-Dec 2025) -> 'Q3 FY26': the Indian fiscal-quarter labels the report's window uses."""
    match = re.fullmatch(r"Q([1-4]) (\d{4})", str(calendar_quarter or "").strip())
    if not match:
        return calendar_quarter
    q, year = int(match.group(1)), int(match.group(2))
    fy_q, fy = (4, year) if q == 1 else (q - 1, year + 1)
    return f"Q{fy_q} FY{fy % 100:02d}"


def _num(raw: str) -> float | None:
    match = _VALUE_RE.match(raw or "")
    return float(match.group(1).replace("−", "-")) if match else None


def find_quarterly_dataset(store) -> QuarterlyDataset | None:
    """The fetched completed-quarter sector-return matrix in the evidence store, if any."""
    calculated = [p for p in store.passages if p.kind == "calculated"]
    by_source: dict[str, list] = {}
    for passage in calculated:
        by_source.setdefault(passage.source_id, []).append(passage)
    for source_id, passages in by_source.items():
        quarters: list[str] = []
        rows: dict[str, Row] = {}
        compounded: dict = {}
        qtd: dict = {}
        qtd_as_of = ""
        for passage in passages:
            mode = ""
            for line in str(passage.text or "").splitlines():
                head = _HEAD_RE.match(line)
                if head:
                    # Shown in the report's Indian fiscal-quarter labels; the quoted rows keep their own.
                    quarters = quarters or [fy_label(q.strip()) for q in head.group(1).split("|")]
                    mode = "matrix"
                    continue
                if _COMPOUND_HEAD_RE.match(line):
                    mode = "compound"
                    continue
                qhead = _QTD_HEAD_RE.match(line)
                if qhead:
                    qtd_as_of, mode = qhead.group(1).strip(), "qtd"
                    continue
                compound = _COMPOUND_RE.match(line)
                if compound and compound.group(1).strip() not in compounded:
                    value = float(compound.group(4).replace("−", "-"))
                    compounded[compound.group(1).strip()] = (value, f"{compound.group(4)}%", line.strip(), passage.passage_id)
                    continue
                cells = [c.strip() for c in line.split("|")]
                if len(cells) < 2 or not cells[0] or cells[0].casefold() == "sector":
                    continue
                if mode == "compound" and len(cells) == 2 and _num(cells[1]) is not None:
                    # The table form wins: a chart point quotes its row.
                    compounded[cells[0]] = (_num(cells[1]), cells[1], line.strip(), passage.passage_id)
                elif mode == "qtd" and len(cells) == 2 and _num(cells[1]) is not None:
                    qtd[cells[0]] = (_num(cells[1]), cells[1], line.strip(), passage.passage_id)
                elif mode == "matrix" and quarters and len(cells) == len(quarters) + 1:
                    rows[cells[0]] = Row(cells[0], line.strip(), passage.passage_id, cells[1:], [_num(c) for c in cells[1:]])
        if quarters and len(rows) >= 3:
            return QuarterlyDataset(source_id, quarters, rows, compounded, qtd_as_of, qtd)
    return None


def _point(column: str, value: float, raw: str, line: str, source_id: str) -> dict:
    return {"label": column, "value": value, "evidenceSource": source_id, "evidenceQuote": line, "evidenceValue": raw}


def _signed(value: float) -> str:
    return f"{value:+.2f}%"


def _spec(kind: str, title: str, series: list[dict], source_id: str, passage_ids: list[str], x_label: str) -> dict:
    return {"type": kind, "title": title, "xLabel": x_label, "yLabel": "Return (%)", "unit": "%", "series": series,
            "source": [source_id], "sourceIds": [source_id], "evidenceBasis": "calculated", "provenance": "fetched dataset",
            "basisNote": (f"Fetched dataset: values copied from evidence passage(s) {', '.join(passage_ids)} "
                          "(calculated from observed month-end NSE index closes).")}


def _cite(passage_ids) -> str:
    return "[" + ", ".join(sorted(set(passage_ids))) + "]"


def sector_vs_benchmark(data: QuarterlyDataset, label: str, index: int) -> PlannedChart | None:
    row, bench = data.rows.get(label), data.rows.get(BENCHMARK)
    if row is None or label == BENCHMARK:
        return None
    cols = [i for i, v in enumerate(row.values) if v is not None and (bench is None or bench.values[i] is not None)]
    if len(cols) < 3:
        return None
    quarters = [data.quarters[i] for i in cols]
    series = [{"name": pretty(label), "data": [_point(data.quarters[i], row.values[i], row.raw[i], row.line, data.source_id) for i in cols]}]
    points = [(pretty(label), data.quarters[i], row.values[i]) for i in cols]
    passages = [row.passage_id]
    if bench is not None:
        series.append({"name": "Nifty 50", "data": [_point(data.quarters[i], bench.values[i], bench.raw[i], bench.line, data.source_id) for i in cols]})
        points += [("Nifty 50", data.quarters[i], bench.values[i]) for i in cols]
        passages.append(bench.passage_id)
    passages = sorted(set(passages))
    vs = " vs Nifty 50" if bench is not None else ""
    title = f"Quarterly return (%): {pretty(label)}{vs}, {quarters[0]} to {quarters[-1]}"
    chart = PlannedChart(chart_id=f"d{index}", spec=_spec("grouped_bar" if bench is not None else "bar", title, series,
                                                          data.source_id, passages, "Quarter"),
                         passage_ids=passages, source_ids=[data.source_id], points=points,
                         match_text=f"{title} {label} quarterly returns sector rotation", heading="fetched dataset")
    chart.interpretation = interpret_sector(data, label, cols)
    return chart


def interpret_sector(data: QuarterlyDataset, label: str, cols: list[int]) -> str:
    row, bench = data.rows[label], data.rows.get(BENCHMARK)
    name = pretty(label)
    vals = [(data.quarters[i], row.values[i], row.raw[i]) for i in cols]
    best = max(vals, key=lambda v: v[1])
    worst = min(vals, key=lambda v: v[1])
    cite = _cite([row.passage_id] + ([bench.passage_id] if bench else []))
    parts = []
    # Stage K: the same facts in one of three phrasings (chosen by the sector's position), so ten deep-dives
    # do not repeat one sentence template word for word.
    variant = list(data.rows).index(label) % 3 if label in data.rows else 0
    if bench is not None:
        beats = sum(1 for i in cols if row.values[i] > bench.values[i])
        n = len(cols)
        bi, wi = data.quarters.index(best[0]), data.quarters.index(worst[0])
        b_best, b_worst = bench.raw[bi], bench.raw[wi]
        if variant == 0:
            parts.append(f"{name} returned more than the Nifty 50 in {beats} of the {n} completed quarters shown.")
            parts.append(f"Its strongest quarter was {best[0]}, when {name} returned {best[2]} against {b_best} for the "
                         f"Nifty 50, and its weakest was {worst[0]}, at {worst[2]} against {b_worst} {cite}.")
        elif variant == 1:
            parts.append(f"Over the {n} completed quarters, {name} beat the Nifty 50 {beats} times.")
            parts.append(f"The best quarter for {name} was {best[0]} ({best[2]}, with the Nifty 50 at {b_best}); the worst was "
                         f"{worst[0]} ({worst[2]}, with the Nifty 50 at {b_worst}) {cite}.")
        else:
            parts.append(f"In {beats} of {n} completed quarters, {name} finished ahead of the Nifty 50.")
            parts.append(f"{best[0]} was its peak at {best[2]} (Nifty 50: {b_best}), while {worst[0]} was its low point "
                         f"at {worst[2]} (Nifty 50: {b_worst}) {cite}.")
    else:
        parts.append(f"{name}'s strongest quarter was {best[0]} ({best[2]}) and its weakest {worst[0]} ({worst[2]}) {cite}.")
    q = data.qtd.get(label)
    qb = data.qtd.get(BENCHMARK)
    if q and data.qtd_as_of:
        against = f" against {qb[1]} for the Nifty 50" if qb else ""
        parts.append(f"In the current quarter to date (to {data.qtd_as_of}, partial), {name} returned {q[1]}{against} "
                     f"{_cite([q[3]] + ([qb[3]] if qb else []))}.")
    return " ".join(parts)


def compounded_chart(data: QuarterlyDataset, index: int) -> PlannedChart | None:
    items = sorted(data.compounded.items(), key=lambda kv: -kv[1][0])
    if len(items) < 3:
        return None
    first, last = data.quarters[0], data.quarters[-1]
    passages = sorted({v[3] for _, v in items})
    series = [{"name": f"Compounded return, {first} to {last}",
               "data": [_point(pretty(k), v[0], v[1], v[2], data.source_id) for k, v in items[:15]]}]
    title = f"Compounded return (%), {first} to {last}: {len(items[:15])} NSE sector indices"
    chart = PlannedChart(chart_id=f"d{index}", spec=_spec("horizontal_bar", title, series, data.source_id, passages, "Index"),
                         passage_ids=passages, source_ids=[data.source_id],
                         points=[("Compounded return", pretty(k), v[0]) for k, v in items[:15]],
                         match_text=f"{title} sector rotation across sectors leaders laggards cumulative", heading="fetched dataset")
    top, bottom = items[:3], items[-3:]
    bench = data.compounded.get(BENCHMARK)
    lead = ", ".join(f"{pretty(k)} ({v[1]})" for k, v in top if k != BENCHMARK)
    lag = ", ".join(f"{pretty(k)} ({v[1]})" for k, v in bottom if k != BENCHMARK)
    text = (f"Compounded over {first} to {last}, the strongest sector indices were {lead}, and the weakest were {lag}"
            + (f", against {bench[1]} for the Nifty 50" if bench else "") + f" {_cite(passages)}.")
    chart.interpretation = text
    return chart


def qtd_chart(data: QuarterlyDataset, index: int) -> PlannedChart | None:
    items = sorted(data.qtd.items(), key=lambda kv: -kv[1][0])
    if len(items) < 3 or not data.qtd_as_of:
        return None
    passages = sorted({v[3] for _, v in items})
    title = f"Quarter-to-date return (%) to {data.qtd_as_of}: {len(items[:15])} NSE sector indices ({QTD_LABEL})"
    series = [{"name": f"QTD to {data.qtd_as_of}", "data": [_point(pretty(k), v[0], v[1], v[2], data.source_id) for k, v in items[:15]]}]
    chart = PlannedChart(chart_id=f"d{index}q", spec=_spec("horizontal_bar", title, series, data.source_id, passages, "Index"),
                         passage_ids=passages, source_ids=[data.source_id],
                         points=[("QTD return", pretty(k), v[0]) for k, v in items[:15]],
                         match_text=f"{title} current quarter rotation across sectors", heading="fetched dataset")
    bench = data.qtd.get(BENCHMARK)
    lead = ", ".join(f"{pretty(k)} ({v[1]})" for k, v in items[:3] if k != BENCHMARK)
    lag = ", ".join(f"{pretty(k)} ({v[1]})" for k, v in items[-3:] if k != BENCHMARK)
    chart.interpretation = (f"In the current quarter so far (to {data.qtd_as_of}; partial, not a completed quarter), the "
                            f"leading sector indices were {lead} and the weakest {lag}"
                            + (f", with the Nifty 50 at {bench[1]}" if bench else "") + f" {_cite(passages)}.")
    return chart


def build_dataset_charts(store) -> tuple[list[PlannedChart], dict]:
    """(charts, funnel note). Empty when no fetched dataset is in the store."""
    data = find_quarterly_dataset(store)
    if data is None:
        return [], {"dataset": "none in the evidence store"}
    charts: list[PlannedChart] = []
    for i, label in enumerate(data.rows, 1):
        chart = sector_vs_benchmark(data, label, i)
        if chart:
            charts.append(chart)
    for chart in (compounded_chart(data, 900), qtd_chart(data, 901)):
        if chart:
            charts.append(chart)
    note = {"dataset": data.source_id, "quarters": data.quarters, "sectors": len(data.rows),
            "benchmark": BENCHMARK in data.rows, "qtdAsOf": data.qtd_as_of, "charts": len(charts)}
    log.info("V2 dataset charts: %d chart(s) from the fetched quarterly dataset %s (%d sectors, %d quarters)",
             len(charts), data.source_id, len(data.rows), len(data.quarters))
    return charts, note


def preassign(charts: list[PlannedChart], sections: list, *, side_charts: int = 4) -> None:
    """Deep-dives take the chart of their own sector; cross-sector charts go to the rotation overview."""
    from utils.chart_titles import canonical, clean_label, label_entities

    deep = [s for s in sections if getattr(s, "role", "") == "deep_dive" and getattr(s, "entity", "")]
    taken: set[str] = set()
    for chart in charts:
        labels = {a for a, _, _ in chart.points} - {"Nifty 50"}
        if len(labels) != 1:
            continue
        label = next(iter(labels))
        exact = canonical(clean_label(label))
        named = label_entities([label])
        match = (next((s for s in deep if s.entity == exact and s.section_id not in taken), None)
                 or next((s for s in deep if s.entity in named and s.section_id not in taken), None))
        if match:
            chart.section_id = match.section_id
            taken.add(match.section_id)
    # The cross-sector charts go to the section about rotation across sectors (any section without a
    # sector of its own: a detailed report's "leaders and laggards" deep-dive has no single entity).
    overview = [s for s in sections if getattr(s, "role", "") != "deep_dive" or not getattr(s, "entity", "")]

    def score(section) -> int:
        text = " ".join([section.title, getattr(section, "goal", ""), *getattr(section, "queries", [])]).casefold()
        return sum(word in text for word in ("rotation", "sector", "across", "leader", "laggard", "relative", "performance", "quarter"))

    if not overview:
        return
    target = max(overview, key=score)
    cross = [c for c in charts if not c.section_id and c.points and c.points[0][0] in {"Compounded return", "QTD return"}]
    for chart in cross:
        chart.section_id = target.section_id
    # Sectors without a deep-dive of their own: the two leaders and two laggards (compounded return
    # over the window) are shown beside the cross-sector charts, so the leadership claims have charts.
    if len(taken) >= 4:
        return
    compounded = next((c for c in cross if c.points[0][0] == "Compounded return"), None)
    order = [b for _, b, _ in sorted(compounded.points, key=lambda p: -p[2])] if compounded else []
    half = max(1, side_charts // 2)
    picks = [name for name in order[:half] + order[-half:] if name != "Nifty 50"]
    for name in dict.fromkeys(picks):
        chart = next((c for c in charts if not c.section_id and {a for a, _, _ in c.points} - {"Nifty 50"} == {name}), None)
        if chart:
            chart.section_id = target.section_id
