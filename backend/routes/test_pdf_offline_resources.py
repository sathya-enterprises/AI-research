"""Current PDF print-preprocessing regressions (the old strict-offline helper was retired)."""
from routes.pdf import _strip_non_printing_runtime


def test_pdf_preprocessor_compiles_print_css_and_normalizes_wide_screen_layouts():
    browser_html = '''<!doctype html><html><head><style>
    @media screen { .hero { display: sticky; width: 100vw; overflow-x: scroll; } }
    .wide { width: 100vw; }
    </style></head><body><h1>Offline PDF</h1><div class="wide">Content</div></body></html>'''
    compiled = _strip_non_printing_runtime(browser_html)
    low = compiled.lower()
    assert 'id="gg-pdf-print-overrides"' in low
    assert "100vw" not in low
    assert "@media screen" not in low
    assert "offline pdf" in low
