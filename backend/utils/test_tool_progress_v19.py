"""Regression tests for genuine tool-progress events used by the chat SSE stream."""
import asyncio

from utils import tools


def test_execute_tools_emits_start_and_completion_events_in_parallel(monkeypatch):
    events = []

    async def progress(event):
        events.append(event)

    async def fake_execute_tool(name, args, ctx):
        await asyncio.sleep(0.01 if name == "web_search" else 0.001)
        return tools.ToolResult(
            name=name, ok=True, args=args,
            summary="ok", context="evidence", sources=[{"title": name, "url": f"https://example.com/{name}"}],
            elapsed_ms=12,
        )

    monkeypatch.setattr(tools, "execute_tool", fake_execute_tool)

    async def run():
        ctx = tools.ToolContext(user_message="test")
        results = await tools.execute_tools(
            [("web_search", {"queries": ["one"]}), ("stored_web_research", {"query": "two"})],
            ctx,
            progress_callback=progress,
        )
        assert [r.name for r in results] == ["web_search", "stored_web_research"]

    asyncio.run(run())
    starts = [event for event in events if event["status"] == "started"]
    completes = [event for event in events if event["status"] == "completed"]
    assert {e["tool"] for e in starts} == {"web_search", "stored_web_research"}
    assert {e["tool"] for e in completes} == {"web_search", "stored_web_research"}
    assert all(e["sourceCount"] == 1 for e in completes)
    assert all("source(s) found" in e["message"] for e in completes)


def test_progress_callback_failure_does_not_break_tool_results(monkeypatch):
    async def fake_execute_tool(name, args, ctx):
        return tools.ToolResult(name=name, ok=True, args=args, summary="ok")

    async def broken_progress(event):
        raise RuntimeError("closed SSE client")

    monkeypatch.setattr(tools, "execute_tool", fake_execute_tool)

    async def run():
        results = await tools.execute_tools(
            [("stored_web_research", {"query": "x"})], tools.ToolContext(),
            progress_callback=broken_progress,
        )
        assert len(results) == 1 and results[0].ok

    asyncio.run(run())


def test_orchestrator_skip_still_emits_truthful_completion(monkeypatch):
    from utils.orchestrator import orchestrate
    events = []
    async def progress(event):
        events.append(event)
    async def run():
        result = await orchestrate(
            user_message="hi", messages=[{"role": "user", "content": "hi"}],
            skip=True, progress_callback=progress,
        )
        assert result.planner == "skipped"
    asyncio.run(run())
    assert events[0]["phase"] == "research"
    assert events[0]["status"] == "completed"
    assert "No external research needed" in events[0]["message"]
