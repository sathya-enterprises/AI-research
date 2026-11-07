"""Regression tests for sector-rotation relevance and quarterly evidence charts."""
from utils.markdown_normalizer import strip_excluded_report_sections

from routes.report import (
    _sector_rotation_topic_quality_filter,
    _build_sector_quarterly_return_chart,
    _build_sector_quarterly_return_heatmap,
    _insert_sector_chart_marker,
    _ensure_sector_heatmap_chart_anchor,
    _use_calculated_sector_return_heatmap,
)


def test_sector_rotation_filter_removes_generic_charts_and_remaps_markers():
    text = "## Sector Performance\n\n[CHART_1]\n\n## Trading Activity\n\n[CHART_2]\n"
    charts = [
        {"type": "line", "title": "NSE Sector Returns by Completed Calendar Quarter",
         "series": [{"name": "NIFTY AUTO", "data": [
             {"label": "Q1 2026", "value": 2.0}, {"label": "Q2 2026", "value": -1.0},
             {"label": "Q3 2026", "value": 1.0},
         ]}]},
        {"type": "bar", "title": "NO. OF TRADES — RANKED",
         "series": [{"name": "Company trades", "data": [{"label": "Yes Bank", "value": 100}]}]},
        {"type": "bar", "title": "June 2026 Benchmark Performance Snapshot",
         "series": [{"name": "Nifty 50", "data": [{"label": "June 2026", "value": 0.9}]}]},
    ]
    filtered_text, filtered_charts, meta = _sector_rotation_topic_quality_filter(text, charts)
    assert len(filtered_charts) == 1
    assert "[CHART_1]" in filtered_text
    assert "[CHART_2]" not in filtered_text
    assert any("TRADES" in title for title in meta["removedCharts"])


def test_sector_rotation_filter_removes_placeholder_and_duplicate_heading():
    text = "## Overview\nFirst.\n\n[[[SECTION TO COMPLETE]]]\n\n## Overview\nDuplicate.\n\n## Findings\nReal finding."
    cleaned, charts, meta = _sector_rotation_topic_quality_filter(text, [])
    assert "[[[SECTION TO COMPLETE]]]" not in cleaned
    assert cleaned.count("## Overview") == 1
    assert "## Findings" in cleaned


def test_quarterly_chart_preserves_missing_quarter_as_a_gap_without_shifting_values():
    rows = [{
        "label": "NIFTY AUTO",
        "quarters": ["Q1 2025", "Q2 2025", "Q3 2025", "Q4 2025"],
        "returns": [1.0, None, -2.0, 3.0],
        "missing_quarters": ["Q2 2025"],
    }]
    chart = _build_sector_quarterly_return_chart(rows, [
        {"id": "S1", "title": "CALCULATED NSE SECTOR RETURNS — EIGHT COMPLETED CALENDAR QUARTERS",
         "url": "", "evidence_origin": "calculated_from_daily_market_data",
         "chartEvidenceExcerpt": "Quarter coverage: Q1 2025, Q2 2025, Q3 2025, Q4 2025\nNIFTY AUTO | +1.00% | N/A | -2.00% | +3.00%"}
    ])
    assert chart["type"] == "line"
    assert chart["source"] == ["S1"]
    values = chart["series"][0]["data"]
    assert len(values) == 4
    assert [point["label"] for point in values] == rows[0]["quarters"]
    assert values[1] == {"label": "Q2 2025", "value": None}
    assert [point["value"] for point in values if point["value"] is not None] == [1.0, -2.0, 3.0]


def test_generic_quarterly_financial_chart_does_not_replace_sector_return_chart():
    from routes.report import _is_sector_quarterly_return_chart
    generic = {
        "type": "line", "title": "Quarterly Financial Performance",
        "series": [{"name": "Revenue", "data": [
            {"label": "Q1 2025", "value": 12.0},
            {"label": "Q2 2025", "value": 13.0},
            {"label": "Q3 2025", "value": 14.0},
        ]}],
    }
    sector = {
        "type": "line", "title": "NSE Sector Returns by Completed Calendar Quarter",
        "series": [{"name": "NIFTY AUTO", "data": [
            {"label": "Q1 2025", "value": 2.0},
            {"label": "Q2 2025", "value": -1.0},
            {"label": "Q3 2025", "value": 3.0},
        ]}],
    }
    assert not _is_sector_quarterly_return_chart(generic)
    assert _is_sector_quarterly_return_chart(sector)


