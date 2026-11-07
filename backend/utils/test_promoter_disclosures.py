from datetime import date

from utils.deep_research_coverage import audit_requested_report_scope, requested_indian_fy_quarters
from utils.promoter_disclosures import (
    extract_verified_promoter_events,
    promoter_event_counts,
    render_promoter_evidence_report,
)


AS_OF = date(2026, 11, 6)
QUESTION = "Explain promoter stake sales and list all promoter exits in the past 8 quarters"


def _source(source_id: str, host: str, body: str, title: str = "Acme Industries Limited") -> dict:
    body = body + "\nThis electronic copy was retrieved directly from the stock exchange website. Please contact the company secretary for questions about the filing."
    return {
        "id": source_id,
        "title": title,
        "url": f"https://{host}/corporate/{source_id.lower()}.pdf",
        "pageFetchFinalUrl": f"https://{host}/corporate/{source_id.lower()}.pdf",
        "pageFetchStatus": "ok",
        "evidenceOrigin": "publisher_page",
        "pageContent": body,
    }


def test_extractor_counts_only_completed_transactions_with_an_event_date():
    completed = _source(
        "S1",
        "nsearchives.nseindia.com",
        "Date: 13 October 2025\nOn 12 October 2025, promoter seller Anita Rao sold 12,000 equity shares of Acme Industries Limited. The filing reports the post-sale holding.",
    )
    proposed = _source(
        "S2",
        "www.bseindia.com",
        "Date: 14 October 2025\nThe promoter proposes to sell 20,000 equity shares of Acme Industries Limited under an Offer for Sale.",
    )
    inter_se = _source(
        "S3",
        "nsearchives.nseindia.com",
        "Date: 20 March 2026\nThe promoter group completed an inter-se transfer of 50,000 equity shares between promoters of Acme Industries Limited.",
    )
    unrelated = _source(
        "S4",
        "www.bseindia.com",
        "Date: 12 October 2025\nVijay Sales reported higher revenue. Promoter shareholding remained 51 percent. No shares were sold.",
    )

    events = extract_verified_promoter_events(
        QUESTION, [completed, proposed, inter_se, unrelated], as_of_date=AS_OF,
    )

    assert [(event["source_id"], event["status"], event["event_date"], event["quarter"]) for event in events] == [
        ("S1", "completed", date(2025, 10, 12), "Q3 FY26"),
        ("S2", "proposed", None, None),
        ("S3", "inter_se_transfer", None, None),
    ]
    assert promoter_event_counts(events, requested_indian_fy_quarters(QUESTION, AS_OF))["Q3 FY26"] == 1


def test_old_agreement_date_is_not_misclassified_as_an_in_window_sale():
    late_filing = _source(
        "S5",
        "nsearchives.nseindia.com",
        "Date: 31 October 2026\nThe promoter seller entered into a Share Purchase Agreement on 20 May 2026 for the sale of 50,000 equity shares of Acme Industries Limited. The proposed transaction remains subject to completion.",
    )

    assert extract_verified_promoter_events(QUESTION, [late_filing], as_of_date=AS_OF) == []


def test_completed_transaction_uses_consummation_date_not_spa_signing_date():
    filing = _source(
        "S6",
        "nsearchives.nseindia.com",
        "Date: 31 October 2026\nI, Deniis Desai (Promoter Seller), entered into a Share Purchase Agreement on 20 May 2026 for sale of 1,71,493 equity shares of Acme Industries Limited. The SPA was consummated on 29 October 2026 and the shares were transferred to the acquirer.",
    )

    events = extract_verified_promoter_events(
        QUESTION, [filing], as_of_date=date(2027, 2, 15),
    )

    assert len(events) == 1
    assert events[0]["event_date"] == date(2026, 10, 29)
    assert events[0]["quarter"] == "Q3 FY27"
    assert events[0]["seller"] == "Deniis Desai"
    assert events[0]["shares"] == "1,71,493"


def test_two_dated_market_sales_in_one_filing_remain_two_transactions():
    filing = _source(
        "S7",
        "www.bseindia.com",
        "Date: 18 September 2025\nThree promoters of Acme Industries Limited, namely Anita Rao, Beena Rao, and Chetan Rao, sold shares in the open market. Details are below:\nOn 05/09/2025, sold 1,30,000 shares by each promoter, totaling 3,90,000 equity shares (1.48%).\nOn 17/09/2025, sold 2,60,000 shares by each promoter, totaling 7,80,000 equity shares (2.96%).",
    )

    events = extract_verified_promoter_events(QUESTION, [filing], as_of_date=AS_OF)

    assert [(event["event_date"], event["shares"], event["percentage"]) for event in events] == [
        (date(2025, 9, 5), "3,90,000", "1.48%"),
        (date(2025, 9, 17), "7,80,000", "2.96%"),
    ]
    assert all(event["seller"] == "Anita Rao, Beena Rao, and Chetan Rao" for event in events)


def test_offer_window_dates_do_not_turn_a_proposed_ofs_into_a_completed_sale():
    filing = _source(
        "S8",
        "www.bseindia.com",
        "Date: 06 August 2025\nWaaree Energies Limited, promoter of Acme Industries Limited, proposes to sell 12,50,000 equity shares. The offer states that shares are proposed to be sold on 07 August 2025 and 08 August 2025 under an Offer for Sale.",
    )

    events = extract_verified_promoter_events(QUESTION, [filing], as_of_date=AS_OF)

    assert len(events) == 1
    assert events[0]["status"] == "proposed"
    assert events[0]["event_date"] is None


def test_unknown_quarters_are_labeled_unknown_and_audited_without_claiming_zero():
    completed = _source(
        "S1",
        "nsearchives.nseindia.com",
        "Date: 13 October 2025\nOn 12 October 2025, promoter seller Anita Rao sold 12,000 equity shares of Acme Industries Limited. The filing reports the post-sale holding.",
    )
    bse_page = _source(
        "S2",
        "www.bseindia.com",
        "Date: 14 October 2025\nThe promoter proposes to sell 20,000 equity shares of Acme Industries Limited under an Offer for Sale.",
    )
    events = extract_verified_promoter_events(QUESTION, [completed, bse_page], as_of_date=AS_OF)
    report, meta = render_promoter_evidence_report(
        QUESTION, events, [completed, bse_page], as_of_date=AS_OF,
    )

    audit = audit_requested_report_scope(
        QUESTION, report, [completed, bse_page], as_of_date=AS_OF,
    )

    assert meta["datedCompletedCount"] == 1
    assert "unknown, not zero" in report
    assert "not a complete issuer-by-issuer registry" in report
    assert audit["promoterQuarterCoverage"]["missingQuarters"] == []
    assert audit["promoterQuarterCoverage"]["uncitedQuarters"] == [
        "Q3 FY25", "Q4 FY25", "Q1 FY26", "Q2 FY26", "Q4 FY26", "Q1 FY27", "Q2 FY27",
    ]
    assert audit["promoterQuarterCoverage"]["unknownRowsMissingCitations"] == audit["promoterQuarterCoverage"]["uncitedQuarters"]
    assert audit["officialExchangeCoverage"] == {"nse": True, "bse": True}
    assert audit["exhaustiveCoverage"]["established"] is False
    assert audit["blocking"] is True
