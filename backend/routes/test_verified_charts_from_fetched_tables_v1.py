from routes import report as report_mod
from utils.chart_evidence_audit import (
    _chart_source_text, _publisher_evidence_verified, audit_chart_evidence_consistency,
)


SOURCE_TABLES = """TABLE 1 — Consolidated balance sheet
(At end-March; ₹ crore)
Item | Public Sector Banks | Private Sector Banks | Foreign Banks
2024 | 2025 | 2024 | 2025 | 2024 | 2025
1 | 2 | 3 | 4 | 5 | 6
Total Liabilities/Assets | 1,54,93,234 | 1,71,41,751 | 1,05,21,628 | 1,15,82,590 | 17,05,711 | 20,51,903

TABLE 2 — Financial performance
(₹ crore)
Item | PSBs | PVBs | FBs | SFBs | PBs | SCBs
2023-24 | 2024-25 | 2023-24 | 2024-25 | 2023-24 | 2024-25 | 2023-24 | 2024-25 | 2023-24 | 2024-25 | 2023-24 | 2024-25
1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13
A. Income | 12,12,665 | 13,65,244 | 9,41,870 | 10,70,709 | 1,29,838 | 1,46,580 | 45,400 | 54,223 | 7,102 | 6,937 | 23,36,876 | 26,43,694
C. Operating Profit | 2,58,964 | 3,01,979 | 2,72,986 | 3,00,362 | 48,130 | 52,006 | 10,740 | 11,640 | 114 | 330 | 5,90,934 | 6,66,316
D. Net Profit | 1,41,202 | 1,78,364 | 1,75,297 | 1,89,211 | 26,886 | 29,915 | 6,219 | 3,496 | -1 | 194 | 3,49,603 | 4,01,180
E. Net Interest Income (NII) | 4,07,632 | 4,29,128 | 3,66,839 | 4,01,683 | 59,049 | 64,612 | 22,173 | 24,446 | 1,063 | 1,157 | 8,56,756 | 9,21,027
"""


def _source(status="ok"):
    return {
        "id": "S5",
        "title": "Publications | Official Website of Reserve Bank of India",
        "url": "https://www.rbi.org.in/scripts/PublicationsView.aspx?id=23639",
        "pageFetchStatus": status,
        "pageFetchRelevanceMatched": True,
        "evidenceOrigin": "publisher_page_and_search_extract",
        "_publisherStructuredTableEvidence": SOURCE_TABLES,
        # The raw prose view is intentionally truncated before the second table,
        # as occurs in the production capture. The fetched structured table is the
        # authoritative table extraction and must still verify it.
        "_publisherRawFetchedContent": "RBI Report on Trend and Progress of Banking in India. Unit: ₹ crore. Table I contains 2024 and 2025 bank-group aggregates.",
    }


def _tables():
    balance_sheet = {
        "type": "table",
        "title": "Supporting Data Table",
        "columns": [
            "Bank Group",
            "Total Liabilities / Assets (₹ crore, 2024)",
            "Total Liabilities / Assets (₹ crore, 2025)",
        ],
        "rows": [
            ["Public Sector Banks", "1,54,93,234", "1,71,41,751"],
            ["Private Sector Banks", "1,05,21,628", "1,15,82,590"],
            ["Foreign Banks", "17,05,711", "20,51,903"],
        ],
    }
    financial = {
        "type": "table",
        "title": "Supporting Data Table",
        "columns": [
            "Financial Parameter (All SCBs)", "2023-24 (₹ crore)", "2024-25 (₹ crore)", "YoY Growth (%)",
        ],
        "rows": [
            ["Total Income", "23,36,876", "26,43,694", "+13.1"],
            ["Operating Profit", "5,90,934", "6,66,316", "+12.8"],
            ["Net Profit", "3,49,603", "4,01,180", "+14.8"],
            ["Net Interest Income (NII)", "8,56,756", "9,21,027", "+7.5"],
        ],
    }
    return [balance_sheet, financial]


