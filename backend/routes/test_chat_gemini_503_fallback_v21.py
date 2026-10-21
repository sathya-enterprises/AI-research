"""Regression tests for fast Gemini streaming fallback on repeated 503s."""
from __future__ import annotations

import asyncio
import json

from routes import chat


def test_gemini_retries_a_503_once_then_switches_models(monkeypatch):
    requests = []

    class FakeResponse:
        def __init__(self, status_code, chunks=None):
            self.status_code = status_code
            self.is_success = 200 <= status_code < 300
            self.is_closed = False
            self.headers = {}
            self._chunks = chunks or []

        async def aread(self):
            return b'{"error":{"message":"temporary overload"}}'

        async def aclose(self):
            self.is_closed = True

        async def aiter_lines(self):
            for line in self._chunks:
                yield line

    class FakeClient:
        is_closed = False

        def __init__(self, **kwargs):
            self.is_closed = False

        def build_request(self, method, url, **kwargs):
            request = {"method": method, "url": url, **kwargs}
            requests.append(request)
            return request

        async def send(self, request, stream=False):
            assert stream is True
            model = request["url"].split("/models/", 1)[1].split(":", 1)[0]
            if model == "gemini-3.5-flash-lite":
                return FakeResponse(503)
            return FakeResponse(200, [
                "data: " + json.dumps({"candidates": [{"content": {"parts": [{"text": "fallback token"}]}}]})
            ])

        async def aclose(self):
            self.is_closed = True

    monkeypatch.setattr(chat, "_GEMINI_CHAT_MODELS", ["gemini-3.5-flash-lite", "gemini-3.6-flash"])
    monkeypatch.setattr(chat, "get_gemini_keys", lambda: ["key-one", "key-two", "key-three"])
    monkeypatch.setattr(chat, "round_robin", lambda keys: list(keys))
    monkeypatch.setattr(chat, "is_rate_limited", lambda *args, **kwargs: False)
    monkeypatch.setattr(chat, "mark_rate_limited", lambda *args, **kwargs: None)
    monkeypatch.setattr(chat.httpx, "AsyncClient", FakeClient)

    async def run():
        stream = chat.gemini_sse("system prompt", [{"role": "user", "content": "hello"}])
        first = await anext(stream)
        assert "fallback token" in first
        terminal = await anext(stream)
        assert terminal.strip() == "data: [DONE]"

    asyncio.run(run())
    models = [r["url"].split("/models/", 1)[1].split(":", 1)[0] for r in requests]
    assert models == [
        "gemini-3.5-flash-lite",
        "gemini-3.5-flash-lite",
        "gemini-3.6-flash",
    ]
    assert len(requests) == 3, "must not rotate through other keys after repeated 503s"


def test_gemini_chat_models_prioritize_lower_latency_candidates():
    assert chat._GEMINI_CHAT_MODELS[:3] == [
        "gemini-3.5-flash-lite",
        "gemini-3.6-flash",
        "gemini-3-flash-preview",
    ]
