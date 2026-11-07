from routes.html_report import build_html_report
from utils.report_tiers import get_report_tier_spec

SOURCES = [
    {"title": "Primary EV data", "url": "https://example.com/ev", "snippet": "data"},
    {"title": "Battery prices", "url": "https://example.com/battery", "snippet": "data"},
]


def test_external_image_uses_direct_source_link_not_s_id():
    report = "## Findings\n\n[WEB_IMG_1]\n"
    images = [{
        "url": "https://cdn.example.com/figure.png",
        "sourceUrl": "https://example.com/original",
        "sourceTitle": "Original source",
        "caption": "Evidence figure",
    }]
    html = build_html_report(report, "EV", "EV", "", [], [], images, sources=SOURCES, report_tier="detailed")
    assert "https://example.com/original" in html
    assert "Original source" in html
    assert "data-source-id=\"S3\"" not in html


def test_visual_source_does_not_create_or_validate_model_s27_citation(monkeypatch):
    # Stage E: inline [S#] links are the audit-copy mode (REPORT_INLINE_CITATIONS=1); this test checks that mode.
    monkeypatch.setenv("REPORT_INLINE_CITATIONS", "1")
    report = "## Findings\nEvidence [S1] and bad visual citation [S27].\n\n[WEB_IMG_1]"
    images = [{
        "url": "https://cdn.example.com/figure.png",
        "sourceUrl": "https://example.com/original",
        "sourceTitle": "Original source",
    }]
    html = build_html_report(report, "EV", "EV", "", [], [], images, sources=SOURCES, report_tier="detailed")
    # S27 remains explicitly unresolved in the appendix; the image attribution
    # must not silently create a verified record for that unrelated citation.
    assert 'source-S27' in html and 'Source record missing for [S27]' in html
    assert 'href="#source-S27"' in html  # citation links only to the unresolved warning
    assert 'href="https://example.com/original"' in html
    assert 'https://cdn.example.com/figure.png" target=' not in html


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
