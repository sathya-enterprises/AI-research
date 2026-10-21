"""Canonical identity helpers shared by search, evidence, and report rendering.

Source identity is deliberately separate from a display/clickable URL. Tracking
parameters, fragments, `www.` aliases, and HTTP/HTTPS variants must not cause one
published source to acquire several IDs or make its fetched evidence unresolvable.
Meaningful query parameters (article IDs, document IDs, etc.) are preserved.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING_KEYS = {
    "gclid", "dclid", "fbclid", "msclkid", "mc_cid", "mc_eid",
    "_ga", "_gl", "vero_conv", "vero_id", "oly_anon_id", "oly_enc_id",
    "s_cid", "cmpid", "campaignid", "igshid", "mkt_tok", "ref_src", "ref_url",
    "fb_action_ids", "fb_action_types", "ga_source", "ga_medium", "ga_campaign",
}


def canonical_http_url(value: Any) -> str:
    """Return a normalized absolute HTTP(S) URL suitable for storage identity.

    Non-HTTP identifiers (for example `internal://verified-feed`) are returned
    lower-cased as opaque identifiers; blank and malformed values return empty.
    """
    raw = str(value or "").replace("\x00", "").strip()
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
        scheme = (parsed.scheme or "").lower()
        if scheme not in {"http", "https"} or not parsed.hostname:
            return raw.lower() if parsed.scheme else ""
        host = parsed.hostname.lower()
        if host.startswith("www."):
            host = host[4:]
        port = parsed.port
        netloc = host
        if port and port not in {80, 443}:
            netloc = f"{host}:{port}"
        # Treat HTTP and HTTPS as aliases for identity. The original clickable
        # URL is retained separately in the report manifest.
        scheme = "https"
        path = re.sub(r"/{2,}", "/", parsed.path or "/")
        if path != "/":
            path = path.rstrip("/") or "/"
        # These public-sector publishers expose the same ASP.NET pages with
        # inconsistent path and query-key casing (e.g. /Scripts/... and
        # /scripts/...?Id=23639). On these hosts those route/parameter names
        # are case-insensitive aliases; normalize them for identity only. The
        # original clickable URL remains untouched in the source manifest.
        case_insensitive_route_hosts = {
            "rbi.org.in", "pib.gov.in", "financialservices.gov.in",
            "indiabudget.gov.in", "npci.org.in", "iba.org.in",
        }
        if host in case_insensitive_route_hosts:
            path = path.casefold()
        pairs = []
        for key, val in parse_qsl(parsed.query, keep_blank_values=True):
            key_lower = key.casefold()
            if key_lower.startswith("utm_") or key_lower in _TRACKING_KEYS:
                continue
            pairs.append((key_lower if host in case_insensitive_route_hosts else key, val))
        query = urlencode(sorted(pairs, key=lambda item: (item[0].casefold(), item[1])))
        return urlunsplit((scheme, netloc, path, query, ""))
    except Exception:
        return ""


def _url_identity(canonical: str) -> str:
    """Canonicalize publisher URL aliases without changing clickable URLs.

    PIB sometimes exposes the same press release through PressReleasePage and
    PressReleaseIframePage with different `reg`/`lang` parameters. `PRID` is the
    stable article key, so path/locale variants must not receive two S# IDs.
    """
    try:
        parsed = urlsplit(canonical)
        host = (parsed.hostname or "").casefold()
        if host == "pib.gov.in":
            pairs = parse_qsl(parsed.query, keep_blank_values=True)
            prid = next((value for key, value in pairs if key.casefold() == "prid" and value.strip()), "")
            if prid:
                return f"url:https://pib.gov.in/__press_release__?prid={prid.strip()}"
    except Exception:
        pass
    return "url:" + canonical if canonical else ""


def source_identity_key(source: Any) -> str:
    """Return the one shared identity key for either a source record or URL."""
    if isinstance(source, dict):
        url = source.get("url") or source.get("sourceUrl") or source.get("source_url") or source.get("canonicalUrl") or source.get("canonical_url")
        if url:
            canonical = canonical_http_url(url)
            if canonical:
                return _url_identity(canonical)
        title = source.get("title") or source.get("name") or ""
        publisher = source.get("publisher") or source.get("domain") or source.get("host") or ""
        kind = source.get("kind") or source.get("source_type") or source.get("sourceType") or ""
    else:
        canonical = canonical_http_url(source)
        return _url_identity(canonical) if canonical else ""
    title_key = re.sub(r"[^a-z0-9]+", " ", str(title or "").casefold()).strip()
    publisher_key = re.sub(r"[^a-z0-9]+", " ", str(publisher or "").casefold()).strip()
    kind_key = re.sub(r"[^a-z0-9]+", " ", str(kind or "").casefold()).strip()
    if title_key or publisher_key:
        return f"label:{title_key}|{publisher_key}|{kind_key}"
    return ""
