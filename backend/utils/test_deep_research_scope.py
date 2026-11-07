from datetime import date

from utils.deep_research_coverage import (
    audit_requested_report_scope,
    extract_verified_fta_signatures,
    requested_indian_fy_quarters,
)


AS_OF = date(2026, 11, 6)
PROMOTER_QUESTION = (
    "Provide an exhaustive list of all promoter stake sales in the past 8 quarters."
)
FTA_QUESTION = "List all FTAs signed by India in the past 1 year."
FTA_WINDOW = "November 6, 2025 through November 6, 2026"


def _promoter_sources():
    return [
        {
            "id": "S1",
            "url": "https://www.nseindia.com/companies-listing/corporate-filings",
            "pageFetchStatus": "ok",
            "evidenceOrigin": "publisher_page",
            "publisherEvidenceExcerpt": "NSE promoter shareholding disclosures.",
        },
        {
            "id": "S2",
            "url": "https://www.bseindia.com/corporates/ann.html",
            "pageFetchStatus": "ok",
            "evidenceOrigin": "publisher_page",
            "publisherEvidenceExcerpt": "BSE promoter shareholding disclosures.",
        },
    ]


def _promoter_table(labels, *, cite=True):
    rows = [
        "| Quarter | Verified promoter exits / disclosure coverage |",
        "| --- | --- |",
    ]
    citation = " [S1, S2]" if cite else ""
    rows.extend(
        f"| {label} | No verified exits identified in the searched exchange filings{citation} |"
        for label in labels
    )
    return "\n".join(rows)


def _fta_source(source_id="S1", host="www.commerce.gov.in"):
    return {
        "id": source_id,
        "url": f"https://{host}/trade-agreements/example",
        "title": "India example free trade agreement signature",
        "pageFetchStatus": "ok",
        "evidenceOrigin": "publisher_page",
        "publisherEvidenceExcerpt": (
            "India signed the example free trade agreement with Test Partner on 12 July 2026."
        ),
    }


def _fta_report(signature_date="12 July 2026", citation="[S1]"):
    return (
        f"Signature window: {FTA_WINDOW}.\n\n"
        "| Agreement | Signature date | Implementation status | Source |\n"
        "| --- | --- | --- | --- |\n"
        f"| Example trade agreement with Test Partner | {signature_date} | "
        f"Implementation status pending | {citation} |"
    )


def test_eight_past_quarters_exclude_the_current_incomplete_quarter():
    periods = requested_indian_fy_quarters(PROMOTER_QUESTION, AS_OF)

    assert [label for label, _, _ in periods] == [
        "Q3 FY25", "Q4 FY25", "Q1 FY26", "Q2 FY26",
        "Q3 FY26", "Q4 FY26", "Q1 FY27", "Q2 FY27",
    ]


def test_exhaustive_promoter_coverage_also_requires_a_complete_registry_source():
    labels = [label for label, _, _ in requested_indian_fy_quarters(PROMOTER_QUESTION, AS_OF)]
    result = audit_requested_report_scope(
        PROMOTER_QUESTION,
        _promoter_table(labels),
        _promoter_sources(),
        as_of_date=AS_OF,
    )

    assert result["blocking"] is True
    assert result["promoterQuarterCoverage"]["missingQuarters"] == []
    assert result["promoterQuarterCoverage"]["uncitedQuarters"] == []
    assert result["officialExchangeCoverage"] == {"nse": True, "bse": True}
    assert result["exhaustiveCoverage"] == {
        "requested": True,
        "established": False,
        "verifiedRegistrySourceIds": [],
    }


def test_promoter_coverage_blocks_missing_or_uncited_quarters_and_snippet_only_sources():
    labels = [label for label, _, _ in requested_indian_fy_quarters(PROMOTER_QUESTION, AS_OF)]
    missing_one = labels[:-1]
    snippet_sources = [
        {
            "id": "S1", "url": "https://www.nseindia.com/filings",
            "pageFetchStatus": "unknown", "evidenceOrigin": "search_extract",
            "evidenceExcerpt": "NSE promoter shareholding disclosures.",
        },
        {
            "id": "S2", "url": "https://www.bseindia.com/filings",
            "pageFetchStatus": "unknown", "evidenceOrigin": "search_extract",
            "evidenceExcerpt": "BSE promoter shareholding disclosures.",
        },
    ]
    result = audit_requested_report_scope(
        PROMOTER_QUESTION,
        _promoter_table(missing_one, cite=False),
        snippet_sources,
        as_of_date=AS_OF,
    )

    assert result["blocking"] is True
    assert result["promoterQuarterCoverage"]["missingQuarters"] == [labels[-1]]
    assert len(result["promoterQuarterCoverage"]["uncitedQuarters"]) == len(missing_one)
    assert result["officialExchangeCoverage"] == {"nse": False, "bse": False}


