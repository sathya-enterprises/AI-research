"""Stage C2: chart titles and chart/text coherence, figure numbering, number policy, editorial pass."""
import asyncio
import re

from utils.evidence_store import Passage


# ── chart titles from the chart's own data ───────────────────────────────────
def _store(*passages):
    class S:
        pass
    s = S()
    s.passages = list(passages)
    return s


def test_title_comes_from_data_not_from_an_unrelated_page_heading():
    from utils.chart_titles import title_data_mismatch
    from utils.section_charts import build_chart_candidates
    table = ("TABLE 3 — Information Technology: Green Shoots After a Difficult Year\nCompany | Revenue Q4 FY26 (Rs Cr)\n"
             "Nestle India | 6,748\nTrent | 5,028\nBajaj Consumer Care | 312\nVST Industries | 420\n")
    charts, _ = build_chart_candidates(_store(Passage("S80.6", "S80", table, "table", reliability=0.8)))
    title = charts[0].spec["title"]
    assert "Information Technology" not in title and "Nestle India" in title and "Revenue Q4 FY26" in title
    assert title_data_mismatch(charts[0].spec) == ""


def test_mixed_sector_and_stock_rows_are_not_one_chart():
    from utils.section_charts import build_chart_candidates
    table = ("TABLE 1 — Sector Flow — NSE Sectoral Indices, OI Buildup & Smart Money\nName | 1D Change %\n"
             "Nifty Bank | 0.8\nNifty Pharma | -0.4\nNifty FMCG | 1.1\nNifty Metal | 2.2\nDIXON | 16.33\nHAVELLS | 7.66\n")
    charts, _ = build_chart_candidates(_store(Passage("S12.1", "S12", table, "table", reliability=0.8)))
    labels = [label for _, label, _ in charts[0].points]
    assert "DIXON" not in labels and "Nifty Bank" in labels


def test_price_levels_and_headerless_tables_are_not_charted():
    from utils.section_charts import build_chart_candidates
    levels = "TABLE 1 — Sectoral Indices\nIndex | Price\nNifty Bank | 59,086\nNifty IT | 35,210\nNifty Pharma | 22,410\n"
    headerless = "TABLE 2 — Industry Trends\nMaterials | 5.99%\nChemicals | 4.10%\nFertilizers | 3.20%\nGases | 2.10%\n"
    charts, _ = build_chart_candidates(_store(Passage("S73.1", "S73", levels, "table", reliability=0.8),
                                              Passage("S121.1", "S121", headerless, "table", reliability=0.8)))
    assert charts == []


def test_continuation_tables_become_one_chart_with_a_specific_unique_title():
    from utils.section_charts import build_chart_candidates
    part1 = ("TABLE 9 — Industry Analysis and Scores: Banking, Auto, FMCG - Quarterly Changes\nIndustry | Qtr Change %\n"
             "1 Telecom Services | 14.08\n2 Hardware Technology | 13.71\n3 Media | 8.79\n")
    part2 = ("TABLE 9 — Industry Analysis and Scores (continued)\nIndustry | Qtr Change %\n"
             "4 Textiles | 6.10\n5 Hotels Restaurants & Tourism | 7.63\n6 Pharmaceuticals | 5.48\n")
    charts, _ = build_chart_candidates(_store(Passage("S56.4", "S56", part1, "table", reliability=0.8),
                                              Passage("S56.5", "S56", part2, "table", reliability=0.8)))
    assert len(charts) == 1 and charts[0].passage_ids == ["S56.4", "S56.5"]
    title = charts[0].spec["title"]
    assert "Banking" not in title and title.startswith("Qtr Change %: Telecom Services") and "1 Telecom" not in title


def test_sector_deep_dive_only_takes_charts_that_plot_its_sector():
    from utils.report_outline import Section
    from utils.section_charts import assign_charts, build_chart_candidates
    table = ("TABLE 1 — Top consumer names\nCompany | Revenue Q4 FY26 (Rs Cr)\nNestle India | 6,748\nTrent | 5,028\n"
             "Bajaj Consumer Care | 312\n")
    charts, _ = build_chart_candidates(_store(Passage("S80.6", "S80", table, "table", reliability=0.8)))
    it = Section("sec-2", "IT: Deep-Dive", goal="IT", queries=["revenue"], passage_ids=["S80.6"], role="deep_dive", entity="IT")
    consumer = Section("sec-3", "Consumer and Services", goal="consumer revenue", queries=["consumer revenue"],
                       passage_ids=[], role="compact")
    assign_charts(charts, [it, consumer])
    assert charts[0].section_id == "sec-3"


