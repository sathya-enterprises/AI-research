"""Stage I: client-readiness checks (leaks, periods, self-description, tables, regime, charts)."""
from datetime import date

from utils.boilerplate import PLACEHOLDER, resolve_source_count
from utils.leak_gate import remove_leaks
from utils.period_hygiene import fy_label, fy_labels_in, in_window, qtd_tag_applies, table_period


# ── FIX 1: leaked instructions ───────────────────────────────────────────────────────────────────
def test_leaked_window_instructions_are_removed_but_prose_is_kept():
    text = ("## Methodology and Evidence Base\n\n**Evidence base.** 40 sources were retained.\n"
            "This report answers: *How can you explain sector rotation.*. QUARTER WINDOW (pre-computed — use these exact "
            "labels and dates, do NOT recompute fiscal quarters yourself):\n - Q3 FY25: Oct 01, 2024 - Dec 31, 2024\n"
            "CURRENT QUARTER TO DATE: Q3 FY27 (Oct 01, 2026 - Nov 08, 2026) is not one of the completed quarters; report it separately.\n\n"
            "## Analysis\n\nOver the eight-quarter window, Nifty Metal led. The evidence passages show steady flows.")
    out, actions = remove_leaks(text)
    assert "QUARTER WINDOW" not in out and "Q3 FY25: Oct 01" not in out and "CURRENT QUARTER TO DATE" not in out
    assert "Over the eight-quarter window, Nifty Metal led." in out and "evidence passages show" in out
    assert "**Evidence base.** 40 sources were retained." in out and len(actions) >= 3


def test_methodology_carries_no_question_or_window_note():
    import inspect
    from utils import report_pipeline_v2
    source = inspect.getsource(report_pipeline_v2.methodology_markdown)
    assert "This report answers" not in source and "{window_note}" not in source


# ── FIX 2: periods ───────────────────────────────────────────────────────────────────────────────
def test_table_periods_and_window():
    label, end = table_period("Sectoral Indices performance in April, 2024", ["Index", "1-month Returns (%)"])
    assert end == date(2024, 4, 30) and not in_window(end, date(2024, 10, 1))
    label, end = table_period("Latest fortnight: 16 Oct 2026 to 31 Oct 2026", ["Sector", "Net investment"])
    assert in_window(end, date(2024, 10, 1))
    label, end = table_period("Market Reports", ["Sector", "Score"], published="")
    assert end is None and not in_window(end, date(2024, 10, 1))  # undated: kept out of layout tables
    label, end = table_period("Heatmap", ["Sector", "1M"], published="2026-10-11")
    assert label == "as of 11 Oct 2026"


def test_fiscal_labels_and_qtd_tag():
    assert fy_label("Q4 2024") == "Q3 FY25" and fy_label("Q1 2026") == "Q4 FY26"
    assert fy_labels_in("Compounded return Q4 2024 to Q3 2026 (%)") == "Compounded return Q3 FY25 to Q2 FY27 (%)"
    assert not qtd_tag_applies("1D, as of 11 Oct 2026") and not qtd_tag_applies("QTD return to Nov 2026 (%)")
    assert qtd_tag_applies("NET INVESTMENT (CR), Oct 2026")


# ── FIX 3: a table header row is never removed ───────────────────────────────────────────────────
def test_table_header_rows_are_not_claims():
    from utils.evidence_check import _is_table_header, fix_flagged_claims
    report = ("**Table: Returns**\n| Sector | Compounded return Q3 FY25 to Q2 FY27 (%) [S2] |\n| --- | --- |\n"
              "| Metal | +26.36% |\n| FMCG | -26.33% |")
    assert _is_table_header(report, report.index("| Sector"))
    group = {"claim": "| Sector | Compounded return Q3 FY25 to Q2 FY27 (%) [S2] |", "sourceIds": ["S2"],
             "unmatchedNumericClaims": ["scalar:2"]}
    out, actions, _ = fix_flagged_claims(report, lambda text: [group] if "| Sector |" in text else [], max_rounds=2)
    assert "| Sector |" in out


# ── FIX 5: self-description ──────────────────────────────────────────────────────────────────────
def test_cited_source_count_comes_from_the_final_data_sources_set():
    report = f"Intro [S1]. More [S2, S3].\n\n## Methodology\n\n{PLACEHOLDER} sources are cited."
    manifest = [{"id": f"S{i}"} for i in range(1, 6)]
    charts = [{"title": "x", "sourceIds": ["S4"]}]
    out = resolve_source_count(report, charts, [], manifest)
    assert "4 sources are cited." in out


