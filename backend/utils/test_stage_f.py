"""Stage F: fact sheet, calculations in code, the two number checks agreeing, the publication report."""
from types import SimpleNamespace

from utils.evidence_check import agreeing_auditor, reconcile_final_audit
from utils.evidence_store import store_from_passages
from utils.fact_sheet import FactSheet, apply_calculations, extract_facts, lookup
from utils.number_policy import apply_number_policy
from utils.publication_report import build_publication_report, render_markdown


def _p(pid, text):
    return SimpleNamespace(passage_id=pid, text=text)


def test_prose_with_currency_and_pipe_titles_is_extracted():
    facts = extract_facts(_p("S21.2", "Financial Services led all selling at ₹11,704 crore.\nOil lost ₹3,351 crore."))
    assert {(f.raw.split("₹")[-1], f.unit) for f in facts} >= {("11,704", "crore"), ("3,351", "crore")}
    # A page title with " | " is prose, not a table row.
    facts = extract_facts(_p("S104.1", "FPI outflows hit telecom stocks | Communications Today Foreign investors pulled money "
                                       "out of Indian telecom stocks through the current financial year, with the sector "
                                       "recording Rs 16,861 crore in net outflows."))
    fact = next(f for f in facts if f.value == "16861")
    assert fact.unit == "crore" and fact.entity != "Financial Services"  # "financial year" is not the sector


def test_table_header_with_digits_and_rank_column():
    text = ("| # | Index | 4-week return (%) | Momentum |\n|---|---|---|---|\n"
            "| 1 | Nifty PSU Bank | -4.64 | 98.5 |\n| 2 | Nifty Bank | -3.79 | 97.4 |")
    facts = extract_facts(_p("S13.2", text))
    psu = next(f for f in facts if f.value == "98.5")
    assert psu.metric == "Momentum" and "Bank" in psu.entity and psu.entity != "1"
    ret = next(f for f in facts if f.value == "-4.64")
    assert ret.unit == "%" and ret.metric.startswith("4-week")


def test_lookup_checks_value_unit_sign_and_metric():
    sheet = FactSheet([_p("S18.1", "| Index | 1-month Returns (%) |\n|---|---|\n| Nifty Consumer Durables | 7.09 |")])
    ok = lookup(sheet, "7.09", sign="", unit="%", sentence="Consumer Durables returned 7.09% over one month",
                passage_ids={"S18.1"})
    assert ok.status == "ok"
    assert lookup(sheet, "7.09", sign="", unit="crore", sentence="returned 7.09 crore", passage_ids={"S18.1"}).status == "unit_mismatch"
    assert lookup(sheet, "7.08", sign="", unit="%", sentence="returned 7.08%", passage_ids={"S18.1"}).status == "absent"


def test_calculations_are_done_in_code_from_facts():
    sheet = FactSheet([_p("S1.1", "FPIs sold ₹47,955 crore in banks and ₹21,745 crore in IT.")])
    body, done, failed = apply_calculations("Together [[calc: 47,955 + 21,745]] crore.", sheet, None)
    assert "69,700 (calculated: 47,955 + 21,745)" in body and done and not failed
    body, _, failed = apply_calculations("[[calc: 47,955 + 99]]", sheet, None)
    assert "[[UNSUPPORTED_CALC]]" in body and failed


def test_policy_corrects_a_citation_instead_of_deleting_a_true_number():
    texts = {"S1.1": "Telecom shares fell after the tariff decision.",
             "S2.1": "Telecom recorded FPI outflows of ₹16,861 crore between April and September."}
    result = apply_number_policy("Telecom saw FPI outflows of ₹16,861 crore [S1.1].", passage_texts=texts,
                                 allowed_numbers=set(), section_id="sec-9")
    assert "16,861" in result.body and "S2.1" in result.body and not result.removed
    assert any("corrected citation" in r["change"] for r in result.rewritten)


def _store():
    return store_from_passages({"S18.1": {"id": "S18.1", "text": "| Index | 1-month Returns (%) |\n|---|---|\n| Nifty Financial Services | 4.06 |"}})


def test_report_py_flags_the_fact_sheet_supports_are_cleared_and_recorded():
    report = "Nifty Financial Services logged a 1-month return of 4.06% [S18].\n\nRealty rose 9.99% [S18]."
    groups = [{"claim": "Nifty Financial Services logged a 1-month return of 4.06%", "sourceIds": ["S18"],
               "unmatchedNumericClaims": ["percent:4.06"]},
              {"claim": "Realty rose 9.99%", "sourceIds": ["S18"], "unmatchedNumericClaims": ["percent:9.99"]}]
    overrides = []
    kept = agreeing_auditor(lambda text: groups, _store(), overrides)(report)
    assert [g["claim"] for g in kept] == ["Realty rose 9.99%"]  # not in the evidence: stays flagged
    assert len(overrides) == 1 and "4.06" in overrides[0]["claim"]

    audits = {"sourceEvidenceAudit": {"blocking": True, "mismatchedCitationGroups": groups[:1], "flags": []}}
    reconcile_final_audit(audits, report, _store())
    assert audits["sourceEvidenceAudit"]["blocking"] is False and audits["sourceEvidenceAudit"]["factSheetOverrides"]


def test_publication_report_lists_each_gate_and_failing_items():
    audits = {"sourceEvidenceAudit": {"mismatchedCitationGroups": [{"claim": "Realty rose 9.99%", "sourceIds": ["S18"],
                                                                    "unmatchedNumericClaims": ["percent:9.99"]}]},
              "publicationAudit": {"danglingChartRefs": [3]}}
    meta = {"numberPolicy": {"sentencesRemoved": [{"reason": "9.99 is not in the evidence", "sentence": "x"}],
                             "numbersRewritten": [], "unresolvedTableValues": []},
            "evidenceCheck": {"stillFlagged": [], "removedClaims": [], "rewrittenClaims": []}}
    report = build_publication_report(outcome={"outcome": "draft", "failedStage": "source_evidence"}, audits=audits, v2_meta=meta)
    status = {g["gate"]: g["status"] for g in report["gates"]}
    assert status["Source evidence (report.py final check)"] == "FAIL"
    assert status["Report structure"] == "FAIL" and status["V2 number policy (fact-sheet lookup)"] == "PASS"
    md = render_markdown(report)
    assert "Realty rose 9.99%" in md and "| Source evidence (report.py final check) | FAIL | 1 |" in md
