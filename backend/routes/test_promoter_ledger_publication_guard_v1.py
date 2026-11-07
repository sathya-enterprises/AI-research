"""Regression coverage for preserving the filing-derived promoter ledger."""
from __future__ import annotations

from datetime import date, timedelta

from routes.report import (
    _sanitize_report_financial_claims_for_publication,
    _sanitize_report_tables_for_publication,
)
from utils.deep_research_coverage import audit_requested_report_scope, requested_indian_fy_quarters
from utils.promoter_disclosures import render_promoter_evidence_report


QUESTION = (
    "Explain the proportion and reasons for stake sales by Indian business promoters. "
    "Include an exhaustive list of all promoter exits in the past 8 quarters."
)
AS_OF = date(2026, 11, 6)


def _publisher_pages() -> list[dict]:
    pages = []
    quarters = requested_indian_fy_quarters(QUESTION, AS_OF)
    for index, (label, start, _end) in enumerate(quarters, start=1):
        filing_date = start + timedelta(days=3)
        text = (
            f"Date: {filing_date:%d %B %Y}\n"
            f"Public exchange disclosure archive page reviewed for {label}. "
            "This filing passage is not treated as proof that no promoter sale occurred."
        )
        if index == 1:
            event_date = start + timedelta(days=3)
            text += (
                f" On {event_date:%d %B %Y}, promoter seller Anita Rao sold 12,000 shares "
                "of Acme Industries Limited; the filing reports a 1.2% stake sale."
            )
        host = "nsearchives.nseindia.com" if index % 2 else "www.bseindia.com"
        pages.append({
            "id": f"S{index}",
            "url": f"https://{host}/filing/{index}",
            "pageFetchStatus": "ok",
            "publisherEvidenceExcerpt": text,
            "pageContent": text,
        })
    return pages


def _rendered_ledger() -> tuple[str, list[dict]]:
    pages = _publisher_pages()
    first_label, first_start, _first_end = requested_indian_fy_quarters(QUESTION, AS_OF)[0]
    events = [{
        "status": "completed",
        "event_date": first_start + timedelta(days=3),
        "disclosure_date": first_start + timedelta(days=3),
        "quarter": first_label,
        "company": "Acme Industries Limited",
        "seller": "Anita Rao",
        "shares": "12,000",
        "percentage": "1.2%",
        "exit_size": "Partial sale; full exit not established",
        "reason": "Reason not stated in the fetched transaction passage.",
        "source_id": "S1",
    }]
    report, _meta = render_promoter_evidence_report(QUESTION, events, pages, as_of_date=AS_OF)
    return report, pages


def test_exact_filing_ledger_survives_generic_financial_and_table_sanitizers():
    report, _pages = _rendered_ledger()

    clean, charts, redactions = _sanitize_report_financial_claims_for_publication(
        report, [], [], deterministic_report=report,
    )
    table_clean, findings = _sanitize_report_tables_for_publication(
        report, [], deterministic_report=report,
    )

    assert clean == report
    assert table_clean == report
    assert charts == []
    assert findings == []
    assert redactions == 0
    assert "| Fiscal quarter |" in table_clean
    assert "| Event date | Quarter | Issuer |" in table_clean


def test_promoter_scope_audit_still_blocks_non_exhaustive_ledger():
    report, pages = _rendered_ledger()

    audit = audit_requested_report_scope(QUESTION, report, pages, as_of_date=AS_OF)
    quarter_coverage = audit["promoterQuarterCoverage"]

    assert quarter_coverage["transactionTableFound"] is True
    assert quarter_coverage["missingQuarters"] == []
    assert quarter_coverage["uncitedQuarters"] == []
    assert audit["exhaustiveCoverage"]["established"] is False
    assert audit["blocking"] is True


def test_modified_ledger_no_longer_uses_the_exact_report_exception():
    report, _pages = _rendered_ledger()
    modified = report.replace("12,000", "12,001", 1)

    clean, _charts, redactions = _sanitize_report_financial_claims_for_publication(
        modified, [], [], deterministic_report=report,
    )
    table_clean, findings = _sanitize_report_tables_for_publication(
        modified, [], deterministic_report=report,
    )

    assert clean != modified
    assert redactions > 0
    assert table_clean != modified
    assert findings
