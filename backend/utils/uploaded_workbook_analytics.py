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
_NA = {"", "na", "n/a", "nd", "n.d.", "--", "—", "-", "undisc.", "undisclosed", "not disclosed", "not available"}
_FILE_MARKER = re.compile(r"(?m)^\[File:\s*(.*?)\]\s*$")
_SHEET_MARKER = re.compile(r"(?m)^\[Sheet:\s*(.*?)\]\s*$")


def _norm(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def is_file_only_workbook_report(question: str, file_context: str, source_documents: object = None,
                                 has_rag: bool = False) -> bool:
    """Identify explicit workbook-first requests, unless the user asks for web research."""
    text = str(question or "").casefold()
    has_file = bool(str(file_context or "").strip()) or bool(source_documents) or bool(has_rag)
    if not has_file:
        return False
    external_override = re.search(
        r"\b(?:search\s+(?:the\s+)?web|web\s+research|external\s+sources?|latest\s+(?:market|news|industry)|"
        r"current\s+(?:market|share\s+price|stock\s+price)|include\s+outside\s+research|"
        r"supplement\s+with\s+(?:web|external)|research\s+from\s+the\s+internet)\b", text, re.I,
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
    workbook_language = re.search(
        r"\b(?:uploaded\s+(?:excel\s+)?(?:file|workbook|spreadsheet|document)|attached\s+(?:excel\s+)?(?:file|workbook|spreadsheet)|"
        r"all\s+relevant\s+worksheets|source\s+worksheet|workbook\s+only|uploaded\s+document\s+only|"
        r"using\s+the\s+uploaded\s+(?:data|file)|based\s+on\s+the\s+(?:uploaded|attached)\s+(?:file|workbook|spreadsheet))\b", text, re.I,
    )
    return bool(workbook_language and ("[sheet:" in str(file_context or "").casefold() or re.search(r"\.xlsx?\b", " ".join(str(x) for x in (source_documents or [])), re.I) or source_documents))


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
    merged: OrderedDict[tuple[str, str], dict[str, Any]] = OrderedDict()
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
                    # PMS returns in this workbook are stored as fractions (e.g. .248 = 24.8%).
                    period_values[period] = number * 100.0
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
                key = (_norm(amc), _norm(approach))
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

    chart_defs = [
        ("1 Yr", "Top PMS Strategies by 1-Year Return", "Reported 1-year return"),
        ("3 Yr", "Top PMS Strategies by 3-Year Return", "Reported 3-year return"),
        ("5 Yr", "Top PMS Strategies by 5-Year Return", "Reported 5-year return"),
    ]
    charts: list[dict] = []
    used_rows: dict[str, list[str]] = {}
    source_map: dict[str, str] = {}
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
            quote = source_row.get("rawLine") or ""
            filename_key = str(source_row.get("filename") or item["filename"]).casefold()
            used_rows.setdefault(filename_key, []).append(
                f"[Sheet: {source_row.get('sheet')}] {source_row.get('tableTitle')} | {source_row.get('headerLine')} | {source_row.get('periodLine')}\n{quote}"
            )
            points.append({
                "label": f"{item['amc']} — {item['approach']}",
                "value": round(float(item["periodValues"][period]), 4),
                "evidenceSource": "",  # assigned once the canonical source registry exists
                "evidenceQuote": quote,
                "evidenceValue": source_row.get("periodRaw", {}).get(period, item.get("periodRaw", {}).get(period, "")),
                "sourceSheet": source_row.get("sheet"), "sourceRow": source_row.get("rowNumber"),
            })
        charts.append({
            "type": "horizontal_bar", "title": chart_title,
            "xLabel": "PMS strategy", "yLabel": f"{period} return (%)", "unit": "%",
            "series": [{"name": series_name, "data": points}],
            "sourceIds": [], "sourceOrigin": "uploaded_workbook", "workbookMetric": period,
            "rationale": f"Ranked comparison of actual {period} return values from the uploaded workbook.",
            "takeaway": f"Shows the ten highest reported {period} returns in the available workbook tables; missing values are excluded.",
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
    return {"kind": "pms_strategy_performance", "files": files_result,
            "strategies": strategies, "charts": charts, "keyStats": stats,
            "evidenceByFile": evidence_by_file}


def assign_workbook_source(charts: list[dict], source_id: str) -> list[dict]:
    """Attach canonical uploaded-workbook source IDs to a deterministic chart set."""
    sid = str(source_id or "").strip().upper()
    out = []
    for chart in charts or []:
        item = dict(chart)
        item["sourceIds"] = [sid] if sid else []
        series_out = []
        for series in item.get("series") or []:
            s = dict(series)
            points = []
            for point in s.get("data") or []:
                p = dict(point)
                if sid:
                    p["evidenceSource"] = sid
                points.append(p)
            s["data"] = points
            series_out.append(s)
        item["series"] = series_out
        out.append(item)
    return out


def workbook_charts_section(charts: list[dict], start_index: int = 1) -> str:
    """Create concise in-body placeholders for deterministic workbook charts."""
    if not charts:
        return ""
    lines = ["## Quantitative comparison from the uploaded workbook", "",
             "The charts below are calculated from the workbook's reported values as of its stated date. Missing or undisclosed values are excluded, not treated as zero. Rankings reflect the reported periods and are not forecasts.", ""]
    for offset, chart in enumerate(charts, start=0):
        lines.extend([f"### {chart.get('title', 'Workbook comparison')}", f"[CHART_{start_index + offset}]",
                      str(chart.get("takeaway") or "This chart compares the available reported observations."), ""])
    return "\n".join(lines).strip()
