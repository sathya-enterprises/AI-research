import re

from routes.html_report import _render_chart_block_inner, _render_extended_svg_chart
from routes.pdf import _svg_chart_from_config
from routes.report import _tier_limits
from utils.chart_catalog import MAX_DISPLAY_SLICES, fold_slice_tail_for_display


def _pie(n: int, ctype: str = "pie") -> dict:
    return {"type": ctype, "title": "Share of deposits", "series": [{"name": "Deposits", "data": [
        {"label": f"Bank {i}", "value": float(i), "evidenceSource": "S1"} for i in range(1, n + 1)]}]}


def test_slice_tail_folds_into_exact_other_total_without_mutating_data():
    chart = _pie(25)
    shown = fold_slice_tail_for_display(chart)
    points = shown["series"][0]["data"]
    assert len(points) == MAX_DISPLAY_SLICES + 1
    assert points[-1]["label"] == "Other (15)" and points[-1]["derived"] is True
    assert sum(p["value"] for p in points) == sum(range(1, 26))
    # The report's chart data keeps every scraped observation.
    assert len(chart["series"][0]["data"]) == 25


def test_slice_fold_keeps_small_and_non_slice_charts_unchanged():
    assert fold_slice_tail_for_display(_pie(MAX_DISPLAY_SLICES + 1)) is not None
    assert len(fold_slice_tail_for_display(_pie(MAX_DISPLAY_SLICES + 1))["series"][0]["data"]) == MAX_DISPLAY_SLICES + 1
    bar = _pie(40, "bar")
    assert fold_slice_tail_for_display(bar) is bar


def test_slice_fold_keeps_original_order_of_kept_slices():
    chart = {"type": "funnel", "series": [{"data": [{"label": str(v), "value": v} for v in [5, 30, 2, 40, 1, 9, 8, 7, 6, 50, 3, 4]]}]}
    labels = [p["label"] for p in fold_slice_tail_for_display(chart)["series"][0]["data"]]
    assert labels == ["5", "30", "40", "9", "8", "7", "6", "50", "3", "4", "Other (2)"]


def test_html_pie_renders_other_slice_for_long_tail():
    html = _render_chart_block_inner(_pie(30, "doughnut"), 1, {})
    assert "Other (20)" in html


def test_html_candlestick_and_error_bar_plot_every_row():
    bars = [{"label": f"W{i}", "open": 100 + i, "high": 105 + i, "low": 95 + i, "close": 102 + i} for i in range(150)]
    svg = _render_extended_svg_chart({"type": "candlestick", "title": "Weekly price", "series": [{"name": "Px", "data": bars}]}, 1, {})
    assert svg.count("<rect") >= 150
    rows = [{"label": f"P{i}", "value": i, "high": i + 1, "low": i - 1} for i in range(120)]
    svg = _render_extended_svg_chart({"type": "error_bar", "title": "Range", "series": [{"name": "V", "data": rows}]}, 1, {})
    assert svg.count("<circle") >= 120


def test_pdf_svg_labels_dense_axis_without_dropping_points():
    labels = [f"D{i}" for i in range(120)]
    config = {"type": "bar", "data": {"labels": labels, "datasets": [{"label": "V", "data": list(range(1, 121))}]}}
    svg = _svg_chart_from_config(config)
    assert svg.count("<rect") >= 120
    drawn = set(re.findall(r">(D\d+)<", svg))
    assert "D0" in drawn and "D117" in drawn and 30 <= len(drawn) <= 40


def test_tier_limits_env_override(monkeypatch):
    defaults = {"brief": 1, "detailed": 2, "in_depth": 3}
    monkeypatch.delenv("GG_TEST_TIER", raising=False)
    assert _tier_limits("GG_TEST_TIER", defaults) == defaults
    monkeypatch.setenv("GG_TEST_TIER", "5, 50, 500")
    assert _tier_limits("GG_TEST_TIER", defaults) == {"brief": 5, "detailed": 50, "in_depth": 500}
    for bad in ("5,50", "a,b,c"):
        monkeypatch.setenv("GG_TEST_TIER", bad)
        assert _tier_limits("GG_TEST_TIER", defaults) == defaults


def test_period_matrix_rows_become_series_across_periods():
    from utils.evidence_dataset import build_evidence_dataset
    text = ("Total | 13,78,323\nIndicator | Average for\nMar-2025 | Jun-2025 | Sep-2025 | Dec-2025 | Mar-2026\n"
            "1 | 2 | 3 | 4 | 5 | 6\nRates | WACR | 6.32 | 5.40 | 5.45 | 5.36 | 5.29\n"
            "Tri-party Repo | 6.17 | 5.31 | 5.38 | 5.19 | 5.09\n")
    ds = build_evidence_dataset([{"id": "S1", "text": text, "origin": "publisher_page", "title": "RBI"}], "RBI rates", targets=[])
    wacr = [(o.period, o.value) for o in ds.observations if o.entity == "WACR"]
    assert wacr == [("Mar 2025", 6.32), ("Jun 2025", 5.4), ("Sep 2025", 5.45), ("Dec 2025", 5.36), ("Mar 2026", 5.29)]
    assert {o.metric_label for o in ds.observations} == {"Period average"}
    assert len(ds.observations) == 10


def test_gallery_quote_gate_checks_the_text_each_quote_came_from():
    from routes.report import _append_evidence_chart_gallery
    page = "Bank | NIM (%) | GNPA (%)\nHDFC Bank | 3.4 | 1.3\nICICI Bank | 4.3 | 1.7\nSBI | 3.1 | 2.1\n"
    manifest = [{"id": "S1", "title": "Bank ratios", "url": "https://example.com/banks"}]
    enriched = [{"id": "S1", "title": "Bank ratios", "url": "https://example.com/banks", "pageContent": page,
                 "pageFetchStatus": "ok", "fullContent": "Search extract with a different text body."}]
    _text, _charts, meta = _append_evidence_chart_gallery(
        report_text="## Overview\n\nx\n", charts=[], question="bank NIM comparison", enriched=enriched,
        source_manifest=manifest, report_tier="detailed", extra={})
    # Before the fix the search extract (same S1) replaced the page text and
    # every table quote was rejected, leaving no observations.
    assert meta["observations"] >= 6
