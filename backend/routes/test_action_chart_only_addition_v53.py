"""Regression tests for chart-only report Actions."""
from routes.report import (
    _action_edit_validation,
    _action_is_chart_only_addition,
    _action_requested_chart_count,
    _action_requests_chart_addition,
)


def test_chart_only_action_may_leave_section_prose_unchanged():
    section = "## Market Drivers\n\nDemand is supported by lower financing costs [S1]."
    instruction = 'Target section: "Market Drivers"\nInstruction: add some charts and data points'

    assert _action_requests_chart_addition(instruction)
    assert _action_requested_chart_count(instruction) == 2
    assert _action_is_chart_only_addition(instruction)
    valid, problems = _action_edit_validation(
        section,
        section,
        instruction,
        evidence_source=section,
    )
    assert valid, problems


def test_chart_action_with_separate_prose_edit_still_requires_material_change():
    section = "## Market Drivers\n\nDemand is supported by lower financing costs [S1]."
    instruction = (
        'Target section: "Market Drivers"\n'
        "Instruction: add some charts and data points, and rewrite the paragraph for clarity"
    )

    assert not _action_is_chart_only_addition(instruction)
    valid, problems = _action_edit_validation(
        section,
        section,
        instruction,
        evidence_source=section,
    )
    assert not valid
    assert any("edit produced no material change" in p for p in problems)


def test_chart_only_action_does_not_bypass_numeric_or_citation_safety():
    section = "## Market Drivers\n\nDemand is supported by lower financing costs [S1]."
    edited = section + "\n\nMarket growth reached 73% [S99]."
    instruction = 'Target section: "Market Drivers"\nInstruction: add some charts and data points'

    valid, problems = _action_edit_validation(
        section,
        edited,
        instruction,
        evidence_source=section,
    )
    assert not valid
    assert any("unsupported source citations" in p for p in problems)
    assert any("new unsupported numeric values" in p for p in problems)


def test_plural_chart_action_commits_two_charts_without_rewriting_section(monkeypatch):
    import asyncio
    import json
    import routes.report as report_module

    report_text = (
        "# Market Outlook\n\n"
        "## Market Drivers\n\n"
        "Demand increased from 10% in 2024 to 12% in 2025 [S1]. "
        "The category mix is 40% and 60% [S1].\n\n"
        "## Risks\n\nSupply constraints remain material [S2].\n"
    )
    original_section = (
        "## Market Drivers\n\n"
        "Demand increased from 10% in 2024 to 12% in 2025 [S1]. "
        "The category mix is 40% and 60% [S1]."
    )
    bar = {
        "type": "bar", "title": "Demand Growth", "unit": "%",
        "xLabel": "Year", "yLabel": "Growth (%)",
        "series": [{"name": "Growth", "data": [
            {"label": "2024", "value": 10}, {"label": "2025", "value": 12},
        ]}],
        "rationale": "Compare reported growth by year.", "takeaway": "Growth increased.",
    }
    pie = {
        "type": "pie", "title": "Category Mix", "unit": "%",
        "series": [{"name": "Mix", "data": [
            {"label": "Category A", "value": 40}, {"label": "Category B", "value": 60},
        ]}],
        "rationale": "Show the supported category mix.", "takeaway": "Category B has the larger share.",
    }
    chart_results = iter([bar, pie])

    monkeypatch.setattr(report_module, "_action_compact_evidence_context", lambda *args, **kwargs: report_text)
    monkeypatch.setattr(report_module, "_action_relevant_cross_sections", lambda *args, **kwargs: "")
    async def no_stored(*args, **kwargs):
        return "", []
    monkeypatch.setattr(report_module, "_action_stored_web_research", no_stored)
    monkeypatch.setattr(report_module, "_action_requires_web_research", lambda *args, **kwargs: False)
    async def preserve_prose(*args, **kwargs):
        return original_section
    monkeypatch.setattr(report_module, "_call_llm_plain", preserve_prose)
    async def make_chart(*args, **kwargs):
        return next(chart_results)
    monkeypatch.setattr(report_module, "_call_llm_json", make_chart)

    instruction = 'Target section: "Market Drivers"\nInstruction: add some charts and data points'
    response = asyncio.run(report_module._edit_report_section_single_body({
        "report": report_text,
        "editInstruction": instruction,
        "title": "Market Outlook",
        "charts": [], "images": [], "sources": [], "presentation": None,
        "reportTier": "brief",
    }))
    payload = json.loads(response.body.decode("utf-8"))

    assert response.status_code == 200, payload
    assert len(payload["charts"]) == 2
    assert "[CHART_1]" in payload["report"]
    assert "[CHART_2]" in payload["report"]
    assert "## Risks\n\nSupply constraints remain material [S2]." in payload["report"]
