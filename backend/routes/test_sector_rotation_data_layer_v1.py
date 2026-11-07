"""Data-layer regressions found in the 6 Nov 2026 sector-rotation run.

* Sector universe lacked NIFTY IT/BANK, Yahoo 404s were not aliased and the
  official Nifty Indices fallback got 302 on every call (no session cookie).
* The generic relevance gate discarded NSDL/NSE/SEBI/ownership pages that carry
  the numbers because they never say "sector rotation".
* The site crawler followed contact/complaints/podcast/portfolio pages.
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import date
from pathlib import Path

import httpx

sys.path.insert(0, Path(__file__).resolve().parents[1].as_posix())

from routes import report
from utils import market_data
from utils import site_crawler

QUESTION = "How can you explain and analyse the sector rotation in Indian stock markets in the past 8 quarters?"


def _src(url, title, body="", **extra):
    row = {"title": title, "url": url, "snippet": body[:300], "fullContent": body, "pageContent": body}
    row.update(extra)
    return row


# --------------------------------------------------------------- relevance gate
def test_rotation_gate_keeps_official_flow_and_ownership_pages_and_drops_noise():
    sources = [
        _src("https://www.fpi.nsdl.co.in/web/Reports/FPI_Fortnightly_Selection.aspx",
             "NSDL : Fortnightly Sector-wise FPI Investment data",
             "Fortnightly sector-wise FPI investment. Financial Services 1,234 crore net outflow; IT 560 crore."),
        _src("https://www.financialexpress.com/market/sep-fii-holdings-in-nifty-500-plunge-to-16-8",
             "Sep FII holdings in Nifty 500 plunge to 16.8%: Why DIIs are buying",
             "FII holdings fell to 16.8% while DII institutional ownership rose to 20.3% in September. " * 3),
        _src("https://www.nseindia.com/market-data/index-performances", "Live Analysis - Index Performances",
             "Nifty Auto 26,750 Nifty Bank 54,100 Nifty IT 35,200 Nifty Pharma 22,500 sector indices performance " * 2),
        _src("https://privatebank.barclays.com/international-banking/contact-us/", "Contact us",
             "Contact our private bank team about investments and markets."),
        _src("https://downstox.com/portfolio-xray", "Portfolio Stress Test India",
             "Run a stress test on your portfolio and see sector allocation."),
        _src("https://www.rupeecase.com/guides/sectoral-thematic-investing", "Sectoral and Thematic Funds",
             "Compare sectoral funds."),
    ]
    kept = report._filter_relevant_sources(sources, QUESTION, None)
    urls = {row["url"] for row in kept}
    assert "https://www.fpi.nsdl.co.in/web/Reports/FPI_Fortnightly_Selection.aspx" in urls
    assert any("financialexpress.com" in u for u in urls)
    assert any("nseindia.com" in u for u in urls)
    assert not any("contact-us" in u for u in urls)
    assert not any("portfolio-xray" in u for u in urls)


def test_rotation_gate_post_fetch_rejects_thin_unofficial_and_single_dimension_crawled_pages():
    thin_blog = _src("https://example-blog.in/post", "Markets note", "FII selling continued. " * 3)
    crawled_single = _src(
        "https://bank.example.com/insights/outlook", "Outlook",
        "Rates and inflation outlook for the year with detailed commentary. " * 8,
        source_type="website_crawl",
    )
    crawled_double = _src(
        "https://research.example.com/sector-flows", "Sector flows",
        "Sector indices performance Nifty Auto 12.5% while FII net outflows of 4,100 crore hit IT. " * 4,
        source_type="website_crawl",
    )
    official_short = _src(
        "https://www.sebi.gov.in/statistics/fpi-investment/fortnightly-sector-wise.html",
        "SEBI | Fortnightly Sector-wise FPI Investment Data",
        "Sector-wise FPI investment net inflow 1,234 crore " + "x" * 90,
    )
    kept = report._filter_relevant_sources(
        [thin_blog, crawled_single, crawled_double, official_short], QUESTION, None, require_context=True,
    )
    urls = {row["url"] for row in kept}
    assert thin_blog["url"] not in urls
    assert crawled_single["url"] not in urls
    assert crawled_double["url"] in urls
    assert official_short["url"] in urls


def test_non_rotation_questions_still_use_the_generic_gate():
    # A banking-sector question must not be routed through the rotation branch.
    assert not report._is_sector_rotation_report_request("Analyse Indian banking sector credit growth and NPAs")


# ---------------------------------------------------------------------- crawler
def test_crawler_scores_utility_pages_zero_and_keeps_research_pages():
    tokens = site_crawler._relevance_tokens(QUESTION)
    for url in (
        "https://privatebank.barclays.com/international-banking/contact-us/",
        "https://privatebank.barclays.com/insights/markets-weekly-podcast/",
        "https://downstox.com/portfolio-xray",
        "https://downstox.com/case-studies",
        "https://cnbc.zendesk.com/hc/en-us/requests/new",
        "https://www.cnbc.com/my-portfolio/",
    ):
        assert site_crawler._link_score("Sector rotation markets", url, tokens) == 0, url
    assert site_crawler._link_score("Sector rotation India", "https://downstox.com/sector-rotation", tokens) > 0
    assert site_crawler._link_score("FPI sector flows", "https://dalalos.in/fii-dii/fpi-sector-investment", tokens) > 0


def test_crawler_generic_words_no_longer_score_for_non_investor_questions():
    tokens = site_crawler._relevance_tokens(QUESTION)
    # "investment"/"capital"/"company" used to add +3 each regardless of the question.
    assert site_crawler._link_score("Company investment capital", "https://x.com/company-investment-capital", tokens) == 0
    investor_tokens = site_crawler._relevance_tokens("venture capital investors in Bengaluru")
    assert site_crawler._link_score(
        "Our portfolio companies", "https://vc.example.com/portfolio", investor_tokens, investor_discovery=True
    ) > 0


# ---------------------------------------------------------------- market data
def _months(first_year=2020, last_year=2027, base=100.0):
    points = []
    value = base
    for year in range(first_year, last_year + 1):
        for month in range(1, 13):
            value += 1.0
            points.append({"date": date(year, month, 1).strftime("%b %Y"), "close": value})
    return points


def test_quarterly_universe_includes_it_and_bank():
    labels = {label for _symbol, label in market_data.SECTOR_QUARTERLY_SYMBOLS}
    assert {"NIFTY IT", "NIFTY BANK"} <= labels
    # The shared history universe is unchanged so the headline series is not duplicated.
    assert "NIFTY IT" not in {label for _s, label in market_data.SECTOR_INDEX_SYMBOLS}


def test_yahoo_alias_fills_sector_when_primary_ticker_404s(monkeypatch):
    calls = []

    async def fake_history(client, symbol, range_str, interval):
        calls.append(symbol)
        if symbol in {"^CNXFIN", "^CNXIT", "^NSEBANK", "^CNXAUTO"}:
            return _months()
        return None

    async def no_official(client, name, start, end):
        return {}

    monkeypatch.setattr(market_data, "_yf_chart_history", fake_history)
    monkeypatch.setattr(market_data, "_niftyindices_month_end_closes", no_official)
    rows = asyncio.run(market_data.fetch_sector_quarterly_returns(quarters=8, concurrency=4))
    by_label = {row["label"]: row for row in rows}
    assert {"NIFTY IT", "NIFTY BANK", "NIFTY AUTO", "NIFTY FINANCIAL SERVICES"} <= set(by_label)
    fin = by_label["NIFTY FINANCIAL SERVICES"]
    assert "alias ^CNXFIN" in fin["history_source"]
    assert fin["missing_quarters"] == []
    assert "^CNXFINANCE" in calls and "^CNXFIN" in calls
    # A sector with no data anywhere is omitted rather than invented.
    assert "NIFTY MEDIA" not in by_label


def test_nifty_indices_session_is_warmed_once_and_redirects_are_followed():
    seen = {"warm": 0, "posts": []}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith("/reports/historical-data"):
            seen["warm"] += 1
            return httpx.Response(200, text="<html></html>", headers={"set-cookie": "ASP.NET_SessionId=abc; Path=/"})
        if request.method == "POST":
            body = json.loads(request.content)
            seen["posts"].append(body["cinfo"])
            if "ASP.NET_SessionId=abc" not in request.headers.get("cookie", ""):
                return httpx.Response(302, headers={"location": "https://www.niftyindices.com/error"})
            rows = [
                {"HistoricalDate": "27 Dec 2024", "CLOSE": "1000.0"},
                {"HistoricalDate": "31 Dec 2024", "CLOSE": "1010.5"},
                {"HistoricalDate": "28 Mar 2025", "CLOSE": "1100.0"},
            ]
            return httpx.Response(200, json={"d": json.dumps(rows)})
        return httpx.Response(404)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
            first = await market_data._niftyindices_month_end_closes(client, "NIFTY HEALTHCARE", (2024, 12), (2025, 3))
            second = await market_data._niftyindices_month_end_closes(client, "NIFTY MEDIA", (2024, 12), (2025, 3))
            return first, second

    first, second = asyncio.run(run())
    assert seen["warm"] == 1
    assert first[(2024, 12)] == 1010.5 and first[(2025, 3)] == 1100.0
    assert second[(2024, 12)] == 1010.5
    # Official name differs from our label for healthcare.
    assert "NIFTY HEALTHCARE INDEX" in seen["posts"][0]
