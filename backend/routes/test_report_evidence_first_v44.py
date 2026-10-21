from __future__ import annotations

import asyncio

from routes import report as report_mod
from utils.chart_catalog import chart_density_assessment


def test_secondary_quality_gate_does_not_call_another_llm_or_rewrite_sections(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("secondary QA must not call an LLM or section editor")

    monkeypatch.setattr(report_mod, "_call_llm_plain", forbidden)
    monkeypatch.setattr(report_mod, "edit_report_section", forbidden)
    duplicate = (
        "This long paragraph describes the renewable capacity outlook, historical installations,"
        " funding conditions, grid reliability, and investment dynamics. " * 3
    )
    body = "## Outlook\n\n" + duplicate + "\n\n" + duplicate + "\n\n## Risks\n\nRisks are evidence dependent."
    cleaned, charts, result = asyncio.run(report_mod._secondary_quality_gate(
        body, "renewable energy outlook", "brief", "Title", "", [], [], [], {},
    ))
    assert cleaned.count(duplicate) == 1
    assert charts == []
    assert result["review"]["status"] == "delegated_to_independent_audit"
    assert result["repairsApplied"] == ["deterministic_duplicate_paragraph_cleanup"]


def test_numeric_source_evidence_requires_all_claim_numbers_to_match():
    manifest = [{"id": "S1", "title": "Capacity report", "url": "https://example.org/capacity"}]
    source_text = (
        "The installed non-fossil capacity reached 283.46 GW and solar capacity reached 150.26 GW. "
        "The installed non-fossil capacity reached 283.46 GW and solar capacity reached 150.26 GW. "
    )
    enriched = [{"url": "https://www.example.org/capacity/?utm_source=report", "fullContent": source_text}]
    report = "Installed non-fossil capacity reached 283.46 GW, solar capacity reached 150.26 GW, and large hydro reached 56.09 GW [S1]."
    result = report_mod._attach_audit_source_evidence(manifest, enriched, report)[0]
    assert result["evidenceAvailable"] is True
    assert result["numericClaimMatches"] == 2
    assert result["citedNumericClaimCount"] == 3
    assert result["claimEvidenceCoverage"] < 1
    assert result["claimEvidenceMatched"] is False
    assert any(str(value).endswith(":56.09") or str(value) == "56.09" for value in result["unmatchedNumericClaims"])


def test_repair_cannot_borrow_numbers_from_a_different_source_id():
    original = "The capacity was 42 GW according to the second source [S2], which reported this figure in its annual outlook."
    patched = report_mod._apply_exact_repair_patches(original, [{
        "target": original,
        "replacement": "The capacity was 162.4 GW according to the second source [S2], which reported this figure in its annual outlook.",
    }], [{
        "id": "S1", "evidenceExcerpt": "The market value reached USD 162.4 billion in 2025.",
        "claimEvidenceMatched": True,
    }, {
        "id": "S2", "evidenceExcerpt": "The report discusses policy trends and does not state a capacity figure.",
        "claimEvidenceMatched": False,
    }])
    assert patched == original


def test_exact_patch_can_be_kept_without_promoting_remaining_audit_findings_to_pass():
    accepted, reason, meets_target = report_mod._evaluate_exact_patch_candidate(
        original_text="A" * 1000,
        repaired_text="A" * 990 + " corrected",
        original_pages=6, repaired_pages=6, page_minimum=5,
        repair_deterministic={"publicationAudit": {"flags": [], "emptyChartIndices": [], "untitledChartIndices": []}},
    )
    assert accepted is True
    assert reason == "exact_patch_applied_with_residual_audit_status"
    assert meets_target is True


def test_compact_capacity_comparison_is_measured_without_a_density_target():
    assessment = chart_density_assessment({
        "type": "bar",
        "title": "India Non-Fossil Capacity Milestone vs 2030 Target",
        "series": [{"name": "Installed capacity", "data": [
            {"label": "Current capacity (2026)", "value": 283.46},
            {"label": "2030 target", "value": 500.0},
        ]}],
    }, "brief")
    assert assessment["applicable"] is True
    assert assessment["count"] == 2
    assert assessment["minimum"] is None
    assert assessment["within_band"] is None


def test_numeric_arrays_without_real_axis_labels_are_not_given_fake_categories():
    assert report_mod._normalise_inline_chart_spec({
        "chart_type": "bar", "title": "Untitled numeric series", "data": [12, 15, 18],
    }) is None


def test_generic_title_is_not_synthesized_from_series_name():
    raw = report_mod._normalise_inline_chart_spec({
        "chart_type": "line", "series": [{"name": "Growth", "data": [
            {"label": "2024", "value": 10.0}, {"label": "2025", "value": 11.0},
        ]}],
    })
    assert raw is not None
    assert raw["title"] == ""


def test_source_appendix_keeps_canonical_ids_for_url_less_and_same_domain_sources():
    from routes.html_report import _render_sources_appendix

    registry = [
        {"id": "S1", "title": "Renewable capacity report", "publisher": "example.org", "url": "https://example.org/capacity", "domain": "example.org", "source_type": "Web source"},
        {"id": "S2", "title": "Verified data feed", "publisher": "RBI", "url": "", "domain": "", "source_type": "Verified data"},
        {"id": "S3", "title": "Renewable investment report", "publisher": "example.org", "url": "https://example.org/investment", "domain": "example.org", "source_type": "Web source"},
    ]
    html = _render_sources_appendix(
        registry, placement="appendix", include_appendix=True,
        source_registry=registry, report_tier="brief", cited_source_ids={"S1", "S2", "S3"},
    )
    assert "Total sources: 3" in html
    assert "[S1]" in html and "[S2]" in html and "[S3]" in html
    assert "Verified data feed" in html
    assert "No public URL supplied" in html
    assert "https://example.org/capacity" in html
    assert "https://example.org/investment" in html


def test_html_renderer_converts_raw_chart_markdown_to_inline_structured_chart():
    from routes.html_report import build_html_report

    chart = {
        "type": "line", "title": "Capacity growth trajectory", "xLabel": "Year", "yLabel": "GW",
        "series": [{"name": "Installed capacity", "data": [
            {"label": "2024", "value": 100}, {"label": "2025", "value": 120},
            {"label": "2026", "value": 140}, {"label": "2027", "value": 160},
            {"label": "2028", "value": 180},
        ]}],
    }
    html = build_html_report(
        report="## Capacity outlook\n\n![Capacity growth trajectory](CHART_1)",
        title="Capacity report", question="capacity", summary="", key_stats=[], charts=[chart],
        images=[], sources=[], report_tier="brief",
    )
    assert "![Capacity growth trajectory](CHART_1)" not in html
    assert "Capacity growth trajectory" in html
    assert "[CHART_1]" not in html
    assert "<svg" in html or "gg-chart" in html


def test_canonical_url_beats_reused_transient_source_id_when_attaching_evidence():
    manifest = [{"id": "S1", "title": "Renewable capacity report", "url": "https://example.org/capacity"}]
    enriched = [
        {"id": "S1", "title": "Wrong source retained stale id", "url": "https://other.example/wrong", "fullContent": "This unrelated page says that a different market reached 999 billion units in 2024. " * 2},
        {"id": "S2", "title": "Renewable capacity report", "url": "https://www.example.org/capacity/?utm_source=search", "fullContent": "The renewable installed capacity reached 283.46 GW in March 2026 according to the official capacity update. " * 2},
    ]
    result = report_mod._attach_audit_source_evidence(manifest, enriched, "Renewable installed capacity reached 283.46 GW [S1].")[0]
    assert result["evidenceAvailable"] is True
    assert result["claimEvidenceMatched"] is True
    assert "283.46 GW" in result["evidenceExcerpt"]
    assert "999 billion" not in result["evidenceExcerpt"]


def test_grouped_citations_can_support_different_numbers_without_false_per_source_failure():
    manifest = [
        {"id": "S1", "title": "Capacity milestone", "url": "https://example.org/capacity"},
        {"id": "S2", "title": "Annual additions", "url": "https://example.org/additions"},
    ]
    enriched = [
        {"url": "https://example.org/capacity", "pageContent": "Total installed non-fossil capacity reached 283.46 GW by March 2026."},
        {"url": "https://example.org/additions", "pageContent": "Annual non-fossil capacity additions reached 55.3 GW in FY 2025-26."},
    ]
    report = "Capacity reached 283.46 GW, while annual additions reached 55.3 GW [S1], [S2]."
    attached = report_mod._attach_audit_source_evidence(manifest, enriched, report)
    audit = report_mod._source_evidence_quality_audit(attached)
    assert attached[0]["_citedClaimGroups"]
    assert attached[1]["_citedClaimGroups"]
    assert audit["citationGroupCount"] == 1
    assert audit["blocking"] is False
    group = audit["mismatchedCitationGroups"]
    assert group == []


def test_grouped_citation_still_blocks_a_numeric_value_unsupported_by_all_cited_sources():
    manifest = [{"id": "S1", "title": "Capacity milestone", "url": "https://example.org/capacity"}]
    enriched = [{"url": "https://example.org/capacity", "pageContent": "Total installed non-fossil capacity reached 283.46 GW by March 2026."}]
    report = "Capacity reached 283.46 GW and annual additions reached 55.3 GW [S1]."
    attached = report_mod._attach_audit_source_evidence(manifest, enriched, report)
    audit = report_mod._source_evidence_quality_audit(attached)
    assert audit["blocking"] is True
    assert audit["mismatchedCitationGroups"]
