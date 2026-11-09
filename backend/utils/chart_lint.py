"""Chart quality gate, CHART LINT and chart inventory (Stage I, FIX 8).

Runs on the final chart specs before rendering (V2 assembly), deterministically:

QUALITY GATE (drop, with a log line; the caller rewrites dependent text):
  all values equal / fewer than 3 distinct values; a Total row plotted with its components; data from
  a table labelled example / illustrative / sample / hypothetical; the same values as an earlier chart.

AUTO-FIX:
  * title "<metric> (<unit>): <entities> vs <comparator>, <period>" - never "and N others", "Values (%)"
    or a bare list of dates; time series titled by their date range; counts match the plotted items
    ("10 NSE sector indices and the Nifty 50 benchmark"); the benchmark named; titles unique;
  * axis labels with units; asterisks removed; entity casing ("NIFTY AUTO" -> "Nifty Auto");
  * chart type fits the data: a ranking is a sorted horizontal bar; categories are never a line;
  * fiscal-quarter labels (heatmap columns "Q4 2024" -> "Q3 FY25");
  * data-label precision = the source's precision (``decimals``);
  * no chart-type rationale ("chronological series, so a line ...");
  * a Source line naming the dataset or publisher and its as-of date.

LINT (``lint_chart``) re-checks all of the above and returns the failures; ``inventory_row`` is one row
of the per-report CHART INVENTORY.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger("chart_lint")

BENCHMARKS = {"nifty 50", "nifty", "nifty (benchmark)", "nifty50", "sensex", "nifty 50 (benchmark)"}
TOTAL_RE = re.compile(r"(?i)^\s*(?:grand\s+)?total\b|^\s*(?:all|overall|aggregate)\s*$")
PLACEHOLDER_RE = re.compile(r"\$#%|#%|\bXX+%?|\[(?:value|x|data|number)\]|\{\{[^}]*\}\}|lorem ipsum", re.I)
FORECAST_ROW_RE = re.compile(r"(?i)\b(?:forecast|projected|projection|estimated?|expected)\b|\b20\d{2}\s?[EeFfPp]\b")
EXAMPLE_RE = re.compile(r"(?i)\b(?:example|examples|illustrative|illustration|sample|hypothetical|for instance|e\.g\.)\b")
_MONTHS = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*"
_TIME_RE = re.compile(rf"(?i)^\s*(?:\d{{1,2}}\s+{_MONTHS}\s+\d{{4}}|{_MONTHS}\s+\d{{2,4}}|Q[1-4]\s*(?:FY)?\s*'?\d{{2,4}}|"
                      rf"FY\s*'?\d{{2,4}}E?|H[12]\s*FY\d{{2,4}}|\d{{4}}(?:-\d{{2}})?|\d{{4}}-\d{{2}}-\d{{2}}|Week\s+\d+)\s*$")
_SEASONAL_RE = re.compile(rf"(?i)^\s*(?:{_MONTHS}|Q[1-4]|H[12])\s*$")
_ACRONYMS = {"FII", "DII", "FPI", "RS", "NIM", "PE", "P/E", "IT", "QTD", "YTD", "YOY", "MOM", "PSU", "FMCG", "GDP", "CPI",
             "WPI", "LDR", "AUM", "SIP", "MF", "MFS", "NSE", "BSE", "US", "INR", "OI", "EPS", "ROE", "PAT", "NPA", "GNPA", "DIIS", "FIIS"}
_GENERIC_TITLE_RE = re.compile(r"(?i)\band\s+\d+\s+others?\b|^\s*values?\b|\(values?\)")
_RATIONALE_TYPE_RE = re.compile(r"(?i)\b(?:chronological series|chart type|visual form|best fit|line chart (?:is|was)|bar chart (?:is|was)|"
                                r"(?:is|was) (?:chosen|used) (?:because|to)|so a (?:line|bar)|is the right visual)\b")


# ── small helpers ────────────────────────────────────────────────────────────────────────────────
def _series(spec: dict) -> list[dict]:
    return [s for s in (spec.get("series") or []) if isinstance(s, dict)]


def _pts(series: dict) -> list[dict]:
    return [p for p in (series.get("data") or series.get("points") or []) if isinstance(p, dict)
            and isinstance(p.get("value"), (int, float))]


def _values(spec: dict) -> list[float]:
    if spec.get("type") == "heatmap":
        return [float(v) for row in spec.get("values") or [] for v in row if isinstance(v, (int, float))]
    return [float(p["value"]) for s in _series(spec) for p in _pts(s)]


def _labels(spec: dict) -> list[str]:
    if spec.get("type") == "heatmap":
        return [str(c) for c in spec.get("columns") or []]
    first = _series(spec)[0] if _series(spec) else {}
    return [str(p.get("label") or "") for p in _pts(first)]


def strip_figure(title: str) -> tuple[str, str]:
    m = re.match(r"^\s*(Figure\s+\d+\s*:\s*)", str(title or ""))
    return (m.group(1), str(title)[m.end():]) if m else ("", str(title or ""))


def case_label(text: str) -> str:
    """'NIFTY AUTO' -> 'Nifty Auto', 'NET INVESTMENT (CR)' -> 'Net investment (CR)'; mixed case is kept."""
    text = str(text or "").replace("*", "").strip()
    letters = re.sub(r"[^A-Za-z]", "", text)
    if not letters or not letters.isupper():
        return text
    from utils.dataset_charts import pretty
    if re.match(r"(?i)^(?:nifty|bse|s&p|sensex)\b", text):
        return pretty(text)
    words = text.split()
    out = []
    for i, w in enumerate(words):
        core = re.sub(r"[^A-Za-z/]", "", w)
        if core.upper() in _ACRONYMS or (len(core) <= 2 and w.startswith("(")):
            out.append(w)
        else:
            out.append(w.capitalize() if i == 0 else w.lower())
    return " ".join(out)


def is_time_axis(labels: list[str]) -> bool:
    labels = [l for l in labels if l]
    return bool(labels) and sum(1 for l in labels if _TIME_RE.match(l)) >= 0.8 * len(labels)


def is_seasonal(labels: list[str]) -> bool:
    labels = [l for l in labels if l]
    return bool(labels) and all(_SEASONAL_RE.match(l) for l in labels)


def _decimals(spec: dict) -> int | None:
    raws = [str(p.get("evidenceValue") or "") for s in _series(spec) for p in _pts(s)]
    decs = [len(m.group(1)) for r in raws for m in [re.search(r"\.(\d+)", r)] if m]
    if decs:
        return max(decs)
    vals = _values(spec)
    if vals and all(float(v).is_integer() for v in vals):
        return 0
    return None


# ── units and metric names ───────────────────────────────────────────────────────────────────────
def infer_unit(spec: dict, context: str = "") -> str:
    y = f"{spec.get('yLabel') or ''} {' '.join(str(s.get('name') or '') for s in _series(spec))}"
    raws = " ".join(str(p.get("evidenceValue") or "") for s in _series(spec) for p in _pts(s))
    unit = str(spec.get("unit") or "").strip()
    hay = f"{y} {context}".casefold()
    if unit == "%" or "%" in y or "%" in raws or re.search(r"\b(?:return|change|growth|yield|weight|holding|allocation)\b", y.casefold()) and "%" in context:
        return "%"
    if re.search(r"(?i)\(\s*cr\s*\)|\bcrore\b|\bcr\b|rs\.?\s*crore|₹\s*cr", y + " " + context[:400]):
        return "₹ crore"
    if re.search(r"(?i)\b(?:inr|₹|rs\.?)\s*(?:bn|billion)\b|\binr\d", hay):
        return "₹ billion"
    if re.search(r"(?i)\$\s*(?:bn|billion)|us\$\s*bn", hay):
        return "US$ billion"
    if re.search(r"(?i)\bp/?e\b|\bmultiple\b", y):
        return "x"
    if re.search(r"(?i)rs[- ]?ratio|rs[- ]?momentum|rs-mom|^\s*rs\s*$", y):
        return "score, 100 = benchmark"
    if re.search(r"(?i)strength|score|index value|\blast\b|\bclose\b|level", y):
        return "index points" if re.search(r"(?i)\blast\b|\bclose\b|level|index value", y) else "score"
    if re.search(r"(?i)\b(?:fii|dii|fpi)\b|net investment|net flow|gross purchases|gross sales", y):
        return "₹ crore"
    if re.search(r"(?i)\b(?:months?|count|number|holders|companies|stocks|days)\b", y):
        return "count"
    if unit:
        return unit
    return ""


_PERIOD_IN_METRIC_RE = re.compile(
    rf"(?i),?\s*(?:\bQ[1-4] (?:FY)?\d{{2,4}} to Q[1-4] (?:FY)?\d{{2,4}}|\bto (?:\d{{1,2}} )?{_MONTHS} \d{{4}}|"
    rf"\b{_MONTHS} \d{{4}}\b|\bFY\d{{2}}E?\b|\bas of [^,()]+)")
_METRIC_CANON = [
    (re.compile(r"(?i)^qtd\b.*|^quarter[- ]to[- ]date.*"), "Quarter-to-date return"),
    (re.compile(r"(?i)^(\d+)[ -]?(day|week|month|year)s?(?: (?:return|change))?$"), lambda m: f"{m.group(1)}-{m.group(2).lower()} return"),
    (re.compile(r"(?i)^(\d+)[ -]?(day|week|month|year)s?[ -]change$"), lambda m: f"{m.group(1)}-{m.group(2).lower()} change"),
    (re.compile(r"(?i)^(\d+)([dwmy])$"), lambda m: {"d": "1-day return", "w": "1-week return", "m": "1-month return", "y": "1-year return"}[m.group(2).lower()]
     if m.group(1) == "1" else f"{m.group(1)}{m.group(2)} return"),
    (re.compile(r"(?i)^vs nifty 50,?\s*(\d+)\s*w$"), lambda m: f"Return relative to the Nifty 50 over {m.group(1)} weeks"),
    (re.compile(r"(?i)^rs$|^rs[- ]ratio$"), "RS-Ratio"),
    (re.compile(r"(?i)^change\s*%?$"), "Change"),
]


def _canon_metric(y: str) -> str:
    for pattern, repl in _METRIC_CANON:
        m = pattern.match(y.strip())
        if m:
            return repl(m) if callable(repl) else repl
    return y


def metric_name(spec: dict) -> str:
    y = str(spec.get("yLabel") or "")
    if not y or y.casefold() in {"value", "values", "return (%)"} and len(_series(spec)) == 1:
        y = str(_series(spec)[0].get("name") if _series(spec) else "") or y
    y = re.sub(r"\s*\((?:%|[^)]*(?:cr|crore|rs|₹|x|bn|billion|score)[^)]*)\)\s*$", "", y, flags=re.I)
    y = re.sub(r"\s*\(%\)", "", y)
    y = re.sub(r"\s*\([^()]*%[^()]*\)\s*$", "", y)  # "Real GDP growth (annual %)" -> "Real GDP growth"
    y = case_label(y).replace("*", "").strip(" :")
    y = re.sub(r"\s*%\s*$", "", y)
    y = _PERIOD_IN_METRIC_RE.sub("", y).strip(" ,:")
    y = _canon_metric(y)
    if not y or y.casefold() in {"value", "values"}:
        y = "Return" if spec.get("unit") == "%" else "Value"
    if y.casefold() == "return" and re.search(r"(?i)quarter", str(spec.get("xLabel") or "")):
        y = "Quarterly return"
    return y[0].upper() + y[1:]


def entity_noun(spec: dict, n: int) -> str:
    x = case_label(str(spec.get("xLabel") or "")).casefold().strip()
    table = {"sector": "sectors", "sector index": "NSE sector indices", "index": "indices", "index / sector": "indices and sectors",
             "nse sector index": "NSE sector indices", "type of dii": "DII categories", "company": "companies", "company name": "companies",
             "stock": "stocks", "defence stock": "defence stocks", "group": "groups", "sector / segment": "sectors and segments",
             "developer / index": "developers and indices", "mutual fund / scheme tracker": "mutual fund schemes", "name": "holders",
             "dii entity": "DII entities", "it sub-segment": "IT sub-segments"}
    noun = table.get(x)
    if not noun:
        noun = (x + "s") if x and not x.endswith("s") else (x or "items")
    if n == 1:
        noun = re.sub(r"(?:ies)$", "y", noun) if noun.endswith("ies") else noun.rstrip("s")
    return noun


def _period_from_title(title: str) -> str:
    rest = strip_figure(title)[1]
    for pat in (r"Q[1-4] FY\d{2} to Q[1-4] FY\d{2}", r"\bQ[1-4] \d{4} to Q[1-4] \d{4}", r"\bto (?:\d{1,2} )?" + _MONTHS + r" \d{4}",
                r"as of [^,()]+", r"\b" + _MONTHS + r" \d{4}\b", r"\bFY\d{2}(?:E)?\b"):
        m = re.search(pat, rest, flags=re.I)
        if m:
            from utils.period_hygiene import fy_labels_in
            return fy_labels_in(m.group(0))
    return ""


def _short_range(first: str, last: str) -> str:
    a = re.match(rf"^(\d{{1,2}}) ({_MONTHS}) (\d{{4}})$", first)
    b = re.match(rf"^(\d{{1,2}}) ({_MONTHS}) (\d{{4}})$", last)
    if a and b and a.group(2) == b.group(2) and a.group(3) == b.group(3):
        return f"{a.group(1)}-{b.group(1)} {a.group(2)} {a.group(3)}"
    return f"{first} to {last}"


# ── quality gate ─────────────────────────────────────────────────────────────────────────────────
def quality_reason(spec: dict, passage_text: str = "", earlier: list[list[float]] | None = None) -> str:
    vals = _values(spec)
    labels = _labels(spec)
    if len({round(v, 6) for v in vals}) < 3:
        return "fewer than 3 distinct values (or all values equal)"
    if spec.get("type") != "heatmap" and len(_series(spec)) == 1 and any(TOTAL_RE.search(l) for l in labels) and len(labels) >= 3:
        return "a Total row is plotted with its components"
    if vals and all(float(v).is_integer() and 1800 <= v <= 2100 for v in vals):
        return "the values are years (an attribute such as a founding year, not data)"
    if sum(1 for l in labels if re.fullmatch(r"\s*\d{1,2}[.)]?\s*", l)) >= max(2, len(labels) // 2):
        return "the row labels are list numbers, not entities or periods"
    heading = str(passage_text or "")[:400]
    quotes = " ".join(str(p.get("evidenceQuote") or "") for s in _series(spec) for p in _pts(s))
    if EXAMPLE_RE.search(heading) or EXAMPLE_RE.search(quotes):
        return "the data comes from an example / illustrative table"
    point_quotes = [str(p.get("evidenceQuote") or "") for s in _series(spec) for p in _pts(s)]
    # Stage J: a teaser / template table ("$#%", "XX%", "[value]") is not reliable data
    if any(PLACEHOLDER_RE.search(q) for q in point_quotes):
        return "the source table contains template placeholders (unreliable data)"
    # Stage J: values the source itself labels as forecasts / estimates are never shown as observed data
    flagged = sum(1 for q in point_quotes if FORECAST_ROW_RE.search(q))
    if point_quotes and flagged * 2 >= len(point_quotes) and not re.search(r"(?i)forecast|projection|estimate", str(spec.get("title"))):
        return "the source labels these values as forecasts / estimates"
    mine = sorted(round(v, 4) for v in vals)
    for other in earlier or []:
        if mine == other:
            return "the same values as an earlier chart"
    return ""


# ── auto-fix ─────────────────────────────────────────────────────────────────────────────────────
def fix_chart(spec: dict, *, passage_text: str = "", manifest: dict | None = None) -> tuple[dict, list[str]]:
    from utils.period_hygiene import fy_label
    spec = dict(spec)
    fixes: list[str] = []
    prefix, old_rest = strip_figure(spec.get("title") or "")
    kind = str(spec.get("type") or "")
    series = _series(spec)
    # entity casing and asterisks in labels / legend names
    for s in series:
        name = case_label(s.get("name"))
        if name != s.get("name"):
            s["name"] = name
        for p in _pts(s):
            lab = case_label(p.get("label"))
            if lab != p.get("label"):
                p["label"] = lab
    if kind == "heatmap":
        cols = [fy_label(str(c)) for c in spec.get("columns") or []]
        if cols != list(spec.get("columns") or []):
            spec["columns"] = cols
            fixes.append("heatmap columns in fiscal-quarter labels")
        spec["rows"] = [case_label(r) for r in spec.get("rows") or []]
    labels = _labels(spec)
    time_axis = is_time_axis(labels)
    unit = str(spec.get("unitLabel") or "").strip() or infer_unit(spec, passage_text)  # a dataset's own unit wins
    metric = metric_name(spec)
    if unit and metric.casefold().endswith(f"({unit.casefold()})"):
        metric = metric[: -len(unit) - 2].rstrip()  # "High-technology exports (current US$)" + "(current US$)"
    source_titles = " ".join(str((manifest or {}).get(sid, {}).get("title") or "") for sid in _ids(spec))
    if re.fullmatch(r"Q[1-4] 20\d{2}", metric):  # a period used as the metric name
        period_label = fy_label(metric)
        metric = "Quarterly return"
        unit = unit or ("%" if spec.get("unit") == "%" or "%" in str(spec.get("yLabel") or "") else unit)
        spec["_period"] = period_label
    if not unit and re.search(r"(?i)\bp/?e\b|price[- ]to[- ]earnings", f"{passage_text[:600]} {old_rest} {source_titles}"):
        vals = _values(spec)
        if vals and all(0 < v < 200 for v in vals):
            metric, unit = (f"P/E ratio, {metric}" if metric.casefold() not in {"p/e", "pe", "p/e ratio"} else "P/E ratio"), "x"
    if not unit:
        unit = infer_unit({"yLabel": metric, "series": []}, passage_text)
    unit_txt = f" ({unit})" if unit else ""
    # chart type fits the data
    if len(series) == 1 and kind in {"line", "area", "spline"} and not time_axis:
        spec["type"], kind = "horizontal_bar", "horizontal_bar"
        fixes.append("categories shown as bars, not a line")
    if len(series) == 1 and kind in {"bar", "horizontal_bar"} and not time_axis and not is_seasonal(labels) and len(labels) >= 3:
        data = _pts(series[0])
        ordered = sorted(data, key=lambda p: p["value"], reverse=True)
        if kind != "horizontal_bar" or ordered != data:
            series[0]["data"] = ordered
            spec["type"], kind = "horizontal_bar", "horizontal_bar"
            fixes.append("ranking shown as a sorted horizontal bar")
    # title
    period = spec.pop("_period", "") or _period_from_title(spec.get("title") or "") or _as_of(spec, manifest or {})
    qtd = " (QTD, partial)" if re.search(r"(?i)\bQTD\b|quarter-to-date", old_rest) else ""
    if kind == "heatmap":
        rows = spec.get("rows") or []
        bench = [r for r in rows if r.casefold() in BENCHMARKS]
        n = len(rows) - len(bench)
        cols = spec.get("columns") or []
        span = f"{cols[0]} to {cols[-1]}" if cols else period
        noun = "NSE sector indices" if any(r.casefold().startswith("nifty") for r in rows) else entity_noun(spec, n)
        title = f"Quarterly return{unit_txt}: {n} {noun}" + (" and the Nifty 50 benchmark" if bench else "") + (f", {span}" if span else "")
        spec["xLabel"], spec["yLabel"] = "Fiscal quarter", "Sector index"
    elif len(series) == 2 and any(str(s.get("name") or "").casefold() in BENCHMARKS for s in series):
        a, b = series[0].get("name"), series[1].get("name")
        span = period or (f"{labels[0]} to {labels[-1]}" if labels else "")
        title = f"{metric}{unit_txt}: {a} vs {b} (benchmark)" + (f", {span}" if span else "")
    elif time_axis and labels:
        names = [str(x.get("name") or "") for x in series if x.get("name")]
        # the plotted entities are named (one or many), unless the single series just repeats the metric
        names = [n for n in names if n.casefold() not in metric.casefold() and metric.casefold() not in n.casefold()]
        who = (": " + (", ".join(names[:-1]) + " and " + names[-1] if len(names) > 1 else names[0])) if names else ""
        title = f"{metric}{unit_txt}{who}, {_short_range(labels[0], labels[-1])}{qtd}"
    else:
        names = [l for l in labels if l]
        bench = [l for l in names if l.casefold() in BENCHMARKS]
        n = len(names) - len(bench)
        is_sector_index = sum(1 for l in names if l.casefold().startswith("nifty")) >= 0.6 * max(1, len(names))
        noun = "NSE sector indices" if is_sector_index else entity_noun(spec, n)
        span = period
        title = f"{metric}{unit_txt}: {n} {noun}" + (" and the Nifty 50 benchmark" if bench else "") + (f", {span}" if span else "") + (qtd if qtd and qtd not in span else "")
    title = re.sub(r"\s+", " ", title).strip().replace(" ,", ",")
    if title != old_rest:
        fixes.append(f"title: '{old_rest[:70]}' -> '{title[:90]}'")
        spec["title"] = prefix + title
    # axes
    if kind != "heatmap":
        x = case_label(str(spec.get("xLabel") or "")) or ("Date" if time_axis else "Category")
        if time_axis and not re.search(r"(?i)date|quarter|month|period|fortnight|week|year", x):
            x = "Date"
        y = f"{metric}{unit_txt}"
        if spec.get("type") == "horizontal_bar":
            pass  # the renderer swaps axes; the labels keep their meaning (category / value)
        if (x, y) != (spec.get("xLabel"), spec.get("yLabel")):
            fixes.append(f"axes: '{spec.get('xLabel')}' / '{spec.get('yLabel')}' -> '{x}' / '{y}'")
            spec["xLabel"], spec["yLabel"] = x, y
    if unit and not spec.get("unit"):
        spec["unit"] = unit if unit in {"%", "x"} else spec.get("unit") or ""
    dec = _decimals(spec)
    if dec is not None:
        spec["decimals"] = min(dec, 2)
    if spec.get("rationale") and _RATIONALE_TYPE_RE.search(str(spec["rationale"])):
        spec["rationale"] = ""
        fixes.append("chart-type rationale removed")
    # source line
    note = source_line(spec, manifest or {})
    if note:
        # the human source line lives in its own field: utils.source_ids normalises "source" to S# ids
        spec["sourceLine"] = note
    return spec, fixes


_TODAY = {"value": ""}


def _fmt_date(raw: str) -> str:
    from datetime import datetime
    raw = str(raw or "")[:10]
    try:
        return f"{datetime.strptime(raw, '%Y-%m-%d'):%d %b %Y}"
    except ValueError:
        return ""


def _source_date(row: dict) -> str:
    """'as of 29 Oct 2026' (published), 'updated 06 Nov 2026' (page modified) or 'retrieved 08 Nov 2026'."""
    for key, word in (("published", "as of"), ("publication_date", "as of"), ("publisher_page_published_at", "as of"),
                      ("publisherPagePublishedAt", "as of"), ("publisher_page_modified_at", "updated")):
        when = _fmt_date(row.get(key))
        if when:
            return f"{word} {when}"
    when = _fmt_date(_TODAY["value"])
    return f"retrieved {when}" if when else ""


def _ids(spec: dict) -> list[str]:
    ids = spec.get("sourceIds") or spec.get("source") or []
    if isinstance(ids, str):
        ids = re.findall(r"S\d+", ids)
    return [str(i).upper() for i in ids if re.fullmatch(r"(?i)S\d+", str(i))]


def _as_of(spec: dict, manifest: dict) -> str:
    """The period of a snapshot chart with none of its own: its source's date."""
    for sid in _ids(spec):
        when = _source_date(manifest.get(sid) or {})
        if when:
            return when
    return ""


