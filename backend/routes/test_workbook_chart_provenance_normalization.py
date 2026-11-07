"""Regression tests for deterministic workbook chart provenance surviving final normalization."""
from routes.report import _filter_final_chart_specs


def test_final_chart_normalization_preserves_server_workbook_evidence_metadata():
    raw = {
        "type": "horizontal_bar",
        "title": "Top PMS Strategies by 1-Year Return",
        "sourceOrigin": "uploaded_workbook",
        "_serverWorkbookEvidence": True,
        "sourceIds": ["S1"],
        "series": [{"name": "Reported 1-year return", "data": [{
            "label": "AMC Alpha — Alpha Growth", "value": 24.8,
            "evidenceSource": "S1", "evidenceValue": "0.248",
            "evidenceQuote": "Sheet: Table 2 | Row: 5 | raw workbook cell value: 0.248; derived plotted return: 24.8%",
            "sourceSheet": "Table 2", "sourceRow": 5,
        }]}],
    }

    # Simulate the renderer normalizer dropping all non-renderer fields.
    def normalizer(chart):
        return {
            "type": chart["type"], "title": chart["title"],
            "series": [{"name": chart["series"][0]["name"], "data": [
                {"label": chart["series"][0]["data"][0]["label"], "value": chart["series"][0]["data"][0]["value"]}
            ]}],
        }

    text, charts, dropped = _filter_final_chart_specs(
        "## Top PMS Strategies by 1-Year Return\n[CHART_1]", [raw], normalizer,
        validator=lambda chart: True,
    )
    assert not dropped
    assert len(charts) == 1
    assert charts[0]["_serverWorkbookEvidence"] is True
    assert charts[0]["sourceOrigin"] == "uploaded_workbook"
    point = charts[0]["series"][0]["data"][0]
    assert point["evidenceSource"] == "S1"
    assert point["evidenceValue"] == "0.248"
    assert "derived plotted return: 24.8%" in point["evidenceQuote"]
