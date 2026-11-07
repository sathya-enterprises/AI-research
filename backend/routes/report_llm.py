"""Provider transport primitives used by the research report pipeline.

This module is the first extraction from the legacy report route. Provider
policy and job orchestration remain in ``report.py`` while HTTP transport is
isolated here so it can be exercised without loading the full route module.
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import MutableSet

import httpx


def coverage_digest(report_text: str, max_chars: int = 7_000, *, parse_sections) -> str:
    """Build compact section coverage for continuation prompts."""
    sections = parse_sections(report_text)
    parts: list[str] = []
    for section in sections:
        body = report_text[section["start"]:section["end"]]
        body_clean = re.sub(r"\s+", " ", body).strip()
        if len(body_clean) > 800:
            body_clean = body_clean[:500] + " … " + body_clean[-280:]
        parts.append(f"## {section['heading']}\n{body_clean}")
    return "\n\n".join(parts)[:max_chars]


def planned_headings(report_plan: dict | None, report_text: str, *, parse_sections) -> list[str]:
    """Preserve report order while selecting planned headings already present."""
    sections = parse_sections(report_text)
    existing = [section["heading"] for section in sections if section.get("level", 0) == 2]
    if not existing:
        existing = [section["heading"] for section in sections if section.get("heading")]
    if not report_plan:
        return existing
    planned = [
        str(section.get("heading") or "").strip()
        for section in (report_plan.get("sections") or [])
        if str(section.get("heading") or "").strip()
    ]
    lower_existing = {heading.lower(): heading for heading in existing}
    output = [lower_existing[heading.lower()] for heading in planned if heading.lower() in lower_existing]
    output.extend(heading for heading in existing if heading not in output)
    return output


def insert_section_addition(report_text: str, heading: str, addition: str, *, parse_sections) -> str:
    """Insert new H3/H4 content inside an existing top-level section."""
    target = next(
        (section for section in parse_sections(report_text)
         if section.get("heading", "").strip().lower() == heading.strip().lower()),
        None,
    )
    addition = (addition or "").strip()
    if not target or not addition:
        return report_text
    addition = re.sub(rf"^##[ \t]+{re.escape(heading)}[ \t]*\n+", "", addition, count=1, flags=re.IGNORECASE)
    end = target["end"]
    prefix = report_text[:end].rstrip()
    suffix = report_text[end:]
    return prefix + "\n\n" + addition + "\n\n" + suffix.lstrip("\n")


def parse_continuation_sections(text: str) -> list[tuple[str, str]]:
    """Unwrap provider JSON and return explicit section-marker blocks."""
    raw = re.sub(r"^```(?:json|markdown)?\s*|\s*```$", "", str(text or "").strip(), flags=re.I)
    if not raw:
        return []
    if raw.startswith("{"):
        try:
            payload = json.loads(raw)
        except Exception:
            payload = None
        if isinstance(payload, dict):
            rows = payload.get("sections")
            if isinstance(rows, list):
                recovered = []
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    heading = str(row.get("heading") or row.get("title") or row.get("section") or "").strip()
                    body = str(row.get("content") or row.get("body") or row.get("markdown") or row.get("text") or "").strip()
                    if heading and body:
                        recovered.append((heading, body))
                if recovered:
                    return recovered
            for key in ("continuation", "content", "markdown", "text", "output", "report"):
                candidate = payload.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    raw = candidate.strip()
                    break
            else:
                return []
    return [
        (match.group(1).strip(), match.group(2).strip())
        for match in re.finditer(
            r"\[\[SECTION:\s*(.+?)\]\]\s*(.*?)\s*\[\[END SECTION\]\]",
            raw, re.IGNORECASE | re.DOTALL,
        )
        if match.group(1).strip() and match.group(2).strip()
    ]


async def groq_report_writer(user_prompt: str, *, runtime) -> str:
    """Call the primary Groq report writer using report-owned key policy."""
    get_groq_keys = runtime.get_groq_keys
    iter_reserved_groq_keys = runtime.iter_reserved_groq_keys
    is_rate_limited = runtime.is_rate_limited
    mark_rate_limited = runtime.mark_rate_limited
    log = runtime.log
    time = runtime.time
    httpx = runtime.httpx
    system_prompt = runtime.SYSTEM_PROMPT
    max_prompt_chars = runtime.GROQ_MAX_PROMPT_CHARS

    keys = get_groq_keys()
    if not keys:
        log.warning("Groq: no keys configured for report generation")
        return ""

    combined = len(system_prompt) + len(user_prompt)
    if combined > max_prompt_chars:
        log.info(
            "Groq: skipping — combined payload %d chars (system %d + user %d) "
            "exceeds %d char budget; going straight to Gemini",
            combined, len(system_prompt), len(user_prompt), max_prompt_chars,
        )
        return ""

    log.info("Groq: calling for report generation with %d key(s)  prompt=%d chars", len(keys), len(user_prompt))
    for key in iter_reserved_groq_keys(keys):
        if is_rate_limited(key):
            log.debug("Groq: skipping rate-limited key ...%s", "[redacted]")
            continue
        try:
            log.debug("Groq: trying key ...%s", "[redacted]")
            t0 = time.perf_counter()
            async with httpx.AsyncClient(timeout=90) as client:
                response = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
                    json={
                        "model": "openai/gpt-oss-120b",
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        "max_tokens": 32768,
                        "temperature": 0.2,
                        "response_format": {"type": "json_object"},
                    },
                )
            if response.status_code == 413:
                log.warning(
                    "Groq 413 on key ...%s despite passing the %d-char pre-check "
                    "— aborting Groq for this request, going straight to Gemini",
                    "[redacted]", max_prompt_chars,
                )
                mark_rate_limited(key, 120_000)
                return ""
            if response.status_code == 429:
                log.warning("Groq 429 on key ...%s", "[redacted]")
                mark_rate_limited(key, 60_000)
                continue
            if response.status_code in (401, 403):
                log.warning("Groq %d on key ...%s", response.status_code, "[redacted]")
                mark_rate_limited(key, 24 * 60 * 60_000)
                continue
            if not response.is_success:
                log.warning("Groq HTTP %d on key ...%s", response.status_code, "[redacted]")
                continue
            content = ((response.json().get("choices") or [{}])[0].get("message") or {}).get("content", "")
            if content and content.strip():
                log.info("Groq: report generated in %.0fms (%d chars)", (time.perf_counter() - t0) * 1000, len(content))
                return content.strip()
            log.warning("Groq: empty response from key ...%s", "[redacted]")
        except Exception as exc:
            log.warning("Groq: call failed on key ...%s: %s", "[redacted]", type(exc).__name__)
    return ""


async def gemini_generate(
    key: str,
    model: str,
    user_prompt: str,
    generation_config: dict,
    depth: str,
    *,
    system_prompt: str,
    streaming: bool,
    request_timeouts: dict,
    stall_timeouts: dict,
    attempt_caps: dict,
    streaming_attempts: MutableSet[tuple[str, str]],
) -> tuple[int, str, str]:
    """Return ``(http_status, text, finish_reason)`` for one Gemini request.

    The API key is sent only in the header, never in the request URL. Streaming
    state is supplied by the caller because the report scheduler uses it when
    deciding whether a hedged request would duplicate active work.
    """
    base = f"https://generativelanguage.googleapis.com/v1beta/models/{model}"
    headers = {"x-goog-api-key": key, "Content-Type": "application/json"}
    body = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": generation_config,
    }
    if not streaming:
        timeout = request_timeouts.get(depth, 35)
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=5, read=timeout, write=10, pool=5)) as client:
            response = await client.post(f"{base}:generateContent", headers=headers, json=body)
        if not response.is_success:
            return response.status_code, "", ""
        payload = response.json()
        candidates = payload.get("candidates") if isinstance(payload, dict) else None
        candidate = candidates[0] if isinstance(candidates, list) and candidates else {}
        candidate = candidate if isinstance(candidate, dict) else {}
        content = candidate.get("content")
        parts = content.get("parts") if isinstance(content, dict) else None
        text = "".join(
            str(part.get("text") or "")
            for part in (parts if isinstance(parts, list) else [])
            if isinstance(part, dict) and part.get("text") and not part.get("thought")
        )
        return response.status_code, text, str(candidate.get("finishReason") or "")

    stall = stall_timeouts.get(depth, 30.0)
    cap = attempt_caps.get(depth, 120.0)
    marker = (key, model)
    parts: list[str] = []
    finish = ""

    async def consume() -> int:
        nonlocal finish
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=5, read=stall, write=10, pool=5)) as client:
            async with client.stream(
                "POST", f"{base}:streamGenerateContent?alt=sse", headers=headers, json=body,
            ) as response:
                if response.status_code != 200:
                    await response.aread()
                    return response.status_code
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if not raw or raw == "[DONE]":
                        continue
                    try:
                        event = json.loads(raw)
                    except ValueError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    if event.get("error"):
                        raise RuntimeError(f"stream error: {str(event['error'])[:160]}")
                    candidate = (event.get("candidates") or [{}])[0]
                    for part in (candidate.get("content") or {}).get("parts") or []:
                        chunk = part.get("text")
                        if chunk and not part.get("thought"):
                            parts.append(chunk)
                    if candidate.get("finishReason"):
                        finish = candidate["finishReason"]
                    streaming_attempts.add(marker)
                return 200

    try:
        status = await asyncio.wait_for(consume(), timeout=cap)
    finally:
        streaming_attempts.discard(marker)
    return status, "".join(parts), finish


async def gemini_one_attempt(
    user_prompt: str,
    report_tier: str,
    key: str,
    model: str,
    *,
    runtime,
) -> tuple[str, str, str]:
    """Run one writer attempt while delegating report policy to its owner."""
    asyncio = runtime.asyncio
    httpx = runtime.httpx
    time = runtime.time
    log = runtime.log
    _extract_requested_depth = runtime._extract_requested_depth
    _GEMINI_MAX_OUTPUT = runtime._GEMINI_MAX_OUTPUT
    _DEPTH_OUTPUT_CAPS = runtime._DEPTH_OUTPUT_CAPS
    _gemini_generate = runtime._gemini_generate
    _recover_report_body = runtime._recover_report_body
    _MIN_VALID_REPORT_BODY_CHARS = runtime._MIN_VALID_REPORT_BODY_CHARS
    mark_rate_limited = runtime.mark_rate_limited
    GEMINI_TIMEOUT_KEY_COOLDOWN_MS = runtime.GEMINI_TIMEOUT_KEY_COOLDOWN_MS
    GEMINI_MODEL_COOLDOWN_MS = runtime.GEMINI_MODEL_COOLDOWN_MS
    GEMINI_KEY_COOLDOWN_MS = runtime.GEMINI_KEY_COOLDOWN_MS

    requested_depth = _extract_requested_depth(user_prompt, report_tier)
    max_out = min(
        _GEMINI_MAX_OUTPUT.get(model, 65_536),
        _DEPTH_OUTPUT_CAPS.get(requested_depth, 65_536),
    )
    generation_config = {"maxOutputTokens": max_out}
    if model in {"gemini-2.5-flash", "gemini-2.5-flash-lite"}:
        generation_config["thinkingConfig"] = {"thinkingBudget": 0}
        generation_config["temperature"] = 0.1
    elif model.startswith("gemini-3"):
        generation_config["thinkingConfig"] = {"thinkingLevel": "minimal"}

    t0 = time.perf_counter()
    try:
        status_code, text, finish_reason = await _gemini_generate(
            key, model, user_prompt, generation_config, requested_depth,
        )
    except asyncio.CancelledError:
        raise
    except (httpx.TimeoutException, asyncio.TimeoutError) as exc:
        log.warning("Gemini attempt timeout model=%s key=...%s after %.0fms: %s",
                    model, "[redacted]", (time.perf_counter() - t0) * 1000, type(exc).__name__)
        mark_rate_limited(f"report:gemini:{key}:{model}", GEMINI_TIMEOUT_KEY_COOLDOWN_MS)
        return "", model, "timeout"
    except Exception as exc:
        exc_text = str(exc)
        if exc_text.startswith("stream error:"):
            if "'code': 503" in exc_text or '"code": 503' in exc_text:
                log.warning("Gemini 503 (mid-stream) model=%s key=...%s after %.0fms",
                            model, "[redacted]", (time.perf_counter() - t0) * 1000)
                mark_rate_limited(f"gemini:model:{model}", GEMINI_MODEL_COOLDOWN_MS)
                return "", model, "503"
            if "'code': 429" in exc_text or '"code": 429' in exc_text:
                log.warning("Gemini 429 (mid-stream) model=%s key=...%s", model, "[redacted]")
                mark_rate_limited(key, GEMINI_KEY_COOLDOWN_MS)
                mark_rate_limited(f"report:gemini:{key}:{model}", GEMINI_KEY_COOLDOWN_MS)
                return "", model, "429"
        log.warning(
            "Gemini attempt exception model=%s key=...%s: %s%s",
            model, "[redacted]", type(exc).__name__,
            f": {exc}" if str(exc) else " (no message)",
        )
        mark_rate_limited(f"report:gemini:{key}:{model}", GEMINI_KEY_COOLDOWN_MS)
        return "", model, "exception"

    elapsed = (time.perf_counter() - t0) * 1000
    if status_code == 503:
        log.warning("Gemini 503 model=%s key=...%s after %.0fms", model, "[redacted]", elapsed)
        mark_rate_limited(f"gemini:model:{model}", GEMINI_MODEL_COOLDOWN_MS)
        return "", model, "503"
    if status_code == 429:
        log.warning("Gemini 429 model=%s key=...%s", model, "[redacted]")
        mark_rate_limited(key, GEMINI_KEY_COOLDOWN_MS)
        mark_rate_limited(f"report:gemini:{key}:{model}", GEMINI_KEY_COOLDOWN_MS)
        return "", model, "429"
    if status_code in (401, 403):
        log.warning("Gemini %d on key=...%s — cooling this key", status_code, "[redacted]")
        mark_rate_limited(key, 24 * 60 * 60_000)
        return "", model, str(status_code)
    if status_code in (400, 404):
        log.warning("Gemini %d — model=%s unavailable/config-invalid", status_code, model)
        return "", model, str(status_code)
    if not 200 <= status_code < 300:
        log.warning("Gemini HTTP %d model=%s key=...%s", status_code, model, "[redacted]")
        return "", model, str(status_code)

    if not text or not text.strip():
        return "", model, "empty"
    if finish_reason == "MAX_TOKENS":
        recovered_body = _recover_report_body(text)
        if len((recovered_body or "").strip()) >= _MIN_VALID_REPORT_BODY_CHARS:
            log.warning(
                "Gemini MAX_TOKENS model=%s; preserving recoverable partial report (%d body chars, %d raw chars)",
                model, len(recovered_body.strip()), len(text),
            )
            return text.strip(), model, "max_tokens_partial"
        log.warning(
            "Gemini MAX_TOKENS model=%s; partial response has no recoverable report body (%d raw chars)",
            model, len(text),
        )
        return "", model, "max_tokens"
    if len(text) > 90_000:
        log.warning("Gemini suspiciously long response model=%s key=...%s (%d chars)", model, "[redacted]", len(text))
        return "", model, "runaway"
    if re.search(r"##\s*2\.\s*methodology\b", text.lower()):
        log.warning("Gemini legacy fixed-structure drift model=%s key=...%s", model, "[redacted]")
        return "", model, "legacy_structure"

    log.info("Gemini: report generated in %.0fms (%d chars) model=%s key=...%s",
             elapsed, len(text), model, "[redacted]")
    return text.strip(), model, "success"


async def run_gemini_wave(
    user_prompt: str,
    depth: str,
    model: str,
    keys: list[str],
    started: float,
    deadline: float,
    launched_out: list[str] | None = None,
    *,
    runtime,
) -> tuple[str, str, dict[str, int], int]:
    """Run one hedged model wave using callbacks owned by the report route."""
    asyncio = runtime.asyncio
    time = runtime.time
    log = runtime.log
    hedge_delay_for = runtime._gemini_hedge_delay
    streaming_attempts = runtime._GEMINI_STREAMING_ATTEMPTS
    hedge_delay = hedge_delay_for(depth)
    queue = list(keys)
    tasks: dict[asyncio.Task, str] = {}
    stats: dict[str, int] = {}
    best_text = ""
    best_model = ""
    stop_launching = False

    def _launch() -> None:
        key = queue.pop(0)
        task = asyncio.create_task(runtime._gemini_one_attempt(user_prompt, depth, key, model))
        tasks[task] = key
        if launched_out is not None:
            launched_out.append(key)

    def _remaining() -> float:
        return max(0.0, deadline - time.perf_counter())

    try:
        if queue:
            _launch()
        if hedge_delay <= 0:
            while queue:
                _launch()
        next_hedge_at = time.perf_counter() + hedge_delay
        pending = set(tasks)
        while pending or (queue and not stop_launching):
            remaining = _remaining()
            if remaining <= 0:
                break
            if not pending:
                _launch()
                pending = {task for task in tasks if not task.done()}
                next_hedge_at = time.perf_counter() + hedge_delay
                continue
            wait_for = remaining
            if queue and not stop_launching and hedge_delay > 0:
                wait_for = max(0.05, min(wait_for, next_hedge_at - time.perf_counter()))
            done, pending = await asyncio.wait(pending, timeout=wait_for, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                try:
                    result = task.result()
                except Exception as exc:
                    stats["task_exception"] = stats.get("task_exception", 0) + 1
                    log.warning("Gemini wave task exception model=%s key=...%s: %s", model, "[redacted]", type(exc).__name__)
                    continue
                text, model_id, status = result
                stats[status] = stats.get(status, 0) + 1
                if text and len(text) > len(best_text):
                    best_text, best_model = text, model_id
                if text and text.strip():
                    return text.strip(), model_id, stats, len(tasks)
                if status in {"503", "400", "404"}:
                    stop_launching = True
                elif queue and not stop_launching:
                    _launch()
                    pending = pending | {t for t in tasks if not t.done() and t not in done}
                    next_hedge_at = time.perf_counter() + hedge_delay
            if (not done and queue and not stop_launching and hedge_delay > 0
                    and time.perf_counter() >= next_hedge_at):
                if any((tasks[task], model) in streaming_attempts for task in tasks if not task.done()):
                    next_hedge_at = time.perf_counter() + hedge_delay
                else:
                    log.info("Gemini wave: no answer after %.0fs on model=%s, adding a hedge request", hedge_delay, model)
                    _launch()
                    pending = {task for task in tasks if not task.done()}
                    next_hedge_at = time.perf_counter() + hedge_delay

        return best_text, best_model, stats, len(tasks)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


async def call_gemini(user_prompt: str, report_tier: str | None = None, *, runtime) -> tuple[str, str]:
    """Select Gemini models/keys for a report and return the first usable response."""
    time = runtime.time
    log = runtime.log
    is_rate_limited = runtime.is_rate_limited
    get_gemini_keys = runtime.get_gemini_keys
    is_rest_api_key = runtime._is_rest_api_key
    extract_requested_depth = runtime._extract_requested_depth
    depth_budgets = runtime._DEPTH_TIME_BUDGETS
    default_budget = runtime.GEMINI_TIME_BUDGET_SECONDS
    wave_size_config = runtime.GEMINI_WAVE_SIZE
    max_attempts_config = runtime.GEMINI_MAX_TOTAL_ATTEMPTS
    round_robin = runtime.round_robin
    models = runtime.GEMINI_MODELS
    model_cooling = runtime._gemini_model_cooling
    candidate_keys = runtime._gemini_candidate_keys
    max_keys_per_model = runtime.GEMINI_MAX_KEYS_PER_MODEL
    mark_rate_limited = runtime.mark_rate_limited

    if is_rate_limited("gemini:report:provider"):
        log.info("Gemini: short report-writer provider cooldown active — trying fallback provider")
        return "", ""
    keys = [key for key in get_gemini_keys() if is_rest_api_key(key)]
    if not keys:
        log.warning("Gemini: no REST-compatible keys configured")
        return "", ""

    depth = extract_requested_depth(user_prompt, report_tier)
    deadline = depth_budgets.get(depth, default_budget)
    wave_size = max(1, min(wave_size_config, len(keys)))
    max_total = min(max_attempts_config, len(keys))
    started = time.perf_counter()
    deadline_at = started + deadline
    log.info(
        "Gemini scheduler: %d REST keys available; using up to %d unique keys across %d models; wave=%d; deadline=%ss; depth=%s",
        len(keys), max_total, len(models), wave_size, deadline, depth,
    )

    remaining = list(round_robin(keys))[:max_total]
    attempts_done = 0
    model_cursor = 0
    model_round = 0
    while remaining and attempts_done < max_total and time.perf_counter() < deadline_at:
        model = models[model_cursor % len(models)]
        model_cursor += 1
        model_round += 1
        if model_cooling(model) and any(not model_cooling(other) for other in models):
            if model_round <= len(models) * 2:
                log.info("Gemini scheduler: skipping model=%s (503 cooldown active)", model)
                continue
        model_keys = candidate_keys(remaining, model, max_keys_per_model)
        if not model_keys:
            if model_round > len(models) and not any(candidate_keys(remaining, other, 1) for other in models):
                break
            continue
        allocated = set(model_keys)
        remaining = [key for key in remaining if key not in allocated]
        wave = model_keys[:wave_size]
        if not wave:
            continue
        log.info("Gemini scheduler: model=%s round=%d keys=%d", model, model_round, len(wave))
        launched_keys: list[str] = []
        text, chosen_model, stats, wave_attempts = await runtime._run_gemini_wave(
            user_prompt, depth, model, wave, started, deadline_at, launched_keys,
        )
        attempts_done += wave_attempts
        unused = [key for key in model_keys if key not in set(launched_keys)]
        if unused:
            remaining = unused + remaining
        if text and text.strip():
            log.info(
                "Gemini scheduler: accepted non-empty response (%d chars) model=%s after %d key attempts stats=%s",
                len(text), chosen_model, attempts_done, stats,
            )
            return text.strip(), chosen_model

    total_elapsed = time.perf_counter() - started
    log.warning(
        "Gemini scheduler: %d attempts exhausted in %.1fs with no usable response — moving to fallback providers",
        attempts_done, total_elapsed,
    )
    mark_rate_limited("gemini:report:provider", 15_000)
    return "", ""


async def groq_report_fallback(user_prompt: str, report_tier: str, *, runtime) -> tuple[str, str]:
    """Try the report-scoped Groq fallback pool with compacted evidence context."""
    get_groq_keys = runtime.get_groq_keys
    _compact_report_user_prompt = runtime._compact_report_user_prompt
    max_prompt_chars = runtime.GROQ_MAX_PROMPT_CHARS
    fallback_system = runtime._REPORT_FALLBACK_SYSTEM
    os = runtime._os
    log = runtime.log
    iter_reserved_groq_keys = runtime.iter_reserved_groq_keys
    is_rate_limited = runtime.is_rate_limited
    mark_rate_limited = runtime.mark_rate_limited
    httpx = runtime.httpx
    max_keys_default = runtime.REPORT_GROQ_FALLBACK_MAX_KEYS

    keys = get_groq_keys()
    if not keys:
        return "", ""
    compact = _compact_report_user_prompt(user_prompt)
    limit = max_prompt_chars - len(fallback_system) - 1_000
    if len(compact) > limit:
        compact = _compact_report_user_prompt(user_prompt, max_chars=max(12_000, limit))
    models = [
        model.strip() for model in re.split(
            r"[,;\n]+", os.environ.get("GROQ_REPORT_MODELS", "openai/gpt-oss-120b,openai/gpt-oss-20b"),
        ) if model.strip()
    ][:3]
    max_keys = min(int(os.environ.get("REPORT_GROQ_FALLBACK_MAX_KEYS", max_keys_default)), len(keys))
    log.info(
        "Report: trying Groq fallback models=%s across up to %d keys (prompt=%d chars, tier=%s)",
        models, max_keys, len(compact), report_tier,
    )

    attempts = 0
    for key in iter_reserved_groq_keys(keys, max_keys=max_keys):
        if attempts >= max_keys:
            break
        if is_rate_limited(f"report:groq:{key}"):
            continue
        attempts += 1
        for model in models:
            if is_rate_limited(f"report:groq:{key}:{model}"):
                continue
            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(connect=5, read=35 if model.endswith("120b") else 25, write=10, pool=5)
                ) as client:
                    response = await client.post(
                        "https://api.groq.com/openai/v1/chat/completions",
                        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
                        json={
                            "model": model,
                            "messages": [
                                {"role": "system", "content": fallback_system},
                                {"role": "user", "content": compact},
                            ],
                            "max_tokens": (65_536 if report_tier in ("detailed", "in_depth") else 12_288),
                            "temperature": 0.2,
                            "response_format": {"type": "json_object"},
                        },
                    )
                if response.status_code in (401, 403):
                    mark_rate_limited(key, 24 * 60 * 60_000)
                    break
                if response.status_code == 429:
                    log.warning("Report: Groq 429 model=%s key=...%s", model, "[redacted]")
                    mark_rate_limited(f"report:groq:{key}:{model}", 60_000)
                    continue
                if response.status_code == 413:
                    log.warning("Report: Groq 413 model=%s — compacting prompt further once", model)
                    tighter = _compact_report_user_prompt(user_prompt, max_chars=16_000)
                    if tighter == compact:
                        continue
                    compact = tighter
                    continue
                if not response.is_success:
                    log.warning("Report: Groq fallback HTTP %d model=%s key=...%s", response.status_code, model, "[redacted]")
                    continue
                try:
                    data = response.json()
                    content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "")
                except Exception as exc:
                    log.warning("Report: Groq fallback parse failed model=%s key=...%s: %s", model, "[redacted]", type(exc).__name__)
                    continue
                if content and content.strip():
                    log.info("Report: Groq fallback succeeded model=%s (%d chars)", model, len(content))
                    return content.strip(), f"groq:{model}:fallback"
            except Exception as exc:
                log.warning("Report: Groq fallback exception model=%s key=...%s: %s", model, "[redacted]", type(exc).__name__)
                mark_rate_limited(f"report:groq:{key}:{model}", 20_000)
                continue
    log.warning("Report: Groq fallback exhausted report-scoped key/model pool after %d key slots", attempts)
    return "", ""


async def openrouter_report_fallback(user_prompt: str, report_tier: str, *, runtime) -> tuple[str, str]:
    """Use the optional OpenRouter provider after primary and Groq writers fail."""
    os = runtime._os
    log = runtime.log
    httpx = runtime.httpx
    _compact_report_user_prompt = runtime._compact_report_user_prompt
    fallback_system = runtime._REPORT_FALLBACK_SYSTEM
    max_tokens = runtime.REPORT_OPENROUTER_MAX_TOKENS

    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        return "", ""
    models = [
        model.strip() for model in re.split(
            r"[,;\n]+", os.environ.get(
                "OPENROUTER_REPORT_MODELS",
                "openai/gpt-oss-120b:free,meta-llama/llama-3.3-70b-instruct:free,mistralai/mistral-7b-instruct:free",
            ),
        ) if model.strip()
    ][:3]
    compact = _compact_report_user_prompt(user_prompt, max_chars=30_000)
    for model in models:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(connect=5, read=30, write=10, pool=5)) as client:
                response = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {key}", "Content-Type": "application/json",
                        "HTTP-Referer": "https://growthgradual.ai", "X-Title": "Growth Gradual Research Intelligence",
                    },
                    json={
                        "model": model,
                        "messages": [{"role": "system", "content": fallback_system}, {"role": "user", "content": compact}],
                        "max_tokens": max_tokens, "temperature": 0.2, "response_format": {"type": "json_object"},
                    },
                )
            if response.status_code in (401, 403):
                return "", ""
            if response.status_code in (404, 429) or not response.is_success:
                continue
            content = ((response.json().get("choices") or [{}])[0].get("message") or {}).get("content", "")
            if content and content.strip():
                return content.strip(), f"openrouter:{model}:fallback"
        except Exception as exc:
            log.warning("Report: OpenRouter fallback failed model=%s: %s", model, type(exc).__name__)
    return "", ""


async def call_report_writer(
    user_prompt: str,
    report_tier: str | None = None,
    report_plan: dict | None = None,
    fallback_sources: list[dict] | None = None,
    *,
    runtime,
) -> tuple[str, str]:
    """Apply writer deadlines/fallback order while keeping policy callbacks in report.py."""
    asyncio = runtime.asyncio
    log = runtime.log
    mark_rate_limited = runtime.mark_rate_limited
    depth = report_tier or "detailed"
    deadline = {"brief": 60, "detailed": 150, "in_depth": 240}.get(depth, 150)
    try:
        raw, model = await asyncio.wait_for(runtime.call_gemini(user_prompt, report_tier), timeout=deadline)
    except asyncio.TimeoutError:
        log.warning("Report: Gemini hard deadline %ss reached — switching provider", deadline)
        mark_rate_limited("gemini:report:provider", 60_000)
        raw, model = "", ""
    if raw:
        parsed_preview = runtime._recover_report_body(raw)
        log.info(
            "%sprovider=%s model=%s raw response chars=%d parsed report chars=%d section count=%d",
            "[IN_DEPTH] " if depth == "in_depth" else "Report: ",
            (model.split(":", 1)[0] if model else "unknown"), model or "unknown",
            len(raw), len(parsed_preview), runtime._report_section_count(parsed_preview),
        )
        return raw, model
    raw, model = await runtime._call_groq_report_fallback(user_prompt, depth)
    if raw:
        return raw, model
    raw, model = await runtime._call_openrouter_report_fallback(user_prompt, depth)
    if raw:
        return raw, model
    log.error("Report: all configured report-writer fallbacks exhausted")
    return runtime._deterministic_report_json(
        user_prompt, depth, report_plan=report_plan, sources=fallback_sources,
    )


async def groq_plain_continuation(prompt: str, report_tier: str, *, runtime) -> tuple[str, str]:
    """Make a bounded Groq fallback call using report-owned key policy."""
    keys = runtime.get_groq_keys()
    if not keys:
        return "", ""

    def excerpt(value: str, limit: int) -> str:
        value = str(value or "")
        if len(value) <= limit:
            return value
        marker = "\n\n[Middle of continuation context omitted to stay within provider limits]\n\n"
        remaining = max(0, limit - len(marker))
        head = int(remaining * 0.30)
        return value[:head] + marker + value[-(remaining - head):]

    compact = excerpt(prompt, 14_000)
    system = (
        "You are a continuation editor for a professional Growth Gradual research report. "
        "Return ONLY new markdown content for the requested existing sections. "
        "Never create an H2/top-level section. Never repeat existing content. "
        "Use only evidence supplied in the prompt. Do not invent numbers, dates, rankings, "
        "company facts or citations. Add useful H3/H4 analysis, comparisons, mechanisms, "
        "historical context and implications when supported. Continue only where distinct evidence supports more analytical depth. "
    )
    attempted = 0
    for key in runtime.iter_reserved_groq_keys(keys, max_keys=4):
        if runtime.is_rate_limited(f"report:groq:{key}"):
            continue
        attempted += 1
        token_budget = 6_000 if report_tier == "in_depth" else (4_500 if report_tier == "detailed" else 2_500)
        variants = [(compact, token_budget), (excerpt(prompt, 7_500), min(3_500, token_budget))]
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(connect=5, read=30, write=10, pool=5)) as client:
                response = None
                for variant_no, (user_content, max_tokens) in enumerate(variants):
                    response = await client.post(
                        "https://api.groq.com/openai/v1/chat/completions",
                        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
                        json={
                            "model": "openai/gpt-oss-120b",
                            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user_content}],
                            "max_tokens": max_tokens,
                            "temperature": 0.2,
                        },
                    )
                    if response.status_code == 413 and variant_no == 0:
                        runtime.log.warning("Report: Groq continuation HTTP 413; retrying with compact prompt/token budget")
                        continue
                    break
            if response is None:
                continue
            if response.status_code in (401, 403):
                runtime.mark_rate_limited(f"report:groq:{key}", 24 * 60 * 60_000)
                continue
            if response.status_code == 429:
                runtime.mark_rate_limited(f"report:groq:{key}", 60_000)
                continue
            if response.status_code == 413:
                runtime.log.warning("Report: Groq continuation still returned HTTP 413 after compact retry")
                return "", ""
            if not response.is_success:
                runtime.log.warning("Report: Groq continuation HTTP %d key=...%s", response.status_code, "[redacted]")
                continue
            content = ((response.json().get("choices") or [{}])[0].get("message") or {}).get("content", "")
            if content and content.strip():
                return content.strip(), "groq:gpt-oss-120b:continuation"
        except Exception as exc:
            runtime.log.warning("Report: Groq continuation failed on key ...%s: %s", "[redacted]", type(exc).__name__)
    runtime.log.warning("Report: Groq continuation produced no usable content after %d attempted keys", attempted)
    return "", ""


async def gemini_plain_continuation(prompt: str, report_tier: str, *, runtime) -> tuple[str, str]:
    """Call Gemini for section continuations, then use the Groq fallback."""
    keys = [key for key in runtime.get_gemini_keys() if runtime._is_rest_api_key(key)]
    if not keys:
        return await runtime._call_groq_plain_continuation(prompt, report_tier)

    ring = runtime.round_robin(keys)
    max_out = 2_500 if report_tier == "brief" else (4_500 if report_tier == "detailed" else 6_000)
    system_text = (
        "You are a continuation editor for a professional Growth Gradual research report. "
        "Return ONLY new markdown content for the requested existing sections. "
        "Never create an H2/top-level section. Never repeat the executive summary, conclusion, "
        "methodology or source list. Use only evidence supplied in the prompt. "
        "Do not invent numbers, dates, rankings, company facts or citations. "
        "Write analytical prose, H3/H4 subsections, comparisons, mechanisms, historical context, "
        "counterpoints, assumptions, sensitivities and implications when supported."
    )
    attempts: list[tuple[str, str]] = []
    for model in runtime.GEMINI_MODELS:
        if runtime._gemini_model_cooling(model):
            continue
        healthy = [key for key in runtime._gemini_candidate_keys(ring, model, len(ring)) if not runtime.is_rate_limited(key)]
        if healthy:
            attempts.append((model, healthy[len(attempts) % len(healthy)]))
        if len(attempts) >= 3:
            break
    for model, key in attempts:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(connect=5, read=35, write=8, pool=5)) as client:
                response = await client.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                    headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                    json={
                        "system_instruction": {"parts": [{"text": system_text}]},
                        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                        "generationConfig": runtime._gemini_gen_config(
                            model, {"maxOutputTokens": max_out, "temperature": 0.2},
                        ),
                    },
                )
            if response.status_code in (401, 403):
                runtime.mark_rate_limited(key, 24 * 60 * 60_000)
                continue
            if response.status_code == 404:
                runtime.mark_rate_limited(f"report:gemini:{key}:{model}", 24 * 60 * 60_000)
                continue
            if response.status_code == 429:
                runtime.mark_rate_limited(key, runtime.GEMINI_KEY_COOLDOWN_MS)
                runtime.mark_rate_limited(f"report:gemini:{key}:{model}", runtime.GEMINI_KEY_COOLDOWN_MS)
                continue
            if response.status_code == 503:
                runtime.mark_rate_limited(f"gemini:model:{model}", runtime.GEMINI_MODEL_COOLDOWN_MS)
                continue
            if not response.is_success:
                runtime.log.warning("Report: plain continuation HTTP %d model=%s", response.status_code, model)
                continue
            parts = []
            for candidate in response.json().get("candidates") or []:
                for part in ((candidate.get("content") or {}).get("parts") or []):
                    if isinstance(part, dict) and part.get("text") and not part.get("thought"):
                        parts.append(str(part["text"]))
            content = "\n".join(parts).strip()
            if content:
                return content, model
        except (httpx.TimeoutException, asyncio.TimeoutError):
            runtime.mark_rate_limited(f"report:gemini:{key}:{model}", runtime.GEMINI_TIMEOUT_KEY_COOLDOWN_MS)
            runtime.log.warning("Report: plain continuation timeout model=%s key=...%s", model, "[redacted]")
        except Exception as exc:
            runtime.mark_rate_limited(f"report:gemini:{key}:{model}", runtime.GEMINI_KEY_COOLDOWN_MS)
            runtime.log.warning("Report: plain continuation model=%s key=...%s failed: %s", model, "[redacted]", type(exc).__name__)
    return await runtime._call_groq_plain_continuation(prompt, report_tier)