def source_line(spec: dict, manifest: dict) -> str:
    ids = spec.get("sourceIds") or spec.get("source") or []
    if isinstance(ids, str):
        ids = re.findall(r"S\d+", ids)
    ids = [str(i).upper() for i in ids if re.fullmatch(r"(?i)S\d+", str(i))]
    if not ids:
        return ""
    parts = []
    for sid in dict.fromkeys(ids):
        row = manifest.get(sid) or {}
        title = str(row.get("title") or "")
        publisher = str(row.get("publisher") or "")
        if str(row.get("url") or "").startswith("internal://") or re.search(r"(?i)calculated", publisher + title):
            name = "NSE sector index closes (Yahoo Finance; official Nifty Indices history for gaps), calculated"
        else:
            name = publisher or title or "Published source"
            if publisher and title and publisher.casefold() not in title.casefold():
                name = f"{publisher}, {title[:90]}"
            elif publisher and title and title.startswith(f"{publisher}: "):
                name = title[:110]  # an official dataset: "World Bank: GDP growth (annual %), 2023-2025"
        labels = _labels(spec)
        period = _period_from_title(spec.get("title") or "")
        date = _source_date(row) if row else _source_date({})
        if re.search(r"(?i)\bto\b", period) and name.endswith("calculated"):
            when = f", covering {period}" + (f", {date}" if date else "")
        else:
            when = f", {date}" if date else (f", covering {period}" if period else "")
        parts.append(f"{name}{when} [{sid}]")
    return "; ".join(parts)


