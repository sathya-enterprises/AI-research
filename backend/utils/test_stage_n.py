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
    assert primary_sources.file_extension("https://sdmx.data.unicef.org/ws/data?format=csv") == ".csv"
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


def test_strict_ooxml_xlsx_still_extracts_sheet_values():
    import zipfile

    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    sheet.title = "Mortality by cause"
    sheet.append(["Country", "Cause", "Deaths"])
    sheet.append(["Brazil", "HIV/AIDS", 1200])
    sheet.append(["India", "HIV/AIDS", 8400])
    original = io.BytesIO()
    book.save(original)

    # Convert a normal fixture to the strict OOXML namespace form used by the
    # WHO workbook that was reported as empty during the live research run.
    strict = io.BytesIO()
    strict_namespaces = {
        b"http://schemas.openxmlformats.org/spreadsheetml/2006/main":
            b"http://purl.oclc.org/ooxml/spreadsheetml/main",
        b"http://schemas.openxmlformats.org/officeDocument/2006/relationships":
            b"http://purl.oclc.org/ooxml/officeDocument/relationships",
    }
    with zipfile.ZipFile(io.BytesIO(original.getvalue())) as source:
        with zipfile.ZipFile(strict, "w", compression=zipfile.ZIP_DEFLATED) as target:
            for info in source.infolist():
                content = source.read(info.filename)
                if info.filename.endswith((".xml", ".rels")):
                    for transitional, namespace in strict_namespaces.items():
                        content = content.replace(transitional, namespace)
                target.writestr(info, content)

    text = data_files.spreadsheet_text(strict.getvalue(), ".xlsx")
    assert "Mortality by cause" in text
    assert "Brazil | HIV/AIDS | 1200" in text
    assert "India | HIV/AIDS | 8400" in text


def test_xlsx_char_budget_is_shared_across_nonempty_sheets():
    from openpyxl import Workbook

    book = Workbook()
    first = book.active
    first.title = "North"
    for name in ("South", "West"):
        book.create_sheet(name)
    for sheet in book.worksheets:
        sheet.append(["Year", "Metric"])
        sheet.append([2022, f"{sheet.title} value"])
        sheet.append([2023, f"{sheet.title} value"])
    buffer = io.BytesIO()
    book.save(buffer)

    text = data_files.spreadsheet_text(buffer.getvalue(), ".xlsx", max_chars=450)
    assert all(name in text for name in ("North", "South", "West"))
    assert len(text) <= 450


def test_csv_and_unreadable_files():
    text = data_files.spreadsheet_text(b"Country;2022;2023\nIndia;93;93\nNigeria;60;62\n", ".csv", title="mcv1.csv")
    assert text.splitlines()[1] == "Country | 2022 | 2023" and "Nigeria | 60 | 62" in text
    assert data_files.spreadsheet_text(b"not a workbook", ".xlsx") == ""
    assert data_files.spreadsheet_text(b"\xd0\xcf\x11\xe0", ".xls") == ""


def test_large_csv_keeps_requested_coded_entities_before_row_truncation():
    import csv
    import io

    csv_buffer = io.StringIO()
    writer = csv.writer(csv_buffer)
    writer.writerow(["REF_AREA", "INDICATOR", "TIME_PERIOD", "OBS_VALUE"])
    for year in range(1990, 2030):
        for _ in range(12):
            writer.writerow(["YEM", "SERIES_1", year, 1])
    for code, value in (("IND", 11), ("BRA", 22), ("CHN", 33), ("NGA", 44)):
        for year in (2010, 2011, 2012, 2025):
            writer.writerow([code, "SERIES_1", year, value + year - 2010])

    text = data_files.spreadsheet_text(
        csv_buffer.getvalue().encode(), ".csv", entities=["India", "Brazil", "China", "Nigeria"],
        years=[2010, 2012])
    assert "YEM" not in text
    assert "2025" not in text
    for country in ("India (IND)", "Brazil (BRA)", "China (CHN)", "Nigeria (NGA)"):
        assert country in text
    assert "2012 | 13" in text and "2012 | 24" in text


def test_wide_xlsx_keeps_requested_country_columns():
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    sheet.append(["Year", "Metric", "Brazil", "China", "India", "Nigeria"])
    sheet.append([2010, "rate", 1, 2, 3, 4])
    sheet.append([2011, "rate", 5, 6, 7, 8])
    buffer = io.BytesIO()
    book.save(buffer)
    text = data_files.spreadsheet_text(buffer.getvalue(), ".xlsx", entities=["Brazil", "China"], years=[2011, 2011])
    assert "Brazil" in text and "China" in text
    assert "India" not in text and "Nigeria" not in text
    assert "2011 | rate | 5 | 6" in text
    assert "2010 | rate" not in text


def test_multilevel_wide_workbook_becomes_a_cause_by_country_table():
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    sheet.title = "Deaths All ages"
    sheet.append(["Sex", "GHE code", "GHE cause", "Brazil", "China", "India", "Nigeria"])
    sheet.append(["", "", "", "BRA", "CHN", "IND", "NGA"])
    sheet.append(["Persons", "A", "HIV/AIDS", 10, 20, 30, 40])
    sheet.append(["Female", "A", "HIV/AIDS", 5, 10, 15, 20])
    sheet.append(["Male", "A", "HIV/AIDS", 5, 10, 15, 20])
    sheet.append(["Persons", "B", "Stroke", 11, 21, 31, 41])
    buffer = io.BytesIO()
    book.save(buffer)

    text = data_files.spreadsheet_text(
        buffer.getvalue(), ".xlsx", entities=["Brazil", "China", "India", "Nigeria"])
    assert "GHE cause | Brazil | China | India | Nigeria" in text
    assert "HIV/AIDS | 10 | 20 | 30 | 40" in text
    assert "Stroke | 11 | 21 | 31 | 41" in text
    assert "Female |" not in text and "Male |" not in text