def test_how_to_read_does_not_promise_inline_markers_when_hidden(monkeypatch):
    from utils.section_layout import how_to_read
    monkeypatch.setenv("REPORT_INLINE_CITATIONS", "0")
    note = how_to_read(deep_dives=0, compact=0, has_qtd=True, has_calculated=False)
    assert "[S#]" not in note and "sector deep-dives" not in note and "compact sections" not in note


# ── FIX 7: data sanity ───────────────────────────────────────────────────────────────────────────
def test_financial_services_qtd_diverging_from_bank_is_blocked():
    from utils.data_sanity import check_dataset_rows
    rows = [
        {"label": "NIFTY FINANCIAL SERVICES", "quarters": ["Q2 FY27"], "returns": [3.1],
         "qtd": {"return": 17.97, "close": 29151.95}},
        {"label": "NIFTY BANK", "quarters": ["Q2 FY27"], "returns": [2.4], "qtd": {"return": 7.61, "close": 58000.0}},
        {"label": "NIFTY METAL", "quarters": ["Q2 FY27"], "returns": [5.0], "qtd": {"return": 4.0, "close": 9000.0}},
    ]
    findings = check_dataset_rows(rows)
    assert rows[0]["qtd"] is None and rows[1]["qtd"] and rows[2]["qtd"]
    assert any("NIFTY BANK" in f["reason"] for f in findings)


def test_qtd_close_far_from_live_level_and_out_of_range_values_are_blocked():
    from utils.data_sanity import check_dataset_rows
    rows = [{"label": "NIFTY IT", "quarters": ["Q1 FY27", "Q2 FY27"], "returns": [80.0, 2.0],
             "qtd": {"return": 3.0, "close": 40000.0}}]
    findings = check_dataset_rows(rows, live_levels={"NIFTY IT": 36000.0})
    assert rows[0]["qtd"] is None and rows[0]["returns"] == [None, 2.0] and len(findings) == 2


# ── FIX 3: text-art diagrams, flattened tables, rendered gate ────────────────────────────────────
def test_text_art_diagrams_become_sentences_and_bars_are_removed():
    from utils.flow_text import convert_text_art
    text = ("Intro [S1].\n- Global Rate Pressures\n- │\n"
            "- ├─> Elevated Sovereign Yields ──> Higher Term Premiums [S10, S32]\n"
            "- ├─> Factors: Value ├─> Factors: Quality\n"
            "IT ████████ 6.2%\n"
            "The mechanism—higher risk premium → higher oil prices → inflation—is clear [S2].\n"
            "| A | B |\n| --- | --- |\n| x > y | 1 |\n> [!NOTE] keep > this")
    out, actions = convert_text_art(text)
    assert "- Elevated Sovereign Yields, leading to Higher Term Premiums [S10, S32]." in out
    assert "│" not in out and "Factors: Value" not in out and "████" not in out
    assert "higher risk premium, leading to higher oil prices, leading to inflation" in out
    assert "| x > y | 1 |" in out and "> [!NOTE] keep > this" in out and actions


def test_headerless_pipe_tables_are_dropped_not_flattened():
    from utils.markdown_normalizer import _clean_ascii_diagram_frames
    from utils.flow_text import convert_text_art
    t = ("Intro.\n\n**Table: X: compared**\n| --- | --- | --- |\n| Metal | -7.34% | +26.36% |\n| Bank | -4.50% | +6.24% |\n"
         "Source: [S2]\n\nNext.\n\n| A | B |\n| --- | --- |\n| 1 | 2 |")
    out = _clean_ascii_diagram_frames(t)
    assert "Metal" not in out and "**Table: X" not in out and "| 1 | 2 |" in out and " — " not in out
    flat = "**Table: Y**\n--- — --- — ---\nMetal — -7.34% — +26.36%\nBank — -4.50% — +6.24%\nSource: [S2]\nNext."
    out, _ = convert_text_art(flat)
    assert out.strip() == "Next."


