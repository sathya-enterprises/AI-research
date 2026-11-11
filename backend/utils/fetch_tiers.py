"""Tiered page fetching: which failed pages to retry, with what budget, and how many at once per host.

Tier 1 is the normal fetch (20 s). Tier 2 retries timeouts / network errors: official pages and data files
first (any topic, see utils.primary_sources), with a longer budget for them because government servers and
large PDFs are slow rather than absent. Tier 3 is the browser render for blocked / script-built pages
(utils.browser_fallback). Rows that still fail keep their search-provider excerpt, labelled
``search_extract_only`` so it is never presented as a fetched publisher page.
"""
from __future__ import annotations

import asyncio
import contextlib
import os

from utils.primary_sources import fetch_priority, host_of, is_data_file, is_primary


def _seconds(name: str, default: float) -> float:
    try:
        return max(5.0, float(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def retry_seconds(row: dict) -> float:
    """Whole-download budget for a retry: long for official pages and data files, moderate otherwise."""
    url = str((row or {}).get("url") or "")
    ctype = str((row or {}).get("pageFetchContentType") or "")
    primary, data = is_primary(url), is_data_file(url, ctype)
    if primary and data:  # a government PDF took 70 s to download on a stable network
        return _seconds("GG_RETRY_FETCH_SECONDS_OFFICIAL_FILE", 90.0)
    if primary or data:
        return _seconds("GG_RETRY_FETCH_SECONDS_PRIMARY", 45.0)
    return _seconds("GG_RETRY_FETCH_SECONDS", 30.0)


def retry_order(rows: list[dict], indices: list[int], *, cap: int) -> list[int]:
    """Failed row indices in retry order (official data files, official pages, data files, rest), capped.

    Ties keep the original order, which is the search / topic ranking."""
    def key(i: int) -> tuple[int, int]:
        row = rows[i] or {}
        return (fetch_priority(str(row.get("url") or ""), str(row.get("pageFetchContentType") or "")), i)
    return sorted(indices, key=key)[: max(0, cap)]


class HostLimiter:
    """At most ``per_host`` concurrent requests to one host (asyncio, single event loop)."""

    def __init__(self, per_host: int = 2):
        self.per_host = max(1, int(per_host))
        self._sems: dict[str, asyncio.Semaphore] = {}

    @contextlib.asynccontextmanager
    async def slot(self, url: str):
        host = host_of(url) or "?"
        sem = self._sems.setdefault(host, asyncio.Semaphore(self.per_host))
        async with sem:
            yield


def status_counts(rows: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows or []:
        status = str((row or {}).get("pageFetchStatus") or "none")
        out[status] = out.get(status, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))
