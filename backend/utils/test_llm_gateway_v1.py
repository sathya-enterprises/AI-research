"""V2 LLM gateway: model/key fallback, budget, tokens, failure isolation, capture."""
import asyncio
import json
import types

import httpx
import pytest

from utils import llm_gateway as G
from utils import research_capture as RC


def _runtime(groq_text="groq answer"):
    limited = set()

    async def groq(prompt, tier):
        return groq_text, "groq-model"

    return types.SimpleNamespace(
        get_gemini_keys=lambda: ["AIzaKEY1", "AIzaKEY2"], _is_rest_api_key=lambda k: True,
        round_robin=lambda keys: list(keys), GEMINI_MODELS=["model-a", "model-b"],
        _gemini_model_cooling=lambda m: f"gemini:model:{m}" in limited,
        _gemini_candidate_keys=lambda ring, model, n: list(ring),
        is_rate_limited=lambda k: k in limited, mark_rate_limited=lambda k, ms: limited.add(k),
        GEMINI_KEY_COOLDOWN_MS=1, GEMINI_MODEL_COOLDOWN_MS=1, GEMINI_TIMEOUT_KEY_COOLDOWN_MS=1,
        _gemini_gen_config=lambda model, cfg: cfg, _call_groq_plain_continuation=groq, limited=limited,
    )


def _patch_http(monkeypatch, handler):
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(G.httpx, "AsyncClient", factory)


def _ok(text, tin=120, tout=40):
    return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}],
                                     "usageMetadata": {"promptTokenCount": tin, "candidatesTokenCount": tout}})


def test_503_cools_the_model_and_the_next_model_answers(monkeypatch):
    seen = []

    def handler(request):
        model = request.url.path.split("/")[-1].split(":")[0]
        seen.append(model)
        return httpx.Response(503) if model == "model-a" else _ok("section text")

    _patch_http(monkeypatch, handler)
    rt = _runtime()
    gw = G.LLMGateway(rt, "detailed", budget=5, concurrency=2, capture=False)
    result = asyncio.run(gw.generate(purpose="section", section_id="sec-1", prompt="p", system="s"))
    assert result.ok and result.text == "section text" and result.model == "model-b"
    assert "gemini:model:model-a" in rt.limited
    assert (result.tokens_in, result.tokens_out) == (120, 40)
    assert gw.stats.to_dict()["calls"] == 1 and gw.stats.tokens_out == 40


def test_budget_refuses_extra_calls_without_raising(monkeypatch):
    _patch_http(monkeypatch, lambda request: _ok("x"))
    gw = G.LLMGateway(_runtime(), "detailed", budget=1, capture=False)
    first = asyncio.run(gw.generate(purpose="outline", prompt="p"))
    second = asyncio.run(gw.generate(purpose="section", prompt="p"))
    assert first.ok and not second.ok and second.error == "budget_exhausted"
    assert gw.stats.budget_refusals == 1 and gw.stats.calls == 1


def test_concurrent_calls_reserve_the_budget_atomically(monkeypatch):
    _patch_http(monkeypatch, lambda request: _ok("x"))
    gw = G.LLMGateway(_runtime(), "detailed", budget=3, concurrency=4, capture=False)

    async def run():
        return await asyncio.gather(*[
            gw.generate(purpose="section", section_id=f"s{i}", prompt="p") for i in range(12)
        ])

    results = asyncio.run(run())
    assert sum(result.ok for result in results) == 3
    assert gw.stats.calls == 3 and gw.stats.budget_refusals == 9
    assert gw.remaining == 0


def test_total_failure_returns_not_ok_for_json_and_uses_groq_for_text(monkeypatch):
    _patch_http(monkeypatch, lambda request: httpx.Response(500))
    gw = G.LLMGateway(_runtime(groq_text=""), "in_depth", budget=5, capture=False)
    res = asyncio.run(gw.generate(purpose="outline", prompt="p", json_mode=True, retries=1))
    assert not res.ok and "http_500" in res.error
    gw2 = G.LLMGateway(_runtime(groq_text="fallback prose"), "in_depth", budget=5, capture=False)
    res2 = asyncio.run(gw2.generate(purpose="section", prompt="p"))
    assert res2.ok and res2.model == "groq-model" and res2.text == "fallback prose"


def test_json_helper_parses_fenced_and_wrapped_json():
    assert G.LLMResult(ok=True, text='```json\n{"a": 1}\n```').json() == {"a": 1}
    assert G.LLMResult(ok=True, text='Here: [1, 2]').json() == [1, 2]
    assert G.LLMResult(ok=True, text="no json").json() is None


