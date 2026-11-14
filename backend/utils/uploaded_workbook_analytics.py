"""Deterministic, evidence-traceable analytics for uploaded Excel CSV text.

The frontend sends workbooks as ``[File: ...]`` / ``[Sheet: ...]`` blocks of CSV.
This module extracts structured PMS strategy rows directly from that text so the
report writer and chart renderer do not have to invent numeric comparisons.
"""
from __future__ import annotations

import csv
import io
import math
import re
from collections import OrderedDict
from statistics import mean, median
from typing import Any

_RETURN_PERIODS = (
    "1 Mth", "3 Mth", "6 Mth", "1 Yr", "2 Yr", "3 Yr", "5 Yr", "10 Yr", "Since Incp.",
)
_RETURN_ALIASES = {
    "1 mth": "1 Mth", "1 month": "1 Mth", "1 mo": "1 Mth",
    "3 mth": "3 Mth", "3 month": "3 Mth",
    "6 mth": "6 Mth", "6 month": "6 Mth",
    "1 yr": "1 Yr", "1 year": "1 Yr",
    "2 yr": "2 Yr", "2 year": "2 Yr",
    "3 yr": "3 Yr", "3 year": "3 Yr",
    "5 yr": "5 Yr", "5 year": "5 Yr",
    "10 yr": "10 Yr", "10 year": "10 Yr",
    "since incp.": "Since Incp.", "since inception": "Since Incp.", "inception": "Since Incp.",
}
MIN_CATEGORY_SIZE = 3  # categories with fewer strategies are listed but not ranked
# Exact plotted rows, calculation quotes and the analytics tables an uploaded file's charts are verified
# against. At 16,000 chars the excerpt was already full before the analytics were added, so correct
# calculated chart points were rejected as unsupported. One limit for every step of the chain.
UPLOAD_CHART_EVIDENCE_CHARS = 64_000
_NA = {"", "na", "n/a", "nd", "n.d.", "--", "—", "-", "undisc.", "undisclosed", "not disclosed", "not available"}
_FILE_MARKER = re.compile(r"(?m)^\[File:\s*(.*?)\]\s*$")
_SHEET_MARKER = re.compile(r"(?m)^\[Sheet:\s*(.*?)\]\s*$")


