"""Regressions for one evidence basis per upload and stable source IDs.

1. Attachment-only reports: the writer is given the server-calculated analytics and the
   attachment number check accepts them, but the evidence guard and chart gate verified only
   the raw file rows, so correct calculated values (means, ranges, counts) were deleted.
2. Mid-run registry rebuilds (entity search, gap agent) numbered web rows before uploads, so
   adding a web page moved an upload from S6 to S7 and gave S6 to the new page.
"""
from __future__ import annotations

import pytest

import routes.report as report
from routes.source_manifest import build_source_registry, rebuild_source_registry_preserving_ids
from utils.chart_evidence_audit import audit_chart_evidence_consistency
from utils.uploaded_workbook_analytics import build_attachment_analytics

_ROWS = "\n".join(
    f"Fund {i},{'Equity' if i % 2 else 'Debt'},{(i * 1.37) % 9 + 1:.2f},{(i * 2.11) % 15 + 3:.2f}"
    for i in range(1, 21)
)
FILE_CONTEXT = "[File: funds.xlsx]\n[Sheet: Returns]\nFund,Category,Return 1 Yr (%),Return 3 Yr (%)\n" + _ROWS


def _upload_manifest(register: bool):
    report._UPLOADED_FILE_EVIDENCE_TEXTS.set({})
    documents: list[dict] = []
    report._seed_uploaded_file_evidence(FILE_CONTEXT, documents)
    if register:
        analytics = build_attachment_analytics(FILE_CONTEXT)
        assert report._register_attachment_analytics_evidence(analytics, documents) == 1
    return build_source_registry([], documents)


def _blocking(manifest, sentence: str) -> bool:
    audited = report._attach_audit_source_evidence(manifest, [], sentence)
    return report._report_source_evidence_audit(audited, attachment_only=True)["blocking"]


@pytest.mark.parametrize("sentence", [
    "The mean 1 Yr return across the attached funds was 5.485% [S1].",
    "The 3 Yr return ranged from 4.65% to 17.77% across the attached funds [S1].",
])
def test_calculated_attachment_statistics_pass_the_evidence_guard(sentence):
    assert _blocking(_upload_manifest(register=False), sentence) is True  # the old, contradictory behaviour
    assert _blocking(_upload_manifest(register=True), sentence) is False


def test_values_absent_from_rows_and_analytics_are_still_blocked():
    assert _blocking(_upload_manifest(register=True),
                     "The mean 1 Yr return across the attached funds was 6.20% [S1].") is True


def _mean_chart(one_year: float) -> dict:
    return {
        "type": "bar", "title": "Mean return by horizon in the attached funds", "unit": "%",
        "yLabel": "Mean return (%)", "source": "S1",
        "series": [{"name": "Mean return", "data": [
            {"label": "Return 1 Yr (%)", "value": one_year, "evidenceSource": "S1"},
            {"label": "Return 3 Yr (%)", "value": 10.9, "evidenceSource": "S1"},
        ]}],
    }


def _chart_failures(manifest, chart: dict) -> list[dict]:
    result = audit_chart_evidence_consistency(
        "[CHART_1]", [chart], manifest, question="Analyze the attached workbook funds.xlsx",
        require_publisher_verification=True,
    )
    return list(result.get("chartEvidenceFailures") or [])


def test_chart_gate_accepts_calculated_points_and_rejects_wrong_ones():
    manifest = _upload_manifest(register=True)

    assert _chart_failures(manifest, _mean_chart(5.485)) == []
    failures = _chart_failures(manifest, _mean_chart(6.2))
    assert [failure.get("value") for failure in failures] == ["6.2"]


def _page(index: int, body: str = "") -> dict:
    return {"url": f"https://site{index}.com/a", "title": f"Publisher page number {index}",
            "pageContent": body or f"Page {index} reports revenue {index * 10}% higher. " * 30,
            "pageFetchStatus": "ok"}


def test_adding_web_sources_mid_run_does_not_renumber_existing_sources():
    documents = [{"name": "file.xlsx", "text": "abc", "evidence_origin": "uploaded_workbook"}]
    rows = [_page(i) for i in range(1, 6)]
    first = build_source_registry(rows, documents)
    assert [s["id"] for s in first if s["title"] == "file.xlsx"] == ["S6"]

    enriched = report._dedupe_enriched_sources(rows + [_page(9), _page(2, "z" * 900)])
    rebuilt = rebuild_source_registry_preserving_ids(first, enriched, documents)

    by_id = {s["id"]: (s.get("url") or s["title"]) for s in rebuilt}
    assert by_id == {
        "S1": "https://site1.com/a", "S2": "https://site2.com/a", "S3": "https://site3.com/a",
        "S4": "https://site4.com/a", "S5": "https://site5.com/a", "S6": "file.xlsx",
        "S7": "https://site9.com/a",
    }


def test_rebuild_keeps_current_metadata_and_keeps_vanished_sources_resolvable():
    rows = [_page(1), {**_page(2), "pageFetchStatus": "http_403", "pageContent": ""}]
    first = build_source_registry(rows, [])
    refetched = [_page(1), _page(2)]  # S2 now fetched successfully; nothing removed yet

    rebuilt = rebuild_source_registry_preserving_ids(first, refetched, [])
    assert {s["id"]: s["page_fetch_status"] for s in rebuilt} == {"S1": "ok", "S2": "ok"}

    shrunk = rebuild_source_registry_preserving_ids(first, [_page(1)], [])
    assert [s["id"] for s in shrunk] == ["S1", "S2"]
