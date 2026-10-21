import asyncio

from routes import chat


class FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.is_success = 200 <= status_code < 300

    def json(self):
        return self._body


class FakeClient:
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


def test_exa_rest_request_uses_server_key_and_normalizes_results(monkeypatch):
    calls = []
    response = FakeResponse(200, {
        "requestId": "req-test-001",
        "results": [{
            "title": "Karnataka startup ecosystem report",
            "url": "https://example.org/karnataka-startups",
            "publishedDate": "2026-10-10T00:00:00Z",
            "author": "Research Desk",
            "highlights": ["Startup investments in Karnataka."],
            "text": "A detailed report on startup investment in Karnataka.",
        }],
    })
    monkeypatch.setattr(chat.httpx, "AsyncClient", lambda **kwargs: FakeClient(response, calls))
    monkeypatch.setattr(chat, "is_rate_limited", lambda _key: False)
    monkeypatch.setattr("utils.research_capture.record_exa_raw_response", lambda *a, **kw: None)

    results = asyncio.run(chat._exa_one_call("secret-test-key", "Karnataka startup investments", 5))

    assert calls[0]["url"] == "https://api.exa.ai/search"
    assert calls[0]["headers"]["x-api-key"] == "secret-test-key"
    assert calls[0]["json"]["type"] == "auto"
    assert calls[0]["json"]["numResults"] == 5
    assert results[0]["title"] == "Karnataka startup ecosystem report"
    assert results[0]["published"] == "2026-10-10T00:00:00Z"
    assert results[0]["searchProvider"] == "exa"
    assert "investment in Karnataka" in results[0]["fullContent"]


def test_exa_auth_error_marks_only_the_key_for_backoff(monkeypatch):
    calls = []
    marked = []
    response = FakeResponse(401, {"requestId": "req-bad-key", "error": "invalid key"})
    monkeypatch.setattr(chat.httpx, "AsyncClient", lambda **kwargs: FakeClient(response, calls))
    monkeypatch.setattr(chat, "is_rate_limited", lambda _key: False)
    monkeypatch.setattr(chat, "mark_rate_limited", lambda key, ms: marked.append((key, ms)))

    results = asyncio.run(chat._exa_one_call("bad-test-key", "Karnataka startup investments", 5))

    assert results is None
    assert marked == [("bad-test-key", 24 * 60 * 60_000)]
    # Credentials are sent only in the request header; code never adds them to request JSON.
    assert "api_key" not in calls[0]["json"]


def test_exa_cache_is_exact_query_scoped_and_does_not_cache_empty_sets():
    key_a = chat._exa_cache_key("Electronic City software investors", 5, None, None, None)
    key_b = chat._exa_cache_key("renewable energy capacity", 5, None, None, None)
    assert key_a != key_b
    before = dict(chat._EXA_SEARCH_CACHE)
    chat._exa_cache_put(key_a, [])
    assert key_a not in chat._EXA_SEARCH_CACHE
    chat._EXA_SEARCH_CACHE.clear()
    chat._EXA_SEARCH_CACHE.update(before)


def test_exa_key_loader_supports_two_explicit_keys_and_deduplicates(monkeypatch):
    from utils import keys

    monkeypatch.setenv("EXA_API_KEY", "first-key")
    monkeypatch.setenv("EXA_API_KEY_2", "second-key")
    monkeypatch.setenv("EXA_API_KEYS", "second-key, third-key")

    assert keys.get_exa_keys() == ["first-key", "second-key", "third-key"]


def test_exa_plan_limit_432_marks_key_and_allows_fallback(monkeypatch):
    calls = []
    marked = []
    response = FakeResponse(432, {"requestId": "req-plan-limit", "error": "usage limit"})
    monkeypatch.setattr(chat.httpx, "AsyncClient", lambda **kwargs: FakeClient(response, calls))
    monkeypatch.setattr(chat, "is_rate_limited", lambda _key: False)
    monkeypatch.setattr(chat, "mark_rate_limited", lambda key, ms: marked.append((key, ms)))

    result = asyncio.run(chat._exa_one_call("exa-limited-test-key", "Electronic City investors", 5))

    assert result is None
    assert marked == [("exa-limited-test-key", 60 * 60_000)]
