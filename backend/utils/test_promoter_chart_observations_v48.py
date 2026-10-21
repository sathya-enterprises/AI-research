"""Regression coverage for source-backed promoter activity chart data."""
from utils.evidence_dataset import build_evidence_dataset


def test_promoter_transaction_table_extracts_share_counts_with_source_quotes():
    source = {
        "id": "S1", "origin": "publisher_page", "title": "Promoter transaction disclosures",
        "text": (
            "| Company | Transaction type | Shares transferred | Stake / voting rights |\n"
            "|---|---|---:|---:|\n"
            "| Meyer Apparel | Inter-se transfer | 8,889,000 shares | 11.04% |\n"
            "| Anmol India | Open-market acquisition | 3,440,000 shares | 4.27% |\n"
            "| Mayur Uniquoters | Open-market purchase | 8,365 shares | 0.019% |"
        ),
    }
    ds = build_evidence_dataset([source], "Analyze Indian listed-company promoter stake sales")
    assert ds.observations, "source-backed transaction table should produce numeric observations"
    assert all(o.source_id == "S1" and o.quote for o in ds.observations)
    # The share-count and stake-percentage dimensions must remain distinct.
    metrics = {o.metric for o in ds.observations}
    assert len(metrics) >= 2


def test_promoter_chart_extraction_does_not_invent_missing_values():
    source = {
        "id": "S2", "origin": "publisher_page", "title": "Promoter disclosure",
        "text": "Meyer Apparel transferred 8,889,000 shares representing 11.04% of voting rights.",
    }
    ds = build_evidence_dataset([source], "Analyze promoter stake sales")
    assert all(o.value is not None and o.quote and o.source_id == "S2" for o in ds.observations)
