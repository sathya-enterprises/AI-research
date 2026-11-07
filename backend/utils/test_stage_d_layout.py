"""Stage D: one source list, QTD window, page policy, evidence-built layout blocks, heading hygiene."""
import re

from utils.evidence_store import Passage


class _Store:
    def __init__(self, passages):
        self.passages = passages

    def get(self, pid):
        return next((p for p in self.passages if p.passage_id == pid), None)


# ── sources listed once ──────────────────────────────────────────────────────
def test_junk_source_titles_fall_back_to_page_title_file_name_or_publisher():
    from utils.source_list import clean_title, metadata_parts, short_reason
    junk = {"title": "AIGENERATEDCONTENT", "publisher": "nsearchives.nseindia.com",
            "url": "https://nsearchives.nseindia.com/web/mediaattachment/2026-08/Market_Pulse_July_2026.pdf"}
    assert clean_title(junk) == "Market Pulse July 2026"
    assert clean_title({"title": "am.gs.com", "url": "https://am.gs.com/public-assets/documents/544fe1b3-ca24-11f0-82ec"}) \
        == "am.gs.com document"
    assert clean_title({"title": "INR295.3b", "publisher_page_title": "Fund Folio: Indian MF Tracker"}) == "Fund Folio: Indian MF Tracker"
    row = {"publisher": "nsearchives.nseindia.com", "publication_date": "2026-10-04", "authority_tier": "primary_official",
           "reliability_score": 0.88, "reliability_note": "primary official (regulator / exchange / official statistics); dated"}
    assert short_reason(row) == "regulator / exchange / official statistics"
    assert metadata_parts(row) == ["nseindia.com", "Published: 2026-10-04", "Tier: primary official", "Reliability: 0.88",
                                   "regulator / exchange / official statistics"]


def test_renderer_lists_cited_sources_once_numbered_by_their_ids():
    from routes.html_report import _render_sources_appendix
    registry = [{"id": f"S{i}", "title": f"Source {i}", "url": f"https://example.org/{i}", "reliability_score": 0.8}
                for i in range(1, 6)]
    html = _render_sources_appendix(None, "end_of_report", False, registry, cited_source_ids={"S2", "S5"},
                                    heading_override="Appendix B — Data Sources")
    assert html.count('class="gg-source-line"') == 2 and 'value="5"' in html and 'id="source-S5"' in html
    assert "Appendix B — Data Sources" in html and "3 further sources were screened but not cited" in html
    assert "Reliability: 0.80" in html


# ── data window ──────────────────────────────────────────────────────────────
def test_current_quarter_data_is_kept_and_charted_separately_as_qtd():
    from utils.section_charts import build_chart_candidates
    table =("TABLE 1 — Net FPI flows by sector\nSector | Q2 FY27 | Q1 FY27 | Q3 FY27\n"
             "Nifty Bank | -883 | 412 | -120\nNifty IT | 10915 | -2210 | 300\nNifty FMCG | -1559 | 900 | 75\n")
    charts, _ = build_chart_candidates(_Store([Passage("S14.1", "S14", table, "table", reliability=0.9)]), today="2026-11-08")
    titles = [c.spec["title"] for c in charts]
    assert len(charts) == 2 and sum("QTD, partial" in t for t in titles) == 1
    qtd = next(c for c in charts if "QTD" in c.spec["title"])
    assert {b for _, b, _ in qtd.points} == {"Nifty Bank", "Nifty IT", "Nifty FMCG"}


# ── page policy ──────────────────────────────────────────────────────────────
def test_page_targets_come_from_evidence_with_a_floor_and_never_pad():
    from utils.page_policy import evidence_page_target, shortfall_note
    plan = evidence_page_target("in_depth", 380 * 108)
    assert plan["target"] == 108 and not plan["belowFloor"] and plan["cap"] == 140
    assert evidence_page_target("in_depth", 380 * 400)["target"] == 140
    thin = evidence_page_target("detailed", 380 * 12)
    assert thin["belowFloor"] and thin["target"] == 12  # below the floor: not padded up to it
    assert "not padded" in shortfall_note("detailed", 380 * 12) and shortfall_note("detailed", 380 * 26) == ""


# ── layout blocks ────────────────────────────────────────────────────────────
SECTORS = ("TABLE 2 — Sector returns\nSector | Q2 FY27 | Q1 FY27\nNifty Bank | -4.2% | 3.1%\nNifty IT | 7.4% | -2.0%\n"
           "Nifty FMCG | -8.8% | 1.2%\nNifty Metal | 2.2% | 0.4%\n")
FLOWS = ("TABLE 3 — FPI net investment\nSector | Net investment (Rs cr)\nBanks | -883\nInformation Technology | 10915\n"
         "FMCG | -1559\nMetals & Mining | -904\n")
PE = "TABLE 4 — Valuation\nIndex | P/E\nNifty Bank | 18.4\nNifty IT | 27.1\nNifty FMCG | 41.2\nNifty Metal | 14.8\n"


def _tables():
    from utils.section_layout import tables_in
    return tables_in(_Store([Passage("S2.1", "S2", SECTORS, "calculated", reliability=1.0),
                             Passage("S14.1", "S14", FLOWS, "table", reliability=0.9),
                             Passage("S51.1", "S51", PE, "table", reliability=0.8)]))


