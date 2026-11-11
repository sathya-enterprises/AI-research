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
from utils.official_data import CATALOG, Dataset, as_source, fits_measure, plan_datasets  # noqa: E402


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
                     f"{18 - (y - 2000) * 0.2:.1f} | 4.4 | {130 - (y - 2000) * 1.5:.1f}" for y in range(2024, 2003, -1))
    return types.SimpleNamespace(passage_id="S29.6", source_id="S29", kind="table", reliability=0.8, published="",
                                 title="Child Mortality Rate by Country: Chart, Data Table and CSV",
                                 text="TABLE 200 — Full data table by year\n"
                                      "Year | World | United States | China | India | Brazil | United Kingdom | Nigeria\n" + rows)


def test_wide_country_year_table_is_narrowed_to_question_and_window():
    focus = table_focus.focus_entities("How did child mortality change in India, China, Brazil and Nigeria?")
    assert focus == ["India", "China", "Brazil", "Nigeria"]
    charts, dropped = build_chart_candidates(types.SimpleNamespace(passages=[_wide()]), min_year=2010, focus=focus)
    spec = charts[0].spec
    assert [s["name"] for s in spec["series"]] == ["China", "India", "Brazil", "Nigeria"]
    labels = [d["label"] for d in spec["series"][0]["data"]]
    assert labels[0] == "2010" and labels[-1] == "2024"            # in-window, oldest first
    assert spec["title"].startswith("Child Mortality Rate: China, India, Brazil")
    assert "larger source table" in spec["basisNote"]


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
