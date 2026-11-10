"""Stage L: one judge for every audit decision; good patches survive old blockers; auditor artifacts dropped."""
from routes import report as report_mod


def _det(groups=None, pub=None):
    return {
        "publicationAudit": pub or {},
        "sourceEvidenceAudit": {"blocking": bool(groups), "mismatchedCitationGroups": groups or [], "flags": []},
        "scopeCoverageAudit": {"blocking": False},
    }


BODY = "A steady paragraph about the market that carries enough words to be a real report body. " * 4
ORIGINAL = BODY + "\n\nSecond paragraph ends here."
REPAIRED = BODY + "\n\nSecond paragraph, corrected, ends here."


def test_exact_patch_kept_when_only_preexisting_blockers_remain():
    before = _det(groups=[{"claim": "Net FDI inflows grew from $27.14 billion"}])
    after = _det(groups=[{"claim": "Net FDI inflows grew from $27.14 billion"}])
    ok, reason, _ = report_mod._evaluate_exact_patch_candidate(
        original_text=ORIGINAL, repaired_text=REPAIRED, original_pages=10, repaired_pages=10,
        page_minimum=0, repair_deterministic=after, original_deterministic=before)
    assert ok and reason == "exact_patch_no_new_blockers"


def test_exact_patch_rejected_when_it_adds_a_blocker():
    before = _det()
    after = _det(groups=[{"claim": "A new unsupported claim of 42%"}])
    ok, reason, _ = report_mod._evaluate_exact_patch_candidate(
        original_text=ORIGINAL, repaired_text=REPAIRED, original_pages=10, repaired_pages=10,
        page_minimum=0, repair_deterministic=after, original_deterministic=before)
    assert not ok and reason == "new_blocker_introduced"


def test_exact_patch_without_baseline_keeps_old_rule():
    after = _det(groups=[{"claim": "x"}])
    ok, reason, _ = report_mod._evaluate_exact_patch_candidate(
        original_text=ORIGINAL, repaired_text=REPAIRED, original_pages=10, repaired_pages=10,
        page_minimum=0, repair_deterministic=after)
    assert not ok and reason == "source_evidence_blocker_remains"


def test_blocker_signature_counts_publication_items():
    pub = {"duplicateTableCount": 2, "danglingChartRefs": ["[CHART_9]"]}
    sig = report_mod._blocker_signature(_det(pub=pub))
    assert "pub:duplicateTableCount#1" in sig and "pub:danglingChartRefs:[CHART_9]" in sig


def test_auditor_chart_marker_and_truncation_findings_dropped():
    text = "Body text [CHART_1] and more [CHART_2].\n\nLast sentence is complete."
    audit = {"status": "FAIL", "findings": [
        {"severity": "critical", "category": "internal_markers", "issue": "Unresolved chart placeholders [CHART_1] and [CHART_2] remain."},
        {"severity": "low", "category": "formatting", "issue": "The report body is truncated mid-sentence."},
        {"severity": "high", "category": "source_mapping", "issue": "Claim X is not in S3."},
    ]}
    out = report_mod._drop_auditor_artifacts(audit, text, [{"title": "a"}, {"title": "b"}])
    assert [f["category"] for f in out["findings"]] == ["source_mapping"]


def test_auditor_marker_finding_kept_when_chart_really_missing():
    text = "Body text [CHART_3].\n\nDone."
    audit = {"findings": [{"severity": "critical", "issue": "Unresolved chart placeholder [CHART_3]."}]}
    out = report_mod._drop_auditor_artifacts(audit, text, [{"title": "a"}])
    assert len(out["findings"]) == 1


def test_auditor_truncation_kept_when_text_really_cut():
    text = "Body text that stops in the middle of"
    audit = {"findings": [{"severity": "low", "issue": "Report is truncated."}]}
    assert len(report_mod._drop_auditor_artifacts(audit, text, [])["findings"]) == 1
