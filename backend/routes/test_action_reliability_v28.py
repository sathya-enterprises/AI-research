"""Regression tests for fresh-source citation handoff and Action research robustness."""
import asyncio
from pathlib import Path

import pytest

import routes.report as report_module


def test_action_research_query_starts_with_target_and_keeps_specific_dimensions():
    query = report_module._action_build_research_query(
        "Update the existing report's **Investment, Costs & Project Economics** and **Policy, Regulation & Grid Infrastructure** sections. "
        "Improve the analysis of project financing, cost of capital, investment requirements, tariffs, and project economics. "
        "Preserve the existing section hierarchy, relevant subsections, numerical evidence, citations, and source links.",
        "3. Investment, Costs & Project Economics",
        "Growth Outlook of India Renewable Energy Sector 2026-2030",
    )
    assert query.startswith("Investment, Costs & Project Economics")
    assert "financing" in query.lower()
    assert "tariffs" in query.lower()
    assert "Growth Outlook" in query
    assert len(query) <= 240
    assert not query.endswith("&")


def test_action_research_query_scopes_multi_target_prompt_to_policy_section():
    instruction = """Update the existing report's **Investment, Costs & Project Economics** and **Policy, Regulation & Grid Infrastructure** sections.

In Investment, Costs & Project Economics, improve project financing and tariffs.

In Policy, Regulation & Grid Infrastructure, improve transmission constraints, unsigned power sale agreements, grid stability, and implementation challenges."""
    query = report_module._action_build_research_query(
        instruction, "4. Policy, Regulation & Grid Infrastructure", "Renewable energy outlook"
    ).lower()
    assert query.startswith("policy, regulation & grid infrastructure")
    assert "transmission" in query and "agreements" in query and "grid stability" in query
    assert "project financing" not in query


def test_fresh_source_ids_only_read_actual_evidence_block_headers():
    context = """[S21] Renewable financing cost report
URL: https://example.com/a
Evidence: Cost of capital and tariff evidence.

[S23] Grid connection report
URL: https://example.com/b
Evidence: Transmission constraint evidence."""
    assert report_module._action_fresh_source_ids(context) == ["S21", "S23"]


def test_action_fresh_citation_mapper_attaches_source_without_rewriting_prose(monkeypatch):
    candidate = (
        "## 3. Investment, Costs & Project Economics\n\n"
        "Renewable project financing costs are sensitive to capital costs and tariff assumptions.\n\n"
        "Developers should assess commissioning risk separately."
    )
    evidence = """[S21] Renewable financing costs
URL: https://example.com/financing
Evidence: Renewable project financing costs depend on capital costs, tariffs and project-specific execution assumptions."""

    async def fake_call(system, user):
        assert "FRESH SOURCES" in user
        return '{"citations":[{"block":2,"source_ids":["S21"]}]}'

    monkeypatch.setattr(report_module, "_call_llm_plain", fake_call)
    actual = asyncio.run(report_module._action_attach_fresh_citations(
        candidate, evidence, "Refresh the financing analysis with fresh sources", "3. Investment, Costs & Project Economics"
    ))
    assert "Renewable project financing costs are sensitive to capital costs and tariff assumptions. [S21]" in actual
    assert "Developers should assess commissioning risk separately." in actual
    assert actual.count("[S21]") == 1


def test_action_fresh_citation_falls_back_to_specific_evidence_overlap(monkeypatch):
    candidate = (
        "## 3. Investment, Costs & Project Economics\n\n"
        "Solar project financing costs and capital costs affect tariffs and the levelized cost of electricity.\n\n"
        "The report also discusses unrelated workforce planning."
    )
    evidence = """[S21] Solar financing and tariff evidence
URL: https://example.com/solar
Evidence: Solar project financing costs and capital costs affect tariff assumptions and levelized cost of electricity for developers."""

    async def fake_call(system, user):
        return "not valid json"

    monkeypatch.setattr(report_module, "_call_llm_plain", fake_call)
    actual = asyncio.run(report_module._action_attach_fresh_citations(
        candidate, evidence, "Use fresh web research", "3. Investment, Costs & Project Economics"
    ))
    assert "[S21]" in actual
    assert "The report also discusses unrelated workforce planning." in actual


