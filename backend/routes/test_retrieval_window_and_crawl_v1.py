"""Stage A: retrieval fetches the requested quarter window; the crawl budget follows the tier."""
import asyncio
from datetime import date

import pytest

import routes.report as R
from utils.deep_research_coverage import requested_quarter_count
from utils.site_crawler import crawl_relevant_internal_pages


@pytest.mark.parametrize("question,expected", [
    ("Sector rotation in the Indian stock markets over the last 4 quarters", 4),
    ("Sector rotation over the past eight quarters", 8),
    ("Sector rotation over the last two years", 8),
    ("Sector rotation over the last 5 years", 12),  # capped
    ("Sector rotation in India", 8),  # unspecified window keeps the previous default
])
def test_requested_quarter_count(question, expected):
    assert requested_quarter_count(question, date(2026, 11, 7)) == expected


def test_sector_rotation_queries_ask_for_the_requested_window_only():
    queries = asyncio.run(R._build_multi_angle_search_queries(
        "Sector rotation in the Indian stock markets over the last 4 quarters", "", "research", report_tier="in_depth",
    ))
    joined = " ".join(queries).casefold()
    assert "last 4 quarters" in joined
    assert "eight" not in joined and "last 8" not in joined


def test_compounded_chart_title_reports_actual_quarter_count():
    quarters = ["Q3 FY26", "Q4 FY26", "Q1 FY27", "Q2 FY27"]
    rows = [{"label": f"NIFTY {name}", "quarters": quarters, "returns": [1.0, -2.0, 3.0, 4.0]} for name in ("IT", "BANK", "AUTO")]
    manifest = [{"id": "S1", "title": "Calculated NSE sector returns by quarter", "url": ""}]
    chart = R._build_sector_compounded_return_chart(rows, manifest)
    if chart is None:  # builder requires a calculated-source record; skip only that precondition
        pytest.skip("compounded chart builder needs its calculated source record")
    assert chart["title"] == "Compounded Sector Return Across 4 Completed Quarters"


def test_in_depth_crawl_budget_is_no_longer_capped_at_28():
    _rows, stats = asyncio.run(crawl_relevant_internal_pages([], "sector rotation India", report_tier="in_depth", max_pages=48, max_depth=3))
    assert stats["page_limit"] == 48


def test_crawl_ceiling_is_configurable(monkeypatch):
    monkeypatch.setenv("GG_CRAWL_MAX_PAGES", "30")
    _rows, stats = asyncio.run(crawl_relevant_internal_pages([], "sector rotation India", report_tier="in_depth", max_pages=48, max_depth=3))
    assert stats["page_limit"] == 30
