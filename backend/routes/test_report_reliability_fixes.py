import pytest
import asyncio
import os, sys
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from routes import report as report_mod


def test_report_plan_validator_allows_deep_research_section_count():
    plan = {
        "depth": "in_depth",
        "sections": [
            {"heading": f"Section {i}", "purpose": "evidence-backed analysis", "format": "prose"}
            for i in range(1, 19)
        ],
    }
    assert report_mod._validate_report_plan(plan) is True


def test_fallback_report_plan_is_question_and_evidence_driven():
    class Intent:
        resolved_topic = "India data centre industry"
        evidence_needed = ["historical", "comparison", "regulatory"]
        intent_label = "research report"

    plan = report_mod._fallback_report_plan(
        "Analyze India's data centre industry including capacity, power, operators, pricing, regulation, risks and outlook",
        intent=Intent(),
        report_tier="detailed",
    )
    headings = [x["heading"] for x in plan["sections"]]
    assert plan["depth"] == "detailed"
    assert headings
    assert "Key Findings & Executive Context" not in headings
    assert any("Competitive" in h for h in headings)
    assert any("Regulation" in h for h in headings)
    assert any("Risk" in h for h in headings)


def test_fallback_report_plan_does_not_fill_a_narrow_request_to_a_section_quota():
    class Intent:
        resolved_topic = "one requested policy question"
        evidence_needed = []
        intent_label = "research report"

    plan = report_mod._fallback_report_plan(
        "What does the current policy require?", intent=Intent(), report_tier="in_depth"
    )
    headings = [section["heading"] for section in plan["sections"]]
    assert len(headings) == 1
    assert "Regulation" in headings[0]
    assert "Methodology & Data Notes" not in headings
    assert "Risk Assessment & Key Uncertainties" not in headings


def test_planning_falls_back_without_provider_cascade():
    class Intent:
        resolved_topic = "India data centre industry"
        evidence_needed = ["historical", "comparison", "regulatory"]
        intent_label = "research report"

    async def _none(*args, **kwargs):
        return None

    with patch.object(report_mod, "_call_llm_json", new=AsyncMock(side_effect=_none)):
        plan = asyncio.run(
            report_mod._plan_report_structure(
                question="Analyze India's data centre industry, power, operators, regulation and outlook",
                enriched_sources=[],
                file_section="",
                okf_context="",
                no_web_sources=False,
                conversation_context="",
                wants_more_data_viz=False,
                intent=Intent(),
                report_tier="detailed",
            )
        )
    assert isinstance(plan, dict)
    assert plan["tier"] == "detailed"
    assert plan["sections"]
    assert plan.get("presentation")


def test_generation_prompt_does_not_set_a_page_or_character_quota():
    source = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(source, "routes", "report.py"), encoding="utf-8") as handle:
        text = handle.read()
    assert "Do not target a page, word, or character count." in text
    assert "_TIER_CONTENT_CHARS" not in text


@pytest.mark.asyncio
async def test_page_budget_controller_only_measures_and_never_pads_to_a_floor(monkeypatch):
    import routes.report as report_mod

    async def unexpected_expansion(*args, **kwargs):
        raise AssertionError("pagination is informational and must not trigger expansion")

    monkeypatch.setattr(report_mod, "_expand_report_to_tier", unexpected_expansion)
    monkeypatch.setattr(report_mod, "_rendered_page_count_preflight", lambda *args, **kwargs: 6)
    base = "## Section 1\n\n" + ("Analysis. " * 900)
    out, pages = await report_mod._ensure_report_page_budget(
        base, "test topic", "detailed", {"sections": [{"heading": "Section 1"}]},
        "source evidence", "Test Report", "Summary", [], [], [], None,
    )
    assert pages is not None
    assert pages == 6
    assert out == base.rstrip()

@pytest.mark.asyncio
async def test_report_writer_uses_groq_fallback_when_gemini_unavailable(monkeypatch):
    async def no_gemini(*args, **kwargs):
        return "", ""

    async def groq_fallback(*args, **kwargs):
        return (
            '{"title":"Fallback","report":"## Report\\n\\nContent","charts":[],"images":[],"keyStats":[],"summary":"ok","theme":"default","presentation":{}}',
            "groq:gpt-oss-120b:fallback",
        )

    async def no_openrouter(*args, **kwargs):
        return "", ""

    monkeypatch.setattr(report_mod, "call_gemini", no_gemini)
    monkeypatch.setattr(report_mod, "_call_groq_report_fallback", groq_fallback)
    monkeypatch.setattr(report_mod, "_call_openrouter_report_fallback", no_openrouter)
    raw, model = await report_mod.call_report_writer("Research India renewable energy", "detailed")
    assert '"report"' in raw
    assert model.startswith("groq:")


def test_compact_report_prompt_preserves_subject_and_tier():
    text = (
        "Research Question / Topic (this — and ONLY this — defines the report's title and subject): India renewable energy\n"
        "SELECTED REPORT DEPTH: In-Detail Report\n"
        "Page and length guidance: no page, word, character, section, statistic, or visualization quota.\n"
        + ("Supplementary web sources (25 results):\n[S1] source evidence\n" * 3000)
        + "\nINSTRUCTIONS:\nWrite a FULL report at the selected tier."
    )
    compact = report_mod._compact_report_user_prompt(text, max_chars=30000)
    assert len(compact) <= 30000
    assert "India renewable energy" in compact
    assert "In-Detail Report" in compact
    assert "INSTRUCTIONS:" in compact


