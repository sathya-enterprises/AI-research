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
import contextvars
import json
import logging
import os
import random
import re
import time
from collections import Counter, deque
from dataclasses import dataclass, field

import httpx

from utils import llm_telemetry as T
from utils.llm_quota import (QUOTAS, SHARED_PROJECT, estimate_tokens, is_daily_quota, parse_retry_delay,
                             quota_violations)

log = logging.getLogger("llm_gateway")

# the logical call an HTTP attempt belongs to (copied into hedged tasks, so their attempts are attributed too)
_CALL: contextvars.ContextVar[dict | None] = contextvars.ContextVar("gg_llm_call", default=None)
# a citation the section prompts ask for ([S12] / [S12.3]); a fallback answer to a prompt that carries passage
# ids must cite them too, like a Gemini answer
_PROMPT_PASSAGE_RE = re.compile(r"\[?\bS\d+\.\d+\b")
_ANSWER_CITE_RE = re.compile(r"\[S\d+(?:\.\d+)?(?:\s*[,;]\s*S\d+(?:\.\d+)?)*\]")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


class AdaptiveLimiter:
    """Calls in flight at once, adjusted to how Google is answering.

    Starts at the configured concurrency (5). Overload (a 429, or a circuit-breaker trip) lowers the limit by one,
    at most once per ``decrease_every`` seconds and never below ``minimum``; after ``increase_after`` successes in
    a row it rises by one, never above ``maximum``. ``maximum`` defaults to the starting value, so by default the
    limit only ever comes back to where it started - more keys never mean more concurrency on their own.
    Waiting is loop-agnostic (futures are made on the running loop), so one gateway can be used from several
    ``asyncio.run`` calls.
    """

    def __init__(self, initial: int, *, minimum: int | None = None, maximum: int | None = None,
                 increase_after: int | None = None, decrease_every: float | None = None) -> None:
        initial = max(1, int(initial))
        self.initial = initial
        self.maximum = max(initial, int(maximum if maximum is not None else _env_int("GG_V2_CONCURRENCY_MAX", initial)))
        self.minimum = max(1, min(initial, int(minimum if minimum is not None else _env_int("GG_V2_CONCURRENCY_MIN", 3))))
        self.increase_after = max(1, int(increase_after if increase_after is not None
                                         else _env_int("GG_V2_CONCURRENCY_INCREASE_AFTER", 10)))
        self.decrease_every = float(decrease_every if decrease_every is not None
                                    else _env_float("GG_V2_CONCURRENCY_DECREASE_EVERY_S", 30.0))
        self.limit = initial
        self.in_use = 0
        self._waiters: deque = deque()
        self._streak = 0
        self._last_decrease = float("-inf")
        self.decreases = 0
        self.increases = 0
        self.lowest = initial

    @property
    def _value(self) -> int:  # asyncio.Semaphore compatibility: slots free now
        return max(0, self.limit - self.in_use)

    def locked(self) -> bool:
        return self.in_use >= self.limit

    async def acquire(self) -> bool:
        while self.in_use >= self.limit:
            waiter = asyncio.get_running_loop().create_future()
            self._waiters.append(waiter)
            try:
                await waiter
            finally:
                if waiter in self._waiters:
                    self._waiters.remove(waiter)
        self.in_use += 1
        return True

    def release(self) -> None:
        self.in_use = max(0, self.in_use - 1)
        self._wake()

    def _wake(self) -> None:
        free = self.limit - self.in_use
        while free > 0 and self._waiters:
            waiter = self._waiters.popleft()
            if not waiter.done():
                try:
                    waiter.get_loop().call_soon_threadsafe(lambda w=waiter: w.done() or w.set_result(True))
                except RuntimeError:  # its loop has closed
                    continue
                free -= 1

    async def __aenter__(self):
        await self.acquire()
        return self

    async def __aexit__(self, *exc) -> None:
        self.release()

    def on_success(self) -> None:
        self._streak += 1
        if self._streak >= self.increase_after and self.limit < self.maximum:
            self.limit += 1
            self.increases += 1
            self._streak = 0
            log.info("V2 gateway: concurrency raised to %d", self.limit)
            self._wake()

    def on_overload(self) -> None:
        self._streak = 0
        now = time.monotonic()
        if self.limit > self.minimum and now - self._last_decrease >= self.decrease_every:
            self.limit -= 1
            self.decreases += 1
            self.lowest = min(self.lowest, self.limit)
            self._last_decrease = now
            log.info("V2 gateway: concurrency lowered to %d after overload", self.limit)

    def to_dict(self) -> dict:
        return {"initial": self.initial, "min": self.minimum, "max": self.maximum, "final": self.limit,
                "lowest": self.lowest, "decreases": self.decreases, "increases": self.increases}

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
# Model calls in flight at once per report. The keys share one Gemini project quota: 8 at once (live run
# 2026-11-24) raised HTTP 429s from 9 to 25 and 503s from 16 to 31, and every call ran ~3x slower, so 5 stays.
DEFAULT_CONCURRENCY = {"detailed": 5, "in_depth": 5}
# Hedged calls: a call still unanswered after this many seconds gets a second attempt on another model, and the
# first answer wins. Section calls had a median of 38 s but a slow tail of 160-206 s (gold live run), and every
# wave waited for its slowest section. Off by default: under the shared quota the second attempts added load
# (9 hedges, 1 win in a live run). GG_V2_HEDGE=1 turns it on; GG_V2_HEDGE_AFTER_S sets the prose delay.
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
    # telemetry: every HTTP answer by status and by model+status, seconds per purpose / model, waits and fallbacks
    http_status: Counter = field(default_factory=Counter)
    model_status: Counter = field(default_factory=Counter)
    seconds_by_purpose: Counter = field(default_factory=Counter)
    seconds_by_model: Counter = field(default_factory=Counter)
    quota_waits: int = 0
    quota_wait_seconds: float = 0.0
    fallbacks: int = 0
    short_fallbacks_retried: int = 0
    fallbacks_without_citations: int = 0
    telemetry: object = None  # callable -> dict, set by the gateway (request totals, breaker, concurrency)

    def to_dict(self) -> dict:
        extra = {}
        if callable(self.telemetry):
            try:
                extra = self.telemetry()
            except Exception:  # telemetry must never break a report
                extra = {}
        return {**extra, "calls": self.calls, "succeeded": self.succeeded, "failed": self.failed,
                "fallbacksWithoutCitations": self.fallbacks_without_citations,
                "budgetRefusals": self.budget_refusals, "networkRetries": self.network_retries, "groqBeforeWait": self.groq_before_wait, "tokensIn": self.tokens_in, "tokensOut": self.tokens_out,
                "byPurpose": dict(self.by_purpose), "models": dict(self.models),
                "httpStatus": dict(self.http_status), "modelStatus": dict(self.model_status),
                "secondsByPurpose": {k: round(v, 1) for k, v in self.seconds_by_purpose.items()},
                "secondsByModel": {k: round(v, 1) for k, v in self.seconds_by_model.items()},
                "quotaWaits": self.quota_waits, "quotaWaitSeconds": round(self.quota_wait_seconds, 1),
                "fallbacks": self.fallbacks, "shortFallbacksRetried": self.short_fallbacks_retried}


