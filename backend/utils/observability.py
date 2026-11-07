"""Small, privacy-safe in-process request telemetry for Growth Gradual.

The buffer is intentionally bounded and ephemeral. It stores operational metadata
only (counts, durations, stage names and statuses), never prompts, document text,
API keys, bearer tokens or provider URLs.
"""
from __future__ import annotations

from collections import deque
from contextvars import ContextVar
from datetime import datetime, timezone
import re
import threading
import uuid
from typing import Any

_TRACE_ID: ContextVar[str] = ContextVar("gg_trace_id", default="")
_EVENTS: deque[dict[str, Any]] = deque(maxlen=500)
_LOCK = threading.Lock()
_SAFE_KEY = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,39}$")
_SECRET_PARTS = ("prompt", "token", "secret", "password", "credential", "authorization", "api_key", "apikey", "url", "path")
_BLOCKED_EXACT = {"query", "question", "text", "content", "context", "messages", "document"}


def normalize_trace_id(value: str | None = None) -> str:
    candidate = str(value or "").strip()
    if candidate and re.fullmatch(r"[A-Za-z0-9_-]{8,80}", candidate):
        return candidate
    return uuid.uuid4().hex


def set_trace_id(value: str):
    return _TRACE_ID.set(normalize_trace_id(value))


def reset_trace_id(token) -> None:
    _TRACE_ID.reset(token)


def get_trace_id() -> str:
    return _TRACE_ID.get() or ""


def _safe_details(details: dict[str, Any] | None) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for raw_key, value in (details or {}).items():
        key = str(raw_key)
        lower = key.lower()
        if not _SAFE_KEY.fullmatch(key) or lower in _BLOCKED_EXACT or lower.endswith(("_text", "_content", "_prompt")) or any(part in lower for part in _SECRET_PARTS):
            continue
        if value is None or isinstance(value, (bool, int, float)):
            safe[key] = value
        elif isinstance(value, str):
            safe[key] = value[:120]
        elif isinstance(value, (list, tuple)):
            safe[key] = [str(item)[:80] for item in list(value)[:12] if isinstance(item, (str, int, float))]
        elif isinstance(value, dict):
            safe[key] = _safe_details(value)
    return safe


def record_event(
    service: str,
    stage: str,
    status: str,
    *,
    duration_ms: float | int | None = None,
    details: dict[str, Any] | None = None,
    trace_id: str | None = None,
) -> dict[str, Any]:
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "traceId": normalize_trace_id(trace_id or get_trace_id()) if (trace_id or get_trace_id()) else "",
        "service": str(service)[:48],
        "stage": str(stage)[:64],
        "status": str(status)[:24],
        "durationMs": round(float(duration_ms), 2) if duration_ms is not None else None,
        "details": _safe_details(details),
    }
    with _LOCK:
        _EVENTS.append(event)
    return event


def recent_events(limit: int = 100) -> list[dict[str, Any]]:
    count = max(1, min(int(limit), 250))
    with _LOCK:
        items = list(_EVENTS)[-count:]
    return list(reversed(items))


def clear_events() -> None:
    with _LOCK:
        _EVENTS.clear()
