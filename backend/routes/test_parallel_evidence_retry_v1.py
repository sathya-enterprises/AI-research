import json
import pytest

from routes import report as report_mod
from utils import keys as key_pool_mod


@pytest.fixture
def isolated_provider_key_pool():
    """Keep this retry test independent of reservations made by earlier tests."""
    state_names = ("_pool_key_leases", "_pool_key_last_reserved", "_usage_count", "_rate_limited_until")
    with key_pool_mod._lock:
        previous = {name: dict(getattr(key_pool_mod, name)) for name in state_names}
        for name in state_names:
            getattr(key_pool_mod, name).clear()
    try:
        yield
    finally:
        with key_pool_mod._lock:
            for name in state_names:
                current = getattr(key_pool_mod, name)
                current.clear()
                current.update(previous[name])

SOURCE_TEXT = (
    "- [S1] **Official deposit statistics**\n"
    "Source: https://example.gov/deposits\n"
    "DIRECT PUBLISHER PAGE TEXT:\nDeposits grew by 12% in 2025. The measure refers to scheduled commercial banks.\n"
    "\n---\n\n"
    "- [S2] **Official credit statistics**\n"
    "Source: https://example.gov/credit\n"
    "DIRECT PUBLISHER PAGE TEXT:\nBank credit grew by 10% in 2025.\n"
)


@pytest.mark.asyncio
async def test_parallel_analyst_retries_with_spare_key_after_transient_connect_timeout(monkeypatch, isolated_provider_key_pool):
    analyst_json = json.dumps({
        "findings": [{
            "claim": "Deposits grew by 12% in 2025.",
            "source_ids": ["S1"],
            "evidence": [{"source_id": "S1", "quote": "Deposits grew by 12% in 2025."}],
            "metric": "deposit growth", "period": "2025", "unit": "%", "kind": "finding",
        }],
        "gaps": [],
    })
    attempts = []

    async def fake_gemini(prompt, tier, key, model):
        attempts.append((key, model))
        if key == "AQ.test-key-1":
            return "", model, "ConnectTimeout"
        return analyst_json, model, "success"

    async def fake_groq(question, tier, prompt):
        return analyst_json, "groq:test-model"

    monkeypatch.setattr(report_mod, "get_gemini_keys", lambda: [f"AQ.test-key-{i}" for i in range(1, 9)])
    monkeypatch.setattr(report_mod, "get_groq_keys", lambda: ["groq-key"])
    monkeypatch.setattr(report_mod, "GEMINI_MODELS", ["model-1", "model-2", "model-3", "model-4"])
    monkeypatch.setattr(report_mod, "is_rate_limited", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(report_mod, "_parallel_gemini_analyst_once", fake_gemini)
    monkeypatch.setattr(report_mod, "_parallel_groq_analyst", fake_groq)
    monkeypatch.setattr(report_mod, "record_research_stage", lambda *_args, **_kwargs: None)

    result = await report_mod.run_parallel_evidence_analysis(
        question="Assess deposit growth outlook.",
        report_tier="brief",
        source_text=SOURCE_TEXT,
        report_plan={"sections": []},
    )
    assert ("AQ.test-key-1", "model-1") in attempts
    assert ("AQ.test-key-5", "model-2") in attempts
    assert result["successful_analysts"] == 5
    assert result["findings"][0]["source_ids"] == ["S1"]