def _norm(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _chart_words(value: object) -> str:
    """Normalize chart labels for semantic matching without relying on titles alone."""
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()


def _return_periods_in_text(value: object) -> set[str]:
    words = f" {_chart_words(value)} "
    aliases = dict(_RETURN_ALIASES)
    aliases.update({
        "one month": "1 Mth", "three months": "3 Mth", "six months": "6 Mth",
        "one year": "1 Yr", "two years": "2 Yr", "three years": "3 Yr",
        "five years": "5 Yr", "ten years": "10 Yr",
    })
    return {
        period for alias, period in aliases.items()
        if f" {_chart_words(alias)} " in words
    }


def _chart_series(chart: dict) -> list[dict]:
    return [series for series in (chart.get("series") or []) if isinstance(series, dict)]


def _chart_points(series: dict) -> list[dict]:
    values = series.get("data") if isinstance(series.get("data"), list) else series.get("values")
    return [point for point in (values or []) if isinstance(point, dict)]


def _category_words(value: object) -> set[str]:
    return {
        word for word in _chart_words(value).split()
        if word not in {"pms", "strategy", "strategies", "category", "categories", "investment"}
    }


def _close_chart_value(left: object, right: object) -> bool:
    try:
        a = float(str(left).replace(",", "").strip().rstrip("%"))
        b = float(str(right).replace(",", "").strip().rstrip("%"))
    except (TypeError, ValueError):
        return False
    return abs(a - b) <= max(0.01, abs(b) * 0.002)


def _quantile(values: list[float], fraction: float) -> float:
    """Linear-interpolated quantile, stable for small as well as large samples."""
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise ValueError("quantile requires at least one value")
    position = min(1.0, max(0.0, float(fraction))) * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _normalized_chart_point_label(point: dict) -> str:
    label = str(point.get("label") or point.get("name") or point.get("x") or "").strip()
    period = _return_period_alias(label)
    if period:
        return f"period:{period}"
    words = _category_words(label)
    return "label:" + " ".join(sorted(words)) if words else ""


def _chart_topic(chart: dict) -> str:
    series = _chart_series(chart)
    descriptor = " ".join([
        str(chart.get("title") or ""), str(chart.get("xLabel") or ""),
        str(chart.get("yLabel") or ""),
        *(str(item.get("name") or "") for item in series),
    ])
    words = _chart_words(descriptor)
    if "aum" in words or "assets under management" in words:
        return "aum"
    if "category" in words or "categories" in words or "categorical" in words:
        return "category"
    if "benchmark" in words or "benchmarks" in words:
        return "benchmark"
    if ("top" in words or "rank" in words) and ("strategy" in words or "strategies" in words):
        return "strategy_ranking"
    period_labels = sum(
        bool(_return_period_alias(point.get("label") or point.get("name") or point.get("x")))
        for item in series for point in _chart_points(item)
    )
    if "horizon" in words or "distribution" in words or period_labels >= 2:
        return "return_horizon"
    return ""


def _series_observations_match(left: dict, right: dict) -> bool:
    left_points = _chart_points(left)
    right_points = _chart_points(right)
    if len(left_points) < 3 or len(right_points) < 3:
        return False
    by_label: dict[str, list[tuple[int, object]]] = {}
    for index, point in enumerate(right_points):
        label = _normalized_chart_point_label(point)
        if label:
            by_label.setdefault(label, []).append((index, point.get("value", point.get("y"))))
    used: set[int] = set()
    matched = 0
    for point in left_points:
        label = _normalized_chart_point_label(point)
        if not label:
            continue
        for index, value in by_label.get(label, []):
            if index not in used and _close_chart_value(point.get("value", point.get("y")), value):
                used.add(index)
                matched += 1
                break
    return matched >= 3 and matched / len(left_points) >= 0.75


def _duplicates_category_medians(chart: dict, workbook_charts: list[dict]) -> bool:
    title_and_axes = " ".join(str(chart.get(key) or "") for key in ("title", "xLabel", "yLabel"))
    model_series = _chart_series(chart)
    if "category" not in _chart_words(title_and_axes) and not any(
        "categor" in _chart_words(series.get("name")) for series in model_series
    ):
        return False
    if "median" not in _chart_words(title_and_axes) and not any(
        "median" in _chart_words(series.get("name")) for series in model_series
    ):
        return False

    model_median_series = [
        series for series in model_series
        if "median" in _chart_words(series.get("name"))
    ] or model_series[:1]
    model_points = [point for series in model_median_series for point in _chart_points(series)]
    if len(model_points) < 3:
        return False

    for baseline in workbook_charts:
        if baseline.get("chartRole") != "snapshot_summary":
            continue
        baseline_title = _chart_words(baseline.get("title"))
        if "categor" not in baseline_title:
            continue
        median_series = [
            series for series in _chart_series(baseline)
            if "median" in _chart_words(series.get("name"))
        ]
        baseline_points = [point for series in median_series for point in _chart_points(series)]
        if not baseline_points:
            continue
        matched = 0
        for point in model_points:
            label = _category_words(point.get("label") or point.get("name") or point.get("x"))
            if not label:
                continue
            for reference in baseline_points:
                reference_label = _category_words(
                    reference.get("label") or reference.get("name") or reference.get("x")
                )
                labels_match = bool(label and reference_label and (label <= reference_label or reference_label <= label))
                if labels_match and _close_chart_value(
                    point.get("value", point.get("y")), reference.get("value", reference.get("y")),
                ):
                    matched += 1
                    break
        if matched >= 3 and matched / len(model_points) >= 0.75:
            return True
    return False


def _duplicates_deterministic_observations(chart: dict, workbook_charts: list[dict]) -> bool:
    """Catch equivalent model charts even when their titles or chart types differ."""
    topic = _chart_topic(chart)
    if not topic:
        return False
    for baseline in workbook_charts:
        if not isinstance(baseline, dict):
            continue
        # ``workbook_charts`` is supplied directly by build_attachment_analytics
        # before the route adds its publication-only marker. Provenance here is
        # the explicit upload origin on the server-built chart itself.
        if (
            baseline.get("_serverWorkbookEvidence") is not True
            and str(baseline.get("sourceOrigin") or "").strip().casefold()
            not in {"uploaded_workbook", "uploaded_attachment"}
        ):
            continue
        if _chart_topic(baseline) != topic:
            continue
        if any(
            _series_observations_match(model_series, baseline_series)
            for model_series in _chart_series(chart)
            for baseline_series in _chart_series(baseline)
        ):
            return True
    return False


def pms_model_chart_keep_mask(
    model_charts: list | None, workbook_charts: list | None,
) -> tuple[list[bool], list[str]]:
    """Reject model charts that duplicate attachment facts or misuse lookback periods.

    Lookback horizons in a PMS snapshot are independent return windows, not a
    chronological axis. Server-built workbook charts remain authoritative; this
    only filters redundant or misleading model-generated charts in PMS reports.
    """
    charts = list(model_charts or [])
    deterministic = [chart for chart in (workbook_charts or []) if isinstance(chart, dict)]
    deterministic_titles = {
        _chart_words(chart.get("title")) for chart in deterministic if _chart_words(chart.get("title"))
    }
    ranking_periods = {
        str(chart.get("workbookMetric") or "")
        for chart in deterministic
        if chart.get("chartRole") == "strategy_ranking" and chart.get("workbookMetric")
    }
    keep_mask: list[bool] = []
    reasons: list[str] = []
    time_series_types = {"line", "spline", "area", "stacked_area", "step_line"}

    for chart in charts:
        if not isinstance(chart, dict) or chart.get("_serverWorkbookEvidence") is True:
            keep_mask.append(True)
            reasons.append("")
            continue
        title = str(chart.get("title") or "").strip()
        title_words = _chart_words(title)
        reason = ""
        if title_words and title_words in deterministic_titles:
            reason = "same title as a deterministic workbook chart"

        series = _chart_series(chart)
        first_points = _chart_points(series[0]) if series else []
        labels = [str(point.get("label") or point.get("name") or point.get("x") or "") for point in first_points]
        label_periods = [_RETURN_ALIASES.get(_chart_words(label)) for label in labels]
        matched_horizons = sum(period is not None for period in label_periods)
        horizon_axis = "horizon" in _chart_words(" ".join((title, str(chart.get("xLabel") or ""))))
        chart_type = _chart_words(chart.get("type")).replace(" ", "_")
        if (
            not reason and chart_type in time_series_types and len(labels) >= 3
            and matched_horizons >= 3 and matched_horizons / len(labels) >= 0.75
            and horizon_axis
        ):
            reason = "reported lookback windows are separate snapshots, not a time series"

        descriptor = " ".join([
            title, str(chart.get("xLabel") or ""), str(chart.get("yLabel") or ""),
            *(str(item.get("name") or "") for item in series),
        ])
        words = _chart_words(descriptor)
        is_strategy_ranking = (
            ("top" in words or "rank" in words)
            and ("strategies" in words or "strategy" in words)
        )
        if not reason and is_strategy_ranking:
            matching_periods = _return_periods_in_text(descriptor) & ranking_periods
            if matching_periods:
                period = sorted(matching_periods)[0]
                reason = f"duplicate {period} strategy ranking already computed from workbook rows"

        if not reason and _duplicates_deterministic_observations(chart, deterministic):
            reason = "observations duplicate a deterministic workbook chart"
        # Same category medians under abbreviated labels or rounded values (the exact-label check above misses them).
        if not reason and _duplicates_category_medians(chart, deterministic):
            reason = "category medians duplicate the deterministic workbook mean/median comparison"

        keep_mask.append(not reason)
        reasons.append(reason)
    return keep_mask, reasons


def _benchmark_key(value: object) -> str:
    """Normalize common long/short benchmark labels for workbook row matching."""
    text = _norm(value)
    text = re.sub(r"(?i)\b(?:tri|idx|index)\b", " ", text)
    text = re.sub(r"(?i)\bcomp\.?\b", "composite", text)
    text = re.sub(r"(?i)\bhyb\.?\b", "hybrid", text)
    text = re.sub(r"(?<=[a-z])(?=\d)|(?<=\d)(?=[a-z])", " ", text)
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _matched_benchmark_chart(pms: dict, filename: str) -> tuple[dict | None, list[str]]:
    """Build one auditable index-vs-matched-strategy comparison from source rows.

    Benchmarks are compared only with strategies that name that same benchmark
    in the workbook. This avoids comparing (for example) debt indexes with the
    median of a mixed equity/debt PMS universe.
    """
    groups: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for row in pms.get("benchmarkRows") or []:
        if not isinstance(row, dict) or str(row.get("filename") or "").casefold() != filename.casefold():
            continue
        label = re.sub(r"\s+", " ", str(row.get("label") or "")).strip()
        key = _benchmark_key(label)
        if not key:
            continue
        group = groups.setdefault(key, {
            "label": label, "periodValues": {}, "periodRecords": {}, "conflicts": set(),
        })
        for period, raw_value in (row.get("periodValues") or {}).items():
            if period in group["conflicts"]:
                continue
            prior = group["periodValues"].get(period)
            if prior is None:
                group["periodValues"][period] = float(raw_value)
                group["periodRecords"][period] = row
            elif abs(float(prior) - float(raw_value)) > max(0.001, abs(float(prior)) * 0.002):
                # Repeated benchmark rows must agree before they are combined.
                group["conflicts"].add(period)
                group["periodValues"].pop(period, None)
                group["periodRecords"].pop(period, None)

    strategies_by_benchmark: dict[str, list[dict]] = {}
    for row in pms.get("strategies") or []:
        if not isinstance(row, dict) or str(row.get("filename") or "").casefold() != filename.casefold():
            continue
        key = _benchmark_key(row.get("benchmark"))
        if key:
            strategies_by_benchmark.setdefault(key, []).append(row)

    choices = []
    preferred_periods = ("1 Mth", "1 Yr", "3 Yr")
    for key, group in groups.items():
        matching = strategies_by_benchmark.get(key, [])
        if len(matching) < MIN_CATEGORY_SIZE:
            continue
        paired: dict[str, tuple[float, list[dict], dict]] = {}
        for period in _RETURN_PERIODS:
            benchmark_value = group["periodValues"].get(period)
            record = group["periodRecords"].get(period)
            if benchmark_value is None or not isinstance(record, dict):
                continue
            strategy_rows = [
                row for row in matching
                if period in (row.get("periodValues") or {})
            ]
            if len(strategy_rows) >= MIN_CATEGORY_SIZE:
                paired[period] = (float(benchmark_value), strategy_rows, record)
        if len(paired) >= 2:
            choices.append((len(matching), len(paired), key, group, paired))
    if not choices:
        return None, []

    _match_count, _period_count, _key, group, paired = max(
        choices, key=lambda item: (item[0], item[1], item[2]),
    )
    selected_periods = [period for period in preferred_periods if period in paired]
    for period in _RETURN_PERIODS:
        if period in paired and period not in selected_periods:
            selected_periods.append(period)
        if len(selected_periods) >= 3:
            break
    selected_periods = selected_periods[:3]

    benchmark_points = []
    strategy_median_points = []
    evidence_lines = []
    for period in selected_periods:
        benchmark_value, strategy_rows, record = paired[period]
        benchmark_raw = str((record.get("periodRaw") or {}).get(period) or "")
        scale_note = "as reported in percent" if "%" in benchmark_raw else "decimal fraction converted to percent"
        benchmark_quote = (
            f"Deterministic uploaded-row observation; file: {filename}; sheet: {record.get('sheet')}; "
            f"row: {record.get('rowNumber')}; benchmark index: {group['label']}; reported horizon: {period}; "
            f"raw workbook cell value: {benchmark_raw}; plotted return: {benchmark_value:.4f}% ({scale_note}); "
            f"original row: {record.get('rawLine') or ''}"
        )
        benchmark_points.append({
            "label": period, "value": round(benchmark_value, 4),
            "evidenceSource": "", "evidenceQuote": benchmark_quote,
            "evidenceValue": benchmark_raw, "evidenceEntity": group["label"],
            "evidenceMetric": "benchmark return", "evidencePeriod": period,
            "evidenceUnit": "%", "sourceFile": filename,
            "sourceSheet": record.get("sheet"), "sourceRow": record.get("rowNumber"),
        })
        evidence_lines.append(benchmark_quote)

        values = [float(row["periodValues"][period]) for row in strategy_rows]
        median_value = float(median(values))
        median_quote = (
            f"Deterministic calculation from {filename}; benchmark match: {group['label']}; "
            f"metric: median PMS strategy return; reported horizon: {period}; "
            f"value: {median_value:.4f}%; observations: {len(values)} unique strategies; "
            "matched using the workbook benchmark field; missing return cells excluded."
        )
        strategy_median_points.append({
            "label": period, "value": round(median_value, 4),
            "evidenceSource": "", "evidenceQuote": median_quote,
            "evidenceValue": f"{median_value:.4f}%", "evidenceEntity": group["label"],
            "evidenceMetric": "median PMS strategy return for matching benchmark",
            "evidencePeriod": period, "evidenceUnit": "%", "sourceFile": filename,
            "sourceObservationCount": len(values),
        })
        evidence_lines.append(median_quote)

    if len(benchmark_points) < 2 or len(strategy_median_points) != len(benchmark_points):
        return None, []
    label = group["label"]
    chart = {
        "type": "grouped_bar",
        "title": f"{label} vs matched PMS strategy median by reported horizon",
        "xLabel": "Reported return horizon", "yLabel": "Return as reported (%)", "unit": "%",
        "series": [
            {"name": f"{label} benchmark", "data": benchmark_points},
            {"name": f"Median of strategies using {label}", "data": strategy_median_points},
        ],
        "sourceIds": [], "sourceOrigin": "uploaded_workbook",
        "chartRole": "benchmark_comparison", "sourceFiles": [filename],
        "rationale": "Compare an index with the median return of attached strategies that name that index as their benchmark.",
        "takeaway": (
            "The benchmark and strategy median are matched on the workbook's benchmark field. "
            "Each label is a separate reported lookback horizon, not a time series."
        ),
    }
    return chart, evidence_lines


def is_file_only_workbook_report(question: str, file_context: str, source_documents: object = None,
                                 has_rag: bool = False) -> bool:
    """Identify attachment-first reports unless the user explicitly asks for outside research."""
    text = str(question or "").casefold()
    document_rows = source_documents if isinstance(source_documents, list) else []
    has_attached_source = bool(str(file_context or "").strip()) or bool(document_rows)
    # A caller may have indexed this session in an earlier turn and omit the
    # current sourceDocuments list. Use RAG as an attachment signal only when
    # the user also refers to an uploaded/attached/indexed file.
    has_rag_source = bool(has_rag) and bool(re.search(
        r"\b(?:attached|uploaded|indexed|source)\s+(?:file|files|document|documents|data|workbook|spreadsheet)\b",
        text,
        re.I,
    ))
    if not (has_attached_source or has_rag_source):
        return False
    external_override = re.search(
        r"\b(?:search\s+(?:the\s+)?web|web\s+(?:search|research)|online\s+research|external\s+sources?|"
        r"latest\s+(?:market|news|industry)|current\s+(?:market|share\s+price|stock\s+price)|"
        r"include\s+outside\s+research|supplement\s+with\s+(?:web|external)|"
        r"research\s+from\s+the\s+internet|external\s+benchmark(?:ing)?)\b", text, re.I,
    )
    # Negative instructions such as "do not search the web" are common in user
    # prompts. They must not accidentally turn off workbook-only mode merely
    # because the forbidden action is mentioned literally.
    negated_external = re.search(
        r"\b(?:do\s+not|don't|never|avoid|exclude|without|no)\s+"
        r"(?:(?:search|use|include|consult|retrieve|cite|reuse|rely\s+on)\s+)?"
        r"(?:the\s+)?(?:web|internet|external\s+sources?|outside\s+research)\b", text, re.I,
    )
    if negated_external:
        external_override = None
    if external_override:
        return False
    report_intent = re.search(
        r"\b(?:report|analysis|analy[sz]e|assessment|review|compare|comparison|findings|insights|"
        r"summar(?:y|ize|ise)|evaluate|assess)\b", text, re.I,
    )
    return bool(report_intent)


def _split_file_blocks(file_context: str) -> list[tuple[str, str]]:
    text = str(file_context or "")
    markers = list(_FILE_MARKER.finditer(text))
    blocks: list[tuple[str, str]] = []
    if not markers:
        return blocks
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
        blocks.append((marker.group(1).strip(), text[marker.end():end].strip()))
    return blocks


def _sheet_blocks(file_body: str) -> list[tuple[str, str]]:
    markers = list(_SHEET_MARKER.finditer(str(file_body or "")))
    sheets: list[tuple[str, str]] = []
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(file_body)
        sheets.append((marker.group(1).strip(), str(file_body[marker.end():end]).strip()))
    return sheets


def _parse_num(value: object) -> float | None:
    raw = str(value or "").strip()
    if _norm(raw) in _NA:
        return None
    raw = raw.replace(",", "").replace("%", "").strip()
    try:
        number = float(raw)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _csv_rows(sheet_body: str) -> tuple[list[list[str]], list[str]]:
    physical = [line for line in str(sheet_body or "").splitlines() if line.strip()]
    try:
        rows = [list(row) for row in csv.reader(io.StringIO("\n".join(physical))) if row]
    except csv.Error:
        rows = [[cell.strip().strip('"') for cell in line.split(",")] for line in physical]
    width = max((len(row) for row in rows), default=0)
    rows = [row + [""] * (width - len(row)) for row in rows]
    return rows, physical


def _find_pms_table(rows: list[list[str]]) -> tuple[int, int, dict[str, int]] | None:
    """Find a PMS-style strategy/return table by its header schema, independent of file name.

    AMC/manager and strategy identity plus a recognized return horizon
    identify the specialized adapter. Benchmark, AUM and category are useful fields,
    but optional; tables without the strategy/return shape stay on the generic path.
    """
    for header_index, row in enumerate(rows):
        header_indices: dict[str, int] = {}
        for column, value in enumerate(row):
            key = re.sub(r"[^a-z0-9]+", " ", _norm(value)).strip()
            if key in {
                "amc", "amc name", "asset management company", "asset manager",
                "fund manager", "portfolio manager", "pms provider", "fund house",
                "provider", "manager", "firm",
            }:
                header_indices.setdefault("amc", column)
            elif key in {
                "investment approach", "investment strategy", "strategy", "strategy name",
                "pms strategy", "portfolio", "portfolio name", "scheme", "scheme name",
                "product", "product name", "investment style",
            }:
                header_indices.setdefault("approach", column)
            elif "benchmark" in key:
                header_indices.setdefault("benchmark", column)
            elif key.startswith("aum") or "assets under management" in key:
                header_indices.setdefault("aum", column)
            elif key in {"category", "investment category", "scheme category"}:
                header_indices.setdefault("category", column)
        if not {"amc", "approach"}.issubset(header_indices):
            continue

        period_index = None
        period_columns: dict[str, int] = {}
        # Periods can share the main header row or sit in a second row below a
        # merged "Returns" heading. Evaluate each row independently so unrelated
        # labels from adjacent rows cannot combine into a false match.
        for candidate_index in range(header_index, min(len(rows), header_index + 4)):
            candidate_periods: dict[str, int] = {}
            for column, value in enumerate(rows[candidate_index]):
                alias = _return_period_alias(value)
                if alias:
                    candidate_periods[alias] = column
            if candidate_periods:
                period_index = candidate_index
                period_columns = candidate_periods
                break
        if period_index is None:
            continue
        return header_index, period_index, {
            **header_indices,
            **{f"return:{period}": col for period, col in period_columns.items()},
        }
    return None


def _return_period_alias(value: object) -> str | None:
    """Normalize common return-horizon headers such as "1 Year Return (%)"."""
    raw = _norm(value)
    direct = _RETURN_ALIASES.get(raw)
    if direct:
        return direct
    simplified = re.sub(r"\b(?:returns?|performance)\b", " ", raw)
    simplified = re.sub(r"[^a-z0-9]+", " ", simplified).strip()
    return _RETURN_ALIASES.get(simplified)


def _return_column_scale(
    rows: list[list[str]], header_index: int, period_index: int, column: int,
) -> float:
    """Infer whether numeric return cells are decimal fractions or percentage points."""
    header_text = " ".join(
        str(rows[index][column] or "")
        for index in range(max(0, header_index - 1), period_index + 1)
        if column < len(rows[index])
    )
    if "%" in header_text or re.search(r"(?i)\bpercent(?:age)?\b|\bper\s+cent\b", header_text):
        return 1.0
    values = []
    for row in rows[period_index + 1:]:
        if column >= len(row):
            continue
        raw = str(row[column] or "").strip()
        if not raw or "%" in raw:
            continue
        value = _parse_num(raw)
        if value is not None:
            values.append(abs(value))
    # Many spreadsheet exports store 0.248 for a displayed 24.8%; other files
    # store 24.8 directly. A value outside the fractional interval identifies
    # percentage-point storage. If every value is within [-1, 1], Excel's
    # decimal-fraction convention is the best available inference absent a unit.
    return 1.0 if any(value > 1.0 for value in values) else 100.0


def _pms_header_fields(
    rows: list[list[str]], header_index: int, period_index: int,
) -> list[str]:
    """Return the visible field names from a detected two-row PMS header."""
    header = rows[header_index] if 0 <= header_index < len(rows) else []
    periods = rows[period_index] if 0 <= period_index < len(rows) else []
    names = []
    for column in range(max(len(header), len(periods))):
        label = str(header[column] if column < len(header) else "").strip()
        period = str(periods[column] if column < len(periods) else "").strip()
        alias = _return_period_alias(period)
        if alias:
            label = f"Returns - {alias}"
        elif not label:
            label = period
        label = re.sub(r"\s+", " ", label).strip()
        if label and label.casefold() not in {value.casefold() for value in names}:
            names.append(label)
    return names


def _pms_semantic_field_map(
    rows: list[list[str]], header_index: int, period_index: int, indices: dict[str, int],
) -> dict[str, str]:
    """Keep the source's actual labels for the semantic fields we recognize."""
    result: dict[str, str] = {}
    for field in ("amc", "approach", "benchmark", "aum", "category"):
        column = indices.get(field)
        if column is None:
            continue
        labels: list[str] = []
        for row_index in dict.fromkeys((header_index, period_index)):
            if row_index < 0 or row_index >= len(rows) or column >= len(rows[row_index]):
                continue
            value = re.sub(r"\s+", " ", str(rows[row_index][column] or "")).strip()
            if value and _return_period_alias(value) is None and value.casefold() not in {x.casefold() for x in labels}:
                labels.append(value)
        if labels:
            result[field] = " / ".join(labels)
    return result


def build_pms_workbook_analytics(file_context: str) -> dict[str, Any]:
    """Parse PMS strategy tables and return measured rows, chart specs and source excerpts."""
    files_result: list[dict[str, Any]] = []
    merged: OrderedDict[tuple[str, str, str], dict[str, Any]] = OrderedDict()
    benchmark_rows: list[dict[str, Any]] = []
    repeated_rows = 0
    duplicate_return_conflicts = 0
    duplicate_conflict_examples: list[dict[str, Any]] = []
    for filename, file_body in _split_file_blocks(file_context):
        sheet_records: list[dict[str, Any]] = []
        file_benchmark_rows: list[dict[str, Any]] = []
        all_rows_for_evidence: list[str] = []
        workbook_date = ""
        sheet_metadata: list[dict[str, Any]] = []
        for sheet_name, sheet_body in _sheet_blocks(file_body):
            rows, physical = _csv_rows(sheet_body)
            if not rows:
                continue
            sheet_date = ""
            for row in rows[:3]:
                match = re.search(r"(?i)AS\s+(?:ON|OF)\s+(.+)", " ".join(row))
                if match:
                    sheet_date = match.group(1).strip()
                    break
            if sheet_date and not workbook_date:
                workbook_date = sheet_date
            found = _find_pms_table(rows)
            if not found:
                continue
            header_index, period_index, indices = found
            title = ""
            for title_row in reversed(rows[max(0, header_index - 3):header_index]):
                value = next((cell.strip() for cell in title_row if cell.strip()), "")
                if value:
                    title = value
                    break
            header_line = physical[header_index] if header_index < len(physical) else ",".join(rows[header_index])
            period_line = physical[period_index] if period_index < len(physical) else ",".join(rows[period_index])
            header_fields = _pms_header_fields(rows, header_index, period_index)
            field_map = _pms_semantic_field_map(rows, header_index, period_index, indices)
            recognized_fields = list(field_map)
            period_scales = {
                period: _return_column_scale(rows, header_index, period_index, column)
                for period, column in indices.items() if period.startswith("return:")
            }
            return_columns = [
                column for key, column in indices.items() if key.startswith("return:")
            ]
            first_return_column = min(return_columns) if return_columns else len(rows[period_index])
            in_benchmark_section = False
            sheet_record_start = len(sheet_records)
            if header_line:
                all_rows_for_evidence.append(f"[Sheet: {sheet_name}] {title} | {header_line} | {period_line}")
            for row_index in range(period_index + 1, len(rows)):
                row = rows[row_index]

                def cell(key: str) -> str:
                    return row[indices[key]].strip() if indices.get(key, 10**6) < len(row) else ""

                amc, approach = cell("amc"), cell("approach")

                # Some PMS exports place benchmark index observations after
                # the strategy rows, under a small "Benchmark (TRI)" section.
                # Those rows share the strategy return columns but have no AMC
                # or investment-approach value, so the strategy parser used to
                # discard them even when a report chart quoted their values.
                section_label = next(
                    (str(value or "").strip() for value in row[:first_return_column]
                     if str(value or "").strip()),
                    "",
                )
                if re.fullmatch(r"(?i)benchmarks?(?:\s*\([^)]*\))?", section_label):
                    in_benchmark_section = True
                    continue

                if in_benchmark_section:
                    # A new table or a ranked-strategy block can follow the
                    # benchmark rows on the same worksheet. End the benchmark
                    # block as soon as both identity fields are populated, so
                    # those later strategy rows are never mislabeled as indexes.
                    if amc and approach:
                        in_benchmark_section = False
                    else:
                        period_values: dict[str, float] = {}
                        period_raw: dict[str, str] = {}
                        for period in _RETURN_PERIODS:
                            column = indices.get(f"return:{period}")
                            if column is None or column >= len(row):
                                continue
                            raw_value = str(row[column] or "").strip()
                            number = _parse_num(raw_value)
                            if number is None:
                                continue
                            period_values[period] = (
                                number if "%" in raw_value
                                else number * period_scales.get(f"return:{period}", 1.0)
                            )
                            period_raw[period] = raw_value
                        if section_label and len(period_values) >= 2:
                            raw_line = physical[row_index] if row_index < len(physical) else ",".join(row)
                            file_benchmark_rows.append({
                                "filename": filename, "sheet": sheet_name,
                                "rowNumber": row_index + 1, "tableTitle": title,
                                "asOf": sheet_date, "label": section_label,
                                "periodValues": period_values, "periodRaw": period_raw,
                                "rawLine": raw_line, "headerLine": header_line,
                                "periodLine": period_line,
                            })
                        continue

                if not amc or not approach or _norm(amc) in {"amc", "top performance", "strategy"}:
                    continue
                period_values: dict[str, float] = {}
                period_raw: dict[str, str] = {}
                for period in _RETURN_PERIODS:
                    column = indices.get(f"return:{period}")
                    if column is None or column >= len(row):
                        continue
                    raw_value = row[column].strip()
                    number = _parse_num(raw_value)
                    if number is None:
                        continue
                    # Honor explicit units; otherwise infer the column's stored
                    # representation once so values like 13.4 are not changed to 1,340%.
                    period_values[period] = (
                        number if "%" in raw_value
                        else number * period_scales.get(f"return:{period}", 1.0)
                    )
                    period_raw[period] = raw_value
                aum_raw = cell("aum")
                aum = _parse_num(aum_raw)
                raw_line = physical[row_index] if row_index < len(physical) else ",".join(row)
                record = {
                    "filename": filename, "sheet": sheet_name, "rowNumber": row_index + 1,
                    "tableTitle": title, "asOf": sheet_date, "amc": amc, "approach": approach,
                    "benchmark": cell("benchmark"), "category": cell("category"),
                    "aum": aum, "aumRaw": aum_raw, "periodValues": period_values,
                    "periodRaw": period_raw, "rawLine": raw_line,
                    "headerLine": header_line, "periodLine": period_line,
                    "recognizedFields": recognized_fields,
                }
                if aum is not None:
                    record["aumEvidence"] = {
                        "filename": filename, "sheet": sheet_name, "rowNumber": row_index + 1,
                        "tableTitle": title, "asOf": sheet_date, "rawValue": aum_raw,
                        "rawLine": raw_line, "headerLine": header_line,
                    }
                if not period_values and aum is None:
                    continue
                sheet_records.append(record)
                # Keep identically named strategies in separate attachments
                # independent; merge duplicate tables only within one file.
                key = (filename.casefold(), _norm(amc), _norm(approach))
                prior = merged.get(key)
                if prior is None:
                    merged[key] = record.copy()
                else:
                    repeated_rows += 1
                    prior["recognizedFields"] = sorted(
                        set(prior.get("recognizedFields") or []) | set(recognized_fields)
                    )
                    # Category tables are the base. Top-performance tables can fill gaps,
                    # but duplicate sheets never overwrite a real observation silently.
                    for period, value in period_values.items():
                        prior_value = prior["periodValues"].get(period)
                        if prior_value is not None:
                            tolerance = max(0.001, abs(float(prior_value)) * 0.002)
                            if abs(float(prior_value) - float(value)) > tolerance:
                                duplicate_return_conflicts += 1
                                if len(duplicate_conflict_examples) < 20:
                                    duplicate_conflict_examples.append({
                                        "file": filename, "amc": amc, "approach": approach,
                                        "period": period, "firstSheet": prior.get("sheet"),
                                        "repeatSheet": sheet_name, "firstValue": prior_value,
                                        "repeatValue": value,
                                    })
                        else:
                            prior["periodValues"][period] = value
                            prior["periodRaw"][period] = period_raw[period]
                            prior.setdefault("periodEvidence", {})[period] = record
                    if prior.get("aum") is None and aum is not None:
                        prior["aum"], prior["aumRaw"] = aum, aum_raw
                        prior["aumEvidence"] = record.get("aumEvidence")
            sheet_metadata.append({
                "name": sheet_name, "title": title, "asOf": sheet_date,
                "columnNames": header_fields,
                "fieldMap": field_map, "recognizedFields": recognized_fields,
                "rowCount": len(sheet_records) - sheet_record_start,
                "sourceRows": len(sheet_records) - sheet_record_start,
                "columnCount": len(header_fields),
            })
        methodology_notes = []
        if re.search(r"(?i)time\s+weighted\s+rate\s+of\s+return|\bTWRR\b", file_body):
            methodology_notes.append(
                "Workbook footnote states returns use the Time Weighted Rate of Return (TWRR) method, as provided by the respective AMCs."
            )
        if re.search(r"(?i)returns?\s+over\s+a\s+1\s+year\s+period\s+are\s+annualized", file_body):
            methodology_notes.append("Workbook footnote states 1-year returns are annualized.")
        files_result.append({
            "filename": filename, "rows": sheet_records, "asOf": workbook_date,
            "sheets": sheet_metadata, "methodologyNotes": methodology_notes,
            "evidenceHeaders": all_rows_for_evidence,
            "benchmarkRows": file_benchmark_rows,
        })
        benchmark_rows.extend(file_benchmark_rows)

    if not merged:
        return {"kind": "generic", "files": [], "strategies": [], "charts": [], "keyStats": [], "evidenceByFile": {}}

    strategies = list(merged.values())
    def evidence_record(item: dict, period: str | None = None) -> dict:
        return item.get("periodEvidence", {}).get(period, item) if period else item

    # Derive chart horizons from the actual return columns in the workbook.
    # The previous fixed 1/3/5-year list silently ignored valid 1/3/6-month,
    # 2-year, 10-year and since-inception data even when those columns existed.
    period_labels = {
        "1 Mth": "1-Month", "3 Mth": "3-Month", "6 Mth": "6-Month",
        "1 Yr": "1-Year", "2 Yr": "2-Year", "3 Yr": "3-Year",
        "5 Yr": "5-Year", "10 Yr": "10-Year", "Since Incp.": "Since-Inception",
    }
    observed_periods = [
        period for period in period_labels
        if sum(1 for item in strategies if period in (item.get("periodValues") or {})) >= 2
    ]
    preferred_periods = [period for period in ("1 Mth", "1 Yr", "5 Yr") if period in observed_periods]
    target_period_count = min(3, len(observed_periods))
    if len(preferred_periods) < target_period_count:
        middle_period = observed_periods[len(observed_periods) // 2] if observed_periods else ""
        for period in (observed_periods[0], middle_period, observed_periods[-1], *observed_periods):
            if period and period not in preferred_periods:
                preferred_periods.append(period)
            if len(preferred_periods) >= target_period_count:
                break
    selected_periods = set(preferred_periods[:target_period_count])
    chart_defs = [
        (period, f"Top PMS Strategies by {period_labels[period]} Return",
         f"Reported {period_labels[period].lower()} return")
        for period in _RETURN_PERIODS if period in selected_periods
    ]
    multiple_filenames = len({str(row.get("filename") or "").casefold() for row in strategies}) > 1
    charts: list[dict] = []
    used_rows: dict[str, list[str]] = {}
    source_items = {row["filename"].casefold(): row for row in files_result}
    for period, chart_title, series_name in chart_defs:
        observations = [item for item in strategies if period in item.get("periodValues", {})]
        observations.sort(key=lambda item: item["periodValues"][period], reverse=True)
        observations = observations[:10]
        if len(observations) < 2:
            continue
        points = []
        for item in observations:
            source_row = evidence_record(item, period)
            raw_value = source_row.get("periodRaw", {}).get(period, item.get("periodRaw", {}).get(period, ""))
            raw_line = str(source_row.get("rawLine") or "").strip()
            sheet_name = str(source_row.get("sheet") or "Unknown sheet").strip()
            row_number = source_row.get("rowNumber")
            header_line = str(source_row.get("headerLine") or "").strip()
            period_line = str(source_row.get("periodLine") or "").strip()
            # The parsed CSV row can omit the normalized period cell (e.g. values
            # represented as percentages or split across a multi-row header). Make
            # the exact raw cell value explicit in the evidence quote, retaining its
            # worksheet/row provenance. Every field here is taken from this source row.
            quote_parts = [f"Sheet: {sheet_name}", f"Row: {row_number}" if row_number is not None else "",
                           f"Table: {source_row.get('tableTitle')}" if source_row.get("tableTitle") else "",
                           f"As of: {source_row.get('asOf')}" if source_row.get("asOf") else "",
                           f"Headers: {header_line}" if header_line else "",
                           f"Period: {period_line}" if period_line else "",
                           f"Metric: {period}; raw workbook cell value: {raw_value}; "
                           f"derived plotted return: {item['periodValues'][period]:.4f}% "
                           f"({'raw percent value' if '%' in str(raw_value) else 'raw decimal fraction multiplied by 100'})",
                           f"Original row: {raw_line}" if raw_line else ""]
            quote = " | ".join(part for part in quote_parts if part)
            filename_key = str(source_row.get("filename") or item["filename"]).casefold()
            used_rows.setdefault(filename_key, []).append(quote)
            points.append({
                "label": (
                    f"{item['amc']} - {item['approach']} ({item['filename']})"
                    if multiple_filenames else f"{item['amc']} - {item['approach']}"
                ),
                "value": round(float(item["periodValues"][period]), 4),
                "evidenceSource": "",  # assigned once the canonical source registry exists
                "evidenceQuote": quote,
                "evidenceValue": raw_value,
                "sourceSheet": sheet_name, "sourceRow": row_number, "sourceFile": source_row.get("filename"),
            })
        ranking_type = (
            "dot_plot" if period in {"1 Mth", "3 Mth", "6 Mth"}
            else "lollipop" if period in {"3 Yr", "5 Yr", "10 Yr", "Since Incp."}
            else "horizontal_bar"
        )
        if ranking_type in {"dot_plot", "lollipop"}:
            x_label, y_label = f"{period} return (%)", "PMS strategy"
        else:
            x_label, y_label = "PMS strategy", f"{period} return (%)"
        charts.append({
            "type": ranking_type, "title": chart_title,
            "xLabel": x_label, "yLabel": y_label, "unit": "%",
            "series": [{"name": series_name, "data": points}],
            "sourceIds": [], "sourceOrigin": "uploaded_workbook", "workbookMetric": period,
            "chartRole": "strategy_ranking",
            "sourceFiles": sorted({str(item.get("filename") or "") for item in observations}),
            "rationale": f"Ranked comparison of actual {period} return values from the uploaded workbook.",
            "takeaway": f"Shows up to ten highest reported {period} returns from the available workbook tables; missing values are excluded. This is a ranked slice, not the full universe.",
        })

    evidence_by_file: dict[str, str] = {}
    for filename, lines in used_rows.items():
        file_data = source_items.get(filename)
        header_lines = file_data.get("evidenceHeaders", []) if file_data else []
        # Reserve the evidence budget for exact plotted rows. Repeated headers
        # across many worksheets used to fill the excerpt before a single value
        # or deterministic calculation could be retained.
        deduped = list(OrderedDict((str(line), None) for line in [*lines, *header_lines]).keys())
        evidence_by_file[filename] = "\n".join(deduped)[:UPLOAD_CHART_EVIDENCE_CHARS]

    stats = []
    one_year = [x for x in strategies if "1 Yr" in x.get("periodValues", {})]
    if one_year:
        leader = max(one_year, key=lambda x: x["periodValues"]["1 Yr"])
        stats.append({"label": "Strategies with 1-year return data", "value": str(len(one_year))})
        stats.append({"label": "Highest reported 1-year return", "value": f"{leader['periodValues']['1 Yr']:.2f}%"})
    if strategies:
        stats.append({"label": "Distinct manager / strategy pairs", "value": str(len(strategies))})
    period_stat_notes = []
    for period in _RETURN_PERIODS:
        values = [
            float(strategy["periodValues"][period])
            for strategy in strategies if period in (strategy.get("periodValues") or {})
        ]
        if values:
            period_stat_notes.append(
                f"{period} median return = {median(values):.2f}% from {len(values)} reported rows"
            )
    category_groups: dict[str, list[float]] = {}
    for strategy in strategies:
        category = str(strategy.get("category") or "").strip()
        value = (strategy.get("periodValues") or {}).get("1 Yr")
        if category and value is not None:
            category_groups.setdefault(category, []).append(float(value))
    category_stat_notes = [
        f"{category} 1 Yr median return = {median(values):.2f}% from {len(values)} reported rows"
        for category, values in sorted(category_groups.items(), key=lambda item: item[0].casefold())
        if len(values) >= MIN_CATEGORY_SIZE
    ]
    raw_strategy_row_count = sum(len(item.get("rows") or []) for item in files_result)
    workbook_sheets = [
        sheet for item in files_result for sheet in (item.get("sheets") or [])
    ]
    field_names = ("amc", "approach", "benchmark", "aum", "category")
    field_sheet_counts = {
        field: sum(1 for sheet in workbook_sheets if field in (sheet.get("recognizedFields") or []))
        for field in field_names
    }
    field_presence = {field: count > 0 for field, count in field_sheet_counts.items()}
    aum_field_strategies = [
        strategy for strategy in strategies if "aum" in (strategy.get("recognizedFields") or [])
    ]
    benchmark_field_strategies = [
        strategy for strategy in strategies if "benchmark" in (strategy.get("recognizedFields") or [])
    ]
    strategies_with_aum = sum(1 for strategy in aum_field_strategies if strategy.get("aum") is not None)
    strategies_without_aum_in_field = len(aum_field_strategies) - strategies_with_aum
    strategies_with_benchmark = sum(
        1 for strategy in benchmark_field_strategies if _norm(strategy.get("benchmark")) not in _NA
    )
    strategies_without_benchmark_in_field = len(benchmark_field_strategies) - strategies_with_benchmark
    dated_sheet_count = sum(1 for sheet in workbook_sheets if str(sheet.get("asOf") or "").strip())
    workbook_dates = sorted({
        str(sheet.get("asOf") or "").strip()
        for sheet in workbook_sheets if str(sheet.get("asOf") or "").strip()
    })
    # Ensure derived KPIs can be audited without pretending the derived count is
    # itself a literal cell in the workbook. The excerpt explicitly labels the
    # calculation and retains the raw worksheet rows that support chart points.
    stats_note = (
        "\n[Deterministic calculations from the uploaded workbook, not literal worksheet cells] "
        + "; ".join([
            *(f"{item['label']} = {item['value']}" for item in stats),
            f"raw strategy rows = {raw_strategy_row_count}; unique manager/strategy pairs = {len(strategies)}; "
            f"repeated rows = {repeated_rows}; conflicting duplicate return values = {duplicate_return_conflicts}",
            (f"AUM values recorded for {strategies_with_aum} of {len(aum_field_strategies)} strategies in tables with an AUM field"
             if field_presence["aum"] else "no AUM field was recognized; AUM coverage is unavailable"),
            (f"Benchmark recorded for {strategies_with_benchmark} of {len(benchmark_field_strategies)} strategies in tables with a benchmark field"
             if field_presence["benchmark"] else "no benchmark field was recognized; benchmark coverage is unavailable"),
            f"worksheets with as-of dates = {dated_sheet_count} of {len(workbook_sheets)}",
            *(f"workbook as-of date = {value}" for value in workbook_dates),
            *period_stat_notes,
            *category_stat_notes,
        ]) if strategies else ""
    )
    if stats_note:
        for filename_key in list(evidence_by_file):
            evidence_by_file[filename_key] = (stats_note + "\n" + evidence_by_file[filename_key])[:UPLOAD_CHART_EVIDENCE_CHARS]
    period_coverage = {
        period: sum(1 for strategy in strategies if period in (strategy.get("periodValues") or {}))
        for period in _RETURN_PERIODS
    }
    raw_strategy_rows = sum(len(item.get("rows") or []) for item in files_result)
    missing_benchmark_count = sum(
        1 for strategy in strategies
        if _norm(strategy.get("benchmark")) in _NA
    )
    missing_aum_count = sum(1 for strategy in strategies if strategy.get("aum") is None)
    all_sheet_rows = [
        (str(file_item.get("filename") or ""), sheet)
        for file_item in files_result for sheet in (file_item.get("sheets") or [])
    ]
    snapshot_dates = sorted({
        str(sheet.get("asOf") or "").strip()
        for _filename, sheet in all_sheet_rows if str(sheet.get("asOf") or "").strip()
    })
    missing_date_sheets = [
        {"file": filename, "sheet": str(sheet.get("name") or "")}
        for filename, sheet in all_sheet_rows if not str(sheet.get("asOf") or "").strip()
    ]
    methodology_notes = list(dict.fromkeys(
        note for item in files_result for note in (item.get("methodologyNotes") or [])
    ))
    return {"kind": "pms_strategy_performance", "files": files_result,
            "strategies": strategies, "charts": charts, "keyStats": stats,
            "benchmarkRows": benchmark_rows,
            "periodCoverage": period_coverage,
            "evidenceByFile": evidence_by_file,
            "snapshotDate": snapshot_dates[0] if len(snapshot_dates) == 1 else "",
            "snapshotDates": snapshot_dates,
            "methodologyNotes": methodology_notes,
            "dataQuality": {
                "rawStrategyRows": raw_strategy_rows,
                "uniqueStrategies": len(strategies),
                "repeatedRows": repeated_rows,
                "duplicateReturnConflicts": duplicate_return_conflicts,
                "duplicateConflictExamples": duplicate_conflict_examples,
                "strategiesWithoutBenchmark": missing_benchmark_count,
                "strategiesWithoutAum": missing_aum_count,
                "fieldPresence": field_presence,
                "fieldSheetCounts": field_sheet_counts,
                "strategiesWithAumField": len(aum_field_strategies),
                "strategiesWithAumValue": strategies_with_aum,
                "strategiesWithoutAumAmongField": strategies_without_aum_in_field,
                "strategiesWithBenchmarkField": len(benchmark_field_strategies),
                "strategiesWithBenchmarkValue": strategies_with_benchmark,
                "strategiesWithoutBenchmarkAmongField": strategies_without_benchmark_in_field,
                "worksheetsWithDate": len(all_sheet_rows) - len(missing_date_sheets),
                "worksheetCount": len(all_sheet_rows),
                "worksheetsWithoutDate": missing_date_sheets,
            }}


_DATA_NUMBER_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$", re.I)
_PERCENT_HEADER_RE = re.compile(r"(?:%|percent|percentage|pct|return|yield|margin|rate)", re.I)
_DATE_HEADER_RE = re.compile(r"\b(?:date|time|month|quarter|year|week|period)\b", re.I)
_LABEL_HEADER_RE = re.compile(
    r"\b(?:name|company|amc|strategy|product|region|country|category|segment|ticker|sector|"
    r"customer|account|portfolio|fund|state|city|channel|type|group)\b",
    re.I,
)
_IDENTIFIER_HEADER_RE = re.compile(r"\b(?:id|uuid|serial|index|row number|record number)\b", re.I)
_OUTCOME_HEADER_RE = re.compile(
    r"\b(?:price|revenue|sales|income|profit|cost|return|yield|margin|score|amount|value)\b", re.I,
)


def _parse_data_number(value: object) -> tuple[float, bool] | None:
    """Parse ordinary numeric cells while retaining whether the cell had a percent sign."""
    raw = str(value or "").strip().replace("\u2212", "-")
    if _norm(raw) in _NA:
        return None
    negative_parentheses = raw.startswith("(") and raw.endswith(")")
    raw = raw.strip("()").replace(",", "").replace(" ", "")
    percent = "%" in raw
    raw = raw.replace("%", "")
    # Strip only currency symbols and unit prefixes/suffixes. Stripping ANY leading text turned labels into
    # numbers: "Fund 1" -> 1, "FY24" -> 24, "Plan B2" -> 2 and "Q3 2025" -> 32025, so name columns were
    # profiled as numeric fields with means and medians.
    raw = re.sub(r"(?i)^(?:₹|rs\.?|inr|usd|us\$|\$|€|eur|£|gbp)", "", raw)
    raw = re.sub(r"(?i)(?:crores?|cr|lakhs?|lacs?|bn|billion|mn|million|k|x|times|bps|pp)$", "", raw)
    if not _DATA_NUMBER_RE.fullmatch(raw):
        return None
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    if negative_parentheses:
        number = -abs(number)
    return number, percent


def _table_rows(text: str) -> tuple[list[list[str]], list[str]]:
    """Read CSV/TSV/semicolon or Markdown table text without assuming a delimiter."""
    physical = [line.rstrip() for line in str(text or "").splitlines() if line.strip()]
    if not physical:
        return [], []

    markdown_rows: list[list[str]] = []
    markdown_lines: list[str] = []
    for line in physical:
        if line.count("|") >= 2:
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if cells and all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in cells):
                continue
            markdown_rows.append(cells)
            markdown_lines.append(line)
    if len(markdown_rows) >= 2:
        return markdown_rows, markdown_lines

    sample = "\n".join(physical[:20])
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    try:
        rows = [list(row) for row in csv.reader(io.StringIO("\n".join(physical)), dialect) if row]
    except csv.Error:
        rows = [[cell.strip().strip('"') for cell in line.split(",")] for line in physical]
    width = max((len(row) for row in rows), default=0)
    return [row + [""] * (width - len(row)) for row in rows], physical


def _choose_table_header(rows: list[list[str]]) -> tuple[int, list[str], int] | None:
    """Choose a likely header row and tolerate title/preamble rows above it."""
    best: tuple[float, int, list[str], int] | None = None
    for index, row in enumerate(rows[:20]):
        labels = [str(cell or "").strip() for cell in row]
        populated = [cell for cell in labels if cell]
        if len(populated) < 2:
            continue
        numeric_ratio = sum(_parse_data_number(cell) is not None for cell in populated) / len(populated)
        if numeric_ratio > 0.5:
            continue
        unique_ratio = len({_norm(cell) for cell in populated}) / len(populated)
        next_rows = rows[index + 1:min(len(rows), index + 5)]
        data_like = [
            candidate for candidate in next_rows
            if sum(bool(str(cell or "").strip()) for cell in candidate) >= 2
        ]
        if not data_like:
            continue
        next_numeric = sum(
            _parse_data_number(cell) is not None
            for candidate in data_like for cell in candidate if str(cell or "").strip()
        )
        next_non_numeric = sum(
            _parse_data_number(cell) is None
            for candidate in data_like for cell in candidate if str(cell or "").strip()
        )
        # A header is usually wider and more unique than a title, and the rows
        # beneath it normally contain either numbers or a mix of data values.
        score = len(populated) + unique_ratio * 3 + min(next_numeric, 8) * 0.2
        score += min(next_non_numeric, 8) * 0.05
        score -= numeric_ratio * 5
        if best is None or score > best[0]:
            best = (score, index, labels, next_numeric)
    if best is None:
        return None

    _, header_index, labels, _ = best
    data_start = header_index + 1
    # Excel often stores a broad heading row followed by a second row of
    # periods/units. Preserve both levels rather than dropping the subheaders.
    if data_start < len(rows):
        second = [str(cell or "").strip() for cell in rows[data_start]]
        second_values = [cell for cell in second if cell]
        if (
            len(second_values) >= 2
            and not any(_parse_data_number(cell) is not None for cell in second_values)
            and any(re.search(r"return|value|amount|metric|sales|revenue|performance", cell, re.I) for cell in labels)
        ):
            width = max(len(labels), len(second))
            labels = [
                " — ".join(part for part in (
                    labels[column].strip() if column < len(labels) else "",
                    second[column].strip() if column < len(second) else "",
                ) if part)
                for column in range(width)
            ]
            data_start += 1

    names: list[str] = []
    seen: dict[str, int] = {}
    for index, label in enumerate(labels):
        name = re.sub(r"\s+", " ", str(label or "").strip()) or f"Column {index + 1}"
        seen_key = name.casefold()
        seen[seen_key] = seen.get(seen_key, 0) + 1
        names.append(f"{name} ({seen[seen_key]})" if seen[seen_key] > 1 else name)
    return header_index, names, data_start


def _column_statistics(
    column_name: str,
    rows: list[list[str]],
    column_index: int,
) -> dict[str, Any] | None:
    raw_values: list[tuple[int, str, float, bool]] = []
    nonempty = 0
    missing = 0
    for row_index, row in enumerate(rows):
        raw = str(row[column_index] if column_index < len(row) else "").strip()
        if _norm(raw) in _NA:
            missing += 1
            continue
        nonempty += 1
        parsed = _parse_data_number(raw)
        if parsed is not None:
            raw_values.append((row_index, raw, parsed[0], parsed[1]))
    if not raw_values:
        return None
    ratio = len(raw_values) / max(1, nonempty)
    if ratio < 0.55:
        return None
    has_explicit_percent = any(value[3] for value in raw_values)
    should_scale_fraction = (
        not has_explicit_percent
        and bool(_PERCENT_HEADER_RE.search(column_name))
        and len(raw_values) >= 2
        and max(abs(value[2]) for value in raw_values) <= 1.0
    )
    scale = 100.0 if should_scale_fraction else 1.0
    values = [value[2] * (1.0 if value[3] else scale) for value in raw_values]
    unit = "%" if has_explicit_percent or should_scale_fraction or "%" in column_name else ""
    currency = next((symbol for symbol in ("₹", "$", "€", "£") if symbol in column_name), "")
    if currency and not unit:
        unit = currency
    return {
        "name": column_name,
        "count": len(values),
        "missing": missing,
        "totalRows": len(rows),
        "min": min(values),
        "median": median(values),
        "mean": mean(values),
        "max": max(values),
        "unit": unit,
        "scale": scale,
        "rawValues": raw_values,
        "scaledValues": values,
    }


def _profile_tabular_section(
    filename: str,
    sheet_name: str,
    body: str,
    *,
    max_charts: int,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], list[str]]:
    rows, physical = _table_rows(body)
    header = _choose_table_header(rows)
    if not header:
        return None, [], []
    header_index, columns, data_start = header
    width = len(columns)
    data_rows: list[list[str]] = []
    source_row_numbers: list[int] = []
    for row_index, row in enumerate(rows[data_start:], start=data_start):
        padded = [str(cell or "").strip() for cell in row[:width]]
        padded += [""] * max(0, width - len(padded))
        if sum(bool(value) for value in padded) < 2:
            continue
        if all(_norm(padded[column]) == _norm(columns[column]) for column in range(width) if padded[column]):
            continue
        data_rows.append(padded)
        source_row_numbers.append(row_index + 1)
    if not data_rows:
        return None, [], []

    numeric = [
        summary for column_index, name in enumerate(columns)
        if (summary := _column_statistics(name, data_rows, column_index)) is not None
        and summary["count"] >= 1
        and not (_IDENTIFIER_HEADER_RE.search(name) and not _PERCENT_HEADER_RE.search(name))
    ]
    nonnumeric_columns = []
    for column_index, name in enumerate(columns):
        values = [
            str(row[column_index] or "").strip()
            for row in data_rows if str(row[column_index] or "").strip()
        ]
        if not values:
            continue
        numeric_count = sum(_parse_data_number(value) is not None for value in values)
        unique_values = list(OrderedDict((_norm(value), value) for value in values).values())
        if numeric_count / len(values) >= 0.55 or len(unique_values) < 2:
            continue
        if len(unique_values) > max(40, int(len(values) * 0.8)):
            continue
        if _IDENTIFIER_HEADER_RE.search(name) and not _LABEL_HEADER_RE.search(name):
            continue
        nonnumeric_columns.append({
            "index": column_index, "name": name, "uniqueCount": len(unique_values),
            "values": values, "uniqueValues": unique_values,
            "labelScore": 4 if _LABEL_HEADER_RE.search(name) else 0,
            "dateScore": 5 if _DATE_HEADER_RE.search(name) else 0,
        })
    label_columns = sorted(
        (item for item in nonnumeric_columns if not item["dateScore"]),
        key=lambda item: (item["labelScore"], min(item["uniqueCount"], 30)),
        reverse=True,
    )
    time_columns = sorted(
        (item for item in nonnumeric_columns if item["dateScore"] and item["uniqueCount"] >= 3),
        key=lambda item: (item["dateScore"], item["uniqueCount"]),
        reverse=True,
    )
    label_column = label_columns[0] if label_columns else None
    time_column = time_columns[0] if time_columns else None

    category_summaries = []
    for column in label_columns[:8]:
        counts: OrderedDict[str, dict[str, Any]] = OrderedDict()
        for value in column["values"]:
            key = _norm(value)
            if not key:
                continue
            if key not in counts:
                counts[key] = {"value": value, "count": 0}
            counts[key]["count"] += 1
        ranked_values = sorted(counts.values(), key=lambda item: (-item["count"], _norm(item["value"])))
        category_summaries.append({
            "name": column["name"],
            "uniqueCount": column["uniqueCount"],
            "missing": len(data_rows) - len(column["values"]),
            "topValues": [
                {"value": item["value"], "count": item["count"],
                 "share": item["count"] / max(1, len(column["values"]))}
                for item in ranked_values[:12]
            ],
        })

    missing_cells = sum(
        _norm(value) in _NA for row in data_rows for value in row
    )
    total_cells = max(1, len(data_rows) * len(columns))
    dataset = {
        "filename": filename,
        "sheet": sheet_name,
        "rowCount": len(data_rows),
        "columnCount": len(columns),
        "columns": columns,
        "numericSummaries": [
            {key: value for key, value in summary.items() if key not in {"rawValues", "scaledValues", "scale"}}
            for summary in numeric
        ],
        "categorySummaries": category_summaries,
        "columnCoverage": [
            {
                "name": name,
                "present": sum(bool(str(row[index] or "").strip()) for row in data_rows),
                "missing": sum(not str(row[index] or "").strip() for row in data_rows),
            }
            for index, name in enumerate(columns)
        ],
        "missingCells": missing_cells,
        "missingRate": missing_cells / total_cells,
    }

    charts: list[dict[str, Any]] = []
    evidence_lines = [
        f"[Sheet: {sheet_name}] {','.join(columns)}",
    ]
    for summary in numeric:
        if len(charts) >= max_charts:
            break
        values_by_row = {
            raw_values[0]: (raw_values[1], summary["scaledValues"][index])
            for index, raw_values in enumerate(summary["rawValues"])
        }
        # One observation per date/period is an auditable time series. For
        # repeated periods, use the category ranking below instead of silently
        # averaging multiple entities together.
        if time_column and summary["count"] >= 3 and summary["count"] == len(data_rows):
            time_index = int(time_column["index"])
            labels = [str(row[time_index] or "").strip() for row in data_rows]
            if len(set(labels)) == len(labels):
                ordered = sorted(range(len(data_rows)), key=lambda index: labels[index])
                points = []
                for index in ordered:
                    raw_value = str(data_rows[index][columns.index(summary["name"])] or "").strip()
                    plotted = values_by_row.get(index, ("", 0.0))[1]
                    row_number = source_row_numbers[index]
                    raw_line = physical[row_number - 1] if row_number - 1 < len(physical) else ",".join(data_rows[index])
                    quote = (
                        f"File: {filename} | Sheet: {sheet_name} | Row: {row_number} | "
                        f"Metric: {summary['name']} | raw workbook cell value: {raw_value} | "
                        f"derived plotted value: {plotted:.8g}{summary['unit']} | Original row: {raw_line}"
                    )
                    evidence_lines.append(quote)
                    points.append({
                        "label": labels[index], "value": round(float(plotted), 6),
                        "evidenceSource": "", "evidenceQuote": quote, "evidenceValue": raw_value,
                        "sourceSheet": sheet_name, "sourceRow": row_number, "sourceFile": filename,
                    })
                charts.append({
                    "type": "line",
                    "title": f"{filename} — {sheet_name} — {summary['name']} over {time_column['name']}",
                    "xLabel": time_column["name"], "yLabel": summary["name"],
                    "unit": summary["unit"], "series": [{"name": summary["name"], "data": points}],
                    "sourceFile": filename,
                    "sourceIds": [], "sourceOrigin": "uploaded_attachment",
                    "workbookMetric": summary["name"],
                    "rationale": "A time series calculated from the attached source rows.",
                    "takeaway": f"Shows {len(points)} attached observations over {time_column['name']}; values are read from the source rows.",
                })
                continue

        if not label_column or summary["count"] < 2:
            continue
        label_index = int(label_column["index"])
        groups: OrderedDict[str, list[tuple[int, str, float]]] = OrderedDict()
        for row_index, row in enumerate(data_rows):
            parsed = _parse_data_number(row[columns.index(summary["name"])])
            label = str(row[label_index] or "").strip()
            if parsed is None or not label:
                continue
            value, explicit_percent = parsed
            plotted = value if explicit_percent else value * summary["scale"]
            groups.setdefault(label, []).append((row_index, str(row[columns.index(summary["name"])]), plotted))
        points = []
        for label, observations in groups.items():
            if not observations:
                continue
            plotted = mean(item[2] for item in observations)
            row_numbers = [source_row_numbers[item[0]] for item in observations]
            raw_values = [item[1] for item in observations]
            if len(observations) == 1:
                evidence_value = raw_values[0]
                raw_description = f"raw cell value: {raw_values[0]}"
            else:
                evidence_value = f"{plotted:.8g}"
                raw_description = (
                    f"arithmetic mean of {len(observations)} source rows; "
                    f"raw cell values: {', '.join(raw_values[:12])}"
                    + (" …" if len(raw_values) > 12 else "")
                )
            row_number = row_numbers[0] if len(row_numbers) == 1 else f"{row_numbers[0]}–{row_numbers[-1]}"
            representative = observations[0][0]
            raw_line = physical[source_row_numbers[representative] - 1] if source_row_numbers[representative] - 1 < len(physical) else ",".join(data_rows[representative])
            quote = (
                f"File: {filename} | Sheet: {sheet_name} | Row(s): {row_number} | "
                f"Category: {label} | Metric: {summary['name']} | {raw_description} | "
                f"derived plotted value: {plotted:.8g}{summary['unit']} | Original row: {raw_line}"
            )
            points.append({
                "label": label, "value": round(float(plotted), 6), "evidenceSource": "",
                "evidenceQuote": quote, "evidenceValue": evidence_value,
                "sourceSheet": sheet_name, "sourceRow": row_numbers[0], "sourceFile": filename,
            })
            evidence_lines.append(quote)
        points.sort(key=lambda item: float(item["value"]), reverse=True)
        points = points[:10]
        if len(points) < 2:
            continue
        charts.append({
            "type": "horizontal_bar",
            "title": f"Top {label_column['name']} by {summary['name']} — {sheet_name}",
            "xLabel": label_column["name"], "yLabel": summary["name"],
            "unit": summary["unit"], "series": [{"name": summary["name"], "data": points}],
            "sourceFile": filename,
            "sourceIds": [], "sourceOrigin": "uploaded_attachment",
            "workbookMetric": summary["name"],
            "rationale": "A ranked comparison calculated from the attached source rows.",
            "takeaway": (
                f"Ranks up to ten {label_column['name']} values by {summary['name']}; "
                "where a label occurs more than once, the plotted value is its arithmetic mean."
            ),
        })

    # Numeric-only files have no category/date column for the ranked and time
    # series paths above. When a header clearly names an outcome (for example,
    # price), show its observed relationship to a few numeric inputs as paired
    # scatter series. Every axis value retains the same source row and raw cell;
    # this is descriptive evidence, not a fitted or causal relationship.
    if not label_column and not time_column and len(charts) < max_charts:
        outcomes = [summary for summary in numeric if _OUTCOME_HEADER_RE.search(summary["name"])]
        outcome = outcomes[0] if outcomes else None
        predictors = [
            summary for summary in numeric
            if summary is not outcome
            and not _IDENTIFIER_HEADER_RE.search(summary["name"])
            and not _DATE_HEADER_RE.search(summary["name"])
        ]
        if outcome:
            def values_by_row(summary: dict[str, Any]) -> dict[int, tuple[str, float]]:
                raw_values = summary.get("rawValues") or []
                scaled_values = summary.get("scaledValues") or []
                return {
                    int(raw[0]): (str(raw[1]), float(scaled_values[index]))
                    for index, raw in enumerate(raw_values)
                    if len(raw) >= 2 and index < len(scaled_values)
                }

            y_by_row = values_by_row(outcome)
            x_units = {summary["name"]: str(summary.get("unit") or "") for summary in predictors}
            y_unit = str(outcome.get("unit") or "")
            scatter_count = 0
            for predictor in predictors:
                if len(charts) >= max_charts or scatter_count >= 4:
                    break
                x_by_row = values_by_row(predictor)
                paired_rows = [
                    (row_index, x_by_row[row_index], y_by_row[row_index])
                    for row_index in range(len(data_rows))
                    if row_index in x_by_row and row_index in y_by_row
                ]
                if len(paired_rows) < 4:
                    continue
                x_unit = x_units[predictor["name"]]
                x_label = f"{predictor['name']} ({x_unit})" if x_unit else predictor["name"]
                y_label = f"{outcome['name']} ({y_unit})" if y_unit else outcome["name"]
                x_points = []
                y_points = []
                for row_index, (raw_x, plotted_x), (raw_y, plotted_y) in paired_rows:
                    row_number = source_row_numbers[row_index]
                    raw_line = (
                        physical[row_number - 1]
                        if row_number - 1 < len(physical)
                        else " | ".join(data_rows[row_index])
                    )
                    label = f"Row {row_number}"
                    quote = (
                        f"File: {filename} | Sheet: {sheet_name} | Row: {row_number} | "
                        f"X metric: {predictor['name']} | raw cell value: {raw_x} | "
                        f"Y metric: {outcome['name']} | raw cell value: {raw_y} | "
                        f"Original row: {raw_line}"
                    )
                    common = {
                        "label": label, "evidenceSource": "", "evidenceQuote": quote,
                        "sourceSheet": sheet_name, "sourceRow": row_number, "sourceFile": filename,
                    }
                    x_points.append({
                        **common, "value": round(float(plotted_x), 6), "evidenceValue": raw_x,
                        "evidenceMetric": predictor["name"], "evidenceUnit": x_unit,
                    })
                    y_points.append({
                        **common, "value": round(float(plotted_y), 6), "evidenceValue": raw_y,
                        "evidenceMetric": outcome["name"], "evidenceUnit": y_unit,
                    })
                    evidence_lines.append(quote)
                charts.append({
                    "type": "scatter",
                    "title": f"{outcome['name']} vs {predictor['name']} - {sheet_name}",
                    "xLabel": x_label, "yLabel": y_label,
                    "series": [
                        {"name": predictor["name"], "data": x_points},
                        {"name": outcome["name"], "data": y_points},
                    ],
                    "sourceFile": filename,
                    "sourceIds": [], "sourceOrigin": "uploaded_attachment",
                    "workbookMetric": outcome["name"],
                    "rationale": "Paired numeric observations read from the same attached source rows.",
                    "takeaway": (
                        f"Shows {len(paired_rows)} observed {predictor['name']} and {outcome['name']} pairs. "
                        "The chart describes the sample and does not establish causation."
                    ),
                })
                scatter_count += 1

    return dataset, charts, evidence_lines