# ── lint (re-check) ──────────────────────────────────────────────────────────────────────────────
def lint_chart(spec: dict, seen_titles: set[str] | None = None) -> list[str]:
    issues = []
    prefix, rest = strip_figure(spec.get("title") or "")
    if not prefix:
        issues.append("no 'Figure N:' number")
    if _GENERIC_TITLE_RE.search(rest):
        issues.append("generic title ('and N others' / 'Values')")
    if not re.search(r"\(([^)]+)\)", rest.split(":")[0]) and spec.get("type") != "heatmap":
        issues.append("title has no unit")
    labels = _labels(spec)
    if spec.get("type") != "heatmap" and is_time_axis(labels) and re.search(r"\d{1,2} \w{3} \d{4}, \d{1,2} \w{3} \d{4}", rest):
        issues.append("time series titled by a list of dates")
    m = re.search(r":\s*(\d+)\s+([^,]+?)(?: and the Nifty 50 benchmark)?(?:,|$)", rest)
    if m and spec.get("type") != "heatmap":
        bench = sum(1 for l in labels if l.casefold() in BENCHMARKS)
        if int(m.group(1)) != len([l for l in labels if l]) - bench:
            issues.append(f"title count {m.group(1)} does not match {len(labels) - bench} plotted items")
    if spec.get("type") != "heatmap":
        if not str(spec.get("xLabel") or "").strip():
            issues.append("x axis has no label")
        if not re.search(r"\([^)]+\)", str(spec.get("yLabel") or "")):
            issues.append("y axis has no unit")
    blob = " ".join([rest, str(spec.get("xLabel") or ""), str(spec.get("yLabel") or "")]
                    + [str(s.get("name") or "") for s in _series(spec)] + labels)
    if "*" in blob:
        issues.append("unexplained asterisk")
    if any(re.sub(r"[^A-Za-z]", "", l).isupper() and len(re.sub(r"[^A-Za-z]", "", l)) > 5 and l.casefold().startswith("nifty")
           for l in labels + [str(s.get("name") or "") for s in _series(spec)]):
        issues.append("entity casing (all caps)")
    if spec.get("type") in {"line", "area", "spline"} and len(_series(spec)) == 1 and not is_time_axis(labels):
        issues.append("categories drawn as a line")
    if spec.get("rationale") and _RATIONALE_TYPE_RE.search(str(spec.get("rationale"))):
        issues.append("chart-type rationale text")
    src = str(spec.get("sourceLine") or spec.get("source") or "")
    if not src.strip() or re.fullmatch(r"(?i)\s*calculated\s*", src) or not re.search(r"\[S\d+\]", src):
        issues.append("source line missing or only 'Calculated'")
    elif not re.search(r"(?i)\b(?:as of|updated|retrieved|covering)\b", src):
        issues.append("source line has no as-of date")
    if seen_titles is not None:
        if rest.casefold() in seen_titles:
            issues.append("duplicate title")
        seen_titles.add(rest.casefold())
    return issues


