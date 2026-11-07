import asyncio
import json

import pytest

from routes import report as report_mod


SOURCE_TEXT = (
    "- [S1] **Official deposit statistics**\n"
    "Source: https://example.gov/deposits\n"
    "DIRECT PUBLISHER PAGE TEXT:\nDeposits grew by 12% in 2025. The measure refers to scheduled commercial banks.\n"
    "\n---\n\n"
    "- [S2] **Official credit statistics**\n"
    "Source: https://example.gov/credit\n"
    "DIRECT PUBLISHER PAGE TEXT:\nBank credit grew by 10% in 2025.\n"
)


def test_parallel_source_blocks_preserve_ids_and_exact_evidence():
    blocks = report_mod._parallel_source_blocks(SOURCE_TEXT)
    assert list(blocks) == ["S1", "S2"]
    assert "Deposits grew by 12% in 2025." in blocks["S1"]["evidence"]
    assert blocks["S1"]["url"] == "https://example.gov/deposits"


def test_parallel_context_balances_later_source_ids_and_engine_observations():
    blocks = report_mod._parallel_source_blocks(SOURCE_TEXT)
    context = report_mod._parallel_evidence_context(blocks, "VALIDATED OBSERVATION: 12% in 2025", 5000)
    assert "[S1]" in context and "[S2]" in context
    assert "Deposits grew by 12% in 2025." in context
    assert "Bank credit grew by 10% in 2025." in context
    assert "VALIDATED EVIDENCE ENGINE EXTRACT" in context


def test_parallel_analyst_result_keeps_only_allowed_source_quoted_numeric_claims():
    blocks = report_mod._parallel_source_blocks(SOURCE_TEXT)
    raw = json.dumps({
        "findings": [
            {
                "claim": "Deposits grew by 12% in 2025.",
                "source_ids": ["S1"],
                "evidence": [{"source_id": "S1", "quote": "Deposits grew by 12% in 2025."}],
                "metric": "deposit growth", "period": "2025", "unit": "%", "kind": "finding",
            },
            {
                "claim": "Deposits grew by 15% in 2025.",
                "source_ids": ["S1"],
                "evidence": [{"source_id": "S1", "quote": "Deposits grew by 12% in 2025."}],
            },
            {
                "claim": "A figure is backed by an invented source.",
                "source_ids": ["S99"],
                "evidence": [{"source_id": "S99", "quote": "Deposits grew by 12% in 2025."}],
            },
            {
                "claim": "This quote is not in the supplied source.",
                "source_ids": ["S1"],
                "evidence": [{"source_id": "S1", "quote": "An invented publisher statement."}],
            },
        ],
        "gaps": [],
    })
    result = report_mod._validate_parallel_analyst_result("test", raw, blocks)
    assert result["status"] == "ok"
    assert len(result["findings"]) == 1
    assert result["findings"][0]["source_ids"] == ["S1"]


@pytest.mark.asyncio
async def test_parallel_analysts_run_concurrently_and_merge_duplicate_findings(monkeypatch):
    source_blocks = report_mod._parallel_source_blocks(SOURCE_TEXT)
    analyst_json = json.dumps({
        "findings": [{
            "claim": "Deposits grew by 12% in 2025.",
            "source_ids": ["S1"],
            "evidence": [{"source_id": "S1", "quote": "Deposits grew by 12% in 2025."}],
            "metric": "deposit growth", "period": "2025", "unit": "%", "kind": "finding",
        }],
        "gaps": [],
    })
    active = 0
    max_active = 0

    async def fake_gemini(prompt, tier, key, model):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        return analyst_json, model, "success"

    async def fake_groq(question, tier, prompt):
        await asyncio.sleep(0.01)
        return analyst_json, "groq:test-model"

    monkeypatch.setattr(report_mod, "get_gemini_keys", lambda: [f"AQ.test-key-{i}" for i in range(1, 5)])
    monkeypatch.setattr(report_mod, "get_groq_keys", lambda: ["groq-key"])
    monkeypatch.setattr(report_mod, "GEMINI_MODELS", ["model-1", "model-2", "model-3", "model-4"])
    monkeypatch.setattr(report_mod, "is_rate_limited", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(report_mod, "_parallel_gemini_analyst_once", fake_gemini)
    monkeypatch.setattr(report_mod, "_parallel_groq_analyst", fake_groq)
    monkeypatch.setattr(report_mod, "record_research_stage", lambda *_args, **_kwargs: None)

    result = await report_mod.run_parallel_evidence_analysis(
        question="Assess deposit growth outlook over the next three years.",
        report_tier="detailed",
        source_text=SOURCE_TEXT,
        research_engine_context="deposit growth observation: 12% in 2025",
        report_plan={"sections": [{"heading": "Growth outlook", "purpose": "Compare evidence."}]},
    )
    assert result["analysts"] == 5
    assert result["successful_analysts"] == 5
    assert len(result["findings"]) == 1
    assert result["findings"][0]["source_ids"] == ["S1"]
    assert max_active >= 2
    assert "secondary guidance" in report_mod._format_parallel_evidence_notes(result)


@pytest.mark.asyncio
async def test_parallel_groq_reviewer_compacts_once_after_http_413(monkeypatch):
    requests = []

    class FakeResponse:
        def __init__(self, status_code):
            self.status_code = status_code
            self.is_success = status_code == 200

        def json(self):
            return {"choices": [{"message": {"content": '{"findings": [], "gaps": []}'}}]}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _url, *, headers, json):
            requests.append(json)
            return FakeResponse(413 if len(requests) == 1 else 200)

    source_blocks = [
        f"- [S{i}] **Publisher evidence {i}**\n"
        f"Source: https://publisher{i}.example.com/disclosure/{i}\n"
        f"DIRECT PUBLISHER PAGE TEXT:\nThe verified promoter transaction value for quarter Q{i % 4 + 1} FY26 was {i * 100} crore. "
        + ("Additional evidence text. " * 18)
        for i in range(1, 31)
    ]
    prompt = (
        "You are an independent source auditor. Preserve every source ID.\n\n"
        "SOURCE EVIDENCE:\n" + "\n\n---\n\n".join(source_blocks)
    )

    monkeypatch.setattr(report_mod, "get_groq_keys", lambda: ["test-groq-key"])
    monkeypatch.setattr(report_mod, "iter_reserved_groq_keys", lambda *_args, **_kwargs: iter(["test-groq-key"]))
    monkeypatch.setattr(report_mod, "is_rate_limited", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(report_mod.httpx, "AsyncClient", FakeClient)

    result, provider = await report_mod._parallel_groq_analyst("Question", "in_depth", prompt)

    assert provider == "groq:openai/gpt-oss-120b"
    assert result == '{"findings": [], "gaps": []}'
    assert len(requests) == 2
    assert len(requests[1]["messages"][1]["content"]) < len(requests[0]["messages"][1]["content"])
    assert requests[0]["max_tokens"] == 3000
    assert requests[1]["max_tokens"] == 1600
    assert "[S1]" in requests[1]["messages"][1]["content"]
    assert "[S30]" in requests[1]["messages"][1]["content"]
