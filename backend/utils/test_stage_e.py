"""Stage E: number check = report.py's check, calculated labels, outline merges, reader-facing citations."""
import asyncio
import json
import re
from pathlib import Path

from utils.evidence_store import Passage

# The six claims that kept the Stage D live report a draft, with the passages they cited.
SOURCES = {
    "S8": "FIIs have been net sellers for the third straight month in Sep'25, though at a slower pace of USD -1.6 bn, while "
          "DII inflows remained strong at USD 7.4 bn. In CY25, FIIs have pulled out only $17 Bn, while DIIs have invested $65 bn.",
    "S81": "Foreign portfolio investors sold Indian equities worth $467 million in April 2026.",
    "S9": "Tariff escalation remains a risk for exporters; the trade deficit widened in the quarter.",
    "S68": "Nifty PSE | +0.50% | +5.02% | +3.24%",
    "S135": "Media advertising inventory declined 13% YoY.",
    "S11": "GST Collections Rs 1.95 lakh cr (+13.9% YoY) Jun 2026. The Markov-switching model puts 88.1% posterior "
           "probability on Risk-off against just 6% on Risk-on.",
    "S12": "Domestically, fiscal and monetary support are aligning to revive demand, with a /uni20B92.3 trillion stimulus "
           "package, income tax and GST relief. The rupee touched its all-time low of 91.15 on Dec 14, 2025.",
    "S131": "Revenues 1,543 1,508 2.3% 1,421 8.6%",
    "S80": "Imported components remain a cost pressure for EMS firms.",
}
CLAIMS = {
    "dii": "DII net buying across the market (totaling $7.4 billion in September 2025) absorbed the FPI cash sell-offs [S8, S81].",
    "gdp": "These tariffs could shave 0.5% to 0.7% off India's GDP growth (calculated: 0.4% + 0.3% from various tariff components) [S9, S68, S135].",
    "gst": "GST collections reached ₹1.95 lakh crore in June 2026, a 13.9% year-on-year increase [S11].",
    "markov": "The Markov-switching model placed the probability of a risk-off state at 88.1% and the composite risk-appetite score at -0.59 [S11].",
    "stimulus": "A ₹2.3 trillion stimulus package and income tax relief measures have been aligned to revive domestic demand [S12, S131].",
    "rupee": "A weak rupee (which touched 91.15 in late 2025) keeps input cost pressure high for importers [S12, S80].",
}


def _auditor():
    from utils.evidence_check import manifest_auditor
    manifest = [{"id": sid, "title": f"Source {sid}", "url": f"https://example.org/{sid.lower()}"} for sid in SOURCES]
    enriched = [{**m, "fullContent": SOURCES[m["id"]], "pageContent": SOURCES[m["id"]], "pageFetchStatus": "ok"} for m in manifest]
    return manifest_auditor(manifest, enriched)


def test_six_stage_d_claims_are_judged_by_value_unit_and_metric():
    report = "## Findings\n\n" + "\n\n".join(CLAIMS.values())
    flagged = _auditor()(report)
    flagged_keys = {k for k, claim in CLAIMS.items() if any(claim[:40] in str(g.get("claim") or "") for g in flagged)}
    # Genuinely unsupported: the 0.5-0.7% range (wrong calculated label) and the -0.59 score.
    assert flagged_keys == {"gdp", "markov"}
    # Supported once the check reads the evidence correctly: "USD 7.4 bn" for DII inflows (acronyms are
    # subjects), "Rs 1.95 lakh cr" ("cr" = crore), "/uni20B92.3 trillion" (= ₹2.3 trillion), the rupee at 91.15.


def test_flagged_claims_are_dropped_before_report_checks_and_logged(caplog):
    from utils.evidence_check import fix_flagged_claims
    report = "## Findings\n\n" + "\n\n".join(CLAIMS.values())
    with caplog.at_level("INFO"):
        fixed, removed, still = fix_flagged_claims(report, _auditor())
    assert still == []
    assert "0.5% to 0.7%" not in fixed and "-0.59" not in fixed
    assert "₹1.95 lakh crore" in fixed and "91.15" in fixed and "$7.4 billion" in fixed
    assert len(removed) == 2 and "V2 evidence check removed a claim" in caplog.text