def test_text_that_describes_other_data_is_a_mismatch():
    from utils.chart_titles import text_chart_mismatch
    sectors = {"title": "1D Change %: Nifty Bank, Nifty Pharma", "series": [{"name": "1D Change %", "data": [
        {"label": "Nifty Bank", "value": 0.8}, {"label": "Nifty Pharma", "value": -0.4}, {"label": "Nifty Metal", "value": 2.2}]}]}
    assert text_chart_mismatch("Figure 5 shows DIXON leading at 16.33, followed by HAVELLS at 7.66.", sectors)
    assert text_chart_mismatch("Figure 5 shows Nifty Metal leading with 2.2%, while Pharma slipped.", sectors) == ""
    assert text_chart_mismatch("Figure 5 shows how breadth improved.", sectors)


# ── figure numbering ──────────────────────────────────────────────────────────
def test_figures_are_numbered_in_document_order():
    from utils.report_pipeline_v2 import finalize_figures
    text = "Figure 1 shows A [S1].\n\n[CHART_1]\n\nFigure 3 shows C [S3].\n\n[CHART_3]\n\nFigure 2 shows B [S2].\n\n[CHART_2]"
    charts = [{"title": "Figure 1: A"}, {"title": "Figure 2: B"}, {"title": "Figure 3: C"}]
    out, charts_out, stats = finalize_figures(text, charts)
    assert [c["title"] for c in charts_out] == ["Figure 1: A", "Figure 2: C", "Figure 3: B"]
    assert re.findall(r"\[CHART_(\d+)\]", out) == ["1", "2", "3"]
    assert "Figure 2 shows C" in out and "Figure 3 shows B" in out and stats["reordered"]


def test_section_cannot_reference_or_caption_figures_it_was_not_given():
    from utils.section_writer import SectionDraft, postprocess

    class Fig:
        def __init__(self, n):
            self.figure_number, self.status, self.drop_reason, self.claim = n, "planned", "", ""

    figs = [Fig(7)]
    raw = ("### Auto\nFigure 4 shows the 3-month returns of sectors [S1.1].\n\n| Figure 4: 3-Month Returns | % |\n| --- | --- |\n"
           "| IT | 7.39 |\n\nFigure 7 shows autos lagging at 7.39% [S1.1].\n<<<SUMMARY>>>\nok")
    draft = postprocess(SectionDraft("s", "t"), raw, allowed_passages={"S1.1"}, allowed_numbers={"7.39", "3"}, figures=figs,
                        min_words=5)
    assert "Figure 4" not in draft.body and "The 3-month returns of sectors" in draft.body
    assert "| 3-Month Returns |" in draft.body and figs[0].status == "referenced"


# ── number policy ─────────────────────────────────────────────────────────────
FLOWS = ("TABLE 27 — All-sector net equity investment by fortnight\nFORTNIGHT ENDING | NET INVESTMENT (CR) | ASSETS UNDER CUSTODY (CR)\n"
         "31 Oct 2026 | +13,112.00 | 65,25,808.00\n15 Oct 2026 | −47,955.00 | 65,94,378.00\n"
         "30 Sept 2026 | −21,745.00 | 66,17,164.00\n15 Sept 2026 | −14,116.00 | 67,46,095.00\n"
         "15 Aug 2026 | +16,621.00 | 70,86,402.00\n")
CAGR = "From 31 Dec 2012 to 31 Dec 2025, IT returned 15.19% a year. Measured to 31 Aug 2026, IT's figure drops to 12.79%."


def _policy(body, texts):
    from utils.number_policy import apply_number_policy, number_set
    return apply_number_policy(body, passage_texts=texts, allowed_numbers=number_set(texts.values()), section_id="t")


def test_calculated_numbers_come_from_code_not_from_guesses():
    # Stage F: derived numbers are computed in code from facts the writer names ([[calc: ...]]);
    # an unlabelled derived number is not guessed (it is removed), and a unit conversion is
    # rewritten back to the passage's own value.
    texts = {"S14.2": FLOWS, "S20.8": CAGR}
    r = _policy("Foreign investors pulled a cumulative ₹[[calc: 47,955 + 21,745 + 14,116]] crore over three "
                "fortnights [S14.2]. Custody assets fell to ₹65.25 trillion [S14.2]. "
                "IT's CAGR fell by 2.4 percentage points [S20.8].", texts)
    assert "₹83,816 (calculated: 47,955 + 21,745 + 14,116) crore" in r.body
    assert "₹65,25,808 crore" in r.body and "trillion" not in r.body
    assert "2.4 percentage points" not in r.body
    assert len(r.calculated) == 1 and len(r.rewritten) == 1 and len(r.removed) == 1


