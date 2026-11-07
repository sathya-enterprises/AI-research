from routes.report import (
    _action_audit_blockers,
    _apply_deterministic_audit_gate,
    _classify_publication_outcome,
    _insert_verified_fta_signing_table,
    _sanitize_report_financial_claims_for_publication,
    _sanitize_report_tables_for_publication,
    _verified_fta_signing_block_for_report,
    _publication_status_from_audits,
)
from datetime import date
from utils.deep_research_coverage import audit_requested_report_scope


def test_requested_scope_blocker_survives_the_publication_gate_and_repair_comparison():
    flag = "The FTA list needs an in-window dated signature row."
    audits = {
        "publicationAudit": {},
        "sourceEvidenceAudit": {},
        "scopeCoverageAudit": {"blocking": True, "flags": [flag]},
    }

    gated = _apply_deterministic_audit_gate(
        {"status": "PASS", "blocking": False, "findings": []},
        audits,
    )
    outcome = _classify_publication_outcome(audits, "A readable report body " * 30, gated["findings"])

    assert _publication_status_from_audits(audits) == "FAIL"
    assert gated["status"] == "FAIL"
    assert gated["blocking"] is True
    assert any(item.get("category") == "scope" for item in gated["findings"])
    assert any(blocker.startswith("scope:") for blocker in _action_audit_blockers(audits))
    assert outcome["outcome"] == "draft"
    assert outcome["failedStage"] == "scope_coverage"
    assert "scope_coverage" in outcome["blockingCategories"]


def test_verified_official_fta_signature_is_inserted_into_the_in_window_section():
    question = "List all FTAs signed by India in the past 1 year."
    report = "### In-Window Signing Review\n\nNo table was drafted.\n"
    events = [{
        "source_id": "S18", "partner": "New Zealand", "agreement": "India-New Zealand FTA",
        "signature_date": date(2026, 4, 27),
    }]

    repaired = _insert_verified_fta_signing_table(report, question, events, as_of_date=date(2026, 11, 6))

    assert "November 6, 2025 through November 6, 2026" in repaired
    assert "| Agreement | Partner | Signature date |" in repaired
    assert "| India-New Zealand FTA | New Zealand | April 27, 2026 |" in repaired
    assert "[S18]" in repaired


def test_verified_fta_signature_block_survives_final_publication_guards():
    question = "List all FTAs signed by India in the past 1 year."
    as_of = date(2026, 11, 6)
    draft = "### In-Window Signing Review\n\nCompare the signed agreements and their status.\n"
    events = [{
        "source_id": "S18", "partner": "New Zealand", "agreement": "India-New Zealand FTA",
        "signature_date": date(2026, 4, 27),
    }]
    sources = [{
        "id": "S18",
        "url": "https://www.commerce.gov.in/trade-agreements/india-new-zealand-fta",
        "title": "India-New Zealand free trade agreement signing",
        "pageFetchStatus": "ok",
        "evidenceOrigin": "publisher_page",
        "publisherEvidenceExcerpt": (
            "India and New Zealand signed the India-New Zealand free trade agreement on 27 April 2026."
        ),
    }]

    trusted_block = _verified_fta_signing_block_for_report(
        draft, question, events, as_of_date=as_of,
    )
    report = _insert_verified_fta_signing_table(
        draft, question, events, as_of_date=as_of,
    )
    report += (
        "\n\n| Metric | FY2026 value | Source |\n"
        "| --- | ---: | --- |\n"
        "| Unsupported export growth | 91% | [S18] |"
    )

    table_guard_report, table_findings = _sanitize_report_tables_for_publication(
        report, sources, trusted_blocks=[trusted_block],
    )
    report, _, financial_removals = _sanitize_report_financial_claims_for_publication(
        table_guard_report, [], sources, trusted_blocks=[trusted_block],
    )
    report, final_table_findings = _sanitize_report_tables_for_publication(
        report, sources, trusted_blocks=[trusted_block],
    )
    scope = audit_requested_report_scope(question, report, sources, as_of_date=as_of)

    assert trusted_block in report
    assert "| India-New Zealand FTA | New Zealand | April 27, 2026 |" in report
    assert "Unsupported export growth" not in report
    assert table_findings
    assert not final_table_findings
    assert financial_removals == 0
    assert scope["blocking"] is False
    assert scope["ftaSignatureCoverage"]["datedSignatureTable"] is True
    assert scope["ftaSignatureCoverage"]["signatureDatesInWindow"] == ["2026-04-27"]
    assert scope["ftaSignatureCoverage"]["officialCitedSource"] is True
