"""Authenticated ingestion and paginated access for the shared article knowledge base.

Scraped news-feed records are persisted into the existing Supabase/Postgres
cross-report web-source library. They remain provenance-bearing source records
and can be retrieved later by ``retrieve_web_research`` during report creation and normal chat.
"""
from __future__ import annotations

import asyncio
import hmac
import hashlib
import json
import logging
import os
import re
import time
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from utils.auth import get_user_id
from utils import research_store

router = APIRouter()
log = logging.getLogger("knowledge")


class ScrapedArticle(BaseModel):
    title: str = Field(min_length=3, max_length=500)
    url: str = Field(min_length=8, max_length=2048)
    source: str = Field(default="", max_length=180)
    published_at: str = Field(default="", max_length=100)
    category: str = Field(default="", max_length=80)
    tag: str = Field(default="", max_length=80)
    summary: str = Field(default="", max_length=4000)
    content: str = Field(default="", max_length=14000)
    full_content_fetched: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class ArticleIngestBatch(BaseModel):
    articles: list[ScrapedArticle] = Field(min_length=1, max_length=100)
    crawl_id: str = Field(default="", max_length=120)


def _internal_ingest_authorized(request: Request) -> bool:
    """Authenticate trusted server-to-server scraper ingestion.

    This credential is accepted only on the ingestion endpoint, is read from
    backend environment at request time, and is never sent to browser code.
    If unconfigured, existing Supabase-user bearer authentication remains the
    only accepted path.
    """
    expected = os.environ.get("ARTICLE_KB_INGEST_TOKEN", "").strip()
    supplied = request.headers.get("x-article-kb-token", "").strip()
    return bool(expected and supplied and hmac.compare_digest(expected, supplied))




# Scraper writes use a bounded in-process queue. psycopg2 is synchronous, so doing
# the write directly inside an async FastAPI endpoint blocks the whole event loop;
# under a crawl this previously starved /api/chat until Next.js reported 502.
# One worker serializes this relatively write-heavy ingestion path, while to_thread
# keeps request handling, chat streaming, status endpoints, and retrieval responsive.
_INGEST_QUEUE: asyncio.Queue[dict[str, Any]] | None = None
_INGEST_WORKER: asyncio.Task | None = None  # compatibility handle for the primary worker
_INGEST_WORKERS: list[asyncio.Task] = []
_INGEST_WORKER_COUNT = 2
_INGEST_STATS_BY_CRAWL: dict[str, dict[str, Any]] = {}
# Idempotency state for retries when a caller times out after the server accepted
# an ingestion batch. These definitions must remain alongside the queue globals;
# the endpoint/helper and worker reference all three names.
_INGEST_BATCH_DEDUP: dict[tuple[str, str], float] = {}
_INGEST_BATCH_DEDUP_TTL_SECONDS = 3600
_INGEST_BATCH_DEDUP_MAX = 50000
_INGEST_QUEUE_MAX = 2000
_INGEST_STATS_MAX_CRAWLS = 300


def _ingest_stats(crawl_id: str) -> dict[str, Any]:
    key = (crawl_id or "unknown")[:120]
    state = _INGEST_STATS_BY_CRAWL.get(key)
    if state is None:
        state = {
            "crawlId": key, "status": "queued", "received": 0, "accepted": 0,
            "storedSources": 0, "storedVersions": 0, "storedChunks": 0,
            "batchesQueued": 0, "batchesProcessed": 0, "batchesFailed": 0,
            "activeBatches": 0, "pendingSources": 0, "retryCount": 0,
            "lastError": None, "lastQueuedAt": None, "lastPersistedAt": None,
            "updatedAt": time.time(),
        }
        _INGEST_STATS_BY_CRAWL[key] = state
        while len(_INGEST_STATS_BY_CRAWL) > _INGEST_STATS_MAX_CRAWLS:
            oldest_key = next(iter(_INGEST_STATS_BY_CRAWL))
            if oldest_key == key and len(_INGEST_STATS_BY_CRAWL) == 1:
                break
            _INGEST_STATS_BY_CRAWL.pop(oldest_key, None)
    return state


def _refresh_ingest_status(state: dict[str, Any]) -> None:
    state["updatedAt"] = time.time()
    if state.get("activeBatches", 0) or state.get("batchesQueued", 0):
        state["status"] = "syncing"
    elif state.get("batchesFailed", 0):
        state["status"] = "partial" if state.get("batchesProcessed", 0) else "failed"
    elif state.get("batchesProcessed", 0):
        state["status"] = "stored"
    else:
        state["status"] = "queued"


