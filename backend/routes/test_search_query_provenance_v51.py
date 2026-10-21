"""Each fetched source must retain the actual search angle that found it."""
import asyncio

from routes import chat


def test_multi_search_records_query_provenance_for_each_result(monkeypatch):
    async def fake_search(query, **kwargs):
        slug = "companies" if "companies" in query else "investors"
        return [{
            "title": f"Relevant {slug}",
            "url": f"https://example.test/{slug}",
            "snippet": "Electronic City Bengaluru software investors and companies",
            "searchProvider": "exa",
        }]

    monkeypatch.setattr(chat, "exa_search", fake_search)
    queries = [
        "software companies in Electronic City Bengaluru",
        "investors in Bengaluru software startups",
    ]
    results = asyncio.run(chat.exa_search_multi(queries, max_results=5))
    assert len(results) == 2
    provenance = {row["url"]: row["searchQuery"] for row in results}
    assert provenance["https://example.test/companies"] == queries[0]
    assert provenance["https://example.test/investors"] == queries[1]