def test_verified_fetched_tables_generate_plots_and_remove_duplicate_table_marker():
    report_text = (
        "## Balance Sheet and Growth\n\n"
        "Balance-sheet aggregates expanded across banking groups.\n\n"
        "[CHART_1]\n\nThe same table was repeated accidentally.\n\n[CHART_1]\n\n"
        "## Profitability Outlook\n\n"
        "Net profit, operating profit and net interest margins shape sector profitability. "
        "Higher deposit costs can compress margins even while income rises [S5]."
    )
    # Reset request-scoped evidence to avoid accidental test-order dependence.
    report_mod._CHART_EVIDENCE_LINES.set({})
    report_mod._PUBLISHER_TABLE_CHART_EVIDENCE_IDS.set(set())
    updated, charts, created = report_mod._add_verified_numeric_charts_from_tables(
        report_text, _tables(), [_source()], "detailed", normalizer=lambda chart: chart, validator=lambda chart: True,
    )
    assert created == 2
    plotted = [chart for chart in charts if chart.get("type") != "table"]
    assert len(plotted) == 2
    assert all(chart.get("series") for chart in plotted)
    assert all(chart.get("source") == ["S5"] for chart in plotted)
    assert updated.count("[CHART_1]") == 1
    assert updated.count("[CHART_3]") == 1
    assert updated.count("[CHART_4]") == 1
    assert "[CHART_2]" not in updated
    assert len(plotted[0]["series"][0]["data"]) == 3
    assert len(plotted[1]["series"][0]["data"]) == 4


def test_search_only_or_unverified_page_cannot_generate_numeric_plots():
    report_text = "Banking evidence [CHART_1]. Duplicate marker [CHART_1]."
    report_mod._CHART_EVIDENCE_LINES.set({})
    report_mod._PUBLISHER_TABLE_CHART_EVIDENCE_IDS.set(set())
    updated, charts, created = report_mod._add_verified_numeric_charts_from_tables(
        report_text, _tables(), [_source(status="timeout")], "detailed",
        normalizer=lambda chart: chart, validator=lambda chart: True,
    )
    assert created == 0
    assert all(chart.get("type") == "table" for chart in charts)
    assert updated.count("[CHART_1]") == 1


def test_table_chart_excerpt_is_accepted_only_when_explicitly_marked_as_verified():
    unsafe = {"id": "S5", "page_fetch_status": "ok", "evidence_origin": "publisher_page",
              "chartEvidenceExcerpt": "Total Income | 23,36,876 | 26,43,694"}
    assert not _publisher_evidence_verified(unsafe)
    safe = dict(unsafe, publisher_table_chart_evidence_verified=True)
    assert _publisher_evidence_verified(safe)
    assert "23,36,876" in _chart_source_text(safe, require_publisher_verification=True)


def test_pdf_chart_conversion_produces_static_svg_not_empty_canvas():
    import re
    from routes.html_report import build_html_report
    from routes.pdf import _replace_chart_runtime_with_svg

    report_mod._CHART_EVIDENCE_LINES.set({})
    report_mod._PUBLISHER_TABLE_CHART_EVIDENCE_IDS.set(set())
    source = _source()
    report_text = (
        "## Balance Sheet and Growth\n\nBalance-sheet aggregates expanded across banking groups.\n\n[CHART_1]\n\n"
        "## Profitability Outlook\n\nNet profit, operating profit and net interest income shape sector profitability. [S5]"
    )
    updated, charts, created = report_mod._add_verified_numeric_charts_from_tables(
        report_text, _tables(), [source], "detailed", normalizer=lambda chart: chart, validator=lambda chart: True,
    )
    assert created == 2
    html_doc = build_html_report(
        updated, "India Banking Sector", "India banking sector", "", [], charts, [],
        sources=[dict(source, pageContent=SOURCE_TABLES, page_fetch_status="ok",
                      evidence_origin="publisher_page", evidence_excerpt_origin="publisher_page",
                      publisher_table_chart_evidence_verified=True,
                      chartEvidenceExcerpt=report_mod._chart_evidence_lines_for("S5"))],
        report_tier="detailed",
    )
    pdf_html = _replace_chart_runtime_with_svg(html_doc)
    assert len(re.findall(r"<svg\b", pdf_html, re.I)) >= 2
    assert "<canvas" not in pdf_html.casefold()
    assert "window.__ggCharts.push" not in pdf_html
    assert "Total Liabilities / Assets (2024–2025) by Bank Group" in pdf_html
    assert "Scheduled Commercial Bank Financial Performance by Fiscal Year" in pdf_html