def test_unsupported_sentence_is_removed_and_recorded():
    r = _policy("Banks grew 77.7% in the quarter [S14.2]. Net selling was −47,955.00 crore [S14.2].", {"S14.2": FLOWS})
    assert "77.7" not in r.body and "47,955.00" in r.body
    assert r.removed and r.removed[0]["number"] == "77.7"


def test_durations_counts_dates_and_ordinals_are_not_flagged():
    from utils.number_policy import statistic_tokens
    sentence = ("Between September 15 and 15 October, 13 out of 15 sectors fell over 13 weeks, holdings hit 13-year lows "
                "and the VIX reached its 84th percentile, while IT absorbed 10,915 of 13,112 crore [S1.1].")
    assert statistic_tokens(sentence) == ["10,915", "13,112"]


def test_uncited_sentence_gets_the_citation_of_the_passage_holding_its_numbers():
    from utils.section_writer import repair_citations
    body = "FPIs sold −₹1,559 crore of FMCG in the fortnight. Nothing to cite here."
    fixed, repaired = repair_citations(body, {"S14.1": {"1559", "883"}, "S6.2": {"12.5"}})
    assert fixed.startswith("FPIs sold −₹1,559 crore of FMCG in the fortnight [S14.1].") and repaired == 1


def test_writer_may_cite_passages_from_the_running_summary():
    from utils.report_outline import Section
    from utils.section_writer import write_section
    from utils.llm_gateway import GatewayStats, LLMResult

    passages = [Passage(f"S1.{i}", "S1", "Domestic flows offset foreign selling across the quarter. " * 5, "narrative", reliability=0.9)
                for i in range(1, 4)]
    earlier = Passage("S19.5", "S19", "Nifty FMCG fell 8.8% in Q3 2026 while Nifty Financial Services fell 7.2%.", "narrative", reliability=0.9)

    class Store:
        def __init__(self):
            self.passages = passages + [earlier]

        def get(self, pid):
            return next((p for p in self.passages if p.passage_id == pid), None)

    class Gw:
        stats = GatewayStats()

        async def generate(self, **kwargs):
            return LLMResult(ok=True, text="### Flows\n" + "Domestic flows offset foreign selling [S1.1]. " * 12
                             + "As noted earlier, FMCG fell 8.8% [S19.5].\n<<<SUMMARY>>>\nok [S1.1].", model="fake")

    section = Section("sec-4", "Flows", goal="flows", queries=["flows"], passage_ids=["S1.1", "S1.2", "S1.3"], word_budget=150)
    draft = asyncio.run(write_section(Gw(), Store(), section, [], question="q", thesis="t",
                                      already_covered=["Laggards: FMCG fell 8.8% [S19.5]."], other_sections=[]))
    assert "FMCG fell 8.8% [S19.5]" in draft.body and draft.unsupported_numbers == [] and draft.removed_sentences == []


# ── editorial pass ────────────────────────────────────────────────────────────
def test_editorial_pass_orders_merges_dedupes_and_flags():
    from utils.editorial_pass import EditedSection, editorial_pass
    repeated = "Nifty FMCG fell 8.8% in Q3 2026 as investors rotated out of consumer staples into technology names [S19]."
    sections = [
        EditedSection("sec-1", "Outlook and Risks", "### Watch list\nRisks remain [S1].", role="cross_cutting"),
        EditedSection("sec-2", "Bank: Deep-Dive", "### Bank: Performance\n" + repeated + "\n\nReport", role="deep_dive", entity="Bank"),
        EditedSection("sec-3", "Market Regime and Leadership", "### Performance\n" + repeated + " Nifty IT gained 17.7% in Q3 2026 [S2].",
                      role="cross_cutting"),
        EditedSection("sec-4", "Market Regime and Sector Leadership", "### Performance\nNifty IT gained 15.0% in Q3 2026 [S3].",
                      role="cross_cutting"),
    ]
    out, report = asyncio.run(editorial_pass(sections))
    assert [s.section_id for s in out] == ["sec-3", "sec-2", "sec-1"]  # context, deep-dive, outlook; sec-4 merged
    assert report.merged_sections and "Market Regime and Sector Leadership" in out[0].body
    assert out[1].body.count("8.8%") == 0 and report.repeated_sentences_removed  # repeat in the later section removed
    assert report.stray_labels_removed == 1 and "\nReport" not in out[1].body
    assert report.contradictions and report.contradictions[0]["values"] == ["17.7%", "15.0%"]
    # Repeated H3 titles in different cross-cutting sections are made specific.
    twins = [EditedSection("x", "Flows", "### Valuation signals\nA [S1]."),
             EditedSection("y", "Earnings Trajectory", "### Valuation signals\nB [S2].")]
    out2, report2 = asyncio.run(editorial_pass(twins))
    assert "### Valuation signals (Earnings Trajectory)" in out2[1].body and report2.renamed_headings


