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


def test_playwright_fetch_runs_sync_driver_in_worker_thread(monkeypatch):
    import asyncio
    from utils import playwright_fetch

    calls = []
    monkeypatch.setattr(playwright_fetch, "_fetch_page_worker", lambda url, max_chars, timeout_ms: calls.append((url, max_chars, timeout_ms)) or "rendered text")
    result = asyncio.run(playwright_fetch.fetch_playwright_page("https://example.com", max_chars=1234))
    assert result == "rendered text"
    assert calls and calls[0][0] == "https://example.com" and calls[0][1] == 1234


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
