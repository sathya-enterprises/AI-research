"""Regression checks for topic-aware scraped evidence and chart recovery."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, Path(__file__).resolve().parents[1])

from utils.evidence_dataset import build_evidence_dataset
from utils.chart_gallery import gallery_types_for
from routes.report import _filter_gallery_dataset_for_question


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
    assert any(o.metric_label == "Shares transacted" and o.value == 165832 for o in rows)
    assert any(o.metric_label == "Stake sold / changed (%)" and o.value == 4.76 for o in rows)
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
