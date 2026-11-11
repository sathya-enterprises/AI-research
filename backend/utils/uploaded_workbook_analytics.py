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
_NA = {"", "na", "n/a", "nd", "n.d.", "--", "—", "-", "undisc.", "undisclosed", "not disclosed", "not available"}
_FILE_MARKER = re.compile(r"(?m)^\[File:\s*(.*?)\]\s*$")
_SHEET_MARKER = re.compile(r"(?m)^\[Sheet:\s*(.*?)\]\s*$")


def _norm(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


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
    for header_index, row in enumerate(rows):
        normalized = [_norm(cell).replace("_", " ") for cell in row]
        joined = " ".join(normalized)
        required = ("amc", "investment approach", "benchmark", "aum", "category", "returns")
        if not all(term in joined for term in required):
            continue
        period_index = None
        period_columns: dict[str, int] = {}
        for candidate_index in range(header_index + 1, min(len(rows), header_index + 4)):
            for column, value in enumerate(rows[candidate_index]):
                alias = _RETURN_ALIASES.get(_norm(value))
                if alias:
                    period_columns[alias] = column
            if len(period_columns) >= 4:
                period_index = candidate_index
                break
        if period_index is None:
            # Some workbook exports flatten the two header rows.
            for column, value in enumerate(row):
                alias = _RETURN_ALIASES.get(_norm(value))
                if alias:
                    period_columns[alias] = column
            period_index = header_index
        indices = {}
        for idx, value in enumerate(row):
            key = _norm(value).replace("_", " ")
            if key in {"amc", "asset management company"}:
                indices["amc"] = idx
            elif "investment approach" in key or key == "strategy":
                indices["approach"] = idx
            elif "benchmark" in key:
                indices["benchmark"] = idx
            elif key.startswith("aum") or "assets under management" in key:
                indices["aum"] = idx
            elif key == "category":
                indices["category"] = idx
        if all(key in indices for key in ("amc", "approach", "benchmark", "aum", "category")) and period_columns:
            return header_index, period_index, {**indices, **{f"return:{period}": col for period, col in period_columns.items()}}
    return None


def build_pms_workbook_analytics(file_context: str) -> dict[str, Any]:
    """Parse PMS strategy tables and return measured rows, chart specs and source excerpts."""
    files_result: list[dict[str, Any]] = []
    merged: OrderedDict[tuple[str, str, str], dict[str, Any]] = OrderedDict()
    for filename, file_body in _split_file_blocks(file_context):
        sheet_records: list[dict[str, Any]] = []
        all_rows_for_evidence: list[str] = []
        workbook_date = ""
        for sheet_name, sheet_body in _sheet_blocks(file_body):
            rows, physical = _csv_rows(sheet_body)
            if not rows:
                continue
            if not workbook_date:
                for row in rows[:3]:
                    match = re.search(r"(?i)AS\s+ON\s+(.+)", " ".join(row))
                    if match:
                        workbook_date = match.group(1).strip()
                        break
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
            if header_line:
                all_rows_for_evidence.append(f"[Sheet: {sheet_name}] {title} | {header_line} | {period_line}")
            for row_index in range(period_index + 1, len(rows)):
                row = rows[row_index]
                def cell(key: str) -> str:
                    return row[indices[key]].strip() if indices.get(key, 10**6) < len(row) else ""
                amc, approach = cell("amc"), cell("approach")
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
                    # Excel exports commonly expose percentage returns as decimal fractions
                    # (0.248 = 24.8%). Some exports instead serialize the displayed percentage
                    # ("24.8%"). Do not multiply the latter a second time.
                    period_values[period] = number if "%" in raw_value else number * 100.0
                    period_raw[period] = raw_value
                aum_raw = cell("aum")
                aum = _parse_num(aum_raw)
                raw_line = physical[row_index] if row_index < len(physical) else ",".join(row)
                record = {
                    "filename": filename, "sheet": sheet_name, "rowNumber": row_index + 1,
                    "tableTitle": title, "asOf": workbook_date, "amc": amc, "approach": approach,
                    "benchmark": cell("benchmark"), "category": cell("category"),
                    "aum": aum, "aumRaw": aum_raw, "periodValues": period_values,
                    "periodRaw": period_raw, "rawLine": raw_line,
                    "headerLine": header_line, "periodLine": period_line,
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
                    # Category tables are the base. Top-performance tables can fill gaps,
                    # but duplicate sheets never overwrite a real observation silently.
                    for period, value in period_values.items():
                        if period not in prior["periodValues"]:
                            prior["periodValues"][period] = value
                            prior["periodRaw"][period] = period_raw[period]
                            prior.setdefault("periodEvidence", {})[period] = record
                    if prior.get("aum") is None and aum is not None:
                        prior["aum"], prior["aumRaw"] = aum, aum_raw
        files_result.append({"filename": filename, "rows": sheet_records, "asOf": workbook_date,
                             "evidenceHeaders": all_rows_for_evidence})

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
    chart_defs = [
        (period, f"Top PMS Strategies by {period_labels[period]} Return",
         f"Reported {period_labels[period].lower()} return")
        for period in _RETURN_PERIODS if period in period_labels
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
                    f"{item['amc']} — {item['approach']} ({item['filename']})"
                    if multiple_filenames else f"{item['amc']} — {item['approach']}"
                ),
                "value": round(float(item["periodValues"][period]), 4),
                "evidenceSource": "",  # assigned once the canonical source registry exists
                "evidenceQuote": quote,
                "evidenceValue": raw_value,
                "sourceSheet": sheet_name, "sourceRow": row_number, "sourceFile": source_row.get("filename"),
            })
        charts.append({
            "type": "horizontal_bar", "title": chart_title,
            "xLabel": "PMS strategy", "yLabel": f"{period} return (%)", "unit": "%",
            "series": [{"name": series_name, "data": points}],
            "sourceIds": [], "sourceOrigin": "uploaded_workbook", "workbookMetric": period,
            "sourceFiles": sorted({str(item.get("filename") or "") for item in observations}),
            "rationale": f"Ranked comparison of actual {period} return values from the uploaded workbook.",
            "takeaway": f"Shows up to ten highest reported {period} returns from the available workbook tables; missing values are excluded. This is a ranked slice, not the full universe.",
        })

    evidence_by_file: dict[str, str] = {}
    for filename, lines in used_rows.items():
        file_data = source_items.get(filename)
        header_lines = file_data.get("evidenceHeaders", []) if file_data else []
        # Include the headers and raw rows supporting plotted observations, plus
        # a transparent note of deterministic workbook-wide calculations used
        # in KPI cards. The note is derived from workbook values, not external data.
        deduped = list(OrderedDict((str(line), None) for line in [*header_lines, *lines]).keys())
        evidence_by_file[filename] = "\n".join(deduped)[:16000]

    stats = []
    one_year = [x for x in strategies if "1 Yr" in x.get("periodValues", {})]
    if one_year:
        leader = max(one_year, key=lambda x: x["periodValues"]["1 Yr"])
        stats.append({"label": "Strategies with 1-year return data", "value": str(len(one_year))})
        stats.append({"label": "Highest reported 1-year return", "value": f"{leader['periodValues']['1 Yr']:.2f}%"})
    if strategies:
        stats.append({"label": "Distinct AMC / strategy combinations", "value": str(len(strategies))})
    # Ensure derived KPIs can be audited without pretending the derived count is
    # itself a literal cell in the workbook. The excerpt explicitly labels the
    # calculation and retains the raw worksheet rows that support chart points.
    stats_note = "\n[Deterministic calculations from the uploaded workbook, not literal worksheet cells] " + "; ".join(
        f"{item['label']} = {item['value']}" for item in stats
    ) if stats else ""
    if stats_note:
        for filename_key in list(evidence_by_file):
            evidence_by_file[filename_key] = (evidence_by_file[filename_key] + "\n" + stats_note)[:16000]
    period_coverage = {
        period: sum(1 for strategy in strategies if period in (strategy.get("periodValues") or {}))
        for period in _RETURN_PERIODS
    }
    return {"kind": "pms_strategy_performance", "files": files_result,
            "strategies": strategies, "charts": charts, "keyStats": stats,
            "periodCoverage": period_coverage,
            "evidenceByFile": evidence_by_file}