def test_rendered_gate_flags_chains_and_flattened_tables_only():
    from utils.flow_text import rendered_flow_findings
    text = ("higher crude → larger dollar demand → weaker rupee\nMetal — -7.34% — +26.36% — +7.96%\n"
            "Eligibility: trade with > 90% of days\n[S7] India's imports - IndBiz | Economic Division | IndBiz |")
    issues = [f["issue"] for f in rendered_flow_findings(text)]
    assert len(issues) == 2 and "arrow" in issues[0] and "table" in issues[1]


# ── FIX 4: regime from data ──────────────────────────────────────────────────────────────────────
def _dataset(rows: dict, compounded: dict | None = None):
    from utils.dataset_charts import QuarterlyDataset, Row
    quarters = ["Q3 FY25", "Q4 FY25", "Q1 FY26", "Q2 FY26"]
    return QuarterlyDataset("S2", quarters, {k: Row(k, "", "p1", [], v) for k, v in rows.items()},
                            {k: (v, "", "", "p1") for k, v in (compounded or {}).items()}, "Nov 2026", {})


def test_regime_is_computed_and_contradicted_rotation_is_rewritten():
    from utils.regime import compute_regime, contradicted_rotation, heal_rotation_claims
    data = _dataset({"NIFTY METAL": [8, 6, -4, -5], "NIFTY AUTO": [5, 4, -3, -2],
                     "NIFTY FMCG": [-1, 0, 3, 4], "NIFTY PHARMA": [0, 1, 4, 5], "NIFTY 50": [2, 2, 0, 0]})
    regime = compute_regime(data)
    assert regime.first_half == "cyclical" and regime.second_half == "defensive"
    assert regime.rotation == ("cyclical", "defensive")
    assert not contradicted_rotation("Leadership rotated from cyclicals to defensives over the window.", regime)
    bad = "Markets saw a rotation from defensives to cyclicals as growth recovered [S3]."
    assert contradicted_rotation(bad, regime)
    text, actions = heal_rotation_claims(f"Intro [S1]. {bad} Next point.", regime)
    assert "from defensives to cyclicals" not in text and "leadership moved from cyclical to defensive" in text
    assert actions and actions[0]["action"].startswith("rewritten")


def test_regime_groups_are_configurable(monkeypatch):
    from utils.regime import compute_regime
    monkeypatch.setenv("REGIME_GROUPS_JSON", '{"cyclical": ["NIFTY METAL"], "defensive": ["NIFTY FMCG", "NIFTY AUTO"]}')
    regime = compute_regime(_dataset({"NIFTY METAL": [8, 6, -4, -5], "NIFTY AUTO": [5, 4, -3, -2],
                                      "NIFTY FMCG": [-1, 0, 3, 4]}))
    assert regime.members["defensive"] == ["NIFTY FMCG", "NIFTY AUTO"]


def test_sector_mislabels_are_corrected():
    from utils.regime import fix_mislabels
    out, actions = fix_mislabels("The high-beta FMCG names fell. IT, a defensive sector, held up. "
                                 "Defensive sectors like Nifty Pharma and Nifty FMCG were steady. "
                                 "High-beta sectors like Nifty Realty and Nifty FMCG fell. It remained defensive.")
    assert "defensive FMCG names" in out and "IT, an export-driven sector" in out
    assert "Defensive sectors like Nifty Pharma" in out and "Sectors like Nifty Realty and Nifty FMCG" in out
    assert "It remained defensive." in out and len(actions) == 3


# ── FIX 6: repetition ────────────────────────────────────────────────────────────────────────────
def test_data_takeaway_is_computed_and_skips_equal_values_and_totals():
    from utils.repetition import data_takeaway
    bars = {"type": "bar", "unit": "%", "series": [{"name": "r", "data": [
        {"label": "Metal", "value": 26.36, "evidenceValue": "+26.36%"}, {"label": "FMCG", "value": -26.33, "evidenceValue": "-26.33%"},
        {"label": "IT", "value": 1.0}]}]}
    assert data_takeaway(bars) == "Highest: Metal (+26.36%); lowest: FMCG (-26.33%); spread 52.69 pp."
    flat = {"type": "bar", "series": [{"name": "Months", "data": [{"label": x, "value": 41} for x in "abcd"]}]}
    assert data_takeaway(flat) == ""
    total = {"type": "bar", "series": [{"name": "g", "data": [{"label": "Total", "value": 21664}, {"label": "Bank", "value": 258},
                                                               {"label": "MFs", "value": 16756}, {"label": "Insurance", "value": 4000}]}]}
    assert "Total" not in data_takeaway(total)


