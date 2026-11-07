from pathlib import Path

PDF = Path(__file__).with_name('pdf.py')


def test_v7_prefers_browser_renderer_then_free_print_fallback():
    text = PDF.read_text(encoding='utf-8')
    browser = text.index('return _finalize(_pdf_with_playwright(html_doc), "Playwright Chromium")')
    weasy = text.index('return _finalize(_pdf_with_weasyprint(html_doc), "WeasyPrint")')
    assert browser < weasy
