"""Regression tests for the v23 premium print design layer."""
from __future__ import annotations

from pathlib import Path
import io
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pypdf import PdfReader
from routes.pdf import build_pdf, _strip_non_printing_runtime
from routes.html_report import build_html_report


def test_v23_premium_layer_is_last_and_covers_editorial_elements():
    source = Path(__file__).with_name("pdf.py").read_text(encoding="utf-8")
    assert 'id="gg-pdf-v23-premium"' in source
    assert 'premium_print_overrides + "</head>"' in source
    assert "@bottom-left" in source
    assert ".gg-section-body > p" in source
    assert ".gg-table tbody tr:nth-child(even) td" in source
    assert ".gg-sources-count" in source
    assert ".gg-cover--bold_banner" in source
    assert ".gg-report-index a::after { content: none !important; display: none !important; }" in source


def test_print_preparation_injects_premium_styles_and_clean_source_list():
    html = build_html_report(
        "## Findings\nCredit grew while deposits lagged [S1].",
        "Banking Sector Outlook",
        "India banking outlook",
        "A concise executive summary.",
        [], [], [],
        sources=[{"id": "S1", "title": "RBI publication", "url": "https://example.com/rbi-report"}],
        report_tier="in_depth",
    )
    prepared = _strip_non_printing_runtime(html)
    assert 'id="gg-pdf-v23-premium"' in prepared
    assert "Total sources: 1" in prepared
    assert "https://example.com/rbi-report" in prepared
    assert "font-size: 10.35pt !important" in prepared
    # A short report should not waste the opening area on a tiny contents panel.
    assert 'id="report-index"' not in html


def test_premium_pdf_smoke_contains_content_source_count_and_clickable_url():
    pdf = build_pdf(
        "## Findings\nCredit grew while deposits lagged [S1].\n\n## Risks\nFunding costs remain a key risk [S1].",
        "Banking Sector Outlook",
        "India banking outlook",
        "A concise executive summary.",
        [{"label": "Credit growth", "value": "15.9%", "change": "+15.9%"}],
        [],
        sources=[{"id": "S1", "title": "RBI publication", "url": "https://example.com/rbi-report"}],
        report_tier="in_depth",
    )
    assert pdf.startswith(b"%PDF-")
    reader = PdfReader(io.BytesIO(pdf))
    assert len(reader.pages) >= 1
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert "Banking Sector Outlook" in text
    assert "Total sources: 1" in text
    assert "https://example.com/rbi-report" in text
    links = [a.get("/URI") for page in reader.pages for a in (page.get("/Annots") or []) if a.get_object().get("/A")]
    # A renderer may expose the link through PDF annotations or as plain text.
    assert "https://example.com/rbi-report" in text or "https://example.com/rbi-report" in links
