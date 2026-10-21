from pathlib import Path

PDF = Path(__file__).with_name('pdf.py')


def test_v7_brief_print_css_uses_editorial_single_column_flow():
    text = PDF.read_text(encoding='utf-8')
    assert 'V7 editorial pagination: Brief is an executive document' in text
    assert 'body.gg-tier--brief .gg-section-body--two_column' in text
    assert 'columns: 1 !important' in text
    assert 'break-inside: avoid !important' in text


def test_v7_prefers_browser_renderer_then_free_print_fallback():
    text = PDF.read_text(encoding='utf-8')
    browser = text.index('return _finalize(_pdf_with_playwright(html_doc), "Playwright Chromium")')
    weasy = text.index('return _finalize(_pdf_with_weasyprint(html_doc), "WeasyPrint")')
    assert browser < weasy
