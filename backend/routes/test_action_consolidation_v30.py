"""Regression tests for Action-time, evidence-safe repetition consolidation."""
import asyncio
import json

import routes.report as report_module


def test_action_repetition_scope_identifies_finance_and_policy_grid_topics():
    prompt = (
        "Update Investment, Costs & Project Economics and Policy, Regulation & Grid Infrastructure. "
        "Remove repeated subsections discussing financing risks and consolidate repetitive transmission bottleneck analysis."
    )
    topics = report_module._action_dedupe_topic_scope(
        prompt,
        ["Investment, Costs & Project Economics", "Policy, Regulation & Grid Infrastructure"],
    )
    assert "investment_finance" in topics
    assert "policy_grid" in topics


def test_action_consolidates_repeated_relevant_subsections_but_leaves_unrelated_content_alone():
    report = """# Renewable Energy Outlook

## Investment, Costs & Project Economics
Intro on project economics [S1].

### Investment Risk and Cost of Capital Sensitivities
Renewable developers need USD 300 billion in cumulative investments by 2032 [S1]. Elevated cost of capital and commissioning delays can threaten project returns and the 2030 capacity trajectory [S1].

## Policy, Regulation & Grid Infrastructure
Policy overview [S2].

### Transmission Bottlenecks and Grid Integration
Transmission corridors face congestion, unsigned power sale agreements, and delayed grid interconnection. More than 40 GW of capacity is stalled by unsigned agreements [S2].

### Regulatory Transmission Mechanisms and Interconnection Frameworks
Transmission corridors face congestion, unsigned power sale agreements, and delayed grid interconnection. More than 40 GW of capacity is stalled by unsigned agreements [S2].

## Other Research Topic

### Workforce Planning
Workforce availability is an issue for manufacturers and project operators, and this paragraph discusses staffing and recruitment [S9].

### Workforce Planning
Workforce availability is an issue for manufacturers and project operators, and this paragraph discusses staffing and recruitment [S9].
"""
    topics = {"investment_finance", "policy_grid"}
    cleaned = report_module._dedupe_redundant_report_content(report, relevant_topics=topics)
    assert cleaned.count("### Transmission Bottlenecks and Grid Integration") == 1
    assert cleaned.count("### Regulatory Transmission Mechanisms and Interconnection Frameworks") == 0
    assert cleaned.count("### Workforce Planning") == 2
    assert "USD 300 billion" in cleaned
    assert "40 GW" in cleaned
    assert "[S1]" in cleaned and "[S2]" in cleaned


def test_action_consolidation_keeps_modules_with_new_evidence_or_unique_visual_assets():
    report = """## Investment, Costs & Project Economics

### Investment Risk Sensitivities
Financing costs are material to renewable projects; the planning figure is USD 300 billion by 2032 [S1].

### Investment Risk Sensitivities
Financing costs are material to renewable projects; the planning figure is USD 300 billion by 2032 [S1]. A separate 44 GW scenario was reported by another source [S7].

### Investment Risk Sensitivities
Financing costs are material to renewable projects; the planning figure is USD 300 billion by 2032 [S1].

[CHART_8]
"""
    cleaned = report_module._dedupe_repeated_subsections(report, relevant_topics={"investment_finance"})
    # A new figure and source prevent the second module being discarded. A unique
    # chart placeholder prevents the third module from being discarded either.
    assert "44 GW scenario" in cleaned
    assert "[S7]" in cleaned
    assert "[CHART_8]" in cleaned
    assert cleaned.count("### Investment Risk Sensitivities") >= 2


def test_action_response_applies_topic_scoped_cleanup_after_success(monkeypatch):
    sources = [
        {
            "id": "S1", "title": "Investment planning evidence",
            "url": "https://example.test/investment", "pageFetchStatus": "ok",
            "evidenceOrigin": "publisher_page",
            "pageContent": (
                "The planning scenario estimates that renewable developers need USD 300 billion in cumulative "
                "investments by 2032. Higher project costs and delays can threaten project returns. "
                "This is a planning scenario for the sector and is not a guaranteed funding outcome. "
                "The scenario describes the investment scale over the stated period and its delivery risks."
            ),
        },
        {
            "id": "S2", "title": "Grid connection status",
            "url": "https://example.test/grid", "pageFetchStatus": "ok",
            "evidenceOrigin": "publisher_page",
            "pageContent": (
                "More than 40 GW of renewable generation capacity remains stalled by unsigned grid connection "
                "agreements. The report attributes delays to transmission planning and interconnection processes. "
                "These figures describe the identified capacity at the time of the report, not all future projects. "
                "The source also explains that transmission buildout and agreement execution affect when projects can connect."
            ),
        },
        {"id": "S3", "title": "Other research note", "url": "internal://note/3", "sourceType": "uploaded document"},
    ]
    original = """## Investment, Costs & Project Economics
Original investment intro [S1].

## Policy, Regulation & Grid Infrastructure
Original policy intro [S2].

## Other Research Topic
Keep all unrelated content [S3].
"""
    updated = """## Investment, Costs & Project Economics
Updated investment analysis [S1].

### Investment Risk Sensitivities
Renewable developers need USD 300 billion in cumulative investments by 2032 [S1]. Higher costs and delays can threaten project returns [S1].

### Investment Risk Mitigation
Renewable developers need USD 300 billion in cumulative investments by 2032 [S1]. Higher costs and delays can threaten project returns [S1].

## Policy, Regulation & Grid Infrastructure
Updated policy analysis [S2].

### Transmission Bottlenecks and Grid Integration
Transmission corridors face congestion and delayed grid interconnection; more than 40 GW remains stalled by unsigned agreements [S2].

### Regulatory Transmission Mechanisms
Transmission corridors face congestion and delayed grid interconnection; more than 40 GW remains stalled by unsigned agreements [S2].

## Other Research Topic
Keep all unrelated content [S3].
"""

    async def fake_single(body):
        return report_module.JSONResponse({
            "report": updated,
            "title": "Renewable outlook",
            "charts": [], "images": [], "sources": sources, "presentation": None,
            "editedSection": "Investment, Costs & Project Economics",
        })

    async def not_used(*args, **kwargs):
        raise AssertionError("This is a mocked successful single Action")

    monkeypatch.setattr(report_module, "_edit_report_section_single_body", fake_single)

    class Request:
        async def json(self):
            return {
                "report": original,
                "editInstruction": (
                    "Target section: \"Investment, Costs & Project Economics\"\n"
                    "Improve this section and remove repeated subsections discussing the same financing risks."
                ),
                "charts": [], "images": [], "sources": sources, "presentation": None,
            }

    response = asyncio.run(report_module.edit_report_section(Request()))
    payload = json.loads(response.body.decode("utf-8"))
    assert response.status_code == 200
    assert payload.get("repetitionCleanupApplied") is True
    assert payload["report"].count("### Investment Risk") == 1
    # A single-section Action should not silently rewrite the other selected-
    # report area; its duplicate grid modules are outside this Action's scope.
    assert payload["report"].count("### Transmission Bottlenecks") == 1
    assert payload["report"].count("### Regulatory Transmission Mechanisms") == 1
    assert "Keep all unrelated content [S3]." in payload["report"]


