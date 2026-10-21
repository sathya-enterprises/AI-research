"""Regression tests for Gemini planner request shape and direct live-search routing."""
from __future__ import annotations

from utils.orchestrator import (
    _is_short_live_lookup,
    _openai_messages_to_gemini,
    _requires_mandatory_web_research,
)


def test_gemini_function_responses_use_supported_user_role():
    system, contents = _openai_messages_to_gemini([
        {"role": "system", "content": "Plan with tools."},
        {"role": "user", "content": "Find current RBI rate information."},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call-1", "type": "function", "function": {"name": "web_search", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "call-1", "name": "web_search", "content": "Two current sources."},
    ])

    assert system == "Plan with tools."
    assert [turn["role"] for turn in contents] == ["user", "model", "user"]
    assert "function" not in [turn["role"] for turn in contents]
    assert contents[-1]["parts"][0]["functionResponse"]["name"] == "web_search"


def test_consecutive_gemini_function_results_are_grouped_into_one_user_turn():
    _system, contents = _openai_messages_to_gemini([
        {"role": "user", "content": "Search and check sources."},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call-1", "type": "function", "function": {"name": "web_search", "arguments": "{}"}},
            {"id": "call-2", "type": "function", "function": {"name": "stored_web_research", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "call-1", "name": "web_search", "content": "Live sources."},
        {"role": "tool", "tool_call_id": "call-2", "name": "stored_web_research", "content": "Stored sources."},
    ])

    assert [turn["role"] for turn in contents] == ["user", "model", "user"]
    response_parts = contents[-1]["parts"]
    assert len(response_parts) == 2
    assert {part["functionResponse"]["name"] for part in response_parts} == {
        "web_search", "stored_web_research"
    }


def test_short_current_finance_queries_use_direct_live_search_path():
    for query in (
        "Nifty outlook today",
        "RBI rate cut impact",
        "banking stocks outlook",
        "latest market news",
    ):
        assert _is_short_live_lookup(query), query
        assert _requires_mandatory_web_research(query), query


def test_long_research_questions_do_not_use_short_lookup_path():
    query = "Assess India's banking sector over the next three years, including earnings and credit growth."
    assert not _is_short_live_lookup(query)
