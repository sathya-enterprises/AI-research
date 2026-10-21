"""
Web Search Service — provider-replaceable façade
================================================
This module exposes a stable provider-neutral search interface so the research
orchestrator does not depend on the vendor-specific API contract. Exa is the
default provider; configured Serper/SerpAPI and public Google News RSS remain
bounded fallbacks for live search failures.

The common result schema is normalized in the backend before it reaches report
generation, source persistence, and citation handling.

Provider selection is environment-driven::

    WEB_SEARCH_PROVIDER=exa         # default
    WEB_SEARCH_PROVIDER=serper      # requires SERPER_API_KEY
    WEB_SEARCH_PROVIDER=none        # hard-disable web search

Nothing here is finance-specific. ``topic``/``region``/``time_range`` are
plain optional parameters chosen per-query by the caller (in practice, by the
LLM through the tool schema) instead of being derived from a hard-coded
finance/general classification.

No API keys are read, logged, or returned by this module beyond what the
underlying providers already do — see ``utils/keys.py`` for the key pools.
"""

from __future__ import annotations

import html
import logging
import os
import re
import time
from typing import Iterable
from xml.etree import ElementTree
from urllib.parse import quote_plus

import httpx

log = logging.getLogger("websearch")

# Broad topic hints shared by provider adapters; these are not a hard-coded
# source allow-list or an automatic finance/market entity classification.
VALID_TOPICS = ("general", "news", "finance")
VALID_TIME_RANGES = ("day", "week", "month", "year")


def _normalize_topic(topic: str | None) -> str:
    t = (topic or "general").strip().lower()
    return t if t in VALID_TOPICS else "general"


def _normalize_time_range(time_range: str | None) -> str | None:
    t = (time_range or "").strip().lower()
    return t if t in VALID_TIME_RANGES else None


class WebSearchProvider:
    """Minimal provider contract. Implementations must never raise."""

    name = "base"

    def available(self) -> bool:  # pragma: no cover - trivial
        return False

    async def search(
        self,
        queries: list[str],
        *,
        max_results: int = 20,
        topic: str | None = None,
        region: str | None = None,
        time_range: str | None = None,
        images_out: list | None = None,
        historical_intent: bool = False,
    ) -> list[dict]:
        raise NotImplementedError


class ExaProvider(WebSearchProvider):
    """Exa Search adapter for the provider-neutral research orchestration layer."""

    name = "exa"

    def available(self) -> bool:
        # exa_search performs key checks and can use configured secondary search
        # providers or public RSS as a bounded fallback when Exa is unavailable.
        return True

    async def search(
        self,
        queries: list[str],
        *,
        max_results: int = 20,
        topic: str | None = None,
        region: str | None = None,
        time_range: str | None = None,
        images_out: list | None = None,
        historical_intent: bool = False,
    ) -> list[dict]:
        from routes.chat import exa_search_multi
        return await exa_search_multi(
            queries, max_results=max_results, topic=topic, region=region,
            time_range=time_range, images_out=images_out,
            historical_intent=historical_intent,
            # Normal chat only needs a small amount of publisher-page enrichment;
            # report.py performs its own dedicated multi-source evidence pass.
            page_enrich_limit=1,
            fast_chat=True,
        )


class SerperProvider(WebSearchProvider):
    """
    Optional drop-in alternative (google.serper.dev). Returns the same normalized
    result dictionaries as Exa so downstream code is vendor-independent.
    """

    name = "serper"
    ENDPOINT = "https://google.serper.dev/search"

    def _keys(self) -> list[str]:
        from utils.keys import get_serper_keys

        return get_serper_keys()

    def available(self) -> bool:
        return bool(self._keys())

    async def search(
        self,
        queries: list[str],
        *,
        max_results: int = 20,
        topic: str | None = None,
        region: str | None = None,
        time_range: str | None = None,
        images_out: list | None = None,
        historical_intent: bool = False,
    ) -> list[dict]:
        keys = self._keys()
        if not keys:
            return []

        # Serper expresses freshness as a Google "tbs" qdr: token.
        qdr = {"day": "qdr:d", "week": "qdr:w", "month": "qdr:m", "year": "qdr:y"}.get(
            _normalize_time_range(time_range) or "", ""
        )
        seen: set[str] = set()
        merged: list[dict] = []
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=25, write=15, pool=10)) as client:
                from utils.keys import mark_rate_limited
                key_index = 0
                for q in queries[:4]:
                    for _ in range(min(len(keys), 2)):
                        key = keys[key_index % len(keys)]
                        key_index += 1
                        payload: dict = {"q": q, "num": min(max_results, 20)}
                        if qdr:
                            payload["tbs"] = qdr
                        if region:
                            payload["gl"] = region[:2].lower()
                        res = await client.post(
                            self.ENDPOINT,
                            headers={"X-API-KEY": key, "Content-Type": "application/json"},
                            json=payload,
                        )
                        if res.status_code == 429:
                            mark_rate_limited(key, 60_000)
                            log.warning("Serper rate limit on key ...%s", key[-4:])
                            continue
                        if res.status_code in (401, 403):
                            mark_rate_limited(key, 24 * 60 * 60_000)
                            log.warning("Serper authentication error HTTP %d on key ...%s", res.status_code, key[-4:])
                            continue
                        if not res.is_success:
                            log.warning("Serper HTTP %d for %r", res.status_code, q[:60])
                            continue
                        organic = res.json().get("organic") or []
                        if not organic:
                            # HTTP 200 with no organic results is not a useful
                            # search result set. Try the next configured key.
                            continue
                        for r in organic:
                            url = r.get("link", "")
                            if not url:
                                continue
                            from utils.source_identity import source_identity_key
                            identity = source_identity_key(url)
                            if not identity or identity in seen:
                                continue
                            seen.add(identity)
                            merged.append({
                                "title": r.get("title", ""),
                                "url": url,
                                "snippet": (r.get("snippet") or "")[:800],
                                "fullContent": (r.get("snippet") or "")[:800],
                                "score": None,
                                "published": r.get("date"),
                                "trusted_source": True,
                                "pageFetchStatus": "not_fetched",
                            })
                        break
        except Exception as exc:
            log.warning("Serper search failed: %s", type(exc).__name__)
        return merged