def inventory_row(spec: dict, issues: list[str]) -> dict:
    prefix, rest = strip_figure(spec.get("title") or "")
    return {"figure": prefix.strip(" :") or "-", "title": rest, "type": spec.get("type"), "x": spec.get("xLabel"),
            "y": spec.get("yLabel"), "unit": spec.get("unit") or "", "period": _period_from_title(spec.get("title") or "")
            or (lambda l: f"{l[0]} to {l[-1]}" if l and is_time_axis(l) else "")(_labels(spec)),
            "source": str(spec.get("sourceLine") or spec.get("source") or "")[:120], "lint": "PASS" if not issues else "FAIL: " + "; ".join(issues)}


def unique_titles(charts: list[dict]) -> list[str]:
    """Second and later charts with the same title get their source/as-of appended."""
    seen: dict[str, int] = {}
    fixes = []
    for chart in charts:
        prefix, rest = strip_figure(chart.get("title") or "")
        key = rest.casefold()
        if key in seen:
            src = re.sub(r"\s*\[S\d+\]", "", str(chart.get("sourceLine") or chart.get("source") or "")).split(";")[0].split(",")[0].strip()
            extra = f" ({src})" if src else f" (view {seen[key] + 1})"
            chart["title"] = prefix + rest + extra
            fixes.append(f"duplicate title made unique: {rest[:60]}{extra}")
            seen[key] += 1
        else:
            seen[key] = 1
    return fixes


