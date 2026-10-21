from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, Path(__file__).resolve().parents[1].as_posix())

from routes.report import _validate_chart_spec


def _chart(chart_type: str, points: list[dict]) -> dict:
    return {
        "type": chart_type,
        "title": "Evidence-backed comparison",
        "xLabel": "Category or period",
        "yLabel": "Capacity (GW)",
        "unit": "GW",
        "series": [{"name": "Capacity", "data": points}],
    }


def test_two_supported_categories_are_enough_for_bar_comparison():
    assert _validate_chart_spec(_chart("bar", [
        {"label": "Solar", "value": 10},
        {"label": "Wind", "value": 20},
    ]))


def test_two_supported_periods_are_enough_for_line_comparison():
    assert _validate_chart_spec(_chart("line", [
        {"label": "FY2023", "value": 10},
        {"label": "FY2025", "value": 20},
    ]))


def test_single_observation_is_still_not_a_comparison_chart():
    assert not _validate_chart_spec(_chart("bar", [{"label": "Solar", "value": 10}]))
