"""Regression tests for report planner fallback scope."""
import asyncio

from routes import report


def test_deep_research_planner_timeout_does_not_crash_when_not_workbook_only(monkeypatch):
    async def timeout_planner(*args, **kwargs):
        raise asyncio.TimeoutError()

    monkeypatch.setattr(report, "_call_llm_json", timeout_planner)
    result = asyncio.run(
        report._plan_report_structure(
            question="Investigate whether FIIs have left Indian equities and compare equity, debt, IPO and FDI channels.",
            enriched_sources=[],
            file_section="",
            okf_context="",
            no_web_sources=False,
            conversation_context="",
            wants_more_data_viz=False,
            report_tier="in_depth",
            file_only_report=False,
            workbook_analytics={"kind": "generic", "files": [], "charts": []},
        )
    )
    assert isinstance(result, dict)
    assert isinstance(result.get("sections"), list)
    assert result["sections"]


def test_workbook_only_planner_timeout_uses_pms_outline(monkeypatch):
    async def timeout_planner(*args, **kwargs):
        raise asyncio.TimeoutError()

    monkeypatch.setattr(report, "_call_llm_json", timeout_planner)
    result = asyncio.run(
        report._plan_report_structure(
            question="Analyze this uploaded PMS workbook and create a report.",
            enriched_sources=[],
            file_section="[Sheet: Table 2]\nAMC,Strategy,1 Yr Return\nExample,Alpha,12.5",
            okf_context="",
            no_web_sources=True,
            conversation_context="",
            wants_more_data_viz=False,
            report_tier="in_depth",
            file_only_report=True,
            workbook_analytics={"kind": "pms_strategy_performance", "files": [], "charts": [], "strategies": [], "keyStats": []},
        )
    )
    assert isinstance(result, dict)
    headings = [str(s.get("heading")) for s in result.get("sections", [])]
    assert "Workbook Coverage and Methodology" in headings
    assert "Return Distribution and Dispersion" in headings