_DATA_NUMBER_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$", re.I)
_PERCENT_HEADER_RE = re.compile(r"(?:%|percent|percentage|pct|return|yield|margin|rate)", re.I)
_DATE_HEADER_RE = re.compile(r"\b(?:date|time|month|quarter|year|week|period)\b", re.I)
_LABEL_HEADER_RE = re.compile(
    r"\b(?:name|company|amc|strategy|product|region|country|category|segment|ticker|sector|"
    r"customer|account|portfolio|fund|state|city|channel|type|group)\b",
    re.I,
)
_IDENTIFIER_HEADER_RE = re.compile(r"\b(?:id|uuid|serial|index|row number|record number)\b", re.I)


def _parse_data_number(value: object) -> tuple[float, bool] | None:
    """Parse ordinary numeric cells while retaining whether the cell had a percent sign."""
    raw = str(value or "").strip().replace("\u2212", "-")
    if _norm(raw) in _NA:
        return None
    negative_parentheses = raw.startswith("(") and raw.endswith(")")
    raw = raw.strip("()").replace(",", "").replace(" ", "")
    percent = "%" in raw
    raw = raw.replace("%", "")
    # Strip common currency symbols and unit prefixes/suffixes without
    # interpreting dates, ratios such as 3/4, or arbitrary text as numbers.
    raw = re.sub(r"^[^\d.+-]+", "", raw)
    raw = re.sub(r"[^\d.eE+-]+$", "", raw)
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


