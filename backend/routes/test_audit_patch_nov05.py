"""Regression tests for the Nov-05 audit patch (continuation, gates, planner, titles, images)."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, Path(__file__).resolve().parents[1].as_posix())
from routes import report
from routes.source_manifest import normalise_source_manifest


def test_gemini_gen_config_minimises_thinking():
    assert report._gemini_gen_config("gemini-3-flash-preview", {"maxOutputTokens": 9})["thinkingConfig"] == {"thinkingLevel": "minimal"}
    assert report._gemini_gen_config("gemini-2.5-flash", {})["thinkingConfig"] == {"thinkingBudget": 0}
    assert "thinkingConfig" not in report._gemini_gen_config("other-model", {})


def test_continuation_uses_writer_model_order_and_header_key(monkeypatch):
    calls = []

    class Resp:
        status_code = 200
        is_success = True
        def json(self):
            return {"candidates": [{"content": {"parts": [{"text": "hello body"}]}}]}

    class Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, headers=None, json=None):
            calls.append((url, headers, json))
            return Resp()

    monkeypatch.setattr(report.httpx, "AsyncClient", Client)
    monkeypatch.setattr(report, "get_gemini_keys", lambda: ["AIzaSyAAAA1111", "AIzaSyBBBB2222"])
    monkeypatch.setattr(report, "is_rate_limited", lambda name: False)
    text, model = asyncio.run(report._call_gemini_plain_continuation("p", "in_depth"))
    assert text == "hello body"
    assert model == report.GEMINI_MODELS[0]
    url, headers, body = calls[0]
    assert "key=" not in url
    assert headers["x-goog-api-key"].startswith("AIzaSy")
    assert body["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "minimal"}


def test_continuation_skips_model_in_503_cooldown(monkeypatch):
    seen = []

    class Resp:
        status_code = 200
        is_success = True
        def json(self):
            return {"candidates": [{"content": {"parts": [{"text": "x"}]}}]}

    class Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, headers=None, json=None):
            seen.append(url)
            return Resp()

    cooling = f"gemini:model:{report.GEMINI_MODELS[0]}"
    monkeypatch.setattr(report.httpx, "AsyncClient", Client)
    monkeypatch.setattr(report, "get_gemini_keys", lambda: ["AIzaSyAAAA1111"])
    monkeypatch.setattr(report, "is_rate_limited", lambda name: name == cooling)
    asyncio.run(report._call_gemini_plain_continuation("p", "detailed"))
    assert report.GEMINI_MODELS[0] not in seen[0]
    assert report.GEMINI_MODELS[1] in seen[0]


def test_llm_json_skips_groq_for_oversized_payload(monkeypatch):
    groq_hits = []

    class Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, headers=None, json=None):
            groq_hits.append(url)
            raise RuntimeError("no network in tests")

    monkeypatch.setattr(report.httpx, "AsyncClient", Client)
    monkeypatch.setattr(report, "get_groq_keys", lambda: ["gsk_test"])
    monkeypatch.setattr(report, "get_gemini_keys", lambda: [])
    monkeypatch.setattr(report, "is_rate_limited", lambda name: False)
    asyncio.run(report._call_llm_json("s", "x" * 30_000))
    assert not any("groq.com" in u for u in groq_hits)


def test_portrait_images_are_rejected_but_topic_images_pass():
    subject = "sector rotation in Indian stock markets"
    assert report._image_candidate_is_portrait(subject, "JioBlackRock CIO speaks at event")
    assert report._image_candidate_is_portrait(subject, "Headshot of the chairman")
    assert not report._image_candidate_is_portrait(subject, "NSE trading floor screens showing Nifty")
    assert not report._image_candidate_is_portrait("profile of the CEO of Infosys", "Infosys CEO portrait")


def test_internal_verified_sources_get_reader_safe_titles():
    rows = normalise_source_manifest([
        {"title": "VERIFIED DAILY OHLC (authoritative - do not invent any figure)",
         "url": "internal://verified-daily-ohlc", "snippet": "x"},
        {"title": "RBI Annual Report", "url": "https://rbi.org.in/a", "snippet": "y"},
    ])
    first = rows[0]
    assert "authoritative" not in first["title"].lower()
    assert "do not invent" not in first["title"].lower()
    assert rows[1]["title"] == "RBI Annual Report"


def test_expansion_does_not_resend_identical_prompt(monkeypatch):
    prompts = []

    async def fake_call(prompt, tier):
        prompts.append(prompt)
        return "[[SECTION: Alpha]]\n### Same\n" + ("Repeated filler sentence about alpha. " * 40) + "\n[[END SECTION]]", "fake"

    monkeypatch.setattr(report, "_call_gemini_plain_continuation", fake_call)
    monkeypatch.setattr(report, "_dedupe_redundant_report_content", lambda t: t.split("### Same")[0].rstrip())
    base = "## Alpha\n\n" + ("Alpha body text with detail. " * 30) + "\n\n## Beta\n\n" + ("Beta body text with detail. " * 30)
    plan = {"sections": [{"heading": "Alpha"}, {"heading": "Beta"}, {"heading": "Gamma"}]}
    asyncio.run(report._expand_report_to_tier(
        base, "q", "detailed", plan, "evidence " * 300, target_override=len(base) + 40_000, max_passes_override=6,
    ))
    assert len(prompts) == len(set(prompts)), "an identical rejected prompt was sent twice"
