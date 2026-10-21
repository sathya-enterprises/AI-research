from routes.html_report import build_html_report


def test_sources_appendix_maps_canonical_ids_to_publisher_links():
    html = build_html_report(
        "## Outlook\n\nA concise evidence-backed paragraph.",
        title="Test", question="Test", summary="Summary", key_stats=[], charts=[], images=[],
        sources=[{
            "id": "S1", "title": "A Long Source Title", "publisher": "Publisher",
            "url": "https://example.com/report", "domain": "example.com", "source_type": "web"
        }], report_tier="brief"
    )
    appendix = html.split('id="data-sources"', 1)[1]
    assert "A Long Source Title" in appendix
    assert "example.com" in appendix
    assert "[S1]" in appendix
    assert 'id="source-S1"' in appendix
    assert "https://example.com/report" in appendix
    assert 'href="https://example.com/report"' in appendix


def test_prose_uses_full_width_single_column_layout():
    html = build_html_report(
        "## Outlook\n\nA long analytical paragraph with enough text to exercise the section body.",
        title="Test", question="Test", summary="Summary", key_stats=[], charts=[], images=[], sources=[],
        report_tier="detailed"
    )
    assert ".gg-section-body--two_column { columns: 1;" in html
    assert "text-align: justify" in html


def test_sources_appendix_shows_total_count_under_heading():
    html = build_html_report(
        "## Outlook\n\nEvidence-backed analysis with a relevant finding.",
        title="Test", question="Test", summary="Summary", key_stats=[], charts=[], images=[],
        sources=[
            {"id": "S1", "title": "One", "url": "https://example.com/one"},
            {"id": "S2", "title": "Two", "url": "https://example.com/two"},
            {"id": "S3", "title": "Duplicate URL", "url": "https://example.com/one/"},
        ], report_tier="brief"
    )
    appendix = html.split('id="data-sources"', 1)[1]
    assert "Total sources: 2" in appendix
    assert appendix.index("Data Sources") < appendix.index("Total sources: 2")


def test_complex_visuals_span_full_width_in_grid_sections():
    html = build_html_report(
        "## Outlook\n\nA long paragraph about the outlook.\n\n| Segment | Projected growth | Primary driver |\n|---|---:|---|\n| Retail banking | 11% | Deposits and digital adoption |",
        title="Test", question="Test", summary="Summary", key_stats=[], charts=[], images=[], sources=[],
        report_tier="detailed"
    )
    assert ".gg-section-body--grid > .gg-table-wrap" in html
    assert "grid-column: 1 / -1 !important" in html
    assert ".gg-table th, .gg-table td { overflow-wrap: break-word !important; word-break: normal !important;" in html


def test_unattributed_web_images_are_not_rendered():
    html = build_html_report(
        "## Outlook\n\n[WEB_IMG_1]",
        title="Test", question="Test", summary="Summary", key_stats=[], charts=[],
        images=[{"url": "https://cdn.example.com/chart.png", "caption": "A rising orange growth line trend on a graph shows the market."}],
        sources=[], report_tier="brief"
    )
    assert "External web image — publisher not identified" not in html
    assert "cdn.example.com/chart.png" not in html