def test_action_research_store_persistence_handles_both_unique_keys_idempotently():
    source = (Path(__file__).parents[1] / "utils" / "research_store.py").read_text(encoding="utf-8")
    # Regression for UniqueViolation: source_key can collide independently from canonical_url.
    assert "ON CONFLICT DO NOTHING" in source
    assert "WHERE canonical_url=%s OR source_key=%s" in source
    assert "ORDER BY (canonical_url=%s) DESC" in source


def test_action_research_persistence_sanitizes_nuls_in_query_and_source_content():
    source = (Path(__file__).parents[1] / "utils" / "research_store.py").read_text(encoding="utf-8")
    persist = source.split("def persist_web_search_results(", 1)[1].split("def retrieve_web_research(", 1)[0]
    assert 'query_text = _clean_pg_text(query or "")' in persist
    assert "title = _clean_pg_text" in persist
    assert "content = _clean_pg_text" in persist


def test_top_level_runtime_frontend_uses_async_action_routes_not_sync_edit_endpoint():
    project_root = Path(__file__).parents[2]
    component = (project_root / "frontend" / "src" / "components" / "GrowthGradualChat.tsx").read_text(encoding="utf-8")
    assert "'/api/chat/report/edit/start'" in component
    assert "`/api/chat/report/edit/status/" in component
    assert "fetch('/api/chat/report/edit'," not in component
    assert (project_root / "frontend" / "src" / "app" / "api" / "chat" / "report" / "edit" / "start" / "route.ts").exists()
    assert (project_root / "frontend" / "src" / "app" / "api" / "chat" / "report" / "edit" / "status" / "[job_id]" / "route.ts").exists()


def test_single_action_does_not_422_when_fresh_citation_handoff_can_map_support(monkeypatch):
    original = "## Investment\n\nExisting project finance analysis remains relevant [S1].\n\n## Unrelated\nKeep this untouched [S3].\n"
    fresh_context = (
        "[S2] Renewable project financing evidence\n"
        "URL: https://example.com/renewable-finance\n"
        "Evidence: Renewable project financing costs, cost of capital and tariff assumptions influence project viability."
    )
    source_manifest = [
        {"id": "S1", "title": "Existing report source", "url": "https://example.com/old"},
        {"id": "S2", "title": "Renewable project financing evidence", "url": "https://example.com/renewable-finance"},
    ]
    calls = []

    async def fake_stored(_instruction, _heading, _title):
        return "", []

    async def fake_web(_instruction, _heading, _title, _sources):
        return fresh_context, source_manifest

    async def fake_llm(system, user):
        calls.append((system, user))
        if "You are a citation mapper" in system:
            return '{"citations":[{"block":2,"source_ids":["S2"]}]}'
        return (
            "## Investment\n\n"
            "The updated assessment should compare renewable project financing costs, cost of capital and tariff assumptions. [S1]"
        )

    monkeypatch.setattr(report_module, "_action_stored_web_research", fake_stored)
    monkeypatch.setattr(report_module, "_action_web_research", fake_web)
    monkeypatch.setattr(report_module, "_call_llm_plain", fake_llm)

    class Request:
        async def json(self):
            return {
                "report": original,
                "title": "Renewable energy outlook",
                "editInstruction": 'Target section: "Investment"\nUpdate this section with fresh web research on renewable project financing, cost of capital and tariffs.',
                "sources": [{"id": "S1", "title": "Existing report source", "url": "https://example.com/old"}],
                "charts": [], "images": [], "presentation": None,
            }

    response = asyncio.run(report_module.edit_report_section(Request()))
    import json
    payload = json.loads(response.body.decode("utf-8"))
    assert response.status_code == 200, payload
    assert "cost of capital and tariff assumptions. [S1] [S2]" in payload["report"]
    assert "Keep this untouched [S3]." in payload["report"]
    assert any(x.get("id") == "S2" for x in payload["sources"])
    assert len(calls) == 2
