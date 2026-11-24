"""LLM gateway for the section-by-section report pipeline (REPORT_PIPELINE_V2).

Reuses the report writer's Gemini key ring and cooldown bookkeeping from
routes/report.py (passed in as ``runtime``) so 401/403/404/429/503 handling is
identical to the existing writer: bad keys are parked, a 503 cools the model,
the next model/key is tried, and Groq is the last resort.

Adds what the V2 pipeline needs:
  * configurable system prompt, output size and JSON mode;
  * a per-report call budget and a concurrency limit;
  * per-call timeout with one retry on a different model/key;
  * token accounting from Gemini usageMetadata (estimated for fallbacks);
  * every call recorded in the research capture (full prompt/response,
    secret-redacted) so later stages can be replayed offline.

A failed call returns ``LLMResult(ok=False, ...)``; it never raises, so one
failed section cannot abort the report.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from collections import Counter
from dataclasses import dataclass, field

import httpx

log = logging.getLogger("llm_gateway")

# Calls that write the report's prose. They wait for a full model rather than use a "lite" one, which wrote
# sections at roughly half the requested length; the lite model is used only after the cooldown waits.
LITE_AVOIDING_PURPOSES = {"section", "section_extend", "executive_summary"}
# Checks that read and classify (the claim judge) start on the lite model: the full models' per-minute quota
# goes to the writing calls (live run: 121 of 167 Gemini attempts were HTTP 429).
# dataset_extract proposals are verified value by value against the passage in code, so the lite model (higher
# rate limits) is enough; on the full models 16 small calls took 9 minutes of rate-limit waits in a live run
LITE_FIRST_PURPOSES = {"claim_check", "dataset_extract"}
# Report prose that Gemini cannot serve right now goes to Groq at once instead of after 20-45 s sleeps: in the
# in-depth sector-rotation run every core section waited out Gemini cooldowns (about 20 min of sleeps across
# calls) and was then written by Groq anyway. The waits remain when Groq also fails.
GROQ_BEFORE_WAIT_PURPOSES = {"section", "section_extend", "executive_summary"}

DEFAULT_BUDGETS = {"detailed": 30, "in_depth": 90}
# Model calls in flight at once per report. Deep Research has twice the sections and 16 keys across several models;
# at 5 the sixth section of each wave queued behind the others (gold live run: waves of 260 s and 332 s).
DEFAULT_CONCURRENCY = {"detailed": 5, "in_depth": 8}
# Hedged calls: a call still unanswered after this many seconds gets a second attempt on another model, and the
# first answer wins. Section calls had a median of 38 s but a slow tail of 160-206 s (gold live run), and every
# wave waited for its slowest section. GG_V2_HEDGE=0 turns it off; GG_V2_HEDGE_AFTER_S sets the prose delay.
HEDGE_AFTER_SECONDS = {"section": 75.0, "section_extend": 75.0, "executive_summary": 75.0,
                       "measure_plan": 40.0, "dataset_extract": 40.0}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


@dataclass
class LLMResult:
    ok: bool
    text: str = ""
    model: str = ""
    error: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    elapsed_ms: int = 0
    attempts: int = 0

    def json(self) -> dict | list | None:
        raw = (self.text or "").strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            raw = raw[raw.find("\n") + 1:] if "\n" in raw else raw
        start = min([i for i in (raw.find("{"), raw.find("[")) if i >= 0], default=-1)
        if start < 0:
            return None
        end = max(raw.rfind("}"), raw.rfind("]"))
        try:
            return json.loads(raw[start:end + 1])
        except (ValueError, TypeError):
            return None


@dataclass
class GatewayStats:
    calls: int = 0
    succeeded: int = 0
    failed: int = 0
    budget_refusals: int = 0
    network_retries: int = 0
    groq_before_wait: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    by_purpose: Counter = field(default_factory=Counter)
    models: Counter = field(default_factory=Counter)

    def to_dict(self) -> dict:
        return {"calls": self.calls, "succeeded": self.succeeded, "failed": self.failed,
                "budgetRefusals": self.budget_refusals, "networkRetries": self.network_retries, "groqBeforeWait": self.groq_before_wait, "tokensIn": self.tokens_in, "tokensOut": self.tokens_out,
                "byPurpose": dict(self.by_purpose), "models": dict(self.models)}


class LLMGateway:
    def __init__(self, runtime, report_tier: str, *, budget: int | None = None, concurrency: int | None = None,
                 capture: bool = True) -> None:
        self.runtime = runtime
        self.tier = str(report_tier or "detailed")
        self.budget = budget if budget is not None else _env_int(
            "GG_V2_LLM_CALL_BUDGET_" + self.tier.upper(), DEFAULT_BUDGETS.get(self.tier, 30))
        # 5 calls at once. Live run G (5 at once) hit 16 rate-limit responses before rate-limited calls tried Groq
        # first and released their slot while waiting; with 16 keys, the last runs at 3 had no rate-limit waits.
        # Set GG_V2_SECTION_CONCURRENCY=3 to go back if a run shows rate-limit cooldowns again.
        self.semaphore = asyncio.Semaphore(max(1, concurrency or _env_int(
            "GG_V2_SECTION_CONCURRENCY", DEFAULT_CONCURRENCY.get(self.tier, 5))))
        self.capture = capture
        self.stats = GatewayStats()
        self._key_cursor = 0  # rotates keys across calls so one key's rate limit is not hit by a long run
        # model -> {key: time of its last 429}; two keys throttled on one model within a minute means the
        # MODEL is rate-limited (a per-project quota), so the model is cooled instead of being retried first
        # by every following call.
        self._model_429: dict[str, dict[str, float]] = {}
        self.stats_rate_limited_models = 0
        self.stats_rejected_models: set[str] = set()  # models that answered HTTP 400 (skipped for 10 min)
        self.stats_hedges = 0       # second attempts started for slow calls
        self.stats_hedge_wins = 0   # ... that answered first

    @property
    def remaining(self) -> int:
        """Logical calls still available to this report, including concurrent work."""
        return max(0, int(self.budget) - int(self.stats.calls))

    # ── public ────────────────────────────────────────────────────────────────
    async def _sleep_without_slot(self, delay: float) -> None:
        """Wait out a backoff without holding a concurrency slot: a sleeping call blocked one of the 3 slots, so
        other sections queued behind calls that were only waiting for a rate limit to clear."""
        self.semaphore.release()
        try:
            await asyncio.sleep(delay)
        finally:
            await self.semaphore.acquire()

    async def generate(self, *, purpose: str, prompt: str, system: str = "", section_id: str = "",
                       max_tokens: int = 4096, temperature: float = 0.2, json_mode: bool = False,
                       timeout: float = 90.0, retries: int = 1) -> LLMResult:
        async with self.semaphore:
            # Reserve a logical call only after entering the semaphore. Checking
            # before awaiting allowed many queued coroutines to all pass the
            # budget check and overspend the per-report allowance.
            if self.stats.calls >= self.budget:
                self.stats.budget_refusals += 1
                log.warning("V2 LLM budget exhausted (%d calls); %s/%s not run", self.budget, purpose, section_id)
                return LLMResult(ok=False, error="budget_exhausted")
            self.stats.calls += 1
            self.stats.by_purpose[purpose] += 1
            started = time.perf_counter()
            result = LLMResult(ok=False, error="no_attempt")
            tried: set[tuple[str, str]] = set()
            backoff = [4, 8, 16]  # exponential; extra tries after network failures and HTTP 5xx only
            rate_backoff = [int(x) for x in os.getenv("GG_V2_RATE_LIMIT_BACKOFF", "20,40").split(",") if x.strip().isdigit()]
            # Every model already cooling (after 429/503s from other calls): the 60 s cooldowns clear, so wait them
            # out instead of failing at once. A live run lost two whole sections this way within one second.
            cooldown_backoff = [int(x) for x in os.getenv("GG_V2_COOLDOWN_BACKOFF", "20,45").split(",") if x.strip().isdigit()]
            # Report prose is written by the full models: under rate limits half the sections went to the lite
            # model and the report came out at about half its planned length. Lite stays a last resort.
            allow_lite = purpose not in LITE_AVOIDING_PURPOSES
            groq_tried = False
            attempt = 0
            while True:
                attempt += 1
                result = await self._gemini_once_hedged(purpose, section_id, prompt, system, max_tokens, temperature,
                                                        json_mode, timeout, tried, allow_lite,
                                                        prefer_lite=purpose in LITE_FIRST_PURPOSES)
                result.attempts = attempt
                if result.ok:
                    break
                log.warning("V2 LLM %s/%s attempt %d failed: %s", purpose, section_id or "-", attempt, result.error)
                network = bool(re.match(r"(?:network:|http_5\d\d)", str(result.error or "")))
                if attempt <= retries:
                    if network:
                        tried.clear()  # the same key/model pairs may be retried after a network failure
                    continue
                if network and backoff:
                    delay = backoff.pop(0)
                    self.stats.network_retries += 1
                    log.warning("V2 LLM %s/%s: transient failure (%s); retrying in %ds", purpose, section_id or "-",
                                str(result.error or "")[:60], delay)
                    await self._sleep_without_slot(delay)
                    tried.clear()
                    continue
                rate_limited = (str(result.error or "") == "no_gemini_key_or_model_available"
                                or str(result.error or "").startswith("http_429"))
                if (rate_limited and not json_mode and purpose in GROQ_BEFORE_WAIT_PURPOSES and not groq_tried
                        and os.getenv("GG_V2_GROQ_BEFORE_WAIT", "1") != "0"):
                    groq_tried = True
                    fallback = await self._groq_fallback(prompt, system, result)
                    if fallback.ok:
                        result = fallback
                        self.stats.groq_before_wait += 1
                        break
                # Stage K: every key/model rate-limited (HTTP 429) - per-minute limits clear quickly, so wait and
                # retry twice (20 s, 40 s) before giving up; a daily-quota failure still fails (and is labelled)
                if str(result.error or "") == "no_gemini_key_or_model_available" and not cooldown_backoff and not allow_lite:
                    allow_lite = True  # every full model still cooling after the waits: the lite model before Groq
                    log.warning("V2 LLM %s/%s: full models still unavailable; using the lite model", purpose, section_id or "-")
                    tried.clear()
                    continue
                if str(result.error or "") == "no_gemini_key_or_model_available" and cooldown_backoff:
                    delay = cooldown_backoff.pop(0)
                    self.stats.network_retries += 1
                    log.warning("V2 LLM %s/%s: every model is cooling down; retrying in %ds", purpose, section_id or "-", delay)
                    await self._sleep_without_slot(delay)
                    tried.clear()
                    continue
                if str(result.error or "").startswith("http_429") and rate_backoff:
                    delay = rate_backoff.pop(0)
                    self.stats.network_retries += 1
                    log.warning("V2 LLM %s/%s: rate-limited on every key; retrying in %ds", purpose, section_id or "-", delay)
                    await self._sleep_without_slot(delay)
                    tried.clear()
                    continue
                break
            if not result.ok and not json_mode and not groq_tried:
                result = await self._groq_fallback(prompt, system, result)
            result.elapsed_ms = int((time.perf_counter() - started) * 1000)
            if not result.tokens_in:
                result.tokens_in = max(1, (len(system) + len(prompt)) // 4)
            if result.ok and not result.tokens_out:
                result.tokens_out = max(1, len(result.text) // 4)
            self.stats.tokens_in += result.tokens_in
            self.stats.tokens_out += result.tokens_out
            if result.ok:
                self.stats.succeeded += 1
                self.stats.models[result.model] += 1
            else:
                self.stats.failed += 1
            if self.capture:
                try:
                    from utils.research_capture import record_llm_call
                    record_llm_call(purpose=purpose, section_id=section_id, model=result.model, system=system,
                                    prompt=prompt, response=result.text, ok=result.ok, error=result.error,
                                    tokens_in=result.tokens_in, tokens_out=result.tokens_out,
                                    elapsed_ms=result.elapsed_ms, attempt=result.attempts,
                                    params={"maxTokens": max_tokens, "temperature": temperature, "json": json_mode})
                except Exception:  # capture must never break generation
                    pass
            return result

    # ── providers ─────────────────────────────────────────────────────────────
    def _hedge_delay(self, purpose: str) -> float | None:
        if os.getenv("GG_V2_HEDGE", "1").strip().casefold() in {"0", "false", "no", "off"}:
            return None
        delay = HEDGE_AFTER_SECONDS.get(purpose)
        if delay is None:
            return None
        if purpose in {"section", "section_extend", "executive_summary"}:
            try:
                delay = float(os.getenv("GG_V2_HEDGE_AFTER_S", "") or delay)
            except ValueError:
                pass
        return max(0.05, delay)

    async def _gemini_once_hedged(self, purpose, section_id, prompt, system, max_tokens, temperature, json_mode,
                                  timeout, tried, allow_lite: bool = True, prefer_lite: bool = False) -> LLMResult:
        """One attempt, plus a second one on a different model if the first is still running after the delay."""
        delay = self._hedge_delay(purpose)
        first = asyncio.ensure_future(self._gemini_once(prompt, system, max_tokens, temperature, json_mode, timeout,
                                                        tried, allow_lite, prefer_lite))
        if delay is None:
            return await first
        done, _ = await asyncio.wait({first}, timeout=delay)
        if done:
            return first.result()
        busy_models = {model for model, _key in tried}
        if not self._attempt_plan(tried, allow_lite, prefer_lite, avoid_models=busy_models, advance=False):
            return await first  # no other model free: keep waiting on the first
        self.stats_hedges += 1
        log.info("V2 gateway: %s/%s still running after %.0fs; started a second attempt on another model",
                 purpose, section_id or "-", delay)
        second = asyncio.ensure_future(self._gemini_once(prompt, system, max_tokens, temperature, json_mode, timeout,
                                                         tried, allow_lite, prefer_lite, avoid_models=busy_models))
        pending = {first, second}
        failure: LLMResult | None = None
        try:
            while pending:
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    result = task.result() if not task.cancelled() and task.exception() is None else LLMResult(
                        ok=False, error=f"hedge {type(task.exception()).__name__ if not task.cancelled() else 'cancelled'}")
                    if result.ok:
                        if task is second:
                            self.stats_hedge_wins += 1
                        return result
                    failure = result
            return failure or LLMResult(ok=False, error="no_response")
        finally:
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

    def _attempt_plan(self, tried: set, allow_lite: bool = True, prefer_lite: bool = False,
                      avoid_models: set | None = None, advance: bool = True) -> list[tuple[str, str]]:
        rt = self.runtime
        keys = [k for k in rt.get_gemini_keys() if rt._is_rest_api_key(k)]
        if not keys:
            return []
        ring = rt.round_robin(keys)
        plan = []
        for model in rt.GEMINI_MODELS:
            if rt._gemini_model_cooling(model) or (not allow_lite and "lite" in str(model).casefold()):
                continue
            if avoid_models and model in avoid_models:
                continue
            healthy = [k for k in rt._gemini_candidate_keys(ring, model, len(ring))
                       if not rt.is_rate_limited(k) and (model, k) not in tried]
            if healthy:
                plan.append((model, healthy[self._key_cursor % len(healthy)]))
        if advance:
            self._key_cursor += 1
        if prefer_lite:
            plan.sort(key=lambda item: "lite" not in str(item[0]).casefold())
        return plan

    async def _gemini_once(self, prompt, system, max_tokens, temperature, json_mode, timeout, tried,
                           allow_lite: bool = True, prefer_lite: bool = False,
                           avoid_models: set | None = None) -> LLMResult:
        rt = self.runtime
        plan = self._attempt_plan(tried, allow_lite, prefer_lite, avoid_models=avoid_models)
        if not plan:
            return LLMResult(ok=False, error="no_gemini_key_or_model_available")
        last_error = "no_response"
        for model, key in plan[:3]:
            # The plan was made when this call started; a slow first attempt can take minutes, during which
            # another call may have found a model broken (HTTP 400) or overloaded. Re-check before using it.
            if rt._gemini_model_cooling(model):
                last_error = f"model_cooling model={model}"
                continue
            tried.add((model, key))
            config = {"maxOutputTokens": int(max_tokens), "temperature": float(temperature)}
            if json_mode:
                config["responseMimeType"] = "application/json"
            body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                    "generationConfig": rt._gemini_gen_config(model, config)}
            if system:
                body["system_instruction"] = {"parts": [{"text": system}]}
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(connect=8, read=timeout, write=15, pool=8)) as client:
                    response = await client.post(
                        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                        headers={"x-goog-api-key": key, "Content-Type": "application/json"}, json=body,
                    )
            except (httpx.TimeoutException, asyncio.TimeoutError):
                rt.mark_rate_limited(f"report:gemini:{key}:{model}", rt.GEMINI_TIMEOUT_KEY_COOLDOWN_MS)
                last_error = f"timeout model={model}"
                continue
            except (httpx.ConnectError, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
                # A network failure is not the key's or the model's fault: no cooldown (live run G: a
                # ConnectError cooled every key/model, so the retry found nothing and V2 fell back).
                last_error = f"network:{type(exc).__name__} model={model}"
                continue
            except Exception as exc:
                rt.mark_rate_limited(f"report:gemini:{key}:{model}", rt.GEMINI_KEY_COOLDOWN_MS)
                last_error = f"{type(exc).__name__} model={model}"
                continue
            status = response.status_code
            if status in (401, 403):
                rt.mark_rate_limited(key, 24 * 60 * 60_000)
                last_error = f"http_{status}"
                continue
            if status == 404:
                rt.mark_rate_limited(f"report:gemini:{key}:{model}", 24 * 60 * 60_000)
                last_error = f"http_404 model={model}"
                continue
            if status == 429:
                rt.mark_rate_limited(key, rt.GEMINI_KEY_COOLDOWN_MS)
                rt.mark_rate_limited(f"report:gemini:{key}:{model}", rt.GEMINI_KEY_COOLDOWN_MS)
                now = time.monotonic()
                recent = {k: t for k, t in self._model_429.get(model, {}).items() if now - t < 60}
                recent[key] = now
                self._model_429[model] = recent
                if len(recent) >= 2:
                    rt.mark_rate_limited(f"gemini:model:{model}", _env_int("GG_V2_MODEL_429_COOLDOWN_MS", 60_000))
                    self.stats_rate_limited_models += 1
                    log.info("V2 gateway: %s rate-limited on %d keys within 60s; model cooled", model, len(recent))
                last_error = f"http_429 model={model}"
                continue
            if status == 503:
                rt.mark_rate_limited(f"gemini:model:{model}", rt.GEMINI_MODEL_COOLDOWN_MS)
                last_error = f"http_503 model={model}"
                continue
            if status == 400:
                # The model rejects the request itself (live run: gemini-3.8-flash and gemini-3.7-flash failed
                # 9 of 9 calls with 400 and were retried on every section). Cool the model so the other models
                # take the calls; any other key would get the same 400.
                rt.mark_rate_limited(f"gemini:model:{model}", _env_int("GG_V2_MODEL_400_COOLDOWN_MS", 600_000))
                self.stats_rejected_models.add(model)
                log.warning("V2 gateway: %s rejected the request (HTTP 400); model skipped for the next 10 min", model)
                last_error = f"http_400 model={model}"
                continue
            if not response.is_success:
                last_error = f"http_{status} model={model}"
                continue
            try:
                payload = response.json()
            except ValueError:
                last_error = f"invalid_json_body model={model}"
                continue
            parts, finish = [], ""
            for candidate in payload.get("candidates") or []:
                finish = str(candidate.get("finishReason") or finish)
                for part in ((candidate.get("content") or {}).get("parts") or []):
                    if isinstance(part, dict) and part.get("text") and not part.get("thought"):
                        parts.append(str(part["text"]))
            text = "\n".join(parts).strip()
            usage = payload.get("usageMetadata") or {}
            if not text:
                last_error = f"empty_response model={model} finish={finish or 'unknown'}"
                continue
            return LLMResult(ok=True, text=text, model=model,
                             error="truncated" if finish == "MAX_TOKENS" else "",
                             tokens_in=int(usage.get("promptTokenCount") or 0),
                             tokens_out=int(usage.get("candidatesTokenCount") or 0))
        return LLMResult(ok=False, error=last_error)

    async def _groq_fallback(self, prompt: str, system: str, previous: LLMResult) -> LLMResult:
        try:
            text, model = await self.runtime._call_groq_plain_continuation((system + "\n\n" + prompt).strip(), self.tier)
        except Exception as exc:
            return LLMResult(ok=False, error=f"{previous.error}; groq {type(exc).__name__}")
        text = str(text or "").strip()
        if not text or text == "NO_ADDITIONAL_EVIDENCE":
            return LLMResult(ok=False, error=f"{previous.error}; groq empty")
        return LLMResult(ok=True, text=text, model=str(model or "groq"))
