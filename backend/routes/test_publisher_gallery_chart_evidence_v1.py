from utils.chart_evidence_audit import audit_chart_evidence_consistency
from routes import report as report_mod


def _line_chart():
    quote = "| Scheduled commercial banks | FY2024 | 12.4% |"
    return {
        "type": "line",
        "title": "Scheduled commercial bank credit growth",
        "xLabel": "Fiscal year",
        "yLabel": "Credit growth (%)",
        "unit": "%",
        "source": ["S1"],
        "series": [{
            "name": "Credit growth",
            "data": [{
                "label": "FY2024", "value": 12.4,
                "evidenceSource": "S1", "evidenceQuote": quote,
                "evidenceValue": "12.4",
            }],
        }],
    }


def test_verified_gallery_excerpt_can_pass_strict_chart_audit_for_fetched_publisher_page():
    quote = "| Scheduled commercial banks | FY2024 | 12.4% |"
    source = {
        "id": "S1", "url": "https://rbi.org.in/example",
        "page_fetch_status": "ok",
        "evidence_origin": "publisher_page_and_search_extract",
        "publisher_gallery_chart_evidence_verified": True,
        "chartEvidenceExcerpt": quote,
    }
    audit = audit_chart_evidence_consistency(
        "Credit growth was 12.4% in FY2024 [S1].", [_line_chart()], [source],
        require_publisher_verification=True,
    )
    assert audit["chartEvidenceFailures"] == []


def test_gallery_excerpt_does_not_bypass_strict_gate_without_publisher_verification_marker():
    source = {
        "id": "S1", "url": "https://rbi.org.in/example",
        "page_fetch_status": "ok",
        "evidence_origin": "publisher_page_and_search_extract",
        "chartEvidenceExcerpt": "| Scheduled commercial banks | FY2024 | 12.4% |",
    }
    audit = audit_chart_evidence_consistency(
        "Credit growth was 12.4% in FY2024 [S1].", [_line_chart()], [source],
        require_publisher_verification=True,
    )
    assert audit["chartEvidenceFailures"]


def test_gallery_source_text_does_not_mix_search_extract_into_fetched_publisher_page():
    manifest = [{
        "id": "S1", "title": "Banking statistics", "url": "https://rbi.org.in/example",
    }]
    fetched_page = "| Scheduled commercial banks | FY2024 | 12.4% |"
    search_only_text = "Search-only hallucination test sentinel with credit growth 88.8%."
    enriched = [{
        "id": "S1", "title": "Banking statistics", "url": "https://rbi.org.in/example",
        "pageContent": fetched_page, "fullContent": search_only_text,
        "pageFetchStatus": "ok", "pageFetchRelevanceMatched": True,
        "evidenceOrigin": "publisher_page",
    }]

    sources = report_mod._gallery_source_texts(manifest, enriched)
    assert len(sources) == 2
    publisher = next(row for row in sources if row["origin"] == "publisher_page")
    search = next(row for row in sources if row["origin"] == "search_extract")
    assert publisher["publisherPageVerified"] is True
    assert publisher["text"] == fetched_page
    assert search_only_text not in publisher["text"]
    assert search["publisherPageVerified"] is True  # fetch status is metadata, not its evidence origin
    assert search["text"] == search_only_text


def test_search_extract_only_gallery_source_is_not_marked_publisher_verified():
    manifest = [{
        "id": "S1", "title": "Banking statistics", "url": "https://rbi.org.in/example",
    }]
    search_only_text = "Search extract: credit growth 12.4% in FY2024."
    enriched = [{
        "id": "S1", "title": "Banking statistics", "url": "https://rbi.org.in/example",
        "fullContent": search_only_text,
        "pageFetchStatus": "blocked", "evidenceOrigin": "search_extract",
    }]

    sources = report_mod._gallery_source_texts(manifest, enriched)
    assert len(sources) == 1
    assert sources[0]["origin"] == "search_extract"
    assert sources[0]["publisherPageVerified"] is False
    assert sources[0]["text"] == search_only_text


def test_merge_source_evidence_statuses_marks_only_current_publisher_gallery_quote():
    quote = "| Scheduled commercial banks | FY2024 | 12.4% |"
    report_mod._PUBLISHER_GALLERY_CHART_EVIDENCE_IDS.set({"S1"})
    report_mod._PUBLISHER_GALLERY_CHART_EVIDENCE_LINES.set({"S1": [quote]})
    report_mod._CHART_EVIDENCE_LINES.set({"S1": [quote]})
    manifest = [{"id": "S1", "title": "Banking statistics", "url": "https://rbi.org.in/example"}]
    audited = [{
        "id": "S1", "url": "https://rbi.org.in/example",
        "pageFetchStatus": "ok", "evidenceExcerptOrigin": "publisher_page_and_search_extract",
        "publisherChartEvidenceExcerpt": quote,
    }]
    merged = report_mod._merge_source_evidence_statuses(manifest, audited)
    assert merged[0]["publisher_gallery_chart_evidence_verified"] is True
    assert quote in merged[0]["publisherChartEvidenceExcerpt"]
    assert "search_extract_chart_evidence_verified" not in merged[0]


def test_chart_row_quote_matches_publisher_text_when_only_outer_markdown_pipes_differ():
    quote = "| 3.1. Demand Deposits | 7,48,951 | 8,00,416 | 8,85,492 |"
    publisher = "3.1. Demand Deposits | 7,48,951 | 8,00,416 | 8,85,492"
    assert report_mod._chart_excerpt_matches_source_text(quote, publisher)
    assert not report_mod._chart_excerpt_matches_source_text(quote.replace("8,00,416", "8,00,417"), publisher)

