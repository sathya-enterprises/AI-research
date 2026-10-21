"""Focused v45 regressions for search coverage and end-to-end source provenance."""
from __future__ import annotations

import asyncio
from unittest.mock import patch

from routes.report import (
    _attach_audit_source_evidence,
    _build_multi_angle_search_queries,
    _dedupe_enriched_sources,
    _extract_company_candidates,
    _merge_source_evidence_statuses,
    _page_text_matches_source,
    _publication_audit_is_blocking,
)
from routes.source_manifest import (
    build_source_registry,
    ensure_cited_source_records,
    extract_source_citation_ids,
)
from utils.intent import RequestIntent
from utils.report_quality import audit_report_publication
from utils.source_extractor import _extract_html, fetch_source_document
from utils.source_identity import canonical_http_url
from routes.html_report import build_html_report
from routes.source_manifest import source_provenance_note


def _intent(topic: str, evidence: list[str] | None = None) -> RequestIntent:
    return RequestIntent(
        resolved_topic=topic,
        evidence_needed=evidence or ["historical"],
        source="heuristic",
    )


def test_heuristic_report_search_expands_to_distinct_evidence_angles():
    intent = _intent("India's renewable energy growth outlook", ["historical"])
    queries = asyncio.run(_build_multi_angle_search_queries(
        "Prepare a Brief research report on India's renewable energy growth outlook for 2027–2030",
        "", "general", intent,
    ))
    assert len(queries) >= 4
    lowered = [q.casefold() for q in queries]
    assert any("official government statistics" in q for q in lowered)
    assert any("policy targets" in q for q in lowered)
    assert any("investment outlook" in q for q in lowered)
    assert len(set(lowered)) == len(lowered)

    # If intent resolution completely fails, non-financial reports still need
    # more than one web query instead of silently degrading to a single search.
    no_intent = asyncio.run(_build_multi_angle_search_queries(
        "Prepare a report on India's renewable energy outlook for 2027–2030",
        "", "general", None,
    ))
    assert len(no_intent) >= 4
    assert any("official government statistics" in q.casefold() for q in no_intent)


def test_sector_topic_is_not_misclassified_as_company_but_real_company_suffixes_survive():
    renewable = _intent("India's renewable energy growth outlook")
    assert _extract_company_candidates(
        "Prepare a Brief research report on India's renewable energy growth outlook for 2027–2030",
        renewable,
    ) == []
    banks = _intent("HDFC Bank and ICICI Bank financial performance", ["comparison"])
    assert _extract_company_candidates(
        "Prepare a report comparing HDFC Bank and ICICI Bank financial performance", banks,
    ) == ["HDFC Bank", "ICICI Bank"]
    power = _intent("Compare Tata Power vs NTPC Limited renewable projects", ["comparison"])
    assert _extract_company_candidates(
        "Compare Tata Power vs NTPC Limited renewable projects", power,
    ) == ["Tata Power", "NTPC Limited"]


def test_duplicate_url_aliases_merge_without_losing_publisher_text():
    rows = [
        {"url": "http://example.com/research/report?utm_source=search", "title": "Report", "snippet": "Short search snippet only."},
        {"url": "https://www.example.com/research/report?utm_medium=feed", "title": "Report copy", "pageContent": "Official report states solar capacity reached 150.26 GW in March 2026. Additional source detail is available.", "pageFetchStatus": "ok"},
    ]
    merged = _dedupe_enriched_sources(rows)
    assert len(merged) == 1
    assert "150.26 GW" in merged[0]["pageContent"]
    assert len(merged[0].get("sourceUrlAliases", [])) >= 2


def test_html_extractor_preserves_table_header_and_row_values():
    html = """<html><head><title>Renewable Capacity Data</title></head><body>
    <nav>Navigation links that must not become source evidence</nav>
    <main><h1>Renewable Capacity Data</h1><p>Official capacity statistics released by the energy ministry.</p>
    <table><tr><th>Metric</th><th>March 2026</th></tr>
    <tr><td>Solar installed capacity</td><td>150.26 GW</td></tr>
    <tr><td>Wind installed capacity</td><td>56.09 GW</td></tr>
    <tr><td>Wind</td><td>56.09 GW</td></tr></table></main>
    <footer>Privacy policy and all rights reserved</footer></body></html>"""
    extracted = _extract_html(html, 3000, False)
    assert "Solar installed capacity | 150.26 GW" in extracted
    assert "Wind installed capacity | 56.09 GW" in extracted
    assert "Wind | 56.09 GW" in extracted
    assert "Navigation links" not in extracted


