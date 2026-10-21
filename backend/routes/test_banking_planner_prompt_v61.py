"""Regression coverage for banking-sector query planning and false entity detection."""
from __future__ import annotations

import asyncio
from utils.intent import RequestIntent
from routes.report import _build_multi_angle_search_queries, _extract_company_candidates


BANKING_PROMPT = """Prepare an in-depth analysis of India's banking sector growth outlook over the next three years. Analyze credit growth, deposit growth, GNPA/NNPA, profitability and NIM, RBI policy rates, digital payments, and competitive developments. Retrieve and use multiple credible sources, prioritizing official RBI publications, bank annual reports, government data, and reliable financial research. Extract 10–15 unique numerical data points where supported. Select charts and report sections dynamically."""


def _intent(topic: str, evidence: list[str] | None = None) -> RequestIntent:
    return RequestIntent(
        resolved_topic=topic,
        evidence_needed=evidence or ["historical", "financials", "market_data"],
        source="llm",
    )


def test_long_banking_sector_prompt_uses_metric_specific_official_queries():
    queries = asyncio.run(_build_multi_angle_search_queries(
        BANKING_PROMPT, "", "general", _intent("India's banking sector"),
    ))
    lowered = [q.casefold() for q in queries]
    assert any("bank credit growth" in q and "site:rbi.org.in" in q for q in lowered)
    assert any("bank deposit growth" in q and "site:rbi.org.in" in q for q in lowered)
    assert any("gross npa" in q and "site:rbi.org.in" in q for q in lowered)
    assert any("profitability" in q and "net interest margin" in q for q in lowered)
    assert any("repo rate" in q and "site:rbi.org.in" in q for q in lowered)
    assert any("upi" in q and "site:npci.org.in" in q for q in lowered)
    assert any("quarterly results" in q and "investor relations" in q for q in lowered)
    assert not any("compliance obligations implementation" in q for q in lowered)
    assert len(set(lowered)) == len(lowered)


def test_bank_acronyms_and_source_labels_are_not_company_candidates():
    candidates = _extract_company_candidates(BANKING_PROMPT, _intent("India's banking sector"))
    assert candidates == []


def test_explicit_bank_comparison_still_resolves_real_entities():
    intent = _intent("HDFC Bank and ICICI Bank financial performance", ["comparison"])
    candidates = _extract_company_candidates(
        "Compare HDFC Bank and ICICI Bank financial performance", intent,
    )
    assert candidates == ["HDFC Bank", "ICICI Bank"]


def test_explicit_multi_bank_enumeration_is_preserved():
    intent = _intent("Indian private sector banking", ["comparison"])
    candidates = _extract_company_candidates(
        "Compare SBI, HDFC Bank, ICICI Bank and Axis Bank", intent,
    )
    assert candidates == ["SBI", "HDFC Bank", "ICICI Bank", "Axis Bank"]
