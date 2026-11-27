"""Project-aware Gemini scheduling (GG_GEMINI_KEY_PROJECTS=per_key): 16 independent projects, 429 vs 503, circuit
breaker, adaptive concurrency, fallback validation and secret-free telemetry. Mocked HTTP only; fake keys."""
import asyncio
import logging
import types

import httpx
import pytest

from utils import llm_gateway as G
from utils import llm_quota as Q
from utils import llm_telemetry as T

KEYS = [f"AQ.fake-secret-{i:02d}-XYZ{i:02d}abcdef" for i in range(1, 17)]


@pytest.fixture(autouse=True)
def _per_key(monkeypatch):
    monkeypatch.setenv("GG_GEMINI_KEY_PROJECTS", "per_key")
    monkeypatch.delenv("GG_GEMINI_QUOTAS", raising=False)
    monkeypatch.setattr(G, "QUOTAS", Q.QuotaManager())


def _runtime(models=("model-a", "model-b"), groq_text="groq answer"):
    limited = set()

    async def groq(prompt, tier):
        return groq_text, "groq-model"

    return types.SimpleNamespace(
        get_gemini_keys=lambda: list(KEYS), _is_rest_api_key=lambda k: True,
        round_robin=lambda keys: list(keys), GEMINI_MODELS=list(models),
        _gemini_model_cooling=lambda m: f"gemini:model:{m}" in limited,
        # like routes/report.py: a key/model pair cooled after its own 429 is skipped for that model only
        _gemini_candidate_keys=lambda ring, model, n: [k for k in ring if f"report:gemini:{k}:{model}" not in limited],
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


def _ok(text="answer [S1.1]"):
    return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}],
                                     "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 30}})


def _who(request):
    model = request.url.path.split("/")[-1].split(":")[0]
    return model, G.QUOTAS.project_of(request.headers["x-goog-api-key"], KEYS)


async def _no_sleep(seconds):
    return None


def test_sixteen_keys_get_sixteen_independent_aliases_with_no_key_material():
    quotas = Q.QuotaManager()
    aliases = [quotas.project_of(k, KEYS) for k in KEYS]
    assert aliases == [f"gemini_project_{i:02d}" for i in range(1, 17)]
    assert all(quotas.alias_of(k, KEYS) == a for k, a in zip(KEYS, aliases))
    assert not any(part in a for a in aliases for k in KEYS for part in (k[-6:], k[:8]))
    assert quotas.project_of("AQ.not-in-the-pool", KEYS) == Q.SHARED_PROJECT


def test_quota_limits_are_isolated_per_project_and_model(monkeypatch):
    monkeypatch.setenv("GG_GEMINI_QUOTAS", '{"gemini_project_01": {"model-a": {"rpm": 1}}}')
    quotas = Q.QuotaManager()
    quotas.record("gemini_project_01", "model-a", 10, now=1000.0)
    assert quotas.wait_seconds("gemini_project_01", "model-a", 10, now=1001.0) > 0
    assert quotas.wait_seconds("gemini_project_01", "model-b", 10, now=1001.0) == 0   # other model of the project
    assert quotas.wait_seconds("gemini_project_02", "model-a", 10, now=1001.0) == 0   # other project
    assert quotas.limits("gemini_project_02", "model-a") == {}                         # nothing invented


def test_tpm_limit_holds_a_large_request_until_tokens_age_out(monkeypatch):
    monkeypatch.setenv("GG_GEMINI_QUOTAS", '{"default": {"tpm": 1000}}')
    quotas = Q.QuotaManager()
    quotas.record("gemini_project_03", "model-a", 900, now=100.0)
    assert 0 < quotas.wait_seconds("gemini_project_03", "model-a", 200, now=110.0) <= 50.0
    assert quotas.wait_seconds("gemini_project_03", "model-a", 50, now=110.0) == 0


