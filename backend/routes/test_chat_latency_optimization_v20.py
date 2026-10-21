"""Regression tests for bounded chat research, planner fallback, and first-token diagnostics."""
from __future__ import annotations

import asyncio

from routes import chat
from utils import orchestrator, tools


def test_page_enrichment_only_fetches_top_ranked_results(monkeypatch):
    calls = []

    async def fake_fetch(url, max_chars=3000):
        calls.append(url)
        await asyncio.sleep(0)
        return f"publisher page content for {url} " * 40

    monkeypatch.setattr(chat, "fetch_page_content", fake_fetch)
    results = [
        {"title": f"Source {i}", "url": f"https://example.com/{i}", "fullContent": "thin", "snippet": "thin"}
        for i in range(8)
    ]

    enriched = asyncio.run(chat._enrich_thin_results(results, limit=2, per_page_timeout=0.5))
    assert calls == ["https://example.com/0", "https://example.com/1"]
    assert len(enriched) == 8
    assert len(enriched[0]["fullContent"]) > len("thin")
    assert enriched[2]["fullContent"] == "thin"


def test_chat_web_search_clamps_query_fanout_and_result_context(monkeypatch):
    captured = {}

    async def fake_search_web(queries, **kwargs):
        captured["queries"] = list(queries)
        captured.update(kwargs)
        rows = [
            {"title": f"Result {i}", "url": f"https://example.com/{i}", "snippet": "s" * 20,
             "fullContent": "body " * 400, "published": "2026-10-17"}
            for i in range(20)
        ]
        return {"results": rows, "provider": "test", "error": None}

    monkeypatch.setattr("utils.websearch.search_web", fake_search_web)
    ctx = tools.ToolContext(user_message="one question")
    result = asyncio.run(tools._run_web_search({
        "queries": ["one", "two", "three", "four"], "max_results": 20,
    }, ctx))
    assert captured["queries"] == ["one", "two"]
    assert captured["max_results"] == 8
    assert len(result.sources) == 20  # metadata retained if a provider returns extras
    assert result.context.count("Source: https://example.com/") == 7
    assert len(result.context) < 10_000


def test_short_market_lookups_use_direct_live_search_fast_path():
    for query in (
        "Upcoming IPOs", "Sector performance", "Nifty today",
        "Best SIPs right now", "top mutual funds today", "best index funds currently",
        "Top banking stocks", "best IT stocks", "top stocks in India",
    ):
        assert orchestrator._is_short_live_lookup(query)
        assert orchestrator._requires_mandatory_web_research(query)


def test_heuristic_fallback_never_calls_company_fundamentals_without_named_company():
    ctx = tools.ToolContext(user_message="Upcoming IPOs")
    calls = orchestrator._heuristic_plan(ctx, ["web_search", "company_fundamentals"], ["Upcoming IPOs"])
    names = [name for name, _args in calls]
    assert "web_search" in names
    assert "company_fundamentals" not in names


def test_heuristic_fallback_can_call_fundamentals_for_named_company():
    ctx = tools.ToolContext(user_message="Analyse HDFC Bank")
    calls = orchestrator._heuristic_plan(ctx, ["web_search", "company_fundamentals"], ["Analyse HDFC Bank"])
    names = [name for name, _args in calls]
    assert "company_fundamentals" in names
    company_call = next(args for name, args in calls if name == "company_fundamentals")
    assert company_call["companies"] == ["Analyse HDFC Bank"]


def test_groq_compactor_has_an_emergency_budget_for_repeated_413s():
    system = chat.build_system("\n\n---\n🌐 WEB SEARCH RESULTS\n" + ("evidence " * 9000))
    normal, normal_messages = chat._compact_chat_prompt_for_groq(system, [{"role": "user", "content": "Sector performance"}])
    emergency, emergency_messages = chat._compact_chat_prompt_for_groq(
        system, [{"role": "user", "content": "Sector performance"}],
        max_system_chars=8_500, max_history_chars=700,
    )
    assert len(emergency) <= 8_500
    assert len(normal) <= 12_000
    assert len(emergency) < len(normal)
    assert emergency_messages[-1]["content"] == "Sector performance"


def test_fast_chat_search_uses_one_exa_key_and_bounded_request(monkeypatch):
    calls = []
    query = "fast chat unique IPO query v20"
    chat._EXA_SEARCH_CACHE.pop(chat._exa_cache_key(query, 8, None, None, None), None)

    async def fake_exa_one(key, query, max_results, topic, country=None, images_out=None, time_range=None, request_timeout=None, search_type="auto"):
        calls.append({"key": key, "timeout": request_timeout, "max_results": max_results, "search_type": search_type})
        return [{"title": "IPO list", "url": "https://example.com/ipo", "snippet": "Current list", "fullContent": "current list " * 100}]

    monkeypatch.setattr(chat, "get_exa_keys", lambda: ["exa-key-1", "exa-key-2", "exa-key-3"])
    monkeypatch.setattr(chat, "round_robin", lambda keys: list(keys))
    monkeypatch.setattr(chat, "is_rate_limited", lambda *args, **kwargs: False)
    monkeypatch.setattr(chat, "_exa_one_call", fake_exa_one)
    monkeypatch.setattr(chat, "_schedule_web_research_persistence", lambda *args, **kwargs: None)

    rows = asyncio.run(chat.exa_search(query, max_results=8, fast_chat=True, page_enrich_limit=1))
    assert len(rows) == 1
    assert calls == [{"key": "exa-key-1", "timeout": 10.0, "max_results": 8, "search_type": "instant"}]


