"""Regression tests for V24 edge-balanced screen and print layouts."""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from routes.html_report import build_html_report
from routes.pdf import _strip_non_printing_runtime


def _sample_html():
    return build_html_report(
        "## Findings\n\nCredit growth remained strong while deposit mobilization lagged, creating funding pressure.\n\n"
        "## Risks\n\nFunding costs and concentrated credit exposure remain key risks.",
        "Banking Sector Outlook", "India banking outlook", "A concise executive summary.",
        [{"label": "Credit growth", "value": "15.9%"}], [], [],
        sources=[{"id": "S1", "title": "RBI publication", "url": "https://example.com/rbi-report"}],
        report_tier="detailed",
    )


def test_v24_interactive_report_uses_responsive_viewport_width_without_fixed_reading_measure():
    html = _sample_html()
    assert "V24 RESPONSIVE EDGE-BALANCED LAYOUT" in html
    assert "width: calc(100% - clamp(24px, 3.2vw, 56px)) !important" in html
    assert ".gg-section-body > p" in html
    assert "width: 100% !important; max-width: none !important; min-width: 0 !important" in html
    assert "grid-template-columns: repeat(auto-fit, minmax(min(100%, 340px), 1fr)) !important" in html
    assert "@media (prefers-reduced-motion: reduce)" in html


def test_v24_pdf_uses_more_printable_width_and_full_width_report_primitives():
    prepared = _strip_non_printing_runtime(_sample_html())
    assert 'id="gg-pdf-v23-premium"' in prepared
    assert "V24 print fit" in prepared
    assert "@page { size: A4; margin: 12mm 10mm 14mm 10mm; }" in prepared
    assert "main { padding: 10pt 0 16pt !important; }" in prepared
    assert ".gg-section-body > p { width: 100% !important; max-width: 100% !important;" in prepared


def test_v24_responsive_changes_keep_sources_count_and_clickable_url():
    html = _sample_html()
    appendix = html.split('id="data-sources"', 1)[1]
    assert "Total sources: 1" in appendix
    assert 'href="https://example.com/rbi-report"' in appendix
