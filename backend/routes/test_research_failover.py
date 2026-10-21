import asyncio

import routes.chat as chat
from utils.research_scope import semantic_follow_up_queries, assess_evidence_coverage


def test_semantic_follow_up_expands_any_missing_dimension():
    queries = semantic_follow_up_queries(
        "Assess platform economics", ["order_density", "financing"], limit=8
    )
    assert any("orders per store" in q for q in queries)
    assert any("working capital" not in q and "financing" in q for q in queries) or queries


def test_exa_failure_uses_configured_secondary_provider(monkeypatch):
    async def failed_exa(*args, **kwargs):
        return None

    class FakeSerper:
        def available(self):
            return True

        async def search(self, queries, **kwargs):
            return [{"title": "Primary filing", "url": "https://example.gov/filing",
                     "snippet": "orders per store data", "fullContent": "orders per store data"}]

    monkeypatch.setattr(chat, "get_exa_keys", lambda: ["exa-test-key"])
    monkeypatch.setattr(chat, "is_rate_limited", lambda key: False)
    monkeypatch.setattr(chat, "_exa_one_call", failed_exa)
    monkeypatch.setattr("utils.websearch.SerperProvider", FakeSerper)

    result = asyncio.run(chat.exa_search(
        'quick commerce "orders per store" primary data official results',
        max_results=5, max_keys=1,
    ))
    assert result and result[0]["url"] == "https://example.gov/filing"
    assert result[0]["searchProvider"] == "serper"


def test_all_providers_empty_does_not_create_evidence(monkeypatch):
    monkeypatch.setattr(chat, "get_exa_keys", lambda: ["exa-test-key"])
    monkeypatch.setattr(chat, "is_rate_limited", lambda key: False)

    async def failed_exa(*args, **kwargs):
        return None

    class EmptyProvider:
        def available(self):
            return True
        async def search(self, *args, **kwargs):
            return []

    monkeypatch.setattr(chat, "_exa_one_call", failed_exa)
    monkeypatch.setattr("utils.websearch.SerperProvider", EmptyProvider)
    monkeypatch.setattr("utils.websearch.SerpAPIProvider", EmptyProvider)
    monkeypatch.setattr("utils.websearch.GoogleNewsRssProvider", EmptyProvider)

    result = asyncio.run(chat.exa_search("missing evidence concept", max_keys=1))
    assert result == []
    coverage = assess_evidence_coverage("Assess order density and financing", result)
    assert coverage["relevant_source_count"] == 0
    assert coverage["pass"] is False



def test_exa_search_tries_second_key_when_selected_key_fails(monkeypatch):
    chat._EXA_SEARCH_CACHE.clear()
    monkeypatch.setattr(chat, "get_exa_keys", lambda: ["exa-one", "exa-two"])
    monkeypatch.setattr(chat, "pick_key", lambda _keys, _label: "exa-one")
    monkeypatch.setattr(chat, "round_robin", lambda _keys: ["exa-one", "exa-two"])
    monkeypatch.setattr(chat, "is_rate_limited", lambda _key: False)
    seen = []

    async def fake_call(key, query, max_results, *args, **kwargs):
        seen.append(key)
        if key == "exa-one":
            return None
        return [{
            "title": "Relevant source", "url": "https://example.org/relevant",
            "snippet": "Electronic City software investors",
            "fullContent": "Electronic City software investors and funding information",
            "searchProvider": "exa",
        }]

    async def no_enrich(rows, **_kwargs):
        # _enrich_thin_results receives keyword-only limits/timeouts in the
        # production path; ignore them in this provider-failover test stub.
        return rows

    monkeypatch.setattr(chat, "_exa_one_call", fake_call)
    monkeypatch.setattr(chat, "_enrich_thin_results", no_enrich)
    monkeypatch.setattr(chat, "_schedule_web_research_persistence", lambda *a, **kw: None)

    result = asyncio.run(chat.exa_search("Electronic City software investors explicit-failover-test", max_results=2))
    assert result and result[0]["url"] == "https://example.org/relevant"
    assert seen == ["exa-one", "exa-two"]
