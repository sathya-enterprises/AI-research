"""V8 regressions: Brief report integrity, evidence visuals, and source hygiene."""

from routes.html_report import (
    _brief_key_stats,
    _match_planned_sections,
    _verified_report_trend,
    build_html_report,
)
from utils.presentation_schema import ReportPresentationSpec


def test_brief_rejects_prose_as_kpi_card_and_keeps_compact_three_card_row():
    stats = [
        {"label": "2029 market size", "value": "$446B"},
        {"label": "CAGR", "value": "26%"},
        {"label": "2024 base", "value": "$140B"},
        {"label": "Accelerator share", "value": "33%"},
        {"label": "Growth drivers", "value": "AI workloads, custom silicon, HBM"},
    ]
    selected = _brief_key_stats(stats)
    assert len(selected) == 3
    assert all(any(ch.isdigit() for ch in str(x["value"])) for x in selected)


def test_report_prose_can_become_a_sourced_trend_without_invention():
    report = (
        "The market is projected to expand from an estimated $140B in 2024 "
        "to $446B by 2029, driven by AI workloads [S4]."
    )
    registry = [{"id": "S4", "title": "Market forecast", "url": "https://example.com/forecast"}]
    chart = _verified_report_trend(report, registry)
    assert chart is not None
    assert [x["value"] for x in chart["series"][0]["data"]] == [140.0, 446.0]
    assert chart["source"] == ["S4"]


def test_section_matching_never_pairs_unrelated_heading_and_body():
    spec, _ = ReportPresentationSpec.from_llm_output({
        "sections": [
            {"title": "Risks & Near-Term Outlook", "order": 1, "layout": "single_column"},
            {"title": "Market Overview: Size & Growth", "order": 2, "layout": "single_column"},
        ]
    })
    actual = [
        {"title": "Executive Takeaway & Key KPIs", "body": "Executive body"},
        {"title": "Market Overview: Size & Growth", "body": "Market body"},
    ]
    matches = _match_planned_sections(spec, actual)
    assert [(plan["title"], body["title"]) for plan, body in matches] == [
        ("Executive Takeaway & Key KPIs", "Executive Takeaway & Key KPIs"),
        ("Market Overview: Size & Growth", "Market Overview: Size & Growth"),
    ]


def test_brief_sources_are_cited_only_and_internal_gap_text_never_leaks():
    report = "## Market Overview\n\nMarket grows from $140B in 2024 to $446B by 2029 [S4]."
    sources = [
        {"id": "S1", "title": "Unrelated index feed", "url": "https://example.com/index"},
        {"id": "S2", "title": "Unrelated live feed", "url": "https://example.com/live"},
        {"id": "S3", "title": "Unused research", "url": "https://example.com/unused"},
        {"id": "S4", "title": "Global Data Center Accelerator Forecast", "url": "https://example.com/forecast"},
    ]
    html = build_html_report(
        report,
        "Global Semiconductor AI Chip & Data-Center Accelerator Market — Executive Brief",
        "Semiconductor AI accelerator market",
        "Executive summary",
        [], [], [], sources=sources, report_tier="brief",
    )
    assert "Unrelated index feed" not in html
    assert "Unrelated live feed" not in html
    assert "Unused research" not in html
    assert "Global Data Center Accelerator Forecast" in html
    assert "Attribution was not supplied with this item." not in html


def test_brief_section_heading_matches_rendered_body_source():
    report = (
        "## Executive Takeaway & Key KPIs\n\n"
        "The market is expanding from $140B in 2024 to $446B by 2029 [S4].\n\n"
        "## Risks & Near-Term Outlook\n\n" + ("Supply constraints remain a key risk [S4]. " * 80)
    )
    presentation = {
        "sections": [
            {"title": "Risks & Near-Term Outlook", "order": 1, "layout": "single_column"},
            {"title": "Executive Takeaway & Key KPIs", "order": 2, "layout": "single_column"},
        ],
        "source_appendix": {"placement": "end_of_report", "include_appendix": True},
    }
    html = build_html_report(
        report,
        "Semiconductor Executive Brief",
        "AI accelerators",
        "Summary",
        [], [], [],
        sources=[{"id": "S4", "title": "Forecast", "url": "https://example.com/forecast"}],
        presentation=presentation,
        report_tier="brief",
    )
    assert '<h2>Executive Takeaway &amp; Key KPIs</h2>' in html
    assert '<h2>Risks &amp; Near-Term Outlook</h2>' in html
    assert html.index('<h2>Executive Takeaway &amp; Key KPIs</h2>') < html.index('<h2>Risks &amp; Near-Term Outlook</h2>')
    assert '<h2>Risks &amp; Near-Term Outlook</h2></div><div' not in html


def test_brief_source_appendix_is_last_and_starts_after_report_content():
    report = (
        "## Market Outlook\n\n"
        "The market expands from $100B in 2025 to $180B in 2030 [S1].\n\n"
        "## Risks\n\nPolicy changes may affect adoption [S1]."
    )
    html = build_html_report(
        report, "EV Market Brief", "EV market", "Summary", [], [], [],
        sources=[{"title": "Primary source", "url": "https://example.com/source"}],
        presentation={"source_appendix": {"placement": "end_of_report", "include_appendix": True}},
        report_tier="brief",
    )
    report_end = html.index('<section id="data-sources"')
    assert report_end > html.index('Market Outlook')
    assert report_end > html.index('Risks')
    assert 'data-source-placement="end_of_report"' in html


def test_orphan_citation_is_preserved_with_an_explicit_unresolved_source_record():
    report = "## Outlook\n\nThis claim has no resolvable source [S27]."
    html = build_html_report(
        report, "Brief", "Topic", "Summary", [], [], [],
        sources=[{"title": "Only source", "url": "https://example.com/source"}],
        report_tier="brief",
    )
    assert 'href="#source-S27"' in html
    assert 'id="source-S27"' in html
    assert "Source URL unavailable" in html
    assert "this citation has not been verified" in html


def test_duplicate_sources_do_not_create_orphan_citation_ids():
    report = "## Market Outlook\n\nThe market expands from $100B in 2025 to $180B in 2030 [S2]."
    sources = [
        {"title": "Same source", "url": "https://example.com/same"},
        {"title": "Same source duplicate", "url": "https://example.com/same"},
    ]
    html = build_html_report(
        report, "Brief", "Topic", "Summary", [], [], [], sources=sources, report_tier="brief"
    )
    # Canonical de-duplication cannot guess that the report's S2 means S1.
    # Preserve the citation as an unresolved entry instead of silently mapping
    # it to the wrong source or deleting a potentially material citation.
    assert 'href="#source-S2"' in html
    assert 'id="source-S2"' in html
    assert 'Same source' not in html
    assert "this citation has not been verified" in html
