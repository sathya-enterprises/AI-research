"""Stage N: domain-neutral data layer (tiered fetching, data files, entity-search extracts)."""
import asyncio
import io

from utils import data_files, fetch_tiers, primary_sources
from utils.entity_research import run_entity_search
from utils.source_extractor import _data_file_links
from utils.source_quality import authority


def test_primary_hosts_any_country_and_domain():
    for url in ("https://mnre.gov.in/x.pdf", "https://cdnbbsr.s3waas.gov.in/a.pdf", "https://www.ons.gov.uk/data",
                "https://www150.statcan.gc.ca/t1", "https://www.ibge.gov.br/x", "https://www.meti.go.jp/a",
                "https://www.who.int/data", "https://data.worldbank.org/x", "https://www.iea.org/data",
                "https://sansad.in/getFile/x.pdf", "https://www.bfs.admin.ch/x", "https://www.census.gov/x"):
        assert primary_sources.is_primary(url), url
    for url in ("https://renewablewatch.in/2026/x", "https://economictimes.indiatimes.com/x",
                "https://www.goodreads.com/x", "https://example.co.in/x", "https://gov.example.com/x"):
        assert not primary_sources.is_primary(url), url


def test_authority_tier_is_generic():
    assert authority("https://mnre.gov.in/report")[0] == "primary_official"
    assert authority("https://www.who.int/data")[0] == "primary_official"
    assert authority("https://renewablewatch.in/x")[0] != "primary_official"


def test_file_extension_from_url_or_type():
    assert primary_sources.file_extension("https://x.org/a/b.XLSX?dl=1") == ".xlsx"
    assert primary_sources.file_extension("https://x.org/a", "text/csv; charset=utf-8") == ".csv"
    assert primary_sources.file_extension("https://x.org/a", "application/pdf") == ".pdf"
    assert primary_sources.file_extension("https://x.org/a.html") == ""


def test_retry_order_official_data_first_and_budget():
    rows = [{"url": "https://blog.example.com/a"}, {"url": "https://news.example.com/b.pdf"},
            {"url": "https://mnre.gov.in/page"}, {"url": "https://mnre.gov.in/report.pdf"}]
    assert fetch_tiers.retry_order(rows, [0, 1, 2, 3], cap=3) == [3, 2, 1]
    assert fetch_tiers.retry_seconds(rows[3]) > fetch_tiers.retry_seconds(rows[0]) >= 20


def test_host_limiter_caps_concurrency_per_host():
    limiter = fetch_tiers.HostLimiter(per_host=2)
    active = {"n": 0, "max": 0}

    async def job(url):
        async with limiter.slot(url):
            active["n"] += 1
            active["max"] = max(active["max"], active["n"])
            await asyncio.sleep(0.01)
            active["n"] -= 1

    async def main():
        await asyncio.gather(*[job("https://slow.gov.in/a%d" % i) for i in range(6)])

    asyncio.run(main())
    assert active["max"] == 2


def test_xlsx_becomes_table_blocks():
    from openpyxl import Workbook
    book = Workbook()
    sheet = book.active
    sheet.title = "Installed capacity"
    sheet.append(["Year", "Solar (MW)", "Wind (MW)"])
    sheet.append([2021, 40085.5, 39247])
    sheet.append([2022, 53996.0, 40358])
    sheet.append([None, None, None])
    buf = io.BytesIO()
    book.save(buf)
    text = data_files.spreadsheet_text(buf.getvalue(), ".xlsx")
    lines = text.splitlines()
    assert lines[0] == "TABLE 1 — Installed capacity"
    assert lines[1] == "Year | Solar (MW) | Wind (MW)"
    assert lines[2] == "2021 | 40085.5 | 39247"
    assert lines[3] == "2022 | 53996 | 40358"


def test_csv_and_unreadable_files():
    text = data_files.spreadsheet_text(b"Country;2022;2023\nIndia;93;93\nNigeria;60;62\n", ".csv", title="mcv1.csv")
    assert text.splitlines()[1] == "Country | 2022 | 2023" and "Nigeria | 60 | 62" in text
    assert data_files.spreadsheet_text(b"not a workbook", ".xlsx") == ""
    assert data_files.spreadsheet_text(b"\xd0\xcf\x11\xe0", ".xls") == ""