def test_heatmap_fingerprint_includes_native_matrix_data():
    text = "## Sector Performance\n\n[CHART_1]\n\n[CHART_2]"
    charts = [
        {"type": "heatmap", "title": "Sector Return Heatmap",
         "rows": ["Auto", "IT"], "columns": ["Q1 2025", "Q2 2025"], "values": [[1, 2], [3, 4]]},
        {"type": "heatmap", "title": "Quarterly Sector Performance",
         "rows": ["Auto", "IT"], "columns": ["Q1 2025", "Q2 2025"], "values": [[2, 4], [6, 8]]},
    ]
    cleaned, kept, _ = _sector_rotation_topic_quality_filter(text, charts)
    assert len(kept) == 2
    assert "[CHART_1]" in cleaned and "[CHART_2]" in cleaned


def test_calculated_eight_quarter_heatmap_replaces_model_preview_at_same_marker():
    quarters = [f"Q{i} 2025" for i in range(1, 5)] + [f"Q{i} 2026" for i in range(1, 5)]
    rows = [
        {"label": label, "quarters": quarters, "returns": values, "missing_quarters": []}
        for label, values in (
            ("NIFTY AUTO", [1.0, -2.0, 3.0, 4.0, 2.0, -1.0, 5.0, 1.0]),
            ("NIFTY IT", [-1.0, 2.0, 1.0, -3.0, 4.0, 2.0, -2.0, 3.0]),
            ("NIFTY BANK", [0.5, 1.0, -1.0, 2.0, 3.0, -2.0, 1.5, 2.5]),
        )
    ]
    sources = [{
        "id": "S1", "title": "CALCULATED NSE SECTOR RETURNS — EIGHT COMPLETED CALENDAR QUARTERS",
        "url": "", "evidence_origin": "calculated_from_daily_market_data",
        "chartEvidenceExcerpt": "Quarter coverage: " + ", ".join(quarters),
    }]
    calculated = _build_sector_quarterly_return_heatmap(rows, sources)
    assert calculated is not None
    report_text = "## Sector Return Heatmap\n\n[CHART_2]\n"
    charts = [
        {"type": "bar", "title": "Sector leadership", "series": [{"name": "Sectors", "data": []}]},
        {"type": "heatmap", "title": "Sectoral Return Heatmap (FY25-FY27)",
         "rows": ["Auto", "IT"], "columns": ["FY25", "FY26"], "values": [[1, 2], [3, 4]],
         "rationale": "The matrix organizes the sector comparison."},
    ]

    updated, result = _use_calculated_sector_return_heatmap(report_text, charts, calculated)

    assert len(result) == 2
    assert result[1]["title"].startswith("Quarterly Sector Return Heatmap (8 completed quarters:")
    assert len(result[1]["columns"]) == 8
    assert len(result[1]["rows"]) == 3
    assert result[1]["source"] == ["S1"]
    assert result[1]["rationale"] == "The matrix organizes the sector comparison."
    assert updated.count("[CHART_2]") == 1
    assert "\\n[CHART_2]" not in updated
    from routes.html_report import _render_chart_block
    rendered = _render_chart_block(result[1], 2, source_registry=sources)
    assert result[1]["title"] in rendered
    assert 'class="gg-heatmap-table"' in rendered
    assert quarters[0] in rendered and quarters[-1] in rendered


def test_sector_calculated_chart_marker_uses_newlines_and_matches_heading():
    report_text = "## Eight-Quarter Sector Performance Matrix\nExplanation."
    updated = _insert_sector_chart_marker(
        report_text, 4,
        r"(?im)^#{1,6}\s+[^\n]*eight[ -]?quarter[^\n]*$",
    )
    assert "## Eight-Quarter Sector Performance Matrix\n\n[CHART_4]\nExplanation." == updated
    assert r"\n" not in updated