def test_compact_official_numeric_table_is_retained_as_publisher_evidence():
    source = {
        "title": "India Renewable Capacity Statistics",
        "url": "https://official.example/capacity",
    }
    # A short but semantically relevant table extract. It is over 35 characters,
    # contains multiple numeric data points, and anchors to the source/topic.
    compact = "India capacity | Solar 150.26 GW | Total non-fossil 283.46 GW"
    assert _page_text_matches_source(
        compact, source, "India renewable energy capacity", _intent("India renewable energy capacity"),
    ) is True


def test_direct_fetch_reports_http_forbidden_separately_from_missing_source():
    class Response:
        status_code = 403
        url = "https://publisher.example/data"
        headers = {"content-type": "text/html"}
        content = b""
        is_success = False

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc, tb):
            return False
        async def get(self, *args, **kwargs):
            return Response()

    with patch("utils.source_extractor.httpx.AsyncClient", FakeClient):
        result = asyncio.run(fetch_source_document("https://publisher.example/data"))
    assert result["status"] == "http_403"
    assert result["http_status"] == 403
    assert result["text"] == ""
    # A blocked publisher is a known fetch failure, not proof the URL does not exist.


def test_cited_source_evidence_is_claim_aligned_per_source_id():
    manifest = build_source_registry([
        {"title": "Official solar capacity statistics", "url": "https://official.example/solar"},
        {"title": "Official wind capacity statistics", "url": "https://official.example/wind"},
    ])
    enriched = [
        {"title": "Official solar capacity statistics", "url": "https://official.example/solar", "pageContent": "Solar installed capacity reached 150.26 GW in March 2026 according to official capacity statistics.", "pageFetchStatus": "ok"},
        {"title": "Official wind capacity statistics", "url": "https://official.example/wind", "pageContent": "Wind installed capacity reached 56.09 GW in March 2026 according to official capacity statistics.", "pageFetchStatus": "ok"},
    ]
    report = "Solar installed capacity reached 150.26 GW in March 2026 [S1].\nWind installed capacity reached 56.09 GW in March 2026 [S2]."
    audited_sources = _attach_audit_source_evidence(manifest, enriched, report)
    by_id = {item["id"]: item for item in audited_sources}
    assert by_id["S1"]["claimEvidenceMatched"] is True
    assert by_id["S2"]["claimEvidenceMatched"] is True
    assert "56.09" not in by_id["S1"]["evidenceExcerpt"]
    assert "150.26" not in by_id["S2"]["evidenceExcerpt"]


def test_missing_citation_id_is_preserved_as_explicit_unresolved_record_and_blocks_pass():
    report = "The capacity target is 500 GW [S1]. Another unsupported claim is attributed to [S9]."
    manifest = build_source_registry([
        {"title": "Official capacity target", "url": "https://official.example/target"},
    ])
    assert extract_source_citation_ids(report) == ["S1", "S9"]
    complete_manifest = ensure_cited_source_records(report, manifest)
    missing = [item for item in complete_manifest if item["id"] == "S9"]
    assert len(missing) == 1
    assert missing[0]["source_status"] == "unresolved"
    audit = audit_report_publication(report, [], [], complete_manifest)
    assert "S9" in audit["missingSourceRecords"]
    assert audit["status"] != "clean"
    assert _publication_audit_is_blocking(audit)


def test_stale_unresolved_placeholder_is_removed_when_repair_removes_citation():
    manifest = ensure_cited_source_records("Claim [S9].", build_source_registry([{"title": "Official source", "url": "https://official.example/source"}]))
    assert any(item["id"] == "S9" and item["source_status"] == "unresolved" for item in manifest)
    repaired = ensure_cited_source_records("Rewritten sentence without unsupported citation.", manifest)
    assert not any(item["id"] == "S9" and item.get("source_status") == "unresolved" for item in repaired)


def test_html_appendix_keeps_missing_citation_visible_when_extra_attribution_is_added():
    html = build_html_report(
        "Installed capacity was 500 GW [S9].\n\nSource: Energy Ministry",
        "Test report", "test", "", [], [], [],
        sources=[{"title": "Official target", "url": "https://example.gov/target"}],
        report_tier="brief",
    )
    assert "Source record missing" in html
    assert "S9" in html
    assert "Energy Ministry" in html


