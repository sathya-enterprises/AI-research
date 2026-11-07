"""Quarter-aware chart gate, injected-chart placement, dangling lead-in cleanup."""
import copy

import routes.report as report
from utils.chart_evidence_audit import quarter_tokens, quarter_column_ok, quarter_window_ok, audit_chart_evidence_consistency

S2 = (
    "COMPLETED CALENDAR-QUARTER NSE SECTOR INDEX RETURNS (calculated)\n"
    "Quarter coverage: Q1 2025, Q2 2025, Q3 2025\n"
    "Sector | Q1 2025 | Q2 2025 | Q3 2025\n"
    "NIFTY PSU BANK | -3.10% | +4.72% | +19.46%\n"
    "NIFTY METAL | -1.00% | +8.20% | +14.29%\n"
    "NIFTY AUTO | -2.45% | +6.04% | +13.33%\n"
    "NIFTY PHARMA | N/A | +3.50% | +1.10%"
)


def _manifest():
    return [{
        "id": "S2", "title": "CALCULATED NSE SECTOR RETURNS", "url": "https://example.test/s2",
        "pageFetchStatus": "ok", "evidenceOrigin": "calculated_from_daily_market_data",
        "chartEvidenceExcerpt": S2, "fullContent": S2,
    }]


def _bar(title, rows):
    return {
        "type": "bar", "title": title, "unit": "%", "yLabel": "Quarterly return (%)", "source": ["S2"],
        "series": [{"name": "Return", "data": [{"label": k, "value": v} for k, v in rows]}],
    }


def test_quarter_tokens_are_exact():
    assert quarter_tokens("Quarterly Returns (Q2 2025 Cyclical Peak)") == {"Q2 2025"}
    assert quarter_tokens("Q4-2025 and Q1 2026") == {"Q4 2025", "Q1 2026"}
    assert quarter_tokens("FY25 results") == set()


def test_column_check_maps_value_to_its_quarter_even_with_na_cells():
    header = "Sector | Q1 2025 | Q2 2025 | Q3 2025"
    pharma = "NIFTY PHARMA | N/A | +3.50% | +1.10%"
    assert quarter_column_ok(pharma, header + " " + pharma, 3.5, {"Q2 2025"}) is True
    assert quarter_column_ok(pharma, header + " " + pharma, 3.5, {"Q3 2025"}) is False
    psu = "NIFTY PSU BANK | -3.10% | +4.72% | +19.46%"
    assert quarter_column_ok(psu, header + " " + psu, 19.46, {"Q2 2025"}) is False
    assert quarter_column_ok(psu, header + " " + psu, 19.46, {"Q3 2025"}) is True


def test_prose_window_rejects_other_quarter():
    assert quarter_window_ok("Auto rose 13.33%", "In Q3 2025 Auto rose 13.33%", 13.33, {"Q2 2025"}) is False
    assert quarter_window_ok("Auto rose 13.33%", "Auto rose 13.33%", 13.33, {"Q2 2025"}) is True


def test_mixed_quarter_bar_chart_points_are_rejected_by_audit():
    # PSU/Metal/Auto are Q3 values; the title claims Q2 2025.
    chart = _bar("NSE Sector Index Quarterly Returns (Q2 2025 Cyclical Peak)",
                 [("NIFTY PSU BANK", 19.46), ("NIFTY METAL", 14.29), ("NIFTY AUTO", 13.33), ("NIFTY PHARMA", 3.5)])
    audit = audit_chart_evidence_consistency("[CHART_1]", [chart], _manifest())
    bad = {f["label"] for f in audit["chartEvidenceFailures"]}
    assert {"NIFTY PSU BANK", "NIFTY METAL", "NIFTY AUTO"} <= bad
    assert "NIFTY PHARMA" not in bad  # genuine Q2 value stays supported


def test_correct_quarter_bar_chart_passes_audit():
    chart = _bar("NSE Sector Index Quarterly Returns (Q2 2025)",
                 [("NIFTY PSU BANK", 4.72), ("NIFTY METAL", 8.2), ("NIFTY AUTO", 6.04)])
    audit = audit_chart_evidence_consistency("[CHART_1]", [chart], _manifest())
    assert not audit["chartEvidenceFailures"]