@pytest.mark.asyncio
async def test_gemini_report_provider_breaker_skips_provider(monkeypatch):
    monkeypatch.setattr(report_mod, "is_rate_limited", lambda key: key == "gemini:report:provider")
    monkeypatch.setattr(report_mod, "get_gemini_keys", lambda: ["AQ.fake"])
    raw, model = await report_mod.call_gemini("test", "detailed")
    assert raw == ""
    assert model == ""


@pytest.mark.asyncio
async def test_plain_continuation_falls_back_to_groq(monkeypatch):
    monkeypatch.setattr(report_mod, "get_gemini_keys", lambda: [])

    async def fake_groq(prompt, tier):
        return "### Additional analysis\nMore evidence.", "groq:gpt-oss-120b:continuation"

    monkeypatch.setattr(report_mod, "_call_groq_plain_continuation", fake_groq)
    text, model = await report_mod._call_gemini_plain_continuation("Expand this section", "detailed")
    assert "Additional analysis" in text
    assert model.startswith("groq:")


def test_report_provider_failover_uses_validated_models_and_key_pool():
    assert report_mod.GEMINI_MAX_KEYS_PER_MODEL >= 4
    assert report_mod.GEMINI_MAX_TOTAL_ATTEMPTS >= 17
    assert report_mod.GEMINI_WAVE_SIZE >= 2
    assert report_mod.GEMINI_MODELS == [
        "gemini-3.5-flash-lite",
        "gemini-3.6-flash",
        "gemini-3.5-flash",
        "gemini-3-flash-preview",
    ]
    assert "gemini-2.5-flash" not in report_mod.GEMINI_MODELS
    assert report_mod._DEPTH_TIME_BUDGETS["detailed"] >= 120
    assert report_mod._DEPTH_TIME_BUDGETS["in_depth"] >= 180


@pytest.mark.asyncio
async def test_report_writer_uses_provider_independent_evidence_fallback(monkeypatch):
    async def no_gemini(*args, **kwargs):
        return "", ""
    async def no_groq(*args, **kwargs):
        return "", ""
    async def no_openrouter(*args, **kwargs):
        return "", ""

    monkeypatch.setattr(report_mod, "call_gemini", no_gemini)
    monkeypatch.setattr(report_mod, "_call_groq_report_fallback", no_groq)
    monkeypatch.setattr(report_mod, "_call_openrouter_report_fallback", no_openrouter)

    plan = {
        "sections": [
            {"heading": "Market Structure", "purpose": "Understand the market landscape."},
            {"heading": "Risks", "purpose": "Assess material risks."},
        ],
        "presentation": {},
    }
    sources = [
        {"title": "Industry report", "url": "https://example.com/a", "snippet": "", "fullContent": "Market capacity reached 10 GW in 2026 [S1]."},
        {"title": "Policy note", "url": "https://example.com/b", "snippet": "", "fullContent": "Regulatory changes affect project timelines [S2]."},
    ]
    raw, model = await report_mod.call_report_writer(
        "Research Question / Topic (this — and ONLY this — defines the report's title and subject): India energy sector",
        "detailed", report_plan=plan, fallback_sources=sources
    )
    assert model == "local:evidence-fallback"
    assert '"report"' in raw
    assert "[S1]" in raw


@pytest.mark.asyncio
async def test_gemini_scheduler_uses_more_than_two_keys_before_fallback(monkeypatch):
    keys = [f"AQ.key{i}" for i in range(8)]
    monkeypatch.setattr(report_mod, "get_gemini_keys", lambda: keys)
    monkeypatch.setattr(report_mod, "is_rate_limited", lambda key: False)

    calls = []
    async def fake_attempt(user_prompt, depth, key, model):
        calls.append((key, model))
        return "", model, "503"

    monkeypatch.setattr(report_mod, "_gemini_one_attempt", fake_attempt)
    monkeypatch.setattr(report_mod, "mark_rate_limited", lambda *args, **kwargs: None)

    raw, model = await report_mod.call_gemini("Research India renewable energy", "detailed")
    assert raw == ""
    assert model == ""
    assert len({k for k, _ in calls}) == 8


@pytest.mark.asyncio
async def test_gemini_scheduler_accepts_short_nonempty_response_without_chasing_length(monkeypatch):
    keys = ["AQ.a", "AQ.b", "AQ.c", "AQ.d"]
    monkeypatch.setattr(report_mod, "get_gemini_keys", lambda: keys)
    monkeypatch.setattr(report_mod, "is_rate_limited", lambda key: False)

    async def fake_attempt(user_prompt, depth, key, model):
        if key == "AQ.a":
            return ("{\"report\":\"Evidence is insufficient; no supported values were found.\"}"), model, "success"
        await asyncio.sleep(0.5)
        return "", model, "503"

    monkeypatch.setattr(report_mod, "_gemini_one_attempt", fake_attempt)
    started = asyncio.get_running_loop().time()
    raw, model = await report_mod.call_gemini("Research India renewable energy", "detailed")
    elapsed = asyncio.get_running_loop().time() - started
    assert "Evidence is insufficient" in raw
    assert len(raw) < 8_000
    assert elapsed < 0.45
