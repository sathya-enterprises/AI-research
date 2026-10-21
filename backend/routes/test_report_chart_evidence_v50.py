from __future__ import annotations

import asyncio
import json
import sys
import re
from pathlib import Path

import pytest

sys.path.insert(0, Path(__file__).resolve().parents[1].as_posix())

from utils.report_quality import audit_report_publication
from routes.report import _publication_audit_is_blocking, _publication_status_from_audits


FIXTURE = Path(__file__).parent / "fixtures" / "growth-gradual-report-2026-10-15-before-fix.pdf"


def _solar_chart(value=90.8, source="S1"):
    return {
        "type": "bar", "title": "Solar installed capacity FY2023-24",
        "xLabel": "Financial year", "yLabel": "Capacity (GW)", "unit": "GW",
        "source": [source],
        "series": [{"name": "Solar Power", "data": [{"label": "FY2023-24", "value": value}]}],
    }


def test_original_pdf_fixture_preserves_known_before_fix_defects():
    fitz = pytest.importorskip("fitz")
    assert FIXTURE.is_file(), "Keep the original 2026-10-15 report as a permanent regression fixture."
    document = fitz.open(FIXTURE)
    text = re.sub(r"\s+", " ", " ".join(page.get_text() for page in document))
    assert len(document) == 3
    assert "file:///" in text and "report.html" in text
    assert "Not verified" in text
    assert all(value in text for value in ("73.3", "44.3", "90.8", "45.9"))
    assert "empty extraction" in text.casefold()
    assert "no usable source text retrieved" in text.casefold()


def test_chart_cannot_promote_table_cells_marked_unverified():
    report = (
        "## Capacity comparison\n\n"
        "| Segment | FY2022-23 | FY2023-24 |\n| --- | --- | --- |\n"
        "| Solar Power | Not verified | Not verified |\n"
        "| Wind Power | Not verified | Not verified |\n"
    )
    charts = [{
        "type": "bar", "title": "Solar vs Wind Installed Capacity", "unit": "GW",
        "series": [
            {"name": "FY2022-23", "data": [{"label": "Solar Power", "value": 73.3}, {"label": "Wind Power", "value": 44.3}]},
            {"name": "FY2023-24", "data": [{"label": "Solar Power", "value": 90.8}, {"label": "Wind Power", "value": 45.9}]},
        ], "source": ["S1"],
    }]
    audit = audit_report_publication(report, charts, [], [{
        "id": "S1", "pageFetchStatus": "ok",
        "evidenceExcerpt": "Solar power capacity was 73.3 GW in FY2022-23 and 90.8 GW in FY2023-24. Wind power capacity was 44.3 GW and 45.9 GW in those years.",
    }])
    assert audit["chartTableVerificationConflicts"]
    assert _publication_audit_is_blocking(audit)
    assert _publication_status_from_audits({"publicationAudit": audit}) == "FAIL"


def test_chart_conflict_is_detected_after_markdown_table_becomes_structured_table_block():
    chart = _solar_chart()
    structured_table = {
        "type": "table", "title": "Capacity verification status",
        "columns": ["Segment", "FY2023-24"], "rows": [["Solar Power", "Not verified"]],
    }
    audit = audit_report_publication(
        "## Capacity\n\n[CHART_1]\n\n[CHART_2]",
        [chart, structured_table], [], [{
            "id": "S1", "pageFetchStatus": "ok",
            "evidenceExcerpt": "Solar installed capacity reached 90.8 GW in FY2023-24.",
        }],
    )
    assert audit["chartTableVerificationConflicts"]
    assert _publication_audit_is_blocking(audit)


def test_numeric_chart_fails_when_cited_source_extraction_failed():
    audit = audit_report_publication(
        "## Solar capacity\n\nSee chart [CHART_1].",
        [_solar_chart()], [], [{"id": "S1", "pageFetchStatus": "empty_extraction", "evidenceAvailable": False}],
    )
    assert audit["chartEvidenceFailures"]
    assert "no usable extracted evidence" in audit["chartEvidenceFailures"][0]["reason"].casefold()
    assert _publication_audit_is_blocking(audit)