def build_attachment_analytics(file_context: str, *, max_charts: int = 12) -> dict[str, Any]:
    """Build deterministic workbook/CSV summaries and charts from attached text."""
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
            sheets.append({
                "name": sheet_name,
                "rowCount": len(sheet_records),
                "sourceRows": max(0, len(sheet_rows) - 4),
                "columnCount": max((len(row) for row in sheet_rows), default=0),
                "missingReturnValues": max(0, len(sheet_records) * len(_RETURN_PERIODS) - available_returns),
            })
        file_info["sheets"] = sheets

    strategies = pms.get("strategies") or []
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
                "min": min(values), "max": max(values),
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
    aggregate_source_refs = list(dict.fromkeys(
        ref for ref in (source_ref(filename) for filename in source_file_names) if ref != "—"
    ))
    if not aggregate_source_refs and source_id:
        aggregate_source_refs = [source_ref()]
    aggregate_sources = ", ".join(aggregate_source_refs) or "—"
    source_note = " Source markers are listed per file." if source_lookup else (f" Source: [{source_id}]." if source_id else "")
    lines = [
        "## Attached-data coverage",
        "",
        f"The inventory and summary statistics below are calculated from the attached source rows; missing values remain missing.{source_note}",
        "",
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
            if isinstance(chart, dict) and chart.get("workbookMetric")
        ]
        if ranked_charts:
            lines.extend([
                "",
                "## Highest reported strategies by return horizon",
                "",
                "The table lists the five highest reported observations in each horizon; the charts show up to ten. These are ranked workbook values, not forecasts.",
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
        lines.extend([f"### {chart.get('title', 'Workbook comparison')}", f"[CHART_{start_index + offset}]",
                      str(chart.get("takeaway") or "This chart compares the available reported observations."), ""])
    return "\n".join(lines).strip()
