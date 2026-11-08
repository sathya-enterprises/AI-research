"""Stage F item 2: data-first charts from fetched datasets, protected from later relevance pruning."""
from utils.chart_evidence_audit import _dedupe_chart_specs, trust_validated_charts
from utils.dataset_charts import build_dataset_charts, preassign
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


def test_v2_validated_charts_survive_the_relevance_heuristic():
    chart = {"type": "bar", "title": "Quarterly return (%): Nifty Pharma", "unit": "%",
             "series": [{"name": "Nifty Pharma", "data": [{"label": q, "value": v} for q, v in zip(QUARTERS, [1, 2, 3, 4])]}]}
    question = "Explain the proportion and reasons for stake sales by Indian business promoters"
    trust_validated_charts([])
    _, kept, removed = _dedupe_chart_specs("[CHART_1]", [dict(chart)], question)
    assert removed == 1  # off-topic for this question: pruned
    trust_validated_charts([chart])
    _, kept, removed = _dedupe_chart_specs("[CHART_1]", [dict(chart)], question)
    assert removed == 0 and kept
    trust_validated_charts([])
