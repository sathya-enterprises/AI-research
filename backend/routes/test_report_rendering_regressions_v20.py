from routes.html_report import build_html_report


def _presentation(**overrides):
    base = {
        "domain": "generic",
        "cover": {"enabled": False, "treatment": "minimal", "show_date": True},
        "executive_summary": {"placement": "none", "heading": "Executive Summary", "key_metrics": []},
        "sections": [
            {"id": "s1", "title": "Executive Summary", "section_type": "executive_summary", "layout": "single_column", "density": "standard", "emphasis": "normal", "order": 0, "blocks": []},
            {"id": "s2", "title": "Value Chain", "section_type": "overview", "layout": "single_column", "density": "standard", "emphasis": "normal", "order": 1, "blocks": []},
        ],
        "source_appendix": {"placement": "end_of_report", "include_appendix": True},
        "default_layout": "single_column", "default_density": "standard",
        "visual_spec": {
            "composition_mode": overrides.get("composition_mode", "narrative_led"),
            "title_treatment": overrides.get("title_treatment", "minimal"),
            "section_treatment": overrides.get("section_treatment", "ruled"),
            "chart_treatment": overrides.get("chart_treatment", "framed"),
            "table_treatment": overrides.get("table_treatment", "striped"),
            "background_style": overrides.get("background_style", "plain"),
            "visual_rhythm": "narrative_flow",
            "whitespace_profile": "balanced",
            "emphasis_profile": "narrative",
            "density": "balanced",
            "opening_mode": "narrative",
        },
    }
    return base


def test_existing_executive_summary_is_not_rendered_twice():
    html = build_html_report(
        "## Executive Summary\n\nThis is the real report summary.\n\n## Findings\n\nEvidence here.",
        "Test Report", "Test", "API summary duplicate", [], [], [], None, [],
        presentation=_presentation(), report_tier="brief",
    )
    assert html.count("Executive Summary") == 1  # only the actual section heading remains
    assert '<section id="report-overview"' not in html


def test_bare_ascii_diagram_is_converted_to_printable_cards():
    report = """## Value Chain\n\n+----------------+\n| UPSTREAM       |\n+----------------+\n| Design & EDA   |\n+----------------+\n| MIDSTREAM      |\n+----------------+\n| Wafer Fab      |\n+----------------+\n"""
    html = build_html_report(report, "Semiconductors", "Test", "", [], [], [], None, [], presentation=_presentation(), report_tier="brief")
    assert "gg-ascii-diagram" in html
    assert "+----------------+" not in html


def test_visual_direction_becomes_renderer_classes():
    presentation = _presentation(composition_mode="comparison_led", title_treatment="classic", section_treatment="banded")
    html = build_html_report("## Comparison\n\nEvidence.", "Comparison", "Test", "", [], [], [], None, [], presentation=presentation, report_tier="detailed")
    assert "gg-v-comparison-led" in html
    assert "gg-v-classic" in html
    assert "gg-v-banded" in html
    assert "gg-section-kicker" in html


def test_brief_kpi_selection_is_capped_at_three():
    from routes.html_report import _brief_key_stats
    stats = [
        {"label": "Revenue", "value": "$1.6 trillion"},
        {"label": "Growth", "value": "5%"},
        {"label": "Foundry shares", "value": "TSMC 53%, Samsung 17%, GlobalFoundries 7%"},
        {"label": "Regional capacity", "value": "Asia-Pacific 65%"},
    ]
    assert len(_brief_key_stats(stats)) == 3


def test_brief_does_not_render_duplicate_dashboard_when_exec_metrics_exist():
    presentation = _presentation()
    presentation["executive_summary"] = {
        "placement": "after_cover",
        "heading": "Executive Takeaway",
        "key_metrics": [
            {"label": "Revenue", "value": "$1.6 trillion"},
            {"label": "Growth", "value": "5%"},
            {"label": "Foundry share", "value": "TSMC 53%"},
        ],
    }
    html = build_html_report(
        "## Executive Summary\n\nThis is the report summary.\n\n## Findings\n\nEvidence here.",
        "Test Report", "Test", "Summary", [
            {"label": "Revenue", "value": "$1.6 trillion"},
            {"label": "Growth", "value": "5%"},
            {"label": "Foundry share", "value": "TSMC 53%"},
            {"label": "Regional capacity", "value": "Asia-Pacific 65%"},
        ], [], [], None, [], presentation=presentation, report_tier="brief",
    )
    assert html.count('aria-label="Report key metrics"') == 0


