from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.report_quality import audit_chart_narrative


def test_chart_narrative_audit_passes_with_context_and_interpretation():
    report = """## Quarterly sector leadership

    This comparison tests whether sector leadership changed across the eight-quarter period and whether relative returns moved with changes in earnings expectations. The relationship matters because a rotation can signal changing market assumptions, but returns alone do not establish causation.

    [CHART_1]

    The observed ranking shift suggests leadership was not stable throughout the period. This may reflect changing earnings expectations or flows, although those mechanisms need separate source evidence; the implication is to compare the sector returns with quarterly earnings and flow data before assigning a cause.
    """
    charts = [{"title": "Sector returns by quarter", "rationale": "Compares relative sector leadership across quarters.", "takeaway": "Leadership changes across the observed quarters."}]
    result = audit_chart_narrative(report, charts)
    assert result["status"] == "pass"
    assert result["chart_marker_count"] == 1
    assert result["missing_lead_in"] == []
    assert result["missing_interpretation"] == []


def test_chart_narrative_audit_flags_chart_with_values_only_or_no_interpretation():
    report = """## Company profitability

    The chart below presents net profit values for the companies included in the retrieved dataset.

    [CHART_1]

    Company A is 10 and Company B is 20.
    """
    result = audit_chart_narrative(report, [{"title": "Net profit by company"}])
    assert result["status"] == "warning"
    assert result["missing_lead_in"]
    assert result["missing_interpretation"]
    assert any("interpretation" in flag for flag in result["flags"])


def test_chart_narrative_audit_does_not_require_fixed_template_when_no_chart_markers():
    result = audit_chart_narrative("## Findings\n\nThe analysis indicates a meaningful change in the observed period.", [])
    assert result["status"] == "not_applicable"
    assert result["checked_chart_count"] == 0


def test_related_charts_can_share_one_lead_in_and_interpretation():
    report = """## Quarterly comparison

    These two charts jointly test whether sector leadership and foreign flows moved together over the same quarters. The comparison matters because co-movement may be consistent with changing risk appetite, although it cannot prove that flows caused returns.

    [CHART_1]

    [CHART_2]

    Read together, the charts suggest that sector leadership and foreign flows did not move in lockstep in every quarter. This divergence means the report should compare domestic flows, earnings revisions and valuation changes before attributing the rotation to foreign investors alone.
    """
    result = audit_chart_narrative(report, [{"title": "Sector returns"}, {"title": "Foreign flows"}])
    assert result["status"] == "pass"
    assert result["missing_lead_in"] == []
    assert result["missing_interpretation"] == []
