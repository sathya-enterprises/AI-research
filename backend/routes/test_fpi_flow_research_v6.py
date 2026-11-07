from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, Path(__file__).resolve().parents[1].as_posix())

from routes.report import (
    _is_fpi_flow_research,
    _fpi_research_queries,
    _complete_deep_research_query_plan,
    _filter_relevant_sources,
    _report_search_results_per_query,
    _report_search_min_results,
    _build_multi_angle_search_queries,
)
from utils.deep_research_coverage import audit_research_coverage
from utils.report_tiers import requested_report_page_count

QUESTION = (
    "I want a 100 page report investigating whether FIIs have fled India, "
    "including Nifty holdings, FDI, IPO/QIP, debt, sectors and Gautam Duggad's claim."
)


def _body(heading: str) -> str:
    return (heading + ". " +
        "The published data describes annual investment flows and gives a time period. "
        "The table compares inflows and outflows across multiple reporting years and "
        "separates equity investments from primary market issuances, debt securities, "
        "or direct investment in businesses. A reader should compare like-for-like "
        "net flows and not treat gross subscriptions, FDI, portfolio equity and debt "
        "as interchangeable measures. The data series includes annual figures for "
        "2015 through 2025, with reported amounts and methodological notes. " * 3)


def test_fpi_question_recognizes_multi_channel_india_research():
    assert _is_fpi_flow_research(QUESTION)


def test_fpi_query_plan_covers_distinct_hypothesis_dimensions():
    queries = _fpi_research_queries(QUESTION)
    assert len(queries) == 12
    joined = "\n".join(queries).casefold()
    for term in ("sebi", "cdsl", "primary market", "nifty 50", "sector", "rbi.org.in", "dpiit.gov.in", "far vrr", "gautam duggad"):
        # FAR/VRR are present as full terms in the debt angle; normalize the spacing in a robust way.
        if term == "far vrr":
            assert "far vrr" in joined or ("far" in joined and "vrr" in joined)
        else:
            assert term in joined


def test_deep_query_plan_deduplicates_and_obeys_cap():
    result = _complete_deep_research_query_plan(["same query", "same query", "another query"], QUESTION, None, max_queries=5)
    assert len(result) == 5
    assert len({q.casefold() for q in result}) == len(result)


def test_fpi_relevance_gate_keeps_fdi_and_primary_market_counterevidence():
    sources = [
        {"title": "India foreign direct investment annual data", "url": "https://dpiit.gov.in/fdi/factsheet",
         "snippet": "Sector-wise FDI inflows into India 2015-2025", "fullContent": _body("Foreign direct investment (FDI) sector-wise inflows")},
        {"title": "India IPO and QIP primary market data", "url": "https://www.sebi.gov.in/statistics/primary-market.html",
         "snippet": "Primary market annual public issue statistics", "fullContent": _body("IPO, QIP and primary market investment")},
        {"title": "Celebrity recipe collections", "url": "https://example.com/recipes",
         "snippet": "Food and recipe information", "fullContent": "Cooking instructions and ingredients with no investment data."},
    ]
    kept = _filter_relevant_sources(sources, QUESTION, None, require_context=True, report_tier="in_depth")
    urls = {row["url"] for row in kept}
    assert "https://dpiit.gov.in/fdi/factsheet" in urls
    assert "https://www.sebi.gov.in/statistics/primary-market.html" in urls
    assert "https://example.com/recipes" not in urls


def test_research_coverage_is_transparent_about_thin_sources():
    thin = audit_research_coverage([], tier="in_depth", query_count=12, requested_pages=100)
    assert thin["status"] == "thin"
    assert thin["uniqueSourceCount"] == 0
    assert any("Unique source count" in gap for gap in thin["coverageGaps"])


def test_research_coverage_counts_distinct_hosts_and_fetched_pages():
    sources = []
    for index in range(36):
        host = f"publisher{index}.example.org"
        sources.append({"title": f"Research source {index}", "url": f"https://{host}/report/{index}",
                        "pageFetchStatus": "ok", "evidenceOrigin": "publisher_page", "pageContent": "Fetched full publisher page."})
    sources.extend([
        {"title": "SEBI statistics", "url": "https://www.sebi.gov.in/statistics/fpi-investment.html",
         "pageFetchStatus": "ok", "evidenceOrigin": "publisher_page", "pageContent": "Official data."},
        {"title": "RBI statistics", "url": "https://www.rbi.org.in/scripts/Statistics.aspx",
         "pageFetchStatus": "ok", "evidenceOrigin": "publisher_page", "pageContent": "Official data."},
    ])
    result = audit_research_coverage(sources, tier="in_depth", query_count=12, requested_pages=100, rendered_pages=100)
    assert result["uniqueSourceCount"] == 38
    assert result["publisherPagesVerified"] == 38
    assert result["officialSourceCount"] == 2
    assert result["status"] == "adequate"


def test_explicit_100_page_target_is_resolved_without_mistaking_years_for_pages():
    assert requested_report_page_count(QUESTION) == 100
    assert requested_report_page_count("Study the last 10 years of foreign flows") is None


def test_deep_search_fanout_is_bounded_per_angle(monkeypatch):
    assert _report_search_results_per_query("brief") == 8
    assert _report_search_results_per_query("detailed") == 12
    assert _report_search_results_per_query("in_depth") == 18
    assert _report_search_min_results(18) <= 6
    monkeypatch.setenv("GG_REPORT_PER_QUERY_SEARCH_CAP", "7")
    assert _report_search_results_per_query("in_depth") == 7


def test_deep_fpi_planner_uses_twelve_distinct_search_angles_without_llm_dependency():
    queries = asyncio.run(_build_multi_angle_search_queries(
        QUESTION, "", "finance", intent=None, report_tier="in_depth"
    ))
    assert len(queries) == 12
    assert len({query.casefold() for query in queries}) == 12
    assert any("gautam duggad" in query.casefold() for query in queries)
    assert any("dpiit.gov.in" in query.casefold() for query in queries)


def test_fpi_coverage_audit_identifies_each_requested_counter_evidence_dimension():
    content = """FPI net equity flows and portfolio outflows; Nifty 50 and Sensex shareholding pattern.
    IPO, QIP and primary market public issue subscriptions; foreign direct investment FDI and DPIIT.
    Debt investment, government securities, FAR and VRR; sector-wise financial services, capital goods and IT.
    Rupee depreciation, US Treasury bond yields, valuations and emerging market flows.
    Gautam Duggad described a ten-year comparison in a dated interview."""
    sources = [{
        "title": f"Source {index}", "url": f"https://publisher{index}.example.org/research/{index}",
        "pageFetchStatus": "ok", "evidenceOrigin": "publisher_page", "pageContent": content,
    } for index in range(36)]
    sources += [{
        "title": "SEBI FPI statistics", "url": "https://www.sebi.gov.in/statistics/fpi-investment.html",
        "pageFetchStatus": "ok", "evidenceOrigin": "publisher_page", "pageContent": content,
    }]
    audit = audit_research_coverage(
        sources, tier="in_depth", query_count=12, requested_pages=100, rendered_pages=100,
        question=QUESTION,
    )
    assert all(audit["topicDimensionCoverage"].values())
    assert audit["missingTopicDimensions"] == []
