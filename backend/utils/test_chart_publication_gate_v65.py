"""V65: export must not 422 because of empty/unsupported charts; they are pruned instead."""
from utils.chart_evidence_audit import (
    audit_chart_evidence_consistency, prune_unsupported_chart_points,
    remap_chart_placeholders, sanitize_charts_for_publication,
)

SOURCES = [{"id": "S1", "title": "MOFSL", "pageContent":
            "Defense (+19%), Metals (+14%) and PSU Banks (+10%) were the key outperformers over the past two years 2024 2026."}]


def _bar(title, points, sid="S1"):
    return {"type": "bar", "title": title, "unit": "%", "sourceIds": [sid],
            "series": [{"name": "2-yr CAGR", "data": [{"label": l, "value": v} for l, v in points]}]}


def test_empty_table_is_pruned_not_kept():
    charts = [_bar("Sector", [("Defense", 19), ("Metals", 14)]),
              {"type": "table", "title": "Supporting Data Table", "columns": ["A"], "rows": []}]
    audit = audit_chart_evidence_consistency("", charts, SOURCES)
    kept, removed, _ = prune_unsupported_chart_points(charts, audit)
    assert removed == [2] and len(kept) == 1


def test_unsupported_points_removed_and_export_audit_is_clean():
    report = "Intro\n[CHART_1]\nMiddle\n[CHART_2]\nEnd [CHART_3]"
    charts = [
        {"type": "table", "title": "Empty", "columns": [], "rows": []},
        _bar("Sector", [("Defense", 19), ("Metals", 14), ("Pharma", 77)]),
        _bar("Invented", [("X", 31), ("Y", 62)]),
    ]
    text, kept, meta = sanitize_charts_for_publication(report, charts, SOURCES)
    assert meta["remainingFindings"] == []
    assert len(kept) == 1 and kept[0]["title"] == "Sector"
    labels = [p["label"] for p in kept[0]["series"][0]["data"]]
    assert labels == ["Defense", "Metals"]
    assert "[CHART_1]" in text and "[CHART_2]" not in text and "[CHART_3]" not in text
    final = audit_chart_evidence_consistency(text, kept, SOURCES)
    assert not final["chartEvidenceFailures"] and not final["chartTableVerificationConflicts"]


def test_remap_placeholders():
    assert remap_chart_placeholders("a\n[CHART_1]\n[CHART_2]\n[CHART_3]", [False, True, True]) == "a\n[CHART_1]\n[CHART_2]"


def test_clean_charts_untouched():
    charts = [_bar("Sector", [("Defense", 19), ("Metals", 14)])]
    text, kept, meta = sanitize_charts_for_publication("x [CHART_1]", charts, SOURCES)
    assert kept == charts and text == "x [CHART_1]" and meta["removedCharts"] == 0 and meta["prunedPoints"] == 0


def test_strict_chart_gate_rejects_search_snippets_as_publisher_verification():
    chart = _bar("Bank credit growth", [("2024", 10), ("2025", 12)])
    snippet_only = [{"id": "S1", "title": "Bank credit", "snippet": "Bank credit growth was 10% in 2024 and 12% in 2025.", "page_fetch_status": "unknown"}]
    _text, kept, meta = sanitize_charts_for_publication(
        "## Credit growth\n\n[CHART_1]", [chart], snippet_only,
        question="How is India's banking sector likely to perform?", require_publisher_verification=True,
    )
    assert kept == []
    assert meta["removedCharts"] == 1


def test_banking_chart_gate_removes_irrelevant_and_duplicate_metric_visuals():
    source = [{
        "id": "S1", "title": "Bank credit data", "url": "https://rbi.org.in/data",
        "pageFetchStatus": "ok", "evidenceOrigin": "publisher_page",
        "evidenceExcerptOrigin": "publisher_page",
        "publisherEvidenceExcerpt": "Bank credit growth was 10% in 2024 and 12% in 2025.",
    }]
    first = {"type": "bar", "title": "Bank credit growth", "unit": "%", "sourceIds": ["S1"],
             "series": [{"name": "Bank credit growth", "data": [{"label": "2024", "value": 10}, {"label": "2025", "value": 12}]}]}
    duplicate = {**first, "type": "line", "title": "Bank credit growth trend"}
    irrelevant = {"type": "bar", "title": "Current Net Owned Fund", "unit": "INR", "sourceIds": ["S1"],
                  "series": [{"name": "Fund size", "data": [{"label": "2024", "value": 100}, {"label": "2025", "value": 110}]}]}
    report = "## Credit growth\n\n[CHART_1]\n\n[CHART_2]\n\n[CHART_3]"
    cleaned, kept, meta = sanitize_charts_for_publication(
        report, [first, duplicate, irrelevant], source,
        question="How is India's banking sector likely to perform over the next three years?",
        require_publisher_verification=True,
    )
    assert len(kept) == 1 and kept[0]["title"] == "Bank credit growth"
    assert "[CHART_1]" in cleaned and "[CHART_2]" not in cleaned and "[CHART_3]" not in cleaned
    assert meta["removedCharts"] == 2


def test_missing_value_marker_still_blocks_chart_after_placeholder_normalization():
    chart = _bar("Solar installed capacity FY2023-24", [("FY2023-24", 90.8)])
    report = "| Segment | FY2023-24 |\n| --- | --- |\n| Solar Power | — |"
    sources = [{"id": "S1", "page_fetch_status": "empty_extraction", "evidence_available": False}]
    _cleaned, kept, meta = sanitize_charts_for_publication(report, [chart], sources, question="Research Report")
    assert kept == []
    assert meta["remainingFindings"]
    assert any("table" in str(row.get("reason", "")).lower() for row in meta["remainingFindings"])


def test_valid_chart_caption_without_marker_is_recovered_before_export_gate():
    source = [{
        "id": "S1", "title": "Bank credit data", "url": "https://rbi.org.in/data",
        "pageFetchStatus": "ok", "evidenceOrigin": "publisher_page",
        "evidenceExcerptOrigin": "publisher_page",
        "publisherEvidenceExcerpt": "Bank credit growth was 10% in 2024 and 12% in 2025.",
    }]
    chart = {"type": "bar", "title": "Bank credit growth", "unit": "%", "sourceIds": ["S1"],
             "series": [{"name": "Bank credit growth", "data": [{"label": "2024", "value": 10}, {"label": "2025", "value": 12}]}]}
    report = "## Credit growth\n\nChart: Bank credit growth\n\nThe trend moderated from 12% to 10%."
    cleaned, kept, meta = sanitize_charts_for_publication(report, [chart], source, require_publisher_verification=True)
    assert len(kept) == 1
    assert cleaned.count("[CHART_1]") == 1
    assert cleaned.index("Chart: Bank credit growth") < cleaned.index("[CHART_1]")
    assert meta["captionMatchedChartsAttached"] == [1]
