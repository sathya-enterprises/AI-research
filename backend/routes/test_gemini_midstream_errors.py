"""A 503/429 delivered as an SSE error event (HTTP 200 stream) must be handled
like the same status on a normal response."""
import asyncio

import sys
from pathlib import Path

sys.path.insert(0, Path(__file__).resolve().parents[1].as_posix())

from routes import report


def _run_attempt(monkeypatch, message):
    async def fake_generate(*args, **kwargs):
        raise RuntimeError(message)

    marks = []
    monkeypatch.setattr(report, "_gemini_generate", fake_generate)
    monkeypatch.setattr(report, "mark_rate_limited", lambda name, ms: marks.append((name, ms)))
    result = asyncio.run(report._gemini_one_attempt("prompt", "in_depth", "AIzaSyKEY1234", "gemini-3-flash-preview"))
    return result, marks


def test_midstream_503_cools_the_model_not_the_key(monkeypatch):
    result, marks = _run_attempt(monkeypatch, "stream error: {'code': 503, 'message': 'high demand', 'status': 'UNAVAILABLE'}")
    assert result == ("", "gemini-3-flash-preview", "503")
    assert marks == [("gemini:model:gemini-3-flash-preview", report.GEMINI_MODEL_COOLDOWN_MS)]


def test_midstream_429_cools_the_key(monkeypatch):
    result, marks = _run_attempt(monkeypatch, "stream error: {'code': 429, 'message': 'quota'}")
    assert result[2] == "429"
    assert all(not name.startswith("gemini:model:") for name, _ in marks)
    assert len(marks) == 2


def test_other_stream_errors_still_count_as_exception(monkeypatch):
    result, _ = _run_attempt(monkeypatch, "stream error: {'code': 500, 'message': 'boom'}")
    assert result[2] == "exception"


def test_preview_is_first_and_cooldown_is_a_minute():
    assert report.GEMINI_MODELS[0] == "gemini-3-flash-preview"
    assert report.GEMINI_MODEL_COOLDOWN_MS == 60_000
