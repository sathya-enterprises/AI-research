from routes.pdf import _svg_chart_from_config


def test_empty_doughnut_renders_explicit_omission_not_blank_ring():
    rendered = _svg_chart_from_config({
        "type": "doughnut",
        "data": {"labels": [], "datasets": [{"label": "Share", "data": []}]},
        "options": {"plugins": {"title": {"text": "Deposit concentration"}}},
    })
    assert "Chart omitted" in rendered
    assert "<svg" not in rendered


def test_doughnut_with_verified_labels_and_values_renders_chart():
    rendered = _svg_chart_from_config({
        "type": "doughnut",
        "data": {
            "labels": ["Top 25 districts", "Other districts"],
            "datasets": [{"label": "Deposit share", "data": [55.2, 44.8]}],
        },
        "options": {"plugins": {"title": {"text": "Deposit concentration"}}},
    })
    assert "<svg" in rendered
    assert "Top 25 districts" in rendered
    assert "Other districts" in rendered
