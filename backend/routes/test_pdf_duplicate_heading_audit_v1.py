"""Regression tests for false-positive duplicate numeric observations in PDF audit."""
from io import BytesIO

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from routes.pdf import _audit_rendered_pdf_bytes


def _text_pdf(lines):
    buffer = BytesIO()
    page = canvas.Canvas(buffer, pagesize=A4)
    y = 800
    for line in lines:
        page.drawString(45, y, line)
        y -= 22
    page.save()
    return buffer.getvalue()


def test_numbered_chart_headings_repeated_by_renderer_are_not_numeric_duplicates():
    lines = [
        "4. Chart – Deposit vs. Credit Growth",
        "This chart caption introduces the comparison of banking credit and deposits.",
        "4. Chart – Deposit vs. Credit Growth",
        "2. Retail-segment asset quality (historical benchmark)",
        "This heading describes a historical benchmark for retail asset quality.",
        "2. Retail-segment asset quality (historical benchmark)",
        "This report includes supporting discussion, source references and sufficient explanatory text.",
    ]
    report = "\n".join(lines[:5])
    result = _audit_rendered_pdf_bytes(
        _text_pdf(lines), report, "Banking report", charts=[], sources=[]
    )
    duplicate_findings = [
        item for item in result["findings"]
        if "repeats the same numeric observation" in str(item.get("issue", ""))
    ]
    assert duplicate_findings == []
    assert result["status"] == "PASS"


def test_real_duplicate_numeric_observation_still_fails_when_ordinals_differ():
    observation = "Credit growth reached 16.5% in June 2026 across scheduled commercial banks"
    lines = [
        "1. " + observation + " [S1]",
        "2. " + observation + " [S2]",
        "This report contains supporting context and enough text to validate the final PDF output.",
    ]
    report = "\n".join(lines)
    result = _audit_rendered_pdf_bytes(
        _text_pdf(lines), report, "Banking report", charts=[], sources=[
            {"id": "S1", "title": "RBI source one", "url": "https://example.com/one"},
            {"id": "S2", "title": "RBI source two", "url": "https://example.com/two"},
        ]
    )
    assert result["status"] == "FAIL"
    assert any(
        "repeats the same numeric observation" in str(item.get("issue", ""))
        for item in result["findings"]
    )