def _build_generic_attachment_analytics(
    file_context: str,
    *,
    max_charts: int,
    excluded_sheets: set[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    """Profile tabular sections, optionally leaving domain-specific sheets to another parser."""
    excluded_sheets = excluded_sheets or set()
    files: list[dict[str, Any]] = []
    datasets: list[dict[str, Any]] = []
    charts: list[dict[str, Any]] = []
    evidence_by_file: dict[str, str] = {}
    for filename, body in _split_file_blocks(file_context):
        sheet_blocks = _sheet_blocks(body)
        sections = sheet_blocks or [(filename.rsplit(".", 1)[0] or "Data", body)]
        file_datasets = []
        for sheet_name, section in sections:
            if (filename.casefold(), sheet_name.casefold()) in excluded_sheets:
                continue
            remaining = max(0, max_charts - len(charts))
            dataset, new_charts, evidence_lines = _profile_tabular_section(
                filename, sheet_name, section, max_charts=remaining,
            )
            if dataset is None:
                continue
            datasets.append(dataset)
            file_datasets.append(dataset)
            charts.extend(new_charts[:remaining])
            evidence_by_file.setdefault(filename.casefold(), "")
            prior = evidence_by_file[filename.casefold()]
            evidence_by_file[filename.casefold()] = "\n".join(
                [part for part in (prior, *evidence_lines) if part]
            )[:80000]
        files.append({"filename": filename, "sheets": file_datasets})
    if not datasets:
        return {
            "kind": "generic", "files": files, "datasets": [], "charts": [],
            "keyStats": [], "evidenceByFile": {}, "sourceOrigin": "uploaded_attachment",
            "sourceLabel": "uploaded attachment",
        }
    records = sum(int(dataset.get("rowCount") or 0) for dataset in datasets)
    fields = sum(int(dataset.get("columnCount") or 0) for dataset in datasets)
    key_stats = [
        {"label": "Tabular sections analyzed", "value": str(len(datasets))},
        {"label": "Data rows analyzed", "value": str(records)},
        {"label": "Columns analyzed", "value": str(fields)},
        {"label": "Deterministic comparisons", "value": str(len(charts))},
    ]
    return {
        "kind": "generic_tabular_data", "files": files, "datasets": datasets,
        "charts": charts, "keyStats": key_stats, "evidenceByFile": evidence_by_file,
        "sourceOrigin": "uploaded_attachment", "sourceLabel": "attached data",
    }


_EXTRACTION_NOTES_RE = re.compile(r"(?im)^\s*\[Extraction notes:\s*(.+?)\]\s*$")


def _pipe_cell(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).replace("|", "/").strip()


def _sheet_as_pipe_table(sheet_name: str, body: str) -> str:
    """One worksheet as a header + rows pipe table, or "" when no table header is recognized.

    The evidence store's fact sheet binds a value to its row label (entity) and column header (metric)
    only in pipe tables. Raw CSV lines carry neither: "GREY SKY ...,GSC SMART CORE ...,...,20.29%,..."
    left 20.29% unbound, so V2 removed correct sentences as "in the evidence for another metric or entity".
    """
    rows, _physical = _csv_rows(body)
    if len(rows) < 3:
        return ""
    width = max(len(row) for row in rows)
    pms = _find_pms_table(rows)
    if pms:
        header_index, period_index, indices = pms
        top = rows[header_index] + [""] * (width - len(rows[header_index]))
        lower = rows[period_index] + [""] * (width - len(rows[period_index]))
        return_columns = {col: period for key, col in indices.items() if key.startswith("return:")
                          for period in [key.split(":", 1)[1]]}
        identity = (indices["amc"], indices["approach"])
        columns = [col for col in range(width) if col not in identity
                   and (str(top[col]).strip() or str(lower[col]).strip() or col in return_columns)]
        labels = []
        for col in columns:
            if col in return_columns:
                labels.append(f"{return_columns[col]} return")
            else:
                parts = [str(top[col]).strip()]
                if period_index != header_index and str(lower[col]).strip() and str(lower[col]).strip() != parts[0]:
                    parts.append(str(lower[col]).strip())
                labels.append(" ".join(part for part in parts if part))
        header = ["AMC - investment approach", *labels]
        body_rows = []
        for row in rows[max(header_index, period_index) + 1:]:
            row = row + [""] * (width - len(row))
            amc, approach = str(row[identity[0]]).strip(), str(row[identity[1]]).strip()
            label = f"{amc} - {approach}" if amc and approach else (amc or approach or next(
                (str(cell).strip() for cell in row if str(cell).strip()), ""))
            values = [str(row[col]).strip() for col in columns]
            if label and any(values):
                body_rows.append([label, *values])
    else:
        chosen = _choose_table_header(rows)
        if not chosen:
            return ""
        header_index, names, data_start = chosen
        header = [str(name or "").strip() or f"Column {i + 1}" for i, name in enumerate(names)]
        body_rows = []
        for row in rows[data_start:]:
            cells = [str(cell).strip() for cell in row[:len(header)]] + [""] * max(0, len(header) - len(row))
            if cells and cells[0] and any(cells[1:]):
                body_rows.append(cells)
    if len(body_rows) < 2:
        return ""
    lines = [f"TABLE — {sheet_name}", "| " + " | ".join(_pipe_cell(cell) for cell in header) + " |",
             "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(_pipe_cell(cell) for cell in row) + " |" for row in body_rows]
    return "\n".join(lines)


def tabulate_calculation_lines(text: str) -> str:
    """``text`` with its grouped "Deterministic calculation ..." lines replaced by their tables.

    Appending the tables was not enough: a section writer that saw only the prose line quoted its value,
    and the section's fact sheet (built from that section's passages) still could not bind it.
    """
    tables = calculation_lines_as_tables(text)
    if not tables:
        return str(text or "")
    tabulated = {label for label in re.findall(r"(?m)^TABLE — (.+)$", tables)}
    kept = []
    for line in str(text or "").splitlines():
        if line.startswith("Deterministic calculation from "):
            parts = [part.strip() for part in line.split(";")]
            free = [part for part in parts[1:] if ":" not in part]
            metric_field = next((part.split(":", 1)[1].strip() for part in parts[1:]
                                 if part.casefold().startswith("metric:")), "")
            metric = _pipe_cell(metric_field or (free[0] if free else ""))
            benchmark = next((part.split(":", 1)[1].strip() for part in parts[1:]
                              if part.casefold().startswith("benchmark match:")), "")
            if benchmark and any(part.casefold().startswith("reported horizon:") for part in parts[1:]):
                metric = _pipe_cell(f"{metric_field} ({benchmark} benchmark)")
            if metric in tabulated:
                continue
        kept.append(line)
    return "\n".join(kept).rstrip() + "\n\n" + tables


def calculation_lines_as_tables(text: str) -> str:
    """The server's "Deterministic calculation ...; <metric>; label: X; value: V" lines as one table per metric.

    As prose the fact sheet could not tie a category mean ("label: ETF; value: 15.1767%") to its category,
    so V2 removed the writer's correct sentence as "in the evidence for another metric or entity".
    """
    groups: dict[str, list[tuple[str, str, str]]] = {}
    for line in str(text or "").splitlines():
        if not line.startswith("Deterministic calculation from "):
            continue
        parts = [part.strip() for part in line.split(";")]
        fields = {}
        free = []
        for part in parts[1:]:
            key, sep, value = part.partition(":")
            if sep and key.strip().casefold() in {"label", "value", "observations", "metric", "reported horizon",
                                                   "benchmark match", "category"}:
                fields[key.strip().casefold()] = value.strip()
            elif not sep:
                free.append(part)
        value = fields.get("value", "")
        label = fields.get("label") or fields.get("reported horizon") or fields.get("category") or ""
        metric = fields.get("metric") or (free[0] if free else "")
        if fields.get("benchmark match") and fields.get("reported horizon"):
            metric = f"{metric} ({fields['benchmark match']} benchmark)"
        if value and label and metric:
            groups.setdefault(metric, []).append((label, value, fields.get("observations", "")))
    tables = []
    for metric, rows in groups.items():
        if len(rows) < 2:
            continue
        lines = [f"TABLE — {_pipe_cell(metric)}", f"| Label | {_pipe_cell(metric)} | Observations |", "|---|---|---|"]
        lines += [f"| {_pipe_cell(label)} | {_pipe_cell(value)} | {_pipe_cell(n)} |" for label, value, n in rows]
        tables.append("\n".join(lines))
    return "\n\n".join(tables)


def evidence_tables_for_store(file_text: str) -> str:
    """Uploaded workbook text with every recognizable sheet rewritten as a pipe table (other text unchanged)."""
    text = str(file_text or "")
    markers = list(_SHEET_MARKER.finditer(text))
    if not markers:
        return text
    out = [text[:markers[0].start()]]
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
        body = text[marker.end():end]
        table = _sheet_as_pipe_table(marker.group(1).strip(), body)
        out.append(f"[Sheet: {marker.group(1).strip()}]\n{table}\n\n" if table else text[marker.start():end])
    return "".join(out)


def extraction_notes(file_context: str) -> list[dict[str, str]]:
    """Truncation notes the browser extractor appends to a file ("Only the first 40 of 52 sheets were extracted.").

    Statistics computed from a truncated file describe only the extracted rows; without these notes the report
    presented them as the whole file.
    """
    text = str(file_context or "")
    blocks = _split_file_blocks(text) if _FILE_MARKER.search(text) else [("Attached data", text)]
    notes = []
    for filename, body in blocks:
        for match in _EXTRACTION_NOTES_RE.finditer(body or ""):
            note = re.sub(r"\s+", " ", match.group(1)).strip()
            if note:
                notes.append({"filename": filename, "note": note})
    return notes


def build_attachment_analytics(file_context: str, *, max_charts: int = 12) -> dict[str, Any]:
    """Build deterministic workbook/CSV summaries and charts from attached text."""
    notes = extraction_notes(file_context)
    # The note is extractor metadata, not a data row (it was counted as one row with missing values).
    analytics = _build_attachment_analytics(_EXTRACTION_NOTES_RE.sub("", str(file_context or "")), max_charts=max_charts)
    if notes and isinstance(analytics, dict):
        analytics["extractionNotes"] = notes
    return analytics


def _build_attachment_analytics(file_context: str, *, max_charts: int = 12) -> dict[str, Any]:
    file_context = str(file_context or "")
    if file_context.strip() and not _FILE_MARKER.search(file_context):
        file_context = f"[File: Attached data]\n{file_context}"
    pms = build_pms_workbook_analytics(file_context)
    if pms.get("kind") != "pms_strategy_performance":
        return _build_generic_attachment_analytics(file_context, max_charts=max_charts)

    pms_sheets: set[tuple[str, str]] = set()
    for file_info in pms.get("files") or []:
        if not isinstance(file_info, dict):
            continue
        filename = str(file_info.get("filename") or "")
        records = file_info.get("rows") or []
        pms_sheets.update(
            (filename.casefold(), str(row.get("sheet") or "").casefold())
            for row in records if isinstance(row, dict)
        )
        sheet_metadata_by_name = {
            str(item.get("name") or "").casefold(): item
            for item in file_info.get("sheets") or [] if isinstance(item, dict)
        }
        file_body = next(
            (item[1] for item in _split_file_blocks(file_context)
             if item[0].casefold() == filename.casefold()),
            "",
        )
        sheets = []
        for sheet_name, body in _sheet_blocks(file_body):
            sheet_rows, _ = _csv_rows(body)
            sheet_records = [row for row in records if str(row.get("sheet") or "") == sheet_name]
            available_returns = sum(
                len(row.get("periodValues") or {}) for row in sheet_records if isinstance(row, dict)
            )
            sheet_metadata = sheet_metadata_by_name.get(sheet_name.casefold(), {})
            table = _find_pms_table(sheet_rows)
            supported_period_count = sum(
                1 for period in _RETURN_PERIODS
                if table and table[2].get(f"return:{period}") is not None
            )
            sheets.append({
                "name": sheet_name,
                "title": str(sheet_metadata.get("title") or ""),
                "asOf": str(sheet_metadata.get("asOf") or ""),
                "columnNames": list(sheet_metadata.get("columnNames") or []),
                "fieldMap": dict(sheet_metadata.get("fieldMap") or {}),
                "recognizedFields": list(sheet_metadata.get("recognizedFields") or []),
                "rowCount": len(sheet_records),
                "sourceRows": len(sheet_records),
                "columnCount": int(sheet_metadata.get("columnCount") or max((len(row) for row in sheet_rows), default=0)),
                "missingReturnValues": max(0, len(sheet_records) * supported_period_count - available_returns),
            })
        file_info["sheets"] = sheets
    pms_sheet_rows = [
        (str(file_info.get("filename") or ""), sheet)
        for file_info in pms.get("files") or []
        if isinstance(file_info, dict)
        for sheet in (file_info.get("sheets") or []) if isinstance(sheet, dict)
    ]
    pms_quality = dict(pms.get("dataQuality") or {})
    pms_quality["worksheetCount"] = len(pms_sheet_rows)
    pms_quality["worksheetsWithDate"] = sum(1 for _name, sheet in pms_sheet_rows if sheet.get("asOf"))
    pms_quality["worksheetsWithoutDate"] = [
        {"file": filename, "sheet": str(sheet.get("name") or "")}
        for filename, sheet in pms_sheet_rows if not sheet.get("asOf")
    ]
    semantic_fields = ("amc", "approach", "benchmark", "aum", "category")
    field_sheet_counts = {
        field: sum(1 for _filename, sheet in pms_sheet_rows if field in (sheet.get("recognizedFields") or []))
        for field in semantic_fields
    }
    pms_quality["fieldPresence"] = {field: count > 0 for field, count in field_sheet_counts.items()}
    pms_quality["fieldSheetCounts"] = field_sheet_counts
    strategies = pms.get("strategies") or []
    for field, key in (("aum", "Aum"), ("benchmark", "Benchmark")):
        applicable = [row for row in strategies if field in (row.get("recognizedFields") or [])]
        if field == "aum":
            value_count = sum(1 for row in applicable if row.get("aum") is not None)
        else:
            value_count = sum(1 for row in applicable if _norm(row.get("benchmark")) not in _NA)
        pms_quality[f"strategiesWith{key}Field"] = len(applicable)
        pms_quality[f"strategiesWith{key}Value"] = value_count
        pms_quality[f"strategiesWithout{key}AmongField"] = len(applicable) - value_count
    mapped_labels: dict[str, set[str]] = {field: set() for field in semantic_fields}
    for _filename, sheet in pms_sheet_rows:
        for field, label in (sheet.get("fieldMap") or {}).items():
            if field in mapped_labels and str(label or "").strip():
                mapped_labels[field].add(str(label).strip())
    pms["fieldMap"] = {field: sorted(labels) for field, labels in mapped_labels.items() if labels}
    pms["dataQuality"] = pms_quality

    periods = []
    for period in _RETURN_PERIODS:
        values = [
            float(row["periodValues"][period]) for row in strategies
            if period in (row.get("periodValues") or {})
        ]
        if values:
            periods.append({
                "period": period, "available": len(values), "total": len(strategies),
                "coverage": len(values) / max(1, len(strategies)),
                "mean": mean(values), "median": median(values),
                "min": min(values), "q1": _quantile(values, 0.25),
                "q3": _quantile(values, 0.75), "max": max(values),
            })
    categories: OrderedDict[str, list[float]] = OrderedDict()
    for row in strategies:
        value = (row.get("periodValues") or {}).get("1 Yr")
        category = str(row.get("category") or "").strip()
        if value is not None and category:
            categories.setdefault(category, []).append(float(value))
    pms["periodSummaries"] = periods
    pms["categorySummaries"] = [
        {"category": category, "available": len(values), "mean1Year": mean(values), "median1Year": median(values)}
        for category, values in sorted(categories.items(), key=lambda item: mean(item[1]), reverse=True)
    ]

    # These are snapshot summaries across independently reported lookback
    # horizons, not sequential observations. Only aggregate across one PMS
    # workbook so every plotted statistic has a single, unambiguous source.
    pms_workbooks = [item for item in (pms.get("files") or []) if isinstance(item, dict) and item.get("filename")]
    if len(pms_workbooks) == 1:
        filename = str(pms_workbooks[0].get("filename") or "")

        def summary_points(label_key: str, value_key: str, metric_label: str, rows: list[dict]) -> list[dict]:
            points = []
            for item in rows:
                label = str(item.get(label_key) or "").strip()
                try:
                    value = round(float(item[value_key]), 4)
                except (KeyError, TypeError, ValueError):
                    continue
                observations = int(item.get("available") or 0)
                quote = (
                    f"Deterministic calculation from {filename}; {metric_label}; label: {label}; "
                    f"value: {value:.4f}%; observations: {observations}; "
                    "missing values excluded; calculated from the attached workbook rows."
                )
                points.append({
                    "label": label, "value": value, "evidenceSource": "",
                    "evidenceQuote": quote, "evidenceValue": f"{value:.4f}%",
                    "sourceFile": filename,
                })
            return points

        distribution_points = []
        for item in periods:
            if int(item.get("available") or 0) < MIN_CATEGORY_SIZE:
                continue
            label = str(item.get("period") or "").strip()
            try:
                values = {
                    key: round(float(item[key]), 4)
                    for key in ("min", "q1", "median", "q3", "max")
                }
                observations = int(item.get("available") or 0)
            except (KeyError, TypeError, ValueError):
                continue
            quote = (
                f"Deterministic calculation from {filename}; reported return horizon: {label}; "
                f"minimum: {values['min']:.4f}%; first quartile: {values['q1']:.4f}%; "
                f"median: {values['median']:.4f}%; third quartile: {values['q3']:.4f}%; "
                f"maximum: {values['max']:.4f}%; observations: {observations}; "
                "missing values excluded; calculated from the attached workbook rows."
            )
            distribution_points.append({
                "label": label, "value": values["median"], **values,
                "evidenceSource": "", "evidenceQuote": quote,
                "evidenceValue": f"{values['median']:.4f}%", "sourceFile": filename,
            })
        ranked_categories = [
            item for item in pms["categorySummaries"]
            if int(item.get("available") or 0) >= MIN_CATEGORY_SIZE
        ]
        category_mean = summary_points("category", "mean1Year", "mean 1-year reported PMS return by category", ranked_categories)
        category_median = summary_points("category", "median1Year", "median 1-year reported PMS return by category", ranked_categories)

        summary_charts = []
        if len(distribution_points) >= 3:
            summary_charts.append({
                "type": "boxplot",
                "title": "PMS return distribution by reported horizon",
                "xLabel": "Reported return horizon", "yLabel": "Reported return (%)", "unit": "%",
                "series": [{"name": "Reported PMS return distribution", "data": distribution_points}],
                "sourceIds": [], "sourceOrigin": "uploaded_workbook", "chartRole": "return_distribution",
                "sourceFiles": [filename],
                "rationale": "Compare the observed return spread and interquartile range across separate workbook lookback columns.",
                "takeaway": (
                    "Boxes show the middle half of observed strategy returns; whiskers show each horizon's minimum and maximum. "
                    "These are separate lookback windows in one snapshot, not sequential quarters."
                ),
            })
        if category_mean and category_median:
            summary_charts.append({
                "type": "grouped_bar",
                "title": "PMS 1-year return averages by category (mean vs median)",
                "xLabel": "PMS category", "yLabel": "Reported 1-year return (%)", "unit": "%",
                "series": [
                    {"name": "Mean 1-year return", "data": category_mean},
                    {"name": "Median 1-year return", "data": category_median},
                ],
                "sourceIds": [], "sourceOrigin": "uploaded_workbook", "chartRole": "snapshot_summary",
                "sourceFiles": [filename],
                "rationale": "Compare category means and medians for reported 1-year returns; small categories are excluded from the chart.",
                "takeaway": (
                    f"Only categories with at least {MIN_CATEGORY_SIZE} reported 1-year observations are shown. "
                    "The mean and median describe this workbook snapshot and do not establish what caused performance."
                ),
            })
        benchmark_chart, benchmark_evidence_lines = _matched_benchmark_chart(pms, filename)
        if benchmark_chart:
            summary_charts.append(benchmark_chart)

        aum_field_labels = list(dict.fromkeys(
            str(label).strip() for label in (pms.get("fieldMap") or {}).get("aum", [])
            if str(label).strip()
        ))
        def aum_unit_key(label: str) -> str:
            words = _chart_words(label)
            tokens = words.split()
            currency_aliases = {
                "rs": "local", "rupee": "local", "rupees": "local", "inr": "local",
                "usd": "usd", "dollar": "usd", "dollars": "usd",
                "eur": "eur", "euro": "eur", "euros": "eur",
                "gbp": "gbp", "pound": "gbp", "pounds": "gbp",
            }
            currencies = {currency_aliases[word] for word in tokens if word in currency_aliases}
            currency = "/".join(sorted(currencies)) if currencies else "local"
            ignored = {
                "aum", "asset", "assets", "under", "management", "in", "of", "total", "reported",
                *currency_aliases,
            }
            units = []
            for token in tokens:
                if token in ignored:
                    continue
                if token in {"cr", "crs", "crores"}:
                    token = "crore"
                elif token in {"lakh", "lakhs", "lacs", "lac"}:
                    token = "lakh"
                units.append(token)
            return " ".join([currency, *units]).strip()

        aum_unit_keys = {aum_unit_key(label) for label in aum_field_labels}
        disclosed_aum = [
            row for row in strategies
            if isinstance(row, dict) and isinstance(row.get("aum"), (int, float))
            and float(row["aum"]) >= 0 and row.get("aumEvidence")
        ]
        if aum_field_labels and len(aum_unit_keys) == 1 and len(disclosed_aum) >= MIN_CATEGORY_SIZE:
            aum_label = aum_field_labels[0] if len(aum_field_labels) == 1 else "AUM (reported units)"
            disclosed_aum.sort(key=lambda row: float(row.get("aum") or 0), reverse=True)
            aum_points = []
            for row in disclosed_aum[:10]:
                evidence = row.get("aumEvidence") or {}
                raw_value = str(evidence.get("rawValue") or row.get("aumRaw") or "").strip()
                plotted_value = round(float(row.get("aum") or 0), 4)
                strategy_label = " / ".join(
                    str(row.get(key) or "").strip() for key in ("amc", "approach")
                    if str(row.get(key) or "").strip()
                ) or "Unlabeled strategy"
                quote = " | ".join(part for part in (
                    f"File: {filename}", f"Sheet: {evidence.get('sheet') or row.get('sheet')}",
                    f"Row: {evidence.get('rowNumber') or row.get('rowNumber')}",
                    f"As of: {evidence.get('asOf') or row.get('asOf')}" if evidence.get("asOf") or row.get("asOf") else "",
                    f"Strategy: {strategy_label}", f"AUM field: {aum_label}",
                    f"raw workbook cell value: {raw_value}",
                    f"plotted reported AUM: {plotted_value:.4f}",
                    f"Headers: {evidence.get('headerLine') or row.get('headerLine')}",
                    f"Original row: {evidence.get('rawLine') or row.get('rawLine')}",
                ) if part)
                aum_points.append({
                    "label": strategy_label, "value": plotted_value,
                    "evidenceSource": "", "evidenceQuote": quote,
                    "evidenceValue": raw_value, "evidenceMetric": aum_label,
                    "sourceSheet": evidence.get("sheet") or row.get("sheet"),
                    "sourceRow": evidence.get("rowNumber") or row.get("rowNumber"),
                    "sourceFile": evidence.get("filename") or row.get("filename") or filename,
                })
            summary_charts.append({
                "type": "horizontal_bar",
                "title": "Largest reported AUM values among disclosing strategies",
                "xLabel": "PMS strategy", "yLabel": f"Reported {aum_label}", "unit": "",
                "series": [{"name": f"Reported {aum_label}", "data": aum_points}],
                "sourceIds": [], "sourceOrigin": "uploaded_workbook", "chartRole": "aum_disclosure",
                "sourceFiles": [filename],
                "rationale": "Rank only the strategy rows with a usable value in the detected AUM field.",
                "takeaway": (
                    f"This ranks the ten largest reported values in {aum_label}. "
                    "It describes the disclosed subset only; missing AUM values prevent a full-universe concentration estimate."
                ),
            })

        pms["charts"] = [*(pms.get("charts") or []), *summary_charts]
        if summary_charts or benchmark_evidence_lines:
            evidence_key = filename.casefold()
            evidence_lines = [
                point["evidenceQuote"]
                for chart in summary_charts
                for series in chart.get("series") or []
                for point in series.get("data") or []
                if point.get("evidenceQuote")
            ]
            evidence_lines = list(dict.fromkeys([*benchmark_evidence_lines, *evidence_lines]))
            prior_excerpt = str((pms.get("evidenceByFile") or {}).get(evidence_key) or "")
            pms.setdefault("evidenceByFile", {})[evidence_key] = "\n".join(
                part for part in (*evidence_lines, prior_excerpt) if part
            )[:UPLOAD_CHART_EVIDENCE_CHARS]

    # Retain notes/summary sheets, other files, and OCR'd tables alongside a
    # specialized PMS workbook instead of returning as soon as it is detected.
    generic = _build_generic_attachment_analytics(
        file_context,
        max_charts=max(0, max_charts - len(pms.get("charts") or [])),
        excluded_sheets=pms_sheets,
    )
    pms["datasets"] = list(generic.get("datasets") or [])
    pms["additionalFiles"] = [
        item for item in (generic.get("files") or [])
        if isinstance(item, dict) and item.get("sheets")
    ]
    pms["charts"] = [*(pms.get("charts") or []), *(generic.get("charts") or [])][:max_charts]
    pms["evidenceByFile"] = {**(pms.get("evidenceByFile") or {}), **(generic.get("evidenceByFile") or {})}
    all_file_info = [*(pms.get("files") or []), *(pms.get("additionalFiles") or [])]
    pms["evidenceOriginByFile"] = {
        str(item.get("filename") or "").casefold(): (
            "uploaded_workbook" if re.search(r"\.(?:xlsx|xlsm|xls)$", str(item.get("filename") or ""), re.I)
            else "uploaded_attachment"
        )
        for item in all_file_info if isinstance(item, dict)
    }
    pms["keyStats"] = list(pms.get("keyStats") or []) + [
        item for item in (generic.get("keyStats") or [])
        if isinstance(item, dict) and item.get("label") not in {"Deterministic comparisons"}
    ]
    pms["sourceOrigin"] = "uploaded_workbook"
    pms["sourceLabel"] = "uploaded files"
    return pms


def _pms_date_label(value: object) -> str:
    raw = re.sub(r"\s+", " ", str(value or "")).strip()
    match = re.search(r"(?i)\b(\d{1,2})\s+([a-z]{3,9})\.?[,]?\s+(20\d{2})\b", raw)
    if not match:
        return raw
    month = match.group(2).casefold()[:3]
    month_names = {
        "jan": "January", "feb": "February", "mar": "March", "apr": "April",
        "may": "May", "jun": "June", "jul": "July", "aug": "August",
        "sep": "September", "oct": "October", "nov": "November", "dec": "December",
    }
    return f"{int(match.group(1))} {month_names.get(month, match.group(2).title())} {match.group(3)}"


def _featured_pms_period_summaries(analytics: dict[str, Any]) -> list[dict[str, Any]]:
    """Choose up to three observed horizons, adapting when common horizons are absent."""
    summaries = [
        item for item in (analytics.get("periodSummaries") or [])
        if isinstance(item, dict) and str(item.get("period") or "").strip()
    ]
    by_period = {str(item.get("period")): item for item in summaries}
    selected = [by_period[name] for name in ("1 Mth", "1 Yr", "5 Yr") if name in by_period]
    for item in reversed(summaries):
        if len(selected) >= min(3, len(summaries)):
            break
        if item not in selected:
            selected.append(item)
    return selected[:3]


def pms_report_cover(analytics: dict[str, Any]) -> tuple[str, str]:
    """Create a safe cover title and summary from cell-derived PMS statistics."""
    if str((analytics or {}).get("kind") or "") != "pms_strategy_performance":
        return "", ""
    snapshot_dates = [str(value).strip() for value in (analytics.get("snapshotDates") or []) if str(value).strip()]
    snapshot_label = _pms_date_label(snapshot_dates[0]) if len(snapshot_dates) == 1 else ""
    title = f"PMS Strategy Performance Audit - {snapshot_label}" if snapshot_label else "PMS Strategy Performance Audit"
    quality = analytics.get("dataQuality") if isinstance(analytics.get("dataQuality"), dict) else {}
    unique_count = int(quality.get("uniqueStrategies") or len(analytics.get("strategies") or []))
    sheet_count = int(quality.get("worksheetCount") or 0)
    dated_count = int(quality.get("worksheetsWithDate") or 0)
    summary_sentences = []
    if snapshot_label:
        snapshot_phrase = f"The attached-data snapshot is dated {snapshot_label}"
    elif snapshot_dates:
        snapshot_phrase = "The attached data contains more than one reporting date"
    else:
        snapshot_phrase = "The attached data does not provide one consistent reporting date"
    summary_sentences.append(f"{snapshot_phrase} and covers {unique_count} unique manager / strategy pairs.")
    if sheet_count and dated_count < sheet_count:
        missing = sheet_count - dated_count
        summary_sentences.append(
            f"{dated_count} of {sheet_count} analyzed worksheets state an as-of date; {missing} {'does' if missing == 1 else 'do'} not."
        )
    returns = []
    for item in _featured_pms_period_summaries(analytics):
        period = str(item.get("period") or "")
        try:
            value = float(item["median"])
            count = int(item.get("available") or 0)
        except (KeyError, TypeError, ValueError):
            continue
        returns.append(f"{period} median {value:.2f}% (n={count})")
    if returns:
        summary_sentences.append("Reported returns: " + ", ".join(returns) + ".")
    summary_sentences.append(
        "These horizons are separate lookback measures in one attachment snapshot; they do not by themselves show quarter-by-quarter changes or establish causes."
    )
    return title, " ".join(summary_sentences)


def rewrite_pms_report_sections(
    report_text: str,
    analytics: dict[str, Any],
    source_ids_by_file: dict[str, str] | None = None,
) -> str:
    """Replace unsupported prose in PMS upload reports with measured workbook facts."""
    if str((analytics or {}).get("kind") or "") != "pms_strategy_performance":
        return str(report_text or "")

    source_lookup = {
        str(filename).casefold(): str(source_id or "").strip().upper()
        for filename, source_id in (source_ids_by_file or {}).items() if source_id
    }
    citations = " ".join(dict.fromkeys(
        f"[{source_lookup[str(item.get('filename') or '').casefold()]}]"
        for item in (analytics.get("files") or [])
        if isinstance(item, dict) and source_lookup.get(str(item.get("filename") or "").casefold())
    ))
    citation = f" {citations}" if citations else ""
    title, summary = pms_report_cover(analytics)
    quality = analytics.get("dataQuality") if isinstance(analytics.get("dataQuality"), dict) else {}
    field_presence = quality.get("fieldPresence") if isinstance(quality.get("fieldPresence"), dict) else {}
    field_map = analytics.get("fieldMap") if isinstance(analytics.get("fieldMap"), dict) else {}
    has_aum_field = bool(field_presence.get("aum"))
    has_benchmark_field = bool(field_presence.get("benchmark"))
    has_category_field = bool(field_presence.get("category"))

    def labels_for(field: str) -> list[str]:
        values = field_map.get(field) or []
        if isinstance(values, str):
            values = [values]
        return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))

    def named_field(field: str, fallback: str) -> str:
        labels = labels_for(field)
        return ", ".join(labels) if labels else fallback

    period_by_name = {
        str(item.get("period") or ""): item
        for item in (analytics.get("periodSummaries") or []) if isinstance(item, dict)
    }
    def period_fact(period: str) -> str:
        item = period_by_name.get(period)
        if not item:
            return f"{period}: no usable observations"
        try:
            return f"{period}: median {float(item['median']):.2f}% across {int(item.get('available') or 0)} observations"
        except (KeyError, TypeError, ValueError):
            return f"{period}: no usable observations"

    featured_periods = _featured_pms_period_summaries(analytics)
    comparative_text = "; ".join(
        period_fact(str(item.get("period") or "")) for item in featured_periods
    ) or "No recognized return horizon has enough usable observations for a comparison"
    since_inception_note = (
        " Since-Inception returns can cover different start dates; use the source's inception-date field before comparing them."
        if "Since Incp." in period_by_name else ""
    )

    categories = [
        item for item in (analytics.get("categorySummaries") or [])
        if isinstance(item, dict) and int(item.get("available") or 0) >= MIN_CATEGORY_SIZE
    ]
    highest = max(categories, key=lambda item: float(item.get("median1Year") or 0), default=None)
    lowest = min(categories, key=lambda item: float(item.get("median1Year") or 0), default=None)
    category_finding = (
        "No category column was recognized, so category comparisons are unavailable."
        if not has_category_field
        else "Category medians are unavailable for a comparison with at least three observations per category."
    )
    if highest and lowest:
        category_finding = (
            f"Among categories with at least {MIN_CATEGORY_SIZE} reported 1-year values, the highest median was "
            f"{float(highest.get('median1Year') or 0):.2f}% for {highest.get('category')} "
            f"(n={int(highest.get('available') or 0)}); the lowest was "
            f"{float(lowest.get('median1Year') or 0):.2f}% for {lowest.get('category')} "
            f"(n={int(lowest.get('available') or 0)}). These are descriptive category comparisons, not evidence of a defensive effect or its cause."
        )

    sheets = [
        sheet for file_info in (analytics.get("files") or []) if isinstance(file_info, dict)
        for sheet in (file_info.get("sheets") or []) if isinstance(sheet, dict)
    ]
    core_sheets = [sheet for sheet in sheets if "top performance" not in str(sheet.get("title") or "").casefold()]
    ranking_sheets = [sheet for sheet in sheets if "top performance" in str(sheet.get("title") or "").casefold()]

    def sheet_names(items: list[dict]) -> str:
        values = [str(item.get("name") or "").strip() for item in items if str(item.get("name") or "").strip()]
        numbers = sorted({int(match.group(1)) for name in values if (match := re.fullmatch(r"(?i)Table\s+(\d+)", name))})
        compressed = []
        index = 0
        while index < len(numbers):
            end = index
            while end + 1 < len(numbers) and numbers[end + 1] == numbers[end] + 1:
                end += 1
            if end - index >= 1:
                compressed.append(f"Tables {numbers[index]}-{numbers[end]}")
            else:
                compressed.append(f"Table {numbers[index]}")
            index = end + 1
        non_table = [name for name in values if not re.fullmatch(r"(?i)Table\s+\d+", name)]
        return ", ".join([*compressed, *non_table]) or "the detected PMS worksheets"

    field_names = list(dict.fromkeys(
        str(field).strip()
        for sheet in core_sheets for field in (sheet.get("columnNames") or [])
        if str(field).strip()
    ))
    field_text = ", ".join(field_names) if field_names else "the fields detected in the strategy and return table"
    role_descriptions = [
        f"manager/provider field: {named_field('amc', 'detected manager field')}",
        f"strategy/portfolio field: {named_field('approach', 'detected strategy field')}",
    ]
    if has_category_field:
        role_descriptions.append(f"category field: {named_field('category', 'detected category field')}")
    else:
        role_descriptions.append("category field: not recognized")
    if has_benchmark_field:
        role_descriptions.append(f"benchmark field: {named_field('benchmark', 'detected benchmark field')}")
    else:
        role_descriptions.append("benchmark field: not recognized")
    if has_aum_field:
        role_descriptions.append(f"AUM field: {named_field('aum', 'detected AUM field')}")
    else:
        role_descriptions.append("AUM field: not recognized")
    missing_date_sheets = [
        str(item.get("sheet") or "") for item in (quality.get("worksheetsWithoutDate") or [])
        if isinstance(item, dict) and item.get("sheet")
    ]
    date_count = int(quality.get("worksheetsWithDate") or 0)
    sheet_count = int(quality.get("worksheetCount") or len(sheets))
    date_quality = (
        f"{date_count} of {sheet_count} analyzed worksheets carry an explicit as-of date"
        if sheet_count else "Worksheet date coverage could not be determined"
    )
    if missing_date_sheets:
        date_quality += "; missing date: " + ", ".join(missing_date_sheets)
    raw_rows = int(quality.get("rawStrategyRows") or 0)
    unique = int(quality.get("uniqueStrategies") or len(analytics.get("strategies") or []))
    repeated = int(quality.get("repeatedRows") or 0)
    conflicts = int(quality.get("duplicateReturnConflicts") or 0)
    benchmark_applicable = int(quality.get("strategiesWithBenchmarkField") or 0)
    benchmark_reported = int(quality.get("strategiesWithBenchmarkValue") or 0)
    benchmark_missing = int(quality.get("strategiesWithoutBenchmarkAmongField") or 0)
    benchmark_values = [
        str(row.get("benchmark") or "").strip()
        for row in (analytics.get("strategies") or [])
        if isinstance(row, dict) and str(row.get("benchmark") or "").strip()
    ]
    benchmark_labels_only = bool(benchmark_values) and all(_parse_num(value) is None for value in benchmark_values)
    benchmark_comparison_chart = next((
        chart for chart in (analytics.get("charts") or [])
        if isinstance(chart, dict) and chart.get("chartRole") == "benchmark_comparison"
    ), None)
    aum_applicable = int(quality.get("strategiesWithAumField") or 0)
    aum_reported = int(quality.get("strategiesWithAumValue") or 0)
    aum_missing = int(quality.get("strategiesWithoutAumAmongField") or 0)
    duplicate_text = (
        f"Across {raw_rows} parsed strategy rows, {unique} unique AMC / investment-approach pairs remained after "
        f"deduplicating {repeated} repeated rows; overlapping reported return conflicts: {conflicts}."
    )
    benchmark_text = (
        "No benchmark column was recognized, so benchmark coverage cannot be assessed."
        if not has_benchmark_field else (
            f"{benchmark_missing} of {benchmark_applicable} strategies represented in tables with a benchmark field "
            "have no usable benchmark value."
            if benchmark_missing else
            f"All {benchmark_reported} strategies represented in tables with a benchmark field have a benchmark value."
        )
    )
    if has_benchmark_field and benchmark_labels_only:
        benchmark_text += " Benchmark entries are labels rather than period-specific return observations in the detected strategy rows; "
        if benchmark_comparison_chart:
            # Do not deny a comparison the report shows: separate benchmark-return rows can support it.
            comparison_periods = list(dict.fromkeys(
                str(point.get("label") or "").strip()
                for series in (benchmark_comparison_chart.get("series") or []) if isinstance(series, dict)
                for point in (series.get("data") or []) if isinstance(point, dict) and str(point.get("label") or "").strip()
            ))
            period_note = f" for {', '.join(comparison_periods)}" if comparison_periods else ""
            benchmark_text += (
                "separate benchmark-return rows in the workbook support the matched comparison shown in "
                f"'{benchmark_comparison_chart.get('title') or 'the benchmark comparison chart'}'{period_note}. "
                "It covers only strategies that name that benchmark."
            )
        else:
            benchmark_text += "the attachment does not support a benchmark-versus-strategy return comparison from this field."
    aum_text = (
        "No AUM column was recognized, so this attachment cannot support AUM disclosure or AUM-weighted comparisons."
        if not has_aum_field else (
            "AUM is incomplete in the attached rows; any disclosed-value comparison is limited to the strategies with a usable value. "
            "This does not support full-universe concentration or AUM-weighted returns."
        )
    )
    aum_section = (
        "No AUM column was recognized; AUM disclosure and AUM-weighted returns cannot be assessed from this attachment."
        if not has_aum_field else (
            f"The detected {named_field('aum', 'AUM')} field supports a comparison of disclosed values only. "
            "The AUM chart and disclosure table exclude missing values; they cannot establish full-universe market concentration "
            "or AUM-weighted returns."
        )
    )
    return_distribution_facts = []
    for item in featured_periods:
        try:
            available = int(item.get("available") or 0)
            if available < MIN_CATEGORY_SIZE:
                continue
            return_distribution_facts.append(
                f"{item['period']}: middle 50% {float(item['q1']):.2f}% to {float(item['q3']):.2f}% "
                f"(n={available}); full range {float(item['min']):.2f}% to {float(item['max']):.2f}%"
            )
        except (KeyError, TypeError, ValueError):
            continue
    return_distribution_text = (
        "Across strategies, the observed return spread is " + "; ".join(return_distribution_facts) + ". "
        if return_distribution_facts else
        "There are not enough repeated return observations in the detected horizons to summarize a distribution. "
    ) + (
        "This is cross-sectional dispersion between strategies, not within-strategy volatility, drawdown, or risk-adjusted performance."
    )
    ranked_periods = list(dict.fromkeys(
        str(chart.get("workbookMetric") or "").strip()
        for chart in (analytics.get("charts") or [])
        if isinstance(chart, dict) and chart.get("chartRole") == "strategy_ranking"
        and str(chart.get("workbookMetric") or "").strip()
    ))
    ranking_text = (
        "Ranked charts and the supporting table show the highest reported strategies for "
        + ", ".join(ranked_periods)
        + ". They use the detected manager and strategy fields, exclude missing return cells, and show a ranked slice rather than the full universe."
        if ranked_periods else
        "No return horizon has enough labeled observations to produce a strategy ranking."
    )
    scope_text = (
        "This attachment compares PMS strategies across reported lookback windows. It does not contain sequential quarter-by-quarter sector returns, "
        "so it cannot establish sector rotation, fund flows, or the causes of performance. Those questions need dated sector and benchmark observations."
    )
    methodology_notes = [
        str(note).strip() for note in (analytics.get("methodologyNotes") or []) if str(note).strip()
    ]
    methodology = " ".join(methodology_notes) or "The attached data does not state a return calculation method in the extracted notes."

    replacement_sections = [
        (
            lambda heading: bool(re.search(r"executive\s+(?:summary|findings)|executive", heading, re.I)),
            "Executive Summary",
            f"{summary}{citation}\n\nThe summary statistics are calculated from the attached rows. Missing values are excluded, and strategy counts are deduplicated by the detected manager/provider and strategy/portfolio fields.",
        ),
        (
            lambda heading: bool(re.search(r"category", heading, re.I)) and not bool(re.search(r"coverage|map|one.year|^.*category comparison$", heading, re.I)),
            "Key Performance Findings by Category",
            f"{category_finding} Where available, the comparison uses category labels and reported 1-year returns; each strategy contributes one value, regardless of AUM.{citation}",
        ),
        (
            lambda heading: bool(re.search(
                r"comparative\s+(?:performance|horizon)|cross[- ]horizon|horizon\s+analysis", heading, re.I
            )),
            "Comparative Performance Analysis",
            f"{comparative_text}.{citation} The recognized horizons are separate lookbacks ending around the attachment snapshot and have different observation counts. They do not by themselves establish quarter-by-quarter sector rotation or explain causes.",
        ),
        (
            lambda heading: bool(re.search(r"\baum\b|assets\s+under\s+management", heading, re.I))
            and not bool(re.search(r"quality|integrity|^aum disclosure$", heading, re.I)),
            "AUM Disclosure and Coverage",
            f"{aum_section}{citation}",
        ),
        (
            lambda heading: bool(re.search(r"top\s+performing\s+strateg|highest\s+reported\s+strateg|strategy\s+ranking|strategies\s+by\s+horizon", heading, re.I))
            and not bool(re.search(r"^highest reported strategies by return horizon$", heading, re.I)),
            "Strategy Rankings by Reported Horizon",
            f"{ranking_text}{citation}",
        ),
        (
            lambda heading: bool(re.search(r"risk|volatility|dispersion|return\s+distribution", heading, re.I))
            and not bool(re.search(r"coverage|methodology|quality|integrity", heading, re.I)),
            "Return Distribution and Risk Limits",
            f"{return_distribution_text}{citation}",
        ),
        (
            lambda heading: bool(re.search(r"strategic\s+implication|market\s+outlook|investment\s+implication", heading, re.I)),
            "Interpretation and Scope",
            f"{scope_text}{citation}",
        ),
        (
            lambda heading: bool(re.search(r"methodology|derived\s+calculation", heading, re.I)),
            "Methodology and Derived Calculations",
            f"{methodology} Strategy identity uses the detected manager/provider and strategy/portfolio fields. Return summaries are calculated separately for each detected horizon; missing values are excluded, not treated as zero. Return scale is inferred per column from its labels, explicit percent signs and observed numeric representation; explicit percentage strings are kept as displayed. {('AUM comparisons use the detected ' + named_field('aum', 'AUM') + ' field where values are available.') if has_aum_field else 'AUM comparisons are unavailable because no AUM field was recognized.'}{citation}",
        ),
        (
            lambda heading: bool(re.search(r"data\s+quality|integrity\s+audit", heading, re.I)),
            "Data Quality and Integrity Audit",
            f"{date_quality}. {duplicate_text} {benchmark_text} {aum_text}{since_inception_note}{citation}\n\n### Worksheet and field map\nPerformance summaries use {sheet_names(core_sheets)}: {field_text}. Detected field roles: {'; '.join(role_descriptions)}. Strategy identity and deduplication use the detected manager/provider plus strategy/portfolio values; return distributions use the detected return-horizon columns. {('Ranking worksheets ' + sheet_names(ranking_sheets) + ' repeat ranked observations and are not counted as additional unique strategies.') if ranking_sheets else ''}",
        ),
    ]
    text = str(report_text or "").replace("\r\n", "\n").replace("\r", "\n")
    heading_matches = list(re.finditer(r"(?m)^[ \t]*##[ \t]+(?!#)([^\n]+)\s*$", text))
    for matches, default_heading, body in replacement_sections:
        heading_matches = list(re.finditer(r"(?m)^[ \t]*##[ \t]+(?!#)([^\n]+)\s*$", text))
        selected = next((match for match in heading_matches if matches(match.group(1).strip())), None)
        if selected:
            next_heading = next((match for match in heading_matches if match.start() > selected.start()), None)
            end = next_heading.start() if next_heading else len(text)
            section_body = text[selected.end():end]
            chart_markers = list(dict.fromkeys(re.findall(r"\[CHART_\d+\]", section_body)))
            new_section = f"## {default_heading}\n\n{body.strip()}"
            if chart_markers:
                new_section += "\n\n" + "\n".join(chart_markers)
            text = text[:selected.start()] + new_section + "\n\n" + text[end:].lstrip("\n")
        else:
            insert_at = next((match.start() for match in heading_matches
                              if re.search(r"attached-data\s+coverage|quantitative\s+comparisons", match.group(1), re.I)), len(text))
            new_section = f"## {default_heading}\n\n{body.strip()}\n\n"
            text = text[:insert_at].rstrip() + "\n\n" + new_section + text[insert_at:].lstrip("\n")

    # Replace model-written horizon analysis with a compact account calculated
    # from the detected return column. This works from the schema-derived PMS
    # rows, so it adapts to uploaded workbooks and CSV exports with different
    # filenames and optional columns.
    number_words = {
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
        "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    }
    horizon_pattern = re.compile(
        r"(?i)\b(?P<number>\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)"
        r"\s*[- ]?\s*(?P<unit>months?|mths?|mth|mos?|years?|yrs?|yr)\b"
    )

    def period_for_heading(heading: str) -> str | None:
        if not re.search(r"\b(?:strategy|performance|returns?)\b", heading, re.I):
            return None
        if re.search(r"\b(?:coverage|quality|integrity|methodology|audit)\b", heading, re.I):
            return None
        match = horizon_pattern.search(heading)
        if not match:
            return None
        raw_number = match.group("number").casefold()
        number = int(raw_number) if raw_number.isdigit() else number_words[raw_number]
        unit = match.group("unit").casefold()
        alias = _return_period_alias(f"{number} {'month' if unit.startswith(('m', 'mo')) else 'year'}")
        return alias if alias in period_by_name else None

    def horizon_summary(period: str) -> str:
        item = period_by_name.get(period)
        if not item:
            return f"No usable observations were detected for the {period} return horizon, so no ranking is reported.{citation}"
        try:
            available = int(item.get("available") or 0)
            total = int(item.get("total") or unique)
            median_value = float(item["median"])
            minimum = float(item["min"])
            maximum = float(item["max"])
        except (KeyError, TypeError, ValueError):
            return f"The {period} return values could not be summarized consistently from the detected rows.{citation}"
        if available <= 0:
            return f"No usable observations were detected for the {period} return horizon, so no ranking is reported.{citation}"

        ranked = []
        for row in analytics.get("strategies") or []:
            if not isinstance(row, dict):
                continue
            value = (row.get("periodValues") or {}).get(period)
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                continue
            identity = " / ".join(
                str(row.get(key) or "").strip() for key in ("amc", "approach")
                if str(row.get(key) or "").strip()
            ) or "Unlabeled strategy"
            ranked.append((numeric, identity))
        ranked.sort(key=lambda pair: (-pair[0], pair[1].casefold()))
        leaders = "; ".join(f"{name} ({value:.2f}%)" for value, name in ranked[:3])
        ranking = f" Highest reported values were: {leaders}." if leaders else " Strategy names were unavailable for ranking."
        return (
            f"The attached data contains {available} usable {period} return observations out of {total} detected strategy rows. "
            f"The median was {median_value:.2f}%, with a range of {minimum:.2f}% to {maximum:.2f}%."
            f"{ranking} This is a cross-sectional lookback snapshot; it does not show quarter-by-quarter rotation or establish causes.{citation}"
        )

    period_headings = list(re.finditer(r"(?m)^[ \t]*##[ \t]+(?!#)([^\n]+)\s*$", text))
    for selected in reversed(period_headings):
        period = period_for_heading(selected.group(1).strip())
        if not period:
            continue
        next_heading = next((match for match in period_headings if match.start() > selected.start()), None)
        end = next_heading.start() if next_heading else len(text)
        section_body = text[selected.end():end]
        chart_markers = list(dict.fromkeys(re.findall(r"\[CHART_\d+\]", section_body)))
        new_section = selected.group(0).rstrip() + "\n\n" + horizon_summary(period)
        if chart_markers:
            new_section += "\n\n" + "\n".join(chart_markers)
        text = text[:selected.start()] + new_section + "\n\n" + text[end:].lstrip("\n")
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    section_matches = list(re.finditer(r"(?m)^##[ \t]+(?!#)([^\n]+)\s*$", text))
    if not section_matches:
        return text

    prefix = text[:section_matches[0].start()].strip()
    sections: list[dict[str, Any]] = []
    for index, selected in enumerate(section_matches):
        end = section_matches[index + 1].start() if index + 1 < len(section_matches) else len(text)
        sections.append({
            "heading": selected.group(1).strip(),
            "body": text[selected.end():end].strip(),
            "index": index,
        })

    headings = [str(item["heading"]) for item in sections]
    has_executive = any(heading.casefold() == "executive summary" for heading in headings)
    has_quality = any(heading.casefold() == "data quality and integrity audit" for heading in headings)

    def section_key(heading: str) -> str:
        normalized = re.sub(r"[^a-z0-9]+", " ", heading.casefold()).strip()
        if normalized == "executive summary":
            return "executive"
        if normalized == "comparative performance analysis":
            return "comparative"
        if normalized == "key performance findings by category":
            return "category"
        if normalized == "strategy rankings by reported horizon":
            return "rankings"
        if normalized == "aum disclosure and coverage":
            return "aum"
        if normalized == "return distribution and risk limits":
            return "distribution"
        if normalized == "interpretation and scope":
            return "scope"
        if normalized == "methodology and derived calculations":
            return "methodology"
        if normalized == "data quality and integrity audit":
            return "quality"
        if normalized.startswith("quantitative comparisons from the uploaded") or normalized == "charts calculated from the attached data":
            return "chart_group"
        return ""

    canonical_sections: list[dict[str, Any]] = []
    first_by_key: dict[str, dict[str, Any]] = {}
    for section in sections:
        heading = str(section["heading"])
        folded = heading.casefold()
        if has_executive and re.search(r"\bmarket snapshot\b", folded):
            continue
        if has_quality and re.search(r"\bsource coverage\b", folded):
            continue
        key = section_key(heading)
        if key and key in first_by_key:
            markers = list(dict.fromkeys(re.findall(r"\[CHART_\d+\]", str(section["body"]))))
            if markers:
                existing = list(dict.fromkeys(re.findall(r"\[CHART_\d+\]", str(first_by_key[key]["body"]))))
                first_by_key[key]["body"] = (
                    str(first_by_key[key]["body"]).rstrip()
                    + "\n\n" + "\n".join(marker for marker in markers if marker not in existing)
                )
            continue
        if key:
            first_by_key[key] = section
        canonical_sections.append(section)

    order = {
        "executive": 0, "comparative": 10, "category": 20, "rankings": 30,
        "aum": 40, "distribution": 50, "scope": 60, "methodology": 70,
        "quality": 80, "chart_group": 90,
    }

    def section_order(section: dict[str, Any]) -> tuple[int, int]:
        heading = str(section["heading"]).casefold()
        key = section_key(str(section["heading"]))
        if key:
            return order[key], int(section["index"])
        if heading == "attached-data coverage":
            return 100, int(section["index"])
        if heading == "return-horizon coverage and distribution":
            return 110, int(section["index"])
        if heading == "aum disclosure":
            return 120, int(section["index"])
        if heading == "one-year category comparison":
            return 130, int(section["index"])
        if heading == "highest reported strategies by return horizon":
            return 140, int(section["index"])
        if re.match(r"^(?:sources?|references?|bibliography|works cited)\b", heading):
            return 200, int(section["index"])
        return 85, int(section["index"])

    canonical_sections.sort(key=section_order)
    rendered = [
        f"## {section['heading']}" + (f"\n\n{str(section['body']).strip()}" if str(section["body"]).strip() else "")
        for section in canonical_sections
    ]
    return "\n\n".join(([prefix] if prefix else []) + rendered).strip()


def _append_generic_analytics_tables(
    lines: list[str], datasets: list[dict[str, Any]], source_ref,
) -> None:
    summary_rows = [
        (dataset, summary)
        for dataset in datasets
        for summary in (dataset.get("numericSummaries") or [])
    ]
    if summary_rows:
        lines.extend([
            "",
            "## Numeric-field summary",
            "",
            "| Sheet / section | Source | Field | Observations | Missing | Minimum | Median | Mean | Maximum |",
            "|---|---|---|---:|---:|---:|---:|---:|---:|",
        ])
        for dataset, summary in summary_rows[:30]:
            unit = str(summary.get("unit") or "")
            def fmt(key: str) -> str:
                value = summary.get(key)
                return "—" if value is None else f"{float(value):,.4g}{unit}"
            lines.append(
                f"| {_md_cell(dataset.get('sheet'))} | {source_ref(dataset.get('filename'))} | {_md_cell(summary.get('name'))} | "
                f"{int(summary.get('count') or 0)} | {int(summary.get('missing') or 0)} | "
                f"{fmt('min')} | {fmt('median')} | {fmt('mean')} | {fmt('max')} |"
            )
        if len(summary_rows) > 30:
            lines.append(f"\n_{len(summary_rows) - 30} additional numeric fields are summarized in the attached data and RAG index._")

    category_rows = [
        (dataset, summary)
        for dataset in datasets
        for summary in (dataset.get("categorySummaries") or [])
    ]
    if category_rows:
        lines.extend([
            "",
            "## Categorical distributions",
            "",
            "| Sheet / section | Source | Field | Distinct values | Most common values (count) |",
            "|---|---|---|---:|---|",
        ])
        for dataset, summary in category_rows[:24]:
            values = ", ".join(
                f"{item.get('value')} ({int(item.get('count') or 0)})"
                for item in (summary.get("topValues") or [])[:8]
            )
            lines.append(
                f"| {_md_cell(dataset.get('sheet'))} | {source_ref(dataset.get('filename'))} | {_md_cell(summary.get('name'))} | "
                f"{int(summary.get('uniqueCount') or 0)} | {_md_cell(values)} |"
            )


def _remove_redundant_source_columns(lines: list[str]) -> list[str]:
    """Drop redundant source-ID columns from one-file report tables.

    The Sources section still resolves each in-body citation to the attachment.
    """
    output: list[str] = []
    index = 0
    while index < len(lines):
        current = lines[index]
        if (
            index + 1 >= len(lines)
            or not current.strip().startswith("|")
            or not current.strip().endswith("|")
            or not lines[index + 1].strip().startswith("|")
        ):
            output.append(current)
            index += 1
            continue
        header = [cell.strip().casefold() for cell in current.strip().strip("|").split("|")]
        separator = [cell.strip().replace(" ", "") for cell in lines[index + 1].strip().strip("|").split("|")]
        if len(header) != len(separator) or not all(re.fullmatch(r":?-{3,}:?", cell) for cell in separator):
            output.append(current)
            index += 1
            continue
        source_indices = [i for i, cell in enumerate(header) if cell in {"source", "sources", "source(s)"}]
        if not source_indices:
            output.extend([current, lines[index + 1]])
            index += 2
            continue

        cursor = index
        while cursor < len(lines):
            row = lines[cursor].strip()
            if not row.startswith("|") or not row.endswith("|"):
                break
            cells = [cell.strip() for cell in row.strip("|").split("|")]
            if len(cells) == len(header):
                output.append("| " + " | ".join(cell for i, cell in enumerate(cells) if i not in source_indices) + " |")
            else:
                output.append(lines[cursor])
            cursor += 1
        index = cursor
    return output


def _extraction_notice(analytics: dict[str, Any]) -> list[str]:
    notes = [item for item in (analytics or {}).get("extractionNotes") or []
             if isinstance(item, dict) and item.get("note")]
    if not notes:
        return []
    detail = " ".join(f"{_md_cell(item.get('filename') or 'Attachment')}: {_md_cell(item['note'])}" for item in notes)
    return [
        "> **Partial attachment:** the file was larger than the extraction limit, so every count, total, average "
        f"and ranking below describes only the extracted rows, not the whole file. {detail}",
        "",
    ]


def attachment_analytics_section(
    analytics: dict[str, Any],
    source_id: str = "",
    source_ids_by_file: dict[str, str] | None = None,
) -> str:
    """Render auditable coverage, summary, and ranked tables for attached data."""
    kind = str((analytics or {}).get("kind") or "")
    if kind not in {"pms_strategy_performance", "generic_tabular_data"}:
        return ""
    source_lookup = {
        str(name).casefold(): str(value or "").strip().upper()
        for name, value in (source_ids_by_file or {}).items() if value
    }

    def source_ref(filename: object = "") -> str:
        value = source_lookup.get(str(filename or "").casefold()) or str(source_id or "").strip().upper()
        return f"[{_md_cell(value)}]" if value else "—"

    source_file_names = [
        str(item.get("filename") or "")
        for key in ("files", "additionalFiles")
        for item in (analytics.get(key) or []) if isinstance(item, dict)
    ]
    source_file_names.extend(
        str(item.get("filename") or "")
        for item in (analytics.get("datasets") or []) if isinstance(item, dict)
    )
    unique_source_files = {name.casefold() for name in source_file_names if name}
    aggregate_source_refs = list(dict.fromkeys(
        ref for ref in (source_ref(filename) for filename in source_file_names) if ref != "—"
    ))
    if not aggregate_source_refs and source_id:
        aggregate_source_refs = [source_ref()]
    aggregate_sources = ", ".join(aggregate_source_refs) or "—"
    source_note = (
        " In-body citations resolve to the attached file listed in the Sources section."
        if len(unique_source_files) == 1 and source_lookup
        else (
            " Source IDs map to the files in this inventory and the Sources appendix."
            if source_lookup else (f" Source: [{source_id}]." if source_id else "")
        )
    )
    lines = [
        "## Attached-data coverage",
        "",
        f"The inventory and summary statistics below are calculated from the attached source rows; missing values remain missing.{source_note}",
        "",
        *_extraction_notice(analytics),
        "| File | Source | Sheet / section | Data rows analyzed | Fields | Missing observations |",
        "|---|---|---|---:|---:|---:|",
    ]
    datasets = list((analytics or {}).get("datasets") or [])
    if kind == "pms_strategy_performance":
        for file_info in analytics.get("files") or []:
            if not isinstance(file_info, dict):
                continue
            for sheet in file_info.get("sheets") or []:
                lines.append(
                    f"| {_md_cell(file_info.get('filename'))} | {source_ref(file_info.get('filename'))} | "
                    f"{_md_cell(sheet.get('name'))} | {int(sheet.get('rowCount') or 0)} | "
                    f"{int(sheet.get('columnCount') or 0)} | {int(sheet.get('missingReturnValues') or 0)} |"
                )
        for dataset in datasets:
            lines.append(
                f"| {_md_cell(dataset.get('filename'))} | {source_ref(dataset.get('filename'))} | "
                f"{_md_cell(dataset.get('sheet'))} | {int(dataset.get('rowCount') or 0)} | "
                f"{int(dataset.get('columnCount') or 0)} | {int(dataset.get('missingCells') or 0)} |"
            )
    else:
        for dataset in datasets:
            lines.append(
                f"| {_md_cell(dataset.get('filename'))} | {source_ref(dataset.get('filename'))} | "
                f"{_md_cell(dataset.get('sheet'))} | {int(dataset.get('rowCount') or 0)} | "
                f"{int(dataset.get('columnCount') or 0)} | {int(dataset.get('missingCells') or 0)} |"
            )
    if kind == "pms_strategy_performance" and analytics.get("periodSummaries"):
        lines.extend([
            "",
            "## Return-horizon coverage and distribution",
            "",
            "The horizons are separate lookback columns in one dated snapshot, not sequential observations. Values are shown as reported; missing values are excluded and coverage differs by horizon.",
            "",
            "| Horizon | Source(s) | Records with values | Coverage | Mean return | Median return | Range |",
            "|---|---|---:|---:|---:|---:|---:|",
        ])
        for item in analytics["periodSummaries"]:
            lines.append(
                f"| {_md_cell(item.get('period'))} | {_md_cell(aggregate_sources)} | "
                f"{int(item.get('available') or 0)} | {float(item.get('coverage') or 0):.1%} | "
                f"{float(item.get('mean') or 0):.2f}% | {float(item.get('median') or 0):.2f}% | "
                f"{float(item.get('min') or 0):.2f}% to {float(item.get('max') or 0):.2f}% |"
            )
        _strategies = [s for s in (analytics.get("strategies") or []) if isinstance(s, dict)]
        if _strategies:
            # AUM disclosure is a common question about these files; give the counted figure so the writer
            # does not estimate it (a live report stated 237 undisclosed where the workbook holds 287).
            _aums = sorted(float(s["aum"]) for s in _strategies if isinstance(s.get("aum"), (int, float)))
            _undisclosed = len(_strategies) - len(_aums)
            lines.extend([
                "",
                "## AUM disclosure",
                "",
                "| Measure | Source(s) | Value |",
                "|---|---|---:|",
                f"| Strategies analysed | {_md_cell(aggregate_sources)} | {len(_strategies)} |",
                f"| Strategies reporting AUM | {_md_cell(aggregate_sources)} | {len(_aums)} |",
                f"| Strategies with AUM not disclosed | {_md_cell(aggregate_sources)} | {_undisclosed} "
                f"({_undisclosed / len(_strategies):.1%}) |",
            ])
            if _aums:
                _mid = len(_aums) // 2
                _median = _aums[_mid] if len(_aums) % 2 else (_aums[_mid - 1] + _aums[_mid]) / 2
                lines.extend([
                    f"| Total reported AUM (₹ crore) | {_md_cell(aggregate_sources)} | {sum(_aums):,.2f} |",
                    f"| Median reported AUM (₹ crore) | {_md_cell(aggregate_sources)} | {_median:,.2f} |",
                ])
        if analytics.get("categorySummaries"):
            lines.extend([
                "",
                "## One-year category comparison",
                "",
                "| Category | Source(s) | Strategies with values | Mean 1-year return | Median 1-year return |",
                "|---|---|---:|---:|---:|",
            ])
            # A category average over one or two strategies is not comparable with one over hundreds:
            # categories with enough strategies are ranked; small ones follow, labelled, unranked.
            _ranked = [i for i in analytics["categorySummaries"] if int(i.get("available") or 0) >= MIN_CATEGORY_SIZE]
            _small = [i for i in analytics["categorySummaries"] if int(i.get("available") or 0) < MIN_CATEGORY_SIZE]
            for item in (_ranked + _small)[:15]:
                small = int(item.get("available") or 0) < MIN_CATEGORY_SIZE
                lines.append(
                    f"| {_md_cell(item.get('category'))}{' (too few to rank)' if small else ''} | {_md_cell(aggregate_sources)} | "
                    f"{int(item.get('available') or 0)} | {float(item.get('mean1Year') or 0):.2f}% | "
                    f"{float(item.get('median1Year') or 0):.2f}% |"
                )
            if _small:
                lines.extend(["", f"Categories with fewer than {MIN_CATEGORY_SIZE} strategies are listed after the ranked "
                                  "categories: their averages describe one or two strategies, not the category."])

        ranked_charts = [
            chart for chart in (analytics.get("charts") or [])
            if isinstance(chart, dict) and chart.get("chartRole") == "strategy_ranking"
        ]
        if ranked_charts:
            lines.extend([
                "",
                "## Highest reported strategies by return horizon",
                "",
                "The table lists the five highest reported observations, and charts show up to ten, for selected short-, medium-, and long-horizon windows. This is a ranked slice, not the full strategy universe or a forecast.",
                "",
                "| Horizon | Rank | Source | Worksheet | AMC / strategy | Reported return |",
                "|---|---:|---|---|---|---:|",
            ])
            for chart in ranked_charts:
                period = str(chart.get("workbookMetric") or "")
                points = [
                    point for series in (chart.get("series") or []) if isinstance(series, dict)
                    for point in (series.get("data") or []) if isinstance(point, dict)
                ]
                try:
                    points.sort(key=lambda point: float(point.get("value")), reverse=True)
                except (TypeError, ValueError):
                    points = [point for point in points if _parse_num(point.get("value")) is not None]
                    points.sort(key=lambda point: float(point.get("value") or 0), reverse=True)
                for rank, point in enumerate(points[:5], start=1):
                    try:
                        value = float(point.get("value"))
                    except (TypeError, ValueError):
                        continue
                    lines.append(
                        f"| {_md_cell(period)} | {rank} | {_md_cell(source_ref(point.get('sourceFile')))} | "
                        f"{_md_cell(point.get('sourceSheet'))} | {_md_cell(point.get('label'))} | {value:.2f}% |"
                    )
        _append_generic_analytics_tables(lines, datasets, source_ref)
    else:
        _append_generic_analytics_tables(lines, datasets, source_ref)
    if kind == "pms_strategy_performance":
        quality = analytics.get("dataQuality") if isinstance(analytics.get("dataQuality"), dict) else {}
        field_presence = quality.get("fieldPresence") if isinstance(quality.get("fieldPresence"), dict) else {}
        if field_presence.get("aum"):
            field_map = analytics.get("fieldMap") if isinstance(analytics.get("fieldMap"), dict) else {}
            aum_labels = field_map.get("aum") or []
            if isinstance(aum_labels, str):
                aum_labels = [aum_labels]
            aum_labels = list(dict.fromkeys(str(label).strip() for label in aum_labels if str(label).strip()))
            if len(aum_labels) == 1:
                for index, line in enumerate(lines):
                    if line.startswith("| Total reported AUM ") or line.startswith("| Median reported AUM "):
                        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
                        if len(cells) == 3:
                            measure = "Total" if line.startswith("| Total reported AUM ") else "Median"
                            cells[0] = f"{measure} reported {_md_cell(aum_labels[0])}"
                            lines[index] = "| " + " | ".join(cells) + " |"
            elif len(aum_labels) > 1:
                lines = [
                    line for line in lines
                    if not line.startswith("| Total reported AUM ") and not line.startswith("| Median reported AUM ")
                ]
                lines.append(
                    "Combined AUM totals and medians are omitted because detected tables use different AUM headers: "
                    + ", ".join(aum_labels) + "."
                )
        else:
            start = next((i for i, line in enumerate(lines) if line.strip() == "## AUM disclosure"), None)
            if start is not None:
                end = next((i for i in range(start + 1, len(lines)) if lines[i].strip().startswith("## ")), len(lines))
                lines[start:end] = [
                    "## AUM disclosure", "",
                    "No AUM column was recognized, so AUM disclosure and AUM-weighted comparisons cannot be assessed from this attachment.",
                ]
    if len(unique_source_files) == 1:
        lines = _remove_redundant_source_columns(lines)
    return "\n".join(lines).strip()


_ATTACHMENT_ANALYTICS_HEADINGS = (
    "Attached-data coverage",
    "Return-horizon coverage and distribution",
    "One-year category comparison",
    "Highest reported strategies by return horizon",
    "Numeric-field summary",
    "Categorical distributions",
)


def append_attachment_analytics_section(
    report_text: str,
    analytics: dict[str, Any],
    source_id: str = "",
    source_ids_by_file: dict[str, str] | None = None,
) -> tuple[str, bool]:
    """Replace stripped or model-authored attachment tables with computed tables."""
    section = attachment_analytics_section(analytics, source_id, source_ids_by_file)
    if not section:
        return str(report_text or ""), False
    text = str(report_text or "")
    for heading in _ATTACHMENT_ANALYTICS_HEADINGS:
        pattern = re.compile(
            rf"(?ims)^[ \t]*##[ \t]+{re.escape(heading)}[ \t]*\n.*?(?=^[ \t]*##[ \t]+|\Z)"
        )
        text = pattern.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text).rstrip()
    return f"{text}\n\n{section}\n" if text else f"{section}\n", True


def ensure_server_workbook_chart_markers(
    report_text: str,
    charts: list[dict] | None,
) -> tuple[str, dict[str, Any]]:
    """Keep verified attachment charts visible beside the analysis they support."""
    text = str(report_text or "").replace("\r\n", "\n").replace("\r", "\n").rstrip()
    chart_rows = [
        (index, chart) for index, chart in enumerate(charts or [], start=1)
        if isinstance(chart, dict)
        and chart.get("_serverWorkbookEvidence") is True
        and str(chart.get("sourceOrigin") or "").strip().casefold() in {"uploaded_workbook", "uploaded_attachment"}
    ]
    if not chart_rows:
        return text, {"restored": 0, "relocated": 0, "titles": [], "relocatedTitles": []}

    from utils.chart_gallery import chart_horizons, explicit_horizons

    heading_re = re.compile(r"(?m)^(#{1,4})[ \t]+(.+?)[ \t]*$")
    marker_re = re.compile(r"(?m)^[ \t]*\[CHART_(\d+)\][ \t]*$")

    def heading_chain(position: int) -> str:
        stack: list[tuple[int, str]] = []
        for match in heading_re.finditer(text, 0, position):
            level, title = len(match.group(1)), match.group(2).strip()
            stack = [item for item in stack if item[0] < level]
            stack.append((level, title))
        return " / ".join(title for _level, title in stack)

    def mismatched_horizon(chart: dict, heading: str) -> bool:
        plotted = chart_horizons(chart)
        segments = [part.strip() for part in heading.split(" / ") if part.strip()]
        specified = [explicit_horizons(part) for part in segments]
        specified = [periods for periods in specified if periods]
        all_periods = set().union(*specified) if specified else set()
        if not plotted or not all_periods:
            return False
        if not (plotted & all_periods):
            return True
        return any(len(periods) == 1 and plotted != periods for periods in specified)

    def target_heading_matches(chart: dict, heading: str) -> bool:
        role = str(chart.get("chartRole") or "").strip().casefold()
        words = _chart_words(heading)
        if not words or re.search(r"\b(?:sources?|references?|bibliography|works cited)\b", words):
            return False
        if role == "strategy_ranking":
            if not ("strategy" in words or "strategies" in words or "ranking" in words):
                return False
            if "aum" in words or "coverage" in words or "quality" in words:
                return False
            return not mismatched_horizon(chart, heading)
        if role == "aum_disclosure":
            return ("aum" in words or "assets under management" in words) and not any(
                word in words for word in ("methodology", "quality", "integrity")
            )
        if role == "benchmark_comparison":
            return (
                ("benchmark" in words and "category" not in words)
                or "comparative" in words or "cross horizon" in words
            )
        if role == "return_distribution":
            return any(word in words for word in (
                "distribution", "volatility", "dispersion", "risk return", "comparative", "cross horizon",
            ))
        if role == "snapshot_summary":
            topic = _chart_topic(chart)
            if topic == "category":
                return "category" in words and "coverage" not in words
            if topic == "return_horizon":
                return any(word in words for word in ("comparative", "cross horizon", "distribution"))
        return False

    def target_section(chart: dict) -> tuple[int, int] | None:
        matches = list(heading_re.finditer(text))
        candidates: list[tuple[int, int, int, int]] = []
        for position, heading_match in enumerate(matches):
            title = heading_match.group(2).strip()
            if not target_heading_matches(chart, title):
                continue
            level = len(heading_match.group(1))
            end = len(text)
            for following in matches[position + 1:]:
                if len(following.group(1)) <= level:
                    end = following.start()
                    break
            # Prefer a specific subheading (for example, a one-year ranking)
            # over a generic parent section, then keep the first matching body
            # section rather than placing the chart in a repeated appendix.
            specific = 1 if explicit_horizons(title) else 0
            candidates.append((specific, -heading_match.start(), heading_match.end(), end))
        if not candidates:
            return None
        candidates.sort(reverse=True)
        _specific, _position, start, end = candidates[0]
        return start, end

    semantic_roles = {
        "strategy_ranking", "aum_disclosure", "benchmark_comparison",
        "return_distribution", "snapshot_summary",
    }

    def marker_matches_role(chart: dict, match: re.Match) -> bool:
        chain = heading_chain(match.start())
        if mismatched_horizon(chart, chain):
            return False
        role = str(chart.get("chartRole") or "").strip().casefold()
        if role not in semantic_roles:
            return True
        leaf_heading = chain.split(" / ")[-1] if chain else ""
        if target_heading_matches(chart, leaf_heading):
            return True
        words = _chart_words(chain)
        return (
            "charts calculated from the attached data" in words
            or "quantitative comparisons from the uploaded files" in words
            or "quantitative comparisons from the uploaded workbook" in words
        )

    marker_matches: dict[int, list[re.Match]] = {}
    for match in marker_re.finditer(text):
        marker_matches.setdefault(int(match.group(1)), []).append(match)

    missing: list[tuple[int, dict]] = []
    relocated: list[tuple[int, dict]] = []
    remove_spans: list[tuple[int, int]] = []
    for index, chart in chart_rows:
        matches = marker_matches.get(index, [])
        if not matches:
            missing.append((index, chart))
            continue
        compatible = next((match for match in matches if marker_matches_role(chart, match)), None)
        if compatible is None:
            relocated.append((index, chart))
            remove_spans.extend(match.span() for match in matches)
        else:
            remove_spans.extend(match.span() for match in matches if match is not compatible)

    for start, end in sorted(remove_spans, reverse=True):
        text = text[:start] + text[end:]
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    def add_chart(index: int, chart: dict) -> None:
        nonlocal text
        marker = f"[CHART_{index}]"
        target = target_section(chart)
        if target:
            _start, end = target
            before, after = text[:end].rstrip(), text[end:].lstrip()
            text = f"{before}\n\n{marker}" + (f"\n\n{after}" if after else "")
            return

        group = re.search(
            r"(?im)^##[ \t]+(?:Charts calculated from the attached data|"
            r"Quantitative comparisons from the uploaded (?:files|workbook))[ \t]*$",
            text,
        )
        if group:
            following = re.search(r"(?m)^##[ \t]+", text[group.end():])
            group_end = group.end() + following.start() if following else len(text)
            group_body = text[group.end():group_end]
            if marker not in group_body:
                text = text[:group_end].rstrip() + f"\n\n{marker}\n\n" + text[group_end:].lstrip()
                return
            return

        block = "## Charts calculated from the attached data\n\nThe charts below use verified values calculated from the uploaded rows.\n\n" + marker
        source_heading = re.search(
            r"(?im)^[ \t]{0,3}#{1,3}[ \t]+(?:sources?|references?|bibliography|works cited)\b[^\r\n]*$",
            text,
        )
        if source_heading:
            insert_at = source_heading.start()
            text = text[:insert_at].rstrip() + "\n\n" + block + "\n\n" + text[insert_at:].lstrip()
        else:
            text = f"{text}\n\n{block}" if text else block

    for index, chart in [*missing, *relocated]:
        add_chart(index, chart)

    # These headings are generated solely to hold attachment-chart markers.
    # Once every chart has a relevant section, remove the now-empty group so it
    # cannot leave a blank-looking appendix or a second chart listing.
    fallback_heading = re.compile(
        r"(?im)^##[ \t]+(?:Charts calculated from the attached data|"
        r"Quantitative comparisons from the uploaded (?:files|workbook))[ \t]*$"
    )
    fallback_matches = list(fallback_heading.finditer(text))
    for position, match in reversed(list(enumerate(fallback_matches))):
        next_heading = re.search(r"(?m)^##[ \t]+", text[match.end():])
        end = match.end() + next_heading.start() if next_heading else len(text)
        block = text[match.start():end]
        if marker_re.search(block):
            continue
        text = text[:match.start()] + text[end:]
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text, {
        "restored": len(missing), "relocated": len(relocated),
        "titles": [str(chart.get("title") or "")[:120] for _, chart in [*missing, *relocated]],
        "relocatedTitles": [str(chart.get("title") or "")[:120] for _, chart in relocated],
    }


def _md_cell(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "—")).replace("|", "\\|").strip()