def test_a_429_cools_only_that_project_and_model(monkeypatch):
    seen = []

    def handler(request):
        model, project = _who(request)
        seen.append((model, project))
        if (model, project) == ("model-a", "gemini_project_01"):
            return httpx.Response(429, headers={"Retry-After": "45"}, json={"error": {"code": 429}})
        return _ok()

    _patch_http(monkeypatch, handler)
    rt = _runtime()
    gw = G.LLMGateway(rt, "detailed", budget=10, capture=False)
    first = asyncio.run(gw.generate(purpose="outline", prompt="p"))
    assert first.ok and seen[0] == ("model-a", "gemini_project_01")
    assert "gemini:model:model-a" not in rt.limited          # the model is not cooled for the 15 other projects
    assert KEYS[0] not in rt.limited                          # nor the key for its other models
    assert 45 <= G.QUOTAS.wait_seconds("gemini_project_01", "model-a", 1) <= 45 * 1.25 + 1  # Retry-After + jitter
    assert G.QUOTAS.wait_seconds("gemini_project_01", "model-b", 1) == 0
    seen.clear()
    asyncio.run(gw.generate(purpose="outline", prompt="p"))
    assert ("model-a", "gemini_project_01") not in seen and seen[0][0] == "model-a"


def test_429s_on_several_projects_open_the_model_breaker(monkeypatch):
    def handler(request):
        model, _project = _who(request)
        return httpx.Response(429, json={"error": {"code": 429}}) if model == "model-a" else _ok()

    _patch_http(monkeypatch, handler)
    monkeypatch.setattr(G.asyncio, "sleep", _no_sleep)
    rt = _runtime()
    gw = G.LLMGateway(rt, "detailed", budget=10, capture=False)
    for _ in range(3):
        assert asyncio.run(gw.generate(purpose="outline", prompt="p")).ok
    assert "gemini:model:model-a" in rt.limited
    assert gw.breaker_log and gw.breaker_log[0]["kind"] == "429" and gw.breaker_log[0]["projects"] == 3


def test_one_503_is_capacity_on_that_project_not_quota_and_not_global(monkeypatch):
    def handler(request):
        model, project = _who(request)
        return httpx.Response(503) if (model, project) == ("model-a", "gemini_project_01") else _ok()

    _patch_http(monkeypatch, handler)
    rt = _runtime()
    gw = G.LLMGateway(rt, "detailed", budget=10, capture=False)
    assert asyncio.run(gw.generate(purpose="outline", prompt="p")).ok
    assert "gemini:model:model-a" not in rt.limited and not gw.breaker_log
    assert G.QUOTAS.wait_seconds("gemini_project_01", "model-a", 1) > 0          # brief project/model cooldown
    assert G.QUOTAS.stats["dailyExhausted"] == 0                                  # never treated as quota
    summary = gw.telemetry()["requestSummary"]
    assert summary["http503"] == 1 and summary["http429"] == 0


def test_503s_from_two_projects_trip_the_breaker_and_recovery_closes_it(monkeypatch):
    state = {"down": True}

    def handler(request):
        model, _project = _who(request)
        return httpx.Response(503) if model == "model-a" and state["down"] else _ok()

    _patch_http(monkeypatch, handler)
    rt = _runtime()
    gw = G.LLMGateway(rt, "detailed", budget=10, capture=False)
    asyncio.run(gw.generate(purpose="outline", prompt="p"))
    asyncio.run(gw.generate(purpose="outline", prompt="p"))
    assert "gemini:model:model-a" in rt.limited and "model-a" in gw._half_open
    trip = gw.breaker_log[0]
    assert trip["kind"] == "503" and 30 <= trip["seconds"] <= 38
    state["down"] = False
    rt.limited.discard("gemini:model:model-a")                                     # the cooldown has run out
    result = asyncio.run(gw.generate(purpose="outline", prompt="p"))
    assert result.ok and result.model == "model-a" and "model-a" not in gw._half_open