class SerpAPIProvider(WebSearchProvider):
    """SerpAPI Google-results fallback for research search."""

    name = "serpapi"
    ENDPOINT = "https://serpapi.com/search.json"

    def _keys(self) -> list[str]:
        from utils.keys import get_serpapi_keys
        return get_serpapi_keys()

    def available(self) -> bool:
        return bool(self._keys())

    async def search(
        self,
        queries: list[str],
        *,
        max_results: int = 20,
        topic: str | None = None,
        region: str | None = None,
        time_range: str | None = None,
        images_out: list | None = None,
        historical_intent: bool = False,
    ) -> list[dict]:
        keys = self._keys()
        if not keys:
            return []

        from utils.keys import mark_rate_limited

        qdr = {"day": "qdr:d", "week": "qdr:w", "month": "qdr:m", "year": "qdr:y"}.get(
            _normalize_time_range(time_range) or "", ""
        )
        seen: set[str] = set()
        merged: list[dict] = []
        key_index = 0
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(connect=8, read=20, write=12, pool=8)) as client:
                for q in queries[:6]:
                    success = False
                    for _ in range(min(len(keys), 2)):
                        key = keys[key_index % len(keys)]
                        key_index += 1
                        params: dict = {
                            "engine": "google",
                            "q": q,
                            "api_key": key,
                            "num": min(max_results, 20),
                        }
                        if qdr:
                            params["tbs"] = qdr
                        if region:
                            params["gl"] = region[:2].lower()
                        res = await client.get(self.ENDPOINT, params=params)
                        if res.status_code == 429:
                            mark_rate_limited(key, 60_000)
                            log.warning("SerpAPI 429 on key ...%s", key[-4:])
                            continue
                        if res.status_code in (401, 403):
                            mark_rate_limited(key, 24 * 60 * 60_000)
                            log.warning("SerpAPI auth error HTTP %d on key ...%s", res.status_code, key[-4:])
                            continue
                        if not res.is_success:
                            log.warning("SerpAPI HTTP %d for %r", res.status_code, q[:60])
                            continue
                        data = res.json()
                        for r in (data.get("organic_results") or []):
                            url = r.get("link", "")
                            if not url:
                                continue
                            from utils.source_identity import source_identity_key
                            identity = source_identity_key(url)
                            if not identity or identity in seen:
                                continue
                            seen.add(identity)
                            merged.append({
                                "title": r.get("title", ""),
                                "url": url,
                                "snippet": (r.get("snippet") or "")[:1200],
                                "fullContent": (r.get("snippet") or "")[:1200],
                                "score": None,
                                "published": r.get("date") or r.get("publication_date"),
                                "trusted_source": True,
                            })
                        success = True
                        break
                    if not success:
                        continue
        except Exception as exc:
            log.warning("SerpAPI search failed: %s", type(exc).__name__)
        return merged


