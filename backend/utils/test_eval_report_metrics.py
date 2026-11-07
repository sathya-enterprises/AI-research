"""Unit tests for the offline report-evaluation detectors (scripts/eval_report.py)."""
import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location("eval_report", Path(__file__).resolve().parents[1] / "scripts" / "eval_report.py")
E = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(E)


def test_husk_blocks_detect_citation_only_lines_but_not_real_sentences():
    report = "> [S5], [S37]\n\n1. [S5], [S7].\n\nReal claim with a citation [S4].\n\n*[S5, S22]*"
    husks = E.husk_blocks(report)
    assert len(husks) == 3
    assert not any("Real claim" in h for h in husks)


def test_sentence_fragments_flag_guard_residue_but_not_years():
    report = "- despite FII exits [S25] |\n\nSharpe ratio of 0.\n\nThis held across the first three quarters of 2026."
    fragments = E.sentence_fragments(report)
    assert len(fragments) == 2
    assert not any("2026" in f for f in fragments)


def test_empty_headings_include_limitation_only_sections_and_ignore_parents_with_children():
    report = (
        "## Risk Matrix\n\n> **Quantitative evidence limitation** One or more numerical statements were omitted.\n\n"
        "## Flows\n\n### FII\n\nForeign investors sold heavily in financials during the quarter [S1].\n"
    )
    assert E.empty_headings(report) == ["Risk Matrix"]


def test_duplicate_headings_catch_reworded_repeats():
    headings = [(3, "Volatility-Driven Sector Timing (India VIX)"), (3, "Volatility-Driven Timing (India VIX)"),
                (2, "Institutional Flows"), (2, "Valuation and Momentum Indicators")]
    pairs = E.duplicate_headings(headings)
    assert pairs == [("Volatility-Driven Sector Timing (India VIX)", "Volatility-Driven Timing (India VIX)")]


def test_misplaced_sections_after_methodology():
    headings = [(2, "Executive Summary"), (2, "Methodology & Data Dictionary"), (2, "Defensive Pivots"), (2, "Conclusion")]
    assert E.misplaced_sections(headings) == ["Defensive Pivots"]


def test_chart_is_explained_only_when_prose_interprets_its_data():
    chart = {"title": "Sector returns", "series": [{"name": "Q3", "data": [{"label": "IT", "value": 1.4}, {"label": "Healthcare", "value": 5.3}]}]}
    unrelated = "It was a volatile year and performance dispersion was wide.\n\n[CHART_1]\n\nMore text."
    interpreted = "Healthcare led with a 5.3% return while IT lagged.\n\n[CHART_1]"
    referenced = "[CHART_1]\n\nFigure 1 shows the spread between sectors."
    assert E.charts_explained(unrelated, [chart]) == (0, 0)
    assert E.charts_explained(interpreted, [chart]) == (1, 0)
    assert E.charts_explained(referenced, [chart]) == (1, 1)


def test_numbers_removed_prefers_capture_stage_then_log():
    capture = {"stages": {"evidence_guard": {"data": {"redactedValues": 7}}}}
    assert E.numbers_removed(capture, "") == 7
    log = "WARNING report — Report evidence guard redacted unsupported financial/percentage value(s)=128 and removed KPI(s)=3"
    assert E.numbers_removed({}, log) == 128
    assert E.numbers_removed({}, "") is None
