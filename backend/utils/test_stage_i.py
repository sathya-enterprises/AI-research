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