def test_duplicate_tables_are_removed_once():
    from utils.repetition import dedupe_tables
    table = "| Sector | Q1 | Q2 |\n| --- | --- | --- |\n| Metal | +1.10% | +2.20% |\n| Bank | +3.30% | -4.40% |"
    report = f"## A\n\n**Table: One**\n{table}\nSource: [S2]\n\n## B\n\n**Table: Two**\n{table}\nSource: [S2]\n\nText."
    out, removed = dedupe_tables(report, [])
    assert out.count("| Metal |") == 1 and "**Table: Two**" not in out and len(removed) == 1 and "Text." in out


def test_headline_stat_cap_keeps_sentences_and_points_back():
    from utils.repetition import cap_headline_stats, repetition_metrics
    report = ("## Executive Summary\n\nCredit growth reached 17.7% [S1].\n\n## Macro\n\nSystemic credit growth reached 17.7% in June [S1].\n\n"
              "## Bank: Credit\n\nBank credit growth stayed at 17.7% this year [S1].\n\n## Outlook\n\n"
              "Looking ahead, credit growth of 17.7% supports lenders such as PSU banks [S1]. Deposits lag (17.7% credit growth) [S1].")
    out, actions = cap_headline_stats(report)
    assert "PSU banks" in out and "the figure given in Section 2" in out  # sentence kept, number points back
    assert "Bank credit growth stayed at 17.7%" in out                     # the deep-dive keeps its own number
    assert repetition_metrics(out)["stats_over_cap"] == 0 and actions


def test_regime_dispersion_makes_the_thesis_sector_specific():
    from utils.regime import compute_regime, thesis_needs_rewrite
    data = _dataset({"NIFTY METAL": [10, 8, 4, 2], "NIFTY REALTY": [-6, -5, -4, -3],
                     "NIFTY PHARMA": [3, 4, 5, 6], "NIFTY FMCG": [-5, -6, -7, -8], "NIFTY 50": [1, 1, 1, 1]})
    regime = compute_regime(data)
    d = regime.dispersion
    assert d["sectorSpecific"] and d["largestRange"] > d["gap"]
    thesis = regime.thesis_sentence()
    assert "sector-specific factors rather than a group-wide" in thesis and "Nifty Metal" in thesis and "Nifty FMCG" in thesis
    assert "compounded over the window: cyclical sectors" in thesis
    assert thesis_needs_rewrite("Leadership rotated from cyclicals to defensives.", regime)
    assert not thesis_needs_rewrite(thesis, regime)


# ── FIX 8: chart quality gate, lint, tables ──────────────────────────────────────────────────────
def _bar(title, labels, values, x="Sector", y="Change %", raw=None):
    return {"type": "bar", "title": title, "xLabel": x, "yLabel": y, "sourceIds": ["S11"], "series": [{"name": y, "data": [
        {"label": l, "value": v, "evidenceValue": (raw or {}).get(l, f"{v:+.2f}%")} for l, v in zip(labels, values)]}]}


def test_chart_quality_gate_drops_meaningless_charts():
    from utils.chart_lint import run_chart_lint
    flat = _bar("Figure 1: Months: a, b and 2 others", list("abcd"), [41, 41, 41, 41], y="Months")
    total = _bar("Figure 2: Gross Purchases: Bank and 3 others", ["Total", "Bank", "MFs", "Insurance"], [21664, 258, 16756, 4000])
    good = _bar("Figure 3: Change %: Fin Services, Banks, Pharma and 9 others", ["Fin Services", "Banks", "Pharma", "IT"], [2.57, 2.27, 1.1, -0.3])
    dup = _bar("Figure 4: 1 Week (%): Nifty (Benchmark) and 3 others", ["Fin Services", "Banks", "Pharma", "IT"], [2.57, 2.27, 1.1, -0.3])
    example = _bar("Figure 5: 1-month return: Banks, IT, FMCG and 1 other", ["Banks", "IT", "FMCG", "Pharma"], [2, -1, 3, 4])
    kept, dropped, _ = run_chart_lint([flat, total, good, dup, example],
                                      passage_text=lambda s: "Example datasets show 1-month returns" if "Figure 5" in s["title"] else "")
    assert [d["index"] for d in dropped] == [1, 2, 4, 5] and len(kept) == 1


