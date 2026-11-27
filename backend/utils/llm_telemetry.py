"""Per-request LLM telemetry: one record per HTTP attempt, and totals per project, model and provider.

A record holds operational metadata only - ids, purpose, provider, model, the project ALIAS (gemini_project_03,
never a key), timing, token counts, HTTP status, error category, retry number and outcome. No prompt, response,
key, header or URL is ever stored.
"""
from __future__ import annotations

import threading
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass

MAX_RECORDS = 4000

# error categories
OK = "ok"
RATE_LIMIT_MINUTE = "rate_limit_minute"
RATE_LIMIT_DAY = "rate_limit_day"
CAPACITY = "capacity_503"
TIMEOUT = "timeout"
NETWORK = "network"
AUTH = "auth"
REJECTED = "rejected_400"
NOT_FOUND = "not_found"
EMPTY = "empty_response"
OTHER = "other"


def category(status: int | None, *, daily: bool = False, timeout: bool = False, network: bool = False,
             empty: bool = False) -> str:
    if timeout:
        return TIMEOUT
    if network:
        return NETWORK
    if status == 429:
        return RATE_LIMIT_DAY if daily else RATE_LIMIT_MINUTE
    if status == 503:
        return CAPACITY
    if status in (401, 403):
        return AUTH
    if status == 400:
        return REJECTED
    if status == 404:
        return NOT_FOUND
    if status and 200 <= status < 300:
        return EMPTY if empty else OK
    return OTHER


@dataclass
class RequestRecord:
    request_id: str
    call_id: str            # the logical call (one generate()) this attempt belongs to
    report_id: str
    purpose: str
    section_id: str
    provider: str           # gemini | groq
    model: str
    project: str            # safe alias
    started_at: float       # unix seconds
    tokens_est: int
    tokens_in: int
    tokens_out: int
    latency_ms: int
    status: int             # HTTP status, 0 when no response
    error_category: str
    attempt: int            # 1 = first attempt of the logical call
    fallback: bool
    outcome: str            # success | failed

    def to_dict(self) -> dict:
        return asdict(self)


def new_id() -> str:
    return uuid.uuid4().hex[:12]


class RequestLog:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.records: list[RequestRecord] = []
        self.dropped = 0

    def add(self, record: RequestRecord) -> None:
        with self._lock:
            if len(self.records) >= MAX_RECORDS:
                self.dropped += 1
                return
            self.records.append(record)


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return round(float(ordered[index]), 1)


def _peak_per_minute(records: list[RequestRecord], value=lambda r: 1) -> float:
    """Largest total of ``value`` over any 60-second window that starts at a request."""
    times = sorted((r.started_at, value(r)) for r in records)
    best, j, total = 0.0, 0, 0.0
    for i, (t, v) in enumerate(times):
        total += v
        while times[j][0] < t - 60:
            total -= times[j][1]
            j += 1
        best = max(best, total)
    return best


def summarise(records: list[RequestRecord], limits_for=None) -> dict:
    """Totals for a run. ``limits_for(project, model)`` returns configured rpm/tpm limits for utilisation."""
    if not records:
        return {"requests": 0}
    by_project: dict[str, list[RequestRecord]] = defaultdict(list)
    by_model: dict[str, list[RequestRecord]] = defaultdict(list)
    for r in records:
        by_project[r.project].append(r)
        by_model[r.model].append(r)

    def block(rows: list[RequestRecord]) -> dict:
        lat = [r.latency_ms / 1000 for r in rows if r.status]
        cats = Counter(r.error_category for r in rows)
        return {"requests": len(rows), "ok": cats.get(OK, 0),
                "http429": cats.get(RATE_LIMIT_MINUTE, 0) + cats.get(RATE_LIMIT_DAY, 0), "http503": cats.get(CAPACITY, 0),
                "tokensIn": sum(r.tokens_in for r in rows), "tokensOut": sum(r.tokens_out for r in rows),
                "p50s": _pct(lat, 0.5), "p95s": _pct(lat, 0.95),
                "peakRequestsPerMinute": _peak_per_minute(rows),
                "peakTokensPerMinute": _peak_per_minute(rows, lambda r: r.tokens_in or r.tokens_est)}

    projects = {}
    for project, rows in sorted(by_project.items()):
        item = block(rows)
        if limits_for is not None and project not in {"groq", ""}:
            utilisation = {}
            for model in {r.model for r in rows}:
                lim = limits_for(project, model) or {}
                model_rows = [r for r in rows if r.model == model]
                if lim.get("rpm"):
                    utilisation[f"{model}:rpm"] = round(_peak_per_minute(model_rows) / float(lim["rpm"]), 2)
                if lim.get("tpm"):
                    utilisation[f"{model}:tpm"] = round(
                        _peak_per_minute(model_rows, lambda r: r.tokens_in or r.tokens_est) / float(lim["tpm"]), 2)
            if utilisation:
                item["peakUtilisation"] = utilisation
        projects[project] = item
    calls = {r.call_id for r in records}
    retried = {r.call_id for r in records if r.attempt > 1}
    latencies = [r.latency_ms / 1000 for r in records if r.status]
    cats = Counter(r.error_category for r in records)
    gemini = [r for r in records if r.provider == "gemini"]
    fallback_calls = {r.call_id for r in records if r.fallback and r.outcome == "success"}
    return {
        "requests": len(records), "logicalCalls": len(calls),
        "successRate": round(cats.get(OK, 0) / len(records), 3),
        "geminiSuccessRate": round(sum(1 for r in gemini if r.error_category == OK) / len(gemini), 3) if gemini else None,
        "retryRate": round(len(retried) / len(calls), 3) if calls else 0.0,
        "http429": cats.get(RATE_LIMIT_MINUTE, 0) + cats.get(RATE_LIMIT_DAY, 0), "http429Daily": cats.get(RATE_LIMIT_DAY, 0),
        "http503": cats.get(CAPACITY, 0), "errorCategories": dict(cats),
        "latencySeconds": {"p50": _pct(latencies, 0.5), "p95": _pct(latencies, 0.95), "p99": _pct(latencies, 0.99),
                           "mean": round(sum(latencies) / len(latencies), 1) if latencies else 0.0},
        "groqFallbackShare": round(len(fallback_calls) / len(calls), 3) if calls else 0.0,
        "projectsUsed": len([p for p in by_project if p.startswith("gemini_")]),
        "byProject": projects,
        "byModel": {m: block(rows) for m, rows in sorted(by_model.items())},
        "byProvider": dict(Counter(r.provider for r in records)),
    }


def now() -> float:
    return time.time()
