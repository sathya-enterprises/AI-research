"""Regression tests for Action visual alternatives and reliable chart/table completion."""
import asyncio
import json
from pathlib import Path

import routes.report as report_module


def test_table_or_chart_is_an_alternative_not_a_mandatory_chart_request():
    prompt = "Add a useful comparison table or chart where supported by the available data."
    assert report_module._action_requests_visual_alternative(prompt)
    assert not report_module._action_requests_chart_addition(prompt)
    assert report_module._action_requests_chart_addition("Add a chart showing the three supported values.")



def test_conditional_chart_with_explicit_table_fallback_is_an_alternative():
    prompt = (
        "Add or improve a useful comparison table using supported evidence. "
        "Create a chart only when sufficient reliable data is available; if not, "
        "use a suitable table instead of failing the entire Action."
    )
    assert report_module._action_requests_visual_alternative(prompt)
    assert not report_module._action_requests_chart_addition(prompt)

    variants = [
        "Create a chart only if enough supported data exists; otherwise use a comparison table.",
        "If chart creation fails validation, fall back to a table rather than aborting the Action.",
        "Use a suitable table if a chart cannot be created safely.",
    ]
    for variant in variants:
        assert report_module._action_requests_visual_alternative(variant), variant
        assert not report_module._action_requests_chart_addition(variant), variant


def test_multi_section_prompt_from_live_failure_does_not_require_chart_json(monkeypatch):
    """Reproduce the exact visual clause that triggered the live 422 error."""
    instruction = (
        "Update the existing report's **Investment, Costs & Project Economics** and "
        "**Policy, Regulation & Grid Infrastructure** sections.\n\n"
        "Improve financing, cost of capital, investment requirements, tariffs and project economics. "
        "Consolidate repeated transmission bottlenecks and regulatory mechanisms.\n\n"
        "Add or improve a useful comparison table using supported evidence. Create a chart only when "
        "sufficient reliable data is available; if not, use a suitable table instead of failing the entire Action.\n\n"
        "Preserve unrelated report content and validate both edits before committing."
    )
    assert report_module._action_requests_visual_alternative(instruction)
    assert not report_module._action_requests_chart_addition(instruction)


def test_chart_or_table_alternative_reuses_existing_table_instead_of_failing_on_chart_json(monkeypatch):
    original = (
        "## Investment, Costs & Project Economics\n\n"
        "Project financing and tariff assumptions shape expected returns [S1].\n\n"
        "| Dimension | Supported observation |\n|---|---|\n"
        "| Financing | Cost of capital affects returns [S1] |\n\n"
        "## Policy, Regulation & Grid Infrastructure\n\n"
        "Transmission constraints can delay projects [S2].\n"
    )
    candidate = (
        "## Investment, Costs & Project Economics\n\n"
        "The revised analysis distinguishes financing risk from tariff and execution risk [S1].\n\n"
        "| Dimension | Supported observation |\n|---|---|\n"
        "| Financing | Cost of capital affects returns [S1] |\n\n"
    )

    async def no_stored(*args, **kwargs):
        return "", []

    async def plain(system, user, *args, **kwargs):
        return candidate

    async def json_must_not_run(*args, **kwargs):
        raise AssertionError("A chart provider should not be called for a table-or-chart alternative")

    monkeypatch.setattr(report_module, "_action_stored_web_research", no_stored)
    monkeypatch.setattr(report_module, "_action_requires_external_research", lambda *args, **kwargs: False)
    monkeypatch.setattr(report_module, "_call_llm_plain", plain)
    monkeypatch.setattr(report_module, "_call_llm_json", json_must_not_run)

    class Request:
        async def json(self):
            return {
                "report": original,
                "title": "Renewable energy outlook",
                "editInstruction": (
                    'Target section: "Investment, Costs & Project Economics"\n'
                    "Improve the project financing analysis and add a useful comparison table or chart where supported by data."
                ),
                "charts": [], "images": [], "sources": [], "presentation": None,
            }

    response = asyncio.run(report_module.edit_report_section(Request()))
    payload = json.loads(response.body.decode("utf-8"))
    assert response.status_code == 200, payload
    assert "revised analysis distinguishes financing risk" in payload["report"]
    assert "Transmission constraints can delay projects [S2]." in payload["report"]
    assert payload.get("visualSatisfied") is True
    assert not payload.get("error")


def test_evidence_only_table_fallback_uses_existing_values_and_citations():
    section = (
        "## Investment\n\n"
        "Project investment needs to reach USD 300 billion by 2032 under the sector plan [S1].\n\n"
        "Financing cost has averaged around 10% in the current project sample [S2].\n\n"
        "Transmission delays can add project risk and raise costs [S3]."
    )
    table = report_module._action_supported_evidence_table(section)
    assert table.startswith("| Evidence dimension | Supported finding from the report |")
    assert "USD 300 billion" in table
    assert "10%" in table
    assert "[S1]" in table and "[S2]" in table
    valid, problems = report_module._validate_new_action_table(section, table, evidence_source=section)
    assert valid, problems


