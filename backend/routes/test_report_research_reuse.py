"""Regression tests for report follow-up reuse and source relevance filtering."""
from routes.report import _filter_relevant_sources
from utils.intent import RequestIntent


def _intent(topic: str) -> RequestIntent:
    return RequestIntent(
        resolved_topic=topic,
        intent_label="research",
        evidence_needed=["market_data", "financials"],
        is_followup=True,
        wants_report=True,
        source="test",
    )


def test_generic_data_centre_relevance_rejects_unrelated_sources():
    intent = _intent("India's data-centre industry")
    sources = [
        {"title": "Giving reports", "url": "https://example.com/giving", "snippet": "donor reporting and fundraising"},
        {"title": "India Data Centre Market Outlook", "url": "https://example.com/data-centre", "snippet": "India data center capacity, power and AI demand"},
        {"title": "Salesforce historical reports", "url": "https://example.com/salesforce", "snippet": "historical reporting features"},
    ]
    kept = _filter_relevant_sources(sources, "give as a report", intent)
    titles = [s["title"] for s in kept]
    assert "India Data Centre Market Outlook" in titles
    assert "Giving reports" not in titles
    assert "Salesforce historical reports" not in titles


def test_electric_two_wheeler_aliases_match_common_source_wording():
    intent = _intent("India's electric two-wheeler market")
    sources = [
        {"title": "India EV scooter market outlook", "url": "https://example.com/ev", "snippet": "electric scooter sales and E2W adoption"},
        {"title": "Industrial automation report", "url": "https://example.com/automation", "snippet": "factory automation software"},
    ]
    kept = _filter_relevant_sources(sources, "give as a report", intent)
    titles = [s["title"] for s in kept]
    assert "India EV scooter market outlook" in titles
    assert "Industrial automation report" not in titles


def test_energy_policy_search_angles_stay_in_general_vertical():
    from routes.chat import classify_query
    query = "India's renewable energy growth outlook government policy targets regulation programme results"
    assert classify_query(query) == "general"


def test_strong_finance_terms_are_token_matched_not_substring_matched():
    from routes.chat import classify_query
    assert classify_query("India's renewable energy policy outlook") == "general"
    assert classify_query("LIC share price outlook") == "finance"


def test_broad_sector_research_terms_do_not_force_the_finance_vertical():
    from routes.chat import classify_query

    queries = [
        "India renewable energy market growth and investment outlook for 2027 to 2030",
        "Technology sector market growth outlook and investment in AI infrastructure",
        "Healthcare market growth outlook and investment in public hospital capacity",
        "India economic outlook including GDP growth, inflation and employment",
    ]
    assert all(classify_query(query) == "general" for query in queries)
    assert classify_query("SBI share price and valuation outlook") == "finance"