def test_calculated_label_needs_cited_inputs_and_correct_arithmetic():
    from utils.number_policy import apply_number_policy, number_set, verify_calculation_labels, number_pool
    texts = {"S9.1": SOURCES["S9"], "S14.2": "15 Oct | −47,955.00 / 30 Sept | −21,745.00 / 15 Sept | −14,116.00"}
    bad = "Tariffs could shave 0.5% to 0.7% off GDP (calculated: 0.4% + 0.3% from various tariff components) [S9.1]."
    assert "not in the cited passages" in verify_calculation_labels(bad, number_pool({"S9.1": texts["S9.1"]}))
    good = "FPIs pulled ₹83,816 crore (calculated: 47,955 + 21,745 + 14,116) [S14.2]."
    assert verify_calculation_labels(good, number_pool({"S14.2": texts["S14.2"]})) == ""
    wrong_sum = "FPIs pulled ₹83,000 crore (calculated: 47,955 + 21,745) [S14.2]."
    assert "does not reproduce" in verify_calculation_labels(wrong_sum, number_pool({"S14.2": texts["S14.2"]}))
    r = apply_number_policy(bad + " " + good, passage_texts=texts, allowed_numbers=number_set(texts.values()))
    assert "0.5%" not in r.body and "₹83,816 crore" in r.body and r.removed


def test_a_number_must_match_the_passage_unit():
    from utils.number_policy import apply_number_policy, number_set
    texts = {"S11.4": "GST Collections Rs 1.95 lakh cr (+13.9% YoY) Jun 2026."}
    r = apply_number_policy("GST reached ₹1.95 trillion [S11.4]. GST reached ₹1.95 lakh crore [S11.4].",
                            passage_texts=texts, allowed_numbers=number_set(texts.values()))
    assert "1.95 trillion" not in r.body and "₹1.95 lakh crore" in r.body


# ── outline: qualifying sectors are not merged ───────────────────────────────
def _tiers(names):
    from utils.entity_tiers import EntityEvidence
    return {"deep_dive": [EntityEvidence(name=n, good_text_sources={f"S{i}" for i in range(3)}) for n in names],
            "compact": [], "coverage_note": []}


def test_outline_merging_qualifying_sectors_is_rejected_and_split():
    from utils.report_outline import Section, repair_outline, validate_outline
    tiers = _tiers(["Energy", "Oil & Gas", "Bank", "PSU Bank"])
    sections = [Section("sec-1", "Energy and Oil & Gas: Supply Side Dynamics", role="deep_dive", entity="Energy"),
                Section("sec-2", "Bank: Credit Cycle", role="deep_dive", entity="Bank"),
                Section("sec-3", "PSU Bank: Asset Quality", role="deep_dive", entity="PSU Bank")]
    errors = validate_outline(sections, "in_depth", tiers)
    assert any("merges qualifying sectors Energy, Oil & Gas" in e for e in errors)
    assert not any("PSU Bank" in e and "merges" in e for e in errors)  # "PSU Bank" is not also "Bank"
    decisions: list[str] = []
    fixed = repair_outline(sections, "in_depth", tiers, decisions)
    deep = {s.entity: s.title for s in fixed if s.role == "deep_dive"}
    assert deep["Energy"] == "Energy: Supply Side Dynamics" and "Oil & Gas" in deep
    assert not [e for e in validate_outline(fixed, "in_depth", tiers) if "merges" in e or "own" in e]


# ── reader-facing citations ──────────────────────────────────────────────────
def test_citation_markers_are_stripped_cleanly():
    from utils.citation_display import only_citations, strip_citation_markers as s
    assert s("in the quarter [S129], [S62]. Next") == "in the quarter. Next"
    assert s("grew 5% [S1] and [S2].") == "grew 5%."
    assert s("Banks led (see [S3]).") == "Banks led."
    assert s("rose [S1][S37], while IT [S2.1; S4] fell.") == "rose, while IT fell."
    assert s("$12.58 billion [S12], followed") == "$12.58 billion, followed"
    assert s("Nifty [50] stays [S5]") == "Nifty [50] stays"  # other brackets are untouched
    assert only_citations("[S5].") and not only_citations("Text [S5].")


def test_renderer_strips_markers_after_checks_and_keeps_a_sources_line(monkeypatch):
    from routes.html_report import build_html_report
    monkeypatch.delenv("REPORT_INLINE_CITATIONS", raising=False)
    report = ("## Flows\n\nFPIs sold ₹883 crore of financials [S1]. DIIs bought steadily [S2].\n\n"
              "| Sector | Flow |\n| --- | --- |\n| Bank | -883 [S1] |\n\n## Outlook\n\nRates may ease [S2].")
    sources = [{"id": "S1", "title": "NSDL fortnightly flows", "url": "https://nsdl.co.in/x", "publisher": "NSDL"},
               {"id": "S2", "title": "AMFI monthly", "url": "https://amfiindia.com/y", "publisher": "AMFI"}]
    html = build_html_report(report, "T", "q", "", [], [], [], sources=sources, report_tier="detailed")
    body = html.split('id="data-sources"')[0]
    assert not re.search(r"\[S\d+", re.sub(r"<[^>]+>", " ", body))
    assert body.count('<span class="gg-source-note-label">Sources:</span>') >= 1 and "nsdl" in body.casefold()
    # The Data Sources list still carries every cited id (the PDF export audit resolves citations there).
    assert "[S1] NSDL fortnightly flows" in html and "[S2] AMFI monthly" in html
    monkeypatch.setenv("REPORT_INLINE_CITATIONS", "1")
    audit_copy = build_html_report(report, "T", "q", "", [], [], [], sources=sources, report_tier="detailed")
    assert 'data-source-id="S1"' in audit_copy.split('id="data-sources"')[0]


