"""Connectivity pre-flight before a long report run (Stage H).

A 20-60 minute report must not start when the LLM or search endpoint cannot be reached (live run G:
a network failure on the outline calls made the report fall back silently). Each endpoint gets one
short unauthenticated request (no keys are sent); any HTTP status counts as reachable, only a
connection/DNS/timeout failure does not. Required endpoints that are down fail the run fast with a
clear message; optional ones (market data) are reported only.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

import httpx

log = logging.getLogger("preflight")

ENDPOINTS = [
    ("LLM (Gemini API)", "https://generativelanguage.googleapis.com/", True),
    ("Web search (Exa)", "https://api.exa.ai/", True),
    ("Market data (Yahoo Finance)", "https://query1.finance.yahoo.com/", False),
]


def enabled() -> bool:
    return os.getenv("GG_CONNECTIVITY_PREFLIGHT", "1") != "0"


async def _probe(client: httpx.AsyncClient, name: str, url: str, required: bool) -> dict:
    started = time.perf_counter()
    try:
        response = await client.get(url)
        return {"name": name, "required": required, "ok": True, "status": response.status_code,
                "ms": int((time.perf_counter() - started) * 1000)}
    except Exception as exc:  # unreachable: DNS, connect, TLS or timeout
        return {"name": name, "required": required, "ok": False, "error": type(exc).__name__,
                "ms": int((time.perf_counter() - started) * 1000)}


async def check_connectivity(*, timeout: float = 8.0, endpoints=None) -> dict:
    """{'ok': bool, 'checks': [...], 'message': str}. One retry for a failed required endpoint."""
    endpoints = endpoints or ENDPOINTS
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=timeout), follow_redirects=False) as client:
        checks = await asyncio.gather(*[_probe(client, n, u, r) for n, u, r in endpoints])
        for i, check in enumerate(checks):
            if check["required"] and not check["ok"]:
                await asyncio.sleep(2)
                checks[i] = await _probe(client, check["name"], endpoints[i][1], True)
    down = [c["name"] for c in checks if c["required"] and not c["ok"]]
    message = ("" if not down else
               "The report was not started: " + " and ".join(down) + " could not be reached from the server "
               "(network or DNS problem). Nothing was charged; please try again in a few minutes.")
    result = {"ok": not down, "checks": checks, "message": message}
    log.info("Connectivity pre-flight: ok=%s %s", result["ok"],
             "; ".join(f"{c['name']}={'up' if c['ok'] else c.get('error')}" for c in checks))
    return result