def _is_transient_ingest_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(token in message for token in (
        "getaddrinfo", "could not translate host name", "connection timed out",
        "connection refused", "server closed the connection", "timeout expired",
        "temporarily unavailable", "connection reset by peer",
    ))


async def _article_ingest_worker(queue: asyncio.Queue[dict[str, Any]]) -> None:
    while True:
        job = await queue.get()
        crawl_id = str(job.get("crawl_id") or "unknown")[:120]
        state = _ingest_stats(crawl_id)
        state["batchesQueued"] = max(0, int(state.get("batchesQueued", 0)) - 1)
        state["activeBatches"] = int(state.get("activeBatches", 0)) + 1
        _refresh_ingest_status(state)
        try:
            stats = None
            for attempt in range(3):
                try:
                    stats = await asyncio.to_thread(
                        research_store.persist_web_search_results,
                        "Growth Gradual market news article knowledge-base ingestion",
                        job["sources"],
                        provider="market_news_scraper",
                        metadata={"ingestion_kind": "scraped_article_feed", "crawl_id": crawl_id},
                    )
                    break
                except Exception as exc:
                    if attempt < 2 and _is_transient_ingest_error(exc):
                        state["retryCount"] = int(state.get("retryCount", 0)) + 1
                        wait_seconds = (1, 3)[attempt]
                        state["lastError"] = "transient_database_connectivity"
                        log.warning("Article KB transient DB/connectivity error for crawl=%s; retry %d/2 after %ds (%s)", crawl_id[:8], attempt + 1, wait_seconds, type(exc).__name__)
                        await asyncio.sleep(wait_seconds)
                        continue
                    raise
            if stats is None:
                raise RuntimeError("article knowledge persistence returned no result")
            state["storedSources"] += int(stats.get("sources", 0))
            state["storedVersions"] += int(stats.get("versions", 0))
            state["storedChunks"] += int(stats.get("chunks", 0))
            state["batchesProcessed"] += 1
            state["pendingSources"] = max(0, int(state.get("pendingSources", 0)) - len(job.get("sources") or []))
            state["lastError"] = None
            state["lastPersistedAt"] = time.time()
            log.info(
                "Article KB batch persisted: crawl=%s sources=%d versions=%d chunks=%d pending_sources=%d queue_depth=%d",
                crawl_id[:8], int(stats.get("sources", 0)), int(stats.get("versions", 0)),
                int(stats.get("chunks", 0)), int(state.get("pendingSources", 0)), queue.qsize(),
            )
        except Exception as exc:
            state["batchesFailed"] += 1
            state["pendingSources"] = max(0, int(state.get("pendingSources", 0)) - len(job.get("sources") or []))
            dedup_key = job.get("dedup_key")
            if isinstance(dedup_key, tuple):
                _INGEST_BATCH_DEDUP.pop(dedup_key, None)
            msg = str(exc).lower()
            if "getaddrinfo" in msg or "could not translate host name" in msg or "name or service not known" in msg:
                state["lastError"] = "database_host_dns_failure"
                log.error("Article KB batch failed for crawl=%s: Supabase/Postgres DNS resolution failed after retries; verify backend database hostname/DNS configuration", crawl_id[:8])
            else:
                state["lastError"] = type(exc).__name__
                log.warning("Article KB queued batch failed for crawl=%s (%s)", crawl_id[:8], type(exc).__name__)
        finally:
            state["activeBatches"] = max(0, int(state.get("activeBatches", 0)) - 1)
            _refresh_ingest_status(state)
            queue.task_done()


def _ensure_ingest_worker() -> asyncio.Queue[dict[str, Any]]:
    global _INGEST_QUEUE, _INGEST_WORKER, _INGEST_WORKERS
    if _INGEST_QUEUE is None:
        _INGEST_QUEUE = asyncio.Queue(maxsize=_INGEST_QUEUE_MAX)
    _INGEST_WORKERS = [task for task in _INGEST_WORKERS if not task.done()]
    while len(_INGEST_WORKERS) < _INGEST_WORKER_COUNT:
        worker_no = len(_INGEST_WORKERS) + 1
        task = asyncio.create_task(_article_ingest_worker(_INGEST_QUEUE), name=f"article-kb-ingest-worker-{worker_no}")
        _INGEST_WORKERS.append(task)
    _INGEST_WORKER = _INGEST_WORKERS[0] if _INGEST_WORKERS else None
    return _INGEST_QUEUE


