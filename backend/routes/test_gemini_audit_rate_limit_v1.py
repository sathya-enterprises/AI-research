from types import SimpleNamespace

import pytest

from routes import report


@pytest.mark.asyncio
async def test_audit_cools_rate_limited_key_before_hedging_another_model(monkeypatch):
    cooled = set()
    calls = 0

    def mark_rate_limited(identifier, _cooldown_ms):
        cooled.add(identifier)

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, *_args, **_kwargs):
            nonlocal calls
            calls += 1
            return SimpleNamespace(status_code=429)

    monkeypatch.setattr(report, "get_gemini_keys", lambda: ["test-key"])
    monkeypatch.setattr(report, "_is_rest_api_key", lambda _key: True)
    monkeypatch.setattr(report, "GEMINI_MODELS", ["model-a", "model-b"])
    monkeypatch.setattr(report, "mark_rate_limited", mark_rate_limited)
    monkeypatch.setattr(report, "is_rate_limited", lambda identifier: identifier in cooled)
    monkeypatch.setattr(report.httpx, "AsyncClient", FakeClient)
    monkeypatch.setenv("REPORT_PARALLEL_QUOTA_COOLDOWN_MS", "60000")

    result = await report._gemini_independent_report_audit(
        title="Test report",
        question="Test question",
        report_text="A short test report.",
        report_tier="detailed",
        charts=[],
        images=[],
        sources=[],
    )

    assert result["status"] == "unavailable"
    assert result["reason"] == "rate_limited"
    assert result["blocking"] is False
    assert calls == 1
    assert {"test-key", "report:gemini:test-key", "report:gemini:test-key:model-a"} <= cooled
