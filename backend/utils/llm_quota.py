"""Project-aware Gemini quota management (requests/minute, tokens/minute, requests/day per project and model).

Gemini limits are counted per Google Cloud PROJECT and model, not per API key: several keys of one project share
one allowance, so rotating them adds no capacity. This module

* maps every key to its project (GG_GEMINI_KEY_PROJECTS); keys without a mapping share ONE pool ("shared"), the
  safe assumption - different keys are never assumed to be different quota pools;
* tracks, per (project, model), the requests and tokens sent in the last 60 s and the requests sent today;
* enforces limits only where they are configured (GG_GEMINI_QUOTAS) - nothing is hard-coded, free-tier and paid
  projects can each have their own numbers;
* cools a (project, model) pool when Google answers 429 for it, for the time Google asks (Retry-After header or
  the RetryInfo delay in the error body) or until the next day for a daily quota, with jitter so waiting calls do
  not all retry in the same second;
* tells the scheduler how long until a pool can take a request of a given size.

Configuration (environment, JSON where noted):

GG_GEMINI_KEY_PROJECTS   project of each key, in the order of the GEMINI_API_KEY pool, comma-separated
                         ("proj-a,proj-a,proj-b,..."), or JSON {"<last 6 characters of the key>": "<project>"},
                         or ``per_key``: every key is its own project, named gemini_project_01, _02, ... in pool
                         order (use only when each key was created in a different Google Cloud project).
                         Keys not listed belong to the "shared" pool.
GG_GEMINI_QUOTAS         JSON limits: {"default": {"rpm": 10, "tpm": 250000, "rpd": 250},
                                       "proj-b": {"*": {"rpm": 1000, "tpm": 4000000},
                                                  "gemini-3-flash-preview": {"rpd": 10000}}}
                         Missing values are unlimited; a project's "*" applies to all its models.
GG_GEMINI_DAILY_RESET_HOURS  hours a pool stays cooled after a per-day quota 429 when Google gives no delay (6).

Only key suffixes ever appear in logs or telemetry, never a key.
"""
from __future__ import annotations

import json
import logging
import os
import random
import re
import threading
import time
from collections import deque

log = logging.getLogger("llm_quota")

SHARED_PROJECT = "shared"
_WINDOW = 60.0
_DAY = 86_400.0


def key_label(key: str) -> str:
    """A key as it may appear in logs: its last 4 characters only."""
    return f"…{str(key or '')[-4:]}" if key else "-"


