"""Regression tests for sector-rotation relevance and quarterly evidence charts."""
from routes.report import _sector_rotation_topic_quality_filter, _build_sector_quarterly_return_chart


def test_sector_rotation_filter_removes_generic_charts_and_remaps_markers():
    text = "## Sector Performance\n\n[CHART_1]\n\n## Trading Activity\n\n[CHART_2]\n"
    charts = [
        {"type": "line", "title": "NSE Sector Returns by Completed Calendar Quarter",
         "series": [{"name": "NIFTY AUTO", "data": [{"label": "Q1 2026", "value": 2.0}]}]},
        {"type": "bar", "title": "NO. OF TRADES — RANKED",
         "series": [{"name": "Company trades", "data": [{"label": "Yes Bank", "value": 100}]}]},
        {"type": "bar", "title": "June 2026 Benchmark Performance Snapshot",
         "series": [{"name": "Nifty 50", "data": [{"label": "June 2026", "value": 0.9}]}]},
    ]
    filtered_text, filtered_charts, meta = _sector_rotation_topic_quality_filter(text, charts)
    assert len(filtered_charts) == 1
    assert "[CHART_1]" in filtered_text
    assert "[CHART_2]" not in filtered_text
    assert any("TRADES" in title for title in meta["removedCharts"])


def test_sector_rotation_filter_removes_placeholder_and_duplicate_heading():
    text = "## Overview\nFirst.\n\n[[[SECTION TO COMPLETE]]]\n\n## Overview\nDuplicate.\n\n## Findings\nReal finding."
    cleaned, charts, meta = _sector_rotation_topic_quality_filter(text, [])
    assert "[[[SECTION TO COMPLETE]]]" not in cleaned
    assert cleaned.count("## Overview") == 1
    assert "## Findings" in cleaned


def test_quarterly_chart_uses_only_observed_sector_values():
    rows = [{
        "label": "NIFTY AUTO",
        "quarters": ["Q1 2025", "Q2 2025", "Q3 2025", "Q4 2025"],
        "returns": [1.0, None, -2.0, 3.0],
        "missing_quarters": ["Q2 2025"],
    }]
    chart = _build_sector_quarterly_return_chart(rows, [
        {"id": "S1", "title": "CALCULATED NSE SECTOR RETURNS — EIGHT COMPLETED CALENDAR QUARTERS",
         "url": "", "evidence_origin": "calculated_from_daily_market_data",
         "chartEvidenceExcerpt": "Quarter coverage: Q1 2025, Q2 2025, Q3 2025, Q4 2025\nNIFTY AUTO | +1.00% | N/A | -2.00% | +3.00%"}
    ])
    assert chart["type"] == "line"
    assert chart["source"] == ["S1"]
    values = chart["series"][0]["data"]
    assert len(values) == 3
    assert all(point["label"] != "Q2 2025" for point in values)


def test_generic_quarterly_financial_chart_does_not_replace_sector_return_chart():
    from routes.report import _is_sector_quarterly_return_chart
    generic = {
        "type": "line", "title": "Quarterly Financial Performance",
        "series": [{"name": "Revenue", "data": [
            {"label": "Q1 2025", "value": 12.0},
            {"label": "Q2 2025", "value": 13.0},
            {"label": "Q3 2025", "value": 14.0},
        ]}],
    }
    sector = {
        "type": "line", "title": "NSE Sector Returns by Completed Calendar Quarter",
        "series": [{"name": "NIFTY AUTO", "data": [
            {"label": "Q1 2025", "value": 2.0},
            {"label": "Q2 2025", "value": -1.0},
            {"label": "Q3 2025", "value": 3.0},
        ]}],
    }
    assert not _is_sector_quarterly_return_chart(generic)
    assert _is_sector_quarterly_return_chart(sector)
