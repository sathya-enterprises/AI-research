"""Stage F item 2: data-first charts from fetched datasets, protected from later relevance pruning."""
from utils.chart_evidence_audit import _dedupe_chart_specs, audit_chart_evidence_consistency
from utils.dataset_charts import build_dataset_charts, find_quarterly_dataset, preassign, sector_vs_benchmark
from utils.evidence_store import EvidenceStore, build_evidence_store
from utils.fact_sheet import FactSheet, lookup
from utils.market_data import format_sector_quarterly_returns_as_source
from utils.report_outline import Section

QUARTERS = ["Q3 2025", "Q4 2025", "Q1 2026", "Q2 2026"]


def _rows():
    def row(label, returns, qtd):
        return {"label": label, "quarters": QUARTERS, "returns": returns, "quarter_end_closes": [1, 2, 3, 4],
                "missing_quarters": [], "history_source": "Yahoo Finance monthly bars",
                "qtd": {"from": QUARTERS[-1], "as_of_month": "Nov 2026", "close": 5, "return": qtd}}
    return [row("NIFTY AUTO", [2.5, -0.22, -3.11, 10.91], 4.1),
            row("NIFTY PHARMA", [1.0, -2.08, 7.15, 14.04], -1.2),
            row("NIFTY REALTY", [-5.0, -17.37, 1.35, 13.6], 2.2),
            row("NIFTY IT", [3.3, 4.4, -6.5, 1.1], 0.4),
            row("NIFTY 50", [1.5, 0.8, -2.0, 6.2], 1.0)]


def _store():
    source = format_sector_quarterly_returns_as_source(_rows())
    store = EvidenceStore()
    store.add_source("S2", source)
    return store.finalize()


LONG_QUARTERS = ["Q4 2024", "Q1 2025", "Q2 2025", "Q3 2025",
                 "Q4 2025", "Q1 2026", "Q2 2026", "Q3 2026"]
SECTORS = ["NIFTY AUTO", "NIFTY BANK", "NIFTY ENERGY", "NIFTY FMCG", "NIFTY IT",
           "NIFTY METAL", "NIFTY PHARMA", "NIFTY PSU BANK", "NIFTY REALTY", "NIFTY MEDIA", "NIFTY 50"]
ROTATION_QUESTION = "How can you explain and analyse the sector rotation in Indian stock markets in the past 8 quarters?"


def _long_store():
    rows = []
    for sector_index, label in enumerate(SECTORS):
        returns = [round((sector_index - 5) * 0.63 + (quarter_index - 3.5) * 0.37, 2)
                   for quarter_index in range(len(LONG_QUARTERS))]
        rows.append({
            "label": label, "quarters": LONG_QUARTERS, "returns": returns,
            "quarter_end_closes": [100 + sector_index * 2 + index for index in range(8)],
            "missing_quarters": [], "history_source": "Yahoo Finance monthly bars",
            "qtd": {"from": LONG_QUARTERS[-1], "as_of_month": "Nov 2026", "close": 110,
                    "return": round((sector_index - 4) * 0.8, 2)},
        })
    source = format_sector_quarterly_returns_as_source(rows)
    store = EvidenceStore()
    store.add_source("S2", source)
    return store.finalize(), source


def test_fetched_dataset_enters_the_store_under_its_renamed_registry_entry():
    source = format_sector_quarterly_returns_as_source(_rows())
    manifest = [{"id": "S2", "title": "NSE sector returns by calendar quarter (calculated)", "url": ""}]
    store = build_evidence_store([source], manifest=manifest)
    assert {p.source_id for p in store.passages} == {"S2"} and all(p.kind == "calculated" for p in store.passages)


def test_sector_vs_benchmark_compounded_and_separate_qtd_charts():
    charts, note = build_dataset_charts(_store())
    titles = [c.spec["title"] for c in charts]
    assert note["benchmark"] and note["qtdAsOf"] == "Nov 2026"
    auto = next(c for c in charts if "Nifty Auto vs Nifty 50" in c.spec["title"])
    assert auto.spec["type"] == "grouped_bar" and auto.spec["provenance"] == "fetched dataset"
    assert [s["name"] for s in auto.spec["series"]] == ["Nifty Auto", "Nifty 50"]
    # Every point quotes the dataset row that holds it.
    point = auto.spec["series"][0]["data"][-1]
    assert point["evidenceValue"] == "+10.91%" and point["evidenceValue"] in point["evidenceQuote"]
    # QTD is charted on its own, labelled partial; never inside the quarterly matrix chart.
    qtd = [t for t in titles if "QTD, partial" in t]
    assert len(qtd) == 1 and all("Q2 2026" not in t for t in qtd)
    assert not any(label == "QTD" for c in charts if "vs Nifty 50" in c.spec["title"] for _, label, _ in c.points)
    assert any(t.startswith("Compounded return (%)") for t in titles)


