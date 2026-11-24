"""Chart forms chosen from the data's shape; the plotted values never change."""
from utils.chart_forms import ALLOWED_FORMS, apply_form, choose_form

YEARS = ["2020-21", "2021-22", "2022-23", "2023-24", "2024-25"]


def _s(name, values, labels=YEARS):
    return {"name": name, "data": [{"label": l, "value": v} for l, v in zip(labels, values)]}


def _form(spec):
    return choose_form(spec)[0]


def test_excluded_chart_types_are_never_chosen():
    excluded = {"scatter", "bubble", "lollipop", "dot_plot", "slope", "dumbbell", "bullet", "treemap", "sunburst",
                "violin", "density", "polar_area", "marimekko", "streamgraph", "cumulative", "choropleth", "network",
                "waffle"}
    assert not (ALLOWED_FORMS & excluded)


def test_time_series_forms():
    assert _form({"title": "Cumulative installed solar capacity (GW)", "type": "line", "unit": "GW",
                  "series": [_s("Installed capacity", [40, 54, 67, 82, 106])]}) == "area"
    assert _form({"title": "Repo rate (%)", "type": "line", "unit": "%",
                  "series": [_s("Repo rate", [4.0, 4.0, 6.5, 6.5, 6.25])]}) == "step_line"
    assert _form({"title": "Exports (US$ bn)", "type": "line", "unit": "US$ bn",
                  "series": [_s("Exports", [290, 420, 450, 437, 437])]}) == "line"
    assert _form({"title": "Capacity additions by source (GW)", "type": "line", "unit": "GW",
                  "series": [_s("Solar", [5, 13, 13, 15, 24]), _s("Wind", [1, 2, 2, 3, 3])]}) == "stacked_bar"
    form, extra, _ = choose_form({"title": "Generation (%)", "type": "line", "unit": "%",
                                  "series": [_s("Coal", [72, 74, 73, 74, 72]), _s("RE", [18, 19, 21, 22, 24]),
                                             _s("Other", [10, 7, 6, 4, 4])]})
    assert form == "stacked_bar" and extra.get("percentStacked")
    # an amount and a rate: combo with its own axis, never stacked even when the title says "share of"
    spec = {"title": "RE capacity (GW) and share of generation (%)", "type": "line", "unit": "GW",
            "series": [_s("RE share (%)", [18, 19, 21, 22, 24]), _s("RE capacity (GW)", [94, 107, 122, 144, 190])]}
    out = apply_form(spec)
    assert out["type"] == "combo" and out["secondaryUnit"] == "%"
    assert out["series"][0]["name"] == "RE capacity (GW)"  # bars first, the rate as the line


def test_category_forms():
    assert _form({"title": "Installed capacity mix (%)", "type": "horizontal_bar", "unit": "%",
                  "series": [_s("Share", [46, 29, 12, 8, 5], ["Coal", "Solar", "Hydro", "Gas", "Others"])]}) == "doughnut"
    assert _form({"title": "Solar pumps (number)", "type": "horizontal_bar", "unit": "number",
                  "series": [_s("Pumps", [420000, 310000, 180000, 95000],
                                ["Applications received", "Sanctioned", "Approved", "Installed"])]}) == "funnel"
    form, extra, _ = choose_form({"title": "Solar capacity vs target (GW)", "type": "horizontal_bar", "unit": "GW",
                                  "series": [_s("Solar", [106, 280], ["Installed so far", "2030 target"])]})
    assert form == "gauge" and extra["target"] == 280 and extra["gaugeIndex"] == 0
    assert _form({"title": "Trade by partner (US$ bn)", "type": "grouped_bar", "unit": "US$ bn",
                  "series": [_s("Exports", [77, 36, 18], ["US", "UAE", "China"]),
                             _s("Imports", [42, 48, 101], ["US", "UAE", "China"])]}) == "population_pyramid"
    assert _form({"title": "Average tariff by state (Rs/kWh)", "type": "horizontal_bar", "unit": "Rs/kWh",
                  "series": [_s("Tariff", [2.4, 2.6, 2.5], ["Rajasthan", "Gujarat", "Karnataka"])]}) == "bar"
    states = [f"State {i}" for i in range(10)]
    skewed = [500, 300, 60, 40, 30, 20, 15, 10, 5, 5]
    assert _form({"title": "Solar capacity by state (MW)", "type": "horizontal_bar", "unit": "MW",
                  "series": [_s("Capacity", skewed, states)]}) == "pareto"
    # an average is never a Pareto: its values do not add up to a meaningful total
    assert _form({"title": "Average tariff by state (Rs/kWh)", "type": "horizontal_bar", "unit": "Rs/kWh",
                  "series": [_s("Average tariff", skewed, states)]}) == "horizontal_bar"


def test_plotted_values_are_unchanged():
    spec = {"title": "Capacity additions by source (GW)", "type": "line", "unit": "GW",
            "series": [_s("Solar", [5, 13, 13, 15, 24]), _s("Wind", [1, 2, 2, 3, 3])]}
    out = apply_form(spec)
    assert out["series"] == spec["series"] and out["type"] != spec["type"]


def test_render_time_layouts_for_histogram_pyramid_and_gauge():
    from routes.html_report import _prepare_chart_form
    values = list(range(1, 31))
    hist = _prepare_chart_form({"type": "histogram", "unit": "MW", "series": [_s("Districts", values, [f"D{i}" for i in values])]})
    counts = [p["value"] for p in hist["series"][0]["data"]]
    assert sum(counts) == 30 and len(counts) >= 5 and hist["yLabel"] == "Count"
    pyramid = _prepare_chart_form({"type": "population_pyramid", "series": [
        _s("Exports", [77, 36], ["US", "UAE"]), _s("Imports", [42, 48], ["US", "UAE"])]})
    assert pyramid["series"][0]["data"][1] == {"label": "UAE", "left": 36, "right": 48}
    gauge = _prepare_chart_form({"type": "gauge", "target": 280, "max": 280, "gaugeIndex": 1,
                                 "series": [_s("Solar", [280, 106], ["2030 target", "Installed so far"])]})
    assert gauge["series"][0]["data"] == [{"label": "Installed so far", "value": 106, "target": 280}]


def test_pdf_draws_stacked_filled_and_stepped_forms():
    from routes.pdf import _svg_chart_from_config
    stacked = {"type": "bar", "data": {"labels": ["A", "B"], "datasets": [
        {"label": "x", "data": [5, 6]}, {"label": "y", "data": [7, 8]}]},
        "options": {"scales": {"x": {"stacked": True}, "y": {"stacked": True}}}}
    svg = _svg_chart_from_config(stacked)
    import re
    assert svg.count("<rect") >= 4
    ticks = [float(t) for t in re.findall(r">(\d+(?:\.\d+)?)</text>", svg)]
    assert max(ticks) >= 14  # the axis reaches the stack total (6 + 8), not the largest single value (8)
    stepped = {"type": "line", "data": {"labels": ["2021", "2022", "2023"], "datasets": [
        {"label": "Rate", "data": [4, 4, 6.5], "stepped": True}]}, "options": {}}
    path = [p for p in _svg_chart_from_config(stepped).split("<path") if 'fill="none"' in p and "stroke-width=\"2.2\"" in p]
    assert path and path[0].count("L ") >= 4  # horizontal then vertical moves
    area = {"type": "line", "data": {"labels": ["2021", "2022", "2023"], "datasets": [
        {"label": "Capacity", "data": [40, 54, 67], "fill": True}]}, "options": {}}
    assert 'fill-opacity="0.18"' in _svg_chart_from_config(area)
