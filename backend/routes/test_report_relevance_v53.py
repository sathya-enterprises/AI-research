from types import SimpleNamespace

from routes.report import (
    _build_strict_subject_search_queries,
    _filter_relevant_sources,
    _subject_acronyms,
    _subject_terms,
)


def _intent(topic="Indian digital payments", evidence=None, label="report request"):
    return SimpleNamespace(
        resolved_topic=topic,
        evidence_needed=evidence or ["comparison", "historical"],
        intent_label=label,
        is_followup=False,
    )


def test_short_topic_acronyms_and_original_metric_words_survive_intent_summarization():
    question = "Compare Indian UPI transaction volume and value for FY2023-24 versus FY2024-25"
    intent = _intent("Indian digital payments")

    assert "upi" in _subject_acronyms(question, intent)
    terms = _subject_terms(question, intent)
    assert "upi" in terms
    assert "transaction" in terms
    assert "volume" in terms
    assert "value" in terms
    assert "fy2023-24" in terms


def test_upi_source_can_be_kept_for_fetch_while_unrelated_sources_are_rejected():
    question = "Compare Indian UPI transaction volume and value for FY2023-24 versus FY2024-25"
    intent = _intent()
    sources = [
        {
            "title": "UPI Product Statistics",
            "snippet": "UPI product statistics by month and financial year.",
            "url": "https://www.npci.org.in/what-we-do/upi/product-statistics",
        },
        {
            "title": "Welcome to Embassy of India",
            "snippet": "Navigation, visa, consular services and official notices.",
            "url": "https://indianembassyusa.gov.in/index",
        },
        {
            "title": "How To Write An Annual Report",
            "snippet": "Templates for balance sheets, executive narratives and financial statements.",
            "url": "https://readz.com/how-to-write-an-annual-report",
        },
    ]

    kept = _filter_relevant_sources(sources, question, intent)
    assert [source["title"] for source in kept] == ["UPI Product Statistics"]


def test_post_fetch_gate_requires_context_beyond_an_acronym_only():
    question = "Compare Indian UPI transaction volume and value for FY2023-24 versus FY2024-25"
    intent = _intent()
    acronym_only = {
        "title": "UPI Awareness Guide",
        "snippet": "UPI basics and account linking.",
        "fullContent": "UPI is a digital payment interface used for person-to-person transfers.",
        "url": "https://example.com/upi-awareness",
    }
    evidence_bearing = {
        "title": "UPI Product Statistics",
        "snippet": "UPI transaction volume and value by financial year.",
        "fullContent": "UPI transaction volumes and transaction values are reported by financial year, with annual series and monthly totals.",
        "url": "https://example.com/upi-statistics",
    }

    assert _filter_relevant_sources([acronym_only], question, intent) == [acronym_only]
    assert _filter_relevant_sources([acronym_only], question, intent, require_context=True) == []
    assert _filter_relevant_sources([evidence_bearing], question, intent, require_context=True) == [evidence_bearing]


def test_strict_comparison_research_retries_with_official_statistics_queries():
    question = "Compare Indian UPI transaction volume and value for FY2023-24 versus FY2024-25"
    queries = __import__("asyncio").run(_build_strict_subject_search_queries(question, _intent()))
    assert len(queries) >= 2
    assert any("official statistics" in query.lower() for query in queries)
    assert any("dataset" in query.lower() for query in queries)
    assert all("upi" in query.lower() or "indian digital payments" in query.lower() for query in queries)


def test_high_trust_host_cannot_make_an_off_topic_page_relevant():
    question = "Compare Indonesian coal export volume in 2022 and 2023"
    intent = _intent("Indonesian coal exports", label="financial")
    off_topic_official = {
        "title": "RBI Annual Report",
        "snippet": "Monetary policy, banking supervision, and currency management.",
        "fullContent": "The central bank reviews inflation and bank liquidity.",
        "url": "https://www.rbi.org.in/Scripts/AnnualReportPublications.aspx",
    }
    assert _filter_relevant_sources([off_topic_official], question, intent) == []


def test_strict_retry_preserves_original_metrics_and_periods():
    question = "Compare Indian UPI transaction volume and value for FY2023-24 versus FY2024-25"
    queries = __import__("asyncio").run(
        _build_strict_subject_search_queries(question, _intent("Indian digital payments"))
    )
    joined = " ".join(queries).casefold()
    for token in ("upi", "transaction", "volume", "value", "fy2023-24", "fy2024-25"):
        assert token in joined
