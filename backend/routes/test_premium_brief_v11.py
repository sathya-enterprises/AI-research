from routes.html_report import build_html_report
from routes.report import _enforce_brief_section_cap
from utils.report_tiers import get_report_tier_spec

SOURCES = [
    {"title": "Primary EV data", "url": "https://example.com/ev", "snippet": "data"},
    {"title": "Battery prices", "url": "https://example.com/battery", "snippet": "data"},
]


def test_brief_tier_is_compact():
    spec = get_report_tier_spec("brief")
    assert spec.research_breadth == "focused"
    assert spec.evidence_intensity == "selective"
    assert "decision-oriented" in spec.objective
    assert spec.page_target.minimum is None
    assert spec.page_target.target is None
    assert spec.page_target.maximum == 8


def test_brief_preserves_planner_and_writer_sections_without_a_hard_cap():
    report = (
        "## Executive Findings\nA\n\n"
        "## Market Trend\nB\n\n"
        "## Competitive Landscape\nC\n\n"
        "## Risks\nD\n\n"
        "## Methodology\nE\n"
    )
    plan = {"sections": [
        {"heading": "Executive Findings"},
        {"heading": "Market Trend"},
        {"heading": "Competitive Landscape"},
        {"heading": "Risks"},
    ]}
    out = _enforce_brief_section_cap(report, plan, 4)
    assert out == report
    assert "## Methodology" in out
    assert out.count("## ") == 5


def test_external_image_uses_direct_source_link_not_s_id():
    report = "## Findings\n\n[WEB_IMG_1]\n"
    images = [{
        "url": "https://cdn.example.com/figure.png",
        "sourceUrl": "https://example.com/original",
        "sourceTitle": "Original source",
        "caption": "Evidence figure",
    }]
    html = build_html_report(report, "EV", "EV", "", [], [], images, sources=SOURCES, report_tier="brief")
    assert "https://example.com/original" in html
    assert "Original source" in html
    assert "data-source-id=\"S3\"" not in html


def test_brief_sources_start_new_print_page():
    html = build_html_report("## Findings\nEvidence [S1]", "EV", "EV", "", [], [], [], sources=SOURCES, report_tier="brief")
    assert 'body.gg-tier--brief .gg-sources' in html
    assert 'page-break-before:always' in html or 'break-before:page' in html


def test_visual_source_does_not_create_or_validate_model_s27_citation():
    report = "## Findings\nEvidence [S1] and bad visual citation [S27].\n\n[WEB_IMG_1]"
    images = [{
        "url": "https://cdn.example.com/figure.png",
        "sourceUrl": "https://example.com/original",
        "sourceTitle": "Original source",
    }]
    html = build_html_report(report, "EV", "EV", "", [], [], images, sources=SOURCES, report_tier="brief")
    # S27 remains explicitly unresolved in the appendix; the image attribution
    # must not silently create a verified record for that unrelated citation.
    assert 'source-S27' in html and 'Source record missing for [S27]' in html
    assert 'href="#source-S27"' in html  # citation links only to the unresolved warning
    assert 'href="https://example.com/original"' in html
    assert 'https://cdn.example.com/figure.png" target=' not in html


def test_brief_page_target_matches_ui_contract():
    spec = get_report_tier_spec("brief")
    assert spec.page_target.minimum is None
    assert spec.page_target.target is None
    assert spec.page_target.maximum == 8


def test_brief_render_limits_structured_metrics_to_four():
    report = "## Findings\nEvidence [S1]"
    presentation = {"sections": [{"title": "Findings", "order": 1, "section_type": "metrics_dashboard", "blocks": [{"kind": "metrics", "items": [
        {"label": "One", "value": "1"}, {"label": "Two", "value": "2"}, {"label": "Three", "value": "3"},
        {"label": "Four", "value": "4"}, {"label": "Five", "value": "5"},
    ]}]}]}
    html = build_html_report(report, "Test", "Test", "", [], [], [], sources=SOURCES, presentation=presentation, report_tier="brief")
    assert html.count('class="gg-metric"') <= 4
    assert html.count('class="gg-stat-card') <= 4


def test_image_asset_url_is_never_used_as_source_link():
    from routes.html_report import _render_image_block
    html_out = _render_image_block(
        {'url': 'https://cdn.example.com/assets/chart.png', 'caption': 'Example chart'},
        1,
        source_registry=[],
    )
    assert 'href="https://cdn.example.com/assets/chart.png"' not in html_out
    # Unknown image hosts must not create a fabricated publisher/caption.
    assert 'External web image — publisher not identified' not in html_out