def test_numeric_chart_fails_when_source_text_does_not_support_value():
    audit = audit_report_publication(
        "## Solar capacity\n\nSee chart [CHART_1].", [_solar_chart()], [], [{
            "id": "S1", "pageFetchStatus": "ok",
            "evidenceExcerpt": "Solar installed capacity reached 88.0 GW in FY2023-24.",
        }],
    )
    assert audit["chartEvidenceFailures"]
    assert "no cited source excerpt" in audit["chartEvidenceFailures"][0]["reason"].casefold()
    assert _publication_audit_is_blocking(audit)


def test_numeric_chart_does_not_borrow_another_series_entity():
    chart = _solar_chart(value=73.3)
    chart["series"][0]["name"] = "FY2022-23"
    chart["series"][0]["data"][0]["label"] = "Wind Power"
    audit = audit_report_publication("See chart [CHART_1].", [chart], [], [{
        "id": "S1", "pageFetchStatus": "ok",
        "evidenceExcerpt": "Solar installed capacity was 73.3 GW in FY2022-23.",
    }])
    assert audit["chartEvidenceFailures"]
    assert _publication_audit_is_blocking(audit)


def test_numeric_chart_fails_when_source_unit_does_not_match_chart_unit():
    audit = audit_report_publication("See chart [CHART_1].", [_solar_chart(73.3)], [], [{
        "id": "S1", "pageFetchStatus": "ok",
        "evidenceExcerpt": "Solar installed capacity was 73.3 MW in FY2023-24.",
    }])
    assert audit["chartEvidenceFailures"]
    assert audit["chartEvidenceFailures"][0]["value"] == "73.3"


def test_numeric_chart_passes_when_value_subject_period_and_source_match():
    audit = audit_report_publication(
        "## Solar capacity\n\nSee chart [CHART_1].", [_solar_chart()], [], [{
            "id": "S1", "pageFetchStatus": "ok",
            "evidenceExcerpt": "Solar installed capacity reached 90.8 GW in FY2023-24.",
        }],
    )
    assert audit["chartEvidenceFailures"] == []
    assert audit["chartTableVerificationConflicts"] == []


def test_narrative_report_without_optional_chart_is_not_failed_for_missing_visuals():
    audit = audit_report_publication("## Findings\n\nSolar capacity increased; evidence remains limited.", [], [], [])
    assert audit["chartEvidenceFailures"] == []
    assert audit["chartTableVerificationConflicts"] == []


def _request(body):
    from starlette.requests import Request

    raw = json.dumps(body).encode("utf-8")

    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}

    return Request({"type": "http", "method": "POST", "path": "/", "headers": []}, receive)


@pytest.mark.parametrize("route_module,handler_name", [
    ("routes.pdf", "generate_pdf"),
    ("routes.html_report", "generate_html_report"),
    ("routes.docx_report", "generate_docx"),
])
def test_all_export_handlers_reject_chart_table_verification_conflicts(route_module, handler_name):
    import importlib

    report = "| Segment | FY2023-24 |\n| --- | --- |\n| Solar Power | Not verified |"
    body = {"report": report, "charts": [_solar_chart()], "sources": [{
        "id": "S1", "page_fetch_status": "empty_extraction", "evidence_available": False,
    }]}
    module = importlib.import_module(route_module)
    response = asyncio.run(getattr(module, handler_name)(_request(body)))
    assert response.status_code == 422
    payload = json.loads(response.body)
    assert payload["publicationStatus"] == "FAIL"
    assert payload["exported"] is False
    assert payload["findings"]


