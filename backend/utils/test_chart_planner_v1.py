from utils.chart_planner import (
    assess_chart_coverage, gap_queries, plan_chart_needs, planned_chart_types, plan_meta, writer_plan_block,
)

SECTOR_Q = (
    "Sector returns by quarter\n"
    "| Sector | Q1 2025 | Q2 2025 | Q3 2025 | Q4 2025 |\n"
    "| Nifty Bank return | 3.1% | 4.2% | -1.0% | 5.0% |\n"
    "| Nifty IT return | -4.0% | 6.1% | 2.2% | 1.1% |\n"
    "| Nifty Pharma return | 2.0% | 7.4% | 1.2% | 0.4% |\n"
    "| Nifty FMCG return | 1.0% | 2.2% | 0.3% | -0.7% |\n"
    "| Nifty Auto return | 5.0% | -2.2% | 4.3% | 3.9% |\n"
    "| Nifty Metal return | 8.0% | 1.2% | -3.3% | 2.9% |\n"
)


def test_sector_rotation_question_plans_sector_matrix_first():
    needs = plan_chart_needs("Sector rotation in Indian markets over the past 8 quarters", "detailed")
    assert needs[0].key == "sector_quarterly_returns"
    assert "heatmap" in needs[0].chart_types
    assert {n.key for n in needs} >= {"fpi_sector_flows", "fpi_net_flow_series"}


def test_unknown_topic_gets_generic_plan_not_domain_charts():
    needs = plan_chart_needs("Summarise the electric vehicle market", "detailed")
    assert {n.key for n in needs} == {"peer_comparison", "headline_trend"}


def test_coverage_satisfied_when_sector_table_has_periods_and_entities():
    needs = plan_chart_needs("sector rotation past 8 quarters")[:1]
    cov = assess_chart_coverage(needs, [{"id": "S4", "text": SECTOR_Q, "url": "https://www.niftyindices.com/x"}])[0]
    assert cov.status == "satisfied"
    assert cov.source_ids == ["S4"]
    assert cov.periods >= 4 and cov.entities >= 5


def test_prose_without_numbers_does_not_count_as_coverage():
    needs = plan_chart_needs("sector rotation past 8 quarters")[:1]
    text = "Sector rotation is a strategy in which investors move between sectors. Nifty Bank performance matters."
    cov = assess_chart_coverage(needs, [{"id": "S1", "text": text}])[0]
    assert cov.status == "missing"


def test_missing_need_produces_targeted_gap_queries_and_blocks_chart_type():
    needs = plan_chart_needs("sector rotation past 8 quarters")
    coverages = assess_chart_coverage(needs, [{"id": "S1", "text": "General market commentary without figures."}])
    queries = gap_queries(coverages, limit=3)
    assert queries and any("sectoral" in q.lower() or "sector" in q.lower() for q in queries)
    assert planned_chart_types(coverages) == []
    block = writer_plan_block(coverages)
    assert "NOT AVAILABLE" in block and "Do NOT draw" in block


def test_plan_block_binds_charts_to_canonical_ids_and_meta_counts():
    needs = plan_chart_needs("sector rotation past 8 quarters")[:1]
    coverages = assess_chart_coverage(needs, [{"id": "S7", "text": SECTOR_Q}])
    block = writer_plan_block(coverages)
    assert "[S7]" in block
    assert plan_meta(coverages)["satisfied"] == 1


def test_calculated_sector_source_satisfies_the_matrix_need():
    needs = plan_chart_needs("sector rotation past 8 quarters")[:1]
    text = "\n".join(f"Nifty Sector{i} | Q{q} 202{y} | {i + q}%" for i in range(6) for q in (1, 2) for y in (4, 5))
    source = {"id": "S2", "title": "CALCULATED NSE SECTOR RETURNS", "text": text,
              "evidence_origin": "calculated_from_daily_market_data"}
    assert assess_chart_coverage(needs, [source])[0].status == "satisfied"


PROMOTER_Q = "Explain the proportion and reasons for stake sales by Indian business promoters in the past 8 quarters"


def _filing(company: str, day: str) -> str:
    return (f"Disclosure dated {day}\n"
            f"The promoter of {company} Limited sold 3,90,000 equity shares on {day}, a stake of 1.2%.\n")


def test_scattered_single_company_filings_are_not_a_quarterly_series():
    need = next(n for n in plan_chart_needs(PROMOTER_Q, "in_depth") if n.key == "promoter_sales_series")
    sources = [
        {"id": "S1", "text": _filing("Fiem Industries", "05 Sep 2025")},
        {"id": "S2", "text": _filing("Akzo Nobel India", "24 Dec 2025")},
        {"id": "S3", "text": _filing("Andhra Cements", "18 Mar 2026")},
        {"id": "S4", "text": _filing("Getalong Enterprise", "16 Jul 2026")},
    ]
    cov = assess_chart_coverage([need], sources)[0]
    assert cov.status == "partial"
    assert "no single source holds the series" in cov.reason
    assert gap_queries([cov], limit=3)


def test_one_market_wide_quarterly_series_satisfies_the_promoter_chart():
    need = next(n for n in plan_chart_needs(PROMOTER_Q, "in_depth") if n.key == "promoter_sales_series")
    text = (
        "Promoter stake sales by quarter (Rs crore)\n"
        "| Quarter | Promoters sold (Rs crore) |\n"
        "| Q3 FY25 | 41,200 |\n| Q4 FY25 | 28,900 |\n| Q1 FY26 | 35,400 |\n| Q2 FY26 | 52,100 |\n"
    )
    cov = assess_chart_coverage([need], [{"id": "S9", "text": text}])[0]
    assert cov.status == "satisfied", cov.reason


def test_market_wide_promoter_data_is_recognised_but_a_single_filing_is_not():
    from routes.report import _is_promoter_market_wide_data

    market = ("At Rs 1.5 lakh crore, promoter selling breaks all records in 2025. Promoters sold shares worth "
              "Rs 1.5 lakh crore across listed companies, against Rs 1.1 lakh crore in 2024. Promoter holding in "
              "NSE-listed companies fell to 40.6% in the March quarter from 41.9%.")
    filing = ("Disclosure under Regulation 29(2) of SEBI (SAST). The promoter group of Fiem Industries Limited "
              "sold 3,90,000 equity shares on 05 Sep 2025.")
    assert _is_promoter_market_wide_data(market.casefold())
    assert not _is_promoter_market_wide_data(filing.casefold())
