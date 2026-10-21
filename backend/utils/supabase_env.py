"""Supabase URL hygiene + startup diagnostics.

``[Errno 11001] getaddrinfo failed`` (Windows) means the OS could not resolve the
Supabase hostname. The usual causes are a malformed value in ``.env`` (quotes,
trailing ``/rest/v1``, placeholder text), a stale ``SCREENER_SUPABASE_URL``
overriding a good ``SUPABASE_URL``, or a paused/deleted project. This module
cleans the value, lets ``screener_kb`` fall back to ``SUPABASE_URL`` when its own
override does not resolve, and logs one clear diagnosis at startup.
"""
from __future__ import annotations

import logging
import os
import re
import socket
from urllib.parse import urlparse

log = logging.getLogger("supabase_env")

_PLACEHOLDER_RE = re.compile(r"your-project|your_project|<|>|example\.com|xxxx", re.I)


def clean_supabase_url(raw: str | None) -> str:
    """Normalise a pasted Supabase project URL to ``https://<ref>.supabase.co``."""
    value = str(raw or "").strip().strip("\"'`").strip()
    if not value:
        return ""
    if not re.match(r"^[a-z][a-z0-9+.-]*://", value, re.I):
        value = "https://" + value
    parsed = urlparse(value)
    host = (parsed.hostname or "").strip()
    if not host:
        return ""
    scheme = parsed.scheme if parsed.scheme in {"http", "https"} else "https"
    port = f":{parsed.port}" if parsed.port and parsed.port not in {80, 443} else ""
    # Drop any path such as /rest/v1, /auth/v1 or a trailing slash.
    return f"{scheme}://{host}{port}"


def is_placeholder(url: str) -> bool:
    return bool(url) and bool(_PLACEHOLDER_RE.search(url))


def host_resolves(url: str, timeout: float = 4.0) -> tuple[bool, str]:
    """Return (resolves, detail). Never raises."""
    host = urlparse(url).hostname if url else None
    if not host:
        return False, "no hostname"
    old = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(timeout)
        socket.getaddrinfo(host, 443)
        return True, host
    except OSError as exc:
        return False, f"{host}: {exc}"
    finally:
        socket.setdefaulttimeout(old)


def supabase_url(*names: str) -> str:
    """First non-empty env var among ``names``, cleaned."""
    for name in names or ("SUPABASE_URL",):
        cleaned = clean_supabase_url(os.environ.get(name))
        if cleaned:
            return cleaned
    return ""


def screener_url() -> str:
    """URL for screener_kb: SCREENER_SUPABASE_URL, but fall back to SUPABASE_URL
    when the override is a placeholder or its hostname does not resolve."""
    override = clean_supabase_url(os.environ.get("SCREENER_SUPABASE_URL"))
    main = clean_supabase_url(os.environ.get("SUPABASE_URL"))
    if override and override != main:
        bad = is_placeholder(override)
        if not bad:
            ok, _detail = host_resolves(override, timeout=3.0)
            bad = not ok
        if bad and main:
            log.warning(
                "screener_kb: SCREENER_SUPABASE_URL (%s) is a placeholder or does not resolve; "
                "falling back to SUPABASE_URL. Remove or fix SCREENER_SUPABASE_URL in .env.", override)
            return main
    return override or main


def diagnose_supabase_env() -> list[str]:
    """Log a plain-language diagnosis; returns the problem lines (empty when healthy)."""
    problems: list[str] = []
    for var in ("SUPABASE_URL", "SCREENER_SUPABASE_URL"):
        raw = os.environ.get(var)
        if not raw:
            continue
        cleaned = clean_supabase_url(raw)
        if cleaned != raw.rstrip("/"):
            log.info("%s was normalised from %r to %r", var, raw[:80], cleaned)
        if is_placeholder(cleaned):
            problems.append(f"{var} still contains placeholder text ({cleaned}). Copy the real URL from Supabase → Project Settings → API.")
            continue
        ok, detail = host_resolves(cleaned)
        if not ok:
            problems.append(
                f"{var} hostname does not resolve ({detail}). Check for a typo, a PAUSED Supabase project "
                f"(free projects pause after ~1 week idle — click Restore in the dashboard), a VPN/proxy, or Windows DNS "
                f"(try `nslookup {urlparse(cleaned).hostname}` and `ipconfig /flushdns`).")
    if os.environ.get("SUPABASE_URL") and not (os.environ.get("SUPABASE_ANON_KEY") or os.environ.get("SUPABASE_SECRET_KEY")):
        problems.append("SUPABASE_URL is set but SUPABASE_ANON_KEY is empty; screener_kb and session storage stay disabled.")
    if not os.environ.get("SUPABASE_URL"):
        problems.append("SUPABASE_URL is not set; session history and the screener knowledge base are disabled.")
    for line in problems:
        log.warning("Supabase config: %s", line)
    if not problems:
        log.info("Supabase config OK (hostnames resolve).")
    return problems