def test_chart_lint_fixes_titles_axes_types_and_sources():
    from utils.chart_lint import lint_all, run_chart_lint
    manifest = {"S11": {"publisher": "strota.in", "title": "Sector Flow Today", "publisher_page_modified_at": "2026-11-06"}}
    chart = _bar("Figure 1: Change %: Fin Services, Banks, Pharma and 9 others",
                 ["Fin Services", "Banks", "NIFTY 50", "Pharma", "IT"], [2.57, 2.27, 0.5, 1.1, -0.3])
    kept, _, log = run_chart_lint([chart], manifest=manifest, today="2026-11-08")
    c = kept[0]
    assert c["type"] == "horizontal_bar" and [p["value"] for p in c["series"][0]["data"]] == sorted([2.57, 2.27, 0.5, 1.1, -0.3], reverse=True)
    assert "and 9 others" not in c["title"] and "4 sectors and the Nifty 50 benchmark" in c["title"] and "(%)" in c["title"]
    assert c["yLabel"] == "Change (%)" and c["decimals"] == 2 and "updated 06 Nov 2026 [S11]" in c["source"]
    rows, failing = lint_all(kept)
    assert failing == 0 and rows[0]["lint"] == "PASS"
    series = _bar("Figure 2: FII net: 21 Oct 2026, 22 Oct 2026 and 3 others", ["21 Oct 2026", "22 Oct 2026", "23 Oct 2026"],
                  [1022.32, -3458.7, 887.65], x="Date", y="FII net",
                  raw={"21 Oct 2026": "1,022.32", "22 Oct 2026": "-3,458.70", "23 Oct 2026": "887.65"})
    kept, _, _ = run_chart_lint([series], manifest=manifest, today="2026-11-08")
    assert kept[0]["title"] == "Figure 2: FII net (₹ crore), 21-23 Oct 2026" and lint_all(kept)[1] == 0


def test_heatmap_gets_fiscal_labels_and_a_real_title():
    from utils.chart_lint import lint_all, run_chart_lint
    heat = {"type": "heatmap", "title": "Figure 1: Values (%): NIFTY AUTO, NIFTY FMCG, NIFTY METAL and 1 others, Q4 2024 to Q3 2025",
            "unit": "%", "sourceIds": ["S2"], "rows": ["NIFTY AUTO", "NIFTY FMCG", "NIFTY METAL", "NIFTY 50"],
            "columns": ["Q4 2024", "Q1 2025", "Q2 2025", "Q3 2025"], "values": [[1, 2, 3, 4], [5, 6, 7, 8], [9, 1, 2, 3], [4, 5, 6, 7]]}
    kept, _, _ = run_chart_lint([heat], manifest={"S2": {"publisher": "Calculated from daily market data"}}, today="2026-11-08")
    h = kept[0]
    assert h["columns"][0] == "Q3 FY25" and h["rows"][0] == "Nifty Auto"
    assert h["title"] == "Figure 1: Quarterly return (%): 3 NSE sector indices and the Nifty 50 benchmark, Q3 FY25 to Q2 FY26"
    assert lint_all(kept)[1] == 0


def test_tables_are_numbered_separately_and_need_a_source():
    from utils.chart_lint import number_tables
    report = ("As Table 2 shows, flows rose [S4].\n\n**Figure 2: Flows by month**\n| Month | Net |\n| --- | --- |\n| Oct | 5 |\n\n"
              "Text without a cite.\n\n| Company | Price |\n| --- | --- |\n| A | 1 |\n\n"
              "Values were mixed [S7].\n\n| Index | Value |\n| --- | --- |\n| X | 2 |\n\n| A | B |\n| --- | --- |\n| [[UNSUPPORTED_CALC]] | 1 |")
    out, actions, inventory = number_tables(report)
    assert "**Table 1: Flows by month**" in out and "Figure 2: Flows" not in out and "As Table 1 shows" in out
    assert "| Company |" not in out and "[[UNSUPPORTED" not in out
    assert "**Table 2: Value, by index**" in out and "Source: [S7]" in out and len(inventory) == 2


