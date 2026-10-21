"""Regression tests for sector-return matrices being invisible to the evidence chart gallery."""
from utils.evidence_dataset import build_evidence_dataset
from utils.chart_gallery import build_chart_gallery


def test_calendar_quarter_sector_matrix_extracts_same_metric_over_time():
    source = {
        "id": "S1",
        "origin": "publisher_page",
        "title": "NSE sector return matrix",
        "text": (
            "| SECTOR INDEX | Q4 2024 | Q1 2025 | Q2 2025 | Q3 2025 |\n"
            "| --- | --- | --- | --- | --- |\n"
            "| NIFTY AUTO | -2.75% | -2.45% | +6.04% | +13.33% |\n"
            "| NIFTY IT | -8.0% | +4.5% | +11.65% | +12.98% |"
        ),
    }
    dataset = build_evidence_dataset([source], "Analyze Indian sector rotation")
    sector = [o for o in dataset.observations if o.metric == "sector_return"]
    assert len(sector) == 8
    assert {o.entity for o in sector} == {"NIFTY AUTO", "NIFTY IT"}
    assert {o.period for o in sector} == {"Q4 2024", "Q1 2025", "Q2 2025", "Q3 2025"}
    assert all(o.unit == "%" and o.source_id == "S1" and o.quote for o in sector)


def test_sector_matrix_builds_real_line_chart_not_empty_axes():
    source = {
        "id": "S1",
        "origin": "publisher_page",
        "title": "NSE sector return matrix",
        "text": (
            "| SECTOR INDEX | Q4 2024 | Q1 2025 | Q2 2025 | Q3 2025 |\n"
            "| --- | --- | --- | --- | --- |\n"
            "| NIFTY AUTO | -2.75% | -2.45% | +6.04% | +13.33% |\n"
            "| NIFTY IT | -8.0% | +4.5% | +11.65% | +12.98% |"
        ),
    }
    dataset = build_evidence_dataset([source], "Analyze Indian sector rotation")
    result = build_chart_gallery(dataset, "Analyze Indian sector rotation", types=["line"])
    line_charts = [item.chart for item in result.charts if item.type == "line"]
    assert line_charts
    chart = line_charts[0]
    assert chart["title"]
    assert len(chart["series"]) >= 2
    assert all(len(series.get("data") or []) >= 2 for series in chart["series"])
    assert all(point.get("value") is not None for series in chart["series"] for point in series["data"])