def test_cover_owns_title_without_a_second_title_band():
    presentation = _presentation()
    presentation["cover"] = {"enabled": True, "treatment": "minimal", "show_date": True, "subtitle": "Strategic overview"}
    html = build_html_report(
        "## Findings\n\nEvidence here.", "Test Report", "Test", "Summary", [], [], [], None, [],
        presentation=presentation, report_tier="brief",
    )
    assert html.count('id="report-cover"') == 1
    assert html.count('id="report-title"') == 0


def test_brief_source_appendix_is_paged_as_a_single_terminal_unit():
    html = build_html_report(
        "## Findings\n\nEvidence here.", "Test Report", "Test", "Summary", [], [], [], None,
        [{"id": "S1", "title": "Source One", "url": "https://example.com", "publisher": "Example"}],
        presentation=_presentation(), report_tier="brief",
    )
    assert 'body.gg-tier--brief .gg-sources { margin-top:10px; padding:8px 10px; break-before:page !important; page-break-before:always !important; }' in html


def test_generic_report_wrapper_is_suppressed_when_body_has_real_heading():
    presentation = _presentation()
    presentation["sections"] = [
        {"id": "s1", "title": "Report", "section_type": "narrative", "layout": "single_column", "density": "standard", "emphasis": "normal", "order": 0, "blocks": []},
    ]
    html = build_html_report(
        "## Report\n\n### Executive Summary – Key Takeaways\n\nEvidence here.",
        "Test Report", "Test", "", [], [], [], None, [], presentation=presentation, report_tier="brief",
    )
    # The planner's generic wrapper should not add a second visible "Report" section heading.
    assert html.count('class="gg-section-heading"') == 0


def test_unicode_flow_diagram_is_rendered_as_atomic_svg():
    report = """## Regulation\n\n┌──────────────────────────────┐\n│ RBI Capital Market Exposure  │\n└──────────────┬───────────────┘\n               ↓\n┌──────────────────────────────┐\n│ Custodian Banks Issuing IPCs │\n└──────────────┬───────────────┘\n               ↓\n┌──────────────────────────────┐\n│ Intraday Risk at 30% of CME  │\n└──────────────────────────────┘\n"""
    html = build_html_report(report, "Banking", "Test", "", [], [], [], None, [], presentation=_presentation(), report_tier="brief")
    assert "gg-diagram-wrap" in html
    assert "gg-inline-diagram" in html
    assert "RBI Capital Market Exposure" in html
    assert "Custodian Banks Issuing IPCs" in html
    assert "Intraday Risk at 30% of CME" in html
    assert "┌──────────────────────────────┐" not in html


def test_fenced_flow_diagram_is_atomic_and_not_printed_as_raw_code():
    report = """## Regulation\n\n```text\n+----------------------+\n| Regulatory Framework |\n+----------------------+\n          ↓\n+----------------------+\n| Custodian Banks      |\n+----------------------+\n```\n"""
    html = build_html_report(report, "Banking", "Test", "", [], [], [], None, [], presentation=_presentation(), report_tier="brief")
    assert "gg-diagram-wrap" in html
    assert "gg-inline-diagram" in html
    assert "+----------------------+" not in html


def test_ascii_diagram_css_forces_atomic_printing():
    html = build_html_report("## Regulation\n\n+-----+\n| A   |\n+-----+\n↓\n+-----+\n| B   |\n+-----+", "Banking", "Test", "", [], [], [], None, [], presentation=_presentation(), report_tier="brief")
    assert ".gg-diagram-wrap" in html
    assert "break-inside: avoid" in html
    assert "page-break-inside: avoid" in html
