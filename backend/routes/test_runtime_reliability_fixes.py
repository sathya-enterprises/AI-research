from routes.report import _extract_company_candidates


def test_broad_sector_rotation_prompt_does_not_resolve_grammar_word_as_company():
    q = "How can you explain and analyze the sector rotation in Indian stock markets over the past 8 quarters?"
    assert _extract_company_candidates(q) == []


def test_promoter_stake_sale_prompt_does_not_resolve_sentence_words_as_companies():
    q = (
        "Explain the proportion and reasons for stake sales by Indian business promoters. "
        "Support it with substantial data which is easy to understand by UHNIs. "
        "Will appreciate an exhaustive list of all promoter exits in past 8 quarters."
    )
    assert _extract_company_candidates(q) == []


def test_fta_prompt_does_not_resolve_generic_acronym_as_company():
    q = (
        "List down the why, what, how of all the FTAs signed by India in the past 1 year. "
        "Compare how these FTAs have been effective internationally."
    )
    assert _extract_company_candidates(q) == []


def test_explicit_company_comparison_still_resolves_companies():
    q = "Compare SBI, HDFC Bank, ICICI Bank, and Axis Bank over the last 5 years"
    assert _extract_company_candidates(q) == ["SBI", "HDFC Bank", "ICICI Bank", "Axis Bank"]


def test_named_companies_with_corporate_suffix_still_resolve():
    q = "Analyze Reliance Industries and Tata Motors performance"
    assert _extract_company_candidates(q) == ["Reliance Industries", "Tata Motors"]


def test_playwright_fetch_uses_the_async_driver_without_blocking(monkeypatch):
    import asyncio
    import sys
    import types
    from utils import playwright_fetch

    calls = []

    class Page:
        async def goto(self, url, **kwargs):
            calls.append(("goto", url, kwargs.get("wait_until")))

        async def wait_for_timeout(self, _milliseconds):
            return None

        def locator(self, _selector):
            return self

        async def inner_text(self, **_kwargs):
            return "rendered text"

    class Browser:
        async def new_page(self, **_kwargs):
            return Page()

        async def close(self):
            return None

    class Chromium:
        async def launch(self, **_kwargs):
            return Browser()

    class PlaywrightContext:
        chromium = Chromium()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    package = types.ModuleType("playwright")
    api = types.ModuleType("playwright.async_api")
    api.async_playwright = PlaywrightContext
    monkeypatch.setitem(sys.modules, "playwright", package)
    monkeypatch.setitem(sys.modules, "playwright.async_api", api)
    result = asyncio.run(playwright_fetch.fetch_playwright_page("https://example.com", max_chars=1234))
    assert result == "rendered text"
    assert calls == [("goto", "https://example.com", "domcontentloaded")]


def test_research_store_retries_deadlocks(monkeypatch):
    import pytest
    psycopg2 = pytest.importorskip("psycopg2")
    from utils import research_store

    attempts = {"n": 0}
    def flaky(*args, **kwargs):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise psycopg2.errors.DeadlockDetected("deadlock detected")
        return {"sources": 1, "versions": 1, "chunks": 2, "events": 1}

    monkeypatch.setattr(research_store, "_persist_web_search_results_once", flaky)
    monkeypatch.setattr(research_store.time, "sleep", lambda _seconds: None)
    monkeypatch.setenv("RESEARCH_STORE_WRITE_RETRIES", "4")
    result = research_store.persist_web_search_results("query", [{"url": "https://example.com"}])
    assert attempts["n"] == 3
    assert result["events"] == 1
