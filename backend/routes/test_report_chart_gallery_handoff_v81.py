"""End-to-end regression coverage for evidence chart placement and publication integrity."""
from __future__ import annotations

import re

from routes.html_report import build_html_report
from routes.pdf import _replace_chart_runtime_with_svg
from routes.report import _sanitize_report_key_stats
from utils.chart_gallery import GalleryChart, GalleryResult, blend_gallery_into_report
from utils.report_integrity import (
    remove_unverified_table_chart_specs,
    repair_banking_growth_and_ratio_labels,
    sanitize_unverified_markdown_tables,
)


def _series_chart(kind: str, title: str, name: str, labels: list[str], values: list[float],
                  x_label: str, y_label: str) -> dict:
    return {
        "type": kind,
        "title": title,
        "xLabel": x_label,
        "yLabel": y_label,
        "unit": y_label,
        "series": [{"name": name, "data": [
            {"label": label, "value": value} for label, value in zip(labels, values)
        ]}],
    }


def test_gallery_places_deposit_series_under_deposit_analysis_and_advances_under_credit():
    report = (
        "## Credit and Deposit Growth Analysis\n"
        "### 1. Recent deposit expansion\n"
        "Deposit mobilisation and higher term savings have shaped funding.\n\n"
        "### 2. Credit growth trajectory\n"
        "Credit growth and advances reflect lending demand.\n\n"
        "### 3. Deposit-to-Loan Ratio\n"
        "The ratio compares deposits with loans and advances.\n"
    )
    charts = [
        GalleryChart("line", _series_chart("line", "Savings bank deposits trend", "Savings deposits", ["FY23", "FY24"], [80, 95], "Financial year", "₹ crore"), "Reported values [S1]", "reported source values"),
        GalleryChart("grouped_bar", _series_chart("grouped_bar", "Demand deposits by year", "Demand deposits", ["FY23", "FY24"], [70, 78], "Financial year", "₹ crore"), "Reported values [S1]", "reported source values"),
        GalleryChart("bar", _series_chart("bar", "Advances by bank group", "Advances", ["Public", "Private"], [120, 140], "Bank group", "₹ crore"), "Reported values [S2]", "reported source values"),
    ]
    merged, ordered_charts = blend_gallery_into_report(report, GalleryResult(charts=charts, observation_count=6), 1)
    lines = merged.splitlines()
    deposit_heading = next(i for i, line in enumerate(lines) if line == "### 1. Recent deposit expansion")
    credit_heading = next(i for i, line in enumerate(lines) if line == "### 2. Credit growth trajectory")
    ratio_heading = next(i for i, line in enumerate(lines) if line == "### 3. Deposit-to-Loan Ratio")
    deposit_markers = [i for i, line in enumerate(lines) if re.fullmatch(r"\[CHART_\d+\]", line) and deposit_heading < i < credit_heading]
    credit_markers = [i for i, line in enumerate(lines) if re.fullmatch(r"\[CHART_\d+\]", line) and credit_heading < i < ratio_heading]
    assert len(deposit_markers) == 2
    assert len(credit_markers) == 1
    assert len(ordered_charts) == 3
    assert sorted(int(x) for x in re.findall(r"\[CHART_(\d+)\]", merged)) == [1, 2, 3]


def test_fake_chart_table_cleanup_does_not_delete_section_or_real_following_section():
    report = (
        "## Credit and Deposit Growth Analysis\n"
        "### 1. Recent deposit expansion\nDeposits expanded during the reported period.\n\n"
        "### 4. Chart – Deposit vs. Credit Growth\n"
        "*Type*: Clustered bar chart. Values are said to compare FY23 and FY24.\n\n"
        "[CHART_1]\n\n"
        "### 2. Credit growth trajectory\nCredit and advances remained an important funding-use metric.\n"
    )
    fake = [{"type": "table", "title": "4. Chart – Deposit vs. Credit Growth",
             "columns": ["Period", "Deposits (%)", "Credit (%)"],
             "rows": [["FY23", "12.0", "14.0"], ["FY24", "13.0", "15.0"]]}]
    cleaned, kept, metadata = remove_unverified_table_chart_specs(report, fake, sources=[])
    assert kept == []
    assert metadata["removedTables"] == 1
    assert "## Credit and Deposit Growth Analysis" in cleaned
    assert "### 1. Recent deposit expansion" in cleaned
    assert "### 2. Credit growth trajectory" in cleaned
    assert "### 4. Chart – Deposit vs. Credit Growth" not in cleaned
    assert "[CHART_1]" not in cleaned


def test_pdf_boundary_materializes_inline_canvas_as_real_svg():
    chart = _series_chart("bar", "Credit growth by bank group", "Credit growth", ["Public", "Private"], [12.4, 14.1], "Bank group", "Growth (%)")
    report = "## Credit Growth\nReported credit growth differed between bank groups.\n\n[CHART_1]"
    html_doc = build_html_report(report, "Banking Outlook", "Compare credit growth", "", [], [chart], [], sources=[], report_tier="detailed")
    assert '<canvas id="gg-chart-1"></canvas>' in html_doc
    static_html = _replace_chart_runtime_with_svg(html_doc)
    assert '<svg' in static_html
    assert 'Credit growth by bank group' in static_html
    assert '<canvas id="gg-chart-1"></canvas>' not in static_html
    assert 'window.__ggCharts.push' not in static_html


def test_unlabeled_key_signal_is_removed_instead_of_rendering_an_orphan_number():
    clean, removed = _sanitize_report_key_stats([
        {"value": "1.27", "label": ""},
        {"value": "1.27", "label": "Key signal"},
    ], sources=[])
    assert clean == []
    assert removed == 2


def test_numeric_table_with_placeholder_column_names_is_disclosed():
    report = (
        "## Capital structure\n\n"
        "| Bank type | — | — | CAR (overall) |\n"
        "| --- | ---: | ---: | ---: |\n"
        "| Public-sector banks | 71.2 | 28.8 | 8.3 |\n"
        "| Private-sector banks | 78.5 | 21.5 | 8.7 |\n"
    )
    cleaned, findings = sanitize_unverified_markdown_tables(report, sources=[])
    assert "| Public-sector banks |" not in cleaned
    assert "Table omitted" in cleaned
    assert findings and findings[0]["invalidHeaders"] is True


def test_verified_rbi_totals_correct_inverse_ratio_direction():
    source = {
        "id": "S1", "url": "https://www.rbi.org.in/example", "pageFetchStatus": "ok",
        "pageContent": (
            "3. Deposits | 1,000 | 1,140\n"
            "4. Loans and advances | 700 | 840\n"
        ),
    }
    report = (
        "## Funding\n"
        "- **Loan-to-Deposit Ratio**: 1.27 (up from 1.20 in FY 2022-23).\n"
        "*Implication* – The DLR’s slight contraction in 2024 signals a tighter funding environment but still leaves banks comfortably funded, reducing liquidity risk."
    )
    repaired, meta = repair_banking_growth_and_ratio_labels(report, [source], [{"id": "S1", "url": source["url"]}])
    assert meta["changed"] is True
    assert "deposit-to-loan ratio" in repaired.casefold()
    assert "loan-to-deposit ratio was 0.74" in repaired.casefold()
    assert len(re.findall(r"deposit-to-loan ratio\*{0,2}\s*:\s*1\.36", repaired, re.I)) == 1
    assert "slight contraction" not in repaired.casefold()