async def _authorize_article_kb_request(request: Request) -> None:
    if _internal_ingest_authorized(request):
        return
    user_id = await get_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="A valid Growth Gradual session or server ingestion credential is required.")

def _canonical_web_url(value: str) -> str | None:
    try:
        parsed = urlsplit((value or "").strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return None
        if parsed.username or parsed.password:
            return None
        if parsed.hostname.lower() in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}:
            return None
        return value.strip().split("#", 1)[0][:2048]
    except Exception:
        return None


def build_article_source(article: ScrapedArticle, crawl_id: str = "") -> dict[str, Any] | None:
    """Map scraper output to a provenance-aware web-library source record."""
    url = _canonical_web_url(article.url)
    title = re.sub(r"\s+", " ", article.title or "").strip()
    if not url or len(title) < 3:
        return None
    source = re.sub(r"\s+", " ", article.source or "").strip()[:180]
    summary = re.sub(r"\s+", " ", article.summary or "").strip()[:4000]
    body = re.sub(r"\x00", "", article.content or "").strip()[:14000]
    full_text_verified_as_fetched = bool(article.full_content_fetched and len(body) >= 500)
    content = body if full_text_verified_as_fetched else summary
    metadata = {
        "category": (article.category or "").strip().lower()[:80],
        "tag": (article.tag or "").strip()[:80],
        "crawl_id": crawl_id or "",
        "record_kind": "market_news_article",
        "article_content_state": "publisher_page_fetched" if full_text_verified_as_fetched else "rss_summary_only" if summary else "metadata_only",
        "full_content_fetched": full_text_verified_as_fetched,
    }
    # Only actual article-page text is marked as publisher_page evidence. RSS
    # descriptions remain explicitly weaker snippets rather than being presented
    # as full-page extraction.
    item: dict[str, Any] = {
        "url": url,
        "title": title[:500],
        "publisher": source,
        "published_at": (article.published_at or "").strip()[:100] or None,
        "source_type": "market_news_article",
        "kind": "market_news_article",
        "text": content,
        "snippet": summary,
        "source_role": "news_article",
        "metadata": metadata,
        "evidenceOrigin": "publisher_page" if full_text_verified_as_fetched else "rss_feed_summary" if summary else "metadata_only",
        "pageFetchStatus": "ok" if full_text_verified_as_fetched else "rss_summary_only" if summary else "not_attempted",
        "pageFetchUrl": url,
        "pageFetchFinalUrl": url,
        "pageFetchHttpStatus": 200 if full_text_verified_as_fetched else None,
        "pageFetchContentType": "text/html" if full_text_verified_as_fetched else "application/rss+xml" if summary else "",
    }
    if full_text_verified_as_fetched:
        item["pageContent"] = body
    return item


def _article_batch_fingerprint(crawl_id: str, sources: list[dict[str, Any]]) -> str:
    stable_rows = [
        {
            "url": str(row.get("url") or ""),
            "title": str(row.get("title") or ""),
            "text": str(row.get("text") or row.get("snippet") or ""),
        }
        for row in sources
    ]
    encoded = json.dumps({"crawl_id": crawl_id, "sources": stable_rows}, sort_keys=True, separators=(",", ":")).encode("utf-8", "ignore")
    return hashlib.sha256(encoded).hexdigest()


def _recent_ingest_batch_seen(key: tuple[str, str]) -> bool:
    now = time.time()
    for old_key, stamped in list(_INGEST_BATCH_DEDUP.items()):
        if now - stamped > _INGEST_BATCH_DEDUP_TTL_SECONDS:
            _INGEST_BATCH_DEDUP.pop(old_key, None)
    stamped = _INGEST_BATCH_DEDUP.get(key)
    return bool(stamped and now - stamped <= _INGEST_BATCH_DEDUP_TTL_SECONDS)


