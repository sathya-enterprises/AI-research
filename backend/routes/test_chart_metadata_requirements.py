import sys
sys.path.insert(0, __import__('pathlib').Path(__file__).resolve().parents[1].as_posix())

from routes.report import _validate_chart_spec


def test_axis_chart_requires_title_and_axis_labels_at_validation_boundary():
    base = {
        "type": "bar",
        "title": "Revenue by Segment",
        "xLabel": "Segment",
        "yLabel": "Revenue (USD Billion)",
        "unit": "B",
        "series": [{"name": "Revenue", "data": [
            {"label": "A", "value": 10},
            {"label": "B", "value": 20},
            {"label": "C", "value": 15},
        ]}],
    }
    assert _validate_chart_spec(base)
    for missing in ("title", "xLabel", "yLabel"):
        bad = dict(base)
        bad.pop(missing)
        assert not _validate_chart_spec(bad)


def test_axis_label_contract_covers_common_axis_chart_types():
    axis_types = {
        "bar", "line", "area", "stacked_bar", "grouped_bar", "horizontal_bar",
        "stacked_area", "step_line", "spline", "combo", "arrow", "scatter", "bubble",
        "waterfall", "candlestick", "ohlc", "histogram", "boxplot", "error_bar",
        "control_chart", "dot_plot", "lollipop", "pareto", "violin", "density",
        "ridgeline", "gantt", "timeline", "population_pyramid",
    }
    assert len(axis_types) >= 25


def test_extended_svg_axis_labels_are_rendered():
    from routes.html_report import _render_extended_svg_chart
    chart = {
        "type": "pareto",
        "title": "Top Categories",
        "xLabel": "Category",
        "yLabel": "Revenue (USD M)",
        "unit": "M",
        "series": [{"name": "Revenue", "data": [
            {"label": "A", "value": 10},
            {"label": "B", "value": 20},
            {"label": "C", "value": 15},
        ]}],
    }
    rendered = _render_extended_svg_chart(chart, 1, {})
    assert "Top Categories" in rendered
    assert "Category" in rendered
    assert "Revenue (USD M)" in rendered