def test_data_file_links_resolved_with_anchor_text():
    html = ('<a href="/uploads/RE_capacity_2026.xlsx">Installed RE capacity (state-wise)</a>'
            '<a href="https://x.gov.in/a.pdf"><span>Annual report</span></a><a href="/about">About</a>'
            '<a href="/uploads/RE_capacity_2026.xlsx">dup</a>')
    links = _data_file_links(html, "https://mnre.gov.in/physical-progress/")
    assert [(x["url"], x["text"], x["format"]) for x in links] == [
        ("https://mnre.gov.in/uploads/RE_capacity_2026.xlsx", "Installed RE capacity (state-wise)", "xlsx"),
        ("https://x.gov.in/a.pdf", "Annual report", "pdf")]
    assert links[1]["context"] == "Installed RE capacity (state-wise)"
    # a bare "XLS" link takes its label from the text printed before it
    eia = '<tr><td>Table 3.1.B. Net generation from renewable sources</td><td><a href="t3_1_b.xlsx">XLS</a></td></tr>'
    link = _data_file_links(eia, "https://www.eia.gov/electricity/annual/")[0]
    assert link["context"].endswith("Net generation from renewable sources")
    m, q = link_terms("US renewable generation", ["net generation from solar"])
    picked = pick_links([{"url": "https://www.eia.gov/electricity/annual/", "pageFetchStatus": "ok",
                          "pageDataLinks": [link]}], measure_terms=m, question_terms=q, limit=2, existing_urls=set())
    assert picked[0]["title"] == "Table 3.1.B. Net generation from renewable sources"


def _entity_run(rows_after_fetch, rescue_fn=None):
    async def search(queries):
        return [{"url": r["url"], "searchQuery": queries[0]} for r in rows_after_fetch]

    async def enrich(rows):
        by_url = {r["url"]: r for r in rows_after_fetch}
        return [dict(by_url[r["url"]], searchQuery=r.get("searchQuery")) for r in rows]

    return asyncio.run(run_entity_search(
        [("KUSUM", "KUSUM solar 2021-2026")], search_fn=search,
        filter_fn=lambda rows, require_context=False: list(rows), enrich_fn=enrich,
        quality_fn=lambda rows: (rows, {}), existing_urls=set(), rescue_fn=rescue_fn))


def test_entity_search_keeps_labelled_extracts_when_fetch_fails():
    rows = [{"url": "https://a.gov.in/x.pdf", "pageFetchStatus": "timeout", "fullContent": "KUSUM " * 200},
            {"url": "https://b.com/y", "pageFetchStatus": "http_403", "fullContent": "short"},
            {"url": "https://c.com/z", "pageFetchStatus": "ok", "pageContent": "page"}]
    kept, stats = _entity_run(rows)
    urls = {r["url"]: r for r in kept}
    assert set(urls) == {"https://a.gov.in/x.pdf", "https://c.com/z"}
    assert urls["https://a.gov.in/x.pdf"]["evidenceOrigin"] == "search_extract_only"
    assert stats["fetchStatus"] == {"timeout": 1, "http_403": 1, "ok": 1}
    assert stats["search_extracts_kept"] == 1 and stats["fetched_ok"] == 1


def test_entity_search_browser_rescue_counts_as_fetched():
    rows = [{"url": "https://b.com/y", "pageFetchStatus": "http_403", "fullContent": "short"}]

    async def rescue(fetched):
        for r in fetched:
            r["pageFetchStatus"] = "ok"
        return {"attempted": 1, "recovered": 1}

    kept, stats = _entity_run(rows, rescue_fn=rescue)
    assert stats["fetched_ok"] == 1 and stats["browser"]["recovered"] == 1 and len(kept) == 1


# --- N2: data-file links ---------------------------------------------------------------------------------
from utils.data_links import link_terms, pick_links  # noqa: E402


