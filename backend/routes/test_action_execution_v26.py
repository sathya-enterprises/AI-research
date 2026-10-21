"""Regression coverage for Action targeting, preservation, and target parsing."""
import asyncio
import json

import pytest

import routes.report as report_module
from routes.report import (
    _action_extract_targets,
    _action_section_end,
    _ensure_action_heading,
    _parse_report_sections,
    _action_relevant_cross_sections,
    _edit_report_section_single_body,
)


def test_parent_action_span_includes_nested_subsections_but_not_next_peer():
    report = """# Banking report

## Market Context
Overview [S1].

### Credit Drivers
Credit growth was 12% [S2].

### Deposit Dynamics
Deposit growth was 9% [S3].

## Risk Assessment
Risk remains material [S4].
"""
    sections = _parse_report_sections(report)
    parent_idx = next(i for i, x in enumerate(sections) if x["heading"] == "Market Context")
    end = _action_section_end(report, sections, parent_idx)
    selected = report[sections[parent_idx]["start"]:end]
    assert "### Credit Drivers" in selected
    assert "### Deposit Dynamics" in selected
    assert "## Risk Assessment" not in selected


def test_child_action_span_stops_at_next_sibling_heading():
    report = """## Market Context
Intro
### Credit Drivers
Credit details [S1].
### Deposit Dynamics
Deposit details [S2].
## Risks
Risk details [S3].
"""
    sections = _parse_report_sections(report)
    child_idx = next(i for i, x in enumerate(sections) if x["heading"] == "Credit Drivers")
    selected = report[sections[child_idx]["start"]:_action_section_end(report, sections, child_idx)]
    assert "Credit details" in selected
    assert "Deposit Dynamics" not in selected
    assert "## Risks" not in selected


def test_quoted_compact_targets_preserve_commas_and_and_in_heading_names():
    targets, compact = _action_extract_targets(
        'Target sections: "Market Size, Structure & Growth"; "Financial, Pricing & Unit Economics"\nInstruction: align.'
    )
    assert compact
    assert targets == ["Market Size, Structure & Growth", "Financial, Pricing & Unit Economics"]


def test_single_quoted_compact_target_preserves_heading_with_and():
    targets, compact = _action_extract_targets(
        'Target sections: "Risks and Outlook"\nInstruction: shorten.'
    )
    assert compact
    assert targets == ["Risks and Outlook"]


def test_heading_guard_restores_parent_before_nested_heading():
    result = _ensure_action_heading(
        "### Credit Drivers\nNew analysis [S1].",
        {"level": 2, "heading": "Market Context"},
        "Reorganize the selected section.",
    )
    assert result.startswith("## Market Context\n\n### Credit Drivers")


def test_heading_guard_only_renames_when_explicitly_requested():
    target = {"level": 2, "heading": "Market Context"}
    unchanged = _ensure_action_heading("## New Name\nBody [S1].", target, "Improve this section.")
    renamed = _ensure_action_heading("## New Name\nBody [S1].", target, "Rename the section heading to New Name.")
    assert unchanged.startswith("## Market Context")
    assert renamed.startswith("## New Name")


def test_parent_action_does_not_leave_original_child_sections_duplicated(monkeypatch):
    original = """# Banking report

## Market Context
High-level overview [S1].

### Credit Drivers
Credit growth was 12% [S2].

### Deposit Dynamics
Deposit growth was 9% [S3].

## Risks
Risk outlook remains uncertain [S4].
"""
    candidate = """## Market Context

Overview of the sector [S1].

### Credit Drivers
Credit growth was 12% [S2].

### Deposit Dynamics
Deposit growth was 9% [S3].
"""

    async def no_stored_research(*args, **kwargs):
        return "", []

    async def fake_llm(system_prompt, user_prompt, *args, **kwargs):
        return candidate

    monkeypatch.setattr(report_module, "_action_stored_web_research", no_stored_research)
    monkeypatch.setattr(report_module, "_call_llm_plain", fake_llm)

    response = asyncio.run(_edit_report_section_single_body({
        "report": original,
        "title": "Banking report",
        "editInstruction": 'Target section: "Market Context"\nInstruction: tighten and reorganize this section while preserving evidence.',
        "charts": [],
        "images": [],
        "sources": [],
        "presentation": None,
    }))
    assert response.status_code == 200
    payload = json.loads(response.body.decode("utf-8"))
    updated = payload["report"]
    # Each nested subsection should occur exactly once after the parent Action.
    assert updated.count("### Credit Drivers") == 1
    assert updated.count("Credit growth was 12%") == 1
    assert updated.count("### Deposit Dynamics") == 1
    assert updated.count("Deposit growth was 9%") == 1
    # Unrelated sections remain byte-for-byte in the returned report.
    assert "## Risks\nRisk outlook remains uncertain [S4]." in updated


