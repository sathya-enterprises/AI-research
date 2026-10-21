"""Regression tests for quantitative-vs-narrative report intent and promoter visuals."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, Path(__file__).resolve().parents[1].as_posix())

import routes.report as report
from utils.chart_evidence_audit import audit_chart_evidence_consistency
from utils.chart_gallery import build_chart_gallery, gallery_types_for, gallery_markdown
from utils.evidence_dataset import build_evidence_dataset

PROMOTER_QUESTION = (
    "Analyze major Indian listed-company promoter stake sales, share pledging reductions, and promoter exits "
    "over the past eight completed quarters. Identify companies, transaction dates, shares sold, transaction values, "
    "and ownership changes where verifiable. Distinguish official NSE/BSE disclosures and bulk/block deals from media "
    "reports, compare activity across sectors and over time. Include a sourced transaction table, sector-wise comparisons, "
    "and meaningful charts of disclosed activity. Clearly disclose incomplete coverage and unsupported data."
)
NEWS_QUESTION = "Summarize the latest news about RBI policy announcements and explain what happened."
NEWS_NUMERIC_QUESTION = "Summarize the latest RBI policy news and compare the announced rate changes in a sourced table."

PUBLISHER_SOURCES = [
    {
        "id": "S1", "origin": "publisher_page", "published": "2026-10-06T10:00:00Z",
        "url": "https://example.com/pcjeweller", "title": "Sanyasi Ayurveda sells 0.53% stake in PC Jeweller",
        "text": "# Sanyasi Ayurveda sells 0.53% stake in PC Jeweller\nPC Jeweller promoter-linked seller Sanyasi Ayurveda sold 0.53% stake in the company on October 5, 2026, for ₹25 crore."
    },
    {
        "id": "S2", "origin": "publisher_page", "published": "2026-10-06T11:00:00Z",
        "url": "https://example.com/tirupatifoam", "title": "Tirupati Foam promoter Kalpesh Kothari sells 7,712 shares",
        "text": "# Tirupati Foam promoter Kalpesh Kothari sells 7,712 shares\nTirupati Foam promoter Kalpesh Babulal Kothari sold 7,712 equity shares on October 5, 2026, reducing his holding from 0.55% to 0.38%."
    },
    {
        "id": "S3", "origin": "publisher_page", "published": "2026-10-08T08:00:00Z",
        "url": "https://example.com/granules", "title": "Granules India Co-Promoter Sells 1.72 Crore Shares",
        "text": "# Granules India Co-Promoter Sells 1.72 Crore Shares\nGranules India co-promoter sold 1.72 crore shares worth ₹1,509 crore on October 7, 2026."
    },
    {
        "id": "S4", "origin": "publisher_page", "published": "2026-10-09T08:00:00Z",
        "url": "https://example.com/vedanta", "title": "Promoter sells 1.2% stake in Vedanta",
        "text": "# Promoter sells 1.2% stake in Vedanta\nVedanta promoter group sold 1.2% stake for ₹225 crore on October 8, 2026."
    },
]


def test_tier_quota_only_applies_to_quantitative_requests():
    assert report._requires_quantitative_coverage(PROMOTER_QUESTION) is True
    assert report._requires_quantitative_coverage(NEWS_QUESTION) is False
    assert report._is_narrative_news_request(NEWS_QUESTION) is True
    assert report._requires_quantitative_coverage(NEWS_NUMERIC_QUESTION) is True
    assert report._is_narrative_news_request(NEWS_NUMERIC_QUESTION) is False


def test_nse_bse_disclosures_do_not_inject_unrelated_market_indices():
    assert report._should_fetch_index_data(PROMOTER_QUESTION, ["historical"]) is False
    assert report._should_fetch_index_data("Analyze sector rotation across NSE sector indices quarter by quarter.", ["historical"]) is True
    assert report._should_fetch_index_data("Compare market returns over the last four quarters.", ["market_data"]) is True


def test_qualitative_news_may_use_optional_image_without_forcing_charts():
    guidance = report._build_image_candidates_block([], NEWS_QUESTION)
    assert "optional" in guidance.casefold()
    assert "no numeric data-point quota" in guidance.casefold()
    assert "return \"images\": []" in guidance
    quantitative = report._build_image_candidates_block([], PROMOTER_QUESTION)
    assert 'return "images": []' in quantitative


def test_publisher_transaction_lines_create_auditable_charts_not_index_charts():
    ds = build_evidence_dataset(PUBLISHER_SOURCES, PROMOTER_QUESTION)
    assert len(ds.observations) >= 7, f"expected transaction facts, got {len(ds.observations)}"
    metrics = {obs.metric for obs in ds.observations}
    assert {"shares_sold", "transaction_value", "stake_sold_pct"} & metrics
    result = build_chart_gallery(ds, PROMOTER_QUESTION, types=gallery_types_for(PROMOTER_QUESTION, "detailed"), report_tier="detailed")
    assert len(result.charts) >= 2, f"expected relevant transaction charts, coverage={result.coverage}"
    titles = " | ".join(str(item.chart.get("title") or "") for item in result.charts).casefold()
    assert "transaction" in titles or "shares sold" in titles or "stake sold" in titles
    assert "nifty" not in titles and "sensex" not in titles

    body, charts = gallery_markdown(result, 1)
    source_manifest = [
        {"id": item["id"], "url": item["url"], "pageContent": item["text"], "chartEvidenceExcerpt": "\n".join(result.evidence_lines.get(item["id"], []))}
        for item in PUBLISHER_SOURCES
    ]
    audit = audit_chart_evidence_consistency(body, json.loads(json.dumps(charts)), json.loads(json.dumps(source_manifest)))
    assert audit.get("chartEvidenceFailures", []) == [], audit


def test_search_extract_transaction_facts_can_seed_charts_without_being_mislabeled_as_fetched_pages():
    from utils.evidence_dataset import build_evidence_dataset
    long_article_extract = (
        "# Transformers and Rectifiers India Promoter Sells 1.8 Per cent Stake Worth Rs 211 Crore\n"
        "A promoter of Transformers and Rectifiers (India) on Monday sold a 1.8 per cent stake in the company. "
        "According to the block deal data on the National Stock Exchange (NSE), Jitendra Mamotra, one of the promoters, "
        "sold 27 lakh shares, amounting to a 1.8 per cent stake. The shares were offloaded at an average price of "
        "Rs 780.30 apiece, taking the transaction value to Rs 210.68 crore on October 15, 2024. "
        "This search extract is retained with lower-grade provenance because direct publisher fetching may fail."
    )
    ds = build_evidence_dataset([{
        "id": "S1", "origin": "search_extract", "published": "2024-10-15T05:28:42Z",
        "url": "https://example.com/transaction", "title": "Transformers and Rectifiers India Promoter Sells 1.8 Per cent Stake Worth Rs 211 Crore",
        "text": long_article_extract,
    }], PROMOTER_QUESTION)
    assert len(ds.observations) >= 2, [o.to_dict() for o in ds.observations]
    assert {o.evidence_origin for o in ds.observations} == {"search_extract"}