def _parent(url, links, status="ok"):
    return {"url": url, "pageFetchStatus": status, "title": "Physical progress", "publisher": "MNRE",
            "pageDataLinks": [{"url": u, "text": t, "format": u.rsplit(".", 1)[-1]} for u, t in links]}


def test_link_terms_split_measures_from_question():
    m, q = link_terms("How has India's renewable capacity grown?", ["installed solar capacity", "wind capacity"])
    assert {"installed", "solar", "capacity", "wind"} <= m and "india" in q and "growth" not in q


def test_pick_links_needs_a_measure_word_and_prefers_official_spreadsheets():
    m, q = link_terms("India renewable energy capacity", ["installed solar capacity"])
    rows = [_parent("https://mnre.gov.in/physical-progress/", [
                ("https://mnre.gov.in/u/state-wise-installed-capacity.xlsx", "State-wise installed capacity"),
                ("https://mnre.gov.in/u/tender-notice.pdf", "Tender notice"),
                ("https://mnre.gov.in/u/solar-capacity-2025.pdf", "Solar capacity report 2025"),
                ("https://mnre.gov.in/u/annual-capacity.xls", "Annual capacity")]),
            _parent("https://blog.example.com/x", [("https://blog.example.com/solar.pdf", "Solar capacity")]),
            _parent("https://down.example.gov.in/", [("https://down.example.gov.in/solar.xlsx", "solar")], status="timeout")]
    picked = pick_links(rows, measure_terms=m, question_terms=q, limit=5, existing_urls=set())
    urls = [p["url"] for p in picked]
    assert urls[0].endswith("state-wise-installed-capacity.xlsx")
    assert "https://mnre.gov.in/u/tender-notice.pdf" not in urls      # no measure word
    assert not any(u.endswith(".xls") for u in urls)                  # legacy format not readable
    assert "https://down.example.gov.in/solar.xlsx" not in urls       # parent page was not fetched
    assert sum(1 for p in picked if p["parentUrl"] == "https://mnre.gov.in/physical-progress/") == 2  # per parent cap
    assert picked[0]["discoveredVia"] == "data_link" and picked[0]["publisher"] == "MNRE"


def test_pick_links_skips_known_urls_and_respects_limit():
    m, q = link_terms("vaccination coverage", ["measles vaccination coverage"])
    rows = [_parent("https://www.who.int/data", [("https://www.who.int/a/coverage.csv", "Measles coverage"),
                                                  ("https://www.who.int/a/vaccination.xlsx", "Vaccination data")])]
    assert len(pick_links(rows, measure_terms=m, question_terms=q, limit=1, existing_urls=set())) == 1
    picked = pick_links(rows, measure_terms=m, question_terms=q, limit=5,
                        existing_urls={"https://www.who.int/a/coverage.csv"})
    assert [p["url"] for p in picked] == ["https://www.who.int/a/vaccination.xlsx"]


# --- N3: chart titles for entity-column tables; same-title dedupe by values -------------------------------
import types  # noqa: E402

from utils.chart_titles import caption_metric, looks_like_entity  # noqa: E402
from utils.section_charts import build_chart_candidates, charts_from_passage  # noqa: E402


def _table(pid, caption, rows, reliability=0.9):
    text = f"TABLE 1 — {caption}\n" + "\n".join(rows)
    return types.SimpleNamespace(passage_id=pid, source_id=pid.split(".")[0], kind="table", text=text,
                                 reliability=reliability, title=caption)


def test_entity_column_takes_measure_from_caption():
    assert caption_metric("Share of electricity from solar (%), 2021–2025") == "Share of electricity from solar (%)"
    assert looks_like_entity("Andhra Pradesh") and not looks_like_entity("Installed capacity (MW)")
    p = _table("S1.1", "Share of electricity from solar (%), 2021–2025",
               ["Year | India", "2021 | 3.91", "2022 | 5.32", "2023 | 6.02", "2024 | 7.03"])
    chart = charts_from_passage(p, 1)[0]
    assert chart.spec["title"] == "Share of electricity from solar (%): India, 2021 to 2024"
    assert chart.spec["metric"] == "Share of electricity from solar (%)"


