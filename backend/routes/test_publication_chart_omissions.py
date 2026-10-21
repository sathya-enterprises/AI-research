"""Regression tests: removed charts must not leave blocking placeholders."""
from utils.report_quality import audit_report_publication, reconcile_orphaned_chart_references


def test_dangling_chart_placeholder_becomes_nonblocking_disclosure():
    report = "## Findings\nThe evidence supports the narrative.\n\n[CHART_3]\n"
    cleaned = reconcile_orphaned_chart_references(report, [])
    assert "[CHART_3]" not in cleaned
    assert "Chart not included" in cleaned
    audit = audit_report_publication(cleaned, [], [], [])
    assert audit["danglingChartRefs"] == []
    assert audit["orphanedChartMentions"] == []


def test_omission_disclosure_is_not_itself_an_orphaned_chart_reference():
    report = "## Findings\n> **Chart not included:** no validated structured chart matches this reference; unsupported values are not shown.\n"
    audit = audit_report_publication(report, [], [], [])
    assert audit["orphanedChartMentions"] == []


if __name__ == "__main__":
    test_dangling_chart_placeholder_becomes_nonblocking_disclosure()
    test_omission_disclosure_is_not_itself_an_orphaned_chart_reference()
    print("PASS: publication chart omission regression checks")