def test_table_or_chart_alternative_can_pass_validation_without_table_if_evidence_does_not_support_one():
    original = "## Section\n\nThis qualitative statement is preserved [S1]."
    edited = "## Section\n\nThe qualitative assessment is clearer and remains source-backed [S1]."
    ok, problems = report_module._action_edit_validation(
        original, edited,
        "Improve this section and add a useful comparison table or chart where supported by data.",
        evidence_source=original,
    )
    assert ok, problems


def test_active_frontends_keep_icon_image_aspect_ratio_without_auto_dimension_warning():
    root = Path(__file__).parents[2]
    for path in (
        root / "frontend" / "src" / "components" / "GrowthGradualChat.tsx",
        root / "backend" / "frontend" / "src" / "components" / "GrowthGradualChat.tsx",
    ):
        src = path.read_text(encoding="utf-8")
        assert "width={32} height={32} style={{ objectFit:'contain', width:32, height:'auto' }}" in src
        assert "width={30} height={30} style={{ objectFit:'contain', width:30, height:'auto'" in src
        assert "width={28} height={28} style={{ objectFit:'contain', width:28, height:'auto' }}" in src


def test_two_section_action_with_table_or_chart_alternative_commits_atomically_without_chart_provider(monkeypatch):
    original = (
        "## 3. Investment, Costs & Project Economics\n\n"
        "Financing assumptions and tariffs shape project economics [S1].\n\n"
        "| Dimension | Evidence |\n|---|---|\n| Cost of capital | Supported analysis [S1] |\n\n"
        "## 4. Policy, Regulation & Grid Infrastructure\n\n"
        "Grid delays and unsigned power sale agreements threaten implementation [S2].\n\n"
        "| Constraint | Evidence |\n|---|---|\n| Transmission | Supported finding [S2] |\n\n"
        "## 5. Unrelated Section\n\nKeep this section byte-for-byte unchanged [S3].\n"
    )

    async def no_stored(*args, **kwargs):
        return "", []

    async def plain(system, user, *args, **kwargs):
        user_text = str(user)
        selected_section = user_text.split("SECTION TO EDIT:", 1)[1].split("\n\nINSTRUCTION:", 1)[0] if "SECTION TO EDIT:" in user_text else ""
        if "Investment, Costs & Project Economics" in selected_section:
            return (
                "## 3. Investment, Costs & Project Economics\n\n"
                "The revised section separates financing exposure, tariff assumptions, and execution risk [S1].\n\n"
                "| Dimension | Evidence |\n|---|---|\n| Cost of capital | Supported analysis [S1] |"
            )
        if "Policy, Regulation & Grid Infrastructure" in selected_section:
            return (
                "## 4. Policy, Regulation & Grid Infrastructure\n\n"
                "The revised section consolidates transmission delays and offtake agreement risks [S2].\n\n"
                "| Constraint | Evidence |\n|---|---|\n| Transmission | Supported finding [S2] |"
            )
        return "COMPLETE"

    async def json_must_not_run(*args, **kwargs):
        raise AssertionError("The optional chart branch must not run when a table is an accepted alternative")

    monkeypatch.setattr(report_module, "_action_stored_web_research", no_stored)
    monkeypatch.setattr(report_module, "_action_requires_external_research", lambda *args, **kwargs: False)
    monkeypatch.setattr(report_module, "_call_llm_plain", plain)
    monkeypatch.setattr(report_module, "_call_llm_json", json_must_not_run)

    class Request:
        async def json(self):
            return {
                "report": original,
                "title": "India renewable energy outlook",
                "editInstruction": (
                    "Update the existing report's **Investment, Costs & Project Economics** and "
                    "**Policy, Regulation & Grid Infrastructure** sections. Improve their analysis, "
                    "remove repeated subsections, and add or improve a useful comparison table using "
                    "supported evidence. Create a chart only when sufficient reliable data is available; "
                    "if not, use a suitable table instead of failing the entire Action. "
                    "Preserve unrelated sections and citations."
                ),
                "charts": [], "images": [], "sources": [], "presentation": None,
            }

    response = asyncio.run(report_module.edit_report_section(Request()))
    payload = json.loads(response.body.decode("utf-8"))
    assert response.status_code == 200, payload
    assert payload.get("multiSectionEdit") is True
    assert payload.get("editedSections") == [
        "3. Investment, Costs & Project Economics",
        "4. Policy, Regulation & Grid Infrastructure",
    ]
    assert "separates financing exposure" in payload["report"]
    assert "consolidates transmission delays" in payload["report"]
    assert "Keep this section byte-for-byte unchanged [S3]." in payload["report"]
    assert payload.get("visualSatisfied") is True
