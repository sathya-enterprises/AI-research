"""Bounded Playwright fallback for publisher pages the HTTP fetcher could not read.

The main fetcher (utils/source_extractor.fetch_source_document) uses httpx +
BeautifulSoup. JavaScript-rendered pages (NSE, many market-data portals) and
pages that block plain HTTP clients come back as ``empty_extraction`` or an
HTTP 401/403/429. This module renders those pages in headless Chromium, then
runs the *same* HTML extraction and publisher-metadata parsing as the normal
path, so boilerplate removal and date/title handling stay identical.

Every rendered page still has to pass the caller's relevance check before it
becomes evidence. Budget and toggles:
  ENABLE_PLAYWRIGHT_FETCH=0           disable all Playwright fetching
  GG_BROWSER_FALLBACK=0               disable only this report fallback
  GG_BROWSER_FALLBACK_LIMIT=6,12       pages per run (detailed,in_depth)
  GG_BROWSER_FALLBACK_TIMEOUT=15       seconds per page
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Callable
from urllib.parse import urlparse

log = logging.getLogger("browser_fallback")

RETRY_STATUSES = {"empty_extraction", "empty_response", "http_401", "http_403", "http_429", "timeout", "network_error", "blocked"}
_DEFAULT_LIMITS = {"detailed": 6, "in_depth": 12}


def enabled() -> bool:
    off = {"0", "false", "no", "off"}
    return (os.getenv("ENABLE_PLAYWRIGHT_FETCH", "1").strip().lower() not in off
            and os.getenv("GG_BROWSER_FALLBACK", "1").strip().lower() not in off)


def page_limit(report_tier: str) -> int:
    raw = os.getenv("GG_BROWSER_FALLBACK_LIMIT", "").strip()
    limits = dict(_DEFAULT_LIMITS)
    if raw:
        try:
            values = [max(0, int(part)) for part in raw.split(",")]
            if len(values) == 2:
                limits = {"detailed": values[0], "in_depth": values[1]}
        except ValueError:
            log.warning("GG_BROWSER_FALLBACK_LIMIT=%r is not two integers; using defaults", raw)
    return limits.get(str(report_tier or "detailed"), limits["detailed"])


def _is_pdf(row: dict) -> bool:
    """A file a browser cannot render as a page (PDF, spreadsheet, CSV)."""
    from utils.primary_sources import is_data_file
    url = str(row.get("url") or "")
    ctype = str(row.get("pageFetchContentType") or "").casefold()
    return "pdf" in ctype or urlparse(url.casefold()).path.endswith(".pdf") or is_data_file(url, ctype)


def candidates(rows: list[dict], limit: int) -> list[int]:
    """Indices of rows worth a browser retry, official/data hosts first."""
    picked = []
    for idx, row in enumerate(rows or []):
        if not isinstance(row, dict) or _is_pdf(row):
            continue
        status = str(row.get("pageFetchStatus") or "")
        if status not in RETRY_STATUSES or not str(row.get("url") or "").startswith(("http://", "https://")):
            continue
        picked.append(idx)

    def priority(idx: int) -> tuple[int, int]:
        # any topic: government / statistics / international data hosts first (utils.primary_sources)
        from utils.primary_sources import is_primary
        return (0 if is_primary(str(rows[idx].get("url") or "")) else 1, idx)

    return sorted(picked, key=priority)[: max(0, limit)]


async def _render_html(urls: list[str], timeout_seconds: float, concurrency: int) -> dict[str, tuple[str, str]]:
    """Render pages with one shared browser; returns {url: (final_url, html)}."""
    from playwright.async_api import async_playwright

    out: dict[str, tuple[str, str]] = {}
    timeout_ms = int(max(3.0, timeout_seconds) * 1000)
    semaphore = asyncio.Semaphore(max(1, concurrency))
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            async def one(url: str) -> None:
                async with semaphore:
                    page = await browser.new_page(viewport={"width": 1280, "height": 1600})
                    try:
                        await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                        try:
                            await page.wait_for_load_state("networkidle", timeout=min(6000, timeout_ms))
                        except Exception:
                            pass
                        html = await page.content()
                        out[url] = (str(page.url or url), html[:3_000_000])
                    except Exception as exc:
                        log.debug("Browser fallback failed host=%s type=%s", urlparse(url).hostname, type(exc).__name__)
                    finally:
                        try:
                            await page.close()
                        except Exception:
                            pass
            await asyncio.gather(*(one(u) for u in urls))
        finally:
            await browser.close()
    return out


async def render_pages(urls: list[str], timeout_seconds: float = 15.0, concurrency: int = 3) -> dict[str, tuple[str, str]]:
    if not urls or not enabled():
        return {}
    try:
        import playwright  # noqa: F401
    except Exception:
        log.warning("Browser fallback unavailable: Playwright is not installed")
        return {}

    def factory():
        return _render_html(urls, timeout_seconds, concurrency)

    try:
        if os.name == "nt":
            # The app loop may be a SelectorEventLoop, which cannot spawn the
            # Playwright driver; run on a Proactor worker loop instead.
            from utils.playwright_runtime import run_coroutine_in_worker
            return await asyncio.to_thread(run_coroutine_in_worker, factory)
        return await factory()
    except Exception as exc:
        log.warning("Browser fallback failed: %s", type(exc).__name__)
        return {}


async def apply_browser_fallback(
    rows: list[dict],
    *,
    report_tier: str,
    max_chars: int,
    matches_source: Callable[[str, dict], bool],
    renderer=None,
) -> dict:
    """Re-fetch failed/blocked rows in a browser; update rows in place.

    ``matches_source(text, row)`` is the caller's relevance check (the same one
    the HTTP path uses). Returns stats for logging and the research capture.
    """
    from utils.source_extractor import _data_file_links, _extract_html, _extract_publisher_metadata

    stats = {"attempted": 0, "recovered": 0, "rejected_by_relevance": 0, "failed": 0, "limit": 0, "seconds": 0.0}
    if not enabled():
        return stats
    limit = page_limit(report_tier)
    stats["limit"] = limit
    picked = candidates(rows, limit)
    if not picked:
        return stats
    started = time.perf_counter()
    urls = [str(rows[i].get("url")) for i in picked]
    stats["attempted"] = len(urls)
    timeout = float(os.getenv("GG_BROWSER_FALLBACK_TIMEOUT", "15") or 15)
    rendered = await (renderer or render_pages)(urls, timeout_seconds=timeout)
    for idx in picked:
        row = rows[idx]
        url = str(row.get("url"))
        final_url, html = rendered.get(url, ("", ""))
        if not html:
            stats["failed"] += 1
            continue
        text = _extract_html(html, max_chars, True)
        # A rendered shell with a title and a few labels is not evidence.
        min_chars = int(os.getenv("GG_BROWSER_FALLBACK_MIN_CHARS", "300") or 300)
        if len(text.strip()) < min_chars:
            stats["failed"] += 1
            row["pageFetchBrowserStatus"] = "too_little_text"
            continue
        if not matches_source(text, row):
            stats["rejected_by_relevance"] += 1
            row["pageFetchBrowserStatus"] = "rejected_by_relevance_gate"
            continue
        meta = _extract_publisher_metadata(html, final_url or url) or {}
        row["pageContent"] = text
        row["pageFetchStatus"] = "ok"
        row["pageFetchMethod"] = "browser"
        links = _data_file_links(html, final_url or url)
        if links:
            row["pageDataLinks"] = links
        row["pageFetchRelevanceMatched"] = True
        row["pageFetchFinalUrl"] = final_url or url
        row["evidenceOrigin"] = "publisher_page_and_search_extract" if (row.get("fullContent") or row.get("snippet")) else "publisher_page"
        if meta.get("page_title"):
            row["publisherPageTitle"] = str(meta["page_title"])[:300]
        if meta.get("publisher"):
            row["publisherPagePublisher"] = str(meta["publisher"])[:180]
        if meta.get("published_at"):
            row["publisherPagePublishedAt"] = str(meta["published_at"])[:120]
            if not str(row.get("publication_date") or "").strip():
                row["publication_date"] = str(meta["published_at"])[:120]
                row["publicationDateSource"] = str(meta.get("publication_date_source") or "publisher_html_metadata")[:120]
        row.pop("pageFetchRejectReason", None)
        stats["recovered"] += 1
    stats["seconds"] = round(time.perf_counter() - started, 1)
    return stats