def test_wide_workbook_uses_dimension_header_and_label_columns_after_title_rows():
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    sheet.append([None, None, None, "World Health Organization"])
    sheet.append([None, None, None, "Department of Data and Analytics"])
    sheet.append([None, None, None, "December 2020"])
    sheet.append([])
    sheet.append([None, None, None, "Estimated deaths ('000) by cause, sex"])
    sheet.append([None, None, None, "and WHO Member State (1), 2019"])
    sheet.append(["Sex", "GHE code", None, "GHE cause", None, None, "Member State",
                  "Afghanistan", "Albania", "Brazil", "China", "India", "Nigeria"])
    sheet.append([None, None, None, None, None, None, "ISO-3 Code", "AFG", "ALB", "BRA", "CHN", "IND", "NGA"])
    sheet.append(["Persons", None, None, "Population ('000) (2)", None, None, None, 50, 60, 100, 200, 300, 400])
    sheet.append(["Persons", "0", None, "All Causes", None, None, None, 11, 12, 1, 2, 3, 4])
    sheet.append(["Persons", "10", "I.", "Communicable conditions", None, None, None, 13, 14, 5, 6, 7, 8])
    sheet.append(["Persons", "20", None, "A.", "Infectious and parasitic diseases", None, None,
                  15, 16, 3, 4, 5, 6])
    buffer = io.BytesIO()
    book.save(buffer)

    text = data_files.spreadsheet_text(
        buffer.getvalue(), ".xlsx", entities=["Brazil", "China", "India", "Nigeria"])

    assert "GHE cause | Brazil | China | India | Nigeria" in text
    assert "All Causes | 1 | 2 | 3 | 4" in text
    assert "Infectious and parasitic diseases | 3 | 4 | 5 | 6" in text
    assert "Estimated deaths ('000) by cause, sex" in text
    assert "\nEstimated deaths |" not in text
    assert "Population" not in text
    assert "A. (A.)" not in text


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
    assert picked[0]["publisherPageTitle"] == "Physical progress"


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
from utils.measure_slots import gap_queries, official_hosts, requested_years, slot_coverage, slot_limit  # noqa: E402


def test_measure_plan_scope_and_breakdown_split():
    plan = validate({"scope": "India", "measures": [
        {"measure": "installed capacity for solar and wind", "entities": ["Solar Power", "Wind Power", "India"]},
        {"measure": "operational renewable portfolio size", "entities": ["ReNew", "Tata Power Renewable Energy"],
         "breakdown": "solar"}]}, tier="in_depth")
    first, second = plan.measures
    assert plan.scope == "India" and first.entities == [] and first.breakdown == ["Solar Power", "Wind Power"]
    assert second.entities == ["ReNew", "Tata Power Renewable Energy"] and second.breakdown == ["solar"]
    queries = measure_queries(plan, "India renewables 2021 to 2025", today=_date(2026, 1, 1), tier="detailed")
    assert queries[0].startswith("installed capacity for solar and wind India Solar Power Wind Power annual time series by year")
    assert "Scope: India" in plan.brief()


