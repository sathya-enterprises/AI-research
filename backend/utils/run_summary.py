"""One compact, log-safe summary of a report run (Phase 8 observability).

Built from what the run already records (the gateway's telemetry, the section / chart / claim logs and the
length verdict) plus stage markers, so an operator can see in one place where the time went, which models
answered, how often Gemini refused (429 / 503), what fell back, and what the report contains. It never holds
prompts, API keys or key suffixes.
"""
from __future__ import annotations

import time


class StageClock:
    """Seconds between named markers: mark("outline") after the outline is built, and so on."""

    def __init__(self) -> None:
        self.started = time.perf_counter()
        self._last = self.started
        self.stages: dict[str, float] = {}

    def mark(self, name: str) -> None:
        now = time.perf_counter()
        self.stages[name] = round(self.stages.get(name, 0.0) + (now - self._last), 1)
        self._last = now

    def to_dict(self) -> dict:
        return dict(self.stages)


def build_run_summary(meta: dict, *, tier: str, stages: dict | None = None) -> dict:
    llm = meta.get("llm") or {}
    sections = meta.get("sections") or []
    claims: dict[str, int] = {}
    corrected = removed = 0
    for entry in meta.get("claimCheck") or []:
        if not isinstance(entry, dict):
            continue
        for status, n in (entry.get("byStatus") or {}).items():
            claims[status] = claims.get(status, 0) + int(n or 0)
        corrected += len(entry.get("corrected") or [])
        removed += len(entry.get("removed") or [])
    charts = meta.get("charts") or {}
    citations = meta.get("citations") or {}
    length = meta.get("length") or {}
    http = llm.get("httpStatus") or llm.get("http_status") or {}
    return {
        "tier": tier,
        "elapsedSeconds": meta.get("elapsedSeconds"),
        "stageSeconds": stages or {},
        "llm": {
            "calls": llm.get("calls"), "succeeded": llm.get("succeeded"), "failed": llm.get("failed"),
            "tokensIn": llm.get("tokensIn"), "tokensOut": llm.get("tokensOut"),
            "models": llm.get("models") or {}, "callsByPurpose": llm.get("byPurpose") or {},
            "http429": int(http.get("429", 0) or 0), "http503": int(http.get("503", 0) or 0),
            "networkRetries": llm.get("networkRetries"), "quotaWaits": llm.get("quotaWaits"),
            "quotaWaitSeconds": llm.get("quotaWaitSeconds"),
            "fallbacks": llm.get("fallbacks"), "groqBeforeWait": llm.get("groqBeforeWait"),
            "shortFallbacksRetried": llm.get("shortFallbacksRetried"),
            "fallbacksWithoutCitations": llm.get("fallbacksWithoutCitations"),
            "projectMode": llm.get("projectMode"),
            "requests": _requests_brief(llm.get("requestSummary") or {}),
            "circuitBreakerTrips": len(llm.get("circuitBreaker") or []),
            "quotaLimitsReported": llm.get("quotaLimitsReported") or [],
            "concurrency": llm.get("concurrency") or {},
        },
        "sources": {"cited": citations.get("sourcesCited"), "passagesCited": citations.get("passagesCited")},
        "claims": {"byStatus": claims, "corrected": corrected, "removed": removed},
        "charts": {"candidates": charts.get("candidates"), "planned": charts.get("planned"), "used": charts.get("used")},
        "sections": {"written": sum(1 for s in sections if s.get("ok")), "planned": len(sections),
                     "short": length.get("shortSections") or [], "failed": length.get("failedSections") or []},
        "words": meta.get("words"), "estimatedPages": meta.get("estimatedPages"),
        "lengthStatus": length.get("status"),
    }


def _requests_brief(req: dict) -> dict:
    """Per-request totals for the summary: success/retry rates, latency percentiles and per-project counts."""
    if not req.get("requests"):
        return {}
    return {k: req.get(k) for k in ("requests", "logicalCalls", "successRate", "geminiSuccessRate", "retryRate",
                                    "http429", "http429Daily", "http503", "latencySeconds", "groqFallbackShare",
                                    "projectsUsed", "byProvider")} | {
        "byProject": {p: {k: v for k, v in item.items() if k in {"requests", "ok", "http429", "http503", "p50s", "p95s",
                                                                  "peakRequestsPerMinute", "peakUtilisation"}}
                      for p, item in (req.get("byProject") or {}).items()}}


def summary_line(summary: dict) -> str:
    llm = summary.get("llm") or {}
    stages = ", ".join(f"{k} {v:.0f}s" for k, v in (summary.get("stageSeconds") or {}).items())
    claims = summary.get("claims") or {}
    return (f"run summary: {summary.get('tier')} {summary.get('elapsedSeconds')}s [{stages}] | "
            f"calls {llm.get('calls')} ({llm.get('failed')} failed) tokens {llm.get('tokensIn')}/{llm.get('tokensOut')} "
            f"429x{llm.get('http429')} 503x{llm.get('http503')} fallbacks {llm.get('fallbacks')} "
            f"projects {(llm.get('requests') or {}).get('projectsUsed')} ({llm.get('projectMode')}) "
            f"p50/p95 {((llm.get('requests') or {}).get('latencySeconds') or {}).get('p50')}/"
            f"{((llm.get('requests') or {}).get('latencySeconds') or {}).get('p95')}s "
            f"breaker {llm.get('circuitBreakerTrips')} concurrency {(llm.get('concurrency') or {}).get('lowest')}-"
            f"{(llm.get('concurrency') or {}).get('final')} | "
            f"claims {claims.get('byStatus')} corrected {claims.get('corrected')} removed {claims.get('removed')} | "
            f"charts {(summary.get('charts') or {}).get('used')} | words {summary.get('words')} "
            f"pages {summary.get('estimatedPages')} ({summary.get('lengthStatus')})")