def test_transition_sentences_cannot_add_facts():
    from utils.editorial_pass import EditedSection, editorial_pass
    from utils.llm_gateway import GatewayStats, LLMResult

    class Gw:
        stats = GatewayStats()

        async def generate(self, **kwargs):
            return LLMResult(ok=True, text='{"b": "Flows explain why leadership changed.", "c": "IT rose 17.7% [S2]."}', model="fake")

    sections = [EditedSection("a", "Regime", "Text A."), EditedSection("b", "Flows", "Text B."), EditedSection("c", "Valuation", "Text C.")]
    out, report = asyncio.run(editorial_pass(sections, gateway=Gw()))
    assert out[1].body.startswith("Flows explain why leadership changed.") and out[2].body == "Text C."
    assert report.transitions_added == 1 and report.transitions_rejected == 1


# ── eval checks ───────────────────────────────────────────────────────────────
def test_eval_flags_title_mismatch_and_numbering():
    from scripts.eval_report import chart_title_mismatches, figure_numbering_issues, repeated_chart_titles
    chart_it = {"title": "Figure 1: Information Technology: Green Shoots", "series": [{"name": "Revenue", "data": [
        {"label": "Nestle India", "value": 6748}, {"label": "Trent", "value": 5028}]}]}
    chart_b = {"title": "Figure 2: Scores", "series": [{"name": "x", "data": [{"label": "Media", "value": 8.79}]}]}
    report = ("Figure 1 shows revenue, led by Nestle India at 6,748 [S1].\n\n[CHART_1]\n\n"
              "| Figure 1: Returns | % |\n\nFigure 2 shows Media at 8.79% [S2].\n\n[CHART_2]")
    assert chart_title_mismatches(report, [chart_it, chart_b]) and len(chart_title_mismatches(report, [chart_it, chart_b])) == 1
    assert any("caption" in i for i in figure_numbering_issues(report, [chart_it, chart_b]))
    assert repeated_chart_titles([chart_b, dict(chart_b, title="Figure 3: Scores")]) == ["scores"]


def test_table_rows_with_unsupported_values_are_removed():
    body = "| Sector | Net (cr) |\n| --- | --- |\n| FMCG | −47,955.00 |\n| Banks | 475 |"
    r = _policy(body, {"S14.2": FLOWS})
    assert "| FMCG | −47,955.00 |" in r.body and "475" not in r.body
    assert r.removed[0]["sentence"].startswith("table row:")


def test_unit_conversion_needs_the_matching_unit_word():
    from utils.number_policy import find_conversion, number_pool
    pool = number_pool({"S1.1": "FPIs bought 2,645 crore of capital goods."})
    assert find_conversion("26,450", pool, unit="million") == "converted from 2,645 crore"
    assert find_conversion("26,450", pool, unit="") == "" and find_conversion("26,450", pool, unit="crore") == ""


def test_prompt_spec_does_not_leak_into_headings():
    from utils.section_writer import SectionDraft, postprocess
    raw = ("### Financial Services: Performance — goal: Review quarterly returns. (~475 words)\n"
           + "Banks led the quarter [S1.1]. " * 10 + "\n<<<SUMMARY>>>\nok")
    draft = postprocess(SectionDraft("s", "t"), raw, allowed_passages={"S1.1"}, allowed_numbers=set(), figures=[], min_words=5)
    assert draft.body.startswith("### Financial Services: Performance\n") and "475" not in draft.body