def test_chart_evidence_prunes_only_unsupported_points_and_keeps_supported_series():
    from utils.chart_evidence_audit import audit_chart_evidence_consistency, prune_unsupported_chart_points

    chart = {
        "type": "line", "title": "Solar capacity trend", "xLabel": "Financial year",
        "yLabel": "Capacity (GW)", "unit": "GW", "source": ["S1"],
        "series": [{"name": "Solar Power", "data": [
            {"label": "FY2023-24", "value": 90.8},
            {"label": "FY2024-25", "value": 99.9},
            {"label": "FY2025-26", "value": 101.2},
        ]}],
    }
    sources = [{
        "id": "S1", "pageFetchStatus": "ok",
        "evidenceExcerpt": "Solar power capacity reached 90.8 GW in FY2023-24 and 101.2 GW in FY2025-26.",
    }]
    audit = audit_chart_evidence_consistency("See chart [CHART_1].", [chart], sources)
    assert len(audit["chartEvidenceFailures"]) == 1
    assert audit["chartEvidenceFailures"][0]["pointIndex"] == 1

    kept, removed_indices, pruned_count = prune_unsupported_chart_points([chart], audit)
    assert removed_indices == []
    assert pruned_count == 1
    assert len(kept) == 1
    assert kept[0]["series"][0]["data"] == [
        {"label": "FY2023-24", "value": 90.8},
        {"label": "FY2025-26", "value": 101.2},
    ]


def test_chart_with_too_few_verified_points_is_removed_after_pruning():
    from utils.chart_evidence_audit import audit_chart_evidence_consistency, prune_unsupported_chart_points

    chart = {
        "type": "bar", "title": "Bank profits", "unit": "INR crore", "source": ["S1"],
        "series": [{"name": "Net profit", "data": [
            {"label": "SBI", "value": 100},
            {"label": "HDFC Bank", "value": 200},
        ]}],
    }
    sources = [{"id": "S1", "pageFetchStatus": "ok", "evidenceExcerpt": "SBI net profit was 100 INR crore."}]
    audit = audit_chart_evidence_consistency("See chart [CHART_1].", [chart], sources)
    kept, removed_indices, pruned_count = prune_unsupported_chart_points([chart], audit)
    assert kept == []
    assert removed_indices == [1]
    assert pruned_count == 1


def test_final_rendered_pdf_audit_reopens_pdf_and_checks_manifest_citations():
    fitz = pytest.importorskip("fitz")
    from routes.pdf import _audit_rendered_pdf_bytes

    report = ("India's banking sector evidence summary: credit growth, deposit growth, "
              "asset quality, profitability and digital payments are reviewed below. "
              "All published statistics must be verified against the original source. [S1]")
    document = fitz.open()
    page = document.new_page(width=595, height=842)
    page.insert_textbox(fitz.Rect(55, 55, 540, 400), report, fontsize=11)
    pdf_bytes = document.tobytes()
    document.close()

    valid = _audit_rendered_pdf_bytes(pdf_bytes, report, "Banking evidence", [], [
        {"id": "S1", "title": "Official banking source", "url": "https://rbi.org.in/data"},
    ])
    assert valid["status"] == "PASS" and valid["pageCount"] == 1

    unresolved_report = report.replace("[S1]", "[S9]")
    invalid = _audit_rendered_pdf_bytes(pdf_bytes, unresolved_report, "Banking evidence", [], [
        {"id": "S1", "title": "Official banking source", "url": "https://rbi.org.in/data"},
    ])
    assert invalid["status"] == "FAIL"
    assert any("do not resolve" in finding["issue"] for finding in invalid["findings"])

    duplicate_report = (
        "Total liabilities/assets for Public Sector Banks were 15,493,234 in 2025 [S1].\n"
        "Total liabilities/assets for Public Sector Banks were 15,493,234 in 2025 [S2].\n"
        "This page contains the source-backed results summary, and all figures are checked against the publisher text."
    )
    duplicate_doc = fitz.open()
    duplicate_page = duplicate_doc.new_page(width=595, height=842)
    duplicate_page.insert_textbox(fitz.Rect(55, 55, 540, 500), duplicate_report, fontsize=11)
    duplicate_pdf_bytes = duplicate_doc.tobytes()
    duplicate_doc.close()
    duplicate_audit = _audit_rendered_pdf_bytes(duplicate_pdf_bytes, duplicate_report, "Banking evidence", [], [
        {"id": "S1", "title": "Official banking source", "url": "https://rbi.org.in/data"},
        {"id": "S2", "title": "Duplicate official banking source", "url": "https://rbi.org.in/data"},
    ])
    assert duplicate_audit["status"] == "FAIL"
    assert any("repeats the same numeric observation" in finding["issue"] for finding in duplicate_audit["findings"])