def run_chart_lint(charts: list[dict], *, passage_text=lambda spec: "", manifest: dict | None = None,
                   today: str = "") -> tuple[list[dict], list[dict], dict]:
    """Quality gate + auto-fix + lint. Returns (kept charts, dropped [{index, title, reason}], log)."""
    _TODAY["value"] = today
    kept, dropped, fixes = [], [], []
    earlier: list[list[float]] = []
    for i, spec in enumerate(charts or [], 1):
        if not isinstance(spec, dict):
            kept.append(spec)
            continue
        text = passage_text(spec) or ""
        reason = quality_reason(spec, text, earlier)
        if reason:
            dropped.append({"index": i, "title": spec.get("title"), "reason": reason})
            log.warning("Chart quality gate: dropped %s - %s", str(spec.get("title"))[:80], reason)
            continue
        earlier.append(sorted(round(v, 4) for v in _values(spec)))
        fixed, f = fix_chart(spec, passage_text=text, manifest=manifest)
        fixes += [f"{strip_figure(fixed.get('title'))[0].strip(' :') or 'chart'}: {x}" for x in f]
        kept.append(fixed)
    fixes += unique_titles([c for c in kept if isinstance(c, dict)])
    return kept, dropped, {"fixed": fixes}


def lint_all(charts: list[dict]) -> tuple[list[dict], int]:
    seen: set[str] = set()
    rows, failing = [], 0
    for spec in charts or []:
        if isinstance(spec, dict):
            issues = lint_chart(spec, seen)
            failing += bool(issues)
            rows.append(inventory_row(spec, issues))
    return rows, failing


