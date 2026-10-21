"""Regression tests for token-first Gemini streaming and Groq 413 compaction."""
import asyncio
import json

from routes import chat


def test_groq_compactor_keeps_persona_and_query_and_bounds_context():
    persona = chat.build_system()
    prompt = persona + "\n\n---\n🌐 WEB SEARCH RESULTS — 20 pages\n\n" + "\n\n".join(
        f"- Source {i}\nSource: https://example.com/{i}\n" + ("evidence " * 500)
        for i in range(20)
    ) + "\n\n---\n📚 PREVIOUSLY STORED WEB RESEARCH\n" + ("stored evidence " * 1200)
    messages = [
        {"role": "user", "content": "Earlier question"},
        {"role": "assistant", "content": "Earlier answer"},
        {"role": "user", "content": "Latest user query must remain intact"},
    ]
    compact, compact_messages = chat._compact_chat_prompt_for_groq(prompt, messages)
    assert compact.startswith(persona)
    assert len(compact) <= 16_500
    assert "Source: https://example.com/0" in compact
    assert compact_messages[-1]["content"] == "Latest user query must remain intact"
    assert len(compact_messages) <= 3


def test_gemini_streams_each_text_delta_from_stream_generate_content(monkeypatch):
    requests = []

    class FakeResponse:
        status_code = 200
        is_success = True
        is_closed = False
        headers = {}

        async def aiter_lines(self):
            yield 'data: ' + json.dumps({"candidates": [{"content": {"parts": [{"text": "First chunk "}]}}]})
            await asyncio.sleep(0)
            yield 'data: ' + json.dumps({"candidates": [{"content": {"parts": [{"text": "second chunk."}]}}]})

        async def aclose(self):
            self.is_closed = True

        async def aread(self):
            return b""

    class FakeClient:
        is_closed = False

        def __init__(self, **kwargs):
            pass

        def build_request(self, method, url, **kwargs):
            requests.append({"method": method, "url": url, **kwargs})
            return requests[-1]

        async def send(self, request, stream=False):
            assert stream is True
            assert ":streamGenerateContent?alt=sse" in request["url"]
            return FakeResponse()

        async def aclose(self):
            self.is_closed = True

    monkeypatch.setattr(chat, "get_gemini_keys", lambda: ["test-key"])
    monkeypatch.setattr(chat, "round_robin", lambda keys: list(keys))
    monkeypatch.setattr(chat, "is_rate_limited", lambda *args, **kwargs: False)
    monkeypatch.setattr(chat.httpx, "AsyncClient", FakeClient)

    async def run():
        stream = chat.gemini_sse("system prompt", [{"role": "user", "content": "hello"}])
        first = await anext(stream)
        obj = json.loads(first.split("data: ", 1)[1])
        assert obj["choices"][0]["delta"]["content"] == "First chunk "
        second = await anext(stream)
        obj2 = json.loads(second.split("data: ", 1)[1])
        assert obj2["choices"][0]["delta"]["content"] == "second chunk."
        terminal = await anext(stream)
        assert terminal.strip() == "data: [DONE]"
        assert len(requests) == 1
        assert requests[0]["headers"]["x-goog-api-key"] == "test-key"
        assert requests[0]["json"]["contents"][-1]["parts"][0]["text"] == "hello"

    asyncio.run(run())


