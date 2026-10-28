"""Regression tests for deterministic PMS workbook analysis and file-only mode."""
import csv
from io import StringIO

from utils.uploaded_workbook_analytics import (
    assign_workbook_source,
    build_pms_workbook_analytics,
    is_file_only_workbook_report,
    workbook_charts_section,
)


def _csv_block(rows):
    buffer = StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerows(rows)
    return buffer.getvalue()


def _pms_context():
    header = ["AMC", "Investment Approach", "Benchmark", "AUM (Cr)", "Category", "Returns"]
    periods = ["", "", "", "", "", "", "1 Mth", "3 Mth", "6 Mth", "1 Yr", "2 Yr", "3 Yr", "5 Yr", "10 Yr", "Since Incp."]
    alpha = ["AMC Alpha", "Alpha Growth", "Benchmark A", "100", "Equity", "Returns", "0.01", "0.02", "0.03", "0.248", "0.30", "0.42", "0.50", "ND", "0.60"]
    beta = ["AMC Beta", "Beta Balanced", "Benchmark B", "200", "Equity", "Returns", "0.02", "0.03", "0.04", "0.19", "0.21", "0.29", "0.30", "NA", "0.40"]
    return "\n".join([
        "[File: PMS_demo.xlsx]",
        "[Sheet: Table 2]",
        _csv_block([["AS ON 31 JAN 2026"], header, periods, alpha, beta]),
    ])


def test_pms_workbook_yields_deterministic_return_charts_and_missing_values_stay_missing():
    result = build_pms_workbook_analytics(_pms_context())
    assert result["kind"] == "pms_strategy_performance"
    assert len(result["strategies"]) == 2
    assert len(result["charts"]) == 3
    one_year = next(c for c in result["charts"] if "1-Year" in c["title"])
    points = one_year["series"][0]["data"]
    assert points[0]["value"] == 24.8
    assert points[0]["evidenceValue"] == "0.248"
    assert all(p["value"] != 0 for p in points)
    five_year = next(c for c in result["charts"] if "5-Year" in c["title"])
    assert len(five_year["series"][0]["data"]) == 2
    assert "0.248" in result["evidenceByFile"]["pms_demo.xlsx"]
    assert "Deterministic calculations from the uploaded workbook" in result["evidenceByFile"]["pms_demo.xlsx"]


def test_workbook_charts_get_canonical_source_and_body_placeholders():
    result = build_pms_workbook_analytics(_pms_context())
    assigned = assign_workbook_source(result["charts"], "S1")
    assert all(chart["sourceIds"] == ["S1"] for chart in assigned)
    assert all(point["evidenceSource"] == "S1" for chart in assigned for point in chart["series"][0]["data"])
    body = workbook_charts_section(assigned, start_index=3)
    assert "[CHART_3]" in body
    assert "[CHART_4]" in body
    assert "[CHART_5]" in body


def test_workbook_only_mode_can_be_overridden_by_explicit_external_research_request():
    context = _pms_context()
    assert is_file_only_workbook_report(
        "Generate a report using all relevant worksheets in the uploaded workbook.", context, has_rag=True
    )
    assert not is_file_only_workbook_report(
        "Generate a report using the uploaded workbook and search the web for current market context.", context, has_rag=True
    )


def test_negative_web_instruction_does_not_turn_off_workbook_only_mode():
    context = _pms_context()
    assert is_file_only_workbook_report(
        "Generate a report from the uploaded workbook. Do not search the web or use external sources.",
        context, has_rag=True,
    )
