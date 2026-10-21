from __future__ import annotations

import time

from routes.report import (
    _add_verified_numeric_charts_from_tables,
    _align_title_horizon,
    _sanitize_unverified_financial_claims,
)
from utils.chart_evidence_audit import audit_chart_evidence_consistency


def _publisher_source(evidence: str) -> dict:
    return {
        "id": "S1",
        "url": "https://example.org/banking-credit-data",
        "pageFetchStatus": "ok",
        "evidenceExcerptOrigin": "publisher_page",
        "evidenceOrigin": "publisher_page",
        "publisherEvidenceExcerpt": evidence,
        "evidenceExcerpt": evidence,
    }


def test_prose_excerpt_alone_is_not_enough_to_generate_a_numeric_chart():
    # The table has source labels, but the source only has prose. Without a
    # successfully fetched structured publisher-table row, these values cannot
    # be assigned to verified period/category cells and must not become a plot.
    evidence = (
        "Agriculture credit growth was 18.1% year over year in July 2024. "
        "Personal loan credit growth was 17.8%. MSME credit growth was 13.0%."
    )
    columns = ["Sector / Segment", "Credit Growth Rate (2024 data)", "Source"]
    rows = [
        ["Agriculture", "18.1%", "[S1]"],
        ["Personal loans", "17.8%", "[S1]"],
        ["MSME", "13.0%", "[S1]"],
        ["Other segment", "12.0%", "Other segment metric not verified in the excerpt [S1]"],
        ["Services", "Not verified", "[S1]"],
    ]
    text, charts, created = _add_verified_numeric_charts_from_tables(
        "## Credit growth by segment\n\n[CHART_1]\n",
        [{"type": "table", "title": "", "columns": columns, "rows": rows}],
        [_publisher_source(evidence)],
        "detailed",
        normalizer=None,
        validator=lambda chart: bool(chart.get("title") and chart.get("xLabel") and chart.get("yLabel") and chart.get("series")),
    )
    assert created == 0
    assert len(charts) == 1
    assert charts[0]["type"] == "table"
    assert text.count("[CHART_1]") == 1
    assert "[CHART_2]" not in text


def test_two_prose_values_do_not_bypass_exact_table_cell_verification():
    evidence = "Agriculture credit growth was 18.1%. Personal loan growth was 17.8%."
    columns = ["Sector", "Credit Growth Rate", "Source"]
    rows = [
        ["Agriculture", "18.1%", "[S1]"],
        ["Personal loans", "17.8%", "[S1]"],
        ["MSME", "Not verified", "[S1]"],
    ]
    text, charts, created = _add_verified_numeric_charts_from_tables(
        "[CHART_1]",
        [{"type": "table", "title": "", "columns": columns, "rows": rows}],
        [_publisher_source(evidence)],
        "in_depth",
        validator=lambda chart: True,
    )
    assert created == 0
    assert len(charts) == 1
    assert charts[0]["type"] == "table"
    assert text.count("[CHART_1]") == 1
    assert "[CHART_2]" not in text


def test_report_title_year_range_matches_next_three_years():
    year = time.gmtime().tm_year
    title = _align_title_horizon("India Banking Sector Outlook (2024‑2027)", "Assess the outlook over the next three years")
    assert f"{year}–{year + 3}" in title
    assert "2024" not in title


def test_unsupported_negative_basis_point_claim_is_removed_from_table():
    source = _publisher_source("Net interest margins faced pressure from slower deposit repricing.")
    text, charts, count = _sanitize_unverified_financial_claims(
        "[CHART_1]",
        [{"type": "table", "title": "NIM changes", "columns": ["Bank group", "NIM decline", "Source"],
          "rows": [["System average", "-21 bps", "[S1]"]]}],
        [source],
    )
    assert count >= 1
    # Unverifiable numeric rows are removed rather than preserved as a blank
    # em-dash row that appears to be a real observation.
    assert charts == []
    assert "-21 bps" not in str(charts)


def test_negative_basis_point_claim_can_be_verified_by_decline_evidence():
    source = _publisher_source("System average net interest margin declined by 21 basis points year over year.")
    assert __import__("routes.report", fromlist=["_publisher_evidence_supports_metric"])._publisher_evidence_supports_metric(
        "-21 bps", "System average NIM decline (YoY)", ["S1"], [source],
    )
