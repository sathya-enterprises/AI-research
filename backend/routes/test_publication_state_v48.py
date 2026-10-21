from routes.report import (
    _apply_deterministic_audit_gate,
    _publication_status_from_audits,
    _source_evidence_quality_audit,
)


def test_publication_state_uses_deterministic_gate_not_model_fail():
    deterministic = {
        "publicationAudit": {"flags": [], "danglingChartRefs": []},
        "sourceEvidenceAudit": {"blocking": False, "flags": []},
        # A short, authentic dataset is diagnostic only. It is not a minimum
        # observation gate and must not turn a sound report into a warning.
        "chartDensity": {"exceptionsOrMisses": [{"count": 2, "minimum": 5}]},
    }
    audit = _apply_deterministic_audit_gate(
        {"status": "FAIL", "blocking": True, "findings": [{"issue": "Chart has two points"}]},
        deterministic,
    )

    assert audit["status"] == "WARNING"
    assert audit["modelReviewStatus"] == "FAIL"
    assert audit["blocking"] is False
    assert _publication_status_from_audits(deterministic) == "PASS"


def test_deterministic_unsupported_claim_still_fails_when_model_passes():
    deterministic = {
        "publicationAudit": {"flags": []},
        "sourceEvidenceAudit": {"blocking": True, "flags": ["unsupported value"]},
    }
    audit = _apply_deterministic_audit_gate(
        {"status": "PASS", "blocking": False, "findings": []}, deterministic
    )

    assert audit["status"] == "FAIL"
    assert audit["blocking"] is True
    assert _publication_status_from_audits(deterministic) == "FAIL"


def test_search_extract_citation_is_a_warning_separate_from_numeric_support():
    evidence = _source_evidence_quality_audit([{
        "id": "S8",
        "title": "Renewable energy outlook",
        "pageFetchStatus": "empty_extraction",
        "evidenceExcerptOrigin": "search_extract_only",
        "_citedClaimGroups": [{"groupId": "g1", "claimTokens": [], "sourceIds": ["S8"]}],
    }])
    deterministic = {
        "publicationAudit": {"flags": []},
        "sourceEvidenceAudit": evidence,
    }

    assert evidence["blocking"] is False
    assert evidence["provenanceWarnings"][0]["id"] == "S8"
    assert _publication_status_from_audits(deterministic) == "WARNING"
