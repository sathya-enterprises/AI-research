from utils.chart_evidence_audit import audit_chart_evidence_consistency, sanitize_charts_for_publication
from routes import report
from routes.source_manifest import build_source_registry, source_provenance_note, _safe_http_url


def _calculated_sector_source():
    quarters = ["Q1 2025", "Q2 2025", "Q3 2025", "Q4 2025"]
    values = [1.0, -2.0, 3.0, 4.0]
    excerpt = (
        "Quarter coverage: " + ", ".join(quarters) + "\n"
        "NIFTY AUTO | " + " | ".join(f"{value:+.2f}%" for value in values) + "\n"
        "History source by index: NIFTY AUTO: Yahoo Finance via Growth Gradual market data"
    )
    return {
        "title": "CALCULATED NSE SECTOR RETURNS — EIGHT COMPLETED CALENDAR QUARTERS",
        "url": "internal://verified-sector-quarterly-returns",
        "kind": "Verified data",
        "publisher": "Calculated from daily market data",
        "snippet": excerpt[:300],
        "fullContent": excerpt,
        "chartEvidenceExcerpt": excerpt,
        "evidenceOrigin": "calculated_from_daily_market_data",
        "pageFetchStatus": "not_applicable",
        "evidenceAvailable": True,
    }


def test_calculated_sector_source_keeps_provenance_and_passes_strict_chart_gate():
    source = _calculated_sector_source()
    manifest = build_source_registry([source])
    assert manifest[0]["id"] == "S1"
    assert manifest[0]["url"] == ""

    report_text = "## Quarterly sector returns\n\n[CHART_1]"
    audited = report._attach_audit_source_evidence(manifest, [source], report_text)
    assert audited[0]["evidenceExcerptOrigin"] == "calculated_from_daily_market_data"
    assert audited[0]["pageFetchStatus"] == "not_applicable"
    assert audited[0]["evidenceAvailable"] is True

    public_sources = report._merge_source_evidence_statuses(manifest, audited)
    assert public_sources[0]["evidence_origin"] == "calculated_from_daily_market_data"
    assert public_sources[0]["page_fetch_status"] == "not_applicable"
    assert "derived dataset" in source_provenance_note(public_sources[0])

    rows = [{
        "label": "NIFTY AUTO", "quarters": ["Q1 2025", "Q2 2025", "Q3 2025", "Q4 2025"],
        "returns": [1.0, -2.0, 3.0, 4.0], "missing_quarters": [],
    }]
    chart = report._build_sector_quarterly_return_chart(rows, manifest)
    assert chart is not None and chart["source"] == ["S1"]

    audit = audit_chart_evidence_consistency(
        report_text, [chart], public_sources, require_publisher_verification=True,
    )
    assert audit["chartEvidenceFailures"] == []
    cleaned, kept, meta = sanitize_charts_for_publication(
        report_text, [chart], public_sources, question="sector rotation", require_publisher_verification=True,
    )
    assert len(kept) == 1
    assert meta["remainingFindings"] == []
    assert "[CHART_1]" in cleaned


def test_source_registry_removes_url_credentials_and_redacts_sensitive_query_values():
    url = _safe_http_url(
        "https://user:password@example.org/report?api_key=secret-one&x-goog-api-key=secret-two&year=2026#token=secret-three"
    )
    assert url.startswith("https://example.org/report?")
    assert "api_key=REDACTED" in url
    assert "x-goog-api-key=REDACTED" in url
    assert "year=2026" in url
    for secret in ("user", "password", "secret-one", "secret-two", "secret-three"):
        assert secret not in url
