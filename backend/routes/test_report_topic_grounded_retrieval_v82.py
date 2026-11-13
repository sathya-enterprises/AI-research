from __future__ import annotations

import asyncio

from routes.report import (
    _build_multi_angle_search_queries,
    _append_requested_measure_scope,
    _prioritize_measure_search_queries,
    _research_domain_from_intent,
    _should_use_evidence_chart_gallery,
    _should_fetch_company_fundamentals,
)
from utils.intent import RequestIntent


def test_country_metric_comparison_uses_data_angles_without_finance_queries_or_lookups():
    question = (
        "Compare internet use (% of individuals using the internet) and mobile-cellular subscriptions per 100 people "
        "in India, Kenya, and Brazil from 2015 to 2024. Show annual trends and latest country gaps."
    )
    intent = RequestIntent(
        resolved_topic="internet use and mobile-cellular subscriptions in India, Kenya, and Brazil",
        intent_label="comparison",
        evidence_needed=["comparison", "historical", "market_data", "financials"],
        source="test",
    )

    assert _research_domain_from_intent(question, intent) == "comparison"
    assert not _should_fetch_company_fundamentals(question, ["Kenya", "Brazil"])
    queries = asyncio.run(_build_multi_angle_search_queries(question, "", "general", intent))
    lowered = [query.casefold() for query in queries]
    assert any("official numeric datasets" in query for query in lowered)
    assert any("comparable statistics" in query or "benchmarks" in query for query in lowered)
    assert not any(
        marker in query
        for query in lowered
        for marker in ("financial statements", "company or sector results", "official filings", "market price")
    )


def test_company_fundamentals_require_financial_intent_and_named_companies():
    question = "Compare HDFC Bank and ICICI Bank net profit, revenue, and ROE."
    intent = RequestIntent(resolved_topic="HDFC Bank and ICICI Bank financial performance", evidence_needed=["comparison"])
    assert _research_domain_from_intent(question, intent) == "financial"
    assert _should_fetch_company_fundamentals(question, ["HDFC Bank", "ICICI Bank"])
    assert not _should_fetch_company_fundamentals("Compare Kenya and Brazil internet use", ["Kenya", "Brazil"])
    assert not _should_fetch_company_fundamentals(question, [])


def test_targeted_measure_queries_are_prioritized_inside_the_tier_search_cap():
    selected = _prioritize_measure_search_queries(
        ["base question", "topic general angle", "topic policy angle", "topic historical angle", "topic extra angle"],
        ["measure one official data", "measure two official data"],
        tier="detailed",
    )
    assert selected == [
        "base question",
        "measure one official data",
        "measure two official data",
        "topic general angle",
        "topic policy angle",
        "topic historical angle",
    ]
    assert len(_prioritize_measure_search_queries(["base"], [f"metric {i}" for i in range(12)], tier="detailed")) == 6


def test_requested_measure_scope_guides_web_outline_without_touching_file_reports():
    class Plan:
        def brief(self):
            return "- internet use (%) (India, Kenya)\n- mobile subscriptions per 100 people (India, Kenya)"

    note = _append_requested_measure_scope("Window: 2015-2024", Plan(), has_uploaded_data=False)
    assert "REQUESTED QUANTITATIVE SCOPE" in note
    assert "mobile subscriptions per 100 people" in note
    assert "Window: 2015-2024" in note
    assert _append_requested_measure_scope("Window: 2015-2024", Plan(), has_uploaded_data=True) == "Window: 2015-2024"


def test_chart_gallery_fills_only_the_web_v2_no_chart_case():
    should_build = lambda **kwargs: _should_use_evidence_chart_gallery(
        promoter_exit_request=False, **kwargs,
    )
    assert should_build(v2_used=True, has_uploaded_data=False, charts=[])
    assert not should_build(v2_used=True, has_uploaded_data=False, charts=[{"type": "line"}])
    assert not should_build(v2_used=True, has_uploaded_data=True, charts=[])