def test_cross_section_context_does_not_treat_selected_target_as_cross_reference():
    report = """## Market Context
Intro text.
### Credit Drivers
Child text.
## Risk Assessment
Risk text.
"""
    context = _action_relevant_cross_sections(
        report,
        "Market Context",
        'Target section: "Market Context"\nInstruction: make this section more concise.',
    )
    assert context == ""


def test_action_does_not_claim_success_when_requested_chart_cannot_be_created(monkeypatch):
    original = "## Growth Trend\n\nValues were 12% and 14% [S1].\n\n## Risks\n\nRisk remains [S2].\n"
    candidate = "## Growth Trend\n\nThe recorded growth values are 12% and 14% [S1].\n"

    async def no_stored_research(*args, **kwargs):
        return "", []

    async def fake_plain(*args, **kwargs):
        return candidate

    async def no_chart(*args, **kwargs):
        return {"error": "insufficient data"}

    monkeypatch.setattr(report_module, "_action_stored_web_research", no_stored_research)
    monkeypatch.setattr(report_module, "_call_llm_plain", fake_plain)
    monkeypatch.setattr(report_module, "_call_llm_json", no_chart)
    response = asyncio.run(_edit_report_section_single_body({
        "report": original,
        "title": "Growth report",
        "editInstruction": 'Target section: "Growth Trend"\nInstruction: rewrite the prose and add a chart showing the values.',
        "charts": [], "images": [], "sources": [], "presentation": None,
    }))
    assert response.status_code == 422
    payload = json.loads(response.body.decode("utf-8"))
    assert payload.get("preserved") is True
    assert "chart could not be created" in payload.get("error", "").lower()


def test_action_does_not_commit_numeric_edit_if_existing_chart_cannot_sync(monkeypatch):
    original = "## Growth Trend\n\nGrowth was 12% [S1].\n\n[CHART_1]\n"
    candidate = "## Growth Trend\n\nGrowth was 13% [S1].\n\n[CHART_1]\n"
    chart = {
        "type": "bar", "title": "Growth trend", "xLabel": "Year", "yLabel": "%",
        "series": [{"name": "Growth", "data": [
            {"label": "FY24", "value": 10}, {"label": "FY25", "value": 11}, {"label": "FY26", "value": 12}
        ]}],
    }

    async def no_stored_research(*args, **kwargs):
        return "", []

    async def fake_plain(*args, **kwargs):
        return candidate

    async def no_chart(*args, **kwargs):
        return None

    monkeypatch.setattr(report_module, "_action_stored_web_research", no_stored_research)
    monkeypatch.setattr(report_module, "_call_llm_plain", fake_plain)
    monkeypatch.setattr(report_module, "_call_llm_json", no_chart)
    response = asyncio.run(_edit_report_section_single_body({
        "report": original,
        "title": "Growth report",
        "editInstruction": 'Target section: "Growth Trend"\nInstruction: change 12% to 13% and update the chart.',
        "charts": [chart], "images": [], "sources": [], "presentation": None,
    }))
    assert response.status_code == 422
    payload = json.loads(response.body.decode("utf-8"))
    assert payload.get("preserved") is True
    assert "synchronized" in payload.get("error", "").lower()


