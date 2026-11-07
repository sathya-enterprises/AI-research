"""Regression: nested boilerplate containers must not crash HTML extraction.

Decomposing an outer boilerplate node leaves its descendants in the
find_all(True) list with attrs=None. Touching them raised AttributeError and
_extract_html returned "", so the page was recorded as an empty extraction
(10 of 14 such pages in the 2026-11-07 live runs were affected).
"""
from utils.source_extractor import _extract_html

HTML = """
<html><head><title>Sector Flow — NSE Sectoral Indices</title></head><body>
<div class="navigation"><div id="inner-nav" class="share-tools"><a href="/x">Home</a></div></div>
<div class="advertisement"><div class="ad-container"><span id="slot">Ad</span></div></div>
<main>
<h1>Sector Flow — NSE Sectoral Indices by Sector</h1>
<p>Financial services led the sectoral indices this week as banks rallied on strong credit growth data and foreign inflows.</p>
<table>
<tr><th>Sector</th><th>Index</th><th>Last</th><th>Change %</th></tr>
<tr><td>Fin Services</td><td>CNXFIN</td><td>27,283.95</td><td>+2.57%</td></tr>
<tr><td>Banks</td><td>NSEBANK</td><td>59,086.00</td><td>+2.27%</td></tr>
<tr><td>Pharma</td><td>CNXPHARMA</td><td>27,940.40</td><td>+1.21%</td></tr>
</table>
</main></body></html>
"""


def test_nested_boilerplate_containers_do_not_empty_the_page():
    text = _extract_html(HTML, 15000, True)
    assert text, "extraction must not fail on nested boilerplate containers"
    assert "Fin Services | CNXFIN | 27,283.95 | +2.57%" in text
    assert "Financial services led the sectoral indices" in text
    assert "Home" not in text  # boilerplate still removed