class GoogleNewsRssProvider(WebSearchProvider):
    """Small public RSS fallback used only after configured search providers fail."""

    name = "google_news_rss"
    ENDPOINT = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"

    def available(self) -> bool:
        return True

    async def search(
        self,
        queries: list[str],
        *,
        max_results: int = 20,
        region: str | None = None,
        **_kwargs,
    ) -> list[dict]:
        from utils.source_identity import source_identity_key

        region_code = (region or "US")[:2].upper()
        seen: set[str] = set()
        merged: list[dict] = []
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(connect=5, read=12, write=5, pool=5), follow_redirects=True) as client:
                for query in queries[:3]:
                    url = self.ENDPOINT.format(query=quote_plus(str(query)[:300]))
                    if region_code != "US":
                        url = url.replace("hl=en-US&gl=US&ceid=US:en", f"hl=en-{region_code}&gl={region_code}&ceid={region_code}:en")
                    response = await client.get(url, headers={"User-Agent": "GrowthGradualResearch/1.0"})
                    if not response.is_success:
                        log.warning("Google News RSS HTTP %d for query=%r", response.status_code, str(query)[:60])
                        continue
                    try:
                        root = ElementTree.fromstring(response.content)
                    except ElementTree.ParseError:
                        log.warning("Google News RSS returned invalid XML")
                        continue
                    for item in root.findall(".//item"):
                        link = str(item.findtext("link") or "").strip()
                        identity = source_identity_key(link)
                        if not link or not identity or identity in seen:
                            continue
                        seen.add(identity)
                        description = str(item.findtext("description") or "")
                        title = str(item.findtext("title") or "")
                        # RSS descriptions are HTML; strip tags without trusting
                        # the snippet as publisher-page content.
                        description = re.sub(r"<[^>]+>", " ", description)
                        description = " ".join(html.unescape(description).split())[:1200]
                        merged.append({
                            "title": html.unescape(title)[:500], "url": link,
                            "snippet": description, "fullContent": description,
                            "score": None, "published": item.findtext("pubDate"),
                            "publisher": item.findtext("source"), "trusted_source": False,
                            "pageFetchStatus": "not_fetched",
                        })
                        if len(merged) >= max_results:
                            return merged
        except Exception as exc:
            log.warning("Google News RSS search failed: %s", type(exc).__name__)
        return merged


class NullProvider(WebSearchProvider):
    """Explicitly disabled web search — used when WEB_SEARCH_PROVIDER=none."""

    name = "none"

    def available(self) -> bool:
        return False

    async def search(self, queries: list[str], **_kwargs) -> list[dict]:
        return []


_REGISTRY: dict[str, type[WebSearchProvider]] = {
    ExaProvider.name: ExaProvider,
    SerperProvider.name: SerperProvider,
    SerpAPIProvider.name: SerpAPIProvider,
    GoogleNewsRssProvider.name: GoogleNewsRssProvider,
    NullProvider.name: NullProvider,
}


def register_provider(cls: type[WebSearchProvider]) -> None:
    """Hook for adding a provider without editing this module."""
    _REGISTRY[cls.name] = cls


def provider_name() -> str:
    configured = (os.environ.get("WEB_SEARCH_PROVIDER") or ExaProvider.name).strip().lower()
    # Older deployments may still have WEB_SEARCH_PROVIDER=tavily. Treat that
    # stale setting as the new default so no request goes to Tavily.
    return "exa" if configured == "tavily" else configured


def get_provider(name: str | None = None) -> WebSearchProvider:
    key = (name or provider_name()).lower()
    cls = _REGISTRY.get(key)
    if cls is None:
        log.warning("Unknown WEB_SEARCH_PROVIDER=%r — falling back to %s", key, ExaProvider.name)
        cls = ExaProvider
    return cls()


def web_search_available() -> bool:
    try:
        return get_provider().available()
    except Exception:
        return False


async def search_web(
    queries: str | Iterable[str],
    *,
    max_results: int = 20,
    topic: str | None = None,
    region: str | None = None,
    time_range: str | None = None,
    images_out: list | None = None,
    historical_intent: bool = False,
    provider: str | None = None,
) -> dict:
    """
    Run a web search through the configured provider.

    Never raises. Always returns::

        {"results": [...], "provider": str, "error": str | None, "elapsed_ms": int}

    An empty ``results`` list with ``error=None`` means the search ran and
    genuinely found nothing — callers must treat that as "no data available",
    never as licence to answer from memory as if it were retrieved fact.
    """
    qs = [queries] if isinstance(queries, str) else [q for q in queries if q and q.strip()]
    if not qs:
        return {"results": [], "provider": provider_name(), "error": "empty query", "elapsed_ms": 0}

    prov = get_provider(provider)
    t0 = time.perf_counter()
    if not prov.available():
        log.warning("Web search unavailable — provider=%s has no usable credentials", prov.name)
        return {
            "results": [],
            "provider": prov.name,
            "error": f"web search provider '{prov.name}' is not configured",
            "elapsed_ms": 0,
        }

    try:
        results = await prov.search(
            qs,
            max_results=max_results,
            topic=topic,
            region=region,
            time_range=time_range,
            images_out=images_out,
            historical_intent=historical_intent,
        )
        err = None
    except Exception as exc:
        results, err = [], type(exc).__name__
        log.warning("Web search provider %s raised: %s", prov.name, type(exc).__name__)

    elapsed = int((time.perf_counter() - t0) * 1000)
    log.info(
        "Web search via %s: %d query(ies) -> %d result(s) in %dms (topic=%s region=%s time_range=%s)",
        prov.name, len(qs), len(results), elapsed, _normalize_topic(topic), region,
        _normalize_time_range(time_range),
    )
    return {"results": results, "provider": prov.name, "error": err, "elapsed_ms": elapsed}
