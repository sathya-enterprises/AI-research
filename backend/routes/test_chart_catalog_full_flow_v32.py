from __future__ import annotations
import json
import sys
from pathlib import Path

sys.path.insert(0, Path(__file__).resolve().parents[1].as_posix())

from routes.html_report import _chart_spec_from_json, _render_chart_block_inner
from routes.report import _validate_chart_spec
from utils.chart_catalog import CHART_TYPES, chart_point_band, normalize_chart_type


def test_all_requested_type_names_have_a_catalog_entry():
    names = [
        "Pie Chart", "Bar Chart", "Line Chart", "Area Chart", "Scatter Plot", "Bubble Chart",
        "Doughnut Chart", "Histogram", "Box Plot", "Waterfall Chart", "Funnel Chart", "Radar Chart",
        "Polar Area Chart", "Treemap", "Heatmap", "Stacked Bar Chart", "Grouped Bar Chart",
        "Horizontal Bar Chart", "Stacked Area Chart", "Step Line Chart", "Spline Chart", "Combo Chart",
        "Candlestick Chart", "OHLC Chart", "Gauge Chart", "Bullet Chart", "Pictogram Chart", "Pareto Chart",
        "Dot Plot", "Lollipop Chart", "Violin Plot", "Density Plot", "Ridgeline Plot", "Gantt Chart",
        "Timeline Chart", "Marimekko Chart", "Sunburst Chart", "Sankey Diagram", "Chord Diagram",
        "Network Graph", "Tree Diagram", "Flowchart", "Venn Diagram", "Funnel Pyramid", "Population Pyramid",
        "Progress Chart", "KPI Chart", "Sparkline", "Error Bar Chart", "Control Chart", "Gunn chart",
    ]
    for name in names:
        canonical = normalize_chart_type(name)
        assert canonical in CHART_TYPES, (name, canonical)


def test_gunn_chart_alias_is_normalized_in_html_parsing_and_gantt_renders():
    raw = json.dumps({
        "type": "Gunn chart", "title": "Delivery schedule", "events": [
            {"label": "Survey", "start": "2026-01-01", "end": "2026-01-07"},
            {"label": "Build", "start": "2026-01-08", "end": "2026-02-01"},
        ],
    })
    spec, consumed = _chart_spec_from_json(raw)
    assert consumed is True
    assert spec and spec["type"] == "gantt"
    assert len(spec["events"]) == 2
    html = _render_chart_block_inner(spec, 1, {})
    assert "Delivery schedule" in html
    assert "Survey" in html


def test_table_spec_is_a_supported_visual_not_a_chart_generation_failure():
    table = {
        "type": "table", "title": "Project finance scenarios",
        "columns": ["Scenario", "Cost of capital"],
        "rows": [["Base", "10%"], ["Stress", "14%"]],
    }
    assert _validate_chart_spec(table)
    spec, consumed = _chart_spec_from_json(json.dumps(table))
    assert consumed is True
    assert spec and spec["type"] == "table"
    assert len(spec["rows"]) == 2
    rendered = _render_chart_block_inner(spec, 1, {})
    assert "Project finance scenarios" in rendered


def test_tier_aliases_have_no_chart_density_quota():
    for label in ("Brief Report", "In-Detail", "Deep Research"):
        band = chart_point_band(label)
        assert band["minimum"] is None
        assert band["maximum"] is None
        assert band["target"] is None
