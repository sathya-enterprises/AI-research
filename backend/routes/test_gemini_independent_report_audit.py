import json
import pytest

from routes import report as report_mod


@pytest.mark.asyncio
async def test_gemini_audit_returns_unavailable_without_key(monkeypatch):
    monkeypatch.setattr(report_mod, "get_gemini_keys", lambda: [])
    result = await report_mod._gemini_independent_report_audit(
        title="Test", question="Test report", report_text="# Test", report_tier="detailed",
        charts=[], images=[], sources=[])
    assert result["status"] == "unavailable"
    assert result["reason"] == "no_gemini_api_key"
    assert result["blocking"] is False


@pytest.mark.asyncio
async def test_gemini_audit_parses_structured_review(monkeypatch):
    monkeypatch.setattr(report_mod, "get_gemini_keys", lambda: ["test-key"])
    monkeypatch.setattr(report_mod, "GEMINI_MODELS", ["gemini-test-model"])

    class FakeResponse:
        status_code = 200
        is_success = True
        def json(self):
            return {"candidates": [{"content": {"parts": [{"text": json.dumps({
                "status": "FAIL", "summary": "Duplicate content found", "blocking": True,
                "critical_issues": ["Repeated table"],
                "findings": [{"severity": "critical", "category": "duplication", "location": "Page 4", "issue": "Repeated table", "recommended_fix": "Remove duplicate"}],
                "checks": {"duplication": "fail"}
            })}]}}]}

    class FakeClient:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def post(self, *args, **kwargs): return FakeResponse()

    monkeypatch.setattr(report_mod.httpx, "AsyncClient", FakeClient)
    result = await report_mod._gemini_independent_report_audit(
        title="Test", question="Test report", report_text="# Test", report_tier="detailed",
        charts=[], images=[], sources=[])
    assert result["status"] == "FAIL"
    assert result["blocking"] is True
    assert result["findings"][0]["issue"] == "Repeated table"
    assert result["provider"] == "Gemini API"


@pytest.mark.asyncio
async def test_gemini_audit_retries_rate_limit_and_falls_back(monkeypatch):
    monkeypatch.setattr(report_mod, "get_gemini_keys", lambda: ["test-key"])
    monkeypatch.setattr(report_mod, "GEMINI_MODELS", ["model-a", "model-b"])
    calls = []
    class FakeResponse:
        def __init__(self, status): self.status_code = status; self.is_success = status == 200
        def json(self):
            return {"candidates": [{"content": {"parts": [{"text": json.dumps({"status":"PASS","summary":"No critical defects","blocking":False,"findings":[],"checks":{}})}]}}]}
    class FakeClient:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def post(self, url, **kwargs):
            calls.append(url)
            return FakeResponse(429 if len(calls) == 1 else 200)
    monkeypatch.setattr(report_mod.httpx, "AsyncClient", FakeClient)
    result = await report_mod._gemini_independent_report_audit(
        title="Test", question="Test report", report_text="# Test", report_tier="detailed",
        charts=[], images=[], sources=[])
    assert result["status"] == "PASS"
    assert len(calls) == 2
