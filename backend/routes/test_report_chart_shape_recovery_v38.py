from routes import report as report_mod


def test_alternate_doughnut_shape_normalizes_to_renderer_series():
    raw = {
        "chart_type": "doughnut",
        "title": "Deposit concentration",
        "labels": ["Top 25 districts", "Other districts"],
        "data": [55.2, 44.8],
    }
    normalized = report_mod._normalise_inline_chart_spec(raw)
    assert normalized is not None
    assert normalized["type"] == "doughnut"
    assert normalized["series"][0]["data"] == [
        {"label": "Top 25 districts", "value": 55.2},
        {"label": "Other districts", "value": 44.8},
    ]
    assert report_mod._valid_composition_chart_data(normalized["type"], normalized["series"])


def test_composition_chart_rejects_empty_or_single_point_payloads():
    assert not report_mod._valid_composition_chart_data("doughnut", [{"name": "Share", "data": []}])
    assert not report_mod._valid_composition_chart_data(
        "doughnut", [{"name": "Share", "data": [{"label": "Top 25 districts", "value": 55.2}]}]
    )


def test_composition_chart_rejects_missing_generic_labels_and_invalid_values():
    assert not report_mod._valid_composition_chart_data(
        "doughnut", [{"name": "Share", "data": [{"label": "1", "value": 55.2}, {"label": "2", "value": 44.8}]}]
    )
    assert not report_mod._valid_composition_chart_data(
        "doughnut", [{"name": "Share", "data": [{"label": "Top 25 districts", "value": -1}, {"label": "Other districts", "value": 101}]}]
    )
    assert not report_mod._valid_composition_chart_data(
        "pie", [{"name": "Share", "data": [{"label": "Top 25 districts", "value": 0}, {"label": "Other districts", "value": 0}]}]
    )


def test_composition_chart_accepts_only_distinct_named_categories_with_real_values():
    valid = [{"name": "Share", "data": [
        {"label": "Top 25 districts", "value": 55.2},
        {"label": "Other districts", "value": 44.8},
    ]}]
    assert report_mod._valid_composition_chart_data("doughnut", valid)
    duplicate = [{"name": "Share", "data": [
        {"label": "Districts", "value": 55.2},
        {"label": "districts", "value": 44.8},
    ]}]
    assert not report_mod._valid_composition_chart_data("doughnut", duplicate)
