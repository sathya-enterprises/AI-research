import asyncio
import json
import sys
from pathlib import Path

import httpx
sys.path.insert(0, Path(__file__).resolve().parents[1].as_posix())

from routes import report as R

_REAL_CLIENT = httpx.AsyncClient


def _install_transport(monkeypatch, handler):
    transport = httpx.MockTransport(handler)

    def factory(*args, **kwargs):
        kwargs.pop("transport", None)
        return _REAL_CLIENT(*args, transport=transport, **kwargs)

    monkeypatch.setattr(R.httpx, "AsyncClient", factory)


def _sse(*events):
    return "".join(f"data: {json.dumps(e)}\r\n\r\n" for e in events).encode()


def _event(text, finish=None):
    cand = {"content": {"parts": [{"text": text}]}}
    if finish:
        cand["finishReason"] = finish
    return {"candidates": [cand]}


def _attempt(prompt="prompt"):
    return asyncio.run(R._gemini_one_attempt(prompt, "detailed", "AQ.fake-key", "gemini-3.6-flash"))


def test_stream_chunks_are_joined_and_key_stays_out_of_the_url(monkeypatch):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["header"] = request.headers.get("x-goog-api-key")
        return httpx.Response(200, content=_sse(_event('{"title":'), _event(' "ok"}', "STOP")))

    _install_transport(monkeypatch, handler)
    text, model, status = _attempt()
    assert (text, status) == ('{"title": "ok"}', "success")
    assert "AQ.fake-key" not in seen["url"] and seen["header"] == "AQ.fake-key"
    assert ":streamGenerateContent" in seen["url"]


def test_http_errors_keep_their_status_codes(monkeypatch):
    for code in ("503", "429", "404"):
        _install_transport(monkeypatch, lambda request, c=int(code): httpx.Response(c, text="{}"))
        monkeypatch.setattr(R, "mark_rate_limited", lambda *a, **k: None)
        assert _attempt()[2] == code


def test_max_tokens_partial_is_preserved_when_report_body_is_recoverable(monkeypatch):
    body = "## Findings\n\n" + ("A supported finding explains the observed change and its source-grounded implication. " * 20)
    raw = json.dumps({"title": "Findings", "report": body}, ensure_ascii=False)[:-1]
    assert len(R._recover_report_body(raw)) >= R._MIN_VALID_REPORT_BODY_CHARS
    _install_transport(monkeypatch, lambda request: httpx.Response(200, content=_sse(_event(raw, "MAX_TOKENS"))))
    text, model, status = _attempt()
    assert text == raw
    assert model == "gemini-3.6-flash"
    assert status == "max_tokens_partial"


def test_max_tokens_without_substantive_report_body_is_rejected(monkeypatch):
    _install_transport(monkeypatch, lambda request: httpx.Response(200, content=_sse(_event("partial", "MAX_TOKENS"))))
    assert _attempt()[2] == "max_tokens"


def test_nonstreaming_gemini_joins_all_non_thought_text_parts(monkeypatch):
    payload = {
        "candidates": [{
            "content": {"parts": [
                {"text": "first "},
                {"text": "hidden reasoning", "thought": True},
                {"text": "last"},
            ]},
            "finishReason": "STOP",
        }],
    }
    _install_transport(monkeypatch, lambda request: httpx.Response(200, json=payload))
    monkeypatch.setattr(R, "_GEMINI_REPORT_STREAMING", False)
    status, text, finish = asyncio.run(R._gemini_generate(
        "AQ.fake-key", "gemini-3.6-flash", "prompt", {}, "detailed",
    ))
    assert (status, text, finish) == (200, "first last", "STOP")


def test_attempt_cap_reports_timeout(monkeypatch):
    class Slow(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield _sse(_event("first"))
            await asyncio.sleep(5)
            yield _sse(_event("never", "STOP"))

    _install_transport(monkeypatch, lambda request: httpx.Response(200, stream=Slow()))
    monkeypatch.setattr(R, "mark_rate_limited", lambda *a, **k: None)
    monkeypatch.setitem(R._GEMINI_ATTEMPT_CAPS, "detailed", 0.2)
    assert _attempt()[2] == "timeout"
    assert not R._GEMINI_STREAMING_ATTEMPTS


def test_hedge_is_not_launched_while_a_stream_is_flowing(monkeypatch):
    launched = []

    async def fake_attempt(prompt, depth, key, model):
        launched.append(key)
        R._GEMINI_STREAMING_ATTEMPTS.add((key, model))
        try:
            await asyncio.sleep(0.4)
        finally:
            R._GEMINI_STREAMING_ATTEMPTS.discard((key, model))
        return "report", model, "success"

    monkeypatch.setattr(R, "_gemini_one_attempt", fake_attempt)
    monkeypatch.setattr(R, "_gemini_hedge_delay", lambda depth: 0.05)
    import time
    started = time.perf_counter()
    text, *_ = asyncio.run(R._run_gemini_wave("p", "detailed", "m", ["k1", "k2"], started, started + 5))
    assert text == "report" and launched == ["k1"]