def test_stat_strip_uses_only_verified_cells_with_their_sources():
    from utils.section_layout import stat_strip
    line = stat_strip(_tables(), "Bank", set())
    assert line.startswith("> [!STATS] ")
    assert "-4.2% [S2]" in line and "-883 [S14]" in line and "18.4 [S51]" in line
    assert stat_strip(_tables(), "Telecom", set()) == ""  # no evidence rows: no strip, nothing invented


def test_scorecard_and_comparison_are_built_from_evidence_rows():
    from utils.section_layout import entity_columns, entity_table
    columns = entity_columns(_tables(), ["Bank", "IT", "FMCG", "Metal"])
    table = entity_table(columns, ["Bank", "IT", "FMCG", "Metal"], caption="Sector scorecard")
    assert table.startswith("**Table: Sector scorecard**") and "| Bank | -4.2% | -883 | 18.4 |" in table
    assert table.rstrip().endswith("Source: [S2], [S14], [S51]")


def test_small_cited_table_is_placed_after_the_paragraph_that_cites_it_once():
    from utils.section_layout import place_inline_tables
    by_id = {t.passage_id: t for t in _tables()}
    placed: set[str] = set()
    body = "Valuations diverged [S51.1].\n\nBanks lagged [S51.1]."
    out = place_inline_tables(body, by_id, placed)
    assert out.count("**Table:") == 1 and out.index("**Table:") > out.index("Valuations diverged")
    assert place_inline_tables(body, by_id, placed).count("**Table:") == 0  # each table appears once


def test_what_to_watch_moves_sentences_and_keeps_paragraphs():
    from utils.section_layout import move_watch_items
    body = ("### Bank: Performance\nBanks fell 4.2% [S2].\n\n### Bank: Risks and what to watch\n"
            "Credit costs could rise if slippages persist [S95]. Deposits grew 11% [S147].\n\n"
            "| A | B |\n| --- | --- |\n| x | 1 |\n\nA rate cut may lift margins [S7].")
    new, watch = move_watch_items(body)
    assert watch.startswith("> [!WATCH] ") and "could rise" in watch and "may lift" in watch
    assert "could rise" not in new and "Deposits grew 11% [S147]." in new and "| x | 1 |" in new


def test_heading_runs_are_removed_without_losing_text():
    from utils.section_layout import fix_heading_runs
    md = "## Flows\n\n### Flows overview\n\nText A [S1].\n\n### Empty\n\n### Next\n\nText B [S2]."
    out, removed = fix_heading_runs(md)
    assert "### Flows overview" not in out and "### Empty" not in out and "### Next" in out and removed == 2
    assert "Text A [S1]." in out and "Text B [S2]." in out


def test_admonitions_render_with_existing_blocks_and_captions_stay_with_tables():
    from routes.html_report import _markdown_to_html
    md = ("> [!STATS] Q2 FY27 return: -4.2% [S2] | P/E: 18.4 [S51] | FPI flow: -883 [S14]\n\n"
          "> [!TAKEAWAYS] Banks lagged [S2]. Valuations stayed low [S51].\n\n"
          "### Bank: Valuation\n\nBanks trade at 18.4x [S51].\n\n"
          "**Table: Valuation**\n| Index | P/E |\n| --- | --- |\n| Nifty Bank | 18.4 |\n\nSource: [S51]")
    html = _markdown_to_html(md, [], [], None, [{"id": "S2"}, {"id": "S14"}, {"id": "S51"}], False)
    assert html.count('class="gg-metric"') == 3 and 'gg-callout' in html and "Key takeaways" in html
    assert '<div class="gg-keep-with-next"><h4' in html
    assert '<div class="gg-chart-title">Valuation</div>' in html and "<strong>Table:" not in html


# ── subsection variety ───────────────────────────────────────────────────────
def test_deep_dive_subsections_follow_each_sectors_evidence():
    from utils.report_outline import Section
    from utils.subsection_planner import diversify_deep_dives, distinct_sets
    text = {
        "Bank": "Bank credit growth stayed at 19% while NPAs and asset quality improved; deposits lagged. Bank returns fell.",
        "Metal": "Metal stocks tracked LME prices and coal input costs; commodity prices and China demand drove metal returns.",
        "Defence": "Defence order book and capex execution stayed strong; government budget and policy support defence orders.",
    }
    passages = [Passage(f"S{i}.{j}", f"S{i}", body, "narrative", reliability=0.8)
                for i, body in enumerate(text.values(), 1) for j in range(4)]
    template = [("Performance and momentum", ""), ("Flows and ownership", ""), ("Valuation and earnings", ""),
                ("Drivers, risks and outlook", "")]
    sections = [Section(f"sec-{i}", f"{e}: Deep-Dive", role="deep_dive", entity=e,
                        subsections=[Section(f"sec-{i}.{j}", t) for j, (t, _) in enumerate(template)])
                for i, e in enumerate(text, 1)]
    assert distinct_sets(sections) == 1
    decisions: list[str] = []
    assert diversify_deep_dives(_Store(passages), sections, decisions) == 3
    titles = {s.entity: [x.title for x in s.subsections] for s in sections}
    assert "Credit growth and asset quality" in titles["Bank"]
    assert "Input costs and commodity prices" in titles["Metal"]
    assert "Orders, capex and execution" in titles["Defence"]
    assert distinct_sets(sections) == 3 and decisions
