"""Regression checks for topic-aware scraped evidence and chart recovery."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, Path(__file__).resolve().parents[1])

from utils.evidence_dataset import build_evidence_dataset
from utils.chart_gallery import gallery_types_for
from utils.chart_evidence_audit import _chart_is_relevant
from utils.report_quality import audit_report_publication, reconcile_orphaned_chart_narration
from routes.report import (
    _filter_gallery_dataset_for_question,
    _gallery_observation_origin_is_chart_verifiable,
)


EXCHANGE_TABLE = """| Enter Security Name / Code / ID / ISIN |
| --- |
| ISIN | Name of the Target Company | Name(s) of the acquirer/seller | Number of Equity Shares acquired/disposed | % of Equity Shares acquired/disposed | % of Equity shares held at the end of the day | Date |
| --- | --- | --- | --- | --- | --- | --- |
| INE04Z101016 | ASIAN WAREHOUSING LIMITED EQ | BHAVIK RASHMI BHIMJYANI | 165832 | 4.76 | 9.70 | 27 Feb 2026 |
| INE925R01014 | CMS INFO SYSTEMS LIMITED EQ | PARAG PARIKH FLEXI CAP FUND | 1600000 | 0.97 | 5.93 | 27 Feb 2026 |
| INE299U01018 | CROMPTON GREAVES CONSUMER ELECTRICALS LIMITED EQ | MIRAE ASSET FUND | 500000 | 0.08 | 9.04 | 27 Feb 2026 |
| INE906Y01028 | GALACTICO CORPORATE SERVICES LIMITED EQ | CHARUSHILA VIPUL LATHI | 32285715 | 17.62 | 45.47 | 27 Feb 2026 |
| INE481N01025 | HOME FIRST FINANCE COMPANY INDIA LIMITED EQ | TRUE NORTH FUND V LLP | 2808585 | 2.69 | 4.73 | 27 Feb 2026 |
"""


def test_exchange_table_extracts_issuer_not_isin_and_keeps_both_numeric_fields():
    ds = build_evidence_dataset(
        [{"id": "S1", "title": "Exchange disclosure", "text": EXCHANGE_TABLE, "origin": "search_extract"}],
        "Analyze promoter stake sales and pledge reductions",
    )
    rows = [o for o in ds.observations if o.entity.startswith("ASIAN WAREHOUSING")]
    # The extractor normalizes the table's "Shares transacted" source header
    # into the chart-facing shares_sold metric.
    assert any(o.metric == "shares_sold" and o.metric_label == "Shares sold" and o.value == 165832 for o in rows)
    assert any(o.metric == "stake_sold_pct" and o.metric_label == "Stake sold (%)" and o.value == 4.76 for o in rows)
    assert all(not o.entity.startswith("INE") for o in ds.observations)


def test_promoter_chart_types_are_relevant_not_full_catalog_by_default():
    types = gallery_types_for("Analyze promoter stake sales and pledging reductions", "detailed")
    assert {"bar", "horizontal_bar", "timeline", "heatmap"} <= set(types)
    assert not {"violin", "density", "ridgeline", "funnel", "network", "venn"} & set(types)


def test_promoter_dataset_filters_explicit_non_promoter_rows_and_market_cap():
    ds = build_evidence_dataset(
        [{"id": "S1", "title": "Exchange disclosure", "text": EXCHANGE_TABLE, "origin": "search_extract"}],
        "Analyze promoter stake sales and pledge reductions",
    )
    ds.observations.extend([
        type(ds.observations[0])(
            entity="Example Ltd", entity_key="example", metric="market_cap",
            metric_label="Market capitalisation", value=100, unit="₹ crore", period="",
            period_sort=0, period_kind="", source_id="S1",
            quote="Example Ltd | Market capitalisation | ₹100 crore", raw_value="100",
            origin="table", evidence_origin="search_extract",
        ),
        type(ds.observations[0])(
            entity="Other Ltd", entity_key="other", metric="shares_transacted",
            metric_label="Shares transacted", value=1000, unit="count", period="",
            period_sort=0, period_kind="", source_id="S1",
            quote="Other Ltd | Non-Promoter | Shares transacted | 1000", raw_value="1000",
            origin="table", evidence_origin="search_extract",
        ),
        type(ds.observations[0])(
            entity="Third Ltd", entity_key="third", metric="shares_transacted",
            metric_label="Shares transacted", value=2000, unit="count", period="",
            period_sort=0, period_kind="", source_id="S1",
            quote="Third Ltd | Shares transacted | 2000", raw_value="2000",
            origin="table", evidence_origin="search_extract",
        ),
    ])
    filtered = _filter_gallery_dataset_for_question(ds, "Analyze promoter stake sales and pledge reductions")
    assert all("Market capitalisation" != o.metric_label for o in filtered.observations)
    assert all("Non-Promoter" not in o.quote for o in filtered.observations)
    assert any(o.metric_label == "Shares transacted" for o in filtered.observations)


def test_tier_table_surfaces_the_right_number_of_unique_observations():
    from routes.report import _insert_source_observation_table

    ds = build_evidence_dataset(
        [{"id": "S1", "title": "Exchange disclosure", "text": EXCHANGE_TABLE, "origin": "search_extract"}],
        "Analyze promoter stake sales and pledge reductions",
    )
    text, count = _insert_source_observation_table(
        "# Scope and Objectives\\n\\nIntro.\\n\\n# Transaction Database\\n\\nMore text.",
        ds, "Analyze promoter stake sales", "detailed",
    )
    assert count == 15
    assert "Source-Extracted Promoter Transaction Data" in text
    assert "| Entity / subject | Measure | Reported value | Period | Evidence type | Source |" in text


def test_promoter_chart_filter_rejects_institutional_concentration_and_keeps_stake_sales():
    question = "Explain promoter stake sales and exits in Indian companies."
    report = "## Promoter exits\n\n[CHART_1]\n\n[CHART_2]"
    unrelated = {
        "type": "bar",
        "title": "Institutional Portfolio Concentration (HHI) by Sector",
        "series": [{"name": "Portfolio HHI", "data": [{"label": "2025", "value": 10}]}],
    }
    relevant = {
        "type": "bar",
        "title": "Promoter stake sold by quarter",
        "xLabel": "Quarter",
        "yLabel": "Stake sold (%)",
        "series": [{"name": "Promoter stake sold", "data": [{"label": "Q1 FY26", "value": 2.5}]}],
    }

    assert _chart_is_relevant(unrelated, report, question, 1) is False
    assert _chart_is_relevant(relevant, report, question, 2) is True


def test_fta_chart_filter_keeps_trade_and_tariff_comparisons():
    chart = {
        "type": "bar",
        "title": "India FTA exports by partner",
        "xLabel": "Trade partner",
        "yLabel": "Exports (USD million)",
        "series": [{"name": "FTA exports", "data": [{"label": "Partner A", "value": 120}]}],
    }

    assert _chart_is_relevant(
        chart, "## FTA outcomes\n\n[CHART_1]",
        "Compare India's FTAs and their effects on exports and tariffs.", 1,
    ) is True


def test_removed_chart_descriptions_are_replaced_with_a_truthful_limitation():
    report = (
        "## Trade results\n\n"
        "Export value (FY25) across 2 entities, plotted from reported figures.\n"
        "Each bar stacks the reported amount by partner."
    )
    audit = audit_report_publication(report, [], [], [])
    cleaned, meta = reconcile_orphaned_chart_narration(report, [])

    assert audit["orphanedChartMentions"]
    assert meta == {"removedLines": 2, "disclosureAdded": True}
    assert "plotted from reported figures" not in cleaned
    assert "Each bar stacks" not in cleaned
    assert "Visual data limitation" in cleaned
    assert audit_report_publication(cleaned, [], [], [])["orphanedChartMentions"] == []


def test_existing_chart_omission_disclosure_is_preserved_without_duplicates():
    report = (
        "> **Visual data limitation:** no source-verified chart is included.\n"
        "The reported observations remain in the cited text."
    )

    cleaned, meta = reconcile_orphaned_chart_narration(report, [])

    assert cleaned == report
    assert meta == {"removedLines": 0, "disclosureAdded": False}


def test_gallery_chart_inputs_match_the_export_provenance_policy(monkeypatch):
    monkeypatch.delenv("REPORT_ACCEPT_SEARCH_EXTRACT_EVIDENCE", raising=False)
    assert _gallery_observation_origin_is_chart_verifiable("publisher_page") is True
    assert _gallery_observation_origin_is_chart_verifiable("search_extract") is False
    publisher_quote = "Advances | FY25 | 120"
    assert _gallery_observation_origin_is_chart_verifiable(
        "search_extract", quote=publisher_quote,
        publisher_text=f"Quarterly data: {publisher_quote}",
    ) is True
    assert _gallery_observation_origin_is_chart_verifiable(
        "search_extract", quote="Advances | FY25 | 121",
        publisher_text=f"Quarterly data: {publisher_quote}",
    ) is False
    assert _gallery_observation_origin_is_chart_verifiable("unverified") is False

    monkeypatch.setenv("REPORT_ACCEPT_SEARCH_EXTRACT_EVIDENCE", "1")
    assert _gallery_observation_origin_is_chart_verifiable("search_extract") is True
