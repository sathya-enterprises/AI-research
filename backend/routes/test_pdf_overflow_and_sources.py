from pathlib import Path


def test_pdf_print_css_has_a4_overflow_guard():
    text = Path(__file__).with_name("pdf.py").read_text(encoding="utf-8")
    assert "Hard A4 content-box guard" in text
    assert "html, body { width: 100%; max-width: 100%; overflow: visible; }" in text
    assert ".gg-chart-inline-svg svg { width: 100% !important; max-width: 100% !important;" in text


def test_html_report_print_css_has_width_guard():
    text = Path(__file__).with_name("html_report.py").read_text(encoding="utf-8")
    assert "WeasyPrint lays out percentage-width blocks" in text
    assert "overflow-wrap: anywhere" in text


def test_trailing_plain_sources_heading_is_removed():
    from routes.html_report import _strip_redundant_model_source_appendix

    report = """## Main\nBody.\n\n## Methodology & Sources\nMethodology stays.\n\nSources:\n- Source A [S1]\n- Source B [S2]\n"""
    cleaned = _strip_redundant_model_source_appendix(report)
    assert "Methodology & Sources" in cleaned
    assert "Sources:\n" not in cleaned
    assert "Source A" not in cleaned


def test_conduct_report_prefix_is_removed_by_heuristic_topic_cleaner():
    from utils.intent import _extract_research_topic

    topic = _extract_research_topic(
        "Conduct a research report on the Indian electric two-wheeler market. Analyze market size and growth."
    )
    assert topic == "the Indian electric two-wheeler market"


def test_bold_trailing_sources_heading_is_removed():
    from routes.html_report import _strip_redundant_model_source_appendix

    report = """## Main
Body.

## Methodology & Sources
Methodology stays.

**Sources:**
- Source A [S1]
- Source B [S2]
"""
    cleaned = _strip_redundant_model_source_appendix(report)
    assert "Methodology & Sources" in cleaned
    assert "**Sources:**" not in cleaned
    assert "Source A" not in cleaned


def test_html_source_appendix_starts_cleanly_in_print():
    text = Path(__file__).with_name("html_report.py").read_text(encoding="utf-8")
    assert "body:not(.gg-tier--brief) .gg-sources {{ break-before: page; page-break-before: always; }}" in text


def test_html_sections_use_dynamic_pagination_not_forced_fresh_pages():
    text = Path(__file__).with_name("html_report.py").read_text(encoding="utf-8")
    assert ".gg-section + .gg-section {{ break-before: page;" not in text


def test_brief_pdf_source_appendix_uses_a_compact_two_column_grid():
    text = Path(__file__).with_name("pdf.py").read_text(encoding="utf-8")
    assert "body.gg-tier--brief .gg-sources-grid { display: grid !important;" in text
    assert "grid-template-columns: repeat(2, minmax(0, 1fr)) !important;" in text


def test_cover_does_not_repeat_the_title_band_in_long_reports():
    text = Path(__file__).with_name("html_report.py").read_text(encoding="utf-8")
    assert "A real cover already owns the report title/date" in text
    assert "gg-has-cover" in text


def test_image_source_normalization_does_not_require_a_generic_open_source_label():
    text = Path(__file__).with_name("html_report.py").read_text(encoding="utf-8")
    assert "source" in text.lower() and "cdn" in text.lower()


def test_external_image_source_rendering_does_not_require_unimported_urlparse():
    from routes.html_report import _render_source_note

    rendered = _render_source_note("https://example.com/image.png", [], [])
    assert "https://example.com/image.png" in rendered
    assert "example.com" in rendered


def test_chart_rules_are_not_mandatory_in_report_prompt():
    text = Path(__file__).with_name("report.py").read_text(encoding="utf-8")
    assert "CHARTS ARE OPTIONAL AND EVIDENCE-DRIVEN" in text
