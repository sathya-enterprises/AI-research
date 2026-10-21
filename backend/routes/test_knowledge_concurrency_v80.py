from __future__ import annotations

import asyncio
import time

import pytest

# The production dependency is listed in backend requirements. Keep this queue
# regression independently collectable in lightweight environments that omit it.
pytest.importorskip("psycopg2")

from routes import knowledge


def test_ingestion_paused_and_active_runs_keep_event_loop_responsive(monkeypatch) -> None:
    """Queue-level A/B regression: sync persistence must stay in a worker thread.

    This is a controlled concurrency test, not a live-DB reproduction of the
    original production deadlock. The same event-loop heartbeat runs while the
    worker is idle and while a deliberately slow persistence function is active.
    """
    calls = {"count": 0}

    def slow_persist(_query, sources, **_kwargs):
        time.sleep(0.12)
        calls["count"] += 1
        return {"sources": len(sources), "versions": len(sources), "chunks": len(sources) * 2}

    monkeypatch.setattr(knowledge.research_store, "persist_web_search_results", slow_persist)

    async def scenario() -> tuple[int, dict]:
        # Paused-ingestion run: idle worker should not hold the event loop.
        paused_queue = asyncio.Queue()
        paused_worker = asyncio.create_task(knowledge._article_ingest_worker(paused_queue))
        await asyncio.wait_for(asyncio.sleep(0.03), timeout=0.2)
        paused_worker.cancel()
        await asyncio.gather(paused_worker, return_exceptions=True)

        # Active-ingestion run: a blocking synchronous write is offloaded to a
        # thread, allowing an async report/chat heartbeat to continue ticking.
        active_queue = asyncio.Queue()
        crawl_id = "test-active-worker-regression"
        knowledge._INGEST_STATS_BY_CRAWL.pop(crawl_id, None)
        active_worker = asyncio.create_task(knowledge._article_ingest_worker(active_queue))
        active_queue.put_nowait({
            "crawl_id": crawl_id,
            "sources": [{"title": "Sample article", "url": "https://publisher.example/article"}],
            "dedup_key": (crawl_id, "fingerprint"),
        })
        heartbeat = {"ticks": 0}

        async def report_chat_heartbeat() -> None:
            while True:
                await asyncio.sleep(0.01)
                heartbeat["ticks"] += 1

        heartbeat_task = asyncio.create_task(report_chat_heartbeat())
        await asyncio.wait_for(active_queue.join(), timeout=2.0)
        heartbeat_task.cancel()
        await asyncio.gather(heartbeat_task, return_exceptions=True)
        active_worker.cancel()
        await asyncio.gather(active_worker, return_exceptions=True)
        return int(heartbeat["ticks"]), dict(knowledge._INGEST_STATS_BY_CRAWL[crawl_id])

    ticks, state = asyncio.run(scenario())
    assert ticks >= 5
    assert state["batchesProcessed"] == 1
    assert state["batchesFailed"] == 0
    assert state["pendingSources"] == 0
    assert calls["count"] == 1
