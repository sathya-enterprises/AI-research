from routes.html_report import (
    _chart_has_renderable_payload,
    _clean_report_visual_payloads,
    build_html_report,
)
from routes.pdf import _is_valid_chart


def _heatmap(title="Quarterly Sector Return Heatmap"):
    return {
        "type": "heatmap",
        "title": title,
        "unit": "%",
        "rows": ["NIFTY AUTO", "NIFTY IT"],
        "columns": ["Q4 2024", "Q1 2025", "Q2 2025"],
        "values": [[-2.5, 1.2, 4.0], [1.0, None, -0.5]],
        "source": ["S1"],
    }


def test_matrix_heatmap_is_renderable_by_html_and_pdf_and_survives_export():
    chart = _heatmap()

    assert _chart_has_renderable_payload(chart)
    assert _is_valid_chart(chart)

    rendered = build_html_report(
        report="## Sector Returns\n\n[CHART_1]\n",
        title="Sector Rotation",
        question="Indian sector rotation",
        summary="",
        key_stats=[],
        charts=[chart],
        images=[],
        sources=[],
        report_tier="in_depth",
    )

    assert 'class="gg-heatmap-table"' in rendered
    assert chart["title"] in rendered
    assert "Q4 2024" in rendered and "Q2 2025" in rendered
    assert "NIFTY AUTO" in rendered and "NIFTY IT" in rendered


def test_unplaced_matrix_heatmap_is_recovered_in_html():
    chart = _heatmap()
    rendered = build_html_report(
        report="## Sector Returns\n\nThe quarter matrix is shown below.\n",
        title="Sector Rotation",
        question="Indian sector rotation",
        summary="",
        key_stats=[],
        charts=[chart],
        images=[],
        sources=[],
        report_tier="in_depth",
    )

    assert 'data-chart-recovered="true"' in rendered
    assert 'class="gg-heatmap-table"' in rendered
    assert chart["title"] in rendered


def test_distinct_heatmap_matrices_do_not_collapse_during_html_cleanup():
    first = _heatmap("Sector returns by quarter")
    second = _heatmap("Another sector return comparison")
    second["rows"] = ["NIFTY BANK", "NIFTY ENERGY"]
    second["values"] = [[3.0, 2.0, -1.0], [0.5, -2.0, 1.5]]

    text, cleaned, _ = _clean_report_visual_payloads(
        "## Returns\n\n[CHART_1]\n\n[CHART_2]", [first, second], [],
    )

    assert len(cleaned) == 2
    assert all(chart.get("type") == "heatmap" for chart in cleaned)
    assert "[CHART_1]" in text and "[CHART_2]" in text


def test_heatmap_requires_a_well_shaped_matrix_with_numeric_observations():
    malformed = _heatmap()
    malformed["values"] = [[None, "N/A", None], [None, None, None]]
    assert not _chart_has_renderable_payload(malformed)
    assert not _is_valid_chart(malformed)

    malformed["values"] = [[1.0], [2.0]]
    assert not _chart_has_renderable_payload(malformed)
    assert not _is_valid_chart(malformed)
