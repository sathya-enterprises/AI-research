import io

import pypdf
from routes import pdf
from routes.report import _rendered_page_count_preflight


class _FakeReader:
    def __init__(self, _stream):
        self.pages = [object() for _ in range(23)]


def test_page_preflight_uses_same_build_pdf_renderer(monkeypatch):
    calls = []
    def fake_build_pdf(**kwargs):
        calls.append(kwargs)
        return b"fake-pdf-bytes"

    monkeypatch.setattr(pdf, "build_pdf", fake_build_pdf)
    monkeypatch.setattr(pypdf, "PdfReader", _FakeReader)
    result = _rendered_page_count_preflight(
        "## Findings\n\nEvidence paragraph.", "Title", "Question", "Summary", [], [], [], {}, "detailed",
        web_images=[{"url": "https://example.test/a.png"}],
        file_images=[{"name": "chart.png", "data": "Zm9v"}],
        theme={"primaryColor": "#17384F"},
    )
    assert result == 23
    assert len(calls) == 1
    assert calls[0]["report_tier"] == "detailed"
    assert calls[0]["web_images"][0]["url"] == "https://example.test/a.png"
    assert calls[0]["file_images"][0]["name"] == "chart.png"
    assert calls[0]["theme"]["primaryColor"] == "#17384F"


def test_pdf_preflight_output_is_not_built_from_weasyprint_first(monkeypatch):
    calls = []
    monkeypatch.setattr(pdf, "build_pdf", lambda **kwargs: calls.append("browser-build") or b"pdf")
    monkeypatch.setattr(pypdf, "PdfReader", _FakeReader)
    assert _rendered_page_count_preflight("body", "T", "Q", "", [], [], [], {}, "detailed") == 23
    assert calls == ["browser-build"]


def test_preflight_does_not_use_different_renderer_when_export_builder_fails(monkeypatch):
    monkeypatch.setattr(pdf, "build_pdf", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("renderer unavailable")))
    result = _rendered_page_count_preflight("body", "T", "Q", "", [], [], [], {}, "detailed")
    assert result is None


def test_accurate_over_max_page_count_does_not_trigger_more_expansion(monkeypatch):
    import asyncio
    from routes import report as report_module

    async def unexpected_expansion(**kwargs):
        raise AssertionError("the exporter already reports an overlong PDF; do not expand it")

    monkeypatch.setattr(report_module, "_rendered_page_count_preflight", lambda *args, **kwargs: 51)
    monkeypatch.setattr(report_module, "_expand_report_to_tier", unexpected_expansion)
    body, pages = asyncio.run(report_module._ensure_report_page_budget(
        report_text="## Findings\n\nShort, non-repeated content.", question="Test",
        report_tier="detailed", report_plan=None, evidence_text="", title="Test",
        summary="", key_stats=[], charts=[], sources=[], presentation={},
    ))
    assert pages == 51
    assert "Short, non-repeated content." in body
