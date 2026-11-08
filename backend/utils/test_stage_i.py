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
