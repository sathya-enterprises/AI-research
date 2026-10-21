"""Short-lived per-session research context shared by chat and report routes.

The browser normally forwards the source manifest from the preceding chat turn,
but the backend keeps a small in-process copy as a defense-in-depth fallback.
This prevents a follow-up such as 'give as a report' from silently starting a
second discovery search when the frontend has lost the source metadata.
"""
from __future__ import annotations

import time
from collections import OrderedDict
from threading import RLock
from typing import Any

_MAX_SESSIONS = 128
_TTL_SECONDS = 3 * 60 * 60
_LOCK = RLock()
_CACHE: "OrderedDict[str, tuple[float, dict[str, Any]]]" = OrderedDict()


def save_research_context(session_id: str, *, topic: str = "", sources: list[dict] | None = None,
                          context: str = "", query: str = "") -> None:
    sid = (session_id or "").strip()
    if not sid:
        return
    payload = {
        "topic": str(topic or ""),
        "sources": [dict(s) for s in (sources or []) if isinstance(s, dict)],
        "context": str(context or "")[:24000],
        "query": str(query or ""),
    }
    now = time.time()
    with _LOCK:
        _CACHE.pop(sid, None)
        _CACHE[sid] = (now, payload)
        while len(_CACHE) > _MAX_SESSIONS:
            _CACHE.popitem(last=False)


def load_research_context(session_id: str) -> dict[str, Any] | None:
    sid = (session_id or "").strip()
    if not sid:
        return None
    now = time.time()
    with _LOCK:
        item = _CACHE.get(sid)
        if not item:
            return None
        created, payload = item
        if now - created > _TTL_SECONDS:
            _CACHE.pop(sid, None)
            return None
        _CACHE.move_to_end(sid)
        return {
            "topic": payload.get("topic", ""),
            "sources": [dict(s) for s in payload.get("sources", []) if isinstance(s, dict)],
            "context": payload.get("context", ""),
            "query": payload.get("query", ""),
        }


def clear_research_context(session_id: str) -> None:
    with _LOCK:
        _CACHE.pop((session_id or "").strip(), None)
