"""v31 regressions for request-level visual alternatives and repeated research prose."""
import asyncio
import json

import routes.report as report_module


FULL_ACTION_PROMPT = '''Update the existing report's **Investment, Costs & Project Economics** and **Policy, Regulation & Grid Infrastructure** sections.

Improve the analysis and consolidate repetitive financing risks, transmission bottlenecks, and regulatory mechanisms.

Add or improve a useful comparison table using supported evidence. Create a chart only when sufficient reliable data is available; if not, use a suitable table instead of failing the entire Action.

Preserve unrelated content and validate both edits before committing them.'''


def test_visual_alternative_override_survives_shortened_target_instruction():
    # This directly models the production failure: the top-level prompt allows a
    # table fallback, but a shortened per-section string may mention only charts.
    assert report_module._action_requests_visual_alternative(FULL_ACTION_PROMPT)
    assert not report_module._action_requests_chart_addition("Create an additional chart", visual_alternative=True)
    assert report_module._action_visual_alternative_requested({"_action_visual_alternative": True}, "Create an additional chart")


def test_multi_section_orchestrator_propagates_visual_alternative_flag(monkeypatch):
    report = (
        "## Investment, Costs & Project Economics\n\nFinance text [S1].\n\n"
        "## Policy, Regulation & Grid Infrastructure\n\nGrid text [S2].\n"
    )
    seen = []

    async def fake_single(body):
        seen.append(body.get("_action_visual_alternative"))
        return report_module.JSONResponse({
            "report": body.get("report", report),
            "charts": body.get("charts", []),
            "images": body.get("images", []),
            "sources": body.get("sources", []),
            "presentation": body.get("presentation"),
            "editedSection": "mocked",
            "visualSatisfied": True,
        })

    monkeypatch.setattr(report_module, "_edit_report_section_single_body", fake_single)

    class Request:
        async def json(self):
            return {
                "report": report,
                "editInstruction": FULL_ACTION_PROMPT,
                "charts": [], "images": [], "sources": [], "presentation": None,
            }

    response = asyncio.run(report_module.edit_report_section(Request()))
    payload = json.loads(response.body.decode("utf-8"))
    assert response.status_code == 200
    assert payload.get("multiSectionEdit") is True
    assert seen == [True, True]


def test_repeated_risk_sentences_are_removed_but_unique_evidence_is_kept():
    repeated = (
        "Elevated financing costs and commissioning delays can increase project costs and threaten the pace of renewable capacity delivery [S1]."
    )
    report = f'''# Renewable Energy Outlook

## Investment, Costs & Project Economics

### Investment Risk Sensitivities
{repeated} A distinct financing note records USD 300 billion of investment needed by 2032 [S1].

### Investment Risk Mitigation
{repeated} A different source reports a separate 44 GW deployment scenario [S7].

## Policy, Regulation & Grid Infrastructure

### Grid Bottlenecks
Transmission congestion and unsigned power sale agreements delay grid connection; more than 40 GW remains stalled [S2].

### Regulatory Transmission Mechanisms
Transmission congestion and unsigned power sale agreements delay grid connection; more than 40 GW remains stalled [S2]. A unique policy note is recorded in 2026 [S8].

## Other Section

### Workforce Planning
Workforce availability and recruiting remain separate operational considerations [S9].
'''
    topics = {"investment_finance", "policy_grid"}
    cleaned = report_module._dedupe_redundant_report_content(report, relevant_topics=topics)
    assert cleaned.count(repeated) == 1
    assert "USD 300 billion" in cleaned
    assert "44 GW deployment scenario" in cleaned
    assert "more than 40 GW remains stalled" in cleaned
    assert "unique policy note is recorded in 2026 [S8]" in cleaned
    assert "Workforce availability and recruiting remain separate operational considerations [S9]." in cleaned


def test_duplicate_subsections_with_equivalent_risk_text_are_consolidated():
    repeated = (
        "Transmission corridors experience congestion and unsigned power sale agreements delay commissioning; more than 40 GW of renewable capacity remains stalled [S2]."
    )
    report = f'''## Policy, Regulation & Grid Infrastructure

### Transmission Bottlenecks and Grid Integration
{repeated}

### Regulatory Transmission Mechanisms and Interconnection Frameworks
{repeated}

### Grid Stability and Storage
Battery storage and pumped storage provide balancing capability for variable generation [S7].
'''
    cleaned = report_module._dedupe_redundant_report_content(report, relevant_topics={"policy_grid"})
    assert cleaned.count(repeated) == 1
    assert cleaned.count("### Transmission Bottlenecks and Grid Integration") == 1
    assert "### Grid Stability and Storage" in cleaned
    assert "[S2]" in cleaned and "[S7]" in cleaned


def test_repetition_cleanup_never_edits_an_unselected_section_even_when_topic_overlaps():
    finance_sentence = (
        "Elevated financing costs and commissioning delays can increase project costs and threaten the pace of renewable capacity delivery [S1]."
    )
    grid_sentence = (
        "Transmission congestion and unsigned power sale agreements delay grid connection; more than 40 GW remains stalled [S2]."
    )
    unrelated_grid_sentence = (
        "Transmission congestion and unsigned power sale agreements delay grid connection; more than 40 GW remains stalled [S2]."
    )
    report = f'''## Investment, Costs & Project Economics

### Investment Risk Sensitivities
{finance_sentence}

### Investment Risk Mitigation
{finance_sentence}

## Policy, Regulation & Grid Infrastructure

### Grid Bottlenecks
{grid_sentence}

### Regulatory Transmission Mechanisms
{grid_sentence}

## Storage & Grid Operations

### Grid Operations Risk
{unrelated_grid_sentence}

### Grid Operations Mitigation
{unrelated_grid_sentence}
'''
    prompt = (
        "Update Investment, Costs & Project Economics and Policy, Regulation & Grid Infrastructure. "
        "Remove repeated financing risks and consolidate repetitive transmission bottlenecks."
    )
    response = report_module.JSONResponse({
        "report": report,
        "editedSections": ["Investment, Costs & Project Economics", "Policy, Regulation & Grid Infrastructure"],
        "editedSection": "Investment, Costs & Project Economics, Policy, Regulation & Grid Infrastructure",
    })
    cleaned_response = report_module._action_consolidation_response(response, prompt)
    payload = json.loads(cleaned_response.body.decode("utf-8"))
    cleaned = payload["report"]
    assert cleaned.count(finance_sentence) == 1
    policy_block = cleaned.split("## Policy, Regulation & Grid Infrastructure", 1)[1].split("## Storage & Grid Operations", 1)[0]
    storage_block = cleaned.split("## Storage & Grid Operations", 1)[1]
    assert policy_block.count(grid_sentence) == 1
    # The unselected storage/grid section is deliberately left untouched.
    assert storage_block.count(unrelated_grid_sentence) == 2
    assert payload.get("repetitionCleanupApplied") is True
