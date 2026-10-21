from utils.research_engine import build_research_engine_context, compute_statistics, format_engine_context


def test_cagr_and_trend_are_source_grounded():
    sources=[{"id":"S1","title":"Series","url":"https://example.com","fullContent":"Revenue was $100 million in 2020 and $121 million in 2022."}]
    ctx=build_research_engine_context(sources,"revenue")
    derived=ctx["statistics"]["derived"]
    assert any(x["metric"]=="CAGR" and abs(x["value"]-10.0)<0.2 for x in derived)
    assert all(r["source_id"]=="S1" for r in ctx["numeric_evidence"])


def test_graph_has_provenance():
    sources=[{"id":"S7","title":"Market","url":"https://example.com","fullContent":"Acme Corp leads Beta Inc in the market."}]
    ctx=build_research_engine_context(sources,"market")
    assert ctx["graph"]["source_count"]==1
    assert any(n["type"]=="entity" for n in ctx["graph"]["nodes"])
    assert all("provenance" in e for e in ctx["graph"]["edges"])


def test_contradiction_flag_does_not_average():
    sources=[
      {"id":"S1","title":"A","fullContent":"The market is $100 billion in 2025."},
      {"id":"S2","title":"B","fullContent":"The market is $140 billion in 2025."},
    ]
    ctx=build_research_engine_context(sources,"market")
    assert ctx["contradictions"]
    assert "do not average" in format_engine_context(ctx).lower()


def test_analytics_does_not_average_revenue_and_profit_into_one_cagr():
    sources = [{
        "id": "S1",
        "title": "Financial series",
        "fullContent": (
            "Revenue was $100 million in 2020 and $121 million in 2022. "
            "Profit was $20 million in 2020 and $18 million in 2022."
        ),
    }]
    ctx = build_research_engine_context(sources, "revenue and profit")
    cagr = [row for row in ctx["statistics"]["derived"] if row["metric"] == "CAGR"]
    by_metric = {row["series"]: row["value"] for row in cagr}

    assert any("revenue" in key for key in by_metric)
    assert any("profit" in key for key in by_metric)
    assert round(next(value for key, value in by_metric.items() if "revenue" in key), 1) == 10.0
    assert next(value for key, value in by_metric.items() if "profit" in key) < 0


def test_analytics_skips_a_metric_series_with_conflicting_values_for_the_same_year():
    numeric = [
        {"source_id": "S1", "raw": "$100 million", "value": 100_000_000, "unit": "absolute", "context": "Revenue was $", "year": 2020},
        {"source_id": "S2", "raw": "$140 million", "value": 140_000_000, "unit": "absolute", "context": "Revenue was $", "year": 2020},
        {"source_id": "S3", "raw": "$121 million", "value": 121_000_000, "unit": "absolute", "context": "Revenue was $", "year": 2022},
    ]
    stats = compute_statistics(numeric)
    assert not any(row["metric"] == "CAGR" for row in stats["derived"])
    assert any(row["reason"] == "conflicting_values_for_same_metric_and_year" for row in stats["series_skipped"])


def test_engine_context_explains_skipped_conflicting_metric_series():
    numeric = [
        {"source_id": "S1", "raw": "$100 million", "value": 100_000_000, "unit": "absolute", "context": "Revenue was $", "year": 2020},
        {"source_id": "S2", "raw": "$140 million", "value": 140_000_000, "unit": "absolute", "context": "Revenue was $", "year": 2020},
        {"source_id": "S3", "raw": "$121 million", "value": 121_000_000, "unit": "absolute", "context": "Revenue was $", "year": 2022},
    ]
    ctx = {"graph": {}, "statistics": compute_statistics(numeric), "numeric_evidence": numeric, "contradictions": []}
    formatted = format_engine_context(ctx).casefold()
    assert "series omitted" in formatted
    assert "conflicting_values_for_same_metric_and_year" in formatted


