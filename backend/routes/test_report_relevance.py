
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from types import SimpleNamespace
from routes.report import _filter_relevant_sources, _build_strict_subject_search_queries, _is_local_company_investor_discovery


def intent(topic, evidence, label):
    return SimpleNamespace(resolved_topic=topic, evidence_needed=evidence, intent_label=label, is_followup=False)


def test_h5n1_relevance_gate_removes_unrelated_sources():
    i = intent("H5N1 avian influenza human transmission risk", ["news", "expert_opinion"], "scientific")
    sources = [
        {"title":"Ebola outbreak update", "snippet":"DRC response funding", "url":"https://example.com/ebola"},
        {"title":"Alzheimer biomarker trial", "snippet":"new dementia research", "url":"https://example.com/alzheimers"},
        {"title":"CDC H5N1 current situation", "snippet":"H5N1 avian influenza human cases and surveillance", "url":"https://cdc.gov/bird-flu"},
        {"title":"WHO H5N1 risk assessment", "snippet":"human transmission, clinical cases and influenza surveillance", "url":"https://who.int/influenza"},
    ]
    kept = _filter_relevant_sources(sources, "Prepare a deep scientific research report on H5N1 avian influenza and human transmission risk", i)
    titles = [x["title"] for x in kept]
    # Both official public-health sources are relevant. Preserve the relevance
    # rank selected by the matcher instead of requiring a brittle WHO/CDC order.
    assert set(titles) == {"WHO H5N1 risk assessment", "CDC H5N1 current situation"}


def test_strict_scientific_queries_are_subject_anchored():
    i = intent("H5N1 avian influenza human transmission risk", ["news"], "scientific")
    qs = _build_strict_subject_search_queries.__wrapped__("x", i) if hasattr(_build_strict_subject_search_queries, "__wrapped__") else None
    # Function is async and deterministic; inspect source expectations through a direct async run.
    import asyncio
    qs = asyncio.run(_build_strict_subject_search_queries("Prepare a deep scientific research report on H5N1 avian influenza", i))
    assert all("H5N1" in q or "h5n1" in q.lower() for q in qs)
    assert all("human" in q.lower() or "clinical" in q.lower() or "genomic" in q.lower() for q in qs)


if __name__ == "__main__":
    test_h5n1_relevance_gate_removes_unrelated_sources()
    test_strict_scientific_queries_are_subject_anchored()
    print("PASS: report relevance/query grounding checks")


def test_promoter_stake_research_is_not_misclassified_as_local_directory_search():
    question = ("Analyze major Indian listed-company promoter stake sales, share pledging "
                "reductions, and promoter exits over the past eight completed quarters.")
    assert not _is_local_company_investor_discovery(question)
    i = intent("Indian listed-company promoter stake sales", ["historical", "market_data", "regulatory", "news"], "financial")
    sources = [
        {"title": "NSE corporate filings: promoter disclosures",
         "snippet": "Regulation 29 disclosures by promoters and promoter groups",
         "url": "https://www.nseindia.com/companies-listing/corporate-filings-regulation-29-promoters"},
        {"title": "BSE bulk and block deals",
         "snippet": "Listed company share transactions and bulk deals",
         "url": "https://www.bseindia.com/markets/equity/eqreports/bulknblockdeals"},
        {"title": "Startup directory in Bengaluru",
         "snippet": "Software companies and angel investors in Bengaluru",
         "url": "https://example.com/bengaluru-startups"},
    ]
    kept = _filter_relevant_sources(sources, question, i)
    urls = {x["url"] for x in kept}
    assert "https://www.nseindia.com/companies-listing/corporate-filings-regulation-29-promoters" in urls
    assert "https://www.bseindia.com/markets/equity/eqreports/bulknblockdeals" in urls
    assert "https://example.com/bengaluru-startups" not in urls