def test_groq_413_retries_once_with_compact_payload(monkeypatch):
    requests = []

    class FakeResponse:
        def __init__(self, status_code, body=b"", chunks=None):
            self.status_code = status_code
            self.is_success = 200 <= status_code < 300
            self.headers = {}
            self.is_closed = False
            self._body = body
            self._chunks = chunks or []

        async def aread(self):
            return self._body

        async def aclose(self):
            self.is_closed = True

        async def aiter_bytes(self):
            for chunk in self._chunks:
                yield chunk

    class FakeClient:
        is_closed = False

        def __init__(self, **kwargs):
            self.is_closed = False

        def build_request(self, method, url, **kwargs):
            return {"method": method, "url": url, **kwargs}

        async def send(self, request, stream=False):
            requests.append(request["json"])
            if len(requests) == 1:
                return FakeResponse(413, b'{"error":{"message":"TPM limit 8000 requested 11478"}}')
            chunk = ('data: ' + json.dumps({"choices": [{"delta": {"content": "streamed"}}]}) + "\n\n").encode()
            return FakeResponse(200, chunks=[chunk])

        async def aclose(self):
            self.is_closed = True

    # Keep this regression focused on the 413 retry branch; production uses a
    # lower proactive-compaction threshold to avoid this oversized first attempt.
    monkeypatch.setattr(chat, "GROQ_PROACTIVE_COMPACT_CHAR_BUDGET", 100_000)
    monkeypatch.setattr(chat, "get_groq_keys", lambda: ["test-groq-key"])
    monkeypatch.setattr(chat, "round_robin", lambda keys: list(keys))
    monkeypatch.setattr(chat, "is_rate_limited", lambda *args, **kwargs: False)
    monkeypatch.setattr(chat, "mark_rate_limited", lambda *args, **kwargs: None)
    monkeypatch.setattr(chat.httpx, "AsyncClient", FakeClient)

    async def run():
        context = chat.build_system("\n\n---\n🌐 WEB SEARCH RESULTS\n" + ("evidence " * 2200))
        stream = await chat.stream_groq(context, [{"role": "user", "content": "check sector performance"}])
        assert stream is not None
        assert len(requests) == 2
        assert len(requests[1]["messages"][0]["content"]) < len(requests[0]["messages"][0]["content"])
        assert requests[0]["max_tokens"] == 2048
        assert requests[1]["max_tokens"] == 1400
        chunks = [part async for part in stream]
        assert "streamed" in "".join(chunks)

    asyncio.run(run())


def test_sanitizer_forwards_live_research_progress_events():
    async def upstream():
        yield 'data: ' + json.dumps({"type": "status", "phase": "research_tool", "status": "started", "tool": "web_search", "message": "Searching the web…"}) + "\n\n"
        yield 'data: ' + json.dumps({"choices": [{"delta": {"content": "First answer chunk."}}]}) + "\n\n"

    async def run():
        chunks = []
        output = []
        async for item in chat._sanitize_and_forward(upstream(), chunks):
            output.append(item.decode() if isinstance(item, bytes) else str(item))
        joined = "".join(output)
        assert '"phase": "research_tool"' in joined
        assert '"message": "Searching the web' in joined
        assert "First answer chunk." in joined

    asyncio.run(run())


def test_groq_429_tries_one_alternate_key_then_yields_to_gemini(monkeypatch):
    requests = []

    class FakeResponse:
        status_code = 429
        is_success = False
        is_closed = False
        headers = {"retry-after": "1"}

        async def aclose(self):
            self.is_closed = True

        async def aread(self):
            return b'{"error":{"message":"rate limit"}}'

    class FakeClient:
        is_closed = False

        def __init__(self, **kwargs):
            self.is_closed = False

        def build_request(self, method, url, **kwargs):
            return {"method": method, "url": url, **kwargs}

        async def send(self, request, stream=False):
            requests.append(request)
            return FakeResponse()

        async def aclose(self):
            self.is_closed = True

    monkeypatch.setattr(chat, "get_groq_keys", lambda: ["groq-key-one", "groq-key-two", "groq-key-three"])
    monkeypatch.setattr(chat, "round_robin", lambda keys: list(keys))
    monkeypatch.setattr(chat, "is_rate_limited", lambda *args, **kwargs: False)
    monkeypatch.setattr(chat, "mark_rate_limited", lambda *args, **kwargs: None)
    monkeypatch.setattr(chat.httpx, "AsyncClient", FakeClient)

    async def run():
        result = await chat.stream_groq("short system prompt", [{"role": "user", "content": "hello"}])
        assert result is None

    asyncio.run(run())
    assert len(requests) == 1, "should hand off to Gemini after the first Groq 429"