def test_multi_section_action_commits_all_non_overlapping_targets_atomically(monkeypatch):
    original = "## Alpha\nAlpha text.\n\n## Beta\nBeta text.\n\n## Gamma\nGamma untouched.\n"
    calls = []

    async def fake_single(body):
        calls.append(body["editInstruction"])
        text = body["report"]
        target = "Alpha" if 'Target section: "Alpha"' in body["editInstruction"] else "Beta"
        if target == "Alpha":
            updated = text.replace("Alpha text.", "Alpha updated.")
        else:
            updated = text.replace("Beta text.", "Beta updated.")
        return report_module.JSONResponse({
            "report": updated, "charts": body.get("charts", []), "images": body.get("images", []),
            "sources": body.get("sources", []), "presentation": body.get("presentation"),
            "editedSection": target, "chartUpdated": False,
        })

    class FakeRequest:
        async def json(self):
            return {
                "report": original,
                "editInstruction": 'Target section: "Alpha"\nTarget section: "Beta"\nInstruction: make both concise.',
                "charts": [], "images": [], "sources": [], "presentation": None,
            }

    monkeypatch.setattr(report_module, "_edit_report_section_single_body", fake_single)
    response = asyncio.run(report_module.edit_report_section(FakeRequest()))
    assert response.status_code == 200
    payload = json.loads(response.body.decode("utf-8"))
    assert "Alpha updated." in payload["report"]
    assert "Beta updated." in payload["report"]
    assert "Gamma untouched." in payload["report"]
    assert payload["editedSections"] == ["Alpha", "Beta"]
    assert len(calls) == 2


def test_multi_section_action_rejects_overlapping_parent_and_child_before_llm(monkeypatch):
    original = "## Alpha\nParent text.\n### Alpha child\nChild text.\n## Beta\nBeta text.\n"
    called = False

    async def should_not_run(body):
        nonlocal called
        called = True
        raise AssertionError("Overlapping Action targets should be rejected before model calls")

    class FakeRequest:
        async def json(self):
            return {
                "report": original,
                "editInstruction": 'Target section: "Alpha"\nTarget section: "Alpha child"\nInstruction: update both.',
                "charts": [], "images": [], "sources": [], "presentation": None,
            }

    monkeypatch.setattr(report_module, "_edit_report_section_single_body", should_not_run)
    response = asyncio.run(report_module.edit_report_section(FakeRequest()))
    assert response.status_code == 422
    assert not called
    payload = json.loads(response.body.decode("utf-8"))
    assert "overlap" in payload.get("error", "").lower()
    assert payload.get("preserved") is True


def test_action_can_add_an_additional_chart_without_replacing_existing_chart(monkeypatch):
    original = "## Growth Trend\n\nGrowth was 10%, 11%, and 12% [S1].\n\n[CHART_1]\n"
    candidate = "## Growth Trend\n\nGrowth values were 10%, 11%, and 12% [S1].\n\n[CHART_1]\n"
    existing_chart = {
        "type": "bar", "title": "Existing growth", "xLabel": "Year", "yLabel": "%",
        "series": [{"name": "Growth", "data": [
            {"label": "FY24", "value": 10}, {"label": "FY25", "value": 11}, {"label": "FY26", "value": 12}
        ]}],
    }
    additional_chart = {
        "type": "line", "title": "Growth trajectory", "xLabel": "Year", "yLabel": "%",
        "series": [{"name": "Growth", "data": [
            {"label": "FY24", "value": 10}, {"label": "FY25", "value": 11}, {"label": "FY26", "value": 12}
        ]}],
    }
    calls = []

    async def no_stored_research(*args, **kwargs):
        return "", []

    async def fake_plain(*args, **kwargs):
        return candidate

    async def fake_json(system_prompt, user_prompt, *args, **kwargs):
        calls.append(system_prompt)
        if "additional chart" in system_prompt.lower():
            return additional_chart
        return existing_chart

    monkeypatch.setattr(report_module, "_action_stored_web_research", no_stored_research)
    monkeypatch.setattr(report_module, "_call_llm_plain", fake_plain)
    monkeypatch.setattr(report_module, "_call_llm_json", fake_json)
    response = asyncio.run(_edit_report_section_single_body({
        "report": original,
        "title": "Growth report",
        "editInstruction": 'Target section: "Growth Trend"\nInstruction: rephrase the text and add a chart for the growth values.',
        "charts": [existing_chart], "images": [], "sources": [], "presentation": None,
    }))
    assert response.status_code == 200
    payload = json.loads(response.body.decode("utf-8"))
    assert len(payload["charts"]) == 2
    assert "[CHART_1]" in payload["report"]
    assert "[CHART_2]" in payload["report"]
    assert payload["chartUpdated"] is True
    assert any("additional chart" in prompt.lower() for prompt in calls)
