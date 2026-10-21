from pathlib import Path

import pytest

from routes import report as report_mod
from utils import keys


@pytest.mark.asyncio
async def test_report_writer_tries_gemini_then_groq_then_openrouter(monkeypatch):
    calls = []

    async def no_gemini(*_args, **_kwargs):
        calls.append("gemini")
        return "", ""

    async def no_groq(*_args, **_kwargs):
        calls.append("groq")
        return "", ""

    async def openrouter(*_args, **_kwargs):
        calls.append("openrouter")
        return '{"report":"evidence-backed report"}', "openrouter:test-model:fallback"

    monkeypatch.setattr(report_mod, "call_gemini", no_gemini)
    monkeypatch.setattr(report_mod, "_call_groq_report_fallback", no_groq)
    monkeypatch.setattr(report_mod, "_call_openrouter_report_fallback", openrouter)

    raw, model = await report_mod.call_report_writer("Research a current market question", "brief")

    assert calls == ["gemini", "groq", "openrouter"]
    assert "evidence-backed report" in raw
    assert model.startswith("openrouter:")


@pytest.mark.asyncio
async def test_all_provider_failures_use_supplied_evidence_fallback(monkeypatch):
    async def unavailable(*_args, **_kwargs):
        return "", ""

    monkeypatch.setattr(report_mod, "call_gemini", unavailable)
    monkeypatch.setattr(report_mod, "_call_groq_report_fallback", unavailable)
    monkeypatch.setattr(report_mod, "_call_openrouter_report_fallback", unavailable)
    source = {
        "id": "S1", "title": "Official capacity data", "url": "https://example.gov/capacity",
        "snippet": "Installed capacity reached 100 GW in 2026.",
        "fullContent": "Installed renewable capacity reached 100 GW in 2026.",
    }

    raw, model = await report_mod.call_report_writer(
        "Research India's renewable capacity outlook",
        "brief",
        report_plan={"sections": [{"heading": "Capacity outlook", "purpose": "Summarize source evidence."}]},
        fallback_sources=[source],
    )

    assert model == "local:evidence-fallback"
    assert "100 GW" in raw
    assert "[S1]" in raw


def test_provider_key_pools_parse_and_deduplicate_multiple_configured_keys(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEYS", "groq-one; groq-two\ngroq-one")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-one,gemini-two")
    monkeypatch.setenv("SERPER_API_KEY", "serper-one,serper-two")
    monkeypatch.setenv("SERPER_API_KEYS", "serper-two; serper-three")

    assert keys.get_groq_keys() == ["groq-one", "groq-two", "groq-one"]
    assert keys.get_gemini_keys() == ["gemini-one", "gemini-two"]
    assert keys.get_serper_keys() == ["serper-one", "serper-two", "serper-three"]


def test_frontend_report_error_keeps_the_existing_message_object():
    component = (Path(__file__).resolve().parents[2] / "frontend/src/components/GrowthGradualChat.tsx").read_text(encoding="utf-8")

    assert "reportError: message" in component
    assert "reportLoading: false, reportError: message, reportPreview: undefined" in component
    assert "The Action could not be completed. Your existing report was preserved" in component
