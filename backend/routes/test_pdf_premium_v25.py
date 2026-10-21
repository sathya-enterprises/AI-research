"""Regression checks for premium print finishing and KPI colour treatment."""
from __future__ import annotations

import io
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pypdf import PdfReader
from routes.html_report import build_html_report, _render_key_stats
from routes.pdf import _strip_non_printing_runtime


def _sample_stats():
    return [
        {"label": "Credit growth", "value": "15.9%", "change": "+2.1 pp"},
        {"label": "Deposit growth", "value": "13.5%", "change": "+0.8 pp"},
        {"label": "Gross NPA", "value": "2.2%", "change": "-0.4 pp"},
        {"label": "Net profit", "value": "₹1.98 lakh Cr"},
        {"label": "Market CAGR", "value": "9.7%"},
        {"label": "Capital ratio", "value": "14.8%"},
    ]


def test_v25_print_finish_has_heading_keep_with_content_and_table_flow_rules():
    source = Path(__file__).with_name("pdf.py").read_text(encoding="utf-8")
    assert 'id="gg-pdf-v25-finish"' in source
    assert ".gg-section-heading + .gg-section-body > :first-child" in source
    assert ".gg-section-h3 + p" in source
    assert ".gg-table-wrap, .gg-table-scroll { break-inside: auto !important" in source
    assert ".gg-table thead { display: table-header-group !important; }" in source
    assert "print-color-adjust: exact !important" in source
    assert '.gg-sources[data-source-placement="end_of_report"]' in source
    assert ".gg-report-sections > .gg-section:nth-child(6n + 4) .gg-section-index" in source


def test_kpi_cards_get_distinct_colour_variables_in_html_and_pdf():
    html_doc = build_html_report(
        "## Growth outlook\nCredit is expanding while deposit growth trails [S1].\n\n## Risks\nFunding costs remain elevated [S1].",
        "India Banking Outlook",
        "India banking sector",
        "Evidence-led summary.",
        _sample_stats(), [], [],
        sources=[{"id": "S1", "title": "RBI data", "url": "https://example.com/rbi"}],
        report_tier="in_depth",
    )
    stat_html = _render_key_stats(_sample_stats(), [])
    assert "--kpi-tint:#EAF2FF" in stat_html
    assert "--kpi-tint:#E6F6EF" in stat_html
    assert "--kpi-tint:#FFF3D9" in stat_html
    assert "--kpi-accent:#7654B8" in stat_html
    assert 'data-source-count="1"' in html_doc
    prepared = _strip_non_printing_runtime(html_doc)
    assert 'id="gg-pdf-v25-finish"' in prepared
    assert "#EAF2FF" in prepared


def test_v25_pdf_smoke_preserves_headings_and_url_only_sources():
    content = (
        "## Credit and deposit dynamics\n"
        "Credit demand has outpaced deposit accumulation, creating funding pressure [S1].\n\n"
        "### What changes next\n"
        "Banks may respond through stronger retail deposit mobilisation and selective pricing [S1].\n\n"
        "| Metric | Current | Outlook |\n|---|---:|---:|\n"
        "| Credit growth | 15.9% | 11.5% |\n| Deposit growth | 13.5% | 13.8% |\n"
    )
    html_doc = build_html_report(
        content,
        "India Banking Outlook",
        "India banking sector",
        "An evidence-led view of growth, profitability and key risks.",
        _sample_stats(), [], [],
        sources=[{"id": "S1", "title": "RBI data", "url": "https://example.com/rbi"}],
        report_tier="in_depth",
    )
    prepared = _strip_non_printing_runtime(html_doc)
    from weasyprint import HTML
    pdf = HTML(string=prepared, base_url=str(ROOT)).write_pdf()
    assert pdf.startswith(b"%PDF-")
    reader = PdfReader(io.BytesIO(pdf))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert "Credit and deposit dynamics" in text
    assert "What changes next" in text
    assert "Total sources: 1" in text
    assert "https://example.com/rbi" in text
    assert len(reader.pages) <= 1, "a compact sample should keep its URL-only source appendix on the same page when space is available"
