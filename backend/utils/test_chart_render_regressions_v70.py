import json

from routes.html_report import _render_chart_block_inner
from routes import report as report_module


def test_heatmap_missing_data_renders_as_na_not_zero():
    html = _render_chart_block_inner({
        "type": "heatmap", "title": "Quarterly Sector Return Heatmap",
        "rows": ["NIFTY AUTO", "NIFTY IT"],
        "columns": ["Q4 2024", "Q1 2025"],
        "values": [[1.5, None], [-2.0, 3.0]], "unit": "%",
    }, 1, {})
    assert "N/A" in html
    assert "No observed value for this period" in html


def test_positive_wide_range_bar_renders_log_axis_and_both_axis_labels():
    html = _render_chart_block_inner({
        "type": "bar", "title": "Promoter Transaction Volumes by Disclosed Entity",
        "xLabel": "Promoter or disclosed entity",
        "yLabel": "Shares transferred (log scale)", "unit": "shares",
        "axisScale": "logarithmic",
        "series": [{"name": "Shares transferred", "data": [
            {"label": "Mayur Uniquoters", "value": 8365},
            {"label": "Meyer Apparel", "value": 8889000},
        ]}],
    }, 1, {})
    assert "Promoter or disclosed entity" in html
    assert "Shares transferred (log scale)" in html
    assert "logarithmic" in html


def test_final_chart_gate_normalizes_specific_title_before_rejecting_untitled_payload():
    raw = [{"type": "bar", "series": [{"name": "Promoter shares sold", "data": [
        {"label": "Company A", "value": 10}, {"label": "Company B", "value": 20},
    ]}]}]
    text, charts, dropped = report_module._filter_final_chart_specs(
        "## Transaction comparison\n\n[CHART_1]", raw,
        normalizer=lambda c: {**c, "title": c.get("title") or "Promoter shares sold by company",
                             "xLabel": "Company", "yLabel": "Shares sold"},
        validator=lambda c: True, auto_type=lambda c: c, adapter=lambda c: c,
    )
    assert not dropped
    assert len(charts) == 1
    assert charts[0]["title"] == "Promoter shares sold by company"
    assert "[CHART_1]" in text


def test_pdf_svg_log_axis_renders_bars_and_both_named_axes():
    import json
    import re
    from routes.pdf import _svg_chart_from_config

    html = _render_chart_block_inner({
        "type": "bar", "title": "Promoter Share Transfer Volume by Company",
        "xLabel": "Company", "yLabel": "Shares transferred (log scale)",
        "unit": "shares", "axisScale": "logarithmic",
        "series": [{"name": "Shares transferred", "data": [
            {"label": "Mayur Uniquoters", "value": 8365},
            {"label": "Meyer Apparel", "value": 8889000},
        ]}],
    }, 4, {})
    match = re.search(r'window\.__ggCharts\.push\(\{ id: "[^"]+", config: (.*?)\}\);', html, re.S)
    assert match, "HTML renderer did not emit chart config for the PDF renderer"
    config = json.loads(match.group(1))
    svg = _svg_chart_from_config(config)
    assert "Company" in svg
    assert "Shares transferred" in svg
    assert "Meyer Apparel" in svg
    assert "Promoter Share Transfer Volume by Company" in html
    assert svg.count("<rect") >= 3


def test_horizontal_bar_uses_metric_on_x_axis_and_categories_on_y_axis():
    import json
    import re

    html = _render_chart_block_inner({
        "type": "horizontal_bar", "title": "Promoter Share Transfer Volume by Company",
        "xLabel": "Company", "yLabel": "Shares transferred (log scale)",
        "unit": "shares", "axisScale": "logarithmic",
        "series": [{"name": "Shares transferred", "data": [
            {"label": "Mayur Uniquoters", "value": 8365},
            {"label": "Meyer Apparel", "value": 8889000},
        ]}],
    }, 5, {})
    match = re.search(r'window\.__ggCharts\.push\(\{ id: "[^"]+", config: (.*?)\}\);', html, re.S)
    assert match
    config = json.loads(match.group(1))
    scales = config["options"]["scales"]
    assert config["options"]["indexAxis"] == "y"
    assert scales["x"]["type"] == "logarithmic"
    assert "beginAtZero" not in scales["x"]
    assert scales["x"]["title"]["text"] == "Shares transferred (log scale)"
    assert scales["y"]["title"]["text"] == "Company"
    from routes.pdf import _svg_chart_from_config
    svg = _svg_chart_from_config(config)
    assert "Shares transferred (log scale)" in svg
    assert "Company" in svg
    assert "Meyer Apparel" in svg
