from utils.chart_evidence_audit import _unit_is_in, audit_chart_evidence_consistency


SOURCE_TEXT = """TABLE 1 - Solar installed capacity by country (GW)
Year | India | China
2023 | 80 | 70
2024 | 90 | 75"""


def _chart(*, label="2024", value=90, column="India", metric="solar installed capacity", row="2024"):
    point = {
        "label": label,
        "value": value,
        "evidenceSource": "S1",
        "evidenceQuote": f"{row} | 90 | 75" if row == "2024" else f"{row} | 80 | 70",
        "evidenceValue": str(value),
        "evidenceColumn": column,
        "evidenceEntity": column,
        "evidenceMetric": metric,
        "evidenceRowLabel": row,
        "evidencePeriod": label,
    }
    return {
        "type": "bar",
        "title": metric,
        "unit": "GW",
        "sourceIds": ["S1"],
        "series": [{"name": column, "data": [point]}],
    }


def _audit(chart):
    return audit_chart_evidence_consistency("See chart [CHART_1].", [chart], [{
        "id": "S1", "pageFetchStatus": "ok", "pageContent": SOURCE_TEXT,
    }])


def test_quoted_chart_point_requires_the_value_in_its_named_source_column():
    mismatched = _chart(value=75)
    audit = _audit(mismatched)
    assert audit["chartEvidenceFailures"]
    assert "named table column" in audit["chartEvidenceFailures"][0]["reason"]


def test_quoted_chart_point_requires_the_exact_row_period():
    mismatched = _chart(label="2024", value=80, row="2023")
    audit = _audit(mismatched)
    assert audit["chartEvidenceFailures"]
    assert "exact period" in audit["chartEvidenceFailures"][0]["reason"]


def test_quoted_chart_point_requires_the_chart_measure_to_match_the_source():
    mismatched = _chart(metric="GDP growth")
    audit = _audit(mismatched)
    assert audit["chartEvidenceFailures"]
    assert "stated measure" in audit["chartEvidenceFailures"][0]["reason"]


def test_quoted_chart_point_passes_when_row_column_period_and_measure_match():
    audit = _audit(_chart())
    assert audit["chartEvidenceFailures"] == []


def test_chart_headline_is_not_used_as_a_declared_measure():
    chart = _chart()
    chart["series"][0]["data"][0].pop("evidenceMetric")
    chart["title"] = "Promoter transaction value by company"
    assert _audit(chart)["chartEvidenceFailures"] == []

    chart["metric"] = "GDP growth"
    audit = _audit(chart)
    assert audit["chartEvidenceFailures"]
    assert "stated measure" in audit["chartEvidenceFailures"][0]["reason"]


def test_unlisted_chart_units_must_appear_in_the_quoted_evidence():
    assert _unit_is_in({"unit": "shares"}, "The promoter sold 27 lakh shares.") is True
    assert _unit_is_in({"unit": "shares"}, "The company reported 27 lakh units.") is False
