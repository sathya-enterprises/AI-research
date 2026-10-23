from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, Path(__file__).resolve().parents[1].as_posix())


def _request(body):
    from starlette.requests import Request

    raw = json.dumps(body).encode("utf-8")

    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}

    return Request({"type": "http", "method": "POST", "path": "/", "headers": []}, receive)


def test_draft_pdf_preserves_charts_and_downloads_with_non_structural_audit_findings(monkeypatch):
    from routes import pdf as pdf_route

    chart = {
        "type": "bar", "title": "Bank comparison", "unit": "INR crore", "source": ["S1"],
        "series": [{"name": "Profit", "data": [{"label": "Bank A", "value": 100}, {"label": "Bank B", "value": 120}]}],
    }
    captured = {}

    def fake_build_pdf(report, title, question, summary, key_stats, charts, logo_b64,
                       file_images, web_images, theme, sources, presentation, report_tier="detailed", **_style):
        captured["report"] = report
        captured["charts"] = charts
        return b"%PDF-1.4\nmock-pdf-bytes"

    monkeypatch.setattr(pdf_route, "build_pdf", fake_build_pdf)
    monkeypatch.setattr(pdf_route, "_trim_trailing_blank_pages", lambda data: data)
    monkeypatch.setattr(pdf_route, "_trim_furniture_only_middle_pages", lambda data: data)
    monkeypatch.setattr(pdf_route, "sanitize_charts_for_publication", lambda *a, **k: (_ for _ in ()).throw(AssertionError("draft export must preserve charts")))
    monkeypatch.setattr(pdf_route, "_audit_rendered_pdf_bytes", lambda *a, **k: {
        "status": "FAIL", "blocking": True, "pageCount": 1, "extractedTextChars": 300,
        "findings": [{"severity": "critical", "issue": "Chart value lacks publisher-page evidence."}],
    })

    response = asyncio.run(pdf_route.generate_pdf(_request({
        "report": "## Banking overview\n\nSee [CHART_1].\n\nThe report reviews bank profitability and asset quality with cited sources [S1].",
        "title": "Banking overview", "question": "Banking overview", "charts": [chart],
        "sources": [{"id": "S1", "title": "Source", "url": "https://example.com", "evidenceExcerpt": "bank data"}],
        "draftExport": True,
    })))

    assert response.status_code == 200
    assert response.media_type == "application/pdf"
    assert response.body.startswith(b"%PDF-")
    assert captured["charts"] == [chart]
    assert "DRAFT — NOT APPROVED FOR EXTERNAL PUBLICATION" in captured["report"]


def test_draft_pdf_still_blocks_structural_pdf_failure(monkeypatch):
    from routes import pdf as pdf_route

    monkeypatch.setattr(pdf_route, "build_pdf", lambda *a, **k: b"%PDF-1.4\nmock-pdf-bytes")
    monkeypatch.setattr(pdf_route, "_trim_trailing_blank_pages", lambda data: data)
    monkeypatch.setattr(pdf_route, "_trim_furniture_only_middle_pages", lambda data: data)
    monkeypatch.setattr(pdf_route, "_audit_rendered_pdf_bytes", lambda *a, **k: {
        "status": "FAIL", "blocking": True, "pageCount": 0, "extractedTextChars": 0,
        "findings": [{"severity": "critical", "issue": "The final PDF could not be reopened for validation."}],
    })

    response = asyncio.run(pdf_route.generate_pdf(_request({
        "report": "A useful report body with enough words to render.", "title": "Draft",
        "question": "Draft", "charts": [], "sources": [], "draftExport": True,
    })))
    assert response.status_code == 422
    payload = json.loads(response.body)
    assert payload["exported"] is False