def test_calls_are_captured_with_full_prompt_and_redaction(monkeypatch, tmp_path):
    monkeypatch.setenv("GG_RESEARCH_CAPTURE", "1")
    monkeypatch.setenv("GG_RESEARCH_CAPTURE_DIR", str(tmp_path))
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaSECRETVALUE123")
    _patch_http(monkeypatch, lambda request: _ok("answer " * 3000))

    async def run():
        token = RC.begin_research_capture("q", "detailed")
        gw = G.LLMGateway(_runtime(), "detailed", budget=3)
        await gw.generate(purpose="section", section_id="sec-2", prompt="long prompt " * 2000 + " AIzaSECRETVALUE123", system="sys")
        path = RC.finish_research_capture({"report": "x"}, token=token)
        return path

    path = asyncio.run(run())
    data = json.loads(open(path, encoding="utf-8").read())
    call = data["llmCalls"][0]
    assert call["purpose"] == "section" and call["sectionId"] == "sec-2" and call["model"] == "model-a"
    assert len(call["llmPrompt"]) > 20000 and len(call["llmResponse"]) > 20000  # not clipped to 4,000 chars
    assert "AIzaSECRETVALUE123" not in call["llmPrompt"]
    assert call["tokensIn"] == 120 and call["tokensOut"] == 40


def test_keys_rotate_across_calls(monkeypatch):
    used = []

    def handler(request):
        used.append(request.headers["x-goog-api-key"])
        return _ok("x")

    _patch_http(monkeypatch, handler)
    gw = G.LLMGateway(_runtime(), "in_depth", budget=10, capture=False)
    for _ in range(4):
        asyncio.run(gw.generate(purpose="section", prompt="p"))
    assert used == ["AIzaKEY1", "AIzaKEY2", "AIzaKEY1", "AIzaKEY2"]