def test_provider_wide_outage_falls_back_without_a_retry_storm(monkeypatch):
    calls = []

    def handler(request):
        calls.append(_who(request))
        return httpx.Response(503)

    _patch_http(monkeypatch, handler)
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(G.asyncio, "sleep", fake_sleep)
    rt = _runtime(groq_text="fallback prose")
    gw = G.LLMGateway(rt, "detailed", budget=10, capture=False)
    result = asyncio.run(gw.generate(purpose="editorial_transitions", prompt="p"))
    assert result.ok and result.model == "groq-model"
    # both models trip the breaker after 2 projects each; later attempts find every model cooling and wait
    # instead of sending more requests: a handful of requests, not 16 projects x retries
    assert len(calls) <= 6, calls
    assert {m for m, _ in calls} == {"model-a", "model-b"}
    assert len({p for _, p in calls}) <= 4


def test_waits_are_jittered_so_projects_do_not_retry_together():
    quotas = Q.QuotaManager()
    cooled = [quotas.cool(f"gemini_project_{i:02d}", "model-a", 20.0) for i in range(1, 17)]
    assert all(20.0 <= c <= 25.0 for c in cooled) and len({round(c, 3) for c in cooled}) > 8


def test_requests_spread_across_projects_instead_of_one(monkeypatch):
    used = []

    def handler(request):
        used.append(_who(request)[1])
        return _ok()

    _patch_http(monkeypatch, handler)
    gw = G.LLMGateway(_runtime(), "in_depth", budget=40, capture=False)

    async def burst():
        await asyncio.gather(*(gw.generate(purpose="outline", prompt="p") for _ in range(16)))

    asyncio.run(burst())
    assert len(set(used)) >= 12, used                 # least-loaded selection, not the first key every time
    assert max(used.count(p) for p in set(used)) <= 3


def test_an_unhealthy_project_is_not_preferred_just_because_it_is_idle():
    quotas = Q.QuotaManager()
    for _ in range(5):
        quotas.outcome("gemini_project_02", "model-a", False)
    quotas.outcome("gemini_project_03", "model-a", True)
    assert quotas.load("gemini_project_02", "model-a") > quotas.load("gemini_project_03", "model-a")


def test_adaptive_concurrency_never_exceeds_its_limit_and_only_lowers_by_default(monkeypatch):
    monkeypatch.delenv("GG_V2_CONCURRENCY_MAX", raising=False)
    limiter = G.AdaptiveLimiter(5, minimum=3, increase_after=2, decrease_every=0)
    peak = {"now": 0, "max": 0}

    async def work():
        async with limiter:
            peak["now"] += 1
            peak["max"] = max(peak["max"], peak["now"])
            await asyncio.sleep(0.01)
            peak["now"] -= 1

    async def run():
        await asyncio.gather(*(work() for _ in range(20)))

    asyncio.run(run())
    assert peak["max"] == 5
    limiter.on_overload(); limiter.on_overload(); limiter.on_overload()
    assert limiter.limit == 3                                   # floor
    for _ in range(10):
        limiter.on_success()
    assert limiter.limit == 5                                   # back to the start, never above it
    assert limiter.to_dict()["max"] == 5 and limiter.lowest == 3


def test_concurrency_ceiling_can_be_raised_only_by_configuration(monkeypatch):
    monkeypatch.setenv("GG_V2_CONCURRENCY_MAX", "7")
    limiter = G.AdaptiveLimiter(5, increase_after=1)
    for _ in range(5):
        limiter.on_success()
    assert limiter.limit == 7