def test_groq_all_rate_limited_hands_off_without_request(monkeypatch):
    monkeypatch.setattr(chat, "get_groq_keys", lambda: ["rate-limited-key"])
    monkeypatch.setattr(chat, "round_robin", lambda keys: list(keys))
    monkeypatch.setattr(chat, "is_rate_limited", lambda *args, **kwargs: True)

    async def run():
        assert await chat.stream_groq("system", [{"role": "user", "content": "question"}]) is None

    asyncio.run(run())


def test_direct_market_lookup_skips_planner_round(monkeypatch):
    web_tool = tools.Tool(
        name="web_search", description="test search", parameters={"type": "object", "properties": {}},
        runner=lambda args, ctx: None,
    )
    monkeypatch.setattr(orchestrator, "build_toolset", lambda _ctx: [web_tool])
    monkeypatch.setattr(orchestrator, "tool_schemas", lambda _toolset: [])
    calls = []

    async def fake_execute(tool_calls, ctx, progress_callback=None):
        calls.extend(name for name, _args in tool_calls)
        return [tools.ToolResult(name="web_search", ok=True, context="source context", sources=[{"title": "IPO", "url": "https://example.com/ipo"}])]

    async def should_not_plan(*args, **kwargs):
        raise AssertionError("short live lookup should not invoke an LLM planner")

    monkeypatch.setattr(orchestrator, "execute_tools", fake_execute)
    monkeypatch.setattr(orchestrator, "_groq_plan", should_not_plan)
    monkeypatch.setattr(orchestrator, "_gemini_plan", should_not_plan)

    result = asyncio.run(orchestrator.orchestrate(
        user_message="Upcoming IPOs", messages=[{"role": "user", "content": "Upcoming IPOs"}],
        search_queries=["upcoming IPOs India 2026"],
    ))
    assert result.planner == "direct-search"
    assert result.tools_used == ["web_search"]
    assert calls == ["web_search"]


def test_best_sips_right_now_skips_planner_and_uses_direct_live_search(monkeypatch):
    web_tool = tools.Tool(
        name="web_search", description="test search", parameters={"type": "object", "properties": {}},
        runner=lambda args, ctx: None,
    )
    monkeypatch.setattr(orchestrator, "build_toolset", lambda _ctx: [web_tool])
    monkeypatch.setattr(orchestrator, "tool_schemas", lambda _toolset: [])
    captured = []

    async def fake_execute(tool_calls, ctx, progress_callback=None):
        captured.extend(tool_calls)
        return [tools.ToolResult(name="web_search", ok=True, context="fresh SIP sources", sources=[
            {"title": "Current SIP source", "url": "https://example.com/sips"},
        ])]

    async def should_not_plan(*args, **kwargs):
        raise AssertionError("SIP suggestion tile should not invoke an LLM planner first")

    monkeypatch.setattr(orchestrator, "execute_tools", fake_execute)
    monkeypatch.setattr(orchestrator, "_groq_plan", should_not_plan)
    monkeypatch.setattr(orchestrator, "_gemini_plan", should_not_plan)

    result = asyncio.run(orchestrator.orchestrate(
        user_message="Best SIPs right now",
        messages=[{"role": "user", "content": "Best SIPs right now"}],
        search_queries=["Best SIPs right now latest"],
    ))
    assert result.planner == "direct-search"
    assert result.tools_used == ["web_search"]
    assert [name for name, _args in captured] == ["web_search"]


def test_top_banking_stocks_tile_skips_planner_and_uses_direct_live_search(monkeypatch):
    web_tool = tools.Tool(
        name="web_search", description="test search", parameters={"type": "object", "properties": {}},
        runner=lambda args, ctx: None,
    )
    monkeypatch.setattr(orchestrator, "build_toolset", lambda _ctx: [web_tool])
    monkeypatch.setattr(orchestrator, "tool_schemas", lambda _toolset: [])
    captured = []

    async def fake_execute(tool_calls, ctx, progress_callback=None):
        captured.extend(tool_calls)
        return [tools.ToolResult(name="web_search", ok=True, context="fresh banking stock sources", sources=[
            {"title": "Banking stocks", "url": "https://example.com/banking-stocks"},
        ])]

    async def should_not_plan(*args, **kwargs):
        raise AssertionError("ranking tile should not invoke an LLM planner first")

    monkeypatch.setattr(orchestrator, "execute_tools", fake_execute)
    monkeypatch.setattr(orchestrator, "_groq_plan", should_not_plan)
    monkeypatch.setattr(orchestrator, "_gemini_plan", should_not_plan)

    result = asyncio.run(orchestrator.orchestrate(
        user_message="Top banking stocks",
        messages=[{"role": "user", "content": "Top banking stocks"}],
        search_queries=["top banking stocks India current market cap"],
    ))
    assert result.planner == "direct-search"
    assert result.tools_used == ["web_search"]
    assert [name for name, _args in captured] == ["web_search"]