def test_grounding_does_not_attach_quote_from_wrong_quarter():
    chart = _bar("Sector Returns (Q2 2025)", [("NIFTY PSU BANK", 19.46), ("NIFTY PSU BANK ", 4.72)])
    chart["series"][0]["data"] = [
        {"label": "NIFTY PSU BANK", "value": 19.46},
        {"label": "NIFTY METAL", "value": 8.2},
    ]
    enriched = [{
        "url": "https://example.test/s2", "title": "CALCULATED NSE SECTOR RETURNS",
        "pageContent": S2, "pageFetchStatus": "ok", "pageFetchUrl": "https://example.test/s2",
    }]
    manifest = _manifest()
    out, meta = report._ground_writer_chart_points([copy.deepcopy(chart)], manifest, enriched)
    points = out[0]["series"][0]["data"]
    assert "evidenceQuote" not in points[0]       # Q3 value under a Q2 title
    assert points[1].get("evidenceQuote")          # genuine Q2 value


# ---- placement ----------------------------------------------------------

REPORT = (
    "# Title\n\n## Executive Summary\n\nIntro.\n\n"
    "## Quarterly Sectoral Leadership Heatmap\n\nThe heatmap below shows eight quarters.\n\n"
    "Second paragraph.\n\n"
    "## Conclusion\n\nDone.\n\n## Data Sources\n\n- S1\n"
)


def test_heatmap_marker_lands_in_its_section_after_intro_paragraph():
    out = report._insert_sector_chart_marker(REPORT, 3, report._SECTOR_HEATMAP_HEADING_RE)
    assert out.index("[CHART_3]") > out.index("The heatmap below shows eight quarters.")
    assert out.index("[CHART_3]") < out.index("Second paragraph.")
    assert out.index("[CHART_3]") < out.index("## Conclusion")


def test_marker_goes_right_after_heading_when_followed_by_table():
    text = "## Quarterly Sector Returns\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n## End\n"
    out = report._insert_sector_chart_marker(text, 1, report._SECTOR_QUARTERLY_HEADING_RE)
    assert out.index("[CHART_1]") < out.index("| a | b |")


def test_marker_falls_back_before_sources_when_no_heading_matches():
    text = "## Intro\n\nx\n\n## Data Sources\n\n- S1\n"
    out = report._insert_sector_chart_marker(text, 1, report._SECTOR_HEATMAP_HEADING_RE)
    assert out.index("[CHART_1]") < out.index("## Data Sources")


# ---- dangling lead-ins --------------------------------------------------

def test_dangling_lead_ins_are_removed_but_real_content_kept():
    text = (
        "## Flows\n\n"
        "This shift was fueled by:\n\n"
        "## Risk\n\n"
        "Risk profiles varied significantly:\n\n"
        "- **Weight vs.\n\n"
        "## Valid\n\n"
        "Banks fell 2% [S1].\n\n"
        "Valid lead-in:\n\n"
        "- item one\n- item two\n\n"
        "### Empty heading\n\n"
        "## Outlook\n\nKept paragraph.\n"
    )
    out, removed = report._remove_dangling_lead_ins(text)
    assert "fueled by" not in out
    assert "Risk profiles varied" not in out
    assert "**Weight vs." not in out
    assert "Empty heading" not in out
    assert "Valid lead-in:" in out and "- item one" in out
    assert "Banks fell 2%" in out and "Kept paragraph." in out
    assert removed == 4


def test_lead_in_before_chart_marker_or_table_is_kept():
    text = "The heatmap below:\n\n[CHART_2]\n\nTable follows:\n\n| a | b |\n|---|---|\n| 1 | 2 |\n"
    out, removed = report._remove_dangling_lead_ins(text)
    assert removed == 0 and out.strip() == text.strip()


def test_sanitizer_cleans_lead_in_after_sentence_redaction():
    text = (
        "## Flows\n\n"
        "This shift was fueled by:\n\n"
        "Nifty Auto declined 17.3% in March [S1].\n\n"
        "## Outlook\n\nKept paragraph.\n"
    )
    sources = [{"id": "S1", "_citedClaimGroups": [{
        "groupId": "u", "sourceIds": ["S1"], "claim": "Nifty Auto declined 17.3% in March",
        "sentence": "Nifty Auto declined 17.3% in March [S1].", "claimTokens": ["percent:17.3"], "supportByToken": {},
    }]}]
    cleaned, _c, count = report._sanitize_unverified_financial_claims(text, [], sources)
    assert count == 1
    assert "fueled by" not in cleaned
    assert "Kept paragraph." in cleaned
