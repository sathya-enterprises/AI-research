from __future__ import annotations

from routes.html_report import (
    _chart_has_renderable_payload,
    _recover_chart_placeholders_from_titles,
    build_html_report,
)


def _line_chart(title: str = "Credit Growth Moderation (FY26 Actual vs FY27 Outlook)") -> dict:
    return {
        "type": "line",
        "title": title,
        "unit": "%",
        "series": [{
            "name": "Credit growth",
            "data": [
                {"label": "FY25", "value": 10.9},
                {"label": "FY26", "value": 15.9},
                {"label": "FY27", "value": 11.4},
            ],
        }],
        "source": ["S1"],
    }


def test_stranded_chart_title_is_recovered_as_placeholder():
    chart = _line_chart()
    body = "## Key Metrics\n\nCredit growth is expected to moderate.\n\n" + chart["title"] + "\n"
    recovered = _recover_chart_placeholders_from_titles(body, [chart])
    assert chart["title"] not in recovered
    assert "[CHART_1]" in recovered


def test_html_report_renders_chart_from_stranded_title_and_no_raw_markdown():
    chart = _line_chart()
    body = "## Key Metrics\n\nCredit growth is expected to moderate.\n\n" + chart["title"] + "\n"
    rendered = build_html_report(
        report=body,
        title="Banking Outlook",
        question="India banking outlook",
        summary="",
        key_stats=[],
        charts=[chart],
        images=[],
        sources=[],
        report_tier="brief",
    )
    assert "![" not in rendered
    assert "[CHART_1]" not in rendered
    assert 'id="chart-canvas-1"' in rendered or 'id="chart-1"' in rendered or "Credit Growth Moderation" in rendered
    assert "data-chart-recovered=\"true\"" not in rendered  # it was restored inline, not appended twice
    assert rendered.count("Credit Growth Moderation") == 1


def test_html_report_appends_valid_unreferenced_chart_after_section_edits():
    chart = _line_chart("Market Size Projection (2025–2031)")
    rendered = build_html_report(
        report="## Growth Outlook\n\nMarket fundamentals remain important.\n",
        title="Banking Outlook",
        question="India banking outlook",
        summary="",
        key_stats=[],
        charts=[chart],
        images=[],
        sources=[],
        report_tier="brief",
    )
    assert 'data-chart-recovered="true"' in rendered
    assert "Market Size Projection (2025–2031)" in rendered
    assert "<canvas" in rendered


def test_html_report_does_not_append_empty_unreferenced_chart():
    empty = {
        "type": "doughnut",
        "title": "Empty Market Split",
        "series": [{"name": "Share", "data": []}],
    }
    assert not _chart_has_renderable_payload(empty)
    rendered = build_html_report(
        report="## Outlook\n\nA concise supported outlook.\n",
        title="Banking Outlook",
        question="India banking outlook",
        summary="",
        key_stats=[],
        charts=[empty],
        images=[],
        sources=[],
        report_tier="brief",
    )
    assert "Empty Market Split" not in rendered
    assert 'data-chart-recovered="true"' not in rendered


def test_doughnut_chart_requires_two_numeric_slices_but_sparse_line_can_render():
    assert not _chart_has_renderable_payload({
        "type": "doughnut", "title": "One slice",
        "series": [{"name": "Share", "data": [{"label": "A", "value": 100}]}],
    })
    assert _chart_has_renderable_payload(_line_chart())
