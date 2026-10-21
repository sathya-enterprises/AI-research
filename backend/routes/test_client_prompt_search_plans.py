"""Regression tests for the three client report prompts and the daily economy prompt."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import routes.report as report


def _queries(question: str) -> list[str]:
    intent = SimpleNamespace(
        resolved_topic=question, evidence_needed=["historical"], source="test",
        is_followup=False, intent_label="report request", wants_report=True, notes="",
    )
    return asyncio.run(report._build_multi_angle_search_queries(question, "", "general", intent))


def test_sector_rotation_uses_eight_quarter_sector_index_and_flow_sources():
    queries = _queries("How can you explain and analyze the sector rotation in Indian stock markets over the past 8 quarters?")
    joined = " ".join(queries).casefold()
    assert len(queries) == 5
    assert "nse" in joined and "quarterly" in joined
    assert "fpi" in joined or "fii" in joined
    assert "sectoral index" in joined or "sector indices" in joined


def test_promoter_exits_prioritize_exchange_disclosures_and_transaction_types():
    queries = _queries("Explain the proportion and reasons for stake sales by Indian business promoters; list promoter exits in the past 8 quarters.")
    joined = " ".join(queries).casefold()
    assert len(queries) == 5
    assert "nse bse" in joined
    assert "shareholding pattern" in joined or "corporate actions" in joined
    assert "bulk deals" in joined or "block deals" in joined
    assert "of" in joined


def test_fta_prompt_prioritizes_official_texts_and_effectiveness_comparison():
    queries = _queries("List the why, what, how of all FTAs signed by India in the past 1 year and compare international effectiveness.")
    joined = " ".join(queries).casefold()
    assert len(queries) == 5
    assert "ministry of commerce" in joined
    assert "tariff schedules" in joined or "agreement text" in joined
    assert "international comparison" in joined and "diversion" in joined


def test_daily_economy_prompt_uses_macro_business_risk_and_comparison_angles():
    queries = _queries("Tell me all that's happening with Indian economy; what shapes progress and what can stall it?")
    joined = " ".join(queries).casefold()
    assert len(queries) == 5
    assert "mospi" in joined or "gdp" in joined
    assert "manufacturing" in joined or "business" in joined
    assert "international comparisons" in joined or "historical macroeconomic trends" in joined
