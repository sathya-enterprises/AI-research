import asyncio
from types import SimpleNamespace

from routes import report


def test_local_company_investor_query_gets_three_targeted_angles():
    topic = "software companies, startups, and investors connected to Electronic City, Bengaluru"
    intent = SimpleNamespace(
        resolved_topic=topic,
        evidence_needed=["financials"],
        intent_label="search audit",
        source="deterministic_search_audit",
    )
    queries = asyncio.run(report._build_multi_angle_search_queries(topic, "", "general", intent=intent))
    assert len(queries) == 3
    assert all("Electronic City" in q and "Bengaluru" in q for q in queries)
    assert any("venture capital" in q.lower() or "angel investors" in q.lower() for q in queries)
    assert all("key financial metrics" not in q.lower() for q in queries)
    assert report._is_local_company_investor_discovery(topic)
    assert report._extract_company_candidates(topic, intent=intent) == []


def test_local_relevance_gate_rejects_general_exa_provider_articles():
    topic = "software companies, startups, and investors connected to Electronic City, Bengaluru"
    intent = SimpleNamespace(resolved_topic=topic, evidence_needed=["general_knowledge"], intent_label="search audit")
    sources = [
        {"title": "Exa review pricing and pros and cons", "url": "https://pipe0.com/reviews/exa", "snippet": "Exa is a search engine for AI applications."},
        {"title": "Electronic City software company directory", "url": "https://example.test/electronic-city-software", "snippet": "Software companies and technology startups located in Electronic City, Bengaluru."},
    ]
    kept = report._filter_relevant_sources(sources, topic, intent)
    assert len(kept) == 1
    assert "Electronic City" in kept[0]["title"]


def test_local_relevance_gate_rejects_electronic_city_homonyms_outside_india():
    topic = "investors for software projects in Electronic City, Bengaluru, Karnataka, India"
    intent = SimpleNamespace(resolved_topic=topic, evidence_needed=["general_knowledge"], intent_label="investor discovery")
    sources = [
        {"title": "Electronic City", "url": "https://exa.ai/library/place/6ncv356z4br", "snippet": "Software company in Johor, Malaysia."},
        {"title": "PT Electronic City Indonesia Tbk", "url": "https://linkedin.com/company/pt-electronic-city-indonesia", "snippet": "Retail company headquartered in Jakarta, Indonesia."},
        {"title": "Around Electronic City · Bengaluru Startup Grid · eChai", "url": "https://echai.ventures/grid/bengaluru/tours/electronic-city", "snippet": "Startups and venture investors in Electronic City, Bengaluru, India."},
    ]
    kept = report._filter_relevant_sources(sources, topic, intent, require_context=True)
    assert len(kept) == 1
    assert "eChai" in kept[0]["title"]