def test_same_generic_title_with_different_values_is_kept():
    rows = lambda vals: ["Year | India"] + [f"{2021 + i} | {v}" for i, v in enumerate(vals)]  # noqa: E731
    store = types.SimpleNamespace(passages=[
        _table("S1.1", "Installed capacity (GW)", rows([40, 54, 67, 82])),
        _table("S2.1", "Installed capacity (GW)", rows([39, 41, 44, 47])),      # same title, other values: kept
        _table("S3.1", "Installed capacity (GW)", rows([40, 54, 67, 82.1]))])   # same title, same values: dropped
    charts, dropped = build_chart_candidates(store)
    assert len(charts) == 2, [c.spec["title"] for c in charts]
    assert any("duplicate" in d["reason"] for d in dropped)


# --- N4: measure plan scope / breakdown; measure slots ----------------------------------------------------
from datetime import date as _date  # noqa: E402

from utils.measure_plan import Measure, MeasurePlan, measure_queries, validate  # noqa: E402
from utils.measure_slots import gap_queries, official_hosts, slot_coverage  # noqa: E402


def test_measure_plan_scope_and_breakdown_split():
    plan = validate({"scope": "India", "measures": [
        {"measure": "installed capacity for solar and wind", "entities": ["Solar Power", "Wind Power", "India"]},
        {"measure": "operational renewable portfolio size", "entities": ["ReNew", "Tata Power Renewable Energy"],
         "breakdown": "solar"}]}, tier="in_depth")
    first, second = plan.measures
    assert plan.scope == "India" and first.entities == [] and first.breakdown == ["Solar Power", "Wind Power"]
    assert second.entities == ["ReNew", "Tata Power Renewable Energy"] and second.breakdown == ["solar"]
    queries = measure_queries(plan, "India renewables 2021 to 2025", today=_date(2026, 1, 1), tier="detailed")
    assert queries[0].startswith("installed capacity for solar and wind India statistics by year")
    assert "Scope: India" in plan.brief()


def _plan():
    return MeasurePlan(scope="Nigeria", measures=[
        Measure("measles vaccination coverage", entities=["Lagos", "Kano"]),
        Measure("number of health workers per 10000 people")])


def test_slot_coverage_needs_a_data_block_with_measure_and_entity():
    rows = [{"url": "https://www.who.int/x", "pageFetchStatus": "ok",
             "pageContent": "TABLE 1 — Measles vaccination coverage by state\nState | 2022 | 2023\nLagos | 81 | 84\n"
                            "Kano | 40 | 47\nOyo | 60 | 62"},
            {"url": "https://news.example.com/y", "pageFetchStatus": "ok",
             "pageContent": "Health workers per 10000 people remain scarce, the minister said."},
            {"url": "https://down.example.com/z", "pageFetchStatus": "timeout",
             "pageContent": "TABLE 1 — health workers\nYear | n\n2021 | 1\n2022 | 2\n2023 | 3"}]
    slots = slot_coverage(_plan(), rows)
    assert [(s["entity"], s["covered"]) for s in slots] == [("Lagos", True), ("Kano", True), ("", False)]
    assert official_hosts(slots) == ["who.int"]


def test_gap_queries_round_robin_with_scope_first():
    slots = [{"measure": "measles vaccination coverage", "entity": "Lagos", "covered": False, "sources": []},
             {"measure": "measles vaccination coverage", "entity": "Kano", "covered": False, "sources": []},
             {"measure": "number of health workers", "entity": "", "covered": False, "sources": []}]
    queries = gap_queries(slots, _plan(), years=[2018, 2024], hosts=["who.int"], cap=4)
    assert queries == ["measles vaccination coverage Nigeria statistics table 2018 to 2024",
                       "number of health workers Nigeria statistics table 2018 to 2024",
                       "measles vaccination coverage Nigeria Lagos statistics table 2018 to 2024",
                       "number of health workers Nigeria annual report data xlsx OR pdf"]
    assert gap_queries([dict(s, covered=True) for s in slots], _plan(), cap=4) == []
