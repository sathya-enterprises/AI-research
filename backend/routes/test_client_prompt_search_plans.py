"""Regression tests for the three client report prompts and the daily economy prompt."""
from __future__ import annotations

import asyncio
from datetime import date
from types import SimpleNamespace

import routes.report as report
from utils.deep_research_coverage import requested_indian_fy_quarters


def _queries(question: str, tier: str = "detailed") -> list[str]:
    intent = SimpleNamespace(
        resolved_topic=question, evidence_needed=["historical"], source="test",
        is_followup=False, intent_label="report request", wants_report=True, notes="",
    )
    return asyncio.run(report._build_multi_angle_search_queries(
        question, "", "general", intent, report_tier=tier,
    ))


def test_sector_rotation_uses_eight_quarter_sector_index_and_flow_sources():
    queries = _queries("How can you explain and analyze the sector rotation in Indian stock markets over the past 8 quarters?")
    joined = " ".join(queries).casefold()
    assert 4 <= len(queries) <= 6
    assert "nse" in joined and "quarterly" in joined
    assert "fpi" in joined or "fii" in joined
    assert "sectoral index" in joined or "sector indices" in joined


def test_promoter_exits_prioritize_exchange_disclosures_and_transaction_types():
    queries = _queries("Explain the proportion and reasons for stake sales by Indian business promoters; list promoter exits in the past 8 quarters.")
    joined = " ".join(queries).casefold()
    assert 4 <= len(queries) <= 6
    assert "nse bse" in joined
    assert "shareholding pattern" in joined or "corporate actions" in joined
    assert "bulk deals" in joined or "block deals" in joined
    assert "of" in joined


def test_fta_prompt_prioritizes_official_texts_and_effectiveness_comparison():
    queries = _queries("List the why, what, how of all FTAs signed by India in the past 1 year and compare international effectiveness.")
    joined = " ".join(queries).casefold()
    assert 4 <= len(queries) <= 6
    assert "ministry of commerce" in joined
    assert "tariff schedules" in joined or "agreement text" in joined
    assert "international comparison" in joined and "diversion" in joined


def test_daily_economy_prompt_uses_macro_business_risk_and_comparison_angles():
    queries = _queries("Tell me all that's happening with Indian economy; what shapes progress and what can stall it?")
    joined = " ".join(queries).casefold()
    assert 4 <= len(queries) <= 6
    assert "mospi" in joined or "gdp" in joined
    assert "manufacturing" in joined or "business" in joined
    assert "international comparisons" in joined or "historical macroeconomic trends" in joined


def test_in_depth_sector_rotation_expands_to_eight_distinct_source_angles():
    queries = _queries(
        "How can you explain and analyze sector rotation in Indian stock markets over the past 8 quarters?",
        "in_depth",
    )
    joined = " ".join(queries).casefold()

    assert len(queries) == 8
    assert len({query.casefold() for query in queries}) == 8
    assert "site:nseindia.com" in joined
    assert "nsdl" in joined
    assert "counterevidence" in joined


def test_sector_rotation_keeps_its_primary_source_plan_when_flows_are_a_subtopic():
    queries = _queries(
        "Explain Indian stock-market sector rotation over the past 8 quarters; use FPI/FII and domestic institutional flows as explanatory evidence.",
        "in_depth",
    )
    joined = " ".join(queries).casefold()

    assert len(queries) == 8
    assert "sectoral indices historical quarterly returns" in joined
    assert "nsdl fpi" in joined
    assert "sector rotation drivers" in joined
    assert "foreign portfolio investor outflows" not in joined


def test_in_depth_promoter_sales_queries_target_each_requested_quarter():
    question = "Explain promoter stake sales and list all promoter exits in the past 8 quarters."
    queries = _queries(question, "in_depth")
    joined = " ".join(queries).casefold()
    requested = requested_indian_fy_quarters(question, date.today())[-8:]

    assert len(queries) == 12
    assert len({query.casefold() for query in queries}) == 12
    assert len(queries[:8]) == len(requested)
    assert "site:nseindia.com" in joined and "site:bseindia.com" in joined
    assert "sast" in joined
    assert "pledge" in joined
    for offset in range(0, 8, 2):
        labels = [label for label, _, _ in requested[offset:offset + 2]]
        start = requested[offset][1]
        end = requested[offset + 1][2]
        nse_query = queries[offset // 2]
        bse_query = queries[4 + offset // 2]
        for query, host in ((nse_query, "nseindia.com"), (bse_query, "bseindia.com")):
            assert f"site:{host}" in query
            assert start.isoformat() in query and end.isoformat() in query
            assert all(label in query for label in labels)


def test_exact_client_promoter_prompt_gets_quarter_specific_filing_searches():
    question = (
        "Explain the proportion and reasons for stake sales by Indian business promoters. "
        "Support it with substantial data that is easy to understand by UNIs. "
        "Will appreciate an exhaustive list of all promoter exits in past 8 quarters. "
        "Add detailed reasoning of this approach by Indian business promoters."
    )
    queries = _queries(question, "in_depth")
    requested = requested_indian_fy_quarters(question, date.today())[-8:]

    assert len(queries) == 12
    for offset in range(0, 8, 2):
        labels = [label for label, _, _ in requested[offset:offset + 2]]
        start = requested[offset][1]
        end = requested[offset + 1][2]
        for query, host in ((queries[offset // 2], "nseindia.com"), (queries[4 + offset // 2], "bseindia.com")):
            assert f"site:{host}" in query
            assert start.isoformat() in query and end.isoformat() in query
            assert all(label in query for label in labels)
    assert any("offer for sale" in query.casefold() or " ofs " in query.casefold() for query in queries[8:])
    assert any("shareholding pattern" in query.casefold() for query in queries[8:])
    assert any("stated" in query.casefold() and "reason" in query.casefold() for query in queries[8:])


def test_in_depth_fta_queries_include_official_signature_dates_and_effectiveness():
    queries = _queries(
        "List all FTAs signed by India in the past 1 year and compare international effectiveness.",
        "in_depth",
    )
    joined = " ".join(queries).casefold()

    assert len(queries) == 8
    assert len({query.casefold() for query in queries}) == 8
    assert "site:commerce.gov.in" in joined
    assert "site:pib.gov.in" in joined
    assert "signing" in joined or "signed" in joined
    assert "trade creation diversion" in joined