def test_calculated_sector_heatmap_audits_each_exact_matrix_cell():
    from utils.chart_evidence_audit import audit_chart_evidence_consistency

    quarters = ["Q1 2025", "Q2 2025", "Q3 2025", "Q4 2025"]
    rows = [
        {"label": "NIFTY AUTO", "quarters": quarters, "returns": [1.0, -2.0, 3.0, 4.0]},
        {"label": "NIFTY IT", "quarters": quarters, "returns": [-1.0, 2.0, 1.0, -3.0]},
        {"label": "NIFTY BANK", "quarters": quarters, "returns": [0.5, 1.0, -1.0, 2.0]},
    ]
    excerpt = (
        "Quarter coverage: " + ", ".join(quarters) + "\n"
        "Sector | " + " | ".join(quarters) + "\n"
        "NIFTY AUTO | +1.00% | -2.00% | +3.00% | +4.00%\n"
        "NIFTY IT | -1.00% | +2.00% | +1.00% | -3.00%\n"
        "NIFTY BANK | +0.50% | +1.00% | -1.00% | +2.00%"
    )
    source = {
        "id": "S1", "title": "CALCULATED NSE SECTOR RETURNS — EIGHT COMPLETED CALENDAR QUARTERS",
        "url": "", "kind": "Verified data", "publisher": "Calculated from daily market data",
        "evidence_origin": "calculated_from_daily_market_data", "page_fetch_status": "not_applicable",
        "chartEvidenceExcerpt": excerpt,
    }
    chart = _build_sector_quarterly_return_heatmap(rows, [source])
    assert chart is not None

    audit = audit_chart_evidence_consistency("[CHART_1]", [chart], [source], require_publisher_verification=True)
    assert audit["chartEvidenceFailures"] == []

    tampered = {**chart, "values": [list(row) for row in chart["values"]]}
    tampered["values"][0][1] = 99.0
    rejected = audit_chart_evidence_consistency("[CHART_1]", [tampered], [source], require_publisher_verification=True)
    assert rejected["chartEvidenceFailures"][0]["row"] == "NIFTY AUTO"
    assert rejected["chartEvidenceFailures"][0]["column"] == "Q2 2025"


def test_publisher_heatmap_cell_must_match_its_period_column():
    from utils.chart_evidence_audit import audit_chart_evidence_consistency

    source = {
        "id": "S1", "url": "https://example.org/sector-returns",
        "pageFetchStatus": "ok", "evidenceExcerptOrigin": "publisher_page",
        "publisherEvidenceExcerpt": (
            "| Sector | Q1 2025 | Q2 2025 |\n|---|---:|---:|\n"
            "| NIFTY AUTO | 1.0% | -2.0% |\n| NIFTY IT | 3.0% | 4.0% |"
        ),
    }
    chart = {
        "type": "heatmap", "title": "Quarterly Sector Returns",
        "rows": ["NIFTY AUTO", "NIFTY IT"], "columns": ["Q1 2025", "Q2 2025"],
        "values": [[1.0, -2.0], [3.0, 4.0]], "unit": "%",
        "source": ["S1"],
    }
    audit = audit_chart_evidence_consistency("[CHART_1]", [chart], [source], require_publisher_verification=True)
    assert audit["chartEvidenceFailures"] == []

    tampered = {**chart, "values": [list(row) for row in chart["values"]]}
    tampered["values"][0][1] = 3.0
    rejected = audit_chart_evidence_consistency("[CHART_1]", [tampered], [source], require_publisher_verification=True)
    assert rejected["chartEvidenceFailures"]

def test_sector_chart_marker_fallback_keeps_chart_before_terminal_sources():
    report_text = (
        "## Sector Performance\n\nThe verified matrix compares eight quarters.\n\n"
        "## Data Sources\n\n| Source | Publisher |\n| --- | --- |\n| S1 | NSE |\n"
    )

    updated = _insert_sector_chart_marker(report_text, 7)

    assert updated.index("[CHART_7]") < updated.index("## Data Sources")
    assert "[CHART_7]\n\n## Data Sources" in updated

def test_sector_chart_marker_fallback_precedes_excluded_provenance_section():
    report_text = (
        "## Sector Performance\n\nThe verified matrix compares eight quarters.\n\n"
        "## Research provenance\n\nInternal capture note.\n"
    )

    updated = _insert_sector_chart_marker(report_text, 7)
    public_text = strip_excluded_report_sections(updated)

    assert updated.index("[CHART_7]") < updated.index("## Research provenance")
    assert "[CHART_7]" in public_text
    assert "Research provenance" not in public_text


def test_final_sector_cleanup_restores_heatmap_anchor_removed_with_excluded_section():
    heatmap = {
        "type": "heatmap", "title": "Quarterly Sector Return Heatmap",
        "rows": ["NIFTY AUTO", "NIFTY IT"],
        "columns": ["Q1 2025", "Q2 2025"],
        "values": [[1.0, -2.0], [0.5, 3.0]],
        "source": ["S1"],
    }
    report_text = (
        "## Sector Performance\n\nVerified return patterns are shown.\n\n"
        "## Research provenance\n\n[CHART_1]\nInternal capture note.\n"
    )

    updated, charts, changed = _ensure_sector_heatmap_chart_anchor(
        report_text, [heatmap], "Explain sector rotation in Indian stock markets"
    )

    assert changed
    assert "Research provenance" not in updated
    assert "[CHART_1]" in updated
    assert len(charts) == 1 and charts[0]["title"] == heatmap["title"]

