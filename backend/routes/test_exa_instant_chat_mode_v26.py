"""Latency-mode regression tests for ordinary chat versus research searches."""
from __future__ import annotations

import asyncio

from routes import chat


class _FakeResponse:
    status_code = 200
    is_success = True

    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


class _FakeClient:
    def __init__(self, response, calls):
        self.response = response
        self.calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, *, headers, json):
        self.calls.append({"url": url, "headers": headers, "json": json})
        return self.response


def test_exa_one_call_accepts_instant_mode_and_keeps_highlights(monkeypatch):
    calls = []
    response = _FakeResponse({
        "requestId": "req-instant-v26",
        "searchTime": 0.42,
        "results": [{
            "title": "Market update",
            "url": "https://example.org/market-update",
            "highlights": ["Current market update facts."],
            "text": "Current market update facts and evidence." * 20,
        }],
    })
    monkeypatch.setattr(chat.httpx, "AsyncClient", lambda **kwargs: _FakeClient(response, calls))
    monkeypatch.setattr(chat, "is_rate_limited", lambda _key: False)
    monkeypatch.setattr("utils.research_capture.record_exa_raw_response", lambda *a, **kw: None)

    results = asyncio.run(chat._exa_one_call(
        "test-only-key", "latest market news v26", 5, search_type="instant"
    ))

    assert calls[0]["json"]["type"] == "instant"
    assert calls[0]["json"]["numResults"] == 5
    assert calls[0]["json"]["contents"] == {"highlights": True}
    assert results and results[0]["searchProvider"] == "exa"


def test_fast_chat_uses_instant_but_general_research_uses_auto(monkeypatch):
    calls = []
    long_content = "Current, evidence-grounded result content. " * 20

    async def fake_exa_one_call(key, query, max_results, topic="general", **kwargs):
        calls.append({"query": query, **kwargs})
        return [{
            "title": "Test result", "url": f"https://example.org/{len(calls)}",
            "snippet": "Current result excerpt", "fullContent": long_content,
            "published": "", "searchProvider": "exa", "trusted_source": True,
        }]

    async def no_enrichment(rows, **_kwargs):
        return rows

    monkeypatch.setattr(chat, "get_exa_keys", lambda: ["key-v26"])
    monkeypatch.setattr(chat, "is_rate_limited", lambda _key: False)
    monkeypatch.setattr(chat, "round_robin", lambda keys: list(keys))
    monkeypatch.setattr(chat, "_exa_one_call", fake_exa_one_call)
    monkeypatch.setattr(chat, "_enrich_thin_results", no_enrichment)
    monkeypatch.setattr(chat, "_schedule_web_research_persistence", lambda *a, **kw: None)

    async def run_checks():
        await chat.exa_search("unique instant chat lookup v26", max_results=5, fast_chat=True)
        await chat.exa_search("unique general research lookup v26", max_results=5, fast_chat=False)

    asyncio.run(run_checks())
    assert calls[0]["search_type"] == "instant"
    assert calls[1]["search_type"] == "auto"
    assert calls[0]["request_timeout"] == 10.0
    assert calls[1]["request_timeout"] is None
