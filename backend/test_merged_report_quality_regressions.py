"""Regression checks for merged chart-density and sector-rotation pipeline fixes."""

from utils.screener_kb import format_screener_snapshots_as_sources
from routes.report import (
    _build_sector_quarterly_return_chart,
    _build_sector_quarterly_return_heatmap,
    _build_sector_leader_laggard_chart,
    _sector_rotation_topic_quality_filter,
    _remove_generic_sector_key_stats,
)
from routes.source_manifest import build_source_registry
from utils.market_data import format_sector_quarterly_returns_as_source


def _rows():
    quarters = ["Q4 2024", "Q1 2025", "Q2 2025", "Q3 2025", "Q4 2025", "Q1 2026", "Q2 2026", "Q3 2026"]
    return [
        {"label": "NIFTY AUTO", "quarters": quarters, "returns": [1.5, -2.2, 3.4, 4.1, -0.5, 2.2, 1.9, 5.1], "missing_quarters": []},
        {"label": "NIFTY METAL", "quarters": quarters, "returns": [0.5, 1.2, -1.4, 2.0, 4.5, -2.1, 3.9, 2.1], "missing_quarters": []},
        {"label": "NIFTY HEALTHCARE", "quarters": quarters, "returns": [None, 0.1, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8], "missing_quarters": ["Q4 2024"]},
    ]


def test_sector_rotation_creates_multiple_distinct_charts_from_same_verified_data():
    rows = _rows()
    source = format_sector_quarterly_returns_as_source(rows)
    manifest = build_source_registry([source])
    line = _build_sector_quarterly_return_chart(rows, manifest)
    heatmap = _build_sector_quarterly_return_heatmap(rows, manifest)
    leader_laggard = _build_sector_leader_laggard_chart(rows, manifest)
    assert line and line["source"] == [manifest[0]["id"]]
    assert heatmap and heatmap["source"] == [manifest[0]["id"]]
    assert "NIFTY HEALTHCARE" not in heatmap["rows"]  # incomplete rows aren't fabricated into heatmap
    assert leader_laggard and leader_laggard["source"] == [manifest[0]["id"]]
    assert len({line["title"], heatmap["title"], leader_laggard["title"]}) == 3


def test_sector_topic_filter_keeps_valid_sector_charts_and_drops_duplicate_and_trade_charts():
    valid = {"type": "line", "title": "NSE Sector Returns by Completed Calendar Quarter", "source": ["S1"], "series": [{"name": "NIFTY AUTO", "data": [{"label": f"Q{i} 2025", "value": i} for i in range(1, 5)]}]}
    duplicate = dict(valid)
    irrelevant = {"type": "bar", "title": "No. of Trades — ranked", "labels": ["A", "B"], "data": [1, 2]}
    report, charts, audit = _sector_rotation_topic_quality_filter("## Sector Matrix\n\n[CHART_1]\n\n[CHART_2]\n\n[CHART_3]", [valid, duplicate, irrelevant])
    assert len(charts) == 1
    assert charts[0]["title"] == valid["title"]
    assert report.count("[CHART_1]") == 1
    assert "[CHART_2]" not in report and "[CHART_3]" not in report
    assert len(audit["removedCharts"]) == 2


def test_generic_prompt_words_cannot_become_sector_kpis():
    stats = [
        {"label": "Include", "entity": "Include", "value": "50"},
        {"label": "Nifty Auto return", "entity": "Nifty Auto", "value": "+5.1%"},
    ]
    cleaned = _remove_generic_sector_key_stats(stats)
    assert len(cleaned) == 1
    assert cleaned[0]["entity"] == "Nifty Auto"


def test_screener_supabase_snapshots_keep_the_direct_company_url():
    snapshots = [{
        "company": {"name": "Example Industries Ltd", "ticker": "500001", "company_url": "https://www.screener.in/company/500001/", "current_price": 123.4},
        "ratios": [], "financials": [], "growth": [], "shareholding": [], "peers": [], "pros_cons": [],
    }]
    sources = format_screener_snapshots_as_sources(snapshots)
    assert len(sources) == 1
    assert sources[0]["url"] == "https://www.screener.in/company/500001/"
    assert sources[0]["evidenceOrigin"] == "supabase_screener_snapshot"
    assert sources[0]["pageFetchStatus"] == "database_snapshot"


def test_screener_source_does_not_invent_url_when_missing():
    snapshots = [{"company": {"name": "Example Industries Ltd", "ticker": "500001"}, "ratios": []}]
    assert format_screener_snapshots_as_sources(snapshots) == []
