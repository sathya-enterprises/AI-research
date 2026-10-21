import asyncio
import json

from fastapi.responses import JSONResponse

from routes import report as report_mod


def test_successful_action_keeps_report_id_and_advances_revision(monkeypatch):
    async def audit_candidate(**_kwargs):
        return (
            {
                "publicationAudit": {"flags": []},
                "sourceEvidenceAudit": {"blocking": False, "flags": []},
                "chartDensity": {"exceptionsOrMisses": []},
                "structure": {"status": "PASS"},
            },
            [{"id": "S1", "page_fetch_status": "ok"}],
            [],
        )

    monkeypatch.setattr(report_mod, "_audit_action_candidate", audit_candidate)
    response = asyncio.run(report_mod._finalize_action_response(
        JSONResponse({"report": "# Outlook\n\nUpdated supported text [S1].", "editedSection": "Outlook"}),
        {
            "report": "# Outlook\n\nOriginal supported text [S1].",
            "reportId": "report-original",
            "revision": 3,
            "reportTier": "brief",
        },
    ))

    result = json.loads(response.body)
    assert response.status_code == 200
    assert result["reportId"] == "report-original"
    assert result["revision"] == 4
    assert result["publicationStatus"] == "PASS"
    assert result["auditSummary"]["publisherPagesVerified"] == 1


def test_action_introducing_new_blocker_preserves_previous_revision(monkeypatch):
    async def audit_candidate(**_kwargs):
        return (
            {"publicationAudit": {"flags": []}, "sourceEvidenceAudit": {"blocking": True}},
            [],
            ["numeric_evidence:unsupported-claim"],
        )

    monkeypatch.setattr(report_mod, "_audit_action_candidate", audit_candidate)
    response = asyncio.run(report_mod._finalize_action_response(
        JSONResponse({"report": "# Outlook\n\nUnsupported new claim: 93 GW [S1]."}),
        {
            "report": "# Outlook\n\nVerified prior version [S1].",
            "reportId": "report-original",
            "revision": 3,
        },
    ))

    result = json.loads(response.body)
    assert response.status_code == 422
    assert result["preserved"] is True
    assert "report" not in result
    assert result["details"] == ["numeric_evidence:unsupported-claim"]


def test_failed_action_response_bypasses_commit_audit(monkeypatch):
    async def should_not_run(**_kwargs):
        raise AssertionError("failed Action responses must not be audited or committed")

    monkeypatch.setattr(report_mod, "_audit_action_candidate", should_not_run)
    response = JSONResponse({"error": "provider unavailable", "preserved": True}, status_code=503)
    actual = asyncio.run(report_mod._finalize_action_response(response, {"reportId": "report-original"}))

    assert actual is response
    assert actual.status_code == 503


def test_preservation_only_table_mention_does_not_request_a_new_table():
    instruction = "Expand the prose using existing evidence. Leave the unrelated table unchanged."
    assert report_mod._action_requests_table(instruction) is False


def test_explicit_table_edit_still_requests_table_handling():
    assert report_mod._action_requests_table("Remove the unsupported value from the table.") is True
    assert report_mod._action_requests_table("Add a compact comparison table with verified values.") is True


def test_action_preservation_of_new_facts_does_not_trigger_research():
    instruction = "Rewrite for clarity without adding new facts, numbers, sources, or citations."
    assert report_mod._action_requires_external_research(instruction) is False


def test_action_explicit_okf_evidence_request_triggers_retrieval():
    instruction = "Add new evidence from saved OKF resources to this section."
    assert report_mod._action_requires_external_research(instruction) is True
    assert report_mod._action_requires_web_research(instruction, okf_available=True) is False


def test_action_attaches_citation_for_supporting_saved_okf_evidence(monkeypatch):
    async def citation_map(_system, _user):
        return '{"citations":[{"block":2,"source_ids":["S7"]}]}'

    monkeypatch.setattr(report_mod, "_call_llm_plain", citation_map)
    evidence = report_mod._action_okf_citation_context([{
        "id": "S7",
        "title": "Test OKF resource",
        "evidenceContent": "Renewable generation expanded as additional projects connected to the regional grid.",
    }])
    candidate = "## Evidence summary\n\nRenewable generation expanded as new projects connected to the regional grid."

    result = asyncio.run(report_mod._action_attach_fresh_citations(
        candidate, evidence, "Add evidence-backed analysis", "Evidence summary",
    ))

    assert result.endswith("[S7]")
    assert "URL: private user-provided OKF resource" in evidence


def test_action_audit_uses_private_okf_excerpt_without_returning_full_text():
    report_text = "# Energy note\n\n## Finding\nRenewable capacity reached 20 GW in 2024 [S1].\n"
    source = {
        "id": "S1",
        "title": "Saved user document",
        "url": "",
        "source_type": "Uploaded document",
        "evidenceOrigin": "okf_retrieval",
        "pageFetchStatus": "ok",
        "evidenceContent": "Renewable capacity reached 20 GW in 2024.",
        "fullContent": "PRIVATE_FULL_TEXT_SENTINEL renewable capacity reached 20 GW in 2024.",
        "documentId": "doc-fixture",
    }

    audits, public_sources, new_blockers = asyncio.run(report_mod._audit_action_candidate(
        original_report=report_text,
        candidate_report=report_text.replace("Energy note", "Energy evidence note"),
        title="Energy note",
        original_charts=[], candidate_charts=[], original_images=[], candidate_images=[],
        original_sources=[source], candidate_sources=[source], report_tier="brief",
    ))

    assert new_blockers == []
    assert public_sources[0]["evidence_available"] is True
    assert public_sources[0]["claim_evidence_matched"] is True
    assert all("PRIVATE_FULL_TEXT_SENTINEL" not in str(value) for value in public_sources[0].values())


def test_action_prose_edit_can_preserve_an_unrelated_table():
    original = "# Outlook\n\nPrior supported text [S1].\n\n|Metric|Value|\n|---|---|\n|Capacity|10 GW|"
    edited = "# Outlook\n\nClearer supported context [S1].\n\n|Metric|Value|\n|---|---|\n|Capacity|10 GW|"
    valid, problems = report_mod._action_edit_validation(
        original,
        edited,
        "Expand the prose with clearer context. Leave the existing table unchanged.",
    )
    assert valid is True
    assert problems == []