def test_grouped_citations_are_parsed_as_ids_not_silently_lost():
    assert extract_source_citation_ids("Capacity is 283 GW [S1, S7]; outlook is positive (S9).") == ["S1", "S7", "S9"]


def test_source_provenance_note_explains_http_403_without_claiming_url_is_missing():
    note = source_provenance_note({
        "url": "https://publisher.example/report",
        "page_fetch_status": "http_403",
        "page_fetch_http_status": 403,
        "evidence_origin": "search_extract_only",
        "evidence_available": True,
        "claim_evidence_matched": False,
        "unmatched_numeric_claims_count": 2,
    })
    assert "HTTP 403" in note
    assert "search-index excerpt only" in note
    assert "not found" not in note.casefold()


def test_audit_evidence_diagnostics_are_carried_into_the_returned_source_registry():
    manifest = build_source_registry([{"title": "Publisher report", "url": "https://publisher.example/report"}])
    audited_sources = [{
        **manifest[0],
        "evidenceExcerptOrigin": "search_extract_only",
        "pageFetchStatus": "http_403",
        "pageFetchHttpStatus": 403,
        "evidenceAvailable": True,
        "claimEvidenceMatched": False,
        "unmatchedNumericClaims": ["capacity_gw:500", "capacity_gw:300"],
    }]
    enriched_manifest = _merge_source_evidence_statuses(manifest, audited_sources)
    assert enriched_manifest[0]["page_fetch_status"] == "http_403"
    assert enriched_manifest[0]["page_fetch_http_status"] == 403
    assert enriched_manifest[0]["evidence_available"] is True
    assert enriched_manifest[0]["claim_evidence_matched"] is False
    assert enriched_manifest[0]["unmatched_numeric_claims_count"] == 2


def test_html_appendix_keeps_source_link_but_omits_verbose_fetch_diagnostics():
    html = build_html_report(
        "Market capacity is forecast to reach 500 GW [S1].",
        "Test report", "test", "", [], [], [],
        sources=[{
            "title": "Publisher market report",
            "url": "https://publisher.example/market",
            "pageFetchStatus": "http_403",
            "pageFetchHttpStatus": 403,
            "evidenceOrigin": "search_extract_only",
            "evidenceAvailable": True,
            "claimEvidenceMatched": False,
            "unmatchedNumericClaims": ["capacity_gw:500"],
        }],
        report_tier="brief",
    )
    assert "publisher.example/market" in html
    assert "HTTP 403" not in html
    assert "search-index excerpt only" not in html
    assert "Publisher page could not be fully extracted" not in html



def test_numeric_evidence_match_inherits_table_units_and_ignores_fiscal_year_suffix():
    manifest = build_source_registry([{
        "title": "Official Renewable Capacity Pipeline",
        "url": "https://official.example/capacity-pipeline",
    }])
    enriched = [{
        "title": "Official Renewable Capacity Pipeline",
        "url": "https://official.example/capacity-pipeline",
        "pageFetchStatus": "ok",
        "pageContent": (
            "Official renewable capacity pipeline. Annual non-fossil additions were 55.3 GW in FY 2025–26.\n"
            "| Sector | Installed Capacity (GW) | Under Implementation (GW) | Total Pipeline (GW) |\n"
            "| --- | --- | --- | --- |\n"
            "| Bio Energy | 11.61 | — | 11.61 |\n"
            "| Small Hydro | 5.16 | 0.44 | 5.60 |\n"
            "| Large Hydro | 50.35 | 25.33 | 75.68 |"
        ),
    }]
    report = (
        "Bio energy installed capacity was 11.61 GW [S1].\n"
        "Small hydro installed capacity was 5.16 GW with total pipeline 5.60 GW [S1].\n"
        "Large hydro installed capacity was 50.35 GW and total pipeline 75.68 GW [S1].\n"
        "Annual non-fossil additions were 55.3 GW in FY 2025–26 [S1]."
    )
    audited = _attach_audit_source_evidence(manifest, enriched, report)
    assert audited[0]["claimEvidenceMatched"] is True
    assert audited[0]["unmatchedNumericClaims"] == []