def test_capture_is_on_disk_after_each_call_even_if_the_run_never_finishes(monkeypatch, tmp_path):
    monkeypatch.setenv("GG_RESEARCH_CAPTURE", "1")
    monkeypatch.setenv("GG_RESEARCH_CAPTURE_DIR", str(tmp_path))
    _patch_http(monkeypatch, lambda request: _ok("section text"))

    async def run_then_die():
        RC.begin_research_capture("q", "in_depth")
        gw = G.LLMGateway(_runtime(), "in_depth", budget=5)
        await gw.generate(purpose="outline", prompt="p1")
        await gw.generate(purpose="section", section_id="sec-1#1", prompt="p2")
        raise RuntimeError("provider outage mid-run")  # finish_research_capture is never called

    with pytest.raises(RuntimeError):
        asyncio.run(run_then_die())
    files = list(tmp_path.glob("research_capture_*.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text(encoding="utf-8"))
    assert [c["purpose"] for c in data["llmCalls"]] == ["outline", "section"]
    assert data["status"] in {"in_progress", "failed", "error"}


def test_waits_out_cooldowns_when_every_model_is_cooling(monkeypatch):
    """Live run: two sections were lost within one second because every model was in its 60 s cooldown."""
    _patch_http(monkeypatch, lambda request: _ok("section text"))
    runtime = _runtime(groq_text="")
    runtime.limited.update({"gemini:model:model-a", "gemini:model:model-b"})
    waits = []

    async def fake_sleep(seconds):
        waits.append(seconds)
        runtime.limited.clear()  # the cooldowns expire while we wait

    monkeypatch.setattr(G.asyncio, "sleep", fake_sleep)
    gw = G.LLMGateway(runtime, "detailed", budget=5, capture=False)
    res = asyncio.run(gw.generate(purpose="section", prompt="p", retries=1))
    assert res.ok and res.text == "section text"
    assert waits == [20]


def test_section_prose_waits_for_a_full_model_and_uses_lite_only_as_last_resort(monkeypatch):
    seen_models = []

    def handler(request):
        seen_models.append(request.url.path.split("/models/")[1].split(":")[0])
        return _ok("section text")

    _patch_http(monkeypatch, handler)
    runtime = _runtime(groq_text="")
    runtime.GEMINI_MODELS = ["model-a", "model-a-lite"]
    runtime.limited.add("gemini:model:model-a")  # the full model is cooling
    waits = []

    async def fake_sleep(seconds):
        waits.append(seconds)
        if len(waits) == 1:
            runtime.limited.clear()  # the cooldown ends during the first wait

    monkeypatch.setattr(G.asyncio, "sleep", fake_sleep)
    gw = G.LLMGateway(runtime, "detailed", budget=5, capture=False)
    res = asyncio.run(gw.generate(purpose="section", prompt="p", retries=1))
    assert res.ok and seen_models == ["model-a"] and waits == [20]

    # a planning call may use the lite model straight away
    seen_models.clear(); waits.clear()
    runtime.limited.add("gemini:model:model-a")
    res = asyncio.run(G.LLMGateway(runtime, "detailed", budget=5, capture=False).generate(purpose="outline", prompt="p"))
    assert res.ok and seen_models == ["model-a-lite"] and waits == []


def test_section_prose_falls_back_to_lite_after_the_cooldown_waits(monkeypatch):
    seen_models = []

    def handler(request):
        seen_models.append(request.url.path.split("/models/")[1].split(":")[0])
        return _ok("section text")

    _patch_http(monkeypatch, handler)
    runtime = _runtime(groq_text="")
    runtime.GEMINI_MODELS = ["model-a", "model-a-lite"]
    runtime.limited.add("gemini:model:model-a")  # stays cooling throughout
    waits = []

    async def fake_sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr(G.asyncio, "sleep", fake_sleep)
    res = asyncio.run(G.LLMGateway(runtime, "detailed", budget=5, capture=False).generate(purpose="section", prompt="p"))
    assert res.ok and seen_models == ["model-a-lite"] and waits == [20, 45]


def test_section_prose_goes_to_groq_before_sleeping_when_gemini_is_rate_limited(monkeypatch):
    """In-depth run: sections slept through 20-45 s cooldowns and were then written by Groq anyway."""
    _patch_http(monkeypatch, lambda request: _ok("gemini text"))
    runtime = _runtime(groq_text="groq prose")
    runtime.limited.update({"gemini:model:model-a", "gemini:model:model-b"})
    waits = []

    async def fake_sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr(G.asyncio, "sleep", fake_sleep)
    gw = G.LLMGateway(runtime, "in_depth", budget=5, capture=False)
    res = asyncio.run(gw.generate(purpose="section", prompt="p", retries=1))
    assert res.ok and res.text == "groq prose" and waits == []
    assert gw.stats.groq_before_wait == 1


def test_json_calls_still_wait_and_release_their_slot_while_sleeping(monkeypatch):
    _patch_http(monkeypatch, lambda request: _ok('{"ok": true}'))
    runtime = _runtime(groq_text="groq prose")
    runtime.limited.update({"gemini:model:model-a", "gemini:model:model-b"})
    slot_free_while_sleeping = []
    gw = G.LLMGateway(runtime, "in_depth", budget=5, capture=False, concurrency=1)

    async def fake_sleep(seconds):
        slot_free_while_sleeping.append(not gw.semaphore.locked())
        runtime.limited.clear()

    monkeypatch.setattr(G.asyncio, "sleep", fake_sleep)
    res = asyncio.run(gw.generate(purpose="outline", prompt="p", json_mode=True, retries=1))
    assert res.ok and slot_free_while_sleeping == [True]
    assert gw.semaphore.locked() is False  # the slot count is balanced afterwards


def test_400_cools_the_model_so_later_calls_skip_it(monkeypatch):
    seen = []

    def handler(request):
        model = request.url.path.split("/")[-1].split(":")[0]
        seen.append(model)
        return httpx.Response(400, json={"error": {"message": "bad"}}) if model == "model-a" else _ok("text")

    _patch_http(monkeypatch, handler)
    rt = _runtime()
    gw = G.LLMGateway(rt, "detailed", budget=5, concurrency=1, capture=False)
    first = asyncio.run(gw.generate(purpose="section", section_id="sec-1", prompt="p", system="s"))
    second = asyncio.run(gw.generate(purpose="section", section_id="sec-2", prompt="p", system="s"))
    assert first.ok and second.ok and second.model == "model-b"
    assert "gemini:model:model-a" in rt.limited and gw.stats_rejected_models == {"model-a"}
    assert seen.count("model-a") == 1  # the rejecting model was tried once, not on every call


def test_a_model_cooled_while_a_call_was_running_is_not_tried_by_that_call(monkeypatch):
    seen = []

    def handler(request):
        model = request.url.path.split("/")[-1].split(":")[0]
        seen.append(model)
        return httpx.Response(503) if model == "model-a" else _ok("text")

    _patch_http(monkeypatch, handler)
    rt = _runtime()
    rt.GEMINI_MODELS = ["model-a", "model-c", "model-b"]
    # model-c is cooled by another call after this call made its plan (simulated: cooled before its turn)
    real_cooling = rt._gemini_model_cooling
    calls = {"n": 0}

    def cooling(model):
        calls["n"] += 1
        if calls["n"] > 3:  # after the plan was built
            rt.limited.add("gemini:model:model-c")
        return real_cooling(model)

    rt._gemini_model_cooling = cooling
    gw = G.LLMGateway(rt, "detailed", budget=5, concurrency=1, capture=False)
    result = asyncio.run(gw.generate(purpose="section", section_id="sec-1", prompt="p", system="s"))
    assert result.ok and result.model == "model-b" and "model-c" not in seen


def _slow_model_a(delay_a):
    async def handler(request):
        model = request.url.path.split("/")[-1].split(":")[0]
        if model == "model-a":
            await asyncio.sleep(delay_a)
            return _ok("slow answer")
        return _ok("fast answer")
    return handler


def test_a_slow_section_call_is_hedged_on_another_model_and_the_first_answer_wins(monkeypatch):
    monkeypatch.setenv("GG_V2_HEDGE", "1")
    _patch_http(monkeypatch, _slow_model_a(5.0))
    monkeypatch.setitem(G.HEDGE_AFTER_SECONDS, "section", 0.2)
    monkeypatch.delenv("GG_V2_HEDGE_AFTER_S", raising=False)
    gw = G.LLMGateway(_runtime(), "in_depth", budget=5, capture=False)
    started = asyncio.get_event_loop_policy().new_event_loop()
    try:
        import time
        t0 = time.perf_counter()
        result = started.run_until_complete(gw.generate(purpose="section", section_id="sec-1", prompt="p"))
        elapsed = time.perf_counter() - t0
    finally:
        started.close()
    assert result.ok and result.text == "fast answer" and result.model == "model-b"
    assert elapsed < 3 and gw.stats_hedges == 1 and gw.stats_hedge_wins == 1
    assert gw.stats.calls == 1  # the hedge is the same logical call: the report's call budget is not spent twice


def test_a_fast_call_is_not_hedged_and_hedging_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("GG_V2_HEDGE", "1")
    _patch_http(monkeypatch, _slow_model_a(0.0))
    monkeypatch.setitem(G.HEDGE_AFTER_SECONDS, "section", 0.5)
    gw = G.LLMGateway(_runtime(), "in_depth", budget=5, capture=False)
    result = asyncio.run(gw.generate(purpose="section", prompt="p"))
    assert result.ok and result.text == "slow answer" and gw.stats_hedges == 0
    _patch_http(monkeypatch, _slow_model_a(0.6))
    monkeypatch.setitem(G.HEDGE_AFTER_SECONDS, "section", 0.1)
    monkeypatch.setenv("GG_V2_HEDGE", "0")
    gw = G.LLMGateway(_runtime(), "in_depth", budget=5, capture=False)
    result = asyncio.run(gw.generate(purpose="section", prompt="p"))
    assert result.ok and result.text == "slow answer" and gw.stats_hedges == 0


def test_claim_checks_are_never_hedged(monkeypatch):
    monkeypatch.setenv("GG_V2_HEDGE", "1")
    _patch_http(monkeypatch, _slow_model_a(0.4))
    gw = G.LLMGateway(_runtime(), "in_depth", budget=5, capture=False)
    result = asyncio.run(gw.generate(purpose="claim_check", prompt="p"))
    assert result.ok and gw.stats_hedges == 0


def test_calls_at_once_default_and_override(monkeypatch):
    monkeypatch.delenv("GG_V2_SECTION_CONCURRENCY", raising=False)
    assert G.LLMGateway(_runtime(), "in_depth", capture=False).semaphore._value == 5
    assert G.LLMGateway(_runtime(), "detailed", capture=False).semaphore._value == 5
    monkeypatch.setenv("GG_V2_SECTION_CONCURRENCY", "3")
    assert G.LLMGateway(_runtime(), "in_depth", capture=False).semaphore._value == 3


def test_hedging_is_off_by_default(monkeypatch):
    monkeypatch.delenv("GG_V2_HEDGE", raising=False)
    _patch_http(monkeypatch, _slow_model_a(0.5))
    monkeypatch.setitem(G.HEDGE_AFTER_SECONDS, "section", 0.1)
    gw = G.LLMGateway(_runtime(), "in_depth", budget=5, capture=False)
    result = asyncio.run(gw.generate(purpose="section", prompt="p"))
    assert result.ok and result.text == "slow answer" and gw.stats_hedges == 0
