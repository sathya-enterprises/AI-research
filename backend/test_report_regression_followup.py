"""Regression tests for the Oct-14 publication-gate follow-up fixes."""
from __future__ import annotations

import re

from routes.report import _attach_audit_source_evidence, _source_evidence_quality_audit, _publication_audit_is_blocking
from routes.html_report import _render_chart_block_inner
from utils.chart_catalog import chart_density_assessment
from utils.report_quality import audit_report_publication
from utils.report_scanability import strip_generation_markers


def _paired_sources(first_content: str, second_content: str):
    manifest = [
        {"id": "S1", "title": "Source One", "url": "https://example.test/source-one"},
        {"id": "S2", "title": "Source Two", "url": "https://example.test/source-two"},
    ]
    enriched = [
        {"id": "S1", "title": "Source One", "url": "https://example.test/source-one", "fullContent": first_content},
        {"id": "S2", "title": "Source Two", "url": "https://example.test/source-two", "fullContent": second_content},
    ]
    report = "In July 2025, renewables met 51.5% of electricity demand of 203 GW [S1], [S2]."
    return manifest, enriched, report


def test_adjacent_citations_can_collectively_support_distinct_numbers():
    manifest, enriched, report = _paired_sources(
        "Renewables met a record 51.5% share in July 2025, according to the reported monthly electricity statistics.",
        "The country's electricity demand reached 203 GW in that same reporting period, according to the monthly system data.",
    )
    audited = _attach_audit_source_evidence(manifest, enriched, report)
    evidence_audit = _source_evidence_quality_audit(audited)
    assert not evidence_audit["blocking"]
    group = next(iter({g["groupId"]: g for s in audited for g in s.get("_citedClaimGroups", [])}.values()))
    assert group["groupEvidenceMatched"] is True
    assert group["supportByToken"]
    assert set(group["supportingSourceIds"]) == {"S1", "S2"}


def test_a_number_unsupported_by_every_cited_source_still_blocks():
    manifest, enriched, report = _paired_sources(
        "The source describes renewable energy policy and clean generation, without reporting a numerical percentage.",
        "The source discusses electricity demand and renewable deployment, without specifying a demand quantity.",
    )
    audited = _attach_audit_source_evidence(manifest, enriched, report)
    evidence_audit = _source_evidence_quality_audit(audited)
    assert evidence_audit["blocking"]
    assert evidence_audit["mismatchedCitationGroups"]
    assert _publication_audit_is_blocking({"controlMarkerCount": 1})


def test_risk_matrix_marker_is_removed_from_reader_text_and_raw_marker_is_blocking():
    text = "## Key Risks\nThree risks are material.\n\n[RISK_MATRIX]\n\nMitigation depends on transmission investment."
    cleaned = strip_generation_markers(text)
    assert "[RISK_MATRIX]" not in cleaned
    audit = audit_report_publication(text, charts=[], images=[], sources=[])
    assert audit["controlMarkerCount"] == 1
    assert _publication_audit_is_blocking(audit)


def test_sector_bar_uses_zero_baseline_and_keeps_all_five_categories():
    chart = {
        "type": "bar",
        "title": "Renewable Capacity Breakdown by Sector (March 2026)",
        "unit": "GW",
        "series": [{"name": "Capacity", "data": [
            {"label": "Solar Power", "value": 150.26},
            {"label": "Wind Power", "value": 56.09},
            {"label": "Large Hydro", "value": 51.41},
            {"label": "Bio Energy", "value": 11.75},
            {"label": "Small Hydro", "value": 5.17},
        ]}],
    }
    rendered = _render_chart_block_inner(chart, 1, {})
    assert '"beginAtZero": true' in rendered
    for label in ("Solar Power", "Wind Power", "Large Hydro", "Bio Energy", "Small Hydro"):
        assert label in rendered
    for value in ("150.26", "56.09", "51.41", "11.75", "5.17"):
        assert value in rendered


def test_short_but_legitimate_capacity_series_is_not_treated_as_density_failure():
    milestones = {
        "type": "line",
        "title": "Non-Fossil Fuel Installed Capacity Milestones",
        "series": [{"name": "Capacity", "data": [
            {"label": "June 2025", "value": 203.6},
            {"label": "March 2026", "value": 283.46},
            {"label": "August 2026", "value": 308.0},
            {"label": "2030 Target", "value": 500.0},
        ]}],
    }
    additions = {
        "type": "bar",
        "title": "Annual Non-Fossil Capacity Additions",
        "series": [{"name": "Capacity added", "data": [
            {"label": "2024-25", "value": 28.72},
            {"label": "2025-26", "value": 55.3},
        ]}],
    }
    for chart in (milestones, additions):
        assessment = chart_density_assessment(chart, "brief")
        assert assessment["applicable"] is True
        assert assessment["minimum"] is None
        assert assessment["within_band"] is None
