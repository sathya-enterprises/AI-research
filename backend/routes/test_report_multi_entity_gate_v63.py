"""Regression: multi-bank comparison collapsed to 1-2 sources (Oct 2026).

Causes covered: instruction words crowding metric words out of the term cap,
one entity's site monopolising the fetch budget, and good pages being dropped by
the keyword gate.
"""
from types import SimpleNamespace

from routes.report import (
    _entity_metric_rescue,
    _interleave_by_entity,
    _subject_terms,
)

QUESTION = (
    "Compare SBI, HDFC Bank, ICICI Bank, and Axis Bank from FY2020 to the latest available reporting "
    "period using official annual reports, RBI publications, investor-relations disclosures, and credible "
    "financial sources. Collect verified data for net profit, total assets, deposits, advances, return on "
    "equity (ROE), and gross NPA ratio. Keep annual and quarterly figures separate."
)


def _intent():
    return SimpleNamespace(resolved_topic=QUESTION[:120], evidence_needed=["comparison", "financials"],
                           intent_label="comparison", is_followup=False)


def test_metric_words_survive_and_instruction_words_are_dropped():
    terms = _subject_terms(QUESTION, _intent())
    for metric in ("profit", "assets", "deposits", "advances", "equity", "gross"):
        assert metric in terms, metric
    for junk in ("using", "collect", "verified", "credible", "publications", "from"):
        assert junk not in terms, junk


def test_each_entity_gets_fetch_slots_even_if_one_site_dominates():
    sources = [{"title": f"Axis Bank doc {i}", "url": f"https://www.axis.bank.in/doc{i}.pdf"} for i in range(15)]
    sources += [
        {"title": "SBI Annual Report FY2025", "url": "https://sbi.bank.in/ar.pdf"},
        {"title": "ICICI Bank results", "url": "https://www.icici.bank.in/q4"},
        {"title": "HDFC Bank IR26", "url": "https://www.hdfc.bank.in/ir26.pdf"},
    ]
    ordered = _interleave_by_entity(sources, QUESTION, _intent(), limit=10)
    top = " ".join(r["url"] for r in ordered[:10])
    for host in ("sbi.bank.in", "icici.bank.in", "hdfc.bank.in", "axis.bank.in"):
        assert host in top, host


def test_entity_metric_rescue_keeps_data_page_and_rejects_boilerplate():
    good = {"title": "ICICI Bank Q4 results", "url": "https://www.icici.bank.in/q4", "pageFetchStatus": "ok",
            "pageContent": "ICICI Bank net profit Rs. 13,502 crore, total assets Rs. 20,10,000 crore, deposits "
                           "Rs. 16,10,000 crore, advances Rs. 13,60,000 crore, ROE 17.8%, GNPA 1.67%, NIM 4.3% " * 3}
    bad = {"title": "ICICI Bank cookie policy", "url": "https://www.icici.bank.in/cookies", "pageFetchStatus": "ok",
           "pageContent": "We use cookies to improve your experience. Accept all cookies. " * 10}
    other = {"title": "Random", "url": "https://example.com", "pageFetchStatus": "ok",
             "pageContent": "net profit 100 crore deposits advances total assets ROE 12% " * 10}
    assert _entity_metric_rescue(good, QUESTION, _intent())
    assert not _entity_metric_rescue(bad, QUESTION, _intent())
    assert not _entity_metric_rescue(other, QUESTION, _intent())
