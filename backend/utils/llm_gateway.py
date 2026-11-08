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
import time
from collections import Counter
from dataclasses import dataclass, field

import httpx

log = logging.getLogger("llm_gateway")

DEFAULT_BUDGETS = {"detailed": 30, "in_depth": 90}


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
    tokens_in: int = 0
    tokens_out: int = 0
    by_purpose: Counter = field(default_factory=Counter)
    models: Counter = field(default_factory=Counter)

    def to_dict(self) -> dict:
        return {"calls": self.calls, "succeeded": self.succeeded, "failed": self.failed,
                "budgetRefusals": self.budget_refusals, "tokensIn": self.tokens_in, "tokensOut": self.tokens_out,
                "byPurpose": dict(self.by_purpose), "models": dict(self.models)}


class LLMGateway:
    def __init__(self, runtime, report_tier: str, *, budget: int | None = None, concurrency: int | None = None,
                 capture: bool = True) -> None:
        self.runtime = runtime
        self.tier = str(report_tier or "detailed")
        self.budget = budget if budget is not None else _env_int(
            "GG_V2_LLM_CALL_BUDGET_" + self.tier.upper(), DEFAULT_BUDGETS.get(self.tier, 30))
        # In-depth reports run 5 calls at once (Stage G runtime: rate-limit retries were 1-3 per run at 3).
        self.semaphore = asyncio.Semaphore(max(1, concurrency or _env_int(
            "GG_V2_SECTION_CONCURRENCY", 5 if self.tier == "in_depth" else 3)))
        self.capture = capture
        self.stats = GatewayStats()
        self._key_cursor = 0  # rotates keys across calls so one key's rate limit is not hit by a long run
        # model -> {key: time of its last 429}; two keys throttled on one model within a minute means the
        # MODEL is rate-limited (a per-project quota), so the model is cooled instead of being retried first
        # by every following call.
        self._model_429: dict[str, dict[str, float]] = {}
        self.stats_rate_limited_models = 0

    # ── public ────────────────────────────────────────────────────────────────
    async def generate(self, *, purpose: str, prompt: str, system: str = "", section_id: str = "",
                       max_tokens: int = 4096, temperature: float = 0.2, json_mode: bool = False,
                       timeout: float = 90.0, retries: int = 1) -> LLMResult:
        if self.stats.calls >= self.budget:
            self.stats.budget_refusals += 1
            log.warning("V2 LLM budget exhausted (%d calls); %s/%s not run", self.budget, purpose, section_id)
            return LLMResult(ok=False, error="budget_exhausted")
        async with self.semaphore:
            self.stats.calls += 1
            self.stats.by_purpose[purpose] += 1
            started = time.perf_counter()
            result = LLMResult(ok=False, error="no_attempt")
            tried: set[tuple[str, str]] = set()
            for attempt in range(1, retries + 2):
                result = await self._gemini_once(prompt, system, max_tokens, temperature, json_mode, timeout, tried)
                result.attempts = attempt
                if result.ok:
                    break
                log.warning("V2 LLM %s/%s attempt %d failed: %s", purpose, section_id or "-", attempt, result.error)
            if not result.ok and not json_mode:
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
    def _attempt_plan(self, tried: set) -> list[tuple[str, str]]:
        rt = self.runtime
        keys = [k for k in rt.get_gemini_keys() if rt._is_rest_api_key(k)]
        if not keys:
            return []
        ring = rt.round_robin(keys)
        plan = []
        for model in rt.GEMINI_MODELS:
            if rt._gemini_model_cooling(model):
                continue
            healthy = [k for k in rt._gemini_candidate_keys(ring, model, len(ring))
                       if not rt.is_rate_limited(k) and (model, k) not in tried]
            if healthy:
                plan.append((model, healthy[self._key_cursor % len(healthy)]))
        self._key_cursor += 1
        return plan

    async def _gemini_once(self, prompt, system, max_tokens, temperature, json_mode, timeout, tried) -> LLMResult:
        rt = self.runtime
        plan = self._attempt_plan(tried)
        if not plan:
            return LLMResult(ok=False, error="no_gemini_key_or_model_available")
        last_error = "no_response"
        for model, key in plan[:3]:
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