def _plan():
    return MeasurePlan(scope="Nigeria", measures=[
        Measure("measles vaccination coverage", entities=["Lagos", "Kano"], chart="comparison"),
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
    assert queries == ["measles vaccination coverage Nigeria comparison across entities 2018 to 2024 statistics table",
                       "number of health workers Nigeria annual time series statistics by year 2018 to 2024 table",
                       "measles vaccination coverage Nigeria downloadable dataset data table CSV XLSX PDF 2018 to 2024",
                       "number of health workers Nigeria downloadable dataset data table CSV XLSX PDF 2018 to 2024"]
    assert gap_queries([dict(s, covered=True) for s in slots], _plan(), cap=4) == []


def test_gap_query_does_not_repeat_a_member_of_a_multi_entity_scope():
    plan = MeasurePlan(scope="India, China, Brazil, and Nigeria", measures=[
        Measure("under-five mortality rate", entities=["India", "China", "Brazil", "Nigeria"])])
    slots = [{"measure": "under-five mortality rate", "entity": name, "covered": False, "sources": []}
             for name in plan.measures[0].entities]
    queries = gap_queries(slots, plan, years=[2010, 2024], cap=6)
    nigeria_query = next(q for q in queries if q.endswith("Nigeria annual time series statistics by year 2010 to 2024 table"))
    assert nigeria_query.count("Nigeria") == 1
    assert queries[0].startswith("under-five mortality rate India, China, Brazil, and Nigeria annual time series statistics by year")


def test_slot_coverage_includes_every_named_entity_and_searches_the_uncovered_fourth():
    plan = MeasurePlan(scope="", measures=[Measure("life expectancy at birth", chart="comparison",
                                                   entities=["India", "China", "Brazil", "Nigeria"])])
    rows = [{"url": "https://who.int/health", "pageFetchStatus": "ok",
             "pageContent": "TABLE 1 — Life expectancy at birth\nCountry | Life expectancy at birth (years)\n"
                            "India | 67\nChina | 74\nBrazil | 72"}]

    slots = slot_coverage(plan, rows)
    assert [(s["entity"], s["covered"]) for s in slots] == [
        ("India", True), ("China", True), ("Brazil", True), ("Nigeria", False)]
    assert any("Nigeria" in query for query in gap_queries(slots, plan, years=[2010, 2024], cap=6))
    assert len(slot_coverage(plan, rows, max_entities=3)) == 3  # explicit caps remain available to callers


def test_structured_csv_query_context_covers_iso_coded_measure_rows():
    plan = MeasurePlan(scope="India and Brazil", measures=[
        Measure("HIV incidence", entities=["India", "Brazil"], chart="trend")])
    page = (
        "TABLE 1 — UNICEF SDMX export\n"
        "REF_AREA | INDICATOR | TIME_PERIOD | OBS_VALUE\n"
        "India (IND) | HVA_EPI_INF_ANN | 2010 | 120\n"
        "India (IND) | HVA_EPI_INF_ANN | 2011 | 118\n"
        "India (IND) | HVA_EPI_INF_ANN | 2012 | 116\n"
        "Brazil (BRA) | HVA_EPI_INF_ANN | 2010 | 140\n"
        "Brazil (BRA) | HVA_EPI_INF_ANN | 2011 | 135\n"
        "Brazil (BRA) | HVA_EPI_INF_ANN | 2012 | 130"
    )
    row = {"url": "https://sdmx.data.unicef.org/ws/data?format=csv", "pageFetchStatus": "ok",
           "pageFetchContentType": "text/csv", "pageDataFormat": "csv", "pageContent": page,
           "searchQuery": "HIV incidence India Brazil annual data 2010 to 2012"}
    slots = slot_coverage(plan, [row], years=[2010, 2012])
    assert [(slot["entity"], slot["covered"]) for slot in slots] == [("India", True), ("Brazil", True)]


def test_metadata_and_latest_year_rankings_do_not_cover_a_trend_measure():
    plan = MeasurePlan(scope="India, China, Brazil, and Nigeria", measures=[
        Measure("under-five mortality rate", entities=["India", "China", "Brazil", "Nigeria"], chart="trend")])
    metadata = ("TABLE 1 â€” Indicator metadata\nField | Value\nName | Under-five mortality rate (per 1000 live births)\n"
                "Indicator unique identifier | 2322814\nUnit of measure | probability of dying by age 5 per 1000 births\n"
                "Geographic entities and groups | India, China, Brazil, Nigeria\n"
                "Time intervals | 2010, 2011, 2012, 2013, 2014, 2015, 2016, 2017, 2018, 2019, 2020, 2021\n")
    latest = ("TABLE 2 â€” Under-five mortality rate by country\nRank | Country or area | Latest year | Under-five mortality rate\n"
              "1 | Nigeria | 2024 | 115.6\n2 | India | 2024 | 26.6\n3 | China | 2024 | 5.7\n4 | Brazil | 2024 | 14.2")
    rows = [{"url": "https://data.who.int/indicator", "pageFetchStatus": "ok",
             "pageContent": metadata + latest}]

    slots = slot_coverage(plan, rows)
    assert len(slots) == 4 and not any(slot["covered"] for slot in slots)
    queries = gap_queries(slots, plan, years=[2010, 2024], cap=6)
    assert any("under-five mortality rate India, China, Brazil, and Nigeria annual time series statistics by year" in query
               for query in queries)


def test_measure_slot_trends_must_have_observations_inside_the_requested_window():
    plan = MeasurePlan(scope="Brazil", measures=[Measure("under-five mortality rate", entities=["Brazil"])])
    rows = [{"url": "https://who.int/data", "pageFetchStatus": "ok",
             "pageContent": "TABLE 1 â€” Under-five mortality rate in Brazil\nYear | Brazil\n"
                            "2000 | 90\n2001 | 85\n2002 | 80"}]
    assert slot_coverage(plan, rows, years=list(range(2010, 2025)))[0]["covered"] is False
    rows[0]["pageContent"] = "TABLE 1 â€” Under-five mortality rate in Brazil\nYear | Brazil\n"
    rows[0]["pageContent"] += "2010 | 60\n2011 | 58\n2012 | 56"
    assert slot_coverage(plan, rows, years=list(range(2010, 2025)))[0]["covered"] is True


def test_linked_data_uses_the_parent_measure_title_without_replacing_file_evidence():
    plan = MeasurePlan(scope="India", measures=[Measure("under-five mortality rate", entities=["India"])])
    row = {"url": "https://who.int/downloads/u5mr.csv", "pageFetchStatus": "ok",
           "title": "Download data (CSV)", "publisherPageTitle": "WHO Under-five mortality rate estimates",
           "pageContent": "TABLE 1 â€” u5mr.csv\nYear | India\n2010 | 52\n2011 | 50\n2012 | 48"}
    slot = slot_coverage(plan, [row], years=[2010, 2024])[0]
    assert slot["covered"] is True and slot["sources"] == [row["url"]]


def test_who_methods_country_coverage_metadata_is_not_measure_data():
    plan = MeasurePlan(scope="Brazil", measures=[
        Measure("under-five mortality rate", entities=["Brazil"]),
        Measure("age-standardized death rate by cause", entities=["Brazil"]),
    ])
    row = {"url": "https://www.who.int/docs/ghe2019_cod_methods.pdf", "pageFetchStatus": "ok",
           "title": "WHO methods and data sources for country-level causes of death",
           "pageContent": ("Characteristics of country vital registration data and inclusion/exclusion.\n"
                           "Country Years of data in the WHO mortality database; years used to compute quality; "
                           "completeness (ages 15 and over); reason data or data-years were excluded.\n"
                           "Albania 1998-2010 low 89% 100% no low quality. Andorra 2011-2018 high 100% 100%.\n"
                           "Brazil 1998-2018 1998-2018 high 100% 100% 73% 84% yes.\n"
                           "Fewer than five years by ICD code; ages 15 and over.")}
    slots = slot_coverage(plan, [row], years=list(range(2010, 2025)))
    assert not any(slot["covered"] for slot in slots)


def test_narrative_year_values_can_cover_a_trend_but_period_mentions_cannot():
    plan = MeasurePlan(scope="Brazil", measures=[Measure("life expectancy", entities=["Brazil"])])
    row = {"url": "https://who.int/data", "pageFetchStatus": "ok",
           "title": "Brazil life expectancy", "pageContent":
           "Life expectancy in Brazil by year: 2010 71.3; 2011 71.6; 2012 71.9."}
    assert slot_coverage(plan, [row], years=[2010, 2012])[0]["covered"] is True
    row["pageContent"] = "Life expectancy in Brazil: estimates cover the period 2000-2024."
    assert slot_coverage(plan, [row], years=[2010, 2024])[0]["covered"] is False


def test_requested_slot_years_keep_the_explicit_end_year_and_expand_in_depth_budget():
    today = _date(2026, 11, 12)
    assert requested_years("Compare health from 2010 through 2024", today) == list(range(2010, 2025))
    assert requested_years("Health outcomes since 2022", today) == [2022, 2023, 2024, 2025, 2026]
    assert requested_years("Forecast outcomes from 2030 through 2035", today) == []
    assert slot_limit("in_depth") == 12
    from utils.data_angles import requested_year_bounds
    from utils.official_data import _years
    assert requested_year_bounds("Compare health from 2010 through 2024", today) == (2010, 2024)
    assert _years(2010, today, allow_current=True, end_year=2024) == list(range(2010, 2025))
    from utils.official_data import fetch_dataset

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return [{}, [{"value": 60 + i, "countryiso3code": "IND", "date": str(y),
                          "country": {"id": "IN"}} for i, y in enumerate((2010, 2023, 2024))]]

    class _Client:
        params = None

        async def get(self, _url, *, params):
            self.params = params
            return _Response()

    client = _Client()
    dataset = asyncio.run(fetch_dataset(client, {"id": "wb.life_exp", "entities": ["IND"]},
                                        start_year=2010, end_year=2024, today=today))
    assert client.params["date"] == "2010:2024" and dataset.years[-1] == 2024
    from utils.measure_slots import _content_words
    assert _content_words("under five mortality rate") == {"under-five", "mortality"}
    assert "age-standardized" in _content_words("age-standardised death rate")


# --- N5: publisher bodies by role; measure-plan seeds -----------------------------------------------------
from utils.entity_research import plan_entity_queries  # noqa: E402
from utils.entity_tiers import entity_tiers  # noqa: E402
from utils.publisher_roles import PublisherRoles  # noqa: E402


def _p(pid, text, url="https://news.example.com/a", publisher="", reliability=0.8):
    return types.SimpleNamespace(passage_id=pid, source_id=pid.split(".")[0], text=text, kind="text", url=url,
                                 publisher=publisher, reliability=reliability)


def test_publisher_roles_from_the_runs_own_sources():
    store = types.SimpleNamespace(passages=[
        _p("S1.1", "The Directorate of Fisheries Statistics (DFS) said output rose. DFS data show 4 million tonnes."),
        _p("S2.1", "Coastal Harvest Ltd reported record landings; Coastal Harvest Ltd expanded its fleet.",
           url="https://coastalharvest.com/ir", publisher="Coastal Harvest Ltd"),
        _p("S3.1", "Analysts say Coastal Harvest Ltd leads; Coastal Harvest Ltd shares rose."),
        _p("S4.1", "According to SeaWatch, stocks fell. SeaWatch data show decline.", url="https://seawatch.org/r"),
        _p("S5.1", "SeaWatch report: catches fell.", url="https://seawatch.org/x", publisher="SeaWatch")])
    roles = PublisherRoles(store, "How has fish production changed in Kerala?")
    assert roles.is_body("DFS") and roles.is_body("Ministry of Fisheries") and roles.is_body("SeaWatch")
    assert not roles.is_body("Coastal Harvest Ltd") and not roles.is_body("Kerala")
    assert not PublisherRoles(store, "What does the Ministry of Fisheries plan?").is_body("Ministry of Fisheries")


def test_seeded_entities_are_candidates_and_bodies_are_not_searched():
    text = ("Gujarat added capacity. Gujarat leads. Rajasthan grew. MNRE data show growth; MNRE said targets. "
            "The Ministry of New and Renewable Energy (MNRE) publishes figures.")
    store = types.SimpleNamespace(passages=[_p(f"S{i}.1", text, url=f"https://site{i}.com/x") for i in range(1, 5)]
                                  + [_p("S9.1", "Kerala installed rooftop units.", url="https://k.com/x")])
    tiers = entity_tiers(store, tier="in_depth", question="Which Indian states lead solar capacity?",
                         seeds=["Kerala", "Bihar"])
    every = {e.name: e for v in tiers.values() for e in v}
    assert every["MNRE"].role == "body" and every["MNRE"].tier != "deep_dive"
    assert every["Kerala"].seeded and "Bihar" not in every  # a seed needs at least one source
    searched = [e for e, _ in plan_entity_queries(tiers, cap=30, window="2021-2025",
                                                  question="Which Indian states lead solar capacity?")]
    assert searched[0] == "Kerala" and "MNRE" not in searched


# --- N6: official datasets declare their fit; unrelated ones rejected; stand-ins labelled -----------------
from utils.official_data import (CATALOG, Dataset, as_source, equivalent_dataset_alternative, fits_measure,
                                 plan_datasets)  # noqa: E402


def test_official_dataset_fit_is_checked_and_stand_ins_labelled():
    class _Result:
        ok = True

        def json(self):
            return {"datasets": [
                {"id": "owid.solar_share", "entities": ["IND"], "measure": "installed solar capacity", "fit": "stand-in"},
                {"id": "wb.domestic_credit", "entities": ["IND"], "measure": "investment in renewable energy projects",
                 "fit": "direct"}]}

    class _Gateway:
        async def generate(self, **_):
            return _Result()

    plan, note = asyncio.run(plan_datasets("India renewables", _Gateway(), today=_date(2026, 11, 10), limit=3,
                                           measures="- installed solar capacity"))
    assert [(x["id"], x["fit"]) for x in plan] == [("owid.solar_share", "stand-in")]
    assert note["rejected"][0]["id"] == "wb.domestic_credit"
    assert not fits_measure(CATALOG["wb.fdi_inflows"], "annual investment in renewable energy projects")
    ds = Dataset(entry=CATALOG["owid.solar_share"], years=[2021, 2022, 2023], series={"India": [3.9, 5.3, 6.0]},
                 retrieved="2026-11-10", latest=2023, fit="stand-in", measure="installed solar capacity")
    row = as_source(ds)
    assert 'NOTE — context indicator for "installed solar capacity"' in row["pageContent"]
    assert row["officialDataset"]["fit"] == "stand-in"


def test_official_data_provider_fallback_requires_exact_title_and_unit():
    assert equivalent_dataset_alternative({"id": "wb.life_exp"}) == "owid.life_expectancy"
    assert equivalent_dataset_alternative({"id": "wb.health_exp"}) is None


# --- N7: charted tables are not printed again; bare "TABLE 1" is not a caption ------------------------------
from utils.section_layout import parse_table, place_inline_tables  # noqa: E402


def test_bare_table_number_is_not_a_caption_and_charted_tables_are_skipped():
    p = types.SimpleNamespace(passage_id="S3.2", source_id="S3", kind="table", reliability=0.8, published="",
                              text="TABLE 1\nState | Solar (MW)\nRajasthan | 41700\nGujarat | 31000\nKarnataka | 9800")
    table = parse_table(p)
    assert table.heading == ""
    body = "Rajasthan leads solar capacity [S3.2]."
    out = place_inline_tables(body, {"S3.2": table}, set())
    assert "**Table: State by Solar (MW)**" in out and "TABLE 1" not in out
    assert place_inline_tables(body, {"S3.2": table}, {"S3.2"}) == body  # already shown as a figure


# --- network outage: wait and rewrite lost sections -------------------------------------------------------
from utils import net_recovery  # noqa: E402


def test_network_failure_classification_and_wait():
    assert net_recovery.network_failure("part 1: network:ConnectError model=gemini")
    assert not net_recovery.network_failure("empty response from model")
    calls = {"n": 0}

    async def probe():
        calls["n"] += 1
        return calls["n"] >= 3

    waited, back = asyncio.run(net_recovery.wait_for_network(5, every=0.01, probe=probe))
    assert back and calls["n"] == 3

    async def never():
        return False

    waited, back = asyncio.run(net_recovery.wait_for_network(0.05, every=0.02, probe=never))
    assert not back and waited < 0.5


def test_wait_budget_is_zero_in_tests_unless_set(monkeypatch):
    monkeypatch.delenv("GG_NETWORK_WAIT_SECONDS", raising=False)
    assert net_recovery.wait_budget() == 0
    monkeypatch.setenv("GG_NETWORK_WAIT_SECONDS", "120")
    assert net_recovery.wait_budget() == 120


# --- N9: fixes from the stable-network live run -------------------------------------------------------------
def test_n9_budgets_slots_links_bodies_scope():
    assert fetch_tiers.retry_seconds({"url": "https://mnre.gov.in/static/uploads/a.pdf"}) == 90
    assert fetch_tiers.retry_seconds({"url": "https://www.pib.gov.in/PressReleasePage.aspx"}) == 45
    plan = MeasurePlan(scope="India", measures=[Measure("grid curtailment rate")])
    rows = [{"url": "https://x.com/a", "pageFetchStatus": "ok",
             "pageContent": "TABLE 1 — Grid investment\nYear | Grid capex\n2021 | 10\n2022 | 12\n2023 | 14"}]
    assert slot_coverage(plan, rows)[0]["covered"] is False      # "grid" alone does not cover "grid curtailment"
    slots = [{"measure": "grid curtailment rate", "entity": "", "covered": False, "sources": []}]
    assert not any("nih.gov" in q for q in gap_queries(slots, plan, hosts=["pmc.ncbi.nlm.nih.gov"], cap=6))
    html = '<a href="/x.pdf"><img alt="Open resources icon" class="usa-icon" /></a>'
    assert _data_file_links(html, "https://pmc.ncbi.nlm.nih.gov/a/")[0]["text"] == ""
    store = types.SimpleNamespace(passages=[_p("S1.1", "The Central Electricity Authority (CEA) said capacity rose.")])
    roles = PublisherRoles(store, "India solar")
    assert roles.is_body("CEA") and roles.is_body("Central Electricity Authority")
    text = "United States added capacity. United States leads. India grew. Gujarat leads."
    store = types.SimpleNamespace(passages=[_p(f"S{i}.1", text, url=f"https://s{i}.com/x") for i in range(1, 5)])
    tiers = entity_tiers(store, tier="in_depth", question="How did India's solar capacity grow?", scope="India")
    us = next(e for v in tiers.values() for e in v if e.name == "United States")
    assert us.tier != "deep_dive" and us.role == "out_of_scope"


# --- N10: more charts from verifiable data -------------------------------------------------------------------
from utils.dataset_extractor import verify_dataset  # noqa: E402
from utils.section_charts import clip_to_window  # noqa: E402


def test_pdf_table_row_aligned_with_period_header_is_verified():
    text = ("STATES / UT's 2021-22 2022-23 2023-24 2024-25\n"
            "Gujarat 17,130.55 19,385.02 24,950.80 32,000.10\n"
            "Kerala 600.1 700.2 800.3\n")  # a short row: alignment uncertain
    ds = {"metric": "RE installed capacity", "unit": "MW", "x": "time", "series": [
        {"name": "Gujarat", "points": [{"label": "2022-23", "value": "19,385.02"},
                                       {"label": "2023-24", "value": "24,950.80"},
                                       {"label": "2024-25", "value": "32,000.10"}]}]}
    assert verify_dataset(ds, text, _date(2026, 11, 11))[0] is not None
    wrong = {**ds, "series": [{"name": "Gujarat", "points": [{"label": "2022-23", "value": "24,950.80"},
                                                             {"label": "2023-24", "value": "19,385.02"},
                                                             {"label": "2024-25", "value": "32,000.10"}]}]}
    assert verify_dataset(wrong, text, _date(2026, 11, 11))[0] is None  # swapped values are caught


def test_fiscal_year_run_then_value_run_is_verified():
    text = "Fig 6.2 RE share 2019-20 2020-21 2021-22 2022-23 21.26 21.67 21.73 22.61"
    ds = {"metric": "Share of RE", "unit": "%", "x": "time", "series": [{"name": "Share of RE", "points": [
        {"label": y, "value": v} for y, v in [("2019-20", "21.26"), ("2020-21", "21.67"), ("2021-22", "21.73"),
                                              ("2022-23", "22.61")]]}]}
    assert verify_dataset(ds, text, _date(2026, 11, 11))[0] is not None


def test_time_series_clipped_to_window_not_dropped():
    from utils.section_charts import PlannedChart
    data = [{"label": lab, "value": v} for lab, v in [("Cumulative till Mar 2014", 1), ("FY 2019-20", 2),
                                                     ("FY 2021-22", 3), ("FY 2022-23", 4), ("FY 2023-24", 5)]]
    chart = PlannedChart(chart_id="c1", spec={"type": "bar", "title": "Added (MW): Cumulative till Mar 2014, FY 2019-20",
                                              "series": [{"name": "Added (MW)", "data": data}]},
                         passage_ids=["S1.1"], source_ids=["S1"],
                         points=[("Added (MW)", d["label"], d["value"]) for d in data])
    assert clip_to_window(chart, 2021) == ""
    assert [d["label"] for d in chart.spec["series"][0]["data"]] == ["FY 2021-22", "FY 2022-23", "FY 2023-24"]
    assert chart.spec["title"] == "Added (MW): FY 2021-22 to FY 2023-24" and len(chart.points) == 3


def test_time_series_is_clipped_at_explicit_end_year():
    from utils.section_charts import PlannedChart, clip_to_window
    labels = [str(year) for year in range(2021, 2027)]
    data = [{"label": year, "value": value} for year, value in zip(labels, range(10, 16))]
    chart = PlannedChart(chart_id="c2", spec={"type": "line", "title": "Figure 2: Index: 2021 to 2026",
                                               "series": [{"name": "Index", "data": data}]},
                         passage_ids=["S2.1"], source_ids=["S2"], points=[("Index", y, int(v)) for y, v in zip(labels, range(10, 16))])
    assert clip_to_window(chart, 2021, 2024) == ""
    assert [d["label"] for d in chart.spec["series"][0]["data"]] == ["2021", "2022", "2023", "2024"]
    assert chart.spec["title"] == "Figure 2: Index: 2021 to 2024"


def test_seeded_company_publishing_its_own_pages_stays_a_subject():
    own = "ReNew reported capacity. ReNew data show growth. ReNew said targets."
    store = types.SimpleNamespace(passages=[_p(f"S{i}.1", own, url=f"https://renew.com/p{i}", publisher="ReNew")
                                            for i in range(1, 5)])
    assert PublisherRoles(store, "India solar companies").is_body("ReNew")  # by role alone it looks like a publisher
    seeded = entity_tiers(store, tier="in_depth", question="India solar companies", seeds=["ReNew"])
    assert next(e.role for v in seeded.values() for e in v if e.name == "ReNew") == ""


# --- N11: wide tables narrowed to the question; units of any domain; entity hygiene ------------------------------
from utils import table_focus  # noqa: E402


def _wide():
    rows = "\n".join(f"{y} | {37 - (y - 2000) * 0.5:.1f} | 6.5 | {20 - (y - 2000) * 0.5:.1f} | {60 - (y - 2000):.1f} | "
                     f"{18 - (y - 2000) * 0.2:.1f} | 4.4 | {130 - (y - 2000) * 1.5:.1f}" for y in range(2026, 2003, -1))
    return types.SimpleNamespace(passage_id="S29.6", source_id="S29", kind="table", reliability=0.8, published="",
                                 title="Child Mortality Rate by Country: Chart, Data Table and CSV",
                                 text="TABLE 200 — Full data table by year\n"
                                      "Year | World | United States | China | India | Brazil | United Kingdom | Nigeria\n" + rows)


def test_wide_country_year_table_is_narrowed_to_question_and_window():
    focus = table_focus.focus_entities("How did child mortality change in India, China, Brazil and Nigeria?")
    assert focus == ["India", "China", "Brazil", "Nigeria"]
    charts, dropped = build_chart_candidates(types.SimpleNamespace(passages=[_wide()]), min_year=2010, max_year=2024,
                                             focus=focus)
    spec = charts[0].spec
    assert [s["name"] for s in spec["series"]] == ["China", "India", "Brazil", "Nigeria"]
    labels = [d["label"] for d in spec["series"][0]["data"]]
    assert labels[0] == "2010" and labels[-1] == "2024"            # in-window, oldest first
    assert spec["title"].startswith("Child Mortality Rate: China, India, Brazil")
    assert "larger source table" in spec["basisNote"]


def test_explicit_country_columns_make_a_comparable_categorical_chart():
    passage = _table("S31.1", "Deaths All ages - Estimated deaths ('000) by cause, sex, 2019", [
        "Cause | Brazil | China | India | Nigeria",
        "All Causes | 100 | 200 | 300 | 400",
        "Communicable conditions | 40 | 50 | 60 | 70",
        "HIV/AIDS | 10 | 20 | 30 | 40",
        "Stroke | 15 | 25 | 35 | 45",
    ])
    focus = table_focus.focus_entities("Compare causes of death in Brazil, China, India and Nigeria")

    charts, dropped = build_chart_candidates(types.SimpleNamespace(passages=[passage]), focus=focus)

    assert len(charts) == 1 and not dropped
    chart = charts[0]
    assert chart.spec["type"] == "grouped_bar"
    assert [series["name"] for series in chart.spec["series"]] == ["Brazil", "China", "India", "Nigeria"]
    assert chart.spec["title"].startswith("Estimated deaths, All ages (thousands):")
    assert chart.spec["yLabel"] == "thousands"
    assert "All Causes" not in [point["label"] for series in chart.spec["series"] for point in series["data"]]
    assert len(chart.points) >= 12
    assert all(point["evidenceQuote"] in passage.text for series in chart.spec["series"]
               for point in series["data"])


def test_same_headers_on_distinct_workbook_sheets_are_not_merged_as_continuations():
    all_ages = _table("S32.1", "Deaths All ages", [
        "Cause | India", "HIV/AIDS | 10", "Stroke | 20", "Pneumonia | 30",
    ])
    under_five = _table("S32.2", "Deaths 0-4", [
        "Cause | India", "HIV/AIDS | 2", "Stroke | 4", "Pneumonia | 6",
    ])

    charts, dropped = build_chart_candidates(types.SimpleNamespace(passages=[all_ages, under_five]))

    assert len(charts) == 2 and not dropped
    assert [chart.passage_ids for chart in charts] == [["S32.1"], ["S32.2"]]
    assert {chart.heading for chart in charts} == {"Deaths All ages", "Deaths 0-4"}


def test_rank_column_and_unit_cells():
    rows = "\n".join(f"{i} | {c} | {v} yrs" for i, (c, v) in enumerate(
        [("Monaco", 86.5), ("Japan", 84.0), ("China", 78.0), ("Brazil", 75.8), ("India", 72.0), ("Nigeria", 54.5)], 1))
    p = types.SimpleNamespace(passage_id="S17.1", source_id="S17", kind="table", reliability=0.8, published="",
                              title="Health indicators", text="TABLE 1 — Data\n# | Country | Life expectancy 2024\n" + rows)
    charts, _ = build_chart_candidates(types.SimpleNamespace(passages=[p]), min_year=2010,
                                       focus=["India", "China", "Brazil", "Nigeria"])
    data = charts[0].spec["series"][0]["data"]
    assert [d["label"] for d in data] == ["China", "Brazil", "India", "Nigeria"] and data[0]["value"] == 78.0


def test_table_words_and_measures_are_not_entities_and_peers_are_out_of_scope():
    table = ("TABLE 1 — Ranking\n# | Country | Value\n1 | Hong Kong | 85\n2 | China | 78\n3 | India | 72\n"
             "4 | Female | 80\n5 | Infant Mortality Rate | 30")
    text = "Hong Kong leads. Hong Kong spends. China grew. India grew. Female Female. Infant Mortality Rate fell."
    store = types.SimpleNamespace(passages=[types.SimpleNamespace(passage_id=f"S{i}.1", source_id=f"S{i}", kind="table",
                                                                  text=table, url=f"https://t{i}.org/x", publisher="",
                                                                  reliability=0.8) for i in range(1, 4)]
                                  + [_p(f"S{i}.2", text, url=f"https://s{i}.com/x") for i in range(4, 8)])
    tiers = entity_tiers(store, tier="in_depth", question="How did health change in India and China?",
                         seeds=["India", "China"], scope="India and China")
    every = {e.name: e for v in tiers.values() for e in v}
    assert "Female" not in every and "Infant Mortality Rate" not in every
    assert every["Hong Kong"].role == "out_of_scope" and every["Hong Kong"].tier != "deep_dive"


def test_latest_year_attribute_is_not_plotted_instead_of_the_measure():
    p = _table("S18.1", "Latest values compared", [
        "Rank | Country or area | Latest year | Value (deaths per 1,000 live births)",
        "1 | Nigeria | 2024 | 115.6", "2 | India | 2024 | 26.6",
        "3 | Brazil | 2024 | 14.2", "4 | China | 2024 | 5.7",
    ])
    charts, _ = build_chart_candidates(types.SimpleNamespace(passages=[p]), min_year=2010,
                                       focus=["India", "China", "Brazil", "Nigeria"])
    assert len(charts) == 1
    chart = charts[0]
    assert "year" not in chart.spec["yLabel"].casefold()
    assert sorted(value for _series, _label, value in chart.points) == [5.7, 14.2, 26.6, 115.6]


def test_machine_code_status_table_is_not_published_as_a_measure_chart():
    p = _table("S22.1", "Indicator data export", [
        "Record ID | Indicator Code | Publish Status",
        "745F475AMRINFECTECOLI | 745F475 | 1", "5DD9606AMRINFECTMRSA | 5DD9606 | 0",
        "A65146DFINPROTECTIONCATATOT10POP | A65146D | 1", "4934B28FINPROTECTIONCATATOT25POP | 4934B28 | 0",
    ])
    charts, dropped = build_chart_candidates(types.SimpleNamespace(passages=[p]))
    assert charts == []
    assert any("data catalog" in row["reason"] for row in dropped)


def test_nonmarket_entity_search_skips_metadata_labels_and_table_only_rows():
    from utils.entity_tiers import EntityEvidence, _non_market_hygiene

    metadata = EntityEvidence(name="Also Known As", table_passages={"S1", "S2"},
                              table_sources={"S1", "S2"})
    table_only = EntityEvidence(name="Battery Storage", table_passages={"S3", "S4"},
                                table_sources={"S3", "S4"})
    named = EntityEvidence(name="India", table_passages={"S5"}, seeded=True)
    clean = _non_market_hygiene([metadata, table_only, named], question="India health outcomes",
                                seeds=["India"], scope="India")
    assert [e.name for e in clean] == ["Battery Storage", "India"]
    tiers = {"deep_dive": [], "compact": clean, "coverage_note": []}
    planned = plan_entity_queries(tiers, cap=6, question="India health outcomes")
    assert planned and {name for name, _query in planned} == {"India"}


def test_dataset_extractor_leaves_chartable_tables_to_the_deterministic_path():
    from utils.dataset_extractor import _already_charted_table, _schema_export

    table = _table("S40.1", "Health outcome by year", [
        "Year | India | China | Brazil | Nigeria",
        "2020 | 70 | 76 | 74 | 53", "2021 | 70.5 | 76.5 | 74.2 | 53.4",
        "2022 | 71 | 77 | 74.5 | 53.8", "2023 | 71.5 | 77.4 | 74.7 | 54.1",
    ])
    catalog = _table("S41.1", "Indicator export", [
        "Record ID | Indicator Code | Publish Status | Description | Unit | Source",
        "745F475AMRINFECTECOLI | 745F475 | 1 | E.coli bloodstream infections | % | WHO",
        "5DD9606AMRINFECTMRSA | 5DD9606 | 0 | MRSA bloodstream infections | % | WHO",
        "A65146DFINPROTECTIONCATATOT10POP | A65146D | 1 | Household spending | % | WHO",
    ])
    assert _already_charted_table(table)
    assert _schema_export(catalog)


def test_dataset_extractor_records_empty_and_failed_model_responses():
    from utils.dataset_extractor import extract_dataset_charts

    passages = [types.SimpleNamespace(passage_id=f"S{i}.1", source_id=f"S{i}", kind="narrative",
                                      text="2019 1 2020 2 2021 3 2022 4 2023 5 2024 6 7 8",
                                      title=f"Source {i}", publisher="Publisher") for i in (1, 2)]

    class Store:
        def search(self, *_args, **_kwargs):
            return [(p, 1.0) for p in passages]

        def get(self, passage_id):
            return next((p for p in passages if p.passage_id == passage_id), None)

    class Gateway:
        async def generate(self, *, section_id, **_kwargs):
            if section_id == "S1.1":
                return types.SimpleNamespace(ok=True, json=lambda: {"datasets": []})
            return types.SimpleNamespace(ok=False, error="network:ConnectError model=gemini")

    _charts, note = asyncio.run(extract_dataset_charts(
        Store(), Gateway(), question="Health outcomes from 2010 through 2024", today="2026-11-12", limit=2))
    assert note["responses"] == 2 and note["successfulResponses"] == 1
    assert note["emptyResponses"] == 1 and note["failedResponses"] == [{"passage": "S2.1", "reason": "ConnectError"}]