def test_promoter_ownership_snapshot_cannot_substitute_for_exit_coverage_table():
    labels = [label for label, _, _ in requested_indian_fy_quarters(PROMOTER_QUESTION, AS_OF)]
    report = (
        "| Quarter | Promoter shareholding | Institutional shareholding |\n"
        "| --- | --- | --- |\n"
        + "\n".join(f"| {label} | 48% [S1] | 22% [S2] |" for label in labels)
    )

    result = audit_requested_report_scope(PROMOTER_QUESTION, report, _promoter_sources(), as_of_date=AS_OF)

    assert result["blocking"] is True
    assert result["promoterQuarterCoverage"]["transactionTableFound"] is False
    assert result["promoterQuarterCoverage"]["missingQuarters"] == labels
    assert any("ownership snapshots alone" in flag for flag in result["flags"])


def test_promoter_no_exit_quarter_requires_both_exchange_citations_on_the_row():
    labels = [label for label, _, _ in requested_indian_fy_quarters(PROMOTER_QUESTION, AS_OF)]
    report = _promoter_table(labels).replace("[S1, S2]", "[S1]")

    result = audit_requested_report_scope(PROMOTER_QUESTION, report, _promoter_sources(), as_of_date=AS_OF)

    assert result["blocking"] is True
    assert result["promoterQuarterCoverage"]["noVerifiedExitRowsMissingBothExchangeCitations"] == labels


def test_promoter_transaction_rows_need_cited_official_exchange_filing():
    question = "List all promoter exits in the past 8 quarters."
    labels = [label for label, _, _ in requested_indian_fy_quarters(question, AS_OF)]
    rows = [
        "| Quarter | Promoter exit transaction | Company | Source |",
        "| --- | --- | --- | --- |",
        f"| {labels[0]} | Promoter sold 12% stake | Example Ltd | [S3] |",
    ]
    report = "\n".join(rows)
    sources = _promoter_sources() + [{
        "id": "S3", "url": "https://example.com/news", "pageFetchStatus": "ok",
        "evidenceOrigin": "publisher_page", "publisherEvidenceExcerpt": "Promoter sold 12% stake.",
    }]

    result = audit_requested_report_scope(question, report, sources, as_of_date=AS_OF)

    assert result["blocking"] is True
    assert result["promoterQuarterCoverage"]["transactionRowsMissingExchangeCitations"] == [labels[0]]


def test_fta_signature_audit_requires_an_in_window_date_and_cited_official_page():
    result = audit_requested_report_scope(
        FTA_QUESTION,
        _fta_report(),
        [_fta_source()],
        as_of_date=AS_OF,
    )

    assert result["blocking"] is False
    assert result["ftaSignatureCoverage"]["explicitWindow"] is True
    assert result["ftaSignatureCoverage"]["signatureDatesInWindow"] == ["2026-07-12"]
    assert result["ftaSignatureCoverage"]["officialCitedSource"] is True


def test_fta_effectiveness_date_cannot_substitute_for_an_out_of_window_signature():
    result = audit_requested_report_scope(
        FTA_QUESTION,
        _fta_report("1 November 2025"),
        [_fta_source()],
        as_of_date=AS_OF,
    )

    assert result["blocking"] is True
    assert result["ftaSignatureCoverage"]["datedSignatureTable"] is False
    assert any("in-window dated signature row" in flag for flag in result["flags"])


def test_fta_signature_date_requires_a_cited_official_indian_publisher_page():
    result = audit_requested_report_scope(
        FTA_QUESTION,
        _fta_report(citation="[S2]"),
        [_fta_source("S1"), _fta_source("S2", "example.com")],
        as_of_date=AS_OF,
    )

    assert result["blocking"] is True
    assert result["ftaSignatureCoverage"]["officialCitedSource"] is False
    assert any("verified official Indian source" in flag for flag in result["flags"])


def test_explicit_no_verified_fta_signing_result_can_pass_with_official_citation():
    report = (
        f"Signature window: {FTA_WINDOW}.\n"
        "No FTA signed by India in this window was verified in the official records [S1]."
    )
    source = {
        **_fta_source(),
        "publisherEvidenceExcerpt": (
            "Official search of India's free trade agreement signing records and signature dates."
        ),
    }
    result = audit_requested_report_scope(
        FTA_QUESTION,
        report,
        [source],
        as_of_date=AS_OF,
    )

    assert result["blocking"] is False
    assert result["ftaSignatureCoverage"]["explicitNoVerifiedSigning"] is True