class LLMGateway:
    def __init__(self, runtime, report_tier: str, *, budget: int | None = None, concurrency: int | None = None,
                 capture: bool = True, report_id: str = "") -> None:
        self.report_id = str(report_id or "")
        self.requests = T.RequestLog()
        # circuit breaker: model -> kind -> {project: time of its last failure}; trips per model; models letting
        # one request through after a trip (half-open) until a success
        self._model_failures: dict[tuple[str, str], dict[str, float]] = {}
        self._breaker_trips: Counter = Counter()
        self._half_open: set[str] = set()
        self._model_inflight: Counter = Counter()
        self.breaker_log: list[dict] = []
        self.quota_hits: dict[str, dict] = {}  # quotaId/model -> Google's limit and how often it was hit
        self.runtime = runtime
        self.tier = str(report_tier or "detailed")
        self.budget = budget if budget is not None else _env_int(
            "GG_V2_LLM_CALL_BUDGET_" + self.tier.upper(), DEFAULT_BUDGETS.get(self.tier, 30))
        # 5 calls at once. Live run G (5 at once) hit 16 rate-limit responses before rate-limited calls tried Groq
        # first and released their slot while waiting; with 16 keys, the last runs at 3 had no rate-limit waits.
        # Set GG_V2_SECTION_CONCURRENCY=3 to go back if a run shows rate-limit cooldowns again.
        self.semaphore = AdaptiveLimiter(max(1, concurrency or _env_int(
            "GG_V2_SECTION_CONCURRENCY", DEFAULT_CONCURRENCY.get(self.tier, 5))))
        self.capture = capture
        self.stats = GatewayStats()
        self.stats.telemetry = self.telemetry
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
            await asyncio.sleep(delay * (1.0 + random.random() * 0.2))  # jitter: waiting calls do not retry together
        finally:
            await self.semaphore.acquire()

    async def generate(self, *, purpose: str, prompt: str, system: str = "", section_id: str = "",
                       max_tokens: int = 4096, temperature: float = 0.2, json_mode: bool = False,
                       timeout: float = 90.0, retries: int = 1, min_words: int = 0) -> LLMResult:
        """One logical model call. ``min_words``: a fallback (Groq) answer shorter than this is kept only as a last
        resort - the call keeps waiting for Gemini, so a busy minute does not shorten a section."""
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
            _CALL.set({"call_id": T.new_id(), "purpose": purpose, "section_id": section_id, "n": 0})
            needs_citations = purpose in GROQ_BEFORE_WAIT_PURPOSES and bool(_PROMPT_PASSAGE_RE.search(prompt or ""))
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
            tokens_est = estimate_tokens(system, prompt)
            quota_waits_left = 3
            short_fallback: LLMResult | None = None
            while True:
                attempt += 1
                result = await self._gemini_once_hedged(purpose, section_id, prompt, system, max_tokens, temperature,
                                                        json_mode, timeout, tried, allow_lite,
                                                        prefer_lite=purpose in LITE_FIRST_PURPOSES, tokens=tokens_est)
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
                    uncited = fallback.ok and needs_citations and not _ANSWER_CITE_RE.search(fallback.text)
                    if uncited:
                        # the same rule as a Gemini answer: section prose cites its passages. An uncited fallback
                        # is kept only if Gemini never answers (the section checks then drop its claims).
                        self.stats.fallbacks_without_citations += 1
                        short_fallback = fallback
                        log.info("V2 LLM %s/%s: fallback answer has no citations; waiting for Gemini",
                                 purpose, section_id or "-")
                    elif fallback.ok and (min_words <= 0 or len(fallback.text.split()) >= min_words):
                        result = fallback
                        self.stats.groq_before_wait += 1
                        break
                    elif fallback.ok:
                        # substantially shorter than the section needs: kept only if Gemini never answers
                        short_fallback = fallback
                        self.stats.short_fallbacks_retried += 1
                        log.info("V2 LLM %s/%s: fallback answer too short (%d < %d words); waiting for Gemini",
                                 purpose, section_id or "-", len(fallback.text.split()), min_words)
                # Stage K: every key/model rate-limited (HTTP 429) - per-minute limits clear quickly, so wait and
                # retry twice (20 s, 40 s) before giving up; a daily-quota failure still fails (and is labelled)
                # Blocked only by configured quota (no cooldown): wait exactly until a pool has room, with jitter
                quota_wait = self._min_quota_wait(tokens_est, allow_lite) if str(result.error or "") ==                     "no_gemini_key_or_model_available" else 0.0
                if quota_wait > 0 and quota_waits_left:
                    quota_waits_left -= 1
                    delay = min(120.0, quota_wait) * (1.0 + random.random() * 0.2)
                    self.stats.quota_waits += 1
                    self.stats.quota_wait_seconds += delay
                    log.info("V2 LLM %s/%s: quota full on every project; waiting %.0fs", purpose, section_id or "-", delay)
                    await self._sleep_without_slot(delay)
                    tried.clear()
                    continue
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
            if not result.ok and short_fallback is not None:
                result = short_fallback  # Gemini never answered: the shorter fallback is better than nothing
            if not result.ok and not json_mode and not groq_tried:
                result = await self._groq_fallback(prompt, system, result)
            result.elapsed_ms = int((time.perf_counter() - started) * 1000)
            self.stats.seconds_by_purpose[purpose] += result.elapsed_ms / 1000
            if result.ok:
                self.stats.seconds_by_model[result.model or "?"] += result.elapsed_ms / 1000
                if str(result.model or "").startswith("groq"):
                    self.stats.fallbacks += 1
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
        if os.getenv("GG_V2_HEDGE", "0").strip().casefold() in {"0", "false", "no", "off", ""}:
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
                                  timeout, tried, allow_lite: bool = True, prefer_lite: bool = False,
                                  tokens: int = 0) -> LLMResult:
        """One attempt, plus a second one on a different model if the first is still running after the delay."""
        delay = self._hedge_delay(purpose)
        first = asyncio.ensure_future(self._gemini_once(prompt, system, max_tokens, temperature, json_mode, timeout,
                                                        tried, allow_lite, prefer_lite, tokens=tokens))
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
                                                         tried, allow_lite, prefer_lite, avoid_models=busy_models,
                                                         tokens=tokens))
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

    def _min_quota_wait(self, tokens: int, allow_lite: bool = True) -> float:
        """Seconds until some (project, model) pool can take a request of ``tokens``; 0 when one can now or when
        the block is not a quota one (cooldowns, bans)."""
        rt = self.runtime
        keys = [k for k in rt.get_gemini_keys() if rt._is_rest_api_key(k) and not rt.is_rate_limited(k)]
        waits = []
        for model in rt.GEMINI_MODELS:
            if rt._gemini_model_cooling(model) or (not allow_lite and "lite" in str(model).casefold()):
                continue
            for project in {QUOTAS.project_of(k, keys) for k in keys}:
                waits.append(QUOTAS.wait_seconds(project, model, tokens))
        return min(waits) if waits and min(waits) > 0 else 0.0

    def _attempt_plan(self, tried: set, allow_lite: bool = True, prefer_lite: bool = False,
                      avoid_models: set | None = None, advance: bool = True, tokens: int = 0) -> list[tuple[str, str]]:
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
            if model in self._half_open and self._model_inflight[model] > 0:
                continue  # after a breaker trip one probe request at a time, until one succeeds
            # only keys whose PROJECT has room for this request's tokens (limits shared by all its keys)
            healthy = [k for k in rt._gemini_candidate_keys(ring, model, len(ring))
                       if not rt.is_rate_limited(k) and (model, k) not in tried
                       and QUOTAS.wait_seconds(QUOTAS.project_of(k, keys), model, tokens) <= 0]
            if healthy and QUOTAS.project_aware():
                # independent projects: the least busy, healthiest project; rotation breaks ties so equal
                # projects take turns instead of the first one taking every request
                n = len(healthy)
                order = {k: (i - self._key_cursor) % n for i, k in enumerate(healthy)}
                # each model of one plan goes to a different project when it can: a failed first attempt then
                # moves to another project instead of the one that just answered 429/503
                planned = {k for _m, k in plan}
                plan.append((model, min(healthy, key=lambda k: (k in planned,
                                                                QUOTAS.load(QUOTAS.project_of(k, keys), model),
                                                                order[k]))))
            elif healthy:
                plan.append((model, healthy[self._key_cursor % len(healthy)]))
        if advance:
            self._key_cursor += 1
        if prefer_lite:
            plan.sort(key=lambda item: "lite" not in str(item[0]).casefold())
        return plan

    async def _gemini_once(self, prompt, system, max_tokens, temperature, json_mode, timeout, tried,
                           allow_lite: bool = True, prefer_lite: bool = False,
                           avoid_models: set | None = None, tokens: int = 0) -> LLMResult:
        rt = self.runtime
        plan = self._attempt_plan(tried, allow_lite, prefer_lite, avoid_models=avoid_models, tokens=tokens)
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
            pool = [k for k in rt.get_gemini_keys() if rt._is_rest_api_key(k)]
            project = QUOTAS.project_of(key, pool)
            alias = QUOTAS.alias_of(key, pool)
            if QUOTAS.wait_seconds(project, model, tokens) > 0:
                last_error = f"quota_full model={model}"  # another call used this project's room meanwhile
                continue
            QUOTAS.record(project, model, tokens)
            config = {"maxOutputTokens": int(max_tokens), "temperature": float(temperature)}
            if json_mode:
                config["responseMimeType"] = "application/json"
            body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                    "generationConfig": rt._gemini_gen_config(model, config)}
            if system:
                body["system_instruction"] = {"parts": [{"text": system}]}
            sent_at, t0 = time.time(), time.perf_counter()
            QUOTAS.begin(project)
            self._model_inflight[model] += 1
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(connect=8, read=timeout, write=15, pool=8)) as client:
                    response = await client.post(
                        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                        headers={"x-goog-api-key": key, "Content-Type": "application/json"}, json=body,
                    )
            except (httpx.TimeoutException, asyncio.TimeoutError):
                rt.mark_rate_limited(f"report:gemini:{key}:{model}", rt.GEMINI_TIMEOUT_KEY_COOLDOWN_MS)
                last_error = f"timeout model={model}"
                self._record("gemini", model, alias, sent_at, t0, tokens, 0, T.TIMEOUT)
                QUOTAS.outcome(project, model, False)
                continue
            except (httpx.ConnectError, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
                # A network failure is not the key's or the model's fault: no cooldown (live run G: a
                # ConnectError cooled every key/model, so the retry found nothing and V2 fell back).
                last_error = f"network:{type(exc).__name__} model={model}"
                self._record("gemini", model, alias, sent_at, t0, tokens, 0, T.NETWORK)
                continue
            except Exception as exc:
                rt.mark_rate_limited(f"report:gemini:{key}:{model}", rt.GEMINI_KEY_COOLDOWN_MS)
                last_error = f"{type(exc).__name__} model={model}"
                self._record("gemini", model, alias, sent_at, t0, tokens, 0, T.OTHER)
                continue
            finally:
                QUOTAS.end(project)
                self._model_inflight[model] = max(0, self._model_inflight[model] - 1)
            status = response.status_code
            self.stats.http_status[str(status)] += 1
            self.stats.model_status[f"{model}:{status}"] += 1
            if status != 200:
                error_body = _safe_body(response) if status == 429 else None
                self._record("gemini", model, alias, sent_at, t0, tokens, status,
                             T.category(status, daily=status == 429 and is_daily_quota(error_body)))
                if status in (429, 503):
                    QUOTAS.outcome(project, model, False)
            if status in (401, 403):
                rt.mark_rate_limited(key, 24 * 60 * 60_000)
                last_error = f"http_{status}"
                continue
            if status == 404:
                rt.mark_rate_limited(f"report:gemini:{key}:{model}", 24 * 60 * 60_000)
                last_error = f"http_404 model={model}"
                continue
            if status == 429:
                self._note_quota_violations(error_body, model)
                self.semaphore.on_overload()
                if project != SHARED_PROJECT:
                    # the project's allowance for this model is used up: every key of the project is cooled for
                    # as long as Google says (Retry-After / RetryInfo), until tomorrow for a per-day quota
                    QUOTAS.on_429(project, model, response.headers, error_body)
                delay = parse_retry_delay(response.headers, error_body)
                key_ms = rt.GEMINI_KEY_COOLDOWN_MS
                if is_daily_quota(error_body):
                    key_ms = max(key_ms, int(float(os.getenv("GG_GEMINI_DAILY_RESET_HOURS", "6") or 6) * 3_600_000))
                elif delay:
                    key_ms = max(key_ms, int(delay * 1000))
                rt.mark_rate_limited(f"report:gemini:{key}:{model}", key_ms)
                if QUOTAS.project_aware():
                    # quotas are per project AND model: this project's other models, and every other project,
                    # keep serving. The model is cooled everywhere only when several independent projects are
                    # throttled on it within a minute (a model-level limit, not one project's allowance).
                    self._breaker(model, "429", project)
                else:
                    rt.mark_rate_limited(key, rt.GEMINI_KEY_COOLDOWN_MS)
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
                # model-serving capacity, not quota: nothing here says the project's allowance is used up
                delay = parse_retry_delay(response.headers, _safe_body(response))
                if QUOTAS.project_aware():
                    # cool this project/model briefly; the model is cooled for everyone only when a second
                    # independent project sees a 503 on it within a minute (circuit breaker)
                    QUOTAS.cool(project, model, max(_env_float("GG_GEMINI_503_PROJECT_COOLDOWN_S", 20.0), delay or 0.0),
                                "model capacity (503)")
                    self._breaker(model, "503", project, delay)
                else:
                    rt.mark_rate_limited(f"gemini:model:{model}", max(rt.GEMINI_MODEL_COOLDOWN_MS, int((delay or 0) * 1000)))
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
                self._record("gemini", model, alias, sent_at, t0, tokens, status, T.EMPTY)
                continue
            self._record("gemini", model, alias, sent_at, t0, tokens, status, T.OK,
                         tokens_in=int(usage.get("promptTokenCount") or 0),
                         tokens_out=int(usage.get("candidatesTokenCount") or 0))
            QUOTAS.outcome(project, model, True)
            self.semaphore.on_success()
            if model in self._half_open:
                self._half_open.discard(model)
                self._breaker_trips.pop(model, None)
                log.info("V2 gateway: %s answered again; circuit breaker closed", model)
            return LLMResult(ok=True, text=text, model=model,
                             error="truncated" if finish == "MAX_TOKENS" else "",
                             tokens_in=int(usage.get("promptTokenCount") or 0),
                             tokens_out=int(usage.get("candidatesTokenCount") or 0))
        return LLMResult(ok=False, error=last_error)

    async def _groq_fallback(self, prompt: str, system: str, previous: LLMResult) -> LLMResult:
        sent_at, t0 = time.time(), time.perf_counter()
        tokens = estimate_tokens(system, prompt)
        try:
            text, model = await self.runtime._call_groq_plain_continuation((system + "\n\n" + prompt).strip(), self.tier)
        except Exception as exc:
            self._record("groq", "groq", "groq", sent_at, t0, tokens, 0, T.OTHER, fallback=True)
            return LLMResult(ok=False, error=f"{previous.error}; groq {type(exc).__name__}")
        text = str(text or "").strip()
        if not text or text == "NO_ADDITIONAL_EVIDENCE":
            self._record("groq", str(model or "groq"), "groq", sent_at, t0, tokens, 200, T.EMPTY, fallback=True)
            return LLMResult(ok=False, error=f"{previous.error}; groq empty")
        self._record("groq", str(model or "groq"), "groq", sent_at, t0, tokens, 200, T.OK, fallback=True,
                     tokens_out=max(1, len(text) // 4))
        return LLMResult(ok=True, text=text, model=str(model or "groq"))

    # ── telemetry and circuit breaker ─────────────────────────────────────────
    def _record(self, provider: str, model: str, alias: str, sent_at: float, t0: float, tokens_est: int,
                status: int, category: str, *, tokens_in: int = 0, tokens_out: int = 0, fallback: bool = False) -> None:
        call = _CALL.get() or {"call_id": "-", "purpose": "", "section_id": "", "n": 0}
        call["n"] = int(call.get("n") or 0) + 1
        record = T.RequestRecord(
            request_id=T.new_id(), call_id=str(call["call_id"]), report_id=self.report_id,
            purpose=str(call.get("purpose") or ""), section_id=str(call.get("section_id") or ""),
            provider=provider, model=str(model), project=str(alias), started_at=sent_at,
            tokens_est=int(tokens_est or 0), tokens_in=int(tokens_in or 0), tokens_out=int(tokens_out or 0),
            latency_ms=int((time.perf_counter() - t0) * 1000), status=int(status or 0), error_category=category,
            attempt=call["n"], fallback=fallback, outcome="success" if category == T.OK else "failed")
        self.requests.add(record)
        log.info("V2 LLM request %s %s/%s %s %s %s status=%s %s %.1fs tokens=%d/%d attempt=%d", record.request_id,
                 record.purpose or "-", record.section_id or "-", provider, model, alias, status or "-", category,
                 record.latency_ms / 1000, record.tokens_in or record.tokens_est, record.tokens_out, record.attempt)

    def _breaker(self, model: str, kind: str, project: str, delay: float | None = None) -> bool:
        """Count a 429/503 from ``project`` on ``model``; when enough DIFFERENT projects fail on the model within
        a minute, the problem is the model (provider capacity, or a model-level limit), so the model is cooled for
        everyone: 30 s for 503 / 60 s for 429, doubling on each trip in a row (at most 5 min), with jitter, then
        one probe request at a time until it answers."""
        threshold = _env_int(f"GG_GEMINI_{kind}_BREAKER_PROJECTS", 2 if kind == "503" else 3)
        now = time.monotonic()
        recent = {p: t for p, t in self._model_failures.get((model, kind), {}).items() if now - t < 60}
        recent[project] = now
        self._model_failures[(model, kind)] = recent
        if len(recent) < max(1, threshold):
            return False
        self._breaker_trips[model] += 1
        base = _env_float(f"GG_GEMINI_{kind}_BREAKER_S", 30.0 if kind == "503" else 60.0)
        seconds = min(300.0, base * 2 ** (self._breaker_trips[model] - 1))
        seconds = max(seconds, delay or 0.0) * (1.0 + random.random() * 0.25)
        self.runtime.mark_rate_limited(f"gemini:model:{model}", int(seconds * 1000))
        self._half_open.add(model)
        self._model_failures[(model, kind)] = {}
        self.stats_rate_limited_models += 1
        self.semaphore.on_overload()
        self.breaker_log.append({"model": model, "kind": kind, "projects": len(recent), "seconds": round(seconds),
                                 "trip": self._breaker_trips[model]})
        log.info("V2 gateway: circuit breaker opened for %s (HTTP %s on %d projects within 60s); cooled %.0fs",
                 model, kind, len(recent), seconds)
        return True

    def _note_quota_violations(self, body: object, model: str) -> None:
        for v in quota_violations(body):
            key = f"{v['quotaId'] or v['quotaMetric']}/{v['model'] or model}"
            item = self.quota_hits.setdefault(key, {"quotaId": v["quotaId"], "quotaMetric": v["quotaMetric"],
                                                    "model": v["model"] or model, "limit": v["quotaValue"], "hits": 0})
            item["hits"] += 1
            if v["quotaValue"]:
                item["limit"] = v["quotaValue"]

    def telemetry(self) -> dict:
        return {"requestSummary": T.summarise(list(self.requests.records), QUOTAS.limits),
                "requestsDropped": self.requests.dropped,
                "projectMode": ("per_key" if os.getenv("GG_GEMINI_KEY_PROJECTS", "").strip().casefold() == "per_key"
                                else "mapped" if QUOTAS.project_aware() else "shared"),
                "circuitBreaker": list(self.breaker_log), "quotaLimitsReported": list(self.quota_hits.values()),
                "concurrency": self.semaphore.to_dict()}


def _safe_body(response) -> object:
    """An error response's body for reading quota details (never logged: it can echo request metadata)."""
    try:
        return response.json()
    except Exception:
        try:
            return response.text[:4000]
        except Exception:
            return ""
