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
    assert links == [{"url": "https://mnre.gov.in/uploads/RE_capacity_2026.xlsx",
                      "text": "Installed RE capacity (state-wise)", "format": "xlsx"},
                     {"url": "https://x.gov.in/a.pdf", "text": "Annual report", "format": "pdf"}]


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
