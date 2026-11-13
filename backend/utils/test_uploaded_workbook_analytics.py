"""Regression tests for deterministic PMS workbook analysis and file-only mode."""
import csv
from io import StringIO

from utils.uploaded_workbook_analytics import (
    append_attachment_analytics_section,
    attachment_analytics_section,
    assign_workbook_source,
    build_attachment_analytics,
    build_pms_workbook_analytics,
    ensure_server_workbook_chart_markers,
    is_file_only_workbook_report,
    pms_report_cover,
    rewrite_pms_report_sections,
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
    # The summary covers every horizon. Ranked charts show representative
    # short-, medium-, and longer-term windows instead of nine repeated bars.
    assert len(result["charts"]) == 3
    titles = [c["title"] for c in result["charts"]]
    assert any("1-Month" in title for title in titles)
    assert any("1-Year" in title for title in titles)
    assert any("5-Year" in title for title in titles)
    one_year = next(c for c in result["charts"] if "1-Year" in c["title"])
    points = one_year["series"][0]["data"]
    assert points[0]["value"] == 24.8
    assert points[0]["evidenceValue"] == "0.248"
    assert all(p["value"] != 0 for p in points)
    five_year = next(c for c in result["charts"] if "5-Year" in c["title"])
    assert len(five_year["series"][0]["data"]) == 2
    assert result["periodCoverage"]["2 Yr"] == 2
    assert result["periodCoverage"]["Since Incp."] == 2
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
    assert body.index(assigned[0]["title"]) < body.index("[CHART_3]")
    assert body.index("Shows up to ten highest reported") < body.index("[CHART_3]")


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

    assert "| File | Sheet / section | Data rows analyzed | Fields | Missing observations |" in section
    assert "| Horizon | Records with values | Coverage | Mean return | Median return | Range |" in section
    assert "| 1 Yr | 2 |" in section
    assert "## One-year category comparison" in section
    assert "## Highest reported strategies by return horizon" in section
    assert "| Horizon | Rank | Worksheet | AMC / strategy | Reported return |" in section
    assert "| 1 Yr | 1 | Table 2 | AMC Alpha" in section
    assert "| Horizon | Source(s) |" not in section
    assert "| Source(s) |" not in section
    assert result["files"][0]["sheets"][0]["missingReturnValues"] == 2


def test_pms_snapshot_charts_use_grouped_summaries_and_pass_the_evidence_gate():
    from utils.chart_evidence_audit import audit_chart_evidence_consistency

    context = _pms_context().replace(
        "AMC Beta,Beta Balanced,Benchmark B,200,Equity,Returns,0.02,0.03,0.04,0.19,0.21,0.29,0.30,NA,0.40",
        "AMC Beta,Beta Balanced,Benchmark B,200,Equity,Returns,0.02,0.03,0.04,0.19,0.21,0.29,0.30,NA,0.40\n"
        "AMC Gamma,Gamma Growth,Benchmark C,150,Equity,Returns,0.03,0.04,0.05,0.15,0.20,0.25,0.30,NA,0.35",
    )
    result = build_attachment_analytics(context)
    charts = assign_workbook_source(result["charts"], "S1")
    for chart in charts:
        chart["_serverWorkbookEvidence"] = True
    source = {
        "id": "S1", "title": "PMS_demo.xlsx", "kind": "uploaded_file",
        "evidenceOrigin": "uploaded_workbook", "pageFetchStatus": "not_applicable",
        "chartEvidenceExcerpt": result["evidenceByFile"]["pms_demo.xlsx"],
    }

    assert len(charts) == 5
    assert [chart["type"] for chart in charts].count("grouped_bar") == 2
    assert [chart["type"] for chart in charts].count("horizontal_bar") == 3
    markers = "\n".join(f"[CHART_{index}]" for index in range(1, len(charts) + 1))
    audit = audit_chart_evidence_consistency(
        markers, charts, [source], question="Analyze the attached PMS workbook.",
        require_publisher_verification=True,
    )
    assert audit.get("chartEvidenceFailures", []) == [], audit
    assert audit.get("chartTableVerificationConflicts", []) == [], audit


def test_benchmark_rows_stop_at_next_strategy_table_and_compare_matched_strategy_median():
    from utils.chart_evidence_audit import audit_chart_evidence_consistency

    periods = ["1 Mth", "3 Mth", "6 Mth", "1 Yr", "2 Yr", "3 Yr", "5 Yr", "10 Yr", "Since Incp."]
    header = ["AMC", "Investment Approach", "Benchmark", "AUM", "Category", "Returns"]
    period_header = ["", "", "", "", "", "", *periods]

    def strategy(manager, name, values):
        return [manager, name, "NIFTY 50", "50", "Equity", "Returns"] + [values.get(period, "NA") for period in periods]

    rows = [
        ["AS ON 31 JAN 2026"], header, period_header,
        strategy("AMC Alpha", "Alpha Growth", {"1 Mth": "0.01", "1 Yr": "0.10", "3 Yr": "0.20"}),
        strategy("AMC Beta", "Beta Growth", {"1 Mth": "0.02", "1 Yr": "0.20", "3 Yr": "0.30"}),
        strategy("AMC Gamma", "Gamma Growth", {"1 Mth": "0.03", "1 Yr": "0.30", "3 Yr": "0.40"}),
        ["BENCHMARK (TRI)"],
        ["NIFTY 50", "", "", "", "", "", "-0.03", "NA", "NA", "0.08", "NA", "0.12", "NA", "NA", "NA"],
        ["TOP PERFORMANCE - 1 Month"], header, period_header,
        strategy("AMC Delta", "Delta Growth", {"1 Mth": "0.04", "1 Yr": "0.40", "3 Yr": "0.50"}),
    ]
    context = "\n".join([
        "[File: manager_export.csv]", "[Sheet: Returns]", _csv_block(rows),
    ])

    result = build_attachment_analytics(context)
    benchmark_rows = result["benchmarkRows"]
    chart = next(c for c in result["charts"] if c.get("chartRole") == "benchmark_comparison")

    assert len(result["strategies"]) == 4
    assert len(benchmark_rows) == 1
    assert benchmark_rows[0]["label"] == "NIFTY 50"
    assert "AMC Delta" not in benchmark_rows[0]["label"]
    assert "matched PMS strategy median" in chart["title"]
    benchmark, matched = chart["series"]
    assert [point["value"] for point in benchmark["data"]] == [-3.0, 8.0, 12.0]
    assert [point["value"] for point in matched["data"]] == [2.5, 25.0, 35.0]
    assert all(point["sourceObservationCount"] == 4 for point in matched["data"])
    assert all("AMC Delta" not in point["evidenceQuote"] for point in benchmark["data"])

    quality_report = rewrite_pms_report_sections(
        "## Data Quality and Integrity Audit\n\nPlaceholder.", result,
    )
    assert "separate benchmark-return rows in the workbook support the matched comparison" in quality_report
    assert "1 Mth, 1 Yr, 3 Yr" in quality_report
    assert "no separate matched period-return series was detected" not in quality_report

    charts = assign_workbook_source(result["charts"], "S1")
    for item in charts:
        item["_serverWorkbookEvidence"] = True
    source = {
        "id": "S1", "title": "manager_export.csv", "kind": "uploaded_file",
        "evidenceOrigin": "uploaded_attachment", "pageFetchStatus": "not_applicable",
        "chartEvidenceExcerpt": result["evidenceByFile"]["manager_export.csv"],
    }
    audit = audit_chart_evidence_consistency(
        "\n".join(f"[CHART_{i}]" for i in range(1, len(charts) + 1)),
        charts, [source], question="Analyze the attached strategy export.",
        require_publisher_verification=True,
    )
    assert audit.get("chartEvidenceFailures", []) == [], audit
    assert audit.get("chartTableVerificationConflicts", []) == [], audit


def test_missing_server_workbook_chart_markers_are_restored_before_source_list():
    from utils.chart_gallery import cohere_chart_placements
    from routes.report import _canonicalize_chart_registry

    charts = [{
        "title": "PMS return averages by reported horizon (mean vs median)",
        "sourceOrigin": "uploaded_workbook", "_serverWorkbookEvidence": True,
        "takeaway": "These are separate return horizons in one workbook snapshot, not a time series.",
        "series": [],
    }]
    original = "## Executive summary\nThe report summarizes the workbook.\n\n## Sources\n[S1] PMS_demo.xlsx"

    restored, meta = ensure_server_workbook_chart_markers(original, charts)
    coherent, kept, coherence_meta = cohere_chart_placements(restored, charts)
    registered_text, registered_charts, registry_meta = _canonicalize_chart_registry(coherent, kept)

    assert meta["restored"] == 1
    assert restored.index("not a time series") < restored.index("[CHART_1]")
    assert restored.index("[CHART_1]") < restored.index("## Sources")
    assert len(kept) == 1, coherence_meta
    assert "[CHART_1]" in coherent
    assert len(registered_charts) == 1, registry_meta
    assert registry_meta["unreferencedChartsRemoved"] == 0
    assert "[CHART_1]" in registered_text


def test_chart_placement_keeps_workbook_charts_with_their_reported_horizon():
    from utils.chart_gallery import chart_horizons, cohere_chart_placements, explicit_horizons

    charts = [
        {
            "type": "horizontal_bar", "title": "Top PMS Strategies by 1-Month Return",
            "xLabel": "PMS strategy", "yLabel": "Reported return (%)",
            "series": [{"name": "Reported return", "data": [
                {"label": "Alpha Growth", "value": 4.2}, {"label": "Beta Balanced", "value": 3.1},
            ]}],
        },
        {
            "type": "horizontal_bar", "title": "Top PMS Strategies by 1-Year Return",
            "xLabel": "PMS strategy", "yLabel": "Reported return (%)",
            "series": [{"name": "Reported return", "data": [
                {"label": "Alpha Growth", "value": 24.8}, {"label": "Beta Balanced", "value": 19.0},
            ]}],
        },
    ]
    text = (
        "## One-Year Strategy Performance\n"
        "The one-year strategy returns range across the reported PMS strategies.\n\n[CHART_1]\n\n"
        "## One-Month Strategy Performance\n"
        "The 1-month PMS strategy returns show the latest reported short-horizon comparison.\n\n[CHART_2]"
    )

    assert chart_horizons(charts[0]) == {1}
    assert explicit_horizons("One-Year Strategy Performance") == {12}
    coherent, kept, meta = cohere_chart_placements(text, charts)

    assert len(kept) == 2, meta
    assert meta["droppedCharts"] == 0
    one_year = coherent.split("## One-Month Strategy Performance")[0]
    one_month = coherent.split("## One-Month Strategy Performance")[1]
    assert "[CHART_1]" not in one_year
    assert "[CHART_2]" in one_year
    assert "[CHART_1]" in one_month
    assert "[CHART_2]" not in one_month


def test_verified_workbook_chart_marker_moves_out_of_an_incompatible_horizon_section():
    from utils.chart_gallery import cohere_chart_placements

    charts = [
        {
            "type": "horizontal_bar", "title": "Top PMS Strategies by 1-Month Return",
            "sourceOrigin": "uploaded_workbook", "_serverWorkbookEvidence": True,
            "takeaway": "The chart compares the reported 1-month PMS strategy returns.",
            "series": [{"name": "Reported return", "data": [{"label": "Alpha Growth", "value": 4.2}]}],
        },
        {
            "type": "horizontal_bar", "title": "Top PMS Strategies by 1-Year Return",
            "sourceOrigin": "uploaded_workbook", "_serverWorkbookEvidence": True,
            "takeaway": "The chart compares the reported 1-year PMS strategy returns.",
            "series": [{"name": "Reported return", "data": [{"label": "Alpha Growth", "value": 24.8}]}],
        },
    ]
    original = (
        "## One-Year Strategy Performance\n\n"
        "The one-year strategy returns are compared below.\n\n"
        "### Top Performing Strategies (1-Year)\n"
        "The one-year ranking is drawn from the uploaded strategy rows.\n[CHART_1]\n[CHART_2]"
    )

    anchored, meta = ensure_server_workbook_chart_markers(original, charts)
    coherent, kept, placement = cohere_chart_placements(anchored, charts)
    one_year = coherent.split("## Charts calculated from the attached data")[0]
    attachment_charts = coherent.split("## Charts calculated from the attached data", 1)[1]

    assert meta["relocated"] == 1
    assert "[CHART_1]" not in one_year and "[CHART_2]" in one_year
    assert "Top PMS Strategies by 1-Month Return" in attachment_charts
    assert "[CHART_1]" in attachment_charts
    assert len(kept) == 2 and placement["droppedCharts"] == 0


def test_final_attachment_chart_repair_fixes_live_report_horizon_and_stale_notice():
    from routes.report import _canonicalize_chart_registry, _resolve_chart_omission_notes

    analytics = build_attachment_analytics(_pms_context())
    charts = assign_workbook_source(analytics["charts"], "S1")
    for chart in charts:
        chart["_serverWorkbookEvidence"] = True
    original = (
        "## One-Year Strategy Performance\n"
        "### Top Performing Strategies (1-Year)\n"
        "> **Data limitation:** the retrieved sources did not provide a period-matched, "
        "source-verified series for Top Performing Strategies (1-Year); the figures in this "
        "section are cited in the text instead of charted.\n\n"
        "[CHART_1]\n[CHART_2]\n[CHART_3]\n"
        "## Charts calculated from the attached data\n\n"
        "### Top PMS Strategies by 1-Year Return\n\n"
        "### Top PMS Strategies by 5-Year Return\n\n"
        "### PMS return averages by reported horizon (mean vs median)\n\n"
        "### PMS 1-year return averages by category (mean vs median)\n"
    )

    repaired, marker_meta = ensure_server_workbook_chart_markers(original, charts)
    repaired, note_meta = _resolve_chart_omission_notes(
        repaired, charts, attachment_only=True, allow_moves=False,
    )
    one_year_section = repaired.split("## Charts calculated from the attached data", 1)[0]
    attachment_section = repaired.split("## Charts calculated from the attached data", 1)[1]
    registered, kept, registry_meta = _canonicalize_chart_registry(repaired, charts)

    assert marker_meta["relocated"] == 2
    assert "retrieved sources did not provide" not in repaired
    assert note_meta["removed"] == 1
    assert "[CHART_1]" not in one_year_section and "[CHART_3]" not in one_year_section
    assert "[CHART_2]" in one_year_section
    assert "Top PMS Strategies by 1-Month Return" in attachment_section
    assert "[CHART_1]" in attachment_section and "[CHART_3]" in attachment_section
    assert all(registered.count(f"[CHART_{i}]") == 1 for i in range(1, len(charts) + 1))
    assert len(kept) == len(charts), registry_meta


def test_attachment_table_replacement_restores_removed_verified_chart_markers():
    from routes.report import _canonicalize_chart_registry

    analytics = build_attachment_analytics(_pms_context())
    charts = assign_workbook_source(analytics["charts"], "S1")
    for chart in charts:
        chart["_serverWorkbookEvidence"] = True
    original = (
        "## Highest reported strategies by return horizon\n\n"
        "Computed from the attached rows.\n\n"
        + "\n".join(f"[CHART_{index}]" for index in range(1, len(charts) + 1))
    )

    after_tables, changed = append_attachment_analytics_section(original, analytics)
    assert changed
    assert not any(f"[CHART_{index}]" in after_tables for index in range(1, len(charts) + 1))
    restored, meta = ensure_server_workbook_chart_markers(after_tables, charts)
    registered, kept, registry = _canonicalize_chart_registry(restored, charts)

    assert meta["restored"] == len(charts)
    assert len(kept) == len(charts), registry
    assert registry["unreferencedChartsRemoved"] == 0
    assert all(registered.count(f"[CHART_{index}]") == 1 for index in range(1, len(charts) + 1))


def test_final_attachment_tables_replace_empty_model_coverage_section():
    result = build_attachment_analytics(_pms_context())
    original = "## Executive Findings\n\n## Attached-data coverage\n\n## Conclusion"

    report, changed = append_attachment_analytics_section(
        original, result, source_ids_by_file={"PMS_demo.xlsx": "S3"}
    )

    assert changed
    assert report.count("## Attached-data coverage") == 1
    assert "| Horizon | Records with values |" in report
    assert "| 1 Yr | 2 |" in report
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


def test_pms_adapter_selects_by_schema_not_filename_and_accepts_optional_columns():
    context = """[File: vendor_export_2026.csv]
[Sheet: Performance]
AS OF 31 Jan 2026
Fund Manager,Investment Strategy,1 Month Return,1 Year Return,3 Year Return
Alpha Capital,Small Cap Growth,-3.2,13.4,10.8
Beta Partners,Income Strategy,1.1,8.7,7.2
"""

    result = build_attachment_analytics(context)

    assert result["kind"] == "pms_strategy_performance"
    assert len(result["strategies"]) == 2
    assert result["strategies"][0]["periodValues"]["1 Yr"] == 13.4
    assert result["strategies"][0]["benchmark"] == ""
    assert result["strategies"][0]["aum"] is None
    assert result["strategies"][0]["category"] == ""
    assert result["dataQuality"]["fieldPresence"]["aum"] is False
    assert result["dataQuality"]["fieldPresence"]["benchmark"] is False
    assert result["dataQuality"]["fieldPresence"]["category"] is False
    _title, summary = pms_report_cover(result)
    assert "3 Yr median" in summary
    assert "do not by themselves show quarter-by-quarter changes" in summary
    assert "attached-data snapshot" in summary
    assert "workbook snapshot" not in summary

    report = rewrite_pms_report_sections(
        "## One-Year Strategy Performance\n\nPlaceholder.\n\n"
        "## AUM Distribution and Market Concentration\n\nPlaceholder.\n\n"
        "## Data Quality and Integrity Audit\n\nPlaceholder.",
        result,
    )
    tables = attachment_analytics_section(result)
    assert "No AUM column was recognized" in report
    assert "No benchmark column was recognized" in report
    assert "AUM (IN Crs)" not in report
    assert "Fund Manager" in report
    assert "Investment Strategy" in report
    assert "Since-Inception returns can cover different start dates" not in report
    assert "The attached data contains 2 usable 1 Yr return observations" in report
    assert "Alpha Capital / Small Cap Growth (13.40%)" in report
    assert "No AUM column was recognized" in tables
    assert "Strategies with AUM not disclosed" not in tables


def test_report_specific_pms_cover_uses_workbook_date_and_rewrite_keeps_chart_markers():
    analytics = build_attachment_analytics(_pms_context())
    title, summary = pms_report_cover(analytics)
    original = """## Executive Summary
The market proved that small-cap PMS strategies were defensive at 62%.

## Key Performance Findings by Category
Small caps rose because investors rotated toward growth.
[CHART_1]

## Comparative Performance Analysis
The workbook proves quarterly rotation.
"""

    rewritten = rewrite_pms_report_sections(original, analytics, {"PMS_demo.xlsx": "S1"})

    assert "31 January 2026" in title
    assert "do not by themselves show quarter-by-quarter changes" in summary
    assert ".." not in summary
    assert "February 2026" not in title
    assert "defensive at 62%" not in rewritten
    assert "investors rotated toward growth" not in rewritten
    assert "full-universe market concentration" in rewritten
    assert "Benchmark entries are labels rather than period-specific return observations" in rewritten
    assert "[CHART_1]" in rewritten
    assert "[S1]" in rewritten


def test_comparative_horizon_alias_replaces_unsupported_causal_prose():
    analytics = build_attachment_analytics(_pms_context())
    original = (
        "## Comparative Horizon Analysis\n\n"
        "Short-term Volatility vs. Long-term Stability\n"
        "The 1-month and 3-month windows show a negative mean return across almost all equity categories, "
        "likely reflecting a market-wide drawdown."
    )

    rewritten = rewrite_pms_report_sections(original, analytics)

    assert "likely reflecting a market-wide drawdown" not in rewritten
    assert "across almost all equity categories" not in rewritten
    assert "The recognized horizons are separate lookbacks" in rewritten
    assert "do not by themselves establish quarter-by-quarter sector rotation or explain causes" in rewritten


def test_pms_horizon_sections_are_rewritten_from_observed_rows_and_keep_charts():
    analytics = build_attachment_analytics(_pms_context())
    original = (
        "## One-Year Strategy Performance\n\n"
        "The market rotated toward small-cap strategies and they delivered 62%.\n[CHART_1]\n\n"
        "## Three-Year Strategy Performance\n\n"
        "This suggests that the winning strategies will continue to outperform.\n[CHART_2]\n\n"
        "## Five-Year Strategy Performance\n\n"
        "The market favored a tactical allocation approach.\n[CHART_3]"
    )

    rewritten = rewrite_pms_report_sections(original, analytics, {"PMS_demo.xlsx": "S1"})
    one_year = rewritten.split("## Three-Year Strategy Performance")[0]
    three_year = rewritten.split("## Three-Year Strategy Performance")[1].split("## Five-Year Strategy Performance")[0]
    five_year = rewritten.split("## Five-Year Strategy Performance")[1]

    assert "62%" not in rewritten
    assert "rotated toward small-cap" not in rewritten
    assert "This suggests" not in rewritten
    assert "favored a tactical allocation" not in rewritten
    assert "median was 21.90%" in one_year and "AMC Alpha / Alpha Growth (24.80%)" in one_year
    assert "median was 35.50%" in three_year
    assert "median was 40.00%" in five_year
    assert all(marker in section for marker, section in (
        ("[CHART_1]", one_year), ("[CHART_2]", three_year), ("[CHART_3]", five_year),
    ))
    assert "[S1]" in rewritten


def test_strategy_header_without_return_horizons_remains_generic():
    context = """[File: strategy_export.xlsx]
[Sheet: Summary]
Company,Strategy,Year,Revenue
Alpha,Core,2024,100
Beta,Growth,2025,120
"""
    result = build_attachment_analytics(context)

    assert result["kind"] == "generic_tabular_data"


def test_pms_aum_summaries_use_the_uploaded_header_unit():
    context = """[File: global_pms_export.csv]
[Sheet: Performance]
Manager,Strategy,1 Month Return,1 Year Return,AUM (USD millions)
Alpha,Core,-1.2,8.5,4.8
Beta,Growth,0.5,11.2,12.0
"""

    result = build_attachment_analytics(context)
    section = attachment_analytics_section(result)

    assert result["kind"] == "pms_strategy_performance"
    assert "Total reported AUM (USD millions)" in section
    assert "crore" not in section.casefold()
    assert "₹" not in section


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


def test_numeric_only_home_prices_csv_gets_paired_scatter_charts_and_keeps_missing_pairs_out():
    context = """[File: homeprices.csv]
area,bedrooms,age,price
2600,3,20,550000
3000,4,15,565000
3200,,18,610000
3600,3,30,595000
4000,5,8,760000
4100,6,8,810000
"""
    result = build_attachment_analytics(context)

    assert result["kind"] == "generic_tabular_data"
    assert result["datasets"][0]["rowCount"] == 6
    assert len(result["charts"]) == 3
    charts = {chart["xLabel"]: chart for chart in result["charts"]}
    assert set(charts) == {"area", "bedrooms", "age"}
    assert all(chart["type"] == "scatter" for chart in charts.values())
    assert all(len(chart["series"]) == 2 for chart in charts.values())
    assert all(len(chart["series"][0]["data"]) == len(chart["series"][1]["data"])
               for chart in charts.values())

    bedroom_chart = charts["bedrooms"]
    assert len(bedroom_chart["series"][0]["data"]) == 5
    assert all(point["label"] != "Row 4" for point in bedroom_chart["series"][0]["data"])
    area_chart = charts["area"]
    x_point = area_chart["series"][0]["data"][0]
    y_point = area_chart["series"][1]["data"][0]
    assert x_point["value"] == 2600 and y_point["value"] == 550000
    assert "raw cell value: 2600" in x_point["evidenceQuote"]
    assert "raw cell value: 550000" in y_point["evidenceQuote"]
    assert "Original row: 2600,3,20,550000" in x_point["evidenceQuote"]
    assert not area_chart.get("unit")  # The CSV does not state a currency.
    from utils.chart_catalog import count_chart_points
    assert count_chart_points(area_chart) == 6
    from utils.chart_evidence_audit import audit_chart_evidence_consistency
    assigned = assign_workbook_source(result["charts"], "S1")
    source = {
        "id": "S1", "title": "homeprices.csv", "kind": "uploaded_attachment",
        "evidenceOrigin": "uploaded_attachment", "pageContent": context,
        "chartEvidenceExcerpt": result["evidenceByFile"]["homeprices.csv"],
    }
    audit = audit_chart_evidence_consistency(
        "[CHART_1]\n[CHART_2]\n[CHART_3]", assigned, [source],
        question="Analyze home price data relationships.", require_publisher_verification=True,
    )
    assert audit.get("chartEvidenceFailures", []) == [], audit
    assert audit.get("chartTableVerificationConflicts", []) == [], audit


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