def test_fta_window_accepts_zero_padded_dates_with_narrow_no_break_spaces():
    report = _fta_report().replace(
        FTA_WINDOW,
        "Nov\u202f06\u202f2025 through Nov\u202f06\u202f2026",
    )

    result = audit_requested_report_scope(FTA_QUESTION, report, [_fta_source()], as_of_date=AS_OF)

    assert result["blocking"] is False
    assert result["ftaSignatureCoverage"]["explicitWindow"] is True


def test_official_fta_signing_extractor_uses_fetched_page_date_and_partner():
    source = {
        "id": "S18",
        "url": "https://www.commerce.gov.in/trade-agreements/india-new-zealand-fta.pdf",
        "title": "India-New Zealand Free Trade Agreement signed by the two countries",
        "pageFetchStatus": "ok",
        "evidenceOrigin": "publisher_page",
        "pageContent": (
            "Print XClose 27-April-2026 16:05 IST. India and New Zealand today signed the "
            "India-New Zealand Free Trade Agreement at Bharat Mandapam."
        ),
    }

    events = extract_verified_fta_signatures(FTA_QUESTION, [source], as_of_date=AS_OF)

    assert len(events) == 1
    assert events[0]["source_id"] == "S18"
    assert events[0]["partner"] == "New Zealand"
    assert events[0]["signature_date"].isoformat() == "2026-04-27"


def test_official_fta_signing_extractor_rejects_search_only_and_future_events():
    search_only = {
        "id": "S18", "url": "https://www.commerce.gov.in/trade-agreements/example",
        "title": "India-New Zealand Free Trade Agreement signed",
        "pageFetchStatus": "unknown", "evidenceOrigin": "search_extract_only",
        "evidenceExcerpt": "27-April-2026 India and New Zealand today signed the FTA.",
    }
    future = {
        "id": "S19", "url": "https://www.commerce.gov.in/trade-agreements/example",
        "title": "India-New Zealand Free Trade Agreement scheduled to be signed",
        "pageFetchStatus": "ok", "evidenceOrigin": "publisher_page",
        "pageContent": "27-April-2026 The agreement is expected to be signed next month.",
    }

    assert extract_verified_fta_signatures(FTA_QUESTION, [search_only, future], as_of_date=AS_OF) == []


def test_official_fta_signing_extractor_uses_signature_dates_not_effective_dates_or_announcements():
    oman_signing = {
        "id": "S16",
        "url": "https://www.commerce.gov.in/files/2026-02/India-and-Oman-CEPA-dated-18.12.2025.pdf",
        "title": "Press Release: Press Information Bureau",
        "pageFetchStatus": "ok",
        "evidenceOrigin": "publisher_page",
        "pageContent": (
            "Ministry of Commerce & Industry India and Oman sign Comprehensive Economic Partnership Agreement (CEPA). "
            "The India-Oman Comprehensive Economic Partnership Agreement (CEPA) was signed in Muscat."
        ),
    }
    new_zealand_effective = {
        "id": "S37",
        "url": "https://www.commerce.gov.in/india-new-zealand-fta.pdf",
        "title": "India-New Zealand FTA to Enter into Force from 20 October 2026",
        "pageFetchStatus": "ok",
        "evidenceOrigin": "publisher_page",
        "pageContent": (
            "21-September-2026 19:01 IST. The India-New Zealand FTA will enter into force from 20 October 2026. "
            "The India-New Zealand FTA was signed on 27th April, 2026 at Bharat Mandapam."
        ),
    }
    summary_only = {
        "id": "S28",
        "url": "https://www.commerce.gov.in/india-ftas-2025-26.pdf",
        "title": "India's Free Trade Agreements (2025-26)",
        "pageFetchStatus": "ok",
        "evidenceOrigin": "publisher_page",
        "pageContent": (
            "Date of Signing of the Agreement. The India-New Zealand FTA was announced on 22 December 2025. "
            "The India-EU FTA was announced on 27 January 2026."
        ),
    }
    uk_effective = {
        "id": "S15",
        "url": "https://www.commerce.gov.in/india-uk-ceta.pdf",
        "title": "India-UK CETA Set to Enter into Force",
        "pageFetchStatus": "ok",
        "evidenceOrigin": "publisher_page",
        "pageContent": (
            "17-June-2026. CETA will enter into force on 15 July 2026. "
            "The India-UK CETA was signed on 24 July 2025."
        ),
    }

    events = extract_verified_fta_signatures(
        FTA_QUESTION,
        [oman_signing, new_zealand_effective, summary_only, uk_effective],
        as_of_date=AS_OF,
    )

    assert [(event["partner"], event["signature_date"].isoformat()) for event in events] == [
        ("Oman", "2025-12-18"),
        ("New Zealand", "2026-04-27"),
    ]