def test_interpretation_numbers_are_facts_of_the_dataset():
    store = _store()
    charts, _ = build_dataset_charts(store)
    auto = next(c for c in charts if "Nifty Auto vs" in c.spec["title"])
    text = auto.interpretation
    assert "Nifty Auto returned more than the Nifty 50 in" in text and "+10.91%" in text
    sheet = FactSheet(store.passages)
    for token in ("10.91", "6.2"):
        found = lookup(sheet, token, sign="+", unit="%", sentence=text, passage_ids=None)
        assert found.status == "ok", (token, found.status)


def test_deep_dives_get_their_sector_chart_and_overview_gets_cross_sector_charts():
    charts, _ = build_dataset_charts(_store())
    sections = [Section("sec-1", "Sector rotation across quarters", role="cross_cutting"),
                Section("sec-2", "Auto: deep dive", role="deep_dive", entity="Auto"),
                Section("sec-3", "Pharma: deep dive", role="deep_dive", entity="Pharma")]
    preassign(charts, sections)
    placed = {c.spec["title"].split(":")[1].split(",")[0].strip(): c.section_id for c in charts if c.section_id}
    assert placed.get("Nifty Auto vs Nifty 50") == "sec-2" and placed.get("Nifty Pharma vs Nifty 50") == "sec-3"
    assert all(c.section_id == "sec-1" for c in charts if c.points[0][0] in {"Compounded return", "QTD return"})


def test_source_verified_charts_still_have_to_match_the_requested_topic():
    chart = {"type": "bar", "title": "Quarterly return (%): Nifty Pharma", "unit": "%",
             "series": [{"name": "Nifty Pharma", "data": [{"label": q, "value": v} for q, v in zip(QUARTERS, [1, 2, 3, 4])]}]}
    question = "Explain the proportion and reasons for stake sales by Indian business promoters"
    _, kept, removed = _dedupe_chart_specs("[CHART_1]", [dict(chart)], question)
    assert removed == 1  # off-topic for this question: pruned


def test_health_charts_use_source_metric_and_reject_world_or_finance_scope():
    question = (
        "Prepare an in-depth comparison of health outcomes in India, China, Brazil, and Nigeria "
        "from 2010 through 2024. Cover life expectancy, child and under-five mortality, HIV, "
        "leading causes of death, and health-related Sustainable Development Goals."
    )
    world_only = {
        "type": "horizontal_bar", "title": "Every year: World, 2010 to 2024", "source": ["S67"],
        "series": [{"name": "World", "data": [{"label": str(y), "value": 70 + y % 3} for y in range(2010, 2025)]}],
    }
    financial_heatmap = {
        "type": "heatmap", "title": "Quarterly return: 63 periods, 2010-12 to 2018-20", "source": ["S87"],
        "rows": ["NIFTY AUTO", "NIFTY BANK"], "columns": ["Q1", "Q2", "Q3"],
        "values": [[1, 2, 3], [4, 5, 6]],
    }
    sources = [
        {"id": "S67", "title": "Brazil vs Nigeria: Child mortality since 1931 · TAKIP", "url": "https://takip.dev/compare/brazil-vs-nigeria/child-mortality"},
        {"id": "S87", "title": "Publications | Official Website of Reserve Bank of India", "url": "https://rbi.org.in/scripts/publicationsview.aspx?id=22800"},
    ]
    _, kept, removed = _dedupe_chart_specs("[CHART_1]\n[CHART_2]", [world_only, financial_heatmap], question, sources=sources)
    assert kept == [] and removed == 2


def test_generic_value_chart_gets_its_metric_from_the_cited_source_title():
    question = "Compare child mortality in India, China, Brazil and Nigeria from 2010 to 2024."
    chart = {
        "type": "line", "title": "Figure 1: Value: China, India, Brazil and Nigeria, 2010 to 2024",
        "source": ["S15"],
        "series": [{"name": country, "data": [{"label": str(year), "value": 10.0} for year in range(2010, 2024)]}
                   for country in ("China", "India", "Brazil", "Nigeria")],
    }
    sources = [{"id": "S15", "title": "Child Mortality Rate by Country: Chart, Data Table and CSV"}]
    _, kept, removed = _dedupe_chart_specs("[CHART_1]", [chart], question, sources=sources)
    assert removed == 0 and len(kept) == 1
    assert kept[0]["title"].startswith("Figure 1: Child Mortality Rate:")


def test_dataset_quarters_use_the_report_fiscal_labels():
    from utils.dataset_charts import fy_label
    assert [fy_label(q) for q in QUARTERS] == ["Q2 FY26", "Q3 FY26", "Q4 FY26", "Q1 FY27"]
    charts, note = build_dataset_charts(_store())
    assert note["quarters"][0] == "Q2 FY26"
    assert any("Q2 FY26 to Q1 FY27" in c.spec["title"] for c in charts)