def test_pipeline_output_keeps_citations_for_the_checks():
    # Stripping is a render step only: the V2 report text that every check sees keeps its [S#] markers.
    from utils.test_report_pipeline_v2 import OUTLINE, FakeGateway, _store
    from utils.report_pipeline_v2 import run_pipeline_v2
    store, manifest = _store()
    out = asyncio.run(run_pipeline_v2(runtime=None, question="Sector rotation in India over the last 4 quarters",
                                      tier="detailed", store=store, manifest=manifest, today="2026-11-07",
                                      gateway=FakeGateway(OUTLINE)))
    assert re.search(r"\[S\d+\]", json.loads(out["raw"])["report"])


def test_v2_tables_are_not_removed_by_the_old_table_sanitizer():
    text = (Path(__file__).resolve().parents[1] / "routes" / "report.py").read_text(encoding="utf-8")
    assert "            _table_integrity_findings = []" in text
    assert "_final_table_findings = []  # V2 tables" in text


def test_takeaway_boxes_never_point_at_figures():
    from utils.section_layout import takeaways
    line = takeaways("Media weakened to a -1.52% return (calculated from Figure 9). Foreign investors sold ₹1,310 crore "
                     "of media shares over the year [S129]. TV news advertising inventory fell 13% year on year [S112].")
    assert line.startswith("> [!TAKEAWAYS]") and "Figure" not in line


def _flags(source_text: str, claim: str) -> list:
    from utils.evidence_check import manifest_auditor
    manifest = [{"id": "S1", "title": "S1", "url": "https://example.org/s1"}]
    enriched = [{**manifest[0], "fullContent": source_text, "pageContent": source_text, "pageFetchStatus": "ok"}]
    return manifest_auditor(manifest, enriched)(f"## A\n\n{claim}")


def test_checker_reads_tables_signs_and_non_statistics_correctly():
    table = ("FORTNIGHT ENDING | NET INVESTMENT (CR) | ASSETS UNDER CUSTODY (CR)\n"
             "31 Oct 2026 | +13,112.00 | 65,25,808.00\n15 Oct 2026 | −47,955.00 | 65,94,378.00")
    # A number-free first row is the header; "(CR)" carries crore to signed cells; outflows = net investment.
    assert _flags(table, "FPIs returned with a net investment of +INR 13,112 crore [S1].") == []
    assert _flags(table, "Mid-October saw outflows of −₹47,955 crore [S1].") == []
    rel = ("Relative rotation of NSE sectoral indices against the Nifty 50, four-week and thirteen-week returns.\n"
           "Sector | Quadrant | Strength | Momentum | 4W rel return | 13W rel return\n"
           "Nifty IT | Leading | 109.0 | 106.7 | +3.84% | +2.61%\nNifty Pharma | Weakening | 105.1 | 95.4 | -3.17% | +3.34%\n"
           "Nifty Auto | Lagging | 97.8 | 97.6 | +0.66% | -7.92%\nNifty Energy | Lagging | 93.5 | 96.1 | -7.31% | -6.61%")
    assert _flags(rel, "Autos underperformed the benchmark over 13 weeks by −7.92% [S1].") == []  # plural, minus, duration
    calc = "15 Oct | −47,955.00 / 30 Sept | −21,745.00 / 15 Sept | −14,116.00 net investment"
    assert _flags(calc, "Between September 15 and October 15, 2026 investors pulled ₹83,816 crore "
                        "(calculated: 47,955 + 21,745 + 14,116) across 15 sectors [S1].") == []
    # Still strict: a value the source does not hold is flagged.
    assert _flags(table, "FPIs returned with a net investment of +INR 14,112 crore [S1].")


def test_short_flagged_table_rows_are_located_and_dash_cells_emptied():
    from utils.evidence_check import _locate
    report = "Intro text here [S1].\n\n| Horizon | Return |\n| --- | --- |\n| **1-Year** | -0.44% [S49] |\n"
    start, end = _locate(report, "**1-Year** | -0.44%")
    assert report[start:end].startswith("| **1-Year**")
    assert _locate("Tiny 1 year 0.44 in prose.", "1-Year | 0.44") is None  # a short fragment never removes prose
