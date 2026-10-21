"""Regression checks for per-query provider pagination and adaptive search merging."""
import asyncio

from routes import chat


def test_exa_multisearch_runs_each_distinct_query_once(monkeypatch):
    calls = []

    async def fake_search(query, **kwargs):
        calls.append(query)
        slug = query.replace(" ", "-")
        return [{"url": f"https://{slug}.example/p1", "title": query}]

    monkeypatch.setattr(chat, "exa_search", fake_search)
    results = asyncio.run(chat.exa_search_multi(
        ["trai subscribers", "telecom arpu"], max_results=10,
    ))
    assert sorted(calls) == ["telecom arpu", "trai subscribers"]
    assert len(results) == 2
    assert {item["title"] for item in results} == {"trai subscribers", "telecom arpu"}


def test_multi_query_merges_results_round_robin_and_deduplicates_urls(monkeypatch):
    by_query = {
        "official statistics": [
            {"url": "https://example.com/a", "title": "A"},
            {"url": "https://example.com/b", "title": "B"},
        ],
        "industry outlook": [
            {"url": "https://example.com/a", "title": "A duplicate"},
            {"url": "https://example.org/c", "title": "C"},
        ],
    }

    async def fake_search(query, **kwargs):
        return by_query[query]

    monkeypatch.setattr(chat, "exa_search", fake_search)
    merged = asyncio.run(chat.exa_search_multi(["official statistics", "industry outlook"]))
    assert [item["title"] for item in merged] == ["A", "B", "C"]
    assert len({item["url"] for item in merged}) == len(merged)
