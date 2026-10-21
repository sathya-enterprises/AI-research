"""Helpers for running Playwright with a Windows-compatible event loop.

Windows' SelectorEventLoop does not implement subprocess transports, which the
Playwright driver needs. A worker thread with an explicitly-created Proactor
loop avoids mutating the application's process-wide event-loop policy.
"""
from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Coroutine
from typing import Any


def run_coroutine_in_worker(coroutine_factory: Callable[[], Coroutine[Any, Any, Any]]) -> Any:
    """Run a coroutine in a dedicated thread-local loop, using Proactor on Windows."""
    if os.name == "nt" and hasattr(asyncio, "ProactorEventLoop"):
        loop = asyncio.ProactorEventLoop()
    else:
        loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coroutine_factory())
    finally:
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception:
            pass
        loop.close()
        asyncio.set_event_loop(None)
