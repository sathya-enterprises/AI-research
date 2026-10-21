from __future__ import annotations

from routes.html_report import build_html_report
from routes.report import (
    _evaluate_exact_patch_candidate,
    _merge_source_evidence_statuses,
    _publisher_evidence_supports_metric,
    _replace_unsupported_financial_claims,
    _sanitize_unverified_financial_claims,
)
from routes.source_manifest import build_source_registry
from utils.source_extractor import _extract_publisher_metadata


def _publisher_source(evidence: str) -> dict:
    return {
        "id": "S1",
        "url": "https://publisher.example/bank-asset-quality",
        "pageFetchStatus": "ok",
        "pageFetchHttpStatus": 200,
        "pageFetchRelevanceMatched": True,
        "evidenceExcerptOrigin": "publisher_page",
        "evidenceOrigin": "publisher_page",
        "publisherEvidenceExcerpt": evidence,
        "evidenceExcerpt": evidence,
    }


def test_range_requires_both_endpoints_together_and_same_metric() -> None:
    source = _publisher_source("Pre-provision operating profit margin was 3.7% in FY2025.")
    supported = _publisher_evidence_supports_metric(
        "3.6%–3.7%", "PPOP margin was 3.6%–3.7% [S1]", ["S1"], [source],
    )
    assert supported is False

    source = _publisher_source(
        "PPOP margin was reported at 3.6%–3.7% in FY2025. NPL formation was 1.7%."
    )
    assert _publisher_evidence_supports_metric(
        "3.6%–3.7%", "PPOP margin was 3.6%–3.7% [S1]", ["S1"], [source],
    ) is True


def test_range_with_wrong_denominator_is_not_supported() -> None:
    source = _publisher_source("NPL formation was 1.7%–1.8% of total loans in FY2025.")
    assert _publisher_evidence_supports_metric(
        "1.7%–1.8%", "NPL formation was 1.7%–1.8% of total deposits [S1]", ["S1"], [source],
    ) is False


def test_unsupported_range_is_redacted_as_one_unit() -> None:
    source = _publisher_source("PPOP margin was 3.7% in FY2025.")
    cleaned, count = _replace_unsupported_financial_claims(
        "PPOP margin was 3.6%–3.7% [S1].", [source],
    )
    assert count == 1
    assert "3.6%" not in cleaned
    assert "3.7%" not in cleaned
    assert "not verified in retrieved publisher-page evidence" in cleaned


def test_markdown_table_rows_with_unverified_values_are_removed() -> None:
    source = _publisher_source("Gross NPA was 2.8% of advances in FY2025.")
    report, charts, count = _sanitize_unverified_financial_claims(
        """## Asset quality

| Metric | Value | Source |
| --- | --- | --- |
| Gross NPA | 2.8% | [S1] |
| NPL formation | 1.7%–1.8% | [S1] |

The report continues with qualitative analysis.""",
        [], [source],
    )
    assert count >= 1
    assert "Gross NPA" in report
    assert "NPL formation" not in report
    assert "| Metric | Value | Source |" in report
    assert charts == []


def test_structured_table_drops_placeholder_metric_rows_and_empty_tables() -> None:
    source = _publisher_source("NIM declined by 21 basis points year over year.")
    report, charts, count = _sanitize_unverified_financial_claims(
        "[CHART_1]", [{
            "type": "table", "title": "NPL and profitability metrics",
            "columns": ["Metric", "Rate", "Source"],
            "rows": [["NPL formation", "—", "[S1]"], ["PPOP margin", "3.6%–3.7%", "[S1]"]],
        }], [source],
    )
    assert count >= 1
    assert charts == []
    assert "[CHART_1]" in report  # the chart-reference reconciliation stage owns its removal


def test_publisher_metadata_extracts_date_title_canonical_url_and_publisher() -> None:
    html = """<!doctype html><html><head>
    <title>India Banking Outlook — Official Research</title>
    <meta property="og:site_name" content="Official Research Bureau">
    <meta property="article:published_time" content="2026-10-12T09:00:00+05:30">
    <link rel="canonical" href="https://publisher.example/research/banking-outlook">
    </head><body><article><h1>India Banking Outlook</h1><p>Credit growth data and asset quality analysis for the banking system.</p></article></body></html>"""
    data = _extract_publisher_metadata(html, "https://publisher.example/redirect")
    assert data["page_title"] == "India Banking Outlook — Official Research"
    assert data["publisher"] == "Official Research Bureau"
    assert data["published_at"] == "2026-10-12T09:00:00+05:30"
    assert data["publication_date_source"] == "meta:article:published_time"
    assert data["canonical_url"] == "https://publisher.example/research/banking-outlook"


def test_source_manifest_keeps_publisher_page_date_and_provenance_metadata() -> None:
    manifest = build_source_registry([{
        "title": "publisher.example",
        "url": "https://publisher.example/banking-outlook",
        "pageFetchStatus": "ok",
        "publisherPageTitle": "India Banking Outlook — Official Research",
        "publisherPageUrl": "https://publisher.example/research/banking-outlook",
        "publisherPagePublishedAt": "2026-10-12",
        "publisherPagePublicationDateSource": "meta:article:published_time",
    }])
    merged = _merge_source_evidence_statuses(manifest, [{
        **manifest[0], "id": "S1", "pageFetchStatus": "ok",
        "evidenceExcerptOrigin": "publisher_page", "evidenceOrigin": "publisher_page",
        "publisherPageTitle": "India Banking Outlook — Official Research",
        "publisherPageUrl": "https://publisher.example/research/banking-outlook",
        "publisherPagePublishedAt": "2026-10-12",
        "publisherPagePublicationDateSource": "meta:article:published_time",
        "publisherEvidenceExcerpt": "Credit growth and asset quality analysis.",
        "evidenceAvailable": True, "claimEvidenceMatched": True,
    }])
    assert merged[0]["publication_date"] == "2026-10-12"
    assert merged[0]["publication_date_source"] == "meta:article:published_time"
    assert merged[0]["publisher_page_title"] == "India Banking Outlook — Official Research"
    assert merged[0]["publisher_page_url"] == "https://publisher.example/research/banking-outlook"


def test_html_source_appendix_shows_publisher_date_without_internal_fetch_diagnostics() -> None:
    html = build_html_report(
        "The banking outlook remains sensitive to deposit repricing [S1].",
        "Banking Outlook", "test", "", [], [], [],
        sources=[{
            "title": "Banking-sector outlook", "url": "https://publisher.example/banking-outlook",
            "publication_date": "2026-10-12", "publication_date_source": "meta:article:published_time",
            "page_fetch_status": "http_403", "page_fetch_http_status": 403,
            "evidence_origin": "search_extract_only", "evidence_available": True,
            "claim_evidence_matched": False, "unmatched_numeric_claims_count": 2,
        }], report_tier="brief",
    )
    assert "Published: 2026-10-12" in html
    assert "publisher.example/banking-outlook" in html
    assert "HTTP 403" not in html
    assert "search-index excerpt only" not in html
    assert "Publisher page could not be fully extracted" not in html


def test_repair_with_blocking_source_evidence_is_rejected() -> None:
    accepted, reason, _ = _evaluate_exact_patch_candidate(
        original_text="A sufficiently long original report with useful evidence. " * 8,
        repaired_text="A sufficiently long repaired report with useful evidence. " * 8,
        original_pages=7, repaired_pages=7, page_minimum=6,
        repair_deterministic={"publicationAudit": {}, "sourceEvidenceAudit": {"blocking": True}},
    )
    assert accepted is False
    assert reason == "source_evidence_blocker_remains"