# ── FIX 2 / gates: periods and rendered text ─────────────────────────────────────────────────────
def test_period_gate_drops_out_of_window_tables_but_not_since_years():
    from utils.stage_i_gates import heal_text
    report = ("## A\n\n### Sectoral Indices performance in April, 2024 [S22]\n\n| Index | 1M |\n| --- | --- |\n| IT | 2% |\n\n"
              "### The falls inside each sector [S59]\n\n| Sector | Deepest fall since 2012 |\n| --- | --- |\n| IT | -30% |")
    out, actions = heal_text(report, date(2024, 10, 1))
    assert "April, 2024" not in out and "Deepest fall since 2012" in out and any(a.startswith("period") for a in actions)


def test_rendered_gate_flags_instructions_and_split_words():
    from utils.stage_i_gates import split_word_findings
    from utils.flow_text import rendered_flow_findings
    assert split_word_findings("RETURNS IN MON THS", "returns over twelve months") and not split_word_findings("in the", "in the months")
    assert rendered_flow_findings("Metal — -7.34% — +26.36% — +7.96%")


# ── FIX 9: smaller items ─────────────────────────────────────────────────────────────────────────
def test_small_items_are_fixed():
    from utils.stage_i_gates import fix_small_items, qualify_low_reliability
    text = ("A examination showed an recovery. First, x. Fourth, y. Fifth, z.\n> [!WATCH] [S2].\n"
            "> [!WATCH] Rates could rise if oil spikes [S3].\n> [!WATCH] The sector grew 5% last year [S4].\n"
            "an FII bought a one-off. Growth * rose. A coefficient ($\nho$) of 0.17. Historically\n, it fell.")
    out, actions = fix_small_items(text)
    assert "An examination showed a recovery" in out and "First, x. Second, y. Third, z." in out
    assert "[!WATCH] [S2]" not in out and "> [!WATCH] Rates could rise" in out and "\nThe sector grew 5% last year [S4]." in out
    assert "an FII bought a one-off" in out and "Growth rose" in out and "(ρ)" in out and "Historically, it fell" in out
    q, _ = qualify_low_reliability("Credit grew 17.7% [S9]. FIIs sold $53 billion [S49].", {"S9": 0.41, "S49": 0.8})
    assert "17.7% (reported by a lower-reliability source) [S9]." in q and "$53 billion [S49]." in q


def test_side_by_side_bullet_diagrams_are_dropped_but_lists_are_kept():
    from utils.flow_text import convert_text_art
    text = ("Intro.\n\n- [ Tight Policy Stance ] [ Monetary Easing ]\n- Repo rate raised • Policy rate cuts delivered\n\n"
            "- Impact: Liquidity squeeze drives banking Impact: Lower discount rates and\n- more fragments\n\n"
            "* **Large caps:** ICICI (Market Cap: ₹11 Lakh Cr; EPS: ₹49) trades at 13x [S12] [S12].\n* **PSU Banks:** cheap.")
    out, actions = convert_text_art(text)
    assert "Tight Policy" not in out and "Impact:" not in out and "**Large caps:**" in out and "**PSU Banks:**" in out


def test_calculated_sources_are_not_labelled_primary_official():
    from utils.source_list import metadata_parts
    parts = metadata_parts({"publisher": "Calculated from daily market data", "url": "", "authority_tier": "primary_official",
                            "reliability_score": 1.0, "reliability_note": "calculated / internal dataset with recorded provenance"})
    assert not any("Reliability" in p or "Tier" in p for p in parts) and any("Calculated in this report" in p for p in parts)


def test_stacked_tables_are_split_and_amount_spellings_are_one_statistic():
    from utils.chart_lint import number_tables
    from utils.repetition import cap_headline_stats, repetition_metrics
    stacked = "Text [S1].\n\n| A | B |\n| --- | --- |\n| x | 1 |\n| C | D |\n| --- | --- |\n| y | 2 |\nSource: [S1]"
    out, _, inventory = number_tables(stacked)
    assert len(inventory) == 2 and "**Table 2:" in out
    report = ("## Executive Summary\n\nX.\n\n## A\n\nTelecom saw +11,065 Cr of inflows [S1].\n\n## B\n\n"
              "Telecommunication inflows were ₹11,065 crore [S1].\n\n## C\n\nLater, Telecom took +₹11,065 crore in buying [S1].")
    capped, _ = cap_headline_stats(report)
    assert "took the figure given in Section 2 in buying" in capped and repetition_metrics(capped)["stats_over_cap"] == 0


