"""Project-aware Gemini quota management and its use by the gateway (mocked HTTP, no live calls)."""
import asyncio
import json

import httpx

from utils import llm_gateway as G
from utils.llm_quota import QuotaManager, SHARED_PROJECT, is_daily_quota, parse_retry_delay
from utils.test_llm_gateway_v1 import _ok, _patch_http, _runtime


def _quota(monkeypatch, projects: str, quotas: dict | None):
    monkeypatch.setenv("GG_GEMINI_KEY_PROJECTS", projects)
    if quotas is None:
        monkeypatch.delenv("GG_GEMINI_QUOTAS", raising=False)
    else:
        monkeypatch.setenv("GG_GEMINI_QUOTAS", json.dumps(quotas))
    fresh = QuotaManager()
    monkeypatch.setattr(G, "QUOTAS", fresh)
    return fresh


def test_unmapped_keys_share_one_pool_and_mapped_keys_get_their_project(monkeypatch):
    q = QuotaManager()
    monkeypatch.delenv("GG_GEMINI_KEY_PROJECTS", raising=False)
    assert q.project_of("AIzaKEY1", ["AIzaKEY1", "AIzaKEY2"]) == SHARED_PROJECT
    monkeypatch.setenv("GG_GEMINI_KEY_PROJECTS", "proj-a,proj-b")
    q = QuotaManager()
    assert q.project_of("AIzaKEY1", ["AIzaKEY1", "AIzaKEY2"]) == "proj-a"
    assert q.project_of("AIzaKEY2", ["AIzaKEY1", "AIzaKEY2"]) == "proj-b"
    monkeypatch.setenv("GG_GEMINI_KEY_PROJECTS", json.dumps({"KEY2": "paid"}))
    q = QuotaManager()
    assert q.project_of("AIzaKEY2", ["AIzaKEY1", "AIzaKEY2"]) == "paid"
    assert q.project_of("AIzaKEY1", ["AIzaKEY1", "AIzaKEY2"]) == SHARED_PROJECT


def test_rpm_tpm_and_rpd_limits_per_project_and_model(monkeypatch):
    monkeypatch.setenv("GG_GEMINI_QUOTAS", json.dumps({"default": {"rpm": 2, "tpm": 1000, "rpd": 3},
                                                       "paid": {"*": {"rpm": 100, "tpm": 10**6, "rpd": 10**5}}}))
    q = QuotaManager()
    now = 1_000_000.0
    q.record("free", "m", 400, now)
    assert q.wait_seconds("free", "m", 400, now) == 0                 # 2nd request fits
    q.record("free", "m", 400, now)
    assert q.wait_seconds("free", "m", 10, now) > 0                    # RPM 2 reached
    assert q.wait_seconds("free", "other-model", 10, now) == 0         # limits are per model
    assert q.wait_seconds("paid", "m", 10, now) == 0                   # and per project
    later = now + 61
    assert q.wait_seconds("free", "m", 900, later) == 0                # the minute has passed
    q.record("free", "m", 900, later)
    assert q.wait_seconds("free", "m", 200, later) > 0                 # TPM: 900 + 200 > 1000
    q.record("free", "m", 10, later + 61)                              # 4th request today
    assert q.wait_seconds("free", "m", 10, later + 200) > 3600         # RPD 3 exceeded: wait for the day


def test_retry_after_header_body_delay_and_daily_quota_detection():
    assert parse_retry_delay({"retry-after": "17"}) == 17.0
    body = {"error": {"details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "37s"}]}}
    assert parse_retry_delay({}, body) == 37.0
    daily = {"error": {"details": [{"violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel"}]}]}}
    assert is_daily_quota(daily) and not is_daily_quota(body)


def test_a_429_cools_every_key_of_the_same_project_for_googles_delay(monkeypatch):
    q = _quota(monkeypatch, "proj-a,proj-a", None)
    seen = []

    def handler(request):
        seen.append((request.url.path.split("/")[-1].split(":")[0], request.headers["x-goog-api-key"]))
        return httpx.Response(429, json={"error": {"details": [{"retryDelay": "30s"}]}})

    _patch_http(monkeypatch, handler)
    gw = G.LLMGateway(_runtime(), "detailed", budget=5, concurrency=1, capture=False)
    asyncio.run(gw._gemini_once("p", "", 100, 0.2, True, 5, set(), tokens=10))
    # one 429 on proj-a/model-a cools that pool: the second key of proj-a is not tried on model-a
    assert [k for m, k in seen if m == "model-a"] == ["AIzaKEY1"]
    assert q.wait_seconds("proj-a", "model-a", 1) >= 29
    assert gw.stats.http_status["429"] >= 1


def test_independent_projects_keep_serving_when_one_is_rate_limited(monkeypatch):
    _quota(monkeypatch, "proj-a,proj-b", None)

    def handler(request):
        key = request.headers["x-goog-api-key"]
        return httpx.Response(429, json={}) if key == "AIzaKEY1" else _ok("from project b")

    _patch_http(monkeypatch, handler)
    gw = G.LLMGateway(_runtime(), "detailed", budget=5, concurrency=1, capture=False)
    res = asyncio.run(gw.generate(purpose="outline", prompt="p", json_mode=False))
    assert res.ok and res.text == "from project b"


def test_requests_wait_when_every_project_is_out_of_quota(monkeypatch):
    q = _quota(monkeypatch, "proj-a,proj-a", {"default": {"rpm": 1}})
    import time as _t
    for model in ("model-a", "model-b"):
        q.record("proj-a", model, 1, _t.time() - 59.5)   # the minute's request was used 59.5 s ago
    _patch_http(monkeypatch, lambda request: _ok("after the wait"))
    waits = []

    async def fake_sleep(delay):
        waits.append(delay)
        q._requests.clear()  # the window has passed

    monkeypatch.setattr(G.asyncio, "sleep", fake_sleep)
    gw = G.LLMGateway(_runtime(), "detailed", budget=5, concurrency=1, capture=False)
    res = asyncio.run(gw.generate(purpose="outline", prompt="p"))
    assert res.ok and res.text == "after the wait"
    assert waits and waits[0] <= 2 and gw.stats.quota_waits == 1   # waited only until the window frees


def test_a_short_fallback_answer_is_kept_only_until_gemini_answers(monkeypatch):
    _quota(monkeypatch, "", None)
    monkeypatch.setenv("GG_V2_RATE_LIMIT_BACKOFF", "20,40")   # other suites switch the waits off
    monkeypatch.setenv("GG_V2_COOLDOWN_BACKOFF", "20,45")
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(429, json={}) if calls["n"] <= 4 else _ok("full gemini section " * 100)

    async def short_groq(prompt, tier):
        return "too short", "groq-model"

    _patch_http(monkeypatch, handler)
    rt = _runtime()
    rt._call_groq_plain_continuation = short_groq
    real_sleep = asyncio.sleep

    async def no_wait(delay):
        rt.limited.clear()  # cooldowns expire while the call waits, as they do in production
        await real_sleep(0)

    monkeypatch.setattr(G.asyncio, "sleep", no_wait)
    gw = G.LLMGateway(rt, "in_depth", budget=5, concurrency=1, capture=False)
    res = asyncio.run(gw.generate(purpose="section", prompt="p", min_words=200))
    assert res.ok and res.model.startswith("model") and gw.stats.short_fallbacks_retried == 1
    assert len(res.text.split()) >= 200
