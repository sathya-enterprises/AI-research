"""Regression tests for uploaded-workbook charts at the publication evidence gate."""
from utils.chart_evidence_audit import audit_chart_evidence_consistency, sanitize_charts_for_publication


def _workbook_chart():
    quote = "AMC Alpha | Alpha Growth | 1 Yr return 0.248"
    return {
        "type": "horizontal_bar",
        "title": "Top PMS Strategies by 1-Year Return",
        "xLabel": "PMS strategy",
        "yLabel": "1 Yr return (%)",
        "unit": "%",
        "sourceOrigin": "uploaded_workbook",
        "_serverWorkbookEvidence": True,
        "sourceIds": ["S1"],
        "series": [{"name": "Reported 1-year return", "data": [
            {"label": "AMC Alpha — Alpha Growth", "value": 24.8, "evidenceSource": "S1",
             "evidenceQuote": quote, "evidenceValue": "0.248"},
            {"label": "AMC Beta — Beta Balanced", "value": 19.0, "evidenceSource": "S1",
             "evidenceQuote": "AMC Beta | Beta Balanced | 1 Yr return 0.19", "evidenceValue": "0.19"},
        ]}],
    }


def test_server_workbook_chart_passes_publisher_gate_using_exact_workbook_quotes():
    chart = _workbook_chart()
    sources = [{"id": "S1", "title": "PMS_demo.xlsx", "kind": "uploaded_file",
                "evidenceOrigin": "uploaded_workbook", "pageFetchStatus": "not_applicable"}]
    audit = audit_chart_evidence_consistency(
        "## Top PMS Strategies by 1-Year Return\n[CHART_1]", [chart], sources,
        require_publisher_verification=True,
    )
    assert not audit.get("chartEvidenceFailures"), audit.get("chartEvidenceFailures")


def test_server_workbook_chart_survives_final_publication_sanitizer():
    chart = _workbook_chart()
    sources = [{"id": "S1", "title": "PMS_demo.xlsx", "kind": "uploaded_file",
                "evidenceOrigin": "uploaded_workbook", "pageFetchStatus": "not_applicable"}]
    text, charts, meta = sanitize_charts_for_publication(
        "## Top PMS Strategies by 1-Year Return\n[CHART_1]",
        [chart], sources, question="Generate a PMS strategy performance and benchmarking report from the uploaded workbook",
        require_publisher_verification=True,
    )
    assert len(charts) == 1, meta
    assert len(charts[0]["series"][0]["data"]) == 2
