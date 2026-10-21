from utils.chart_catalog import (
    CHART_TYPES, CHART_TYPE_ALIASES, chart_density_assessment,
    chart_density_contract, chart_point_band, count_chart_points,
    normalize_chart_type,
)


def test_requested_human_chart_names_normalize_to_supported_ids():
    requested = {
        "Pie Chart": "pie", "Bar Chart": "bar", "Line Chart": "line", "Area Chart": "area",
        "Scatter Plot": "scatter", "Bubble Chart": "bubble", "Doughnut Chart": "doughnut",
        "Histogram": "histogram", "Box Plot": "boxplot", "Waterfall Chart": "waterfall",
        "Funnel Chart": "funnel", "Radar Chart": "radar", "Polar Area Chart": "polar_area",
        "Treemap": "treemap", "Heatmap": "heatmap", "Stacked Bar Chart": "stacked_bar",
        "Grouped Bar Chart": "grouped_bar", "Horizontal Bar Chart": "horizontal_bar",
        "Stacked Area Chart": "stacked_area", "Step Line Chart": "step_line", "Spline Chart": "spline",
        "Combo Chart": "combo", "Candlestick Chart": "candlestick", "OHLC Chart": "ohlc",
        "Gauge Chart": "gauge", "Bullet Chart": "bullet", "Pictogram Chart": "pictogram",
        "Pareto Chart": "pareto", "Dot Plot": "dot_plot", "Lollipop Chart": "lollipop",
        "Violin Plot": "violin", "Density Plot": "density", "Ridgeline Plot": "ridgeline",
        "Gantt Chart": "gantt", "Timeline Chart": "timeline", "Marimekko Chart": "marimekko",
        "Sunburst Chart": "sunburst", "Sankey Diagram": "sankey", "Chord Diagram": "chord",
        "Network Graph": "network", "Tree Diagram": "tree", "Flowchart": "flowchart",
        "Venn Diagram": "venn", "Funnel Pyramid": "funnel_pyramid", "Population Pyramid": "population_pyramid",
        "Progress Chart": "progress", "KPI Chart": "kpi", "Sparkline": "sparkline",
        "Error Bar Chart": "error_bar", "Control Chart": "control_chart", "Gunn chart": "gantt",
    }
    for label, canonical in requested.items():
        assert normalize_chart_type(label) == canonical, label
        assert canonical in CHART_TYPES, label


def test_chart_density_contract_has_no_observation_quota():
    for tier in ("brief", "detailed", "in_depth"):
        contract = chart_density_contract(tier)
        assert "there is no minimum point count or visual quota" in contract
        assert "Do not create repeated labels, fake dates, padded bins, or synthetic records." in contract

    band = chart_point_band("in_depth")
    assert band["minimum"] is None
    assert band["maximum"] is None


def test_density_assessment_accepts_a_supported_two_period_comparison():
    chart = {"type": "bar", "title": "Capacity FY2022-23 vs FY2023-24", "series": [{"name": "Capacity", "data": [
        {"label": "FY2022-23", "value": 10}, {"label": "FY2023-24", "value": 12}
    ]}]}
    assessment = chart_density_assessment(chart, "in_depth")
    assert count_chart_points(chart) == 2
    assert assessment["within_band"] is None
    assert assessment["minimum"] is None
    assert assessment["maximum"] is None

def test_density_assessment_counts_observations_and_keeps_compact_types_exempt():
    brief_chart = {"type": "line", "series": [{"name": "Value", "data": [
        {"label": str(2020 + i), "value": i + 1} for i in range(6)
    ]}]}
    assert count_chart_points(brief_chart) == 6
    assert chart_density_assessment(brief_chart, "brief")["within_band"] is None
    deep_chart = {"type": "line", "series": [{"name": "Value", "data": [
        {"label": str(2020 + i), "value": i + 1} for i in range(72)
    ]}]}
    deep_assessment = chart_density_assessment(deep_chart, "in_depth")
    assert deep_assessment["count"] == 72
    assert deep_assessment["applicable"] is True
    assert deep_assessment["within_band"] is None
    pie = {"type": "pie", "series": [{"name": "Share", "data": [
        {"label": f"Category {i + 1}", "value": i + 1} for i in range(5)
    ]}]}
    assert chart_density_assessment(pie, "in_depth")["applicable"] is True


def test_heatmap_counts_numeric_cells_only():
    chart = {"type": "heatmap", "rows": ["A", "B"], "columns": ["X", "Y"], "values": [[1, None], [3, "n/a"]]}
    assert count_chart_points(chart) == 2
