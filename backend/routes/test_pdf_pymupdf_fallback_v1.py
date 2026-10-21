from __future__ import annotations

import builtins
import io
import types

from reportlab.pdfgen import canvas

from routes import pdf as pdf_mod


def _make_text_pdf(report: str) -> bytes:
    buffer = io.BytesIO()
    page = canvas.Canvas(buffer, pagesize=(612, 792))
    text = page.beginText(44, 744)
    text.setFont("Helvetica", 10)
    for paragraph in report.splitlines():
        for start in range(0, len(paragraph), 88):
            text.textLine(paragraph[start:start + 88])
    page.drawText(text)
    page.save()
    return buffer.getvalue()


def test_final_pdf_audit_uses_pypdf_when_pymupdf_is_unavailable(monkeypatch):
    report = (
        "India banking outlook summary: this page contains substantive report text for validating "
        "the final rendered PDF. The audit must be able to reopen the file and inspect the visible "
        "text even when PyMuPDF is missing from the active Python environment. No numeric claims "
        "or chart values are required for this dependency-fallback regression test."
    )
    pdf_bytes = _make_text_pdf(report)
    original_import = builtins.__import__

    def block_pymupdf(name, globals=None, locals=None, fromlist=(), level=0):
        if name in {"pymupdf", "fitz"}:
            raise ModuleNotFoundError(f"No module named '{name}'", name=name)
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", block_pymupdf)
    result = pdf_mod._audit_rendered_pdf_bytes(
        pdf_bytes=pdf_bytes, report=report, title="PDF fallback test", charts=[], sources=[],
    )

    assert result["validationEngine"] == "pypdf-fallback"
    assert result["pageCount"] == 1
    assert result["extractedTextChars"] >= 120
    assert result["status"] == "PASS"
    assert result["blocking"] is False


def test_backend_requirements_declare_pymupdf():
    from pathlib import Path

    requirements = Path(__file__).resolve().parents[1] / "requirements.txt"
    rows = requirements.read_text(encoding="utf-8").splitlines()
    assert any(row.lower().startswith("pymupdf") for row in rows)


def test_final_pdf_audit_falls_back_when_pymupdf_imports_but_open_fails(monkeypatch):
    report = (
        "The report has enough extractable content to validate a rendered PDF. "
        "A PyMuPDF module can import successfully yet fail while opening the document "
        "when a legacy fitz dependency is missing. In that case pypdf must still run "
        "the publication checks instead of returning a dependency-related 422."
    )
    pdf_bytes = _make_text_pdf(report)
    fake_pymupdf = types.ModuleType("pymupdf")

    def broken_open(*_args, **_kwargs):
        raise ModuleNotFoundError("No module named 'fitz'", name="fitz")

    fake_pymupdf.open = broken_open
    original_import = builtins.__import__

    def broken_pymupdf_open(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "pymupdf":
            return fake_pymupdf
        if name == "fitz":
            raise ModuleNotFoundError("No module named 'fitz'", name="fitz")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", broken_pymupdf_open)
    result = pdf_mod._audit_rendered_pdf_bytes(
        pdf_bytes=pdf_bytes, report=report, title="PDF open fallback test", charts=[], sources=[],
    )
    assert result["validationEngine"] == "pypdf-fallback"
    assert result["pageCount"] == 1
    assert result["status"] == "PASS"
    assert result["blocking"] is False
