"""Bounded, query-aware same-site crawler for research reports.

This supplements search APIs (Exa/Serper/SerpAPI) by following a small number
of relevant internal links from publisher pages already admitted by the
relevance gate. It is not an unrestricted spider: every run has a depth/page
budget, stays on the seed hostname (including its www/non-www alias and
subdomains), skips non-HTML/assets and records crawl provenance.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import re
from collections import deque
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx

log = logging.getLogger("site_crawler")

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/132.0 Safari/537.36 GrowthGradualResearch/1.0",
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
    "Accept-Language": "en-US,en;q=0.8",
}
_SKIP_EXTENSIONS = {
    ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".css", ".js",
    ".zip", ".gz", ".rar", ".7z", ".mp4", ".mp3", ".avi", ".woff", ".woff2", ".ttf",
    ".eot", ".xlsx", ".xls", ".csv", ".ppt", ".pptx", ".doc", ".docx",
}
_SKIP_PATHS = re.compile(
    r"/(?:login|logout|signin|sign-in|signup|register|privacy|terms|cookie|cart|checkout|"
    r"wp-admin|wp-login|feed|rss|search|tag|category|author|page/\d+)(?:/|$)", re.I
)
_SKIP_QUERY_KEYS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "gclid", "fbclid", "ref", "source"}
_STOPWORDS = {
    "about", "after", "also", "and", "are", "around", "based", "before", "between", "from", "into", "near",
    "over", "that", "the", "this", "through", "using", "what", "when", "where", "which", "with", "within",
    "company", "companies", "software", "technology", "tech", "city", "bengaluru", "bangalore", "india", "report",
    "research", "current", "credible", "sources", "source", "target", "targets", "find", "search", "investors",
}
_HIGH_VALUE_LINK_TERMS = {
    "investor", "investors", "venture", "capital", "angel", "fund", "funding", "portfolio", "investment", "investments",
    "startup", "startups", "founder", "founders", "company", "companies", "member", "members", "directory", "incubator",
    "accelerator", "technology", "software", "business", "team", "about", "contact", "project", "projects", "ecosystem",
}


def _safe_url(raw: str, base: str = "") -> str | None:
    try:
        value = urljoin(base, str(raw or "").strip()) if base else str(raw or "").strip()
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            return None
        if parsed.username or parsed.password:
            return None
        host = parsed.hostname.lower().rstrip(".")
        if host in {"localhost", "localhost.localdomain", "0.0.0.0", "::1"} or host.endswith(".local"):
            return None
        try:
            ip = ipaddress.ip_address(host)
            if not ip.is_global:
                return None
        except ValueError:
            pass
        path = parsed.path or "/"
        if path.casefold().endswith(tuple(_SKIP_EXTENSIONS)) or _SKIP_PATHS.search(path):
            return None
        kept_query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k.casefold() not in _SKIP_QUERY_KEYS]
        return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, urlencode(kept_query, doseq=True), ""))
    except Exception:
        return None


def _site_key(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def _same_site(candidate: str, seed: str) -> bool:
    ch, sh = _site_key(candidate), _site_key(seed)
    return bool(ch and sh and (ch == sh or ch.endswith("." + sh)))


def _relevance_tokens(question: str) -> set[str]:
    return {
        token for token in re.findall(r"[a-z0-9]{3,}", str(question or "").casefold())
        if token not in _STOPWORDS and not token.isdigit()
    }


def _link_score(text: str, url: str, query_tokens: set[str]) -> int:
    value = f"{text} {urlsplit(url).path.replace('-', ' ').replace('_', ' ')}".casefold()
    tokens = set(re.findall(r"[a-z0-9]{3,}", value))
    score = 2 * len(tokens & query_tokens) + 3 * len(tokens & _HIGH_VALUE_LINK_TERMS)
    if any(word in value for word in ("portfolio", "investor", "venture", "funding", "member-companies", "company-directory")):
        score += 5
    return score


def _extract_html_page(raw_html: str, url: str, max_chars: int) -> tuple[str, str, list[tuple[str, str]], str]:
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(raw_html or "", "html.parser")
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        canonical = soup.find("link", rel=lambda value: value and "canonical" in str(value).lower())
        canonical_url = _safe_url(canonical.get("href", ""), url) if canonical else url
        if not canonical_url or not _same_site(canonical_url, url):
            canonical_url = url
        raw_links: list[tuple[str, str]] = []
        for anchor in soup.find_all("a", href=True):
            href = _safe_url(anchor.get("href", ""), url)
            if not href or not _same_site(href, url) or href == url:
                continue
            label = re.sub(r"\s+", " ", anchor.get_text(" ", strip=True))[:240]
            if len(label) < 2 and len(urlsplit(href).path.strip("/")) < 3:
                continue
            raw_links.append((href, label))
        for node in soup.find_all(["script", "style", "noscript", "svg", "nav", "footer", "header", "aside", "form", "button", "iframe", "canvas"]):
            node.decompose()
        for node in soup.find_all(True):
            marker = " ".join((str(node.get("id") or ""), " ".join(str(x) for x in (node.get("class") or [])))).casefold()
            if any(term in marker for term in ("cookie", "navigation", "breadcrumb", "share-tools", "newsletter", "advertisement", "modal", "related-content")):
                node.decompose()
        blocks: list[str] = []
        for selector in ("article", "main", '[role="main"]', '[itemprop="articleBody"]', ".article-body", ".article-content", ".entry-content", ".post-content"):
            for node in soup.select(selector):
                local = [re.sub(r"\s+", " ", x.get_text(" ", strip=True)).strip() for x in node.find_all(["h1", "h2", "h3", "h4", "p", "li", "blockquote", "tr"])]
                local = [x for x in local if len(x) >= 25]
                if sum(map(len, local)) > sum(map(len, blocks)):
                    blocks = local
        if not blocks:
            body = soup.body or soup
            blocks = [re.sub(r"\s+", " ", x.get_text(" ", strip=True)).strip() for x in body.find_all(["h1", "h2", "h3", "h4", "p", "li", "blockquote", "tr"])]
            blocks = [x for x in blocks if len(x) >= 25]
        if title and (not blocks or title.casefold() not in blocks[0].casefold()):
            blocks.insert(0, title)
        text = "\n".join(dict.fromkeys(blocks))[:max_chars].strip()
        # De-duplicate URL/label pairs without losing the page's own link text.
        links_seen: set[str] = set()
        links = []
        for link, label in raw_links:
            if link in links_seen:
                continue
            links_seen.add(link)
            links.append((link, label))
        description = ""
        desc = soup.find("meta", attrs={"name": re.compile("description", re.I)})
        if desc:
            description = re.sub(r"\s+", " ", str(desc.get("content") or "")).strip()[:600]
        return title[:300], text, links, canonical_url
    except Exception:
        return "", "", [], url


async def _fetch_rendered_html_pages(urls: list[str], timeout_seconds: float = 9.0) -> dict[str, str]:
    """Bounded Playwright fallback for same-site pages whose HTTP HTML exposes no links."""
    if os.getenv("ENABLE_PLAYWRIGHT_CRAWL_FALLBACK", "1").strip().lower() in {"0", "false", "no", "off"}:
        return {}
    safe_urls = []
    for raw in (urls or [])[:2]:
        url = _safe_url(raw)
        if url and url not in safe_urls:
            safe_urls.append(url)
    if not safe_urls:
        return {}
    try:
        from playwright.async_api import async_playwright
    except Exception:
        log.warning("Site crawl browser fallback unavailable: Playwright package is not installed")
        return {}
    output: dict[str, str] = {}
    timeout_ms = max(2500, min(int(float(timeout_seconds) * 1000), 10000))
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                async def render_one(url: str) -> tuple[str, str]:
                    page = await browser.new_page(viewport={"width": 1280, "height": 1200})
                    try:
                        await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                        try:
                            await page.wait_for_timeout(450)
                        except Exception:
                            pass
                        final_url = _safe_url(str(page.url or url))
                        if not final_url or not _same_site(final_url, url):
                            return url, ""
                        html = await page.content()
                        return url, html if len(html) <= 2_000_000 else html[:2_000_000]
                    except Exception as exc:
                        log.debug("Playwright crawl fallback failed host=%s type=%s", urlsplit(url).hostname, type(exc).__name__)
                        return url, ""
                    finally:
                        try:
                            await page.close()
                        except Exception:
                            pass
                rows = await asyncio.gather(*(render_one(url) for url in safe_urls), return_exceptions=False)
                output = {url: html for url, html in rows if html}
            finally:
                await browser.close()
    except Exception as exc:
        log.warning("Site crawl browser fallback failed type=%s", type(exc).__name__)
    return output


async def crawl_relevant_internal_pages(
    seeds: list[dict],
    question: str,
    *,
    report_tier: str = "brief",
    max_pages: int | None = None,
    max_depth: int = 2,
    per_page_chars: int = 9000,
    timeout_seconds: float = 9.0,
) -> tuple[list[dict], dict]:
    """Crawl relevant same-site pages from fetched, query-relevant source seeds.

    Only successful, relevance-approved seed pages are used. The crawler follows
    high-value internal links, stays within each seed site, and uses a bounded
    concurrency/page/time budget. The report layer applies its normal query-specific
    relevance filter again before any crawled content can be used as evidence.
    """
    tier = str(report_tier or "brief").lower()
    default_limits = {"brief": 3, "detailed": 12, "in_depth": 24}
    # Deep Research needs a wider bounded crawl; the report layer still filters
    # every returned page for query relevance before the model can use it.
    page_limit = max(0, min(int(max_pages if max_pages is not None else default_limits.get(tier, 3)), 28))
    depth_limit = max(1, min(int(max_depth or 1), 3))
    stats = {"enabled": page_limit > 0, "seed_count": 0, "pages_requested": 0, "pages_fetched": 0, "links_discovered": 0, "accepted_candidates": 0, "errors": 0, "browser_fallback_attempts": 0, "browser_fallback_successes": 0, "max_depth": depth_limit, "page_limit": page_limit}
    if page_limit <= 0 or not seeds:
        return [], stats
    query_tokens = _relevance_tokens(question)
    seed_rows: list[dict] = []
    seed_hosts: set[str] = set()
    max_seeds = {"brief": 2, "detailed": 5, "in_depth": 8}.get(tier, 2)
    for source in seeds:
        if not isinstance(source, dict) or source.get("pageFetchStatus") != "ok" or source.get("pageFetchRelevanceMatched") is False:
            continue
        url = _safe_url(str(source.get("pageFetchUrl") or source.get("url") or ""))
        if not url:
            continue
        host = _site_key(url)
        if not host or host in seed_hosts:
            continue
        # Sites whose seed text has little or no relation to the query should not be expanded.
        seed_text = " ".join((str(source.get("title") or ""), str(source.get("snippet") or ""), str(source.get("pageContent") or "")))
        seed_tokens = set(re.findall(r"[a-z0-9]{3,}", seed_text.casefold()))
        overlap = len(seed_tokens & query_tokens)
        if query_tokens and overlap == 0:
            continue
        seed_hosts.add(host)
        seed_rows.append({"url": url, "title": str(source.get("title") or ""), "source": source})
        if len(seed_rows) >= max_seeds:
            break
    stats["seed_count"] = len(seed_rows)
    if not seed_rows:
        return [], stats

    try:
        crawl_concurrency = max(3, min(10, int(os.getenv("GG_REPORT_CRAWL_CONCURRENCY", "7"))))
    except (TypeError, ValueError):
        crawl_concurrency = 7
    semaphore = asyncio.Semaphore(crawl_concurrency)
    visited: set[str] = set(row["url"] for row in seed_rows)
    queued: set[str] = set(visited)
    candidates: deque[tuple[str, str, str, int]] = deque()
    discovered_order: list[tuple[str, str, str, int]] = []
    fetched_rows: list[dict] = []
    per_host_counts: dict[str, int] = {}

    async def fetch_html(client: httpx.AsyncClient, url: str):
        stats["pages_requested"] += 1
        try:
            async with semaphore:
                response = await client.get(url, headers=_HEADERS, timeout=httpx.Timeout(timeout_seconds))
            if not response.is_success:
                stats["errors"] += 1
                return None, response.status_code, str(response.url), response.headers.get("content-type", "")
            content_type = str(response.headers.get("content-type") or "").casefold()
            if "html" not in content_type and "xhtml" not in content_type:
                return None, response.status_code, str(response.url), content_type
            return response.text, response.status_code, str(response.url), content_type
        except Exception as exc:
            stats["errors"] += 1
            log.debug("Site crawl fetch failed host=%s type=%s", urlsplit(url).hostname, type(exc).__name__)
            return None, None, url, ""

    async with httpx.AsyncClient(follow_redirects=True, headers=_HEADERS, timeout=httpx.Timeout(timeout_seconds)) as client:
        # Fetch seed HTML concurrently only to discover internal links; the
        # report's separate source extractor remains authoritative for the seed
        # evidence. This avoids serially spending the full timeout per seed.
        seed_fetches = await asyncio.gather(*(fetch_html(client, seed["url"]) for seed in seed_rows))
        parsed_seeds = []
        fallback_urls = []
        for seed, (html, _status, final_url, _ctype) in zip(seed_rows, seed_fetches):
            if html:
                _title, _text, links, _canonical = _extract_html_page(html, final_url, 1200)
            else:
                links = []
            parsed_seeds.append((seed, html, final_url, links))
            # A publisher page that renders its navigation with JavaScript can
            # return HTTP 200 but an empty link set. Retry at most two such seeds
            # in a browser; the same-site and page/depth bounds still apply.
            if not links and len(fallback_urls) < 2:
                fallback_urls.append(seed["url"])
        rendered_html = {}
        if fallback_urls and os.getenv("ENABLE_PLAYWRIGHT_CRAWL_FALLBACK", "1").strip().lower() not in {"0", "false", "no", "off"}:
            stats["browser_fallback_attempts"] = len(fallback_urls)
            rendered_html = await _fetch_rendered_html_pages(fallback_urls, timeout_seconds=min(timeout_seconds, 10.0))
            stats["browser_fallback_successes"] = len(rendered_html)
        for seed, html, final_url, links in parsed_seeds:
            if not links and rendered_html.get(seed["url"]):
                rendered_title, _rendered_text, rendered_links, rendered_canonical = _extract_html_page(
                    rendered_html[seed["url"]], seed["url"], 1200,
                )
                if rendered_links:
                    links = rendered_links
                    final_url = rendered_canonical or seed["url"]
                    log.info("Site crawl: browser-rendered internal links discovered host=%s links=%d", urlsplit(seed["url"]).hostname, len(links))
            stats["links_discovered"] += len(links)
            ranked = []
            for link, label in links:
                if link in queued or link in visited or not _same_site(link, seed["url"]):
                    continue
                score = _link_score(label, link, query_tokens)
                if score <= 0:
                    continue
                ranked.append((score, link, label, final_url, 1))
            ranked.sort(key=lambda item: (-item[0], item[1]))
            for _score, link, label, seed_url, depth in ranked[:max(8, page_limit * 3)]:
                queued.add(link)
                candidates.append((link, label, seed_url, depth))
        # Breadth-first within a bounded depth/page budget. Follow only selected
        # same-site links whose labels/paths show some relationship to the query.
        while candidates and len(fetched_rows) < page_limit:
            batch: list[tuple[str, str, str, int]] = []
            while candidates and len(batch) < min(8, page_limit - len(fetched_rows)):
                candidate = candidates.popleft()
                url, label, seed_url, depth = candidate
                if url in visited:
                    continue
                host = _site_key(url)
                host_cap = {"brief": 2, "detailed": 4, "in_depth": 6}.get(tier, 2)
                if per_host_counts.get(host, 0) >= host_cap:
                    continue
                visited.add(url)
                per_host_counts[host] = per_host_counts.get(host, 0) + 1
                batch.append(candidate)
            if not batch:
                continue
            raw_pages = await asyncio.gather(*(fetch_html(client, row[0]) for row in batch))
            for (url, anchor, seed_url, depth), (raw_html, status_code, final_url, content_type) in zip(batch, raw_pages):
                if not raw_html:
                    continue
                title, content, links, canonical = _extract_html_page(raw_html, final_url, per_page_chars)
                if not content or len(content) < 180:
                    continue
                stats["pages_fetched"] += 1
                candidate_tokens = set(re.findall(r"[a-z0-9]{3,}", (title + " " + anchor + " " + urlsplit(canonical).path).casefold()))
                if query_tokens and not (candidate_tokens & query_tokens) and _link_score(anchor, canonical, query_tokens) <= 0:
                    # Keep investor/funding/member-profile pages if they are relevant to the use case.
                    if not (candidate_tokens & _HIGH_VALUE_LINK_TERMS):
                        continue
                fetched_rows.append({
                    "title": title or anchor or urlsplit(canonical).path.strip("/") or canonical,
                    "url": canonical,
                    "snippet": content[:900],
                    "fullContent": content,
                    "pageContent": content,
                    "pageFetchStatus": "ok",
                    "pageFetchRelevanceMatched": True,
                    "pageFetchHttpStatus": status_code,
                    "pageFetchContentType": content_type,
                    "pageFetchFinalUrl": final_url,
                    "pageFetchUrl": canonical,
                    "source_type": "website_crawl",
                    "kind": "website_crawl",
                    "source_role": "crawled_internal_page",
                    "searchProvider": "site_crawl",
                    "searchQuery": question,
                    "evidenceOrigin": "publisher_page_crawl",
                    "crawlDepth": depth,
                    "crawlSeedUrl": seed_url,
                    "crawlAnchorText": anchor[:240],
                    "trusted_source": True,
                })
                if depth < depth_limit and len(fetched_rows) < page_limit:
                    stats["links_discovered"] += len(links)
                    ranked = []
                    for link, label in links:
                        if link in queued or link in visited or not _same_site(link, seed_url):
                            continue
                        score = _link_score(label, link, query_tokens)
                        if score > 0:
                            ranked.append((score, link, label, seed_url, depth + 1))
                    ranked.sort(key=lambda item: (-item[0], item[1]))
                    for _score, link, label, parent_seed, next_depth in ranked[:max(4, (page_limit - len(fetched_rows)) * 2)]:
                        queued.add(link)
                        candidates.append((link, label, parent_seed, next_depth))
    stats["accepted_candidates"] = len(fetched_rows)
    log.info(
        "Site crawl complete: tier=%s seeds=%d requested=%d fetched=%d links=%d returned=%d errors=%d browser_fallbacks=%d/%d depth=%d limit=%d",
        tier, stats["seed_count"], stats["pages_requested"], stats["pages_fetched"], stats["links_discovered"],
        len(fetched_rows), stats["errors"], stats["browser_fallback_successes"], stats["browser_fallback_attempts"], depth_limit, page_limit,
    )
    return fetched_rows[:page_limit], stats
