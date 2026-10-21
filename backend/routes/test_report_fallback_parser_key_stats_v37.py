import pytest

from routes import report as report_mod
from utils.report_quality import audit_report_publication, reconcile_orphaned_chart_references, ensure_requested_chart_disclosure


def test_key_stats_missing_from_valid_provider_envelope_defaults_to_empty_list():
    assert report_mod._normalise_report_key_stats({"title": "Fallback", "report": "## Test\n\nBody"}) == []


def test_key_stats_accepts_expected_and_legacy_names_and_rejects_non_list():
    expected = [{"label": "Credit growth", "value": "12%"}, {"label": "NPA", "value": "2%"}]
    assert report_mod._normalise_report_key_stats({"keyStats": expected}) == expected
    assert report_mod._normalise_report_key_stats({"key_stats": expected}) == expected
    assert report_mod._normalise_report_key_stats({"keyStats": "not a list"}) == []
    assert report_mod._normalise_report_key_stats(None) == []


def test_orphan_chart_promise_is_replaced_after_chart_validation_drops_it():
    body = "## Digital Banking Evolution\n\nChart: Foreign Inflows (2025) as a proxy for digital banking impact — see below.\n\nThe sector is expected to continue digitising."
    result = reconcile_orphaned_chart_references(body, [])
    assert "see below" not in result.lower()
    assert "Chart not included" in result
    audit = audit_report_publication(result, charts=[], images=[], sources=[])
    assert audit["orphanedChartMentions"]
    assert "no matching validated structured chart" in " ".join(audit["flags"]).lower()


def test_chart_reference_is_preserved_when_matching_structured_chart_exists():
    body = "Chart: Foreign Inflows (2025) as a proxy for digital banking impact — see below.\n\n[CHART_1]"
    charts = [{"type": "bar", "title": "Foreign Inflows (2025)", "series": [{"name": "Inflows", "data": [{"label": "2025", "value": 14.5}]}]}]
    result = reconcile_orphaned_chart_references(body, charts)
    assert "see below" in result.lower()
    assert "Chart not included" not in result
    assert "[CHART_1]" in result


def test_deterministic_publication_gate_downgrades_false_gemini_pass():
    audit = {"status": "PASS", "summary": "Looks good", "blocking": False, "findings": []}
    deterministic = {"publicationAudit": {
        "status": "review_required",
        "flags": ["One or more chart placeholders do not resolve to structured chart data."],
        "danglingChartRefs": [1],
    }}
    result = report_mod._apply_deterministic_audit_gate(audit, deterministic)
    assert result["status"] == "FAIL"
    assert result["blocking"] is True
    assert result["findings"]


def test_gemini_unavailable_is_not_promoted_to_pass():
    result = report_mod._apply_deterministic_audit_gate(
        {"status": "unavailable", "reason": "audit_timeout", "blocking": False, "findings": []},
        {"publicationAudit": {"status": "clean", "flags": []}},
    )
    assert result["status"] == "unavailable"



def test_explicit_chart_request_gets_disclosure_when_no_structured_data_chart_survives():
    question = "Include at least one suitable chart with genuine data."
    report = "## Findings\n\nThe evidence is limited."
    result = ensure_requested_chart_disclosure(report, question, charts=[{"type": "table", "title": "Evidence table", "columns": ["Metric"], "rows": [["NPA"]]}])
    assert "Chart not included" in result
    assert "No chart values have been invented" in result


def test_chart_disclosure_is_not_added_when_a_real_numeric_chart_exists():
    question = "Include at least one suitable chart."
    chart = {"type": "bar", "title": "Credit growth", "series": [{"name": "Growth", "data": [{"label": "FY25", "value": 12.5}]}]}
    report = "## Findings\n\nGrowth slowed."
    assert "Chart not included" not in ensure_requested_chart_disclosure(report, question, [chart])
