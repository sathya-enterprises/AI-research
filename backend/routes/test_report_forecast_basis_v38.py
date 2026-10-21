from utils.report_quality import audit_report_publication
from routes.report import _apply_deterministic_audit_gate


def _report_with_cross_metric_cagr() -> str:
    return """# Banking outlook

| Metric | FY25 (USD trillion) | FY26 (projected) |
|---|---:|---:|
| Deposits | 2.86 | 3.10* |
| Gross Advances | 2.26 | 2.50* |
| Market Value | 162 | 200* |

*Projections based on 9.7% CAGR to 2031 [S3].
"""


def test_general_cagr_footnote_flags_multiple_unrelated_metric_families():
    audit = audit_report_publication(
        _report_with_cross_metric_cagr(),
        charts=[], images=[], sources=[{"id": "S3", "url": "https://example.com/source"}],
    )
    issues = audit["crossMetricProjectionBasisIssues"]
    assert issues
    assert set(issues[0]["metricFamilies"]) >= {"deposits", "advances/credit", "market value/size"}
    assert any("metric-specific basis" in flag for flag in audit["flags"])


def test_cross_metric_cagr_issue_blocks_publication_even_if_gemini_unavailable():
    publication = audit_report_publication(
        _report_with_cross_metric_cagr(),
        charts=[], images=[], sources=[{"id": "S3", "url": "https://example.com/source"}],
    )
    result = _apply_deterministic_audit_gate(
        {"status": "unavailable", "blocking": False, "findings": []},
        {"publicationAudit": publication, "chartDensity": {"exceptionsOrMisses": []}},
    )
    assert result["status"] == "FAIL"
    assert result["modelReviewStatus"] == "unavailable"
    assert result["blocking"] is True
    assert any("CAGR footnote" in item.get("issue", "") for item in result["findings"])


def test_cagr_issue_not_raised_for_single_metric_forecast_or_no_general_footnote():
    single_metric = """| Metric | FY25 | FY26 projected |
|---|---:|---:|
| Market Value | 162 | 200 |

Projection based on 9.7% CAGR to 2031 [S3].
"""
    unrelated_without_note = """| Metric | FY25 | FY26 projected |
|---|---:|---:|
| Deposits | 2.86 | 3.10 |
| Gross Advances | 2.26 | 2.50 |
| Market Value | 162 | 200 |
"""
    for report in (single_metric, unrelated_without_note):
        audit = audit_report_publication(report, charts=[], images=[], sources=[{"id": "S3"}])
        assert audit["crossMetricProjectionBasisIssues"] == []
