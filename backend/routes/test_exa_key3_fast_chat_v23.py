"""Regressions for EXA_API_KEY_3 and the Exa fast_chat parameter contract."""
from __future__ import annotations

import asyncio

from routes import chat
from utils import keys
from utils.websearch import ExaProvider


def test_exa_key_loader_supports_three_explicit_keys_and_deduplicates(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "exa-one")
    monkeypatch.setenv("EXA_API_KEY_2", "exa-two")
    monkeypatch.setenv("EXA_API_KEY_3", "exa-three")
    monkeypatch.setenv("EXA_API_KEYS", "exa-three, exa-four")
    assert keys.get_exa_keys() == ["exa-one", "exa-two", "exa-three", "exa-four"]


def test_exa_provider_fast_chat_flag_matches_multi_search_signature(monkeypatch):
    seen = {}

    async def fake_multi(queries, **kwargs):
        seen["queries"] = queries
        seen.update(kwargs)
        return [{"title": "result", "url": "https://example.org/result", "snippet": "evidence"}]

    monkeypatch.setattr(chat, "exa_search_multi", fake_multi)
    results = asyncio.run(ExaProvider().search(["test query"], max_results=4))

    assert results[0]["url"] == "https://example.org/result"
    assert seen["fast_chat"] is True
    assert seen["max_results"] == 4