def test_publisher_page_data_precedes_search_snippet_and_captures_physical_units():
    sources = [{
        "id": "S9", "title": "Capacity series",
        "pageContent": "Capacity | Year | Value\nCapacity | 2022 | 10 GW\nCapacity | 2023 | 15 GW",
        "fullContent": "Search snippet only mentions 1% growth.",
    }]
    ctx = build_research_engine_context(sources, "capacity trend")
    observations = ctx["numeric_evidence"]
    capacity = [row for row in observations if row["raw"] in {"10 GW", "15 GW"}]
    assert len(capacity) == 2
    assert [row["year"] for row in capacity] == [2022, 2023]
    assert all(row["unit"] == "absolute" for row in capacity)
    assert not ctx["contradictions"], "Different years in the same metric series are not contradictory estimates"


def test_numeric_inventory_is_source_balanced():
    from utils.research_engine import format_engine_context
    numeric = []
    for source_id in ("S1", "S2", "S3"):
        for i in range(10):
            numeric.append({"source_id": source_id, "raw": f"{i + 1} GW", "unit": "absolute", "context": f"capacity {i + 1}", "value": i + 1, "year": 2020 + i})
    formatted = format_engine_context({"graph": {}, "statistics": {}, "contradictions": [], "numeric_evidence": numeric}, max_chars=10000, max_numeric_observations=6)
    assert "[S1]" in formatted and "[S2]" in formatted and "[S3]" in formatted


def test_numeric_observations_retain_period_quote_url_and_verification_level():
    from utils.research_engine import extract_numeric_evidence

    source = {
        "id": "S1", "title": "Official metrics", "url": "https://example.gov/data?utm_source=test",
        "pageContent": "Revenue was $100 million in 2020 and $121 million in 2022.",
        "pageFetchStatus": "ok", "evidenceOrigin": "publisher_page",
    }
    rows = extract_numeric_evidence([source])
    assert [(row["year"], row["value"]) for row in rows] == [(2020, 100_000_000), (2022, 121_000_000)]
    assert all(row["publisher_page_verified"] for row in rows)
    assert all(row["evidence_quote"] == source["pageContent"] for row in rows)
    assert all(row["canonical_url"] == "https://example.gov/data" for row in rows)
    assert all(row["source_ids"] == ["S1"] for row in rows)


def test_numeric_extraction_rejects_file_sizes_and_unlabelled_numbers_and_marks_snippets_unverified():
    from utils.research_engine import extract_numeric_evidence

    rows = extract_numeric_evidence([
        {"id": "S1", "title": "Search result", "snippet": (
            "File size: 13 KB. Source count: 69. Unrelated token 8201. "
            "Bank credit growth was 12.3% in September 2026."
        ), "url": "https://example.com/search"},
    ])
    assert len(rows) == 1
    assert rows[0]["raw"] == "12.3%"
    assert rows[0]["metric"] and rows[0]["period"] == "September 2026"
    assert rows[0]["publisher_page_verified"] is False
    assert rows[0]["evidence_origin"] == "search_extract"


def test_observation_dedup_preserves_different_entities_periods_denominators_and_units():
    from utils.research_engine import extract_numeric_evidence

    rows = extract_numeric_evidence([
        {"id": "S1", "title": "Results", "pageContent": (
            "Gross NPA ratio of Public Sector Banks was 3.2% of total advances in 2025.\n"
            "Gross NPA ratio of Private Sector Banks was 2.1% of total advances in 2025.\n"
            "Gross NPA ratio of Public Sector Banks was 2.9% of total advances in 2026."
        ), "pageFetchStatus": "ok", "evidenceOrigin": "publisher_page"},
        {"id": "S2", "title": "Results duplicate", "pageContent": (
            "Gross NPA ratio of Public Sector Banks was 3.2% of total advances in 2025."
        ), "pageFetchStatus": "ok", "evidenceOrigin": "publisher_page"},
    ])
    assert len(rows) == 3
    assert {row["year"] for row in rows} == {2025, 2026}
    assert len({row["entity"] for row in rows}) >= 2
    assert any(row["denominator"] == "advances" for row in rows)
    assert any(len(row["source_ids"]) > 1 for row in rows)
