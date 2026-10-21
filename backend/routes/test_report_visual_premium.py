"""Regression tests for current presentation-driven HTML report rendering."""
from routes.html_report import _brief_key_stats, _render_key_stats, build_html_report
from utils.report_scanability import strip_generation_markers


def test_internal_section_and_risk_markers_never_reach_reader_output():
    cleaned = strip_generation_markers(
        "## Findings\nAlpha.\n[[SECTION]] Findings\nBeta.\n[[END SECTION]]\n[RISK_MATRIX]"
    )
    assert "[[SECTION" not in cleaned
    assert "[[END SECTION]]" not in cleaned
    assert "[RISK_MATRIX]" not in cleaned


def test_unlabelled_kpis_are_dropped():
    stats = _brief_key_stats([
        {"label": "Revenue", "value": "$96B", "sourceIds": ["S1"]},
        {"value": "$107B", "sourceIds": ["S1"]},
        {"label": "Margin", "value": "42%", "sourceIds": ["S1"]},
    ])
    rendered = _render_key_stats(stats, [{"id": "S1", "title": "Source", "url": "https://example.com"}])
    assert "Revenue" in rendered and "Margin" in rendered
    assert "$107B" not in rendered


def test_structured_charts_markdown_tables_and_citations_render():
    report = """## Competitive Landscape
Tesla has a $1.25 trillion market cap while BYD has $107 billion [S1].

| Metric | Tesla | BYD | Volkswagen |
|---|---:|---:|---:|
| Market Cap | $1.25 trillion | $107 billion | Not reported |
| Core Strategy | Premium | Mass-market | Broad |

[CHART_1]
"""
    chart = {"type": "bar", "title": "Market cap comparison", "series": [{"name": "USD billions", "data": [
        {"label": "Tesla", "value": 1250}, {"label": "BYD", "value": 107},
    ]}]}
    html = build_html_report(
        report, "Competitive Landscape", "Compare companies", "Summary", [], [chart], [],
        sources=[{"id": "S1", "title": "Source", "url": "https://example.com/source"}],
        report_tier="detailed",
    )
    assert "Market cap comparison" in html
    assert "Tesla" in html and "Market Cap" in html and "Core Strategy" in html
    assert "https://example.com/source" in html
    assert "[CHART_1]" not in html


def test_brief_html_keeps_compact_report_without_unplanned_kpi_dashboard():
    report = "## Key Findings\nAlpha leads while Beta gains volume [S1].\n\n## Risks\nExecution risk remains. [S1]"
    html = build_html_report(
        report, "Alpha vs Beta", "Compare the companies", "Alpha leads.",
        [{"label": "Alpha market cap", "value": "$1.25T", "sourceIds": ["S1"]}, {"value": "$107B", "sourceIds": ["S1"]}], [], [],
        sources=[{"id": "S1", "title": "Source", "url": "https://example.com/source"}],
        report_tier="brief",
    )
    # Brief output must not add a dashboard unless the dynamic presentation
    # plan actually asks for one; it should still keep the narrative and markers clean.
    assert "Key Findings" in html and "Execution risk remains" in html
    assert "Alpha market cap" not in html
    assert "[RISK_MATRIX]" not in html