def assign_workbook_source(
    charts: list[dict],
    source_id: str,
    source_ids_by_file: dict[str, str] | None = None,
) -> list[dict]:
    """Attach canonical source IDs to deterministic charts, per source file when available."""
    sid = str(source_id or "").strip().upper()
    source_lookup = {
        str(name).casefold(): str(value or "").strip().upper()
        for name, value in (source_ids_by_file or {}).items() if value
    }
    out = []
    for chart in charts or []:
        item = dict(chart)
        series_out = []
        chart_source_ids: list[str] = []
        for series in item.get("series") or []:
            s = dict(series)
            points = []
            for point in s.get("data") or []:
                p = dict(point)
                point_file = str(p.get("sourceFile") or "").casefold()
                point_sid = source_lookup.get(point_file) or sid
                if point_sid:
                    p["evidenceSource"] = point_sid
                    if point_sid not in chart_source_ids:
                        chart_source_ids.append(point_sid)
                points.append(p)
            s["data"] = points
            series_out.append(s)
        if not chart_source_ids:
            for source_file in item.get("sourceFiles") or [item.get("sourceFile")]:
                source_sid = source_lookup.get(str(source_file or "").casefold())
                if source_sid and source_sid not in chart_source_ids:
                    chart_source_ids.append(source_sid)
        if not chart_source_ids and sid:
            chart_source_ids = [sid]
        item["sourceIds"] = chart_source_ids
        item["source"] = chart_source_ids
        item["series"] = series_out
        out.append(item)
    return out


def workbook_charts_section(
    charts: list[dict],
    start_index: int = 1,
    source_label: str = "uploaded workbook",
) -> str:
    """Create concise in-body placeholders for deterministic attachment charts."""
    if not charts:
        return ""
    if source_label == "uploaded workbook":
        intro = (
            "The charts below are calculated from the workbook's reported values as of its stated date. "
            "Missing or undisclosed values are excluded, not treated as zero. Rankings reflect the reported periods and are not forecasts."
        )
    else:
        intro = (
            "The charts below are calculated from the attached source rows. "
            "Missing values are excluded rather than treated as zero."
        )
    lines = [f"## Quantitative comparisons from the {source_label}", "", intro, ""]
    for offset, chart in enumerate(charts, start=0):
        marker = f"[CHART_{start_index + offset}]"
        if chart.get("takeaway") or chart.get("rationale"):
            # The rendered chart panel already contains its title, context and
            # takeaway. Repeating them in Markdown strands captions when the
            # complete chart panel moves to the next PDF page.
            lines.extend([marker, ""])
        else:
            lines.extend([
                f"### {chart.get('title', 'Workbook comparison')}",
                "This chart compares the available reported observations.",
                marker, "",
            ])
    return "\n".join(lines).strip()
