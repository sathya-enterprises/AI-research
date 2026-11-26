"""Code-first claim verification: claim types, arithmetic checks, figure verification and structured records.
No live model calls: the judge is a stub."""
import asyncio
from types import SimpleNamespace

from utils.claim_check import arithmetic_check, candidates, check_section, claim_type, code_verdict, overlap


def _verdict(sentence, passages):
    cited = list(passages.items())
    return code_verdict(sentence, cited, overlap(sentence, [t for _, t in cited]))


def test_claim_types():
    assert claim_type("Capacity is expected to reach 500 GW by 2030 [S1].") == "forecast"
    assert claim_type("Sales rose from 100 units to 150 units, a 50% increase [S1].") == "calculation"
    assert claim_type("Margins fell to 12% because input costs rose [S1].") == "qualitative"
    assert claim_type("The share was 23.4% [S1].") == "percentage"
    assert claim_type("Exports were higher than imports at 410 billion [S1].") == "comparison"
    assert claim_type("The plant opened in 2019 [S1].") == "date"


def test_arithmetic_is_rounding_aware_and_corrects_a_wrong_percentage():
    # 9.2 -> 16.3 can be 75.7%..78.7% once the inputs' rounding is allowed for: a source's "76%" is consistent
    assert arithmetic_check("Output rose from 9.2 tonnes to 16.3 tonnes, a 76% increase [S1].") is None
    wrong = arithmetic_check("Output rose from 100 tonnes to 150 tonnes, a 75% increase [S1].")
    assert wrong and "50.0%" in wrong[0] and "a 50% increase" in wrong[1]
    # different units are not compared
    assert arithmetic_check("Revenue rose from 100 crore to 2 billion, a 10% increase [S1].") is None


def test_figures_alone_do_not_verify_a_claim():
    # the figure is in the source but means something else (a deal size, not a price band)
    passages = {"S1.1": "The company raised a ₹15,000-crore loan from a consortium of banks last year to fund "
                        "expansion. Separately, analysts discussed handset demand in smaller cities."}
    v = _verdict("Most handsets now sell at sub-₹15,000 price points in smaller cities [S1.1].", passages)
    assert v is None or v["method"] != "figures_in_cited_passages"
    # the figure is there but the claim adds a period the source never names
    passages = {"S2.1": "In the most recent quarter, the brand held 18.2% of shipments, ahead of its rivals."}
    v = _verdict("The brand held 18.2% of shipments in Q1 2024, ahead of rivals [S2.1].", passages)
    assert v is None


def test_a_year_from_the_sources_publication_date_counts_and_the_judge_sees_it():
    passages = {"S5.1": "IndiGo currently operates a total fleet of 426 aircraft, the largest in India."}
    sentence = "As of 2026, IndiGo operated a total fleet of 426 aircraft, the largest in India [S5.1]."
    assert _verdict(sentence, passages) is None                                      # undated: the judge decides
    cited = list(passages.items())
    assert code_verdict(sentence, cited, overlap(sentence, passages.values()), "2026-11-02")["status"] == "VERIFIED"
    judged, _checked, _records = candidates("## Fleet\nThe airline flies 426 aircraft on domestic routes now [S5.1].",
                                            passages, {"S5.1": "2026-11-02"})
    assert not judged or judged[0]["excerpts"][0][1].startswith("(published 2026-11-02)")


def test_figures_words_and_years_together_verify_a_claim():
    passages = {"S3.1": "Solar power led the expansion, with installed capacity increasing from 97.86 GW in 2024 to "
                        "135.81 GW in 2025, registering a growth of 38.8 per cent."}
    v = _verdict("Installed solar capacity reached 135.81 GW in 2025, up from 97.86 GW in 2024 [S3.1].", passages)
    assert v and v["status"] == "VERIFIED"


def test_a_forecast_with_the_writers_own_figure_is_not_verified_by_code():
    passages = {"S4.1": "The ministry expects installed capacity to rise strongly over the coming decade."}
    v = _verdict("Installed capacity is expected to rise to 900 GW over the coming decade [S4.1].", passages)
    assert v is None


def test_records_statuses_and_judge_failure_keeps_claims_for_review():
    passages = {"S1.1": "Retail inflation eased to 4.3 percent in March as vegetable prices fell sharply across cities.",
                "S2.1": "Output rose from 100 tonnes to 150 tonnes over the year."}
    body = ("## Prices\nRetail inflation eased to 4.3 percent in March as vegetable prices fell [S1.1]. "
            "The decline was caused mainly by a collapse in global crude oil demand [S1.1]. "
            "Output rose from 100 tonnes to 150 tonnes, a 75% increase [S2.1].")
    judged, checked, records = candidates(body, passages)
    assert checked == 3 and len(records) == 3
    assert {r["status"] for r in records} == {"VERIFIED", "REQUIRES_REVIEW", "CONTRADICTED"}
    assert all({"id", "sentence", "type", "sources", "status", "method", "confidence", "explanation"} <= set(r)
               for r in records)

    class Broken:
        async def generate(self, **kwargs):
            return SimpleNamespace(ok=False, error="timeout", text="")

    draft = SimpleNamespace(section_id="s1", body=body)
    entry = asyncio.run(check_section(Broken(), draft, passages))
    assert entry["removed"] == [] and "collapse in global crude" in draft.body      # nothing removed on failure
    assert "a 50% increase" in draft.body and entry["corrected"]                    # arithmetic still corrected
    assert entry["byStatus"].get("REQUIRES_REVIEW") == 1

    class Judge:
        async def generate(self, **kwargs):
            data = [{"i": 1, "verdict": "PARTIAL", "reason": "cause only partly stated"}]
            return SimpleNamespace(ok=True, text="", json=lambda: data)

    draft = SimpleNamespace(section_id="s1", body=body)
    entry = asyncio.run(check_section(Judge(), draft, passages))
    assert entry["byStatus"].get("PARTIALLY_VERIFIED") == 1 and entry["removed"] == []