def test_eight_quarter_rotation_uses_full_heatmap_without_partial_qtd():
    store, source = _long_store()
    charts, note = build_dataset_charts(store, question=ROTATION_QUESTION)

    heatmap = next(chart for chart in charts if chart.spec.get("type") == "heatmap")
    assert note["matrixView"] is True
    assert note["quarters"] == ["Q3 FY25", "Q4 FY25", "Q1 FY26", "Q2 FY26",
                                "Q3 FY26", "Q4 FY26", "Q1 FY27", "Q2 FY27"]
    assert heatmap.spec["columns"] == note["quarters"]
    assert len(heatmap.spec["rows"]) == 11
    assert len(heatmap.spec["values"]) == 11
    assert all(len(row) == 8 and all(value is not None for value in row) for row in heatmap.spec["values"])
    assert not note["qtdChartIncluded"]
    assert not any("QTD, partial" in chart.spec["title"] for chart in charts)
    assert any(chart.spec["title"].startswith("Compounded return (%)") for chart in charts)
    overview = Section("overview", "Sector rotation across Indian markets", role="cross_cutting")
    preassign(charts, [overview])
    assert all(chart.section_id == "overview" for chart in charts)
    source["id"] = "S2"
    placeholders = "\n".join(f"[CHART_{index}]" for index in range(1, len(charts) + 1))
    _text, relevant, removed = _dedupe_chart_specs(
        placeholders, [chart.spec for chart in charts], ROTATION_QUESTION, sources=[source],
    )
    assert removed == 0 and len(relevant) == 2


def test_named_sector_in_a_broad_matrix_gets_one_focused_drilldown():
    store, _source = _long_store()
    charts, _note = build_dataset_charts(
        store, question="How did the IT sector perform over the past 8 quarters?",
    )
    drilldowns = [chart for chart in charts if "Nifty IT vs Nifty 50" in chart.spec["title"]]
    assert len(drilldowns) == 1
    assert drilldowns[0].spec["series"][0]["name"] == "Nifty IT"
    assert not any("Nifty Auto vs Nifty 50" in chart.spec["title"] for chart in charts)


def test_sector_drilldown_retains_calendar_column_and_passes_evidence_audit():
    store, source = _long_store()
    source["id"] = "S2"
    data = find_quarterly_dataset(store)
    assert data is not None
    assert data.source_quarters == LONG_QUARTERS

    chart = sector_vs_benchmark(data, "NIFTY IT", 1)
    assert chart is not None
    point = chart.spec["series"][0]["data"][0]
    assert point["label"] == "Q3 FY25"
    assert point["evidencePeriod"] == "Q4 2024"
    assert point["evidenceColumn"] == "Q4 2024"
    assert point["evidenceColumnIndex"] == 1
    assert point["evidenceRowLabel"] == "NIFTY IT"

    from utils.section_charts import _period_end_year, clip_to_window
    assert _period_end_year("Q3 FY25") == 2024
    assert _period_end_year("Q1 FY27") == 2026
    assert _period_end_year("Q2 FY27") == 2026
    assert _period_end_year("Q4 FY27") == 2027
    assert clip_to_window(chart, 2024, 2026) == ""
    assert len(chart.spec["series"][0]["data"]) == 8

    result = audit_chart_evidence_consistency("[CHART_1]", [chart.spec], [source],
                                              question=ROTATION_QUESTION,
                                              require_publisher_verification=True)
    assert result.get("chartEvidenceFailures", []) == [], result


def test_full_sector_heatmap_cells_audit_against_calendar_source_columns():
    store, source = _long_store()
    source["id"] = "S2"
    charts, _note = build_dataset_charts(store, question=ROTATION_QUESTION)
    heatmap = next(chart for chart in charts if chart.spec.get("type") == "heatmap")

    result = audit_chart_evidence_consistency("[CHART_1]", [heatmap.spec], [source],
                                              question=ROTATION_QUESTION,
                                              require_publisher_verification=True)
    assert result.get("chartEvidenceFailures", []) == [], result
    assert result.get("chartTableVerificationConflicts", []) == [], result


def test_past_eight_quarters_can_include_qtd_only_when_explicitly_requested():
    store, _source = _long_store()
    charts, note = build_dataset_charts(store, question=ROTATION_QUESTION + " Include current quarter-to-date too.")
    assert note["qtdChartIncluded"]
    assert any("QTD, partial" in chart.spec["title"] for chart in charts)


def test_eight_quarter_request_drops_partial_and_subquarter_snapshot_charts():
    short_horizon_charts = [
        {"type": "horizontal_bar", "title": "Quarter-to-date return, QTD partial", "series": []},
        {"type": "horizontal_bar", "title": "1-month return as of 31 Jan 2026", "series": []},
        {"type": "horizontal_bar", "title": "Day return retrieved 13 Nov 2026", "series": []},
        {"type": "line", "title": "Quarterly sector returns, Q3 FY25 to Q2 FY27", "series": []},
    ]
    placeholders = "\n".join(f"[CHART_{index}]" for index in range(1, len(short_horizon_charts) + 1))

    _text, kept, removed = _dedupe_chart_specs(
        placeholders, short_horizon_charts, ROTATION_QUESTION,
    )

    assert removed == 3
    assert len(kept) == 1 and kept[0]["title"].startswith("Quarterly sector returns")