# ── Stage J: domain-generic dataset extraction ───────────────────────────────────────────────────
def test_extracted_datasets_are_verified_against_the_passage():
    from utils.dataset_extractor import verify_dataset, to_chart
    from utils.evidence_store import Passage
    text = ("Figure 2: EV Penetration Rate - Global & India 3.08% 3.54% 4.91% 0.23% 0.38% 0.51% 2016 2017 2018\n"
            "Global EV Penetration India EV Penetration")
    years = ["2016", "2017", "2018"]
    good = {"metric": "EV penetration rate", "unit": "%", "x": "time", "series": [
        {"name": "India", "points": [{"label": y, "value": v} for y, v in zip(years, ["0.23", "0.38", "0.51"])]},
        {"name": "Global", "points": [{"label": y, "value": v} for y, v in zip(years, ["3.08", "3.54", "4.91"])]}]}
    ds, reason = verify_dataset(good, text, date(2026, 11, 9))
    assert ds and not reason
    bad = {**good, "series": [{"name": "India", "points": [{"label": y, "value": v} for y, v in zip(years, ["0.23", "0.96", "0.51"])]}]}
    assert "not in the passage" in verify_dataset(bad, text, date(2026, 11, 9))[1]
    future = {"metric": "EV sales", "unit": "Mn", "x": "time", "series": [{"name": "EV sales", "points": [
        {"label": y, "value": v} for y, v in [("2028", "0.23"), ("2029", "0.38"), ("2030", "0.51")]]}]}
    assert "forecast" in verify_dataset(future, text + " 2028 2029 2030", date(2026, 11, 9))[1]
    chart = to_chart(ds, Passage("S2.3", "S2", text, "narrative", title="EV report"), "EV report", 1)
    assert chart.interpretation.startswith("India went from 0.23% in 2016 to 0.51% in 2018")
    assert all(p["evidenceValue"] in text for s in chart.spec["series"] for p in s["data"])


def test_data_seeking_angles():
    from utils.data_angles import data_angles
    q = "How has India's electric vehicle market evolved over the past 3 years, and what are the key growth drivers?"
    angles = data_angles(q, ["India EV policy"], tier="in_depth", today=date(2026, 11, 10))
    assert len(angles) == 2 and angles[0].startswith("India electric vehicle market year-wise statistics 2023")
    assert data_angles(q, ["India EV sales statistics by year"], tier="detailed", today=date(2026, 11, 10)) == []
    since = data_angles("Impacts of semiconductor supply chain shifts since 2022?", ["x"], tier="detailed", today=date(2026, 11, 10))
    assert "2022 2023 2024 2025 2026" in since[0]


def test_question_windows_since_and_past_years():
    from utils.chart_evidence_audit import _periods_outside_question_window as outside
    from utils.source_quality import window_start
    q = "Impacts of semiconductor supply chain shifts since 2022?"
    assert window_start(q, date(2026, 11, 10)) == date(2022, 1, 1)
    assert outside(q, ["2021"], {}) and not outside(q, ["2022"], {}) and not outside(q, ["2025"], {})
    assert window_start("trends from 2022 to 2024", date(2026, 11, 10)) == date(2022, 1, 1)
    p3 = "How has the market evolved over the past 3 years?"
    assert outside(p3, ["2022"], {}) and not outside(p3, ["2023"], {}) and not outside(p3, ["2024"], {})


def test_quality_gate_drops_teaser_tables_and_unlabelled_forecasts():
    from utils.chart_lint import quality_reason
    def chart(quote, title="Sales"):
        return {"type": "line", "title": title, "series": [{"name": "x", "data": [
            {"label": str(y), "value": v, "evidenceQuote": quote.format(y=y, v=v)} for y, v in [(2023, 1), (2024, 2), (2025, 3)]]}]}
    assert "placeholders" in quality_reason(chart("{y} | $ {v} Mn | $#% | x"), "")
    assert "forecast" in quality_reason(chart("{y} | {v} | Forecast"), "")
    assert quality_reason(chart("{y} | {v} | Forecast", "Sales (forecast)"), "") == ""
    assert quality_reason(chart("In {y} sales were {v} units"), "") == ""
