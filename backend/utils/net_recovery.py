"""Wait out a network outage instead of losing the work in flight.

A live run lost all 13 sections when the host's DNS dropped for several minutes mid-report: every section call
retried for ~40 s, failed, and the report fell back to the single-call writer (one page). When calls fail only
because the network is unreachable, the caller waits here (probing the LLM / search endpoints every few seconds,
bounded) and retries the failed work once when the network is back.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

log = logging.getLogger("net_recovery")


def network_failure(error: str) -> bool:
    """A failure caused by the network (DNS, connect, read timeout), not by the model or the request."""
    text = str(error or "")
    return "network:" in text or any(k in text for k in ("ConnectError", "ConnectTimeout", "ReadTimeout",
                                                          "getaddrinfo", "Name or service not known"))


def wait_budget() -> float:
    """Total seconds a report may spend waiting for the network (GG_NETWORK_WAIT_SECONDS, default 300)."""
    # unit tests simulate network errors with fake gateways: they never wait on the real network
    default = "0" if os.getenv("PYTEST_CURRENT_TEST") else "300"
    try:
        return max(0.0, float(os.getenv("GG_NETWORK_WAIT_SECONDS", default)))
    except (TypeError, ValueError):
        return 300.0


async def wait_for_network(max_seconds: float, *, every: float = 10.0, probe=None) -> tuple[float, bool]:
    """(seconds waited, network back?). Probes at once, then every ``every`` seconds up to ``max_seconds``."""
    if probe is None:
        from utils.preflight import check_connectivity

        async def probe():
            return (await check_connectivity(timeout=6.0)).get("ok", False)
    started = time.monotonic()
    while True:
        try:
            if await probe():
                waited = time.monotonic() - started
                if waited > 1:
                    log.warning("Network back after %.0fs", waited)
                return waited, True
        except Exception as exc:  # a probe failure is just "not back yet"
            log.debug("Network probe failed: %s", type(exc).__name__)
        waited = time.monotonic() - started
        if waited + every > max_seconds:
            log.warning("Network still unreachable after %.0fs; giving up the wait", waited)
            return waited, False
        await asyncio.sleep(every)
