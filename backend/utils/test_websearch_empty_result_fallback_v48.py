import asyncio

from utils import websearch


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.is_success = 200 <= status_code < 300

    def json(self):
        return self._payload


def test_serper_rotates_key_after_http_200_empty_result_set(monkeypatch):
    calls = []

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, _url, *, headers, json):
            calls.append((headers["X-API-KEY"], json["q"]))
            if headers["X-API-KEY"] == "key-a":
                return _FakeResponse({"organic": []})
            return _FakeResponse({"organic": [{
                "title": "Official filing",
                "link": "https://example.gov/filing?utm_source=serper",
                "snippet": "Official evidence excerpt",
            }]})

    monkeypatch.setattr(websearch.httpx, "AsyncClient", lambda **_kwargs: FakeClient())
    monkeypatch.setattr("utils.keys.get_serper_keys", lambda: ["key-a", "key-b"])
    results = asyncio.run(websearch.SerperProvider().search(["renewable capacity"], max_results=5))

    assert [call[0] for call in calls] == ["key-a", "key-b"]
    assert len(results) == 1
    assert results[0]["pageFetchStatus"] == "not_fetched"

