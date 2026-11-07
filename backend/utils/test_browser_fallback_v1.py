"""Stage A: bounded browser fallback for pages the HTTP fetcher could not read."""
import asyncio

from utils import browser_fallback as BF

PAGE = """<html><head><title>FPI sectoral activity</title>
<meta property="article:published_time" content="2026-10-02T09:00:00Z"></head><body><main>
<h1>FPI sectoral activity — September 2026</h1>
<p>Foreign portfolio investors sold financial services stocks worth 11,704 crore in the second half of September while buying capital goods.</p>
<p>Domestic institutions absorbed most of the selling through mutual fund SIP inflows, which reached a record during the quarter.</p>
</main></body></html>"""


def _rows():
    return [
        {"url": "https://www.moneycontrol.com/markets/fii-dii-data/fpi-sectoral-activity/", "pageFetchStatus": "empty_extraction", "snippet": "FPI"},
        {"url": "https://example.com/report.pdf", "pageFetchStatus": "empty_extraction"},           # PDFs are never rendered
        {"url": "https://example.com/missing", "pageFetchStatus": "http_404"},                      # not a retry status
        {"url": "https://www.nseindia.com/market-data/sectoral", "pageFetchStatus": "http_403"},    # official host goes first
        {"url": "https://example.com/ok", "pageFetchStatus": "ok", "pageContent": "x"},
    ]


def test_candidates_skip_pdfs_404s_and_ok_rows_and_put_official_hosts_first():
    rows = _rows()
    assert BF.candidates(rows, 10) == [3, 0]
    assert BF.candidates(rows, 1) == [3]


def test_page_limit_by_tier_and_env(monkeypatch):
    assert BF.page_limit("detailed") == 6 and BF.page_limit("in_depth") == 12
    monkeypatch.setenv("GG_BROWSER_FALLBACK_LIMIT", "2,4")
    assert BF.page_limit("detailed") == 2 and BF.page_limit("in_depth") == 4


def test_apply_recovers_relevant_pages_and_rejects_irrelevant_or_thin_ones():
    rows = _rows()

    async def fake_renderer(urls, timeout_seconds=15.0):
        return {
            urls[0]: (urls[0], "<html><body><main><p>Login to continue.</p></main></body></html>"),  # nseindia: too thin
            urls[1]: (urls[1] + "?r=1", PAGE),                                                   # moneycontrol: real page
        }

    stats = asyncio.run(BF.apply_browser_fallback(
        rows, report_tier="detailed", max_chars=15000,
        matches_source=lambda text, row: "foreign portfolio investors" in text.casefold(),
        renderer=fake_renderer,
    ))
    assert stats["attempted"] == 2 and stats["recovered"] == 1 and stats["failed"] == 1
    recovered = rows[0]
    assert recovered["pageFetchStatus"] == "ok" and recovered["pageFetchMethod"] == "browser"
    assert "11,704 crore" in recovered["pageContent"]
    assert recovered["publication_date"].startswith("2026-10-02")
    assert rows[3]["pageFetchStatus"] == "http_403" and rows[3]["pageFetchBrowserStatus"] == "too_little_text"


def test_relevance_check_still_applies():
    rows = [{"url": "https://example.com/a", "pageFetchStatus": "empty_extraction"}]

    async def fake_renderer(urls, timeout_seconds=15.0):
        return {urls[0]: (urls[0], PAGE)}

    stats = asyncio.run(BF.apply_browser_fallback(
        rows, report_tier="in_depth", max_chars=15000, matches_source=lambda text, row: False, renderer=fake_renderer,
    ))
    assert stats["rejected_by_relevance"] == 1
    assert rows[0]["pageFetchStatus"] == "empty_extraction" and not rows[0].get("pageContent")


def test_disabled_by_env(monkeypatch):
    monkeypatch.setenv("ENABLE_PLAYWRIGHT_FETCH", "0")
    stats = asyncio.run(BF.apply_browser_fallback(_rows(), report_tier="detailed", max_chars=1000, matches_source=lambda t, r: True))
    assert stats["attempted"] == 0
