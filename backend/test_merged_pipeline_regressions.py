"""Regression coverage for merged chart-density, source-evidence and report-quality fixes."""
from routes.source_manifest import build_source_registry
from utils.market_data import format_sector_quarterly_returns_as_source
import routes.report as report


def _sector_rows():
    quarters = ["Q4 2024", "Q1 2025", "Q2 2025", "Q3 2025", "Q4 2025", "Q1 2026", "Q2 2026", "Q3 2026"]
    return [
        {"label": "NIFTY AUTO", "quarters": quarters,
         "returns": [1.5, -2.2, 3.4, 4.1, -0.5, 2.2, 1.9, 5.1], "missing_quarters": []},
        {"label": "NIFTY METAL", "quarters": quarters,
         "returns": [0.5, 1.2, -1.4, 2.0, 4.5, -2.1, 3.9, 2.1], "missing_quarters": []},
        {"label": "NIFTY FMCG", "quarters": quarters,
         "returns": [-0.5, 0.4, 0.8, 1.1, -1.2, 0.6, 0.2, 0.9], "missing_quarters": []},
    ]


def test_sector_matrix_line_heatmap_and_leader_laggard_charts_are_auditable():
    rows = _sector_rows()
    source = format_sector_quarterly_returns_as_source(rows)
    manifest = build_source_registry([source])
    assert manifest[0]["url"] == ""  # internal URL normalized away on purpose
    assert "NIFTY AUTO | +1.50%" in manifest[0]["chartEvidenceExcerpt"]

    chart_builders = [
        report._build_sector_quarterly_return_chart,
        report._build_sector_quarterly_return_heatmap,
        report._build_sector_leader_laggard_chart,
    ]
    for builder in chart_builders:
        chart = builder(rows, manifest)
        assert chart is not None
        assert report._validate_chart_spec(chart)
        body = "## Quarterly Sector-Return Matrix\n\nThe return series are observed data.\n\n[CHART_1]"
        cleaned, charts, final_manifest, audited, meta = report._final_chart_evidence_gate(
            body, [chart], manifest, [], normalizer=lambda item: item,
            validator=report._validate_chart_spec,
            adapter=report._normalise_inline_chart_spec,
            stage="merged-regression",
        )
        assert len(charts) == 1
        assert "[CHART_1]" in cleaned
        assert meta["residualFindings"] == 0


def test_sector_matrix_is_inserted_and_missing_values_are_not_fabricated():
    rows = _sector_rows()
    rows[0]["returns"][3] = None
    rows[0]["missing_quarters"] = ["Q3 2025"]
    manifest = build_source_registry([format_sector_quarterly_returns_as_source(rows)])
    body = "## Eight-Quarter Sector Performance Matrix\n\nThis section studies sector performance.\n\n## Drivers\nEvidence follows."
    merged = report._insert_sector_quarterly_return_matrix(body, rows, manifest)
    assert "| Sector index | Q4 2024 | Q1 2025 | Q2 2025 | Q3 2025" in merged
    assert "N/A" in merged
    assert "NIFTY AUTO: Q3 2025" in merged
    assert merged.index("| Sector index |") < merged.index("## Drivers")


def test_sector_sanitizer_removes_leaked_json_and_incomplete_placeholders():
    raw = '''## Matrix\n\nReddy's, Cipla\", \"Counter-Cyclical Resilience\", \"1.34\", \"Defensive\"], [\"Nifty FMCG\", \"HUL, ITC, Nestle\", \"Stable Compounder\", \"1.28\"], \"source\": \"[S2]\" } ```\n\n[[[SECTION TO COMPLETE]]]\n\n## Macro Context\n\nUseful analysis remains.\n'''
    cleaned = report._sanitize_sector_rotation_report_text(raw)
    assert "Counter-Cyclical Resilience" not in cleaned
    assert "source\": \"[S2]" not in cleaned
    assert "SECTION TO COMPLETE" not in cleaned
    assert "Useful analysis remains." in cleaned


def test_sector_topic_filter_removes_generic_kpi_and_exact_duplicate_charts():
    bad_kpi = {
        "type": "bar", "title": "KPI — INCLUDE EARNINGS PER SHARE (FY26)",
        "xLabel": "Entity", "yLabel": "EPS", "series": [{"name": "Include", "data": [{"label": "Include", "value": 50}]}],
    }
    core = report._build_sector_quarterly_return_chart(_sector_rows(), build_source_registry([format_sector_quarterly_returns_as_source(_sector_rows())]))
    duplicate = dict(core)
    text = "## Sector Returns\n\n[CHART_1]\n\n[CHART_2]\n\n[CHART_3]"
    cleaned, charts, meta = report._sector_rotation_topic_quality_filter(text, [bad_kpi, core, duplicate])
    assert len(charts) == 1
    assert charts[0]["title"] == core["title"]
    assert meta["removedCharts"]
    assert "[CHART_1]" in cleaned
    assert "[CHART_2]" not in cleaned
    assert "[CHART_3]" not in cleaned


def test_generic_prompt_word_is_not_a_sector_kpi():
    cleaned = report._remove_generic_sector_key_stats([
        {"label": "Include earnings per share (FY26)", "value": "50"},
        {"label": "Nifty 50 return", "value": "+4.2%"},
    ])
    assert len(cleaned) == 1
    assert cleaned[0]["label"] == "Nifty 50 return"


def test_screener_database_records_keep_direct_company_url_as_citation():
    from utils.screener_kb import format_screener_snapshots_as_sources
    records = format_screener_snapshots_as_sources([{
        "company": {
            "id": 7, "ticker": "500014", "name": "Utique Enterprises Ltd",
            "company_url": "https://www.screener.in/company/500014/",
            "current_price": 3.89, "price_change_pct": 2.37,
        },
        "ratios": [], "financials": [], "growth_cagr": [], "shareholding": [],
    }])
    assert len(records) == 1
    assert records[0]["url"] == "https://www.screener.in/company/500014/"
    assert records[0]["publisher"] == "Screener.in"
    assert records[0]["evidenceOrigin"] == "supabase_screener_snapshot"
    assert "Utique Enterprises Ltd" in records[0]["fullContent"]


def test_chart_narrative_diagnostic_is_available():
    result = report.audit_chart_narrative(
        "The comparison matters because the quarterly sector returns diverge sharply across the period.\n\n"
        "[CHART_1]\n\nThe pattern suggests rotating leadership, although the incomplete sector coverage limits conclusions.",
        [{"title": "NSE Sector Returns by Completed Calendar Quarter", "rationale": "Compare quarterly returns across sectors", "takeaway": "Leadership varies by quarter"}],
    )
    assert result["chart_marker_count"] == 1
    assert result["status"] in {"pass", "warning"}
