from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from routes.html_report import _clean_report_visual_payloads, _chart_size_class, _chart_canvas_height


def chart(title, labels, values, ctype="bar"):
    return {"type": ctype, "title": title, "unit": "INR crore", "xLabel": "Company", "yLabel": "Profit (INR crore)",
            "series": [{"name": "Profit", "data": [{"label": label, "value": value} for label, value in zip(labels, values)]}],
            "source": ["S1"]}


def test_generic_kpi_and_placeholder_are_suppressed():
    charts = [{"type": "kpi", "title": "KPI — Include earnings per share FY26", "series": [{"name": "EPS", "data": [{"label": "Include", "value": 50}]}]}]
    text, cleaned, stats = _clean_report_visual_payloads("## Summary\n\n[CHART_1]", charts, [{"label": "Include", "value": 50}])
    assert "[CHART_1]" not in text
    assert cleaned == [{}]
    assert stats == []


def test_duplicate_chart_data_is_removed_from_second_placement():
    first = chart("Net profit by company", ["A", "B"], [10, 20])
    duplicate = chart("Net profit — company comparison", ["A", "B"], [10, 20])
    text, cleaned, _ = _clean_report_visual_payloads("## A\n[CHART_1]\n\n## B\n[CHART_2]", [first, duplicate], [])
    assert "[CHART_1]" in text
    assert "[CHART_2]" not in text
    assert cleaned[1] == {}


def test_repeated_same_placeholder_only_renders_once():
    one = chart("Net profit by company", ["A", "B"], [10, 20])
    text, _, _ = _clean_report_visual_payloads("[CHART_1]\n\n[CHART_1]", [one], [])
    assert text.count("[CHART_1]") == 1


def test_serialized_chart_payload_is_not_left_as_report_text():
    text, _, _ = _clean_report_visual_payloads('''Intro\n\n```json\n{"type":"bar", "title":"Leaked", "series": []}\n```\n\nConclusion''', [], [])
    assert '"series"' not in text
    assert "Intro" in text and "Conclusion" in text


def test_dense_charts_get_larger_layout():
    dense = chart("Profit", [f"Company {i}" for i in range(1, 18)], list(range(1, 18)))
    assert _chart_size_class(dense) == "large"
    assert _chart_canvas_height(dense) >= 500


def test_dense_long_company_bar_chart_uses_horizontal_layout_and_readable_ticks():
    from routes.html_report import _render_chart_block_inner
    labels = [f"Long Company Name {i}" for i in range(1, 15)]
    dense = chart("Net profit by company", labels, list(range(1, 15)))
    rendered = _render_chart_block_inner(dense, 1, {})
    assert '"indexAxis": "y"' in rendered
    assert '"size": 11' in rendered
    assert 'style="height:480px"' in rendered