# ── tables: their own "Table N" sequence, a caption, a header and a source ───────────────────────
_SEP_ROW_RE = re.compile(r"^\s*\|?\s*:?-{3,}")
_CAPTION_RE = re.compile(r"^\s*\*\*(?:Table|Figure)(?:\s+(\d+))?\s*:\s*(.+?)\*\*\s*$")
_HEADING_CAPTION_RE = re.compile(r"^\s*(#{3,4})\s+(?:Table\s+\d+:\s*)?(.+?)\s*$")
_CITE_IN_RE = re.compile(r"\[\s*(S\d+(?:\.\d+)?(?:\s*[,;]\s*S\d+(?:\.\d+)?)*)\s*\]")


def _derived_caption(header: str) -> str:
    cols = [c.strip() for c in header.strip().strip("|").split("|") if c.strip()]
    cols = [re.sub(r"\s*\[S\d+[^\]]*\]", "", c) for c in cols]
    if len(cols) >= 2:
        rest = cols[1:4]
        tail = ", ".join(rest[:-1]) + (" and " if len(rest) > 1 else "") + rest[-1]
        return f"{tail}, by {cols[0].lower() if not cols[0].isupper() else cols[0]}"
    return cols[0] if cols else "Data"


def number_tables(report: str) -> tuple[str, list[str], list[dict]]:
    """Every table gets "**Table N: caption**" (its own sequence; a table captioned as a Figure is renamed),
    a Source line (from the citations in the table or in the paragraph before it), or is dropped when it
    has no source. Prose references "Table k" follow the new numbers; unknown ones become "the table".
    Appendix tables keep their heading and get the number in it."""
    lines = str(report or "").split("\n")
    out: list[str] = []
    actions: list[str] = []
    inventory: list[dict] = []
    mapping: dict[int, int] = {}
    number = 0
    i = 0
    while i < len(lines):
        line = lines[i]
        if not (line.strip().startswith("|") and i + 1 < len(lines) and _SEP_ROW_RE.match(lines[i + 1])):
            out.append(line)
            i += 1
            continue
        j = i + 2
        # two tables stacked without a blank line: the block ends before the next header + separator
        while j < len(lines) and lines[j].strip().startswith("|") and not (j + 1 < len(lines) and _SEP_ROW_RE.match(lines[j + 1])):
            j += 1
        block = lines[i:j]
        # caption: the nearest non-blank line before the table
        k = len(out) - 1
        while k >= 0 and not out[k].strip():
            k -= 1
        caption, heading, old_no = "", "", None
        if k >= 0:
            m = _CAPTION_RE.match(out[k])
            h = _HEADING_CAPTION_RE.match(out[k])
            if m:
                old_no = int(m.group(1)) if m.group(1) else None
                caption = m.group(2).strip()
                if out[k].lstrip().startswith("**Figure"):
                    actions.append(f"table captioned as a figure renamed: {caption[:60]}")
                del out[k]
            elif h:
                heading, caption = h.group(1), h.group(2).strip()
                del out[k]
        # source: a Source line after the table, the table's own citations, or the paragraph before it
        after = j
        while after < len(lines) and not lines[after].strip():
            after += 1
        has_source = after < len(lines) and re.match(r"^\s*\*?Source:", lines[after]) and _CITE_IN_RE.search(lines[after])
        cites = [c for row in block for c in _CITE_IN_RE.findall(row)] + _CITE_IN_RE.findall(caption)
        if not cites:
            back = len(out) - 1
            while back >= 0 and not out[back].strip():
                back -= 1
            if back >= 0 and not out[back].lstrip().startswith(("|", "#", ">")):
                cites = _CITE_IN_RE.findall(out[back])[-1:]
        if any("[[" in row for row in block):
            actions.append(f"table dropped (unsupported values): {(caption or block[0])[:70]}")
            i = after + 1 if has_source else j
            continue
        if not has_source and not cites:
            actions.append(f"table dropped (no source): {(caption or block[0])[:70]}")
            i = j
            continue
        number += 1
        if old_no:
            mapping[old_no] = number
        title = caption or _derived_caption(block[0])
        if not caption:
            actions.append(f"caption added to Table {number}: {title[:60]}")
        if heading:
            out.append(f"{heading} Table {number}: {title}")
        else:
            out.append(f"**Table {number}: {title}**")
        out.extend(block)
        source_text = lines[after] if has_source else ""
        if not has_source:
            ids = sorted({sid.strip() for c in cites for sid in re.split(r"\s*[,;]\s*", c)},
                         key=lambda x: [int(t) for t in re.findall(r"\d+", x)])
            source_text = f"Source: [{', '.join(ids)}]"
            out.append(source_text)
        inventory.append({"table": f"Table {number}", "title": title, "columns": len(block[0].strip().strip("|").split("|")),
                          "rows": max(0, len(block) - 2), "source": source_text.strip()[:100]})
        i = j
    # an orphan caption (its table was removed by a later stage) is dropped
    cleaned: list[str] = []
    for n, line in enumerate(out):
        if re.match(r"^\s*\*\*(?:Table|Figure)(?:\s+\d+)?:\s*.+\*\*\s*$", line) or re.match(r"^\s*#{3,4}\s+Table \d+:", line):
            k = n + 1
            while k < len(out) and not out[k].strip():
                k += 1
            if not (k < len(out) and out[k].strip().startswith("|")) and "Table" in line[:12]:
                actions.append(f"orphan table caption removed: {line.strip()[:60]}")
                continue
        cleaned.append(line)
    out = cleaned
    text = "\n".join(out)
    text = re.sub(r"\bTable (\d+)\b(?!:)", lambda m: f"Table {mapping[int(m.group(1))]}" if int(m.group(1)) in mapping
                  else ("the table" if int(m.group(1)) > number else m.group(0)), text)
    return text, actions, inventory