def test_source_evidence_audit_blocks_unmatched_cited_numeric_claims():
    from routes.report import _source_evidence_quality_audit, _apply_deterministic_audit_gate

    result = _source_evidence_quality_audit([{
        "id": "S8", "title": "Policy page", "citedNumericClaimCount": 2,
        "numericClaimMatches": 0, "claimEvidenceMatched": False,
        "unmatchedNumericClaims": ["percent:5", "percent:12"],
        "evidenceExcerptOrigin": "search_extract_only",
    }])
    assert result["blocking"] is True
    gated = _apply_deterministic_audit_gate(
        {"status": "PASS", "blocking": False, "findings": []},
        {"publicationAudit": {"flags": []}, "sourceEvidenceAudit": result, "chartDensity": {"exceptionsOrMisses": []}},
    )
    assert gated["status"] == "FAIL"
    assert gated["blocking"] is True
    from routes.report import _is_text_repairable_audit_finding
    assert any(_is_text_repairable_audit_finding(item) for item in gated["findings"])



def test_rejected_http_200_page_status_is_explained_not_mislabeled_as_fetch_failure():
    note = source_provenance_note({
        "page_fetch_status": "rejected_by_relevance_gate",
        "page_fetch_http_status": 200,
        "page_fetch_candidate_chars": 1840,
        "evidence_origin": "search_extract_only",
    })
    assert "returned page text" in note
    assert "1840 characters" in note
    assert "search-index excerpt" in note



def test_pib_press_release_page_and_iframe_urls_share_identity_by_prid():
    from utils.source_identity import source_identity_key

    page = "https://www.pib.gov.in/PressReleasePage.aspx?PRID=2250039&reg=3&lang=1"
    iframe = "https://www.pib.gov.in/PressReleaseIframePage.aspx?PRID=2250039&reg=48&lang=2"
    other = "https://www.pib.gov.in/PressReleasePage.aspx?PRID=2209478&reg=3&lang=1"
    assert source_identity_key(page) == source_identity_key(iframe)
    assert source_identity_key(page) != source_identity_key(other)


def test_banking_sector_retrieval_uses_separate_official_metric_queries():
    queries = asyncio.run(_build_multi_angle_search_queries(
        "How is India's banking sector likely to perform over the next three years? Analyze credit and deposit growth, GNPA/NNPA, profitability and NIM, RBI policy rates, and UPI adoption.",
        "", "finance", _intent("India banking sector outlook"),
    ))
    joined = "\n".join(queries).casefold()
    assert len(queries) >= 6
    for family in ("bank credit growth", "deposit growth", "gross npa", "net interest margin", "repo rate", "upi product statistics", "quarterly results"):
        assert family in joined
    assert any("site:rbi.org.in" in query.casefold() for query in queries)
    assert any("site:npci.org.in" in query.casefold() for query in queries)
    assert len({query.casefold() for query in queries}) == len(queries)


def test_publication_document_aliases_merge_but_distinct_annual_editions_survive():
    rows = [
        {"url": "https://rbi.org.in/Scripts/ReportMain.aspx?year=2025", "title": "Report on Trend and Progress of Banking in India 2024-25", "publisher": "RBI", "publication_date": "2025-12-10", "snippet": "Overview"},
        {"url": "https://rbi.org.in/Scripts/PDF/TrendProgress2024-25.pdf?utm_source=search", "title": "Report on Trend and Progress of Banking in India 2024-25 (PDF)", "publisher": "RBI", "publication_date": "2025-12-10", "pageContent": "Official annual banking publication " + ("bank credit and deposits " * 80), "pageFetchStatus": "ok"},
        {"url": "https://rbi.org.in/Scripts/ReportMain.aspx?year=2024", "title": "Report on Trend and Progress of Banking in India 2023-24", "publisher": "RBI", "publication_date": "2024-12-10", "snippet": "Prior year edition"},
    ]
    merged = _dedupe_enriched_sources(rows)
    assert len(merged) == 2
    assert any("2024-25" in str(row.get("title")) and "bank credit" in str(row.get("pageContent")) for row in merged)
    assert any("2023-24" in str(row.get("title")) for row in merged)


def test_canonical_urls_drop_tracking_but_keep_meaningful_query_identifiers():
    left = canonical_http_url("https://www.example.gov/statistics?id=44&utm_source=newsletter&fbclid=abc#chart")
    right = canonical_http_url("http://example.gov/statistics?id=44")
    assert left == right
    assert canonical_http_url("https://example.gov/article?ref=A") != canonical_http_url("https://example.gov/article?ref=B")
    assert canonical_http_url("https://example.gov/article?source=RBI") != canonical_http_url("https://example.gov/article?source=NPCI")