def test_a_fallback_section_without_citations_waits_for_gemini(monkeypatch):
    state = {"n": 0}

    def handler(request):
        state["n"] += 1
        return httpx.Response(429, json={"error": {}}) if state["n"] <= 2 else _ok("gemini prose [S2.1]")

    async def time_passes(seconds):  # the cooldowns run out while the call waits
        G.QUOTAS._cooled_until.clear()
        rt.limited.difference_update({k for k in rt.limited if k.startswith("report:gemini:")})

    monkeypatch.setenv("GG_V2_RATE_LIMIT_BACKOFF", "20,40")   # routes/conftest.py switches the waits off
    monkeypatch.setenv("GG_V2_COOLDOWN_BACKOFF", "20,45")
    _patch_http(monkeypatch, handler)
    monkeypatch.setattr(G.asyncio, "sleep", time_passes)
    rt = _runtime(models=("model-a",), groq_text="uncited fallback prose " * 20)
    for k in KEYS[2:]:
        rt.limited.add(k)                                       # two projects available, both throttled at first
    gw = G.LLMGateway(rt, "in_depth", budget=5, capture=False)
    result = asyncio.run(gw.generate(purpose="section", prompt="Write. Evidence [S2.1] ...", retries=1))
    assert result.ok and result.text == "gemini prose [S2.1]"
    assert gw.stats.fallbacks_without_citations == 1


def test_a_cited_fallback_is_accepted_like_gemini_output(monkeypatch):
    _patch_http(monkeypatch, lambda request: httpx.Response(429, json={"error": {}}))
    monkeypatch.setattr(G.asyncio, "sleep", _no_sleep)
    rt = _runtime(groq_text="fallback prose citing [S2.1] " * 10)
    gw = G.LLMGateway(rt, "in_depth", budget=5, capture=False)
    result = asyncio.run(gw.generate(purpose="section", prompt="Evidence [S2.1]", retries=1, min_words=10))
    assert result.ok and result.model == "groq-model"
    rows = gw.requests.records
    assert any(r.provider == "groq" and r.fallback for r in rows)
    assert gw.telemetry()["requestSummary"]["groqFallbackShare"] == 1.0


def test_telemetry_and_logs_never_contain_a_key(monkeypatch, caplog):
    def handler(request):
        model, project = _who(request)
        if project == "gemini_project_01":
            return httpx.Response(429, json={"error": {"code": 429, "details": [
                {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [
                    {"quotaMetric": "generativelanguage.googleapis.com/generate_content_free_tier_requests",
                     "quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier", "quotaValue": "10",
                     "quotaDimensions": {"model": model}}]},
                {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "31s"}]}})
        return _ok()

    _patch_http(monkeypatch, handler)
    caplog.set_level(logging.INFO)
    gw = G.LLMGateway(_runtime(), "detailed", budget=5, capture=False, report_id="job-1")
    assert asyncio.run(gw.generate(purpose="outline", section_id="outline", prompt="p")).ok
    tele = gw.stats.to_dict()
    text = repr(tele) + caplog.text + repr([r.to_dict() for r in gw.requests.records])
    for key in KEYS:
        assert key not in text and key[-10:] not in text
    rec = gw.requests.records[0]
    assert (rec.report_id, rec.purpose, rec.project, rec.status, rec.error_category, rec.attempt) == (
        "job-1", "outline", "gemini_project_01", 429, T.RATE_LIMIT_MINUTE, 1)
    assert gw.requests.records[1].attempt == 2 and gw.requests.records[1].outcome == "success"
    assert tele["projectMode"] == "per_key"
    assert tele["quotaLimitsReported"][0]["limit"] == "10"         # Google's own number, read from the 429
    assert tele["requestSummary"]["byProject"]["gemini_project_01"]["http429"] == 1
    assert tele["requestSummary"]["retryRate"] == 1.0


def test_shared_mode_is_unchanged_when_no_mapping_is_set(monkeypatch):
    monkeypatch.delenv("GG_GEMINI_KEY_PROJECTS", raising=False)

    def handler(request):
        model, _ = _who(request)
        return httpx.Response(503) if model == "model-a" else _ok()

    _patch_http(monkeypatch, handler)
    rt = _runtime()
    gw = G.LLMGateway(rt, "detailed", budget=5, capture=False)
    assert asyncio.run(gw.generate(purpose="outline", prompt="p")).ok
    assert "gemini:model:model-a" in rt.limited                    # one 503 cools the model, as before
    assert gw.telemetry()["projectMode"] == "shared"
    assert gw.requests.records[0].project == "gemini_key_01"
