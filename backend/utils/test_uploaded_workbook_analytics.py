"""Regression tests for deterministic PMS workbook analysis and file-only mode."""
import csv
from io import StringIO

from utils.uploaded_workbook_analytics import (
    append_attachment_analytics_section,
    attachment_analytics_section,
    assign_workbook_source,
    build_attachment_analytics,
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
    # Charts are generated for every return horizon with at least two numeric
    # observations, not just the former hard-coded 1/3/5-year subset.
    assert len(result["charts"]) >= 7
    titles = [c["title"] for c in result["charts"]]
    assert any("1-Month" in title for title in titles)
    assert any("2-Year" in title for title in titles)
    assert any("Since-Inception" in title for title in titles)
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


def test_pms_attachment_tables_cover_horizons_categories_rankings_and_per_file_sources():
    result = build_attachment_analytics(_pms_context())
    section = attachment_analytics_section(result, source_ids_by_file={"PMS_demo.xlsx": "S7"})

    assert "| File | Source | Sheet / section | Data rows analyzed | Fields | Missing observations |" in section
    assert "| Horizon | Source(s) | Records with values | Coverage | Mean return | Median return | Range |" in section
    assert "| 1 Yr | [S7] | 2 |" in section
    assert "## One-year category comparison" in section
    assert "## Highest reported strategies by return horizon" in section
    assert "| 1 Yr | 1 | [S7] | Table 2 | AMC Alpha" in section
    assert result["files"][0]["sheets"][0]["missingReturnValues"] == 2


def test_final_attachment_tables_replace_empty_model_coverage_section():
    result = build_attachment_analytics(_pms_context())
    original = "## Executive Findings\n\n## Attached-data coverage\n\n## Conclusion"

    report, changed = append_attachment_analytics_section(
        original, result, source_ids_by_file={"PMS_demo.xlsx": "S3"}
    )

    assert changed
    assert report.count("## Attached-data coverage") == 1
    assert "| Horizon | Source(s) | Records with values |" in report
    assert "| 1 Yr | [S3] | 2 |" in report
    assert report.index("## Conclusion") < report.index("## Attached-data coverage")


def test_negative_web_instruction_does_not_turn_off_workbook_only_mode():
    context = _pms_context()
    assert is_file_only_workbook_report(
        "Generate a report from the uploaded workbook. Do not search the web or use external sources.",
        context, has_rag=True,
    )


def test_percentage_display_strings_are_not_multiplied_twice():
    context = _pms_context().replace('"0.248"', '"24.8%"').replace('"0.19"', '"19%"')
    result = build_pms_workbook_analytics(context)
    one_year = next(c for c in result["charts"] if "1-Year" in c["title"])
    values = [p["value"] for p in one_year["series"][0]["data"]]
    assert values[0] == 24.8
    assert values[1] == 19.0
    assert "derived plotted return: 24.8000%" in one_year["series"][0]["data"][0]["evidenceQuote"]


def test_generic_report_request_with_structured_workbook_uses_workbook_analytics_by_default():
    context = _pms_context()
    assert is_file_only_workbook_report(
        "Analyze this data and create an in-depth report with charts.", context, has_rag=True
    )
    assert not is_file_only_workbook_report(
        "Analyze this data and create a report with charts using current market data from the web.",
        context, has_rag=True,
    )


def test_csv_report_builds_numeric_and_category_tables_with_data_backed_charts():
    context = """[File: sales.csv]
Month,Region,Revenue,Margin
2026-01,North,100,15%
2026-02,South,130,18%
2026-03,North,115,16%
2026-04,West,140,21%
"""
    result = build_attachment_analytics(context)
    assert result["kind"] == "generic_tabular_data"
    assert result["datasets"][0]["rowCount"] == 4
    assert {item["name"] for item in result["datasets"][0]["numericSummaries"]} == {"Revenue", "Margin"}
    assert len(result["charts"]) >= 2
    table = attachment_analytics_section(result)
    assert "Numeric-field summary" in table
    assert "Categorical distributions" in table
    assert "North (2)" in table
    assert is_file_only_workbook_report("Create a report from the attached CSV file", context)
    assert not is_file_only_workbook_report("Create a report and search the web for current sector news", context)


def test_pms_workbook_keeps_other_attached_tabular_files_in_the_same_report():
    context = _pms_context() + """

[File: sales.csv]
Month,Region,Revenue
2026-01,North,100
2026-02,South,130
2026-03,North,115
"""
    result = build_attachment_analytics(context)
    assert result["kind"] == "pms_strategy_performance"
    assert len(result["strategies"]) == 2
    assert result["datasets"][0]["filename"] == "sales.csv"
    assert any("Revenue" in chart["title"] for chart in result["charts"])
    profile = attachment_analytics_section(result)
    assert "Return-horizon coverage and distribution" in profile
    assert "sales.csv" in profile


def test_attachment_chart_source_ids_are_assigned_per_file():
    context = """[File: sales.csv]
Month,Region,Revenue
2026-01,North,100
2026-02,South,130
2026-03,North,115
"""
    result = build_attachment_analytics(context)
    assigned = assign_workbook_source(result["charts"], "S1", {"sales.csv": "S2"})
    assert assigned
    assert all(chart["sourceIds"] == ["S2"] for chart in assigned)
    assert all(
        point["evidenceSource"] == "S2"
        for chart in assigned for series in chart["series"] for point in series["data"]
    )


def test_generic_summary_tables_cite_the_correct_attachment_per_row():
    context = """[File: north.csv]
Month,Region,Revenue
2026-01,North,100
2026-02,North,120
2026-03,North,110

[File: south.csv]
Month,Region,Revenue
2026-01,South,200
2026-02,South,220
2026-03,South,210
"""
    analytics = build_attachment_analytics(context)
    section = attachment_analytics_section(
        analytics, source_ids_by_file={"north.csv": "S1", "south.csv": "S2"},
    )

    assert "| Sheet / section | Source | Field |" in section
    north_row = next(line for line in section.splitlines() if "[S1]" in line and "Revenue" in line)
    south_row = next(line for line in section.splitlines() if "[S2]" in line and "Revenue" in line)
    assert "north.csv" not in north_row  # file name is kept in the coverage table
    assert "[S2]" not in north_row
    assert "[S1]" not in south_row
