"""Regression tests for article persistence into Growth Gradual's shared knowledge base."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from starlette.requests import Request

from routes.knowledge import ScrapedArticle, build_article_source, _internal_ingest_authorized
from routes import knowledge
from utils import research_store


def make_article(**overrides):
    payload = {
        "title": "RBI announces a new monetary policy decision",
        "url": "https://example.com/markets/rbi-monetary-policy-decision-2026",
        "source": "Example Financial News",
        "published_at": "2026-10-16T06:30:00Z",
        "category": "finance",
        "tag": "Monetary Policy",
        "summary": "The Reserve Bank of India announced its latest monetary policy decision.",
        "content": "",
        "full_content_fetched": False,
        "metadata": {},
    }
    payload.update(overrides)
    return ScrapedArticle(**payload)


def test_rss_summary_is_persistable_but_never_mislabeled_as_full_page():
    source = build_article_source(make_article(), "crawl-123")
    assert source is not None
    assert source["source_type"] == "market_news_article"
    assert source["text"].startswith("The Reserve Bank")
    assert "pageContent" not in source
    assert source["pageFetchStatus"] == "rss_summary_only"
    assert source["evidenceOrigin"] == "rss_feed_summary"
    assert source["metadata"]["article_content_state"] == "rss_summary_only"
    assert source["metadata"]["crawl_id"] == "crawl-123"


def test_only_explicit_successful_long_article_capture_is_full_page_evidence():
    body = ("The central bank announced its monetary policy decision and described the reasons. " * 12).strip()
    source = build_article_source(make_article(content=body, full_content_fetched=True), "crawl-456")
    assert source is not None
    assert source["pageContent"] == body
    assert source["text"] == body
    assert source["pageFetchStatus"] == "ok"
    assert source["evidenceOrigin"] == "publisher_page"
    assert source["pageFetchHttpStatus"] == 200
    assert source["metadata"]["article_content_state"] == "publisher_page_fetched"


def test_long_text_without_success_flag_does_not_claim_full_page_extraction():
    body = "Long-looking but unverified body text. " * 30
    source = build_article_source(make_article(content=body, full_content_fetched=False), "crawl-789")
    assert source is not None
    assert "pageContent" not in source
    assert source["pageFetchStatus"] == "rss_summary_only"
    assert source["evidenceOrigin"] == "rss_feed_summary"
    assert source["text"] == source["snippet"]


@pytest.mark.parametrize("bad_url", [
    "javascript:alert(1)",
    "file:///C:/private.txt",
    "http://localhost/admin",
    "http://127.0.0.1/internal",
    "http://user:password@example.com/article",
    "not a URL",
])
def test_ingestion_rejects_unsafe_or_invalid_urls(bad_url):
    assert build_article_source(make_article(url=bad_url), "crawl-unsafe") is None


def test_article_fields_and_provenance_survive_mapping():
    source = build_article_source(make_article(
        title="  RBI   monetary   policy  ",
        source="Publisher",
        category="Banks",
        tag="Rates",
        summary="  Short RSS excerpt.  ",
    ), "crawl-provenance")
    assert source is not None
    assert source["title"] == "RBI monetary policy"
    assert source["publisher"] == "Publisher"
    assert source["metadata"]["category"] == "banks"
    assert source["metadata"]["tag"] == "Rates"
    assert source["metadata"]["crawl_id"] == "crawl-provenance"
    assert source["metadata"]["record_kind"] == "market_news_article"


def test_report_retrieval_prefers_full_article_and_discloses_rss_only_evidence(monkeypatch):
    monkeypatch.setattr(research_store, "initialise", lambda: None)
    queries = []

    class FakeCursor:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def execute(self, sql, params): queries.append((sql, params))
        def fetchone(self): return {"total": 21}
        def fetchall(self):
            return [{
                "source_key": "key-1", "canonical_url": "https://example.com/a",
                "title": "RBI policy article", "publisher": "Publisher", "domain": "example.com",
                "published_at": "2026-10-16", "first_seen_at": "2026-10-16T00:00:00+00:00",
                "last_seen_at": "2026-10-16T00:00:00+00:00", "metadata_json": {"category": "finance"},
                "content_text": "RSS excerpt text", "retrieved_at": "2026-10-16T00:00:00+00:00",
                "source_version_id": "version-1", "version_metadata_json": {"evidenceOrigin": "rss_feed_summary"},
            }]

    class FakeConnection:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def cursor(self): return FakeCursor()

    monkeypatch.setattr(research_store, "_connect", lambda: FakeConnection())
    result = research_store.search_market_news_articles("RBI policy", page=2, page_size=10, category="finance")
    assert result["total"] == 21
    assert result["page"] == 2
    assert result["page_size"] == 10
    assert result["has_previous"] is True
    assert result["has_next"] is True
    assert "OFFSET %s" in queries[-1][0]
    assert queries[-1][1][-1] == 10
    assert queries[-1][1][-2] == 10

    dossier = research_store.format_stored_web_research([{
        "title": "RBI policy article", "url": "https://example.com/a", "publisher": "Publisher",
        "retrieved_at": "2026-10-16", "fullContent": "RSS excerpt text",
        "evidenceOrigin": "rss_feed_summary", "pageFetchStatus": "rss_summary_only",
        "source_type": "market_news_article",
    }])
    assert "RSS feed summary only" in dossier
    assert "not as proof for detailed or quantitative claims" in dossier


def test_scraper_has_rss_atom_pagination_archive_and_incremental_persistence_wiring():
    route = PROJECT_ROOT / "frontend" / "src" / "app" / "api" / "scrape" / "route.ts"
    text = route.read_text(encoding="utf-8")
    assert "extractFeedItemBlocks(xml, MAX_ITEMS_PER_FEED)" in text
    assert "extractNextFeedUrl" in text
    assert "googleNewsDateWindowUrls" in text
    assert "knowledgeSummary" in text
    assert "previousKnowledgeLength" in text
    assert "pagesFetched >= MAX_PAGINATED_FEED_PAGES" in text
    assert ".slice(0, 4000)" in text
    assert "const summary = knowledgeSummary.slice(0, 320)" in text
    assert "xt(block, 'content:encoded'), xt(block, 'content')" in text
    assert ".sort((a, b) => b.length - a.length)[0]" in text
    assert "syncScrapedArticlesToKnowledgeBase" in text
    assert "activeScrapeTask" in text
    assert "result.firstPage.length > 0 || result.allPages.length > 0" in text
    assert "return await scrapeResponse(cache, { scraping: true" in text


def test_article_reader_persists_full_extraction_and_feed_page_forwards_session():
    article_route = (PROJECT_ROOT / "frontend" / "src" / "app" / "api" / "article" / "route.ts").read_text(encoding="utf-8")
    feed_page = (PROJECT_ROOT / "frontend" / "src" / "components" / "FeedPage.tsx").read_text(encoding="utf-8")
    assert "content.fullContentFetched && bodyText.length >= 500" in article_route
    assert "syncScrapedArticlesToKnowledgeBase" in article_route
    assert "supabase.auth.getSession()" in feed_page
    assert "Authorization: `Bearer ${data.session.access_token}`" in feed_page


def test_shared_research_store_preserves_article_library_classification():
    store_source = (BACKEND_ROOT / "utils" / "research_store.py").read_text(encoding="utf-8")
    assert "source_type='market_news_article' OR %s='market_news_article'" in store_source
    assert "THEN 'market_news_article'" in store_source


def test_scraper_uses_server_side_ingest_token_without_browser_session():
    sync = (PROJECT_ROOT / "frontend" / "src" / "lib" / "server" / "article-knowledge-sync.ts").read_text(encoding="utf-8")
    route = (PROJECT_ROOT / "frontend" / "src" / "app" / "api" / "scrape" / "route.ts").read_text(encoding="utf-8")
    assert "ARTICLE_KB_INGEST_TOKEN" in sync
    assert "X-Article-KB-Token" in sync
    assert "canSyncScrapedArticlesToKnowledgeBase(accessToken)" in route
    assert "if (!canSyncScrapedArticlesToKnowledgeBase(token)) return cache;" in route


def test_ingestion_endpoint_compares_internal_token_but_browsing_stays_user_authenticated():
    source = (BACKEND_ROOT / "routes" / "knowledge.py").read_text(encoding="utf-8")
    assert "hmac.compare_digest(expected, supplied)" in source
    assert "if _internal_ingest_authorized(request):" in source
    assert "if not user_id:" in source
    assert "async def search_articles(" in source
    # The article listing endpoint continues to require a valid app session.
    assert "A valid Growth Gradual session is required to browse the article knowledge base." in source


def _fake_request(headers: dict[str, str]) -> Request:
    return Request({
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "POST", "scheme": "http", "path": "/api/knowledge/articles/ingest",
        "raw_path": b"/api/knowledge/articles/ingest", "query_string": b"",
        "headers": [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in headers.items()],
        "client": ("127.0.0.1", 1234), "server": ("testserver", 80),
    })


def test_internal_ingest_token_authorization(monkeypatch):
    monkeypatch.setenv("ARTICLE_KB_INGEST_TOKEN", "a-test-only-random-token")
    assert _internal_ingest_authorized(_fake_request({"X-Article-KB-Token": "a-test-only-random-token"}))
    assert not _internal_ingest_authorized(_fake_request({"X-Article-KB-Token": "wrong-token"}))
    assert not _internal_ingest_authorized(_fake_request({}))
    monkeypatch.delenv("ARTICLE_KB_INGEST_TOKEN", raising=False)
    assert not _internal_ingest_authorized(_fake_request({"X-Article-KB-Token": "a-test-only-random-token"}))


def test_stored_crawl_content_is_available_to_regular_chat_retrieval():
    tools_source = (BACKEND_ROOT / "utils" / "tools.py").read_text(encoding="utf-8")
    orchestrator_source = (BACKEND_ROOT / "utils" / "orchestrator.py").read_text(encoding="utf-8")
    store_source = (BACKEND_ROOT / "utils" / "research_store.py").read_text(encoding="utf-8")
    assert 'name="stored_web_research"' in tools_source
    assert "retrieve_web_research" in tools_source
    assert "stored_web_research" in orchestrator_source
    assert "_should_search_stored_web_research" in orchestrator_source
    assert "web_source_library" in store_source
    assert "web_source_versions" in store_source


def test_report_reads_private_okf_bucket_then_legacy_fallback():
    report_source = (BACKEND_ROOT / "routes" / "report.py").read_text(encoding="utf-8")
    assert '"rag-knowledge-private"' in report_source
    assert '"paperly-uploads"' in report_source
    assert "SUPABASE_SECRET_KEY" in report_source
    assert "SUPABASE_SERVICE_ROLE_KEY" in report_source
    assert "bucket_credentials" in report_source


def test_article_ingestion_is_queued_and_database_work_runs_off_event_loop():
    source = (BACKEND_ROOT / "routes" / "knowledge.py").read_text(encoding="utf-8")
    assert "asyncio.Queue(maxsize=_INGEST_QUEUE_MAX)" in source
    assert "status_code=202" in source
    assert '"status": "queued", "queued": True' in source
    assert "await asyncio.to_thread(" in source
    assert "research_store.persist_web_search_results" in source
    assert '@router.get("/articles/ingest/status")' in source


def test_ai_sdk_adapter_streams_status_immediately_and_forwards_to_fastapi_directly():
    adapter = (PROJECT_ROOT / "frontend" / "src" / "app" / "api" / "chat" / "ai-sdk" / "route.ts").read_text(encoding="utf-8")
    assert "data-chat-status" in adapter
    assert "Connecting to the research engine" in adapter
    assert "fetch(`${backend}/api/chat`" in adapter
    assert "fetch(new URL('/api/chat'" not in adapter
    end_block = adapter.split("const end = (reason = 'stop') => {", 1)[1].split("};", 1)[0]
    assert end_block.index("emit({ type: 'text-end'") < end_block.index("ended = true;")
    assert "data: [DONE]\\n\\n" in adapter


def test_scraper_sync_treats_202_as_queued_not_as_durable_storage_success():
    sync = (PROJECT_ROOT / "frontend" / "src" / "lib" / "server" / "article-knowledge-sync.ts").read_text(encoding="utf-8")
    assert "data.queued === true || data.status === 'queued'" in sync
    assert "recentQueuedBatches.set(fingerprint" in sync
    assert "batchesQueued" in sync


def test_ingest_batch_dedup_state_is_defined_and_expires(monkeypatch):
    # Regression for a production NameError that made every article-ingest POST 500.
    knowledge._INGEST_BATCH_DEDUP.clear()
    monkeypatch.setattr(knowledge.time, "time", lambda: 10_000.0)
    key = ("crawl-regression", "fingerprint-regression")

    assert knowledge._recent_ingest_batch_seen(key) is False
    knowledge._INGEST_BATCH_DEDUP[key] = 9_999.0
    assert knowledge._recent_ingest_batch_seen(key) is True

    expired_key = ("old-crawl", "old-fingerprint")
    knowledge._INGEST_BATCH_DEDUP[expired_key] = (
        10_000.0 - knowledge._INGEST_BATCH_DEDUP_TTL_SECONDS - 1
    )
    assert knowledge._recent_ingest_batch_seen(expired_key) is False
    assert expired_key not in knowledge._INGEST_BATCH_DEDUP
    assert knowledge._INGEST_BATCH_DEDUP_MAX > 0