def test_multi_section_action_consolidates_both_requested_topics_atomically(monkeypatch):
    updated = """## Investment, Costs & Project Economics
Updated financing analysis [S1].

### Investment Risk Sensitivities
Renewable developers need USD 300 billion in cumulative investments by 2032 [S1]. Higher costs and delays can threaten project returns [S1].

### Investment Risk Mitigation
Renewable developers need USD 300 billion in cumulative investments by 2032 [S1]. Higher costs and delays can threaten project returns [S1].

## Policy, Regulation & Grid Infrastructure
Updated policy analysis [S2].

### Transmission Bottlenecks and Grid Integration
Transmission corridors face congestion, unsigned power sale agreements, and delayed grid interconnection. More than 40 GW of capacity is stalled by unsigned agreements [S2].

### Regulatory Transmission Mechanisms and Interconnection Frameworks
Transmission corridors face congestion, unsigned power sale agreements, and delayed grid interconnection. More than 40 GW of capacity is stalled by unsigned agreements [S2].

## Other Research Topic
Keep this section unchanged [S3].

### Workforce Planning
Workforce availability is an issue for manufacturers and project operators [S9].

### Workforce Planning
Workforce availability is an issue for manufacturers and project operators [S9].
"""

    async def fake_single(body):
        return report_module.JSONResponse({
            "report": updated, "charts": [], "images": [], "sources": [],
            "presentation": None, "editedSection": "mocked section",
        })

    monkeypatch.setattr(report_module, "_edit_report_section_single_body", fake_single)

    class Request:
        async def json(self):
            return {
                "report": updated,
                "editInstruction": (
                    'Target section: "Investment, Costs & Project Economics"\n'
                    'Target section: "Policy, Regulation & Grid Infrastructure"\n'
                    "Improve both sections, remove repeated subsections discussing financing risks, "
                    "and consolidate repetitive discussions of transmission bottlenecks and regulatory mechanisms."
                ),
                "charts": [], "images": [], "sources": [], "presentation": None,
            }

    response = asyncio.run(report_module.edit_report_section(Request()))
    payload = json.loads(response.body.decode("utf-8"))
    assert response.status_code == 200
    assert payload.get("multiSectionEdit") is True
    assert payload.get("repetitionCleanupApplied") is True
    assert payload["report"].count("### Investment Risk") == 1
    assert payload["report"].count("### Transmission Bottlenecks") == 1
    assert payload["report"].count("### Regulatory Transmission Mechanisms") == 0
    assert payload["report"].count("### Workforce Planning") == 2
    assert "Keep this section unchanged [S3]." in payload["report"]
    assert {"S1", "S2", "S3", "S9"}.issubset(set(report_module._action_protected_evidence(payload["report"])["citations"]))


def test_action_without_explicit_repeat_cleanup_does_not_run_global_cleanup(monkeypatch):
    updated = "## Alpha\n\nUpdated text [S1].\n\n## Beta\n\nBeta text [S2].\n"

    async def fake_single(body):
        return report_module.JSONResponse({"report": updated, "editedSection": "Alpha"})

    monkeypatch.setattr(report_module, "_edit_report_section_single_body", fake_single)

    class Request:
        async def json(self):
            return {
                "report": "## Alpha\nAlpha text [S1].\n## Beta\nBeta text [S2].\n",
                "editInstruction": 'Target section: "Alpha"\nInstruction: Improve the section wording.',
            }

    response = asyncio.run(report_module.edit_report_section(Request()))
    payload = json.loads(response.body.decode("utf-8"))
    assert response.status_code == 200
    assert "repetitionCleanupApplied" not in payload
    assert payload["report"] == updated
