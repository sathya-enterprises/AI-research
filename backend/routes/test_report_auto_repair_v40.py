from routes.report import _attach_audit_source_evidence, _apply_deterministic_audit_gate


def test_audit_source_evidence_attaches_real_fetched_text_by_url():
    manifest = [{"id": "S1", "title": "RBI report", "url": "https://example.org/report"}]
    enriched = [{
        "title": "RBI report", "url": "https://example.org/report/",
        "fullContent": "Verified excerpt: scheduled bank credit growth was reported for the stated financial year. " * 3,
    }]
    result = _attach_audit_source_evidence(manifest, enriched)
    assert result[0]["id"] == "S1"
    assert result[0]["evidenceAvailable"] is True
    assert result[0]["evidenceExcerpt"].startswith("Verified excerpt:")


def test_audit_source_evidence_does_not_invent_excerpt_from_title_or_url():
    result = _attach_audit_source_evidence(
        [{"id": "S1", "title": "RBI report", "url": "https://example.org/report"}],
        [],
    )
    assert result[0]["evidenceExcerpt"] == ""
    assert result[0]["evidenceAvailable"] is False


def test_deterministic_blocker_cannot_be_overridden_by_gemini_pass():
    result = _apply_deterministic_audit_gate(
        {"status": "PASS", "blocking": False, "findings": []},
        {"publicationAudit": {"flags": ["dangling chart reference"], "danglingChartRefs": ["CHART_2"]},
         "chartDensity": {}, "structure": {}},
    )
    assert result["status"] == "FAIL"
    assert result["blocking"] is True
