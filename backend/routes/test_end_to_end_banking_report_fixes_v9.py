from utils.intent import RequestIntent
from routes.report import (
    _filter_relevant_sources,
    _is_sector_rotation_report_request,
    _extract_company_candidates,
    _source_evidence_quality_audit,
)
from utils.markdown_normalizer import normalize_report_markdown


def _intent(topic="India banking sector performance"):
    return RequestIntent(
        resolved_topic=topic,
        evidence_needed=["historical", "financials", "comparison"],
        source="llm",
    )


def test_banking_sector_performance_does_not_trigger_equity_sector_rotation_gate():
    question = (
        "Analyze India's banking sector performance and outlook through 2029. "
        "Investigate credit and deposit growth, asset quality, capital adequacy and profitability."
    )
    assert not _is_sector_rotation_report_request(question)
    assert _is_sector_rotation_report_request("Analyze NSE sector rotation and quarterly sector return matrix")


def test_latest_rbi_data_release_ranks_above_old_dated_annual_publication_archive():
    question = "Analyze India's banking sector performance and outlook through 2029 using the latest available evidence."
    old_archive = {
        "title": "Trend and Progress of Banking in India - Reserve Bank of India",
        "url": "https://rbi.org.in/scripts/AnnualPublications.aspx?fromdate=10%2F21%2F2009&head=Trend+and+Progress+of+Banking+in+India&todate=10%2F23%2F2009",
        "snippet": "Historical bank credit, deposits, GNPA, profitability, capital adequacy and interest margins for 2008-09.",
        "fullContent": "Bank credit grew in 2008-09. Deposits, GNPA, profitability, capital adequacy and net interest margin are discussed in historical appendices.",
    }
    current_release = {
        "title": "Press Releases | Official Website of Reserve Bank of India",
        "url": "https://rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=63477",
        "snippet": "Date : Aug 31, 2026\nQuarterly Basic Statistical Return (BSR)-1 on Credit by Scheduled Commercial Banks - June 2026. Bank credit growth accelerated to 16.5 per cent at end-June 2026 from 9.9 per cent a year ago.",
        "fullContent": "Quarterly Basic Statistical Return on Credit by Scheduled Commercial Banks, June 2026. Bank credit growth accelerated to 16.5 per cent at end-June 2026 from 9.9 per cent a year ago.",
    }
    kept = _filter_relevant_sources([old_archive, current_release], question, _intent(), report_tier="detailed")
    urls = [item["url"] for item in kept]
    assert current_release["url"] in urls
    assert old_archive["url"] in urls
    assert urls.index(current_release["url"]) < urls.index(old_archive["url"])


def test_latest_rbi_release_survives_post_fetch_single_metric_gate():
    question = "Analyze India's banking sector performance and outlook through 2029 using the latest available evidence."
    current_release = {
        "title": "Press Releases | Official Website of Reserve Bank of India",
        "url": "https://rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=63477",
        "snippet": "Quarterly Basic Statistical Return on Credit by Scheduled Commercial Banks, June 2026; bank credit growth was 16.5 per cent.",
        "fullContent": "Quarterly Basic Statistical Return on Credit by Scheduled Commercial Banks, June 2026. Bank credit growth was 16.5 per cent.",
        "pageContent": "Quarterly Basic Statistical Return on Credit by Scheduled Commercial Banks, June 2026. Bank credit growth was 16.5 per cent.",
        "pageFetchStatus": "ok",
        "pageFetchRelevanceMatched": True,
    }
    kept = _filter_relevant_sources([current_release], question, _intent(), require_context=True, report_tier="detailed")
    assert kept == [current_release]


def test_section_to_complete_control_line_is_removed_from_rendered_report():
    report = (
        "## Findings\nSupported finding.\n\n"
        "[[SECTION TO COMPLETE]] Macro Economic Context and Outlook\n\n"
        "## Macro Economic Context and Outlook\nReal content.\n\n"
        "[[[SECTION TO COMPLETE]]]\n"
    )
    cleaned = normalize_report_markdown(report)
    assert "SECTION TO COMPLETE" not in cleaned
    assert cleaned.count("Macro Economic Context and Outlook") == 1
    assert "Real content." in cleaned


def test_instruction_words_are_not_treated_as_companies_for_market_data_lookups():
    question = (
        "Analyze India's banking sector performance and outlook through 2029, using the latest available evidence.\n\n"
        "Investigate credit and deposit growth. Retrieve relevant official RBI publications and extract the data."
    )
    assert _extract_company_candidates(question, _intent()) == []


def test_successfully_fetched_hybrid_publisher_source_is_not_mislabeled_search_only():
    result = _source_evidence_quality_audit([{
        "id": "S1",
        "title": "RBI bank credit release",
        "pageFetchStatus": "ok",
        "pageFetchRelevanceMatched": True,
        "evidenceExcerptOrigin": "publisher_page_and_search_extract",
        "pageContent": "Bank credit growth accelerated to 16.5 per cent at end-June 2026.",
        "_citedClaimGroups": [{"groupId": "g1", "claimTokens": [], "sourceIds": ["S1"]}],
    }])
    assert result["provenanceWarnings"] == []