@router.post("/articles/ingest")
async def ingest_articles(payload: ArticleIngestBatch, request: Request):
    """Queue scraped article batches for non-blocking persistence into Supabase."""
    await _authorize_article_kb_request(request)

    crawl_id = (payload.crawl_id or "unknown")[:120]
    mapped = [build_article_source(a, crawl_id) for a in payload.articles]
    sources = [item for item in mapped if item]
    if not sources:
        return {"ok": True, "status": "skipped", "queued": False, "received": len(payload.articles),
                "accepted": 0, "stored_sources": 0, "stored_versions": 0, "stored_chunks": 0,
                "skipped": len(payload.articles), "reason": "no_valid_article_urls"}

    queue = _ensure_ingest_worker()
    state = _ingest_stats(crawl_id)
    dedup_key = (crawl_id, _article_batch_fingerprint(crawl_id, sources))
    if _recent_ingest_batch_seen(dedup_key):
        # The previous request may have been accepted even if the caller timed out
        # before receiving its 202. Return queued without enqueueing the same batch twice.
        return JSONResponse(status_code=202, content={
            "ok": True, "status": "queued", "queued": True, "deduplicated": True,
            "received": len(payload.articles), "accepted": len(sources),
            "stored_sources": 0, "stored_versions": 0, "stored_chunks": 0,
            "queue_position": queue.qsize(), "crawl_id": crawl_id,
            "pending_sources": int(state.get("pendingSources", 0)), "durable": False,
        })
    try:
        queue.put_nowait({"crawl_id": crawl_id, "sources": sources, "dedup_key": dedup_key})
    except asyncio.QueueFull:
        state["batchesFailed"] += 1
        state["lastError"] = "ingestion_queue_full"
        _refresh_ingest_status(state)
        raise HTTPException(status_code=503, detail="The article ingestion queue is temporarily full. The crawl remains available in the feed cache; retry later.") from None

    _INGEST_BATCH_DEDUP[dedup_key] = time.time()
    while len(_INGEST_BATCH_DEDUP) > _INGEST_BATCH_DEDUP_MAX:
        oldest_key = min(_INGEST_BATCH_DEDUP, key=_INGEST_BATCH_DEDUP.get)
        _INGEST_BATCH_DEDUP.pop(oldest_key, None)
    state["received"] += len(payload.articles)
    state["accepted"] += len(sources)
    state["pendingSources"] = int(state.get("pendingSources", 0)) + len(sources)
    state["batchesQueued"] += 1
    state["lastQueuedAt"] = time.time()
    _refresh_ingest_status(state)
    # 202 means this batch was accepted into the bounded worker queue, not that
    # the durable database write has finished. The status endpoint reports completion.
    return JSONResponse(status_code=202, content={
        "ok": True, "status": "queued", "queued": True,
        "received": len(payload.articles), "accepted": len(sources),
        "stored_sources": 0, "stored_versions": 0, "stored_chunks": 0,
        "skipped": len(payload.articles) - len(sources),
        "queue_position": queue.qsize(), "crawl_id": crawl_id,
        "pending_sources": int(state.get("pendingSources", 0)), "durable": False,
    })


@router.get("/articles/ingest/status")
async def ingest_articles_status(
    request: Request,
    crawl_id: str = Query(default="", max_length=120),
):
    """Return aggregate progress for one crawl's article-ingestion queue."""
    await _authorize_article_kb_request(request)
    key = (crawl_id or "unknown")[:120]
    state = _INGEST_STATS_BY_CRAWL.get(key)
    if state is None:
        return {"status": "not_found", "crawlId": key, "received": 0, "accepted": 0,
                "storedSources": 0, "storedVersions": 0, "storedChunks": 0,
                "batchesQueued": 0, "batchesProcessed": 0, "batchesFailed": 0}
    # Copy primitive status and expose the distinction between accepted and durable data.
    payload = dict(state)
    payload["queueDepth"] = _INGEST_QUEUE.qsize() if _INGEST_QUEUE is not None else 0
    payload["workerAlive"] = any(not task.done() for task in _INGEST_WORKERS)
    payload["durable"] = bool(
        payload.get("status") == "stored" and int(payload.get("pendingSources", 0)) == 0
        and int(payload.get("batchesFailed", 0)) == 0
    )
    payload["updatedAgeSeconds"] = max(0, int(time.time() - float(payload.get("updatedAt") or time.time())))
    return payload


@router.get("/articles")
async def search_articles(
    request: Request,
    q: str = Query(default="", max_length=300),
    page: int = Query(default=1, ge=1, le=100000),
    page_size: int = Query(default=20, ge=1, le=100),
    category: str | None = Query(default=None, max_length=80),
):
    """Paginated article-KB browser/search endpoint for QA and future UI reuse."""
    user_id = await get_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="A valid Growth Gradual session is required to browse the article knowledge base.")
    try:
        return await asyncio.to_thread(research_store.search_market_news_articles, q, page=page, page_size=page_size, category=category)
    except Exception as exc:
        log.warning("Article KB query failed (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="The Supabase article knowledge base is unavailable.") from None