def estimate_tokens(*texts: str) -> int:
    """Input tokens of a request, estimated before it is sent (about 4 characters per token, rounded up)."""
    return max(1, sum(len(str(t or "")) for t in texts) // 4 + 1)


def _parse_json_env(name: str):
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        log.warning("%s is not valid JSON; ignored", name)
        return None


def parse_retry_delay(headers=None, body: object = None) -> float | None:
    """Seconds Google asks the client to wait: the Retry-After header, else the RetryInfo delay in the body."""
    try:
        value = (headers or {}).get("retry-after") or (headers or {}).get("Retry-After")
    except Exception:
        value = None
    if value:
        try:
            return max(0.0, float(str(value).strip()))
        except ValueError:
            pass
    text = body if isinstance(body, str) else json.dumps(body or {})
    match = re.search(r'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"', text or "")
    return float(match.group(1)) if match else None


def quota_violations(body: object = None) -> list[dict]:
    """Google's QuotaFailure details from a 429 body: which quota was hit and its limit (quotaId, quotaMetric,
    quotaValue, model). These are Google's own numbers for the project, safe to log (no credentials)."""
    if not isinstance(body, dict):
        return []
    out = []
    for detail in (body.get("error") or {}).get("details") or []:
        if not isinstance(detail, dict) or "QuotaFailure" not in str(detail.get("@type") or ""):
            continue
        for v in detail.get("violations") or []:
            if isinstance(v, dict):
                dims = v.get("quotaDimensions") or {}
                out.append({"quotaId": str(v.get("quotaId") or "")[:120], "quotaMetric": str(v.get("quotaMetric") or "")[:160],
                            "quotaValue": str(v.get("quotaValue") or "")[:20],
                            "model": str(dims.get("model") or "")[:60] if isinstance(dims, dict) else ""})
    return out


def is_daily_quota(body: object = None) -> bool:
    """A 429 for a per-day quota (it does not clear in a minute)."""
    text = (body if isinstance(body, str) else json.dumps(body or {})).casefold()
    return bool(re.search(r"per ?day|perday|requestsperday|daily", text))


class QuotaManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests: dict[tuple, deque] = {}     # (project, model) -> deque[(time, tokens)]
        self._day: dict[tuple, deque] = {}          # (project, model) -> deque[time]
        self._cooled_until: dict[tuple, float] = {}
        self._project_cache: tuple[str, str, dict] | None = None
        self._limits_cache: tuple[str, object] | None = None
        self.stats = {"cooled": 0, "dailyExhausted": 0, "waitsSuggested": 0}
        self._inflight: dict[str, int] = {}
        self._outcomes: dict[str, deque] = {}

    # ── configuration ─────────────────────────────────────────────────────────
    def project_aware(self) -> bool:
        """True when keys are mapped to projects (a 429 then cools only its own project and model)."""
        return bool(os.getenv("GG_GEMINI_KEY_PROJECTS", "").strip())

    def alias_of(self, key: str, pool: list[str] | None = None) -> str:
        """A safe name for a key in logs and telemetry: its project, or its position in the pool when every key
        is in the shared pool (gemini_key_03). Never contains any part of the key."""
        project = self.project_of(key, pool)
        if project != SHARED_PROJECT:
            return project
        pool = pool if pool is not None else _gemini_pool()
        index = next((i for i, k in enumerate(pool) if k == key), -1)
        return f"gemini_key_{index + 1:02d}" if index >= 0 else "gemini_key_unlisted"

    def project_of(self, key: str, pool: list[str] | None = None) -> str:
        raw = os.getenv("GG_GEMINI_KEY_PROJECTS", "").strip()
        if not raw or not key:
            return SHARED_PROJECT
        pool = pool if pool is not None else _gemini_pool()
        if raw.casefold() == "per_key":
            index = next((i for i, k in enumerate(pool) if k == key), -1)
            return f"gemini_project_{index + 1:02d}" if index >= 0 else SHARED_PROJECT
        signature = (raw, "|".join(k[-6:] for k in pool))
        if self._project_cache is None or self._project_cache[:2] != signature:
            mapping: dict[str, str] = {}
            parsed = _parse_json_env("GG_GEMINI_KEY_PROJECTS") if raw.startswith("{") else None
            if isinstance(parsed, dict):
                # {"<key suffix>": "<project>"}: a key belongs to the project of the suffix it ends with
                mapping = {k[-6:]: str(v) for k in pool for suffix, v in parsed.items()
                           if str(suffix) and k.endswith(str(suffix))}
                self._suffixes = {str(sfx): str(v) for sfx, v in parsed.items() if str(sfx)}
            else:
                names = [p.strip() for p in raw.split(",")]
                mapping = {k[-6:]: names[i] for i, k in enumerate(pool) if i < len(names) and names[i]}
                self._suffixes = {}
            self._project_cache = (signature[0], signature[1], mapping)
        found = self._project_cache[2].get(key[-6:])
        if found is None:
            found = next((v for sfx, v in getattr(self, "_suffixes", {}).items() if key.endswith(sfx)), SHARED_PROJECT)
        return found

    def limits(self, project: str, model: str) -> dict:
        raw = os.getenv("GG_GEMINI_QUOTAS", "")
        if self._limits_cache is None or self._limits_cache[0] != raw:
            self._limits_cache = (raw, _parse_json_env("GG_GEMINI_QUOTAS") or {})
        cfg = self._limits_cache[1] if isinstance(self._limits_cache[1], dict) else {}
        out: dict = {}
        for layer in (cfg.get("default"), (cfg.get(project) or {}).get("*") if isinstance(cfg.get(project), dict) else None,
                      (cfg.get(project) or {}).get(model) if isinstance(cfg.get(project), dict) else None):
            if isinstance(layer, dict):
                out.update({k: v for k, v in layer.items() if k in {"rpm", "tpm", "rpd"} and v})
        return out

    # ── state ─────────────────────────────────────────────────────────────────
    def _prune(self, pool: tuple, now: float) -> None:
        window = self._requests.setdefault(pool, deque())
        while window and now - window[0][0] >= _WINDOW:
            window.popleft()
        day = self._day.setdefault(pool, deque())
        while day and now - day[0] >= _DAY:
            day.popleft()

    def wait_seconds(self, project: str, model: str, tokens: int, now: float | None = None) -> float:
        """0 when the pool can take a request of ``tokens`` now; otherwise how long until it can."""
        now = time.time() if now is None else now
        pool = (project, model)
        with self._lock:
            self._prune(pool, now)
            waits = [max(0.0, self._cooled_until.get(pool, 0.0) - now)]
            lim = self.limits(project, model)
            window = self._requests[pool]
            if lim.get("rpm") and len(window) >= int(lim["rpm"]):
                waits.append(_WINDOW - (now - window[0][0]))
            if lim.get("tpm"):
                used = sum(t for _ts, t in window)
                if used + tokens > int(lim["tpm"]) and window:
                    # the oldest entries leave the window first: wait until enough tokens have aged out
                    excess, freed = used + tokens - int(lim["tpm"]), 0
                    for ts, t in window:
                        freed += t
                        if freed >= excess:
                            waits.append(_WINDOW - (now - ts))
                            break
            if lim.get("rpd") and len(self._day[pool]) >= int(lim["rpd"]):
                waits.append(_DAY - (now - self._day[pool][0]))
            return max(waits)

    def record(self, project: str, model: str, tokens: int, now: float | None = None) -> None:
        """A request was sent (counted on sending: a rejected request still used the minute's allowance)."""
        now = time.time() if now is None else now
        pool = (project, model)
        with self._lock:
            self._prune(pool, now)
            self._requests[pool].append((now, max(0, int(tokens))))
            self._day[pool].append(now)

    def cool(self, project: str, model: str, seconds: float, reason: str = "") -> float:
        """Cool a (project, model) pool; jitter (up to +25%) spreads the retries of everyone waiting on it."""
        seconds = max(1.0, float(seconds)) * (1.0 + random.random() * 0.25)
        with self._lock:
            pool = (project, model)
            self._cooled_until[pool] = max(self._cooled_until.get(pool, 0.0), time.time() + seconds)
            self.stats["cooled"] += 1
        log.info("Quota: %s / %s cooled for %.0fs (%s)", project, model, seconds, reason or "rate limited")
        return seconds

    def on_429(self, project: str, model: str, headers=None, body: object = None) -> float:
        delay = parse_retry_delay(headers, body)
        if is_daily_quota(body):
            self.stats["dailyExhausted"] += 1
            hours = float(os.getenv("GG_GEMINI_DAILY_RESET_HOURS", "6") or 6)
            return self.cool(project, model, max(delay or 0.0, hours * 3600), "daily quota exhausted")
        return self.cool(project, model, delay if delay else 60.0, "per-minute quota")

    # ── health and load (request distribution) ────────────────────────────────
    def begin(self, project: str) -> None:
        with self._lock:
            self._inflight[project] = self._inflight.get(project, 0) + 1

    def end(self, project: str) -> None:
        with self._lock:
            self._inflight[project] = max(0, self._inflight.get(project, 0) - 1)

    def outcome(self, project: str, model: str, ok: bool) -> None:
        """Result of one request; the last 20 per project feed its health."""
        with self._lock:
            recent = self._outcomes.setdefault(project, deque(maxlen=20))
            recent.append(bool(ok))

    def load(self, project: str, model: str, now: float | None = None) -> float:
        """How busy and how unhealthy a project is for this model (lower is better). In-flight requests weigh
        most, then requests in the last minute; recent failures add up to 5. A project with no history is
        neutral (0 failures counted), not assumed healthy beyond that."""
        now = time.time() if now is None else now
        with self._lock:
            self._prune((project, model), now)
            inflight = self._inflight.get(project, 0)
            minute = len(self._requests.get((project, model), ()))
            recent = self._outcomes.get(project, ())
            failure_rate = (sum(1 for ok in recent if not ok) / len(recent)) if recent else 0.0
        return inflight * 2.0 + minute + failure_rate * 5.0

    def snapshot(self) -> dict:
        now = time.time()
        with self._lock:
            return {f"{p}/{m}": {"lastMinuteRequests": len([1 for ts, _t in self._requests.get((p, m), ()) if now - ts < _WINDOW]),
                                 "lastMinuteTokens": sum(t for ts, t in self._requests.get((p, m), ()) if now - ts < _WINDOW),
                                 "today": len(self._day.get((p, m), ())),
                                 "cooledFor": max(0, round(self._cooled_until.get((p, m), 0.0) - now))}
                    for (p, m) in set(self._requests) | set(self._cooled_until)}


def _gemini_pool() -> list[str]:
    try:
        from utils.keys import get_gemini_keys
        return list(get_gemini_keys())
    except Exception:
        return []


QUOTAS = QuotaManager()
