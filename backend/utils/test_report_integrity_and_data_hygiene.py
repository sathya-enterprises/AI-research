"""Regressions from the end-to-end pipeline review (second round).

* A body that no model wrote, that is split into single characters, or that is nearly empty must not be
  presented as a normal PASS report.
* "Sources disagree" compares the same entity: a multi-country sentence gave every number every country.
* The browser's truncation note is reported, and is not parsed as a data row.
* Label cells ("Fund 1", "Q3 2025", "FY24") are not numbers.
* A chart titled from a generic "Value" axis keeps its known metric.
"""
from __future__ import annotations

from utils.chart_lint import metric_name
from utils.disagreements import find_disagreements
from utils.report_sanity import apply_integrity_outcome, fragmented_text, report_integrity_issues
from utils.uploaded_workbook_analytics import (
    _parse_data_number, attachment_analytics_section, build_attachment_analytics, extraction_notes,
)

_HEALTHY = ("## Findings\n\n" + "Internet use in India rose steadily as smartphones became affordable and data prices fell. " * 60)


def test_character_split_body_is_detected():
    broken = "## Quarterly matrix\n" + "\n".join("Sector rotation in Indian stock markets explained")
    assert fragmented_text(broken)
    assert not fragmented_text(_HEALTHY)


def test_integrity_issues_cover_no_llm_writer_fragmented_and_near_empty_bodies():
    assert report_integrity_issues(_HEALTHY, "in_depth", "gemini-3-flash") == []
    no_llm = report_integrity_issues(_HEALTHY, "detailed", "local:evidence-fallback")
    assert [issue["stage"] for issue in no_llm] == ["writer_unavailable"]
    tiny = report_integrity_issues("## Findings\n\nToo short.", "detailed", "gemini")
    assert tiny and tiny[0]["stage"] == "report_integrity"


def test_table_heavy_attachment_report_is_not_flagged_as_too_short():
    table = "\n".join(f"| Strategy {i} | LARGE CAP | Equity growth fund | 12.{i}% |" for i in range(120))
    body = "## Ranked strategies\n\nThe table lists the reported strategies.\n\n| Name | Category | Kind | Return |\n|---|---|---|---:|\n" + table
    assert report_integrity_issues(body, "detailed", "gemini") == []


def test_integrity_outcome_makes_a_draft_and_status_stops_saying_pass():
    payload = {"publicationStatus": "PASS"}
    outcome = {"outcome": "published", "failedStage": None, "blockingCategories": [], "failureSummary": []}
    issues = report_integrity_issues("x", "in_depth", "local:evidence-fallback")
    result = apply_integrity_outcome(outcome, payload, issues)
    assert result["outcome"] == "draft" and result["failedStage"] == "writer_unavailable"
    assert payload["publicationStatus"] == "WARNING" and payload["draftAvailable"] is True
    assert apply_integrity_outcome(outcome, {"publicationStatus": "PASS"}, []) is outcome


def test_multi_entity_sentences_do_not_create_false_disagreements_or_hide_real_ones():
    report = (
        "## Access\nIn 2024, internet usage rates reached 84.46% in Brazil, 64.94% in India and 34.98% in Kenya [S1].\n\n"
        "## Saturation\nIn 2024, internet usage rates stood at 34.98% in Kenya, 64.94% in India and 84.46% in Brazil [S8].\n\n"
        "## Survey\nIn 2024, Kenya's internet usage rate was 34.98% according to the World Bank [S1].\n"
        "In 2024, Kenya's internet usage rate was 45.20% according to a survey [S9].\n"
    )
    found = find_disagreements(report)
    assert [(d["a"]["shown"], d["b"]["shown"]) for d in found] == [("34.98%", "45.20%")]


def test_extraction_note_is_reported_and_not_counted_as_a_row():
    rows = "\n".join(f"Fund {i},{'Equity' if i % 2 else 'Debt'},{(i * 1.37) % 9 + 1:.2f}" for i in range(1, 21))
    context = ("[File: funds.xlsx]\n[Sheet: Returns]\nFund,Category,Return 1 Yr (%)\n" + rows
               + "\n\n[Extraction notes: Returns: extracted 400 of 1,250 rows.]")
    assert extraction_notes(context) == [{"filename": "funds.xlsx", "note": "Returns: extracted 400 of 1,250 rows."}]
    analytics = build_attachment_analytics(context)
    section = attachment_analytics_section(analytics, "S1")
    assert "**Partial attachment:**" in section and "extracted 400 of 1,250 rows" in section
    assert "| funds.xlsx | Returns | 20 | 3 | 0 |" in section
    assert "Partial attachment" not in attachment_analytics_section(build_attachment_analytics(context.split("\n\n[Extraction")[0]), "S1")


def test_label_cells_are_not_numbers_but_currency_and_units_still_are():
    for label in ("Fund 1", "Table 20", "Q3 2025", "FY24", "3 Yr", "Plan B2"):
        assert _parse_data_number(label) is None, label
    assert _parse_data_number("₹1,200") == (1200.0, False)
    assert _parse_data_number("45 crore") == (45.0, False)
    assert _parse_data_number("12.5%") == (12.5, True)
    assert _parse_data_number("(3.4)") == (-3.4, False)
    assert _parse_data_number("2.5x") == (2.5, False)


def test_generic_value_axis_uses_the_chart_metric():
    spec = {"title": "x", "yLabel": "Value", "metric": "Individuals using the Internet (% of population)",
            "series": [{"name": "Brazil", "data": []}, {"name": "India", "data": []}]}
    assert metric_name(spec).startswith("Individuals using the Internet")


def test_pe_guess_never_relabels_a_chart_that_names_its_own_measure():
    from utils.chart_lint import fix_chart
    mobile = {"type": "bar", "title": "Mobile cellular subscriptions (per 100 people): Kenya, Brazil and India",
              "xLabel": "Country", "yLabel": "Mobile cellular subscriptions (per 100 people)", "source": "S17",
              "series": [{"name": "Mobile cellular subscriptions (per 100 people)", "data": [
                  {"label": "Kenya", "value": 126.48}, {"label": "Brazil", "value": 101.93}, {"label": "India", "value": 79.35}]}]}
    fixed, _ = fix_chart(mobile, passage_text="Sector P/E multiples and mobile subscriptions", manifest={"S17": {"title": "x"}})
    assert "P/E" not in fixed["title"] and fixed["title"].count("per 100 people") == 1
    valuation = {"type": "bar", "title": "Valuation", "yLabel": "Value", "source": "S2", "series": [{"name": "Value", "data": [
        {"label": "IT", "value": 28.1}, {"label": "Banks", "value": 14.2}, {"label": "FMCG", "value": 45.0}]}]}
    fixed, _ = fix_chart(valuation, passage_text="Sector P/E ratios as of October", manifest={"S2": {"title": "valuations"}})
    assert fixed["title"].startswith("P/E ratio (x)")


def test_official_dataset_fetch_backs_off_and_retries_transient_errors(monkeypatch):
    import asyncio
    from datetime import date

    import httpx

    from utils import official_data

    calls, sleeps = [], []

    async def flaky(client, entry, entities, years):
        calls.append(1)
        if len(calls) < 3:
            request = httpx.Request("GET", "https://api.worldbank.org/x")
            raise httpx.HTTPStatusError("busy", request=request, response=httpx.Response(503, request=request))
        return {"India": {y: 10.0 + i for i, y in enumerate(years)}, "Kenya": {y: 5.0 + i for i, y in enumerate(years)}}

    async def no_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(official_data, "_fetch_wb", flaky)
    monkeypatch.setattr(official_data.asyncio, "sleep", no_sleep)
    ds = asyncio.run(official_data.fetch_dataset(None, {"id": "wb.internet", "entities": ["IND", "KEN"]},
                                                 start_year=2015, today=date(2026, 11, 14), end_year=2024))
    assert ds is not None and len(calls) == 3 and sleeps == [1.5, 3.0]


def test_reader_text_rounds_republished_api_precision_but_not_ids_urls_or_versions():
    from utils.reader_text import reader_text
    assert reader_text("Brazil began at 58.327953% [S1]; Kenya reached 126.47771.") == "Brazil began at 58.33% [S1]; Kenya reached 126.48."
    assert reader_text("A ratio of 0.000123456 and 1,234.56789 crore.") == "A ratio of 0.000123 and 1,234.57 crore."
    kept = "Cite [S26.11], version 3.14159.2, https://x.org/a/1.23456, 12.5% in 2024."
    assert reader_text(kept) == kept


def test_default_chart_design_note_is_not_shown_as_reader_context():
    from routes.html_report import _render_chart_block

    def chart(rationale):
        return {"type": "bar", "title": "Internet use", "rationale": rationale, "takeaway": "",
                "series": [{"name": "Use", "data": [{"label": "Brazil", "value": 84.5}, {"label": "India", "value": 64.9},
                                                     {"label": "Kenya", "value": 35.0}]}]}

    design_note = _render_chart_block(chart("Use a categorical comparison because the evidence is best read across named items."), 1)
    real_context = _render_chart_block(chart("Brazil leads because mobile broadband arrived earlier."), 1)
    assert "gg-chart-note--why" not in design_note
    assert "gg-chart-note--why" in real_context


def test_table_charts_verify_against_any_fetched_publisher_and_uploaded_files():
    from utils.report_integrity import remove_unverified_table_chart_specs

    page = ("Individuals using the Internet (% of population)\nCountry | 2023 | 2024\n"
            "Brazil | 84.15 | 84.46\nIndia | 60.25 | 64.94\nKenya | 32.07 | 34.98\n")

    def table(rows):
        return {"type": "table", "title": "Internet use by country", "columns": ["Country", "2023", "2024"], "rows": rows}

    real = [["Brazil", "84.15", "84.46"], ["India", "60.25", "64.94"], ["Kenya", "32.07", "34.98"]]
    fake = [["Brazil", "71.00", "73.00"], ["India", "40.00", "45.00"], ["Kenya", "20.00", "22.00"]]
    world_bank = [{"url": "https://data.worldbank.org/indicator/IT.NET.USER.ZS", "pageFetchStatus": "ok", "pageContent": page}]
    upload = [{"name": "internet.xlsx", "evidence_origin": "uploaded_workbook", "text": page}]

    for sources in (world_bank, upload):
        _, kept, _ = remove_unverified_table_chart_specs("[CHART_1]", [table(real)], sources)
        assert len(kept) == 1, sources
        _, kept, _ = remove_unverified_table_chart_specs("[CHART_1]", [table(fake)], sources)
        assert kept == [], sources


def test_large_upload_chart_evidence_is_not_cut_at_16k_in_the_manifest():
    from routes.source_manifest import build_source_registry
    from utils.uploaded_workbook_analytics import UPLOAD_CHART_EVIDENCE_CHARS

    evidence = "\n".join(f"Deterministic uploaded-row observation; row {i}; plotted return: {i * 0.37:.4f}%"
                         for i in range(600))  # ~38k chars, like the real PMS workbook's 35k
    assert 16_000 < len(evidence) < UPLOAD_CHART_EVIDENCE_CHARS
    manifest = build_source_registry([], [{"name": "PMS.xlsx", "evidence_origin": "uploaded_workbook",
                                           "chartEvidenceExcerpt": evidence}])
    assert manifest[0]["chartEvidenceExcerpt"] == evidence


def test_uploaded_worksheets_enter_the_evidence_store_as_verifiable_tables():
    """V2 removed correct workbook sentences: from raw CSV rows the fact sheet could bind no value."""
    from utils.evidence_store import build_evidence_store
    from utils.fact_sheet import FactSheet, lookup
    from utils.uploaded_workbook_analytics import evidence_tables_for_store

    sheet_text = (
        "[Sheet: Table 2]\nAS ON 31 JAN 2026,,,,,,,\n"
        "AMC,INVESTMENT APPROACH,BENCHMARK (TRI),AUM (IN Crs),CATEGORY,RETURNS,,\n"
        ",,,,,1 Mth,1 Yr,5 Yr\n"
        "2POINT2 CAPITAL,LONG TERM VALUE FUND,S&P BSE 500,1835.56,MULTI CAP & FLEXI CAP,0.90%,24.80%,22.70%\n"
        "GREY SKY CAPITAL PRIVATE LIMITED,GSC SMART CORE PORTFOLIO,S&P BSE 500,19.70,MULTI CAP & FLEXI CAP,20.29%,11.10%,NA\n"
        "AEQUITAS INVESTMENT CONSULTANCY PVT LTD,INDIA OPPORTUNITIES PRODUCT,S&P BSE 500,7200.10,SMALL CAP,15.60%,62.34%,49.21%\n"
    )
    converted = evidence_tables_for_store(sheet_text)
    assert "| AMC - investment approach | BENCHMARK (TRI) | AUM (IN Crs) | CATEGORY | 1 Mth return | 1 Yr return | 5 Yr return |" in converted

    rows = [{"_canonicalEvidenceSourceId": "S1", "title": "PMS.xlsx", "pageContent": converted,
             "evidenceOrigin": "uploaded_workbook", "authorityTier": "unknown"}]
    store = build_evidence_store(rows, question="PMS workbook", manifest=[{"id": "S1", "title": "PMS.xlsx"}])
    sheet, pids = FactSheet(store.passages), {p.passage_id for p in store.passages}

    def status(value, sentence):
        return lookup(sheet, value, sign="", unit="%", sentence=sentence, passage_ids=pids).status

    assert status("20.29", "Over one month, Grey Sky Capital Private Limited - GSC Smart Core Portfolio returned 20.29%.") == "ok"
    assert status("62.34", "Over one year, Aequitas Investment Consultancy's India Opportunities Product returned 62.34%.") == "ok"
    assert status("31.20", "Over one year, 2POINT2 Capital's Long Term Value Fund returned 31.20%.") != "ok"


def test_numbers_inside_names_are_not_checked_as_statistics():
    from utils.number_policy import _is_name_number

    evidence = {"S1.1": "RIGHT HORIZONS | ALPHABOTS MIDCAP 29 | S&P BSE 500 | MID CAP | -1.96%\n"
                        "ALCHEMY CAPITAL MANAGEMENT | ALPHA 100 | NIFTY 50 | LARGE CAP | 3.10%"}

    def name_number(sentence, token):
        at = sentence.find(token)
        return _is_name_number(sentence, at, at + len(token), "", evidence, set())

    assert name_number("The RIGHT HORIZONS - ALPHABOTS MIDCAP 29 strategy reports returns.", "29")
    assert name_number("Strategies like ALCHEMY - ALPHA 100 or FRACTAL lack AUM.", "100")
    assert not name_number("Kenya had 29 operators.", "29")
    assert not name_number("MIDCAP 29% higher.", "29")


def test_server_calculation_lines_become_tables_the_fact_sheet_can_bind():
    from utils.evidence_store import build_evidence_store
    from utils.fact_sheet import FactSheet, lookup
    from utils.uploaded_workbook_analytics import calculation_lines_as_tables

    lines = "\n".join(
        f"Deterministic calculation from P.xlsx; mean 1-year reported PMS return by category; label: {label}; "
        f"value: {value}%; observations: {n}; missing values excluded; calculated from the attached workbook rows."
        for label, value, n in [("ETF", "15.1767", 3), ("MULTI ASSET", "13.9686", 22), ("DEBT", "9.1746", 13)])
    tables = calculation_lines_as_tables(lines)
    assert "| ETF | 15.1767% | 3 |" in tables
    store = build_evidence_store([{"_canonicalEvidenceSourceId": "S1", "title": "P.xlsx", "pageContent": lines + "\n\n" + tables,
                                   "evidenceOrigin": "uploaded_workbook"}], question="PMS", manifest=[{"id": "S1", "title": "P.xlsx"}])
    sheet, pids = FactSheet(store.passages), {p.passage_id for p in store.passages}
    assert lookup(sheet, "15.1767", sign="", unit="%", sentence="ETF: mean 1-year return of 15.1767%.", passage_ids=pids).status == "ok"
    assert lookup(sheet, "19.9999", sign="", unit="%", sentence="ETF: mean 1-year return of 19.9999%.", passage_ids=pids).status != "ok"


def test_table_numbering_keeps_worksheet_names_that_look_like_table_references():
    from utils.chart_lint import number_tables

    report = ("## Coverage\nThe workbook spans Table 2 through Table 26; Table 20 holds benchmark rows [S1].\n\n"
              "| Sheet / Section | Data Rows Analyzed |\n|---|---|\n| Table 2 | 25 |\n| Table 3 | 25 |\n| Table 20 | 3 |\n\n"
              "Source: [S1]\n")
    out, _, _ = number_tables(report)
    assert "Table 2 through Table 26; Table 20 holds" in out
    assert "| Table 3 | 25 |" in out and "the table" not in out

    web = ("## Returns\nAs Table 1 shows, returns vary. See Table 7 too.\n\n"
           "| Horizon | Median |\n|---|---|\n| 1 Mth | -3.17% |\n| 1 Yr | 3.67% |\n\nSource: [S1]\n")
    out, _, _ = number_tables(web)
    assert "See the table too." in out  # a dangling reference in an ordinary report is still neutralized


def test_tabulated_calculation_lines_replace_their_prose_in_the_store_text():
    from utils.uploaded_workbook_analytics import tabulate_calculation_lines

    lines = "\n".join(
        f"Deterministic calculation from P.xlsx; mean 1-year reported PMS return by category; label: {label}; "
        f"value: {value}%; observations: {n}; missing values excluded."
        for label, value, n in [("ETF", "15.1767", 3), ("DEBT", "9.1746", 13)])
    single = "Deterministic calculation from P.xlsx; total AUM; label: all; value: 152016.88; observations: 219."
    out = tabulate_calculation_lines(lines + "\nSheet: Table 2 | Row: 5 | raw row\n" + single)
    assert "| DEBT | 9.1746% | 13 |" in out
    assert "label: DEBT" not in out           # the prose form is gone, so a section can only quote the table
    assert single in out and "Sheet: Table 2 | Row: 5" in out  # ungrouped lines and row evidence stay


def test_number_policy_keeps_a_value_verified_elsewhere_in_the_store_and_cites_it():
    from types import SimpleNamespace

    from utils.fact_sheet import FactSheet
    from utils.number_policy import apply_number_policy

    section = {"S1.1": "Workbook overview: 506 strategies across 25 worksheets."}
    elsewhere = SimpleNamespace(passage_id="S1.7", text=(
        "TABLE — Table 3\n| AMC - investment approach | AUM (IN Crs) | 5 Yr return |\n|---|---|---|\n"
        "| BUOYANT CAPITAL PVT LTD - OPPORTUNITIES | 8402.39 | 27.10% |\n"
        "| STALLION ASSET PVT LTD - CORE FUND | 6361.36 | 25.40% |\n"))
    store_sheet = FactSheet([SimpleNamespace(passage_id=k, text=v) for k, v in section.items()] + [elsewhere])
    body = ("Buoyant Capital Pvt Ltd - Opportunities holds 8402.39 AUM (IN Crs) [S1.1]. "
            "Stallion Asset Pvt Ltd - Core Fund holds 9999.99 AUM (IN Crs) [S1.1].")

    without = apply_number_policy(body, passage_texts=section, allowed_numbers=set())
    assert len(without.removed) == 2
    result = apply_number_policy(body, passage_texts=section, allowed_numbers=set(), fallback_sheet=store_sheet)
    assert "8402.39" in result.body and "S1.7" in result.body
    assert "9999.99" not in result.body and len(result.removed) == 1


def test_fiscal_year_ranges_set_the_requested_window():
    from datetime import date

    from utils.data_angles import requested_year_bounds

    today = date(2026, 11, 15)
    assert requested_year_bounds("EV sales grew from FY2021 to FY2025", today) == (2020, 2025)
    assert requested_year_bounds("FY21 to FY25 trend", today) == (2020, 2025)
    assert requested_year_bounds("FY2020-21 to FY2024-25", today) == (2020, 2025)
    assert requested_year_bounds("between FY 2019 and FY 2023", today) == (2018, 2023)
    assert requested_year_bounds("from 2015 to 2024", today) == (2015, 2024)


def test_government_domains_are_never_treated_as_brand_lookalikes():
    from utils.source_quality import authority

    assert authority("https://www.beeindia.gov.in/ev-charging")[0] == "primary_official"
    assert authority("https://bseindla.com/notice")[0] == "denylisted"


def test_single_country_official_dataset_chart_passes_pre_writing_verification():
    """Live run (EV sales in India): every one-column official chart was rejected for its measure/entity,
    in-window points were called out of window by their neighbouring rows, and the caption regex held
    mojibake dashes, so the report had no charts."""
    import routes.report as R
    from routes.source_manifest import build_source_registry
    from utils.chart_evidence_audit import audit_chart_evidence_consistency
    from utils.evidence_store import build_evidence_store
    from utils.official_data import official_charts
    from utils.section_charts import clip_to_question_window

    table = ("OFFICIAL DATASET — Our World in Data, Our World in Data grapher: Electric car sales (cars) [electric-car-sales]\n"
             "Retrieved from the Our World in Data public API on 2026-11-15. Latest observation: 2025.\n"
             "TABLE 1 — Electric car sales (cars), 2020–2025\nYear | India\n2020 | 3,143\n2021 | 12,050\n"
             "2022 | 48,023\n2023 | 82,270\n2024 | 92,095\n2025 | 170,100")
    rows = [{"title": "Our World in Data: Electric car sales (cars), 2020-2025", "url": "https://ourworldindata.org/grapher/electric-car-sales",
             "publisher": "Our World in Data", "fullContent": table, "pageContent": table, "pageFetchStatus": "ok",
             "evidenceOrigin": "publisher_page", "sourceType": "Official dataset", "kind": "Official dataset",
             "authorityTier": "primary_official", "reliabilityScore": 0.95, "retrievedAt": "2026-11-15", "dataPeriod": "2020-2025",
             "officialDataset": {"id": "owid.ev_car_sales", "code": "electric-car-sales", "unit": "cars", "entities": ["India"],
                                 "years": [2020, 2021, 2022, 2023, 2024, 2025], "fit": "direct",
                                 "measure": "annual electric vehicle sales volume"}}]
    question = "How have electric vehicle sales in India grown from FY2021 to FY2025?"
    manifest = build_source_registry(rows, [])
    store = build_evidence_store(rows, question=question, manifest=manifest)
    charts = official_charts(store, question)
    assert charts
    assert clip_to_question_window(charts[0], question) == ""  # calendar 2020 and 2025 straddle the FY window
    assert [d["label"] for d in charts[0].spec["series"][0]["data"]] == ["2021", "2022", "2023", "2024"]
    specs = [dict(charts[0].spec, evidencePassages=charts[0].passage_ids)]
    R._register_v2_chart_evidence(specs, store)
    public = R._merge_source_evidence_statuses(manifest, R._attach_audit_source_evidence(manifest, rows, ""))
    result = audit_chart_evidence_consistency("", specs, public, question=question, require_publisher_verification=True)
    assert result.get("chartEvidenceFailures") == []


def test_chart_relevance_matches_plurals_and_two_letter_acronyms():
    from utils import chart_evidence_audit as A

    question = ("How have electric vehicle sales in India grown from FY2021 to FY2025, and which state EV policies "
                "drove the change?")
    fys = ["2020-21", "2021-22", "2022-23", "2023-24", "2024-25"]

    def chart(title, name, values):
        return {"type": "bar", "title": title, "xLabel": "Financial Year", "sourceIds": ["S17"],
                "series": [{"name": name, "data": [{"label": fy, "value": v} for fy, v in zip(fys, values)]}]}

    ev = chart("EV as a % of total vehicles registered (%), 2020-21 to 2024-25", "EV share", [0.82, 2.49, 5.3, 6.82, 7.31])
    wheat = chart("Wheat yield by state (tonnes), 2020-21 to 2024-25", "Wheat yield", [3.1, 3.2, 3.4, 3.5, 3.6])
    _, kept, removed = A._dedupe_chart_specs("[CHART_1]\n[CHART_2]", [ev, wheat], question, sources=[])
    assert [c["title"] for c in kept] == [ev["title"]] and removed == 1
    assert {"ev", "vehicle"} <= A._chart_metric_tokens(ev)


def test_split_fiscal_year_labels_are_read_as_fiscal_years():
    from utils.chart_evidence_audit import _period_ranges, _periods_outside_question_window

    def months(label):
        (start, end, _), = _period_ranges(label)
        return (start // 12, start % 12 + 1), (end // 12, end % 12 + 1)

    assert months("2020-21") == ((2020, 4), (2021, 3))
    assert months("FY 2022-23") == ((2022, 4), (2023, 3))  # was read as FY2022 (a year early)
    question = "EV sales in India from FY2021 to FY2025"
    assert not _periods_outside_question_window(question, ["2020-21"], [])  # FY2021 itself
    assert _periods_outside_question_window(question, ["2019-20"], [])


def test_trimmed_fiscal_year_chart_title_names_the_kept_span_once():
    from utils.section_charts import _title_with_kept_periods

    assert (_title_with_kept_periods("Segment: e2w, e3w and e4w, 2019-20 to 2024-25", ["2020-21", "2024-25"], {"2019-20"})
            == "Segment: e2w, e3w and e4w, 2020-21 to 2024-25")


def test_a_repeated_table_row_is_accepted_when_any_copy_sits_under_the_right_column():
    from utils.chart_evidence_audit import _table_point_column_matches

    glued = ("Year | Something else | Other\n"                       # another table's header glued above
             "2021-22 | 179.86 | 4.59 | 2.49%\n\n"
             "Financial Year | No. of ICE Vehicles registered | No. of EV Vehicles registered | EV share\n"
             "2021-22 | 179.86 | 4.59 | 2.49%\n")
    point = {"evidenceValue": "4.59", "evidenceColumn": "No. of EV Vehicles registered", "evidencePeriod": "2021-22"}
    assert _table_point_column_matches(point, "EV", "2021-22", 4.59, "2021-22 | 179.86 | 4.59 | 2.49%", glued) is True
    # every copy rejects a value that is not in the named column -> still rejected
    wrong = {"evidenceValue": "2.49%", "evidenceColumn": "No. of EV Vehicles registered", "evidencePeriod": "2021-22"}
    assert _table_point_column_matches(wrong, "EV", "2021-22", 2.49, "2021-22 | 179.86 | 4.59 | 2.49%", glued) is False


def test_gate_healing_drops_charts_pruned_below_three_points():
    from utils import gate_healing

    charts = [{"type": "line", "title": "ICE vs EV", "series": [{"name": "EV", "data": [
        {"label": y, "value": v, "evidenceSource": "S1"} for y, v in [("2021-22", 4.59), ("2022-23", 11.83), ("2023-24", 16.81)]]}]}]
    pub = {"chartEvidenceFailures": [{"chartIndex": 1, "seriesIndex": 0, "pointIndex": 0, "label": "2021-22",
                                      "chartTitle": "ICE vs EV", "reason": "x"}]}
    actions = []
    text, kept = gate_healing._remedy_publication("Intro.\n\n[CHART_1]\n", charts, pub, actions, force=False)
    assert kept == [] and "[CHART_1]" not in text
    assert any("fewer than 3 points" in a["detail"] for a in actions)


def test_ev_column_does_not_fuzzily_match_the_ice_column():
    from utils.chart_evidence_audit import _table_point_column_matches

    table = ("Financial Year | No. of ICE Vehicles registered | No. of EV Vehicles registered | EV share\n"
             "2021-22 | 179.86 | 4.59 | 2.49%\n")
    ice_value = {"evidenceValue": "179.86", "evidenceColumn": "No. of EV Vehicles registered", "evidencePeriod": "2021-22"}
    assert _table_point_column_matches(ice_value, "EV", "2021-22", 179.86, "2021-22 | 179.86 | 4.59 | 2.49%", table) is False
    ev_value = {"evidenceValue": "4.59", "evidenceColumn": "No. of EV Vehicles registered", "evidencePeriod": "2021-22"}
    assert _table_point_column_matches(ev_value, "EV", "2021-22", 4.59, "2021-22 | 179.86 | 4.59 | 2.49%", table) is True


_PIB_FAME_TABLE = (
    "TABLE 1\nSegment | No. of Vehicles incentivized | Incentive Disbursed (Rs. crore)\n"
    "e-2W | 14,28,882 | 4911 .81\ne-3W | 1,64,718 | 1109.86\ne-4W | 22,615 | 536.52\n"
    "Total | 16,16,215 | 6,558.19\n"
)


def _fame_audit(report: str):
    from routes.report import _attach_audit_source_evidence, _source_evidence_quality_audit

    url = "https://pib.gov.in/pressreleasepage.aspx?prid=2222608"
    manifest = [{"id": "S8", "title": "Press Release Page | Press Information Bureau", "url": url}]
    enriched = [{**manifest[0], "pageContent": _PIB_FAME_TABLE, "pageFetchStatus": "ok", "evidenceOrigin": "publisher_page"}]
    rows = _attach_audit_source_evidence(manifest, enriched, report)
    return rows[0], _source_evidence_quality_audit(rows)


def test_short_rows_of_a_fetched_publisher_table_verify_the_claims_that_cite_them():
    # Observed run: the PIB FAME II table rows are shorter than a prose sentence, and the page
    # renders 4911.81 as "4911 .81"; every figure was reported as missing from S8 and removed.
    row, audit = _fame_audit(
        "Under FAME II, e-2Ws received Rs 4,911.81 crore in incentives supporting 14,28,882 vehicles [S8]. "
        "e-3Ws accounted for Rs 1,109.86 crore supporting 1,64,718 vehicles [S8].")
    assert row["claimEvidenceMatched"] is True and row["unmatchedNumericClaims"] == []
    assert not audit.get("mismatchedCitationGroups")
    # The auditor's publisher excerpt now carries the rows it is asked to check.
    assert "1109.86" in row["publisherEvidenceExcerpt"] and "4911.81" in row["publisherEvidenceExcerpt"]


def test_a_shared_unit_word_is_not_a_shared_subject():
    _row, audit = _fame_audit("India's software exports reached Rs 1,109.86 crore [S8].")
    assert audit.get("mismatchedCitationGroups")
    _row, audit = _fame_audit("Under FAME II, e-2Ws received Rs 4,811.81 crore in incentives [S8].")
    assert audit.get("mismatchedCitationGroups")


def test_auditor_excerpt_keeps_each_matched_row_under_its_header():
    from routes.report import _audit_excerpt_for_claims

    text = ("Intro sentence about the scheme.\nOther | Table\nA | 1 | 2\nTABLE 1\n"
            "Segment | No. of Vehicles incentivized | Incentive Disbursed (Rs. crore)\n"
            "e-2W | 14,28,882 | 4911.81\ne-3W | 1,64,718 | 1109.86")
    excerpt = _audit_excerpt_for_claims(text, "S8", [{"sourceIds": ["S8"], "claimTokens": ["inr_crore:1109.86"]}], 2400)
    assert excerpt.splitlines() == [
        "Segment | No. of Vehicles incentivized | Incentive Disbursed (Rs. crore)", "e-3W | 1,64,718 | 1109.86"]


def test_rows_of_a_borderless_pdf_table_verify_the_claims_that_cite_them():
    # Observed run: a PIB PDF extracted as "e-4 Wheelers 22,644" lines (no pipes); every
    # line was shorter than a sentence and dropped, so the FAME II counts were flagged.
    from routes.report import _attach_audit_source_evidence, _source_evidence_quality_audit

    page = ("The scheme focuses on increasing EV adoption and strengthening the charging infrastructure.\n\n"
            "EVs Supported under FAME-II as of June 2025\nCategory Number of EVs Supported\n"
            "e-2 Wheelers 14,35,065\ne-3 Wheelers 1,65,029\ne-4 Wheelers 22,644\ne-Buses 5,165 (6,862 sanctioned)\n")
    manifest = [{"id": "S4", "title": "Final Green Mobility", "url": "https://static.pib.gov.in/doc.pdf"}]
    enriched = [{**manifest[0], "pageContent": page, "pageFetchStatus": "ok", "evidenceOrigin": "publisher_page",
                 "pageFetchRelevanceMatched": True}]

    def audit(report):
        return _source_evidence_quality_audit(_attach_audit_source_evidence(manifest, enriched, report))

    assert not audit("Under FAME II, 22,644 e-4Ws and 1,65,029 e-3Ws were supported as of June 2025 [S4].").get(
        "mismatchedCitationGroups")
    assert audit("Under FAME II, 23,644 e-4Ws were supported as of June 2025 [S4].").get("mismatchedCitationGroups")
    # A page rejected by the relevance check still verifies nothing.
    enriched[0]["pageFetchRelevanceMatched"] = False
    assert audit("Under FAME II, 22,644 e-4Ws were supported as of June 2025 [S4].").get("mismatchedCitationGroups")


_EV_PASSAGES = {
    "S20.1": ("TABLE 1\nCalendar Year | Total EV registered on Vahan | Total Vehicles registered on Vahan | % Share of EVs\n"
              "(A) | (B) | (C)=(A)/(B)\n2024 | 19,50,490 | 2,62,07,453 | 7.44%"),
    "S20.2": ("TABLE 1\nFinancial Year | No. of ICE Vehicles registered | No. of EV Vehicles registered | "
              "EV as a % of total vehicles registered\n2020-21 | 173.79 | 1.43 | 0.82%\n2021-22 | 179.86 | 4.59 | 2.49%"),
    "S43.2": ("TABLE 7\nSr. No. | State | Number of Manufacturing Units\n1 | Andhra Pradesh | 4\n"
              "10 | Maharashtra | 77\nTotal | 257"),
    "S12.1": ("TABLE 1\nSl. No. | Segment | No. of EVs for which incentive paid\n1 | e-2 wheelers | 14,28,009\n"
              "2 | e-3 wheelers | 1,64,523\nTotal | 16,15,080"),
    "S8.1": ("TABLE 2\nSr. No. | Category | 2018 Total | 2018 EV | 2018 % | 2019 Total | 2019 EV | 2019 %\n"
             "4 | Goods Vehicles | 8,86,047 | 658 | 0.07 | 7,99,661 | 54 | 0.01"),
}


def _number_policy(body: str):
    from utils.number_policy import apply_number_policy

    return apply_number_policy(body, passage_texts=_EV_PASSAGES, allowed_numbers=set(), section_id="t")


def test_number_policy_keeps_values_from_official_tables_it_used_to_misread():
    # Observed run: 19 of 20 removed sentences quoted these tables correctly. A column-code row
    # "(A) | (B)" replaced the header, a year-first row was labelled by its first value, a
    # "Total | 257" row was not read as a table row, and "EVs" / "e-2Ws" matched nothing.
    body = ("By 2024, Vahan recorded 19,50,490 EV registrations, a 7.44% share of EVs [S20.1]. "
            "The share of EVs rose to 2.49% in FY2022 [S20.2]. "
            "Nationally there are 257 manufacturing units [S43.2]. "
            "FAME II supported 14,28,009 e-2Ws and 16,15,080 vehicles in all [S12.1].")
    result = _number_policy(body)
    assert result.removed == [], result.removed


def test_number_policy_reads_the_subject_from_the_previous_sentence_or_list_lead_in():
    body = ("EV penetration is EV registrations as a share of total vehicles registered [S20.2]. "
            "This metric rose from a marginal 0.82% in FY2021 [S20.2].\n\n"
            "Goods Vehicles registrations recorded in central registries show:\n"
            "- 2018: 8,86,047 total (658 EVs; 0.07% share) [S8.1]\n- 2019: 7,99,661 total (54 EVs; 0.01% share) [S8.1]")
    assert _number_policy(body).removed == []
    # The right value under the wrong subject is still removed.
    wrong = _number_policy("Passenger car sales reached 8,86,047 units [S8.1].")
    assert [r["number"] for r in wrong.removed] == ["8,86,047"]


_VALUE_PASSAGES = {
    "S1.1": ("Individuals using the Internet (% of population)\nYear | India | Kenya\n2019 | 29.52 | 22.67\n2020 | 43.41 | 24.72"),
    "S2.1": ("MSP of Kharif crops (Rs per quintal)\nYear | Paddy common | Maize\n2018-19 | 1750 | 1700\n2019-20 | 1815 | 1760"),
    "S3.1": "State-wise vehicles supported\n1 Maharashtra 4,28,253 2 Uttar Pradesh 2,78,178 3 Karnataka 2,67,127",
}


def _value_policy(body: str):
    from utils.number_policy import apply_number_policy

    return apply_number_policy(body, passage_texts=_VALUE_PASSAGES, allowed_numbers=set(), section_id="t")


def test_number_policy_accepts_values_the_evidence_pins_down_without_shared_words():
    # Benchmark (30 prompts): half the "another metric or entity" removals were the writer's own data
    # rows ("2018-19 1750 1700") or paraphrases ("penetration" for "Individuals using the Internet")
    # of values whose row period or specificity identifies the fact.
    body = ("India's penetration jumped to 29.52% in 2019 and 43.41% in 2020 [S1.1].\n\n"
            "2018-19 1750 1700 [S2.1]\n\n2019-20 1815 1760 [S2.1]")
    assert _value_policy(body).removed == []


def test_number_policy_still_removes_pinned_values_under_the_wrong_row_or_year():
    # Kenya's value attributed to India (a sibling column of the same table).
    assert _value_policy("India's penetration reached 22.67% in 2019 [S1.1].").removed
    # A data row whose period is another row's period.
    assert _value_policy("2019-20 1750 [S2.1]").removed
    # A state's value presented as the national total (flattened table: the name before it owns it).
    assert _value_policy("Nationally, 2,78,178 vehicles were supported under the scheme [S3.1].").removed
    assert not _value_policy("Uttar Pradesh had 2,78,178 vehicles supported [S3.1].").removed


def test_replay_capture_reruns_the_checks_from_a_saved_capture():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "replay_capture", Path(__file__).resolve().parents[1] / "scripts" / "replay_capture.py")
    replay = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(replay)

    url = "https://pib.gov.in/pressreleasepage.aspx?prid=1"
    page = "TABLE 7\nSr. No. | State | Number of Manufacturing Units\n10 | Maharashtra | 77\nTotal | 257"
    capture = {
        "question": "How many EV manufacturing units are there in India?",
        "llmCalls": [{"purpose": "section", "sectionId": "sec-1", "ok": True,
                      "llmResponse": "Nationally there are 257 manufacturing units [S1.1]. Cars sold reached 77 lakh [S1.1]."}],
        "stages": {
            "evidence_store": {"data": {"passages": {"S1.1": {"text": page}}}},
            "audit_sources": {"data": {"sources": [{"title": "PIB", "url": url, "pageContent": page,
                                                    "pageFetchStatus": "ok", "evidenceOrigin": "publisher_page",
                                                    "pageFetchRelevanceMatched": True}]}},
            "final_output": {"data": {"report": "Nationally there are 257 manufacturing units [S1].", "charts": [],
                                      "sources": [{"id": "S1", "title": "PIB", "url": url}], "audit": {}}},
        },
    }
    numbers = replay.replay_number_policy(capture)
    assert [r["number"] for r in numbers["replayed"]] == ["77"]
    sources = replay.replay_source_evidence(capture)
    assert sources["exactSourceRows"] is True and sources["replayed"] == []
    assert replay.replay_charts(capture, sources["audited"])["failures"] == []


def test_calculation_inputs_may_be_a_passage_count_written_in_lakh():
    from utils.number_policy import number_pool, verify_calculation_labels

    pool = number_pool({"S3.1": "FAME II aimed to support 10 lakh e-2Ws; 14,28,009 e-2Ws were supported."})
    assert verify_calculation_labels("e-2Ws reached 142.8% (calculated: 1428009 / 1000000) of target.", pool) == ""
    # An input that is in no passage, in any form, still fails.
    assert verify_calculation_labels("e-2Ws reached 71.4% (calculated: 1428009 / 2000000) of target.", pool)


def test_a_ranked_table_row_quoted_without_its_serial_cell_matches_its_column():
    # Observed run: every state-wise and corridor-wise chart failed because the chart quotes
    # "UTTAR PRADESH | 6,65,247 | ..." for the source row "1 | UTTAR PRADESH | 6,65,247 | ...".
    from utils.chart_evidence_audit import _table_point_column_matches

    table = ("TABLE 1\nSl. No. | State/ UT | Total EV | Total Vehicles Sold | % Share of EV in Total Vehicles Sold\n"
             "1 | UTTAR PRADESH | 6,65,247 | 1,53,17,699 | 4.34%\n")
    quote = "UTTAR PRADESH | 6,65,247 | 1,53,17,699 | 4.34%"
    point = {"evidenceValue": "6,65,247", "evidenceColumn": "Total EV", "evidenceRowLabel": "UTTAR PRADESH"}
    assert _table_point_column_matches(point, "Total EV", "UTTAR PRADESH", 665247, quote, table) is True
    wrong = {"evidenceValue": "1,53,17,699", "evidenceColumn": "Total EV", "evidenceRowLabel": "UTTAR PRADESH"}
    assert _table_point_column_matches(wrong, "Total EV", "UTTAR PRADESH", 15317699, quote, table) is False


def test_a_two_column_row_reads_its_measure_from_a_caption_above_notes():
    from utils.chart_evidence_audit import _value_windows

    text = ("TABLE 1 — Electric car sales (cars), 2020–2025\nRetrieved from the Our World in Data public API.\n"
            "Empty cells are years with no published value.\nYear | India\n2020 | 3,143\n2021 | 12,050")
    (_own, context), = _value_windows("12,050", text)
    assert "Electric car sales" in context


def test_a_point_from_a_dated_column_is_dated_by_that_column_not_its_neighbour():
    from utils.chart_evidence_audit import audit_chart_evidence_consistency

    page = ("TABLE 1\n# | Name of Applicant | Category | Incentive Disbursed in FY2024-25 | Incentive Disbursed in FY2025-26\n"
            "1 | Tata Motors Ltd. | OEM | 142.13 | 403.18\n3 | Mahindra & Mahindra Ltd. | OEM | 104.08 | 283.82\n"
            "5 | Ola Electric Tech | OEM | 73.74 | 366.78")
    source = {"id": "S1", "title": "PLI Auto disbursements", "url": "https://pib.gov.in/x", "pageContent": page,
              "publisherEvidenceExcerpt": page, "pageFetchStatus": "ok", "evidenceOrigin": "publisher_page",
              "evidenceExcerptOrigin": "publisher_page", "pageFetchRelevanceMatched": True,
              "chartEvidenceExcerpt": page, "publisher_table_chart_evidence_verified": True}
    rows = [("Tata Motors Ltd.", "142.13", "Tata Motors Ltd. | OEM | 142.13 | 403.18"),
            ("Mahindra & Mahindra Ltd.", "104.08", "Mahindra & Mahindra Ltd. | OEM | 104.08 | 283.82"),
            ("Ola Electric Tech", "73.74", "Ola Electric Tech | OEM | 73.74 | 366.78")]
    chart = {"type": "bar", "title": "Incentive Disbursed in FY2024-25 (INR crore)", "unit": "INR crore", "sourceIds": ["S1"],
             "source": ["S1"], "series": [{"name": "Incentive Disbursed in FY2024-25", "data": [
                 {"label": name, "value": float(value), "evidenceSource": "S1", "evidenceQuote": quote, "evidenceValue": value,
                  "evidenceColumn": "Incentive Disbursed in FY2024-25", "evidenceRowLabel": name} for name, value, quote in rows]}]}
    result = audit_chart_evidence_consistency("", [chart], [source], question="EV policy in India from FY2021 to FY2025",
                                              require_publisher_verification=True)
    assert not [f for f in result.get("chartEvidenceFailures") or [] if "time window" in str(f.get("reason"))]


def test_segment_labels_keep_their_numbers_and_calendar_year_columns_are_periods():
    from types import SimpleNamespace

    from utils.section_charts import charts_from_passage

    passage = SimpleNamespace(
        passage_id="S16.1", source_id="S16", kind="table", url="", title="PIB", publisher="", published="",
        authority_tier="primary_official", reliability=1.0, numbers=[], parent_source_id="", parent_data_context="",
        text=("TABLE 1\nCATEGORY | Calendar Year 2022 | Calendar Year 2023 | % Growth\n2 Wheelers | 6,31,464 | 8,59,376 | 36.09%\n"
              "3 Wheelers | 3,52,710 | 5,82,793 | 65.23%\nCommercial Vehicles | 2,649 | 5,673 | 114.16%"))
    (chart,) = charts_from_passage(passage, 0, None)
    assert [s["name"] for s in chart.spec["series"]] == ["2 Wheelers", "3 Wheelers", "Commercial Vehicles"]
    assert [d["label"] for d in chart.spec["series"][0]["data"]] == ["Calendar Year 2022", "Calendar Year 2023"]
    assert chart.spec["unit"] != "years" and "(years)" not in chart.spec["title"] and "CATEGORY" not in chart.spec["title"]


def test_a_requested_policy_timeline_is_built_in_the_policy_sections():
    from datetime import date

    from utils.question_asks import parse_asks
    from utils.report_pipeline_v2 import _timeline_subject_matches
    from utils.section_layout import timeline_from_body

    asks = parse_asks("Which government policies (FAME II, PLI) drove EV adoption? Show the yearly trend and the "
                      "policy timeline.", date(2026, 11, 15))
    assert asks.timeline_requested and asks.timeline_subject == "policy"
    assert any(a.text == "policy timeline" for a in asks.asks)
    assert _timeline_subject_matches(asks.timeline_subject, "Central Government Policy Drivers: FAME II")
    assert _timeline_subject_matches(asks.timeline_subject, "Supply-Side Incentives: PLI for Auto")
    assert not _timeline_subject_matches(asks.timeline_subject, "Segment-Wise Annual EV Sales Trends")
    assert not parse_asks("How did EV sales grow from FY2021 to FY2025?", date(2026, 11, 15)).timeline_requested

    body = ("FAME II was notified effective from April 1, 2019 with an outlay of Rs 10,000 crore [S3]. "
            "The PLI scheme for Auto was approved on September 15, 2021 [S5]. The ACC PLI was approved on May 12, 2021 [S5].")
    box = timeline_from_body(body)
    assert box.startswith("> [!TIMELINE] Apr 2019 — FAME II") and "May 2021" in box and "Sep 2021" in box
    assert " ." not in box


def test_an_aspnet_page_wrapped_in_a_form_keeps_its_text_but_a_search_box_is_dropped():
    # Observed run: pib.gov.in wraps every release in <form id="form1">; removing all forms removed
    # the release text, so PIB pages came back as "empty_extraction" or as their tables only.
    from utils.source_extractor import _extract_html

    paragraphs = "".join(f"<p>FAME II paragraph {i}: 16.15 lakh electric vehicles were incentivised under the scheme.</p>"
                         for i in range(4))
    html = ("<html><head><title>Press Release Page | Press Information Bureau</title></head><body>"
            "<form id='form1'><div class='innner-page-main-about-us-content-right-part'>"
            f"<h2>16.15 LAKH ELECTRIC VEHICLES INCENTIVISED UNDER FAME-II SCHEME</h2>{paragraphs}</div></form>"
            "<form class='search'><input name='q'><p>Search this site</p></form></body></html>")
    text = _extract_html(html, 8000, True)
    assert "16.15 lakh electric vehicles were incentivised" in text
    assert "Search this site" not in text


def test_entities_differing_only_in_case_are_one_entity():
    # Observed in-depth run: "TAMIL NADU" (a table row) and "Tamil Nadu" (prose) were two deep-dive
    # entities, so the outline had "Tamil Nadu: Deep-Dive" and "TAMIL NADU: Deep-Dive".
    from utils.entity_tiers import EntityEvidence, merge_case_variants

    merged = merge_case_variants([
        EntityEvidence(name="TAMIL NADU", table_passages={"S32.1"}, table_sources={"S32"}),
        EntityEvidence(name="Delhi", good_text_sources={"S5"}),
        EntityEvidence(name="Tamil Nadu", text_passages={"S8.2"}, good_text_sources={"S8"}),
    ])
    assert [e.name for e in merged] == ["Tamil Nadu", "Delhi"]
    tamil = merged[0]
    assert tamil.table_passages == {"S32.1"} and tamil.good_text_sources == {"S8"} and "TAMIL NADU" in tamil.aliases


def test_scheme_name_fragments_and_spelled_out_concepts_are_not_entities():
    from types import SimpleNamespace

    from utils.entity_tiers import _acronym_names_a_concept, _only_inside_hyphenated_names

    store = SimpleNamespace(passages=[SimpleNamespace(text=(
        "The PM E-DRIVE scheme (PM -E DRIVE) and PM E - DRIVE targets. Public Charging Stations (PCS) grew. "
        "Goods and Services Tax (GST) was cut. NITI Aayog and the Ministry of Heavy Industries (MHI) reported."))])
    assert _only_inside_hyphenated_names(store, "DRIVE")
    assert _acronym_names_a_concept(store, "PCS") and _acronym_names_a_concept(store, "GST")
    assert not _acronym_names_a_concept(store, "MHI")  # an organisation keeps its deep-dive eligibility


def test_number_policy_partial_subheader_malformed_numbers_and_lakh_counts():
    from utils.number_policy import apply_number_policy, well_formed_number

    passages = {
        "S100.2": ("TABLE 4\nState | FY20-21 | FY21-22 | FY22-23 | FY23-24 | FY24-25 | Total EVPCS installed | "
                   "Total EVPCS operational\nInstalled | Decommissioned\nMaharashtra | 9 | 129 | 465 | 517 | 957 | 2243 | 1786"),
        "S30.5": "Reimbursement of power cost with an annual cap of INR 15 lakh per enterprise for charging stations.",
    }

    def removed(body):
        return [r["number"] for r in apply_number_policy(body, passage_texts=passages, allowed_numbers=set()).removed]

    # A merged-cell sub-header ("Installed | Decommissioned") no longer erases the column names.
    assert removed("Maharashtra has 1,786 operational EVPCS charging stations [S100.2].") == []
    # "INR 1,500,000" is the source's "INR 15 lakh".
    assert removed("The annual power-cost cap is INR 1,500,000 per enterprise [S30.5].") == []
    # A malformed grouping (a live typo for 5,00,000) is removed.
    assert removed("Retrofitting earns INR 5,00,00 per vehicle [S30.5].") == ["5,00,00"]
    assert well_formed_number("15,00,000") and well_formed_number("1,500,000") and not well_formed_number("1,2345")


def test_a_segment_name_heading_in_toc_and_body_is_not_a_repeated_observation():
    import re

    import routes.pdf as pdf

    source = open(pdf.__file__, encoding="utf-8").read()
    assert "E-2W" in source  # the label rule documents the live case
    # The rule strips segment / hyphenated-code labels before deciding a line holds a number.
    labels = re.compile(r"(?:\b[Ee]-?)?\b[234]\s*-?\s*[Ww](?:s|heelers?)?|\b[A-Za-z]{2,}-\d+[A-Za-z]*")
    assert not re.search(r"\d", labels.sub("", "E-2W: The Primary Growth Engine"))
    assert not re.search(r"\d", labels.sub("", "3Ws and COVID-19 recovery"))
    assert re.search(r"\d", labels.sub("", "E-2W sales reached 11.5 lakh"))


def test_an_untitled_table_and_its_continuation_are_one_chart_source():
    from types import SimpleNamespace

    from utils.section_charts import merge_continuations

    header = "Sl. No. | State/ UT | Total EV | Total Vehicles Sold"
    first = SimpleNamespace(passage_id="S33.1", source_id="S33", kind="table", reliability=1.0, publisher="", title="",
                            url="", published="", text=f"TABLE 1\n{header}\n1 | UTTAR PRADESH | 6,65,247 | 1,53,17,699")
    rest = SimpleNamespace(passage_id="S33.2", source_id="S33", kind="table", reliability=1.0, publisher="", title="",
                           url="", published="", text=f"TABLE 1 (continued)\n{header}\n30 | LADAKH | 191 | 35,896")
    other = SimpleNamespace(passage_id="S33.3", source_id="S33", kind="table", reliability=1.0, publisher="", title="",
                            url="", published="", text=f"TABLE 2 (continued)\n{header}\n31 | GOA | 9 | 100")
    merged = merge_continuations([first, rest, other])
    assert [getattr(p, "merged_ids", [p.passage_id]) for p in merged] == [["S33.1", "S33.2"], ["S33.3"]]


def test_psbs_are_public_sector_banks_even_when_private_sector_banks_spell_psb():
    # Live banking run: "Gross NPAs of PSBs ... 9.11%" was charted as Private Sector Banks.
    from routes.report import build_evidence_dataset

    text = ("Gross NPAs of PSBs have been declining during the last five financial years- reducing from 9.11% to "
            "2.58% between March 2021 to March 2025. The GNPA ratio of PVBs was 1.8% in March 2025.")
    question = "How did Indian banks' GNPA change from FY2021 to FY2025?"
    full = build_evidence_dataset([{"id": "S5", "title": "PIB", "text": text}], question,
                                  targets=["Scheduled Commercial Banks", "Public Sector Banks", "Private Sector Banks"])
    assert {(o.entity, o.value) for o in full.observations} >= {("Public Sector Banks", 9.11), ("Private Sector Banks", 1.8)}
    private_only = build_evidence_dataset([{"id": "S5", "title": "PIB", "text": text}], question,
                                          targets=["Private Sector Banks"])
    assert all(o.value != 9.11 for o in private_only.observations if o.entity == "Private Sector Banks")


def test_a_retrieval_date_in_a_chart_title_is_not_the_data_period():
    from utils.chart_evidence_audit import _periods_outside_question_window

    question = "How did Indian banks' GNPA change from FY2021 to FY2025?"
    assert _periods_outside_question_window(question, ["Q4: 2021-22: 14 sectors, retrieved 15 Nov 2026"], []) == []
    assert _periods_outside_question_window(question, ["Retrieved from the API on 2026-11-15"], []) == []
    assert _periods_outside_question_window(question, ["Credit growth in Q1 2026"], []) == ["Q1 2026"]


def test_markets_run_horizon_headers_and_definition_thresholds():
    from routes.report import _attach_audit_source_evidence
    from utils.section_charts import _header_units

    # "1-Year Returns" is a horizon, not a unit (titles read "1-Year Returns (years)").
    assert _header_units("1-Year Returns") == set() and _header_units("3Yr Change %") == {"%"}
    assert _header_units("Tenure (years)") == {"years"}
    # "a Beta greater than 1" is a definition, not a statistic to find in the source.
    url = "https://example.org/beta"
    rows = _attach_audit_source_evidence(
        [{"id": "S1", "title": "Beta", "url": url}],
        [{"id": "S1", "title": "Beta", "url": url, "pageContent": "Sector betas.", "pageFetchStatus": "ok"}],
        "Nine sectors were aggressive, possessing a Beta greater than 1 [S1].")
    groups = rows[0].get("_citedClaimGroups") or []
    assert all("scalar:1" not in (g.get("claimTokens") or []) for g in groups)


def test_registered_chart_rows_keep_the_tables_caption():
    # Renewables run: three OWID charts passed before writing and were dropped after assembly ("does
    # not establish the chart's stated measure") because only "Year | India" rows were registered.
    from types import SimpleNamespace

    import routes.report as report

    passage = SimpleNamespace(passage_id="S1.1", source_id="S1", kind="table", text=(
        "OFFICIAL DATASET — Our World in Data\nTABLE 1 — Share of electricity from solar (%), 2019–2025\n"
        "Retrieved from the Our World in Data public API on 2026-11-15.\nYear | India\n2020 | 3.38\n2021 | 3.91"))
    store = SimpleNamespace(get=lambda pid: passage if pid == "S1.1" else None)
    report._CHART_EVIDENCE_LINES.set({})
    try:
        report._register_v2_chart_evidence([{"evidencePassages": ["S1.1"]}], store)
        lines = report._CHART_EVIDENCE_LINES.get()["S1"]
    finally:
        report._CHART_EVIDENCE_LINES.set({})
    assert lines[0].startswith("TABLE 1 — Share of electricity from solar") and "2020 | 3.38" in lines


def test_healthcare_run_growth_calculations_and_rate_denominators():
    from types import SimpleNamespace

    from utils.fact_sheet import FactSheet, apply_calculations, compute
    from utils.number_policy import statistic_tokens

    table = ("TABLE 1 — Healthcare expenditure per capita (international $), 2015–2023\n"
             "Year | Indonesia | Philippines | Thailand | Vietnam\n2015 | 293.2 | 268.8 | 608.3 | 361.4\n"
             "2023 | 416.6 | 560.4 | 1,066.8 | 689.6")
    sheet = FactSheet([SimpleNamespace(passage_id="S2.1", text=table)])
    # The writer's usual growth request was refused as "[[UNSUPPORTED_CALC]]" (6 sentences removed).
    assert compute("(560.4 - 268.8) / 268.8", sheet, None) == ("108.5%", "change from 268.8 to 560.4")
    assert compute("(1,066.8 - 608.3) / 608.3 * 100", sheet, None)[0] == "75.4%"
    assert compute("(560.4 - 268.8) / 293.2", sheet, None) is None  # not a change from its own base
    assert "UNSUPPORTED" not in apply_calculations("growth of [[calc: (560.4 - 268.8) / 268.8]].", sheet, None)[0]
    # "per 100,000 live births" is a denominator, not a statistic to look up.
    assert statistic_tokens("An MMR of 34 per 100,000 live births [S19].") == ["34"]
    assert "100,000" in statistic_tokens("The plan adds 100,000 new beds.")


def test_semiconductor_run_disagreement_dates_and_dataset_subject():
    from utils.disagreements import find_disagreements
    from utils.official_data import CATALOG, fits_measure

    # "40% in the mid-1980s ... 7.4% by 2024" was reported as two values for Japan's 2024 share.
    report = ("## Regional trends\n\nJapan, once the dominant player with around 40% market share in the mid-1980s, "
              "saw its share shrink to 7.4% by 2024 [S3].\n\n## Recovery\n\n"
              "Japan's market share declined to just 7.4% by 2024 [S14].")
    assert find_disagreements(report) == []
    # "sales" says how something is measured, not what: EV car sales do not stand in for semiconductor sales.
    assert not fits_measure(CATALOG["owid.ev_car_sales"], "semiconductor sales by end-use application (Automotive)")
    assert fits_measure(CATALOG["owid.ev_car_sales"], "annual electric vehicle sales volume")


def test_attachment_only_audit_is_reconciled_with_the_fact_sheet(monkeypatch):
    # Workbook run: the delivered audit said "FAIL ... not publication-ready" from 30 flags that the
    # final gate had cleared, on a report that was published. Attachment-only audits are reconciled too.
    import asyncio

    import routes.report as R
    import utils.evidence_check as EC

    calls = []
    monkeypatch.setattr(EC, "reconcile_final_audit", lambda audits, text, store: calls.append(store) or [])

    async def fake_review(**_kwargs):
        return {"status": "PASS", "blocking": False, "findings": []}

    monkeypatch.setattr(R, "_gemini_independent_report_audit", fake_review)
    store = object()
    asyncio.run(R._run_independent_report_audit(
        title="PMS", question="Analyse this PMS workbook", report_text="## A\n\nText.", report_tier="detailed",
        charts=[], images=[], sources=[], store=store, attachment_only=True))
    assert calls == [store]


def test_chart_audit_keeps_failures_for_every_chart_in_a_large_batch():
    # The audit returned the first 100 failures of the whole batch: in a 47-chart pre-writing batch the
    # first charts used every slot and each later failing chart looked verified (found by the benchmark).
    from utils.chart_evidence_audit import audit_chart_evidence_consistency

    source = {"id": "S1", "title": "t", "url": "https://example.org/a", "pageContent": "Nothing relevant here.",
              "pageFetchStatus": "ok", "evidenceOrigin": "publisher_page"}
    charts = [{"type": "bar", "title": f"Chart {i}", "sourceIds": ["S1"], "source": ["S1"], "series": [{"name": "x", "data": [
        {"label": f"P{j}", "value": float(j + 1), "evidenceSource": "S1", "evidenceQuote": f"P{j} | {j + 1}"}
        for j in range(30)]}]} for i in range(6)]
    result = audit_chart_evidence_consistency("", charts, [source])
    failing = {f.get("chartIndex") for f in result["chartEvidenceFailures"]}
    assert failing == {1, 2, 3, 4, 5, 6}


def test_calculation_labels_with_parentheses_percent_constants_and_year_like_inputs():
    # Benchmark: removals across healthcare / insurance / inflation / agriculture prompts.
    from utils.number_policy import apply_number_policy, number_pool, verify_calculation_labels

    pool = number_pool({"S1": "The CPI index rose from 35.0 to 41.2. Wheat MSP | 2015 | 2040 | 2275"})
    # A label with parentheses was cut at the first ")" and never verified.
    assert verify_calculation_labels("It rose 17.7% (calculated: (41.2 - 35.0) / 35.0 * 100).", pool) == ""
    assert verify_calculation_labels("It rose 19.9% (calculated: (41.2 - 35.0) / 35.0 * 100).", pool)
    # "× 100" is the percent constant, not an input to find in a passage.
    assert verify_calculation_labels("Ratio 117.7% (calculated: 41.2 / 35.0 × 100).", pool) == ""
    # A price that looks like a year ("MSP ₹2040") is a valid named input; an invented one is not.
    assert verify_calculation_labels("MSP rose 11.5% (calculated: change from 2040 to 2275).", pool) == ""
    assert verify_calculation_labels("MSP rose 9.9% (calculated: change from 2040 to 9999).", pool)
    kept = apply_number_policy("The index rose 17.7% (calculated: (41.2 - 35.0) / 35.0 * 100) [S1].",
                               passage_texts={"S1": "The CPI index rose from 35.0 to 41.2."}, allowed_numbers=set())
    assert kept.removed == []


def test_calculations_read_units_percent_points_scales_and_signed_operands():
    # Benchmark: 92 of 106 refused [[calc]] requests had facts for operands and failed only on units.
    from types import SimpleNamespace

    from utils.fact_sheet import FactSheet, apply_calculations, compute
    from utils.number_policy import number_pool, verify_calculation_labels

    text = ("Asia grew 160.9% while the Americas grew 124.4%. Jio share 40.60% vs 35.43%. Capex $140 billion rose "
            "to $217B; all filers $1.20T. UPI value ₹12.12 trillion over 7.31 billion transactions. Global average 7.3. "
            "Nifty Metal -0.73 then 29.90. Investment USD 100 billion and USD 12 billion.")
    sheet = FactSheet([SimpleNamespace(passage_id="S1", text=text)])
    assert compute("160.9% - 124.4%", sheet, None)[0] == "36.5 percentage points"
    assert compute("change from $140B to $217B", sheet, None)[0] == "55.0%"
    assert compute("$217B / $1.20T", sheet, None)[0] == "18.1%"
    assert compute("₹12.12 trillion / 7.31 billion", sheet, None)[0] == "₹1,658"
    assert compute("USD 100 billion - USD 12 billion", sheet, None)[0] == "$88 billion"
    assert compute("29.90 - (-0.73)", sheet, None)[0] == "30.63"
    assert compute("7.3", sheet, None) == ("7.3", "")
    assert compute("99.9% - 35.43%", sheet, None) is None  # an operand that is not a fact is still refused
    body, _done, _failed = apply_calculations("Jio gained [[calc: 40.60% - 35.43%]] percentage points; average "
                                              "[[calc: 7.3]].", sheet, None)
    assert body == "Jio gained 5.17 percentage points (calculated: 40.60% - 35.43%); average 7.3."
    # The label checker agrees with the engine on signed operands.
    pool = number_pool({"S1": "Nifty Metal | -0.73 | 29.90"})
    assert verify_calculation_labels("| Metal | -0.73 | 29.90 | 30.63 (calculated: 29.90 - (-0.73)) |", pool) == ""
    assert verify_calculation_labels("| Metal | -0.73 | 29.90 | 29.17 (calculated: 29.90 - (-0.73)) |", pool)


def test_chart_points_are_dated_by_their_own_period_labels():
    # Benchmark: 21 of 79 "outside the requested window" rejections were in-window points. "FY 23-24" was
    # read as FY2023 and "31.03.2021" was dated by its row's neighbouring "2020" column.
    from utils.chart_evidence_audit import _label_period, _period_ranges, _periods_outside_question_window

    assert _period_ranges("FY 23-24")[0][:2] == _period_ranges("2023-24")[0][:2]
    assert _period_ranges("FY21-22")[0][:2] == _period_ranges("2021-22")[0][:2]
    question = "How did subscribers change from 2021 to 2025?"
    assert _label_period("31.03.2021") and not _periods_outside_question_window(question, [_label_period("31.03.2021")])
    assert _label_period("FY 24-25") and _label_period("Mar 2024") and not _label_period("Uttar Pradesh")
    assert _periods_outside_question_window(question, [_label_period("31.03.2020")])


# ── Fact ids: the writer references fact-sheet cells, the code inserts the exact values ──────────
_MSP_PASSAGES = {
    "S2.1": ("MSP of Kharif crops (Rs per quintal)\nYear | Paddy common | Maize\n2018-19 | 1750 | 1700\n2019-20 | 1815 | 1760"),
    "S3.1": "Share of EVs\nYear | EV share (%)\n2023 | 6.39\n2024 | 7.44",
}


def _fact_postprocess(raw: str):
    from types import SimpleNamespace

    from utils.section_writer import SectionDraft, numbered_facts, postprocess

    passages = [SimpleNamespace(passage_id=k, text=v) for k, v in _MSP_PASSAGES.items()]
    facts = numbered_facts(passages)
    draft = SectionDraft(section_id="t", title="t")
    postprocess(draft, raw, allowed_passages=set(_MSP_PASSAGES), allowed_numbers=set(), figures=[], min_words=1,
                passage_texts=_MSP_PASSAGES, fact_refs=facts)
    return draft, facts


def test_fact_sheet_lists_numbered_ids_and_tells_the_writer_to_use_them():
    from types import SimpleNamespace

    from utils.section_writer import fact_block, numbered_facts

    passages = [SimpleNamespace(passage_id=k, text=v) for k, v in _MSP_PASSAGES.items()]
    block = fact_block(passages, facts=numbered_facts(passages))
    assert "- F1 | 1750 | Paddy common | 2018-19 | 2018 | [S2.1]" in block
    assert "- F6 | 7.44% | EV share (%) | 2024 | 2024 | [S3.1]" in block  # two-column tables too
    assert "[[F3]]" in block and "[[calc: F3 - F1]]" in block


def test_fact_ids_become_the_exact_cell_with_its_citation_and_are_not_second_guessed():
    # The sentence names neither "MSP" nor the crop: typed, 1815 could be any cell; by id it is exactly one.
    draft, facts = _fact_postprocess("It then rose to [[F3]] per quintal.\n\n<<<SUMMARY>>>\nRose to [[F3]].")
    f3 = facts[2]
    assert (f3.label, f3.metric, f3.raw) == ("2019-20", "Paddy common", "1815")
    assert "rose to 1815 per quintal [S2.1]." in draft.body
    assert draft.removed_sentences == []
    assert "\u2060" not in draft.body + draft.summary and "1815" in draft.summary
    assert {k: v for k, v in draft.fact_refs_used[0].items() if not k.startswith("_")} == {"id": "F3", "value": "1815", "passage": "S2.1"}


def test_fact_ids_carry_units_without_doubling_them_and_work_in_calculations():
    draft, facts = _fact_postprocess(
        "EV share reached [[F6]] in 2024 [S3.1]. Measured as a share it was [[F6]]% of registrations [S3.1]. "
        "Paddy MSP rose by [[calc: F3 - F1]] per quintal [S2.1].")
    assert "reached 7.44% in 2024" in draft.body
    assert "7.44% of registrations" in draft.body and "7.44%%" not in draft.body
    assert "65 (calculated: 1815 - 1750)" in draft.body.replace("1,815", "1815").replace("1,750", "1750")


def test_unknown_fact_ids_remove_their_sentence_with_a_reason():
    draft, _ = _fact_postprocess("Paddy MSP was [[F1]] [S2.1]. Wheat MSP was [[F99]] [S2.1].")
    assert "Paddy MSP was 1750 [S2.1]." in draft.body
    assert "Wheat" not in draft.body
    assert [r["reason"] for r in draft.removed_sentences] == ["refers to a fact id that is not on the section's fact sheet"]


def test_two_column_tables_are_tables():
    # 3,494 two-column rows in the benchmark evidence. A two-column table that starts with its header
    # ("Year | India") was read as prose, so its values had no metric or row; and a label/value row
    # with no header ("Reliance Jio | 908.34 million subscribers") must stay data, not become a header.
    from types import SimpleNamespace

    from utils.fact_sheet import FactSheet

    sheet = FactSheet([
        SimpleNamespace(passage_id="S1.1", text="Domestic credit to private sector (% of GDP)\nYear | India\n2020 | 40.79\n2021 | 36.89"),
        SimpleNamespace(passage_id="S2.1", text="Subscribers, October 2025\nReliance Jio | 908.34 million subscribers\nBharti Airtel | 485.02 million subscribers"),
    ])
    india = sheet.candidates("36.89")[0]
    assert (india.kind, india.label, india.metric) == ("table", "2021", "India")
    jio = sheet.candidates("908.34")[0]
    assert (jio.kind, jio.label) == ("table", "Reliance Jio")


def test_fact_ids_insert_the_column_unit_when_the_cell_has_none():
    # Live run (steel): "India produced [[F1]] of crude steel" became "produced 109.14 of crude steel":
    # the unit was in the header ("Quantity (in MnT)"), not in the cell.
    from types import SimpleNamespace

    from utils.fact_sheet import _header_unit_text
    from utils.section_writer import fact_value_text, numbered_facts

    assert _header_unit_text("Quantity (in MnT)") == "MnT"
    assert _header_unit_text("MSP (Rs per quintal)") == "Rs per quintal"
    assert _header_unit_text("TABLE 1 (continued)") == "" and _header_unit_text("Revenue (2019)") == ""
    passages = [SimpleNamespace(passage_id="S24.1", text="TABLE 1\nYear | Quantity (in MnT)\n2019-20 | 109.14\n2020-21 | 103.54"),
                SimpleNamespace(passage_id="S6.1", text="Procurement\nYear | Total MSP Value (In Lakh Crore) | Growth (%)\n2024-25 | 3.47 | 4.1")]
    values = [fact_value_text(f) for f in numbered_facts(passages)]
    assert values == ["109.14 MnT", "103.54 MnT", "3.47 lakh crore", "4.1%"]


def test_currency_column_units_go_before_the_value():
    # Live run (IPO): a "(₹ Cr)" column gave "raising 11,455 ₹ Cr".
    from types import SimpleNamespace

    from utils.section_writer import fact_value_text, numbered_facts

    passages = [SimpleNamespace(passage_id="S25.1", text="SME IPOs\nYear | Issues | Amount (₹ Cr)\nFY2025 | 267 | 11,455")]
    values = [fact_value_text(f) for f in numbered_facts(passages)]
    assert "₹11,455 Cr" in values and not any("₹ Cr" in v for v in values)


def test_a_value_bounded_the_opposite_way_from_its_passage_is_removed():
    # Live run (IPO): the passage said "net worth not exceeding Rs. 25 crores"; the writer wrote
    # "a net worth exceeding [[F23]]", so a correct value carried the opposite meaning.
    from types import SimpleNamespace

    from utils.section_writer import SectionDraft, _bound, numbered_facts, postprocess

    assert _bound("net worth not exceeding Rs.") == "upper" and _bound("a net worth exceeding") == "lower"
    assert _bound("at least Rs") == "lower" and _bound("below") == "upper" and _bound("the size of Rs") == ""
    text = ("RPT norms would not apply to SMEs which have paid up capital not exceeding Rs. 10 crores and net "
            "worth not exceeding Rs. 25 crores. The regulator also proposed further disclosure changes for issuers.")
    passages = {"S4.12": text}
    facts = numbered_facts([SimpleNamespace(passage_id=k, text=v) for k, v in passages.items()])
    ids = {f.raw: i for i, f in enumerate(facts, 1)}
    assert "25" in " ".join(ids) and "10" in " ".join(ids)
    f10 = next(i for raw, i in ids.items() if raw.endswith("10"))
    f25 = next(i for raw, i in ids.items() if raw.endswith("25"))

    def run(raw):
        draft = SectionDraft(section_id="t", title="t")
        postprocess(draft, raw, allowed_passages=set(passages), allowed_numbers=set(), figures=[], min_words=1,
                    passage_texts=passages, fact_refs=facts)
        return draft

    flipped = run(f"RPT norms would apply to SMEs with a net worth exceeding [[F{f25}]] [S4.12]. "
                  f"Small issuers with capital not exceeding [[F{f10}]] are exempt [S4.12].")
    assert "exceeding Rs 25" not in flipped.body and "Rs 25" not in flipped.body
    assert "not exceeding Rs 10" in flipped.body
    assert any("opposite way" in r.get("reason", "") for r in flipped.removed_sentences)


def test_rs_with_a_full_stop_is_not_left_as_a_sentence_end():
    # Live run (IPO): "a net worth exceeding Rs. 25 crore [S4]." reached the report as
    # "exceeding Rs [S4]. 25 crore [S4]." - later steps split sentences at ". " before a digit.
    draft, _ = _fact_postprocess("The MSP rose to Rs. 1815 per quintal in 2023 [S2.1]. It was raised again in 2024 [S2.1].")
    assert "Rs. 1815" not in draft.body and "Rs 1815" in draft.body


def test_prose_statistics_get_fact_ids_with_their_units_and_noise_is_left_out():
    # Most numbers the writer still typed after table ids came from passage prose.
    from types import SimpleNamespace

    from utils.section_writer import fact_block, fact_value_text, numbered_facts

    text = ("Sales Turnover of Rs. 104545 crore during FY'24 has been achieved. Crude Steel Production of 19.240 MT "
            "was achieved. Per capita income may rise from INR 1.8 lakh to INR 16.5 lakh by 2047. See Table 1.6 for "
            "details. Total MF AUM (lakh crore) 53.4 172 461 1,152 2,791. The meeting was held on 15 November.")
    passages = [SimpleNamespace(passage_id="S3.1", text=text)]
    values = [fact_value_text(f) for f in numbered_facts(passages)]
    # "Rs." no longer ends a sentence, the unit word after a number is kept, and table numbers,
    # flattened number runs and dates get no id.
    assert values == ["Rs. 104545 crore", "19.240 MT", "INR 1.8 lakh", "INR 16.5 lakh"]
    block = fact_block(passages)
    assert "NUMBERS IN THE PASSAGES' TEXT" in block
    assert "- F1 | Rs. 104545 crore | Sales Turnover of Rs. 104545 crore during FY'24 has been achieved.… | [S3.1]" in block


def test_calculation_checks_read_scales_constants_and_rs_without_a_space():
    # Replayed benchmark removals of correct arithmetic (upi, ai_investment, education, fmcg, telecom,
    # insurance, inflation).
    from utils.number_policy import drop_restating_calculations, number_pool, verify_calculation_labels

    pool = number_pool({"S1.1": "In October 2022, 7.31 billion transactions moved Rs 12.12 trillion. "
                                "Big four spent $217B against $1.20T in total. 76% of students lacked a device."})
    assert verify_calculation_labels("an average of 1,658 (calculated: 12.12 trillion / 7.31 billion).", pool) == ""
    assert verify_calculation_labels("18.1% (calculated: $217B / $1.20T) of all spending.", pool) == ""
    assert verify_calculation_labels("24 percentage points (calculated: 100% - 76%) had access.", pool) == ""
    assert verify_calculation_labels("an average of 9,999 (calculated: 12.12 trillion / 7.31 billion).", pool)
    rs = number_pool({"S2.1": "Average revenue per GB Rs.10.38\nAverage revenue per GB Rs.9.12"})
    assert {n.raw for n in rs} >= {"10.38", "9.12"}
    assert drop_restating_calculations("attracted 62.0% (calculated: 62 / 100) of FDI") == "attracted 62.0% of FDI"
    assert drop_restating_calculations("grew 2.7 times (calculated: 2.7 * 1)").startswith("grew 2.7 times")
    assert "(calculated: 50 / 100)" in drop_restating_calculations("rose 40% (calculated: 50 / 100)")


def test_split_fiscal_year_question_range_keeps_the_years_in_between():
    # agri_msp: "from 2018-19 to 2024-25" was not read as a range; 2019-20 .. 2023-24 were "outside".
    from utils.chart_evidence_audit import _periods_outside_question_window

    question = "How have MSPs for wheat and paddy changed from 2018-19 to 2024-25?"
    for label in ("2018-19", "2019-20", "2022-23", "2024-25"):
        assert _periods_outside_question_window(question, [label], []) == []
    assert _periods_outside_question_window(question, ["2025-26"], []) == ["2025-26"]
    assert _periods_outside_question_window("from FY2019-20 to FY2024-25", ["2017-18"], [])


def test_column_numbering_rows_are_not_data_and_century_rollover():
    from types import SimpleNamespace

    from utils.fact_sheet import extract_facts, is_column_numbering
    from utils.section_charts import _period_end_year, charts_from_passage

    assert is_column_numbering(["1", "2", "3", "4"]) and is_column_numbering(["(1)", "(2)", "(3)"])
    assert not is_column_numbering(["2019", "2", "3"]) and not is_column_numbering(["1", "5", "9"])
    assert _period_end_year("1999-00") == 2000 and _period_end_year("2018-19") == 2019
    text = ("TABLE 1\nYear | Paddy common | Wheat | Gram\n1 | 2 | 3 | 4\n2018-19 | 1750 | 1840 | 4620\n"
            "2019-20 | 1815 | 1925 | 4875\n2020-21 | 1868 | 1975 | 5100")
    passage = SimpleNamespace(passage_id="S12.1", source_id="S12", kind="table", reliability=1.0, publisher="", title="",
                              text=text)
    assert not any(f.raw in {"2", "3", "4"} for f in extract_facts(passage))
    charts = charts_from_passage(passage, 1)
    assert charts and all(str(d.get("label")) != "1" for c in charts for s in c.spec["series"] for d in s["data"])


def test_fetched_page_stored_under_its_final_url_keeps_its_fetch_status():
    # steel_production: S27 was fetched (PIB, HTTP 200, tables) but the manifest held the final URL and the
    # fetched row the search URL; every PIB release shares path and title, so no row matched and the source
    # was reported as metadata only (its production chart failed verification).
    import routes.report as report

    table = "TABLE 1\nYear | Quantity (in MnT)\n2019-20 | 109.14\n2020-21 | 103.54"
    fetched = {"title": "Press Release: Press Information Bureau", "url": "https://pib.gov.in/PressReleseDetailm.aspx?PRID=2119067",
               "pageFetchFinalUrl": "https://www.pib.gov.in/PressReleseDetailm.aspx?PRID=2119067&reg=48&lang=2",
               "pageFetchStatus": "ok", "evidenceOrigin": "publisher_page", "pageContent": table, "snippet": table}
    other = {"title": "Press Release: Press Information Bureau", "url": "https://pib.gov.in/PressReleseDetailm.aspx?PRID=2000001",
             "pageFetchStatus": "ok", "evidenceOrigin": "publisher_page", "pageContent": "Other release text."}
    manifest = [{"id": "S27", "title": "Press Release: Press Information Bureau",
                 "url": "https://pib.gov.in/pressrelesedetailm.aspx?lang=2&prid=2119067&reg=48"}]
    merged = report._merge_source_evidence_statuses(
        manifest, report._attach_audit_source_evidence(manifest, [other, fetched], "Output was 109.14 MnT [S27]."))
    assert merged[0]["page_fetch_status"] == "ok"
    assert merged[0]["evidence_origin"] == "publisher_page"


def test_dataset_tables_snake_case_columns_long_tables_and_schema_listings():
    # tourism (forex_earnings_USD), healthcare (WHO table: rows 139/175 below the header), 7 prompts with a
    # "Column | Type | Distinct | Count" listing that became a chart.
    from types import SimpleNamespace

    import utils.chart_evidence_audit as cea
    from utils.chart_titles import clean_label
    from utils.fact_sheet import extract_facts, is_schema_table
    from utils.section_charts import charts_from_passage

    assert clean_label("forex_earnings_USD") == "forex earnings USD" and clean_label("**Total**") == "Total"
    note = "forex_earnings_INR in rupee crores, forex_earnings_USD in million USD"
    text = f"year | forex_earnings_INR | forex_earnings_USD | note\n2022 | 169917 | 21360 | {note}\n2021 | 63978 | 8651 | {note}"
    point = {"evidenceQuote": f"2021 | 63978 | 8651 | {note}", "evidenceValue": "8651", "evidenceColumn": "forex_earnings_USD"}
    assert cea._table_point_column_matches(point, "forex earnings USD", "2021", 8651.0, point["evidenceQuote"], text) is True
    wrong = dict(point, evidenceValue="63978")
    assert cea._table_point_column_matches(wrong, "x", "2021", 63978.0, wrong["evidenceQuote"], text) is False

    rows = "\n".join(f"Country{i:03d} | US${i} per person | {60 + i / 10:.1f} years" for i in range(150))
    table = "Country | Health spending | Life expectancy at birth\n" + rows
    far = {"evidenceQuote": "Country140 | US$140 per person | 74.0 years", "evidenceValue": "74.0 years",
           "evidenceColumn": "Life expectancy at birth"}
    assert cea._table_point_column_matches(far, "Life expectancy", "Country140", 74.0, far["evidenceQuote"], table) is True

    schema = ("TABLE 20 — Columns\nColumn | Type | Distinct | Count | Description\nyear | Int64 | 26 | 26 | Year.\n"
              "forex_earnings_INR | Int64 | 26 | 26 | Earnings.\nforex_earnings_USD | Int64 | 26 | 26 | Earnings.")
    passage = SimpleNamespace(passage_id="S35.3", source_id="S35", kind="table", reliability=1.0, publisher="",
                              title="", text=schema)
    assert is_schema_table(["Column", "Type", "Distinct", "Count", "Description"])
    assert not is_schema_table(["Year", "Count", "Value"])
    assert charts_from_passage(passage, 1) == [] and not [f for f in extract_facts(passage) if f.kind == "table"]


def test_dataset_column_listing_is_not_evidence():
    # mutual_fund_sip / tourism: the writer turned a portal's column listing into "516 total records" prose.
    from utils.evidence_store import EvidenceStore

    page = ("TABLE 1 — SIP inflows\nYear | Inflow (Rs crore)\n2023 | 1,56,000\n2024 | 2,89,352\n2025 | 3,10,000\n\n"
            "TABLE 2 — Columns\nColumn | Type | Distinct | Count | Description\nyear | Int64 | 11 | 516 | Year.\n"
            "value | Float | 508 | 516 | Value.\n")
    store = EvidenceStore()
    store.add_source("S1", {"url": "https://example.org/data", "title": "SIP data", "pageContent": page})
    texts = " ".join(p.text for p in store.passages)
    assert "2,89,352" in texts and "Distinct" not in texts


def test_long_format_country_year_table_is_a_time_series_with_its_own_rows_quoted():
    # internet_use_comparison: "Entity | Code | Year | Value" became a country bar chart with each country's
    # 2015 value quoting its 2024 row; the column check then took the row above for a header.
    from types import SimpleNamespace

    import utils.chart_evidence_audit as cea
    from utils.section_charts import _focused_charts

    rows = []
    for year, values in ((2015, (14.9, 16.58, 58.32)), (2016, (16.5, 16.6, 60.87)), (2017, (18.2, 17.82, 67.47)),
                         (2018, (20.08, 19.5, 70.43))):
        rows += [f"{name} | {code} | {year} | {v}" for (name, code), v in zip((("India", "IND"), ("Kenya", "KEN"),
                                                                               ("Brazil", "BRA")), values)]
    text = "TABLE 1 — internet.csv\nEntity | Code | Year | Share of the population using the Internet\n" + "\n".join(rows)
    passage = SimpleNamespace(passage_id="S27.1", source_id="S27", kind="table", reliability=1.0, publisher="",
                              title="", text=text)
    charts = _focused_charts(passage, 1, None, ["Kenya", "India"], None)
    assert len(charts) == 1 and charts[0].spec["type"] in {"line", "grouped_bar"}
    series = {s["name"]: s["data"] for s in charts[0].spec["series"]}
    assert set(series) == {"Kenya", "India"}
    kenya_2016 = next(d for d in series["Kenya"] if d["label"] == "2016")
    assert kenya_2016["value"] == 16.6 and kenya_2016["evidenceQuote"] == "Kenya | KEN | 2016 | 16.6"
    for name, data in series.items():
        for point in data:
            assert cea._table_point_column_matches(point, name, point["label"], point["value"],
                                                   point["evidenceQuote"], text) is True


def test_live_run_writer_artifacts_identifiers_and_rs_abbreviation():
    # Live 30-prompt run: road names checked as statistics, ASCII charts in tables, "Rs." split in the audit.
    import re

    from utils.number_policy import statistic_tokens
    from utils.section_writer import strip_ascii_charts

    assert statistic_tokens("National Highway-539 and National Highway-8 have 14 and 12 stations.") == ["14", "12"]
    assert statistic_tokens("Highways such as National Highway-40, 716, and 727A have one station.") == []
    assert statistic_tokens("Sales rose 12-15% in 2024.") == ["12", "15"]
    body = ("Text [S1].\n\n**Table 2: Installs**\n\n| 1,000 | ----------------*---- |\n| --- | --- |\n"
            "| 800 | -------------/------ |\n\nMore text [S2].")
    cleaned = strip_ascii_charts(body)
    assert "----*" not in cleaned and "Table 2" not in cleaned and "More text" in cleaned
    keep = "| Year | Value |\n|:--- |:--- |\n| 2024 | 5 |\n| 2025 | 6 |"
    assert strip_ascii_charts(keep) == keep
    source = open("routes/report.py", encoding="utf-8").read()
    assert not re.search(r're\.split\(r"\(\?<=\[\.!\?\]\)\s\+', source)  # every splitter has the abbreviation guard


def test_scorecard_date_column_is_labelled_by_measure_and_plain_date():
    # markets live run: "30-Jan-26, February 2026 [S2]" as a scorecard metric; the audit read "30" as a claim.
    from utils.section_layout import Table, _metric_label

    table = Table(passage_id="S2.1", source_id="S2", heading="Nifty sectoral index closing levels",
                  header=["Index", "30-Jan-26", "Change (%)"], rows=[["Nifty Bank", "59,610.45", "1.2"]],
                  period_label="February 2026")
    label = _metric_label(table, 1)
    assert label == "Nifty sectoral index closing levels, 30 Jan 2026"


def test_audit_reads_investor_money_flows_as_rupees_not_counts():
    # markets live run: "foreign investors poured +11,065.00 crore into Telecom" was compared as a count.
    import routes.report as report

    page = "FPI flows in October 2026: net investment in Telecom stood at Rs 11,065 crore, the largest of any sector."
    enriched = [{"title": "NSDL FPI fortnightly", "url": "https://www.fpi.nsdl.co.in/report", "pageFetchStatus": "ok",
                 "evidenceOrigin": "publisher_page", "pageContent": page}]
    manifest = [{"id": "S56", "title": "NSDL FPI fortnightly", "url": "https://www.fpi.nsdl.co.in/report"}]
    audited = report._attach_audit_source_evidence(
        manifest, enriched, "In October, foreign investors poured +11,065.00 crore into Telecom [S56].")
    assert audited[0].get("unmatchedNumericClaims") in ([], None)


def test_every_way_a_writer_writes_a_fact_id_is_resolved_or_removed():
    # Live 30-prompt run: bare "F61", "(F30)", "[F21]", "[[F14%]]", "[[F56.1: 5171.97]]" and ids inside citations
    # reached the published reports.
    from utils.section_writer import normalize_fact_refs

    assert normalize_fact_refs("GNPA fell by F61 in H1 [S8].", 100) == "GNPA fell by [[F61]] in H1 [S8]."
    assert normalize_fact_refs("grew 1.54% [[F15], [F21], S30.9] [S30].", 100) == "grew 1.54% [S30.9] [S30]."
    assert normalize_fact_refs("gains to [[F14%]] in May.", 100) == "gains to [[F14]]% in May."
    assert normalize_fact_refs("via [[F56.1: 5171.97]] for FAME [S56].", 100) == "via 5171.97 for FAME [S56]."
    assert normalize_fact_refs("608.3 dollars (F30) in 2015 [S1].", 100) == "608.3 dollars in 2015 [S1]."
    assert normalize_fact_refs("Capacity [F4] and [[ fact: F5 ]] [S2].", 100) == "Capacity [[F4]] and [[F5]] [S2]."
    assert "UNKNOWN_FACT" in normalize_fact_refs("Indonesia ([[FFF:=F31 in table]]) lagged.", 100)
    assert "UNKNOWN_FACT" in normalize_fact_refs("reached F250 by 2024.", 100)  # not on the sheet
    keep = "The F-16 jet, F&O volumes and PDF/F2 files. [[FIGURE 2]] [[calc: 5 - 3]] [[F7]]"
    assert normalize_fact_refs(keep, 100) == keep

    draft, facts = _fact_postprocess("The MSP for paddy rose to F2 in 2019-20 [S2.1]. It rose again later [S2.1].")
    assert "F2" not in draft.body and facts[1].raw in draft.body


def test_audit_reads_a_flattened_pdf_table_with_its_unit_line():
    # internet live run: TRAI "Internet Subscribers (million)" above "Mar-2023 357.99 523.26 881.25".
    import routes.report as report

    page = ("Table1.41: Subscriber Base & Tele-density - Rural & Urban\nInternet Subscribers (million) Internet tele-density (%)\n"
            "Mar-2022 331.81 493.08 824.89 37.06 102.82 60.00\nMar-2023 357.99 523.26 881.25 39.84 107.11 63.53\n"
            "Mar-2024 398.35 556.05 954.40 44.16 111.75 68.19")
    enriched = [{"title": "TRAI annual report", "url": "https://trai.gov.in/report.pdf", "pageFetchStatus": "ok",
                 "evidenceOrigin": "publisher_page", "pageContent": page}]
    manifest = [{"id": "S30", "title": "TRAI annual report", "url": "https://trai.gov.in/report.pdf"}]
    audited = report._attach_audit_source_evidence(
        manifest, enriched, "Internet subscribers grew to 881.25 million in March 2023 [S30].")
    assert audited[0].get("unmatchedNumericClaims") in ([], None)


def test_percent_after_an_id_is_dropped_for_a_fact_with_another_unit():
    # renewables live run: "Coal | 2,19,610%" - a "%" after the id of a capacity fact.
    from types import SimpleNamespace

    from utils.section_writer import numbered_facts, resolve_fact_refs

    passages = [SimpleNamespace(passage_id="S5.1", text="Capacity by source\nSource | Capacity (MW) | Share (%)\n"
                                                         "Coal | 2,19,610 MW | 45.3\nSolar | 1,29,000 MW | 26.6")]
    facts = numbered_facts(passages)
    mw = next(i for i, f in enumerate(facts, 1) if f.raw.startswith("2,19,610"))
    share = next(i for i, f in enumerate(facts, 1) if f.raw == "45.3")
    text, _ = resolve_fact_refs(f"Coal had [[F{mw}]]% and a share of [[F{share}]]% [S5.1].", facts)
    text = text.replace("⁠", "")
    assert "2,19,610 MW%" not in text and "2,19,610%" not in text and "2,19,610 MW" in text
    assert "45.3%" in text and "45.3%%" not in text


def test_audit_reads_money_rows_under_a_currency_scale_label():
    # semiconductors live run: "Sales (in billions) ... in USD:" above "Asia-Pacific 86.17 221.42".
    import routes.report as report

    page = ("WSTS quarterly update.\nSales (in billions) Quarter growth in % Year on Year growth in % in USD:\n"
            "Asia-Pacific 86.17 221.42 23.1 108.5 45.0\nAmericas 61.20 195.10 12.2 40.1 45.2\nEurope 13.40 51.30 2.1 -3.0 -8.1")
    enriched = [{"title": "WSTS", "url": "https://www.wsts.org/q1", "pageFetchStatus": "ok",
                 "evidenceOrigin": "publisher_page", "pageContent": page}]
    manifest = [{"id": "S8", "title": "WSTS", "url": "https://www.wsts.org/q1"}]
    audited = report._attach_audit_source_evidence(
        manifest, enriched, "Asia-Pacific semiconductor sales reached $86.17 billion in the quarter [S8].")
    assert audited[0].get("unmatchedNumericClaims") in ([], None)
    wrong = report._attach_audit_source_evidence(
        manifest, enriched, "Asia-Pacific semiconductor sales reached $96.17 billion in the quarter [S8].")
    assert wrong[0].get("unmatchedNumericClaims")


def test_region_by_year_table_becomes_a_verified_chart():
    # semiconductors live run: "Region | 1986 | ... | 2025" was dropped as header-less (years read as data);
    # once built, long rows lost their row label and period, and the title read "Region: ...".
    from types import SimpleNamespace

    import utils.chart_evidence_audit as cea
    from utils.chart_titles import header_is_data
    from utils.section_charts import charts_from_passage

    assert not header_is_data("2024") and header_is_data("19.86") and header_is_data("5.99%")
    years = list(range(2010, 2025))
    rows = {"World Total": [300 + 10 * i for i in range(len(years))], "Americas": [60 + 5 * i for i in range(len(years))],
            "Europe": [30 + i for i in range(len(years))], "Japan": [40 + i for i in range(len(years))]}
    text = "TABLE 1 — Data Table\nRegion | " + " | ".join(map(str, years)) + "\n" + "\n".join(
        f"{name} | " + " | ".join(f"{v:.1f}" for v in values) for name, values in rows.items())
    passage = SimpleNamespace(passage_id="S3.1", source_id="S3", kind="table", reliability=1.0, publisher="",
                              title="Semiconductor Sales | Dashboard", text=text)
    charts = charts_from_passage(passage, 1, _only_cols=list(range(11, 16)))
    assert charts and charts[0].spec["title"].startswith("Semiconductor Sales")
    spec = charts[0].spec
    for series in spec["series"]:
        for point in series["data"]:
            reason = cea._quoted_point_mismatch(spec, series["name"], point["label"], point["value"], point,
                                                point["evidenceQuote"], text)
            assert reason == "", (series["name"], point["label"], reason)


def test_generic_page_title_is_not_a_chart_measure():
    from types import SimpleNamespace

    from utils.section_charts import charts_from_passage

    text = ("TABLE 1\nCategory | FY 2019-20 | FY 2020-21 | FY 2021-22 | FY 2022-23\nPassenger Vehicles | 3.30 | 5.83 | 22.36 | 62.28\n"
            "Two-Wheelers | 26.84 | 44.83 | 252.78 | 728.21\nThree-Wheelers | 143.83 | 91.97 | 185.38 | 404.88")
    passage = SimpleNamespace(passage_id="S43.1", source_id="S43", kind="table", reliability=1.0, publisher="",
                              title="Press Release Page | Press Information Bureau", text=text)
    charts = charts_from_passage(passage, 1)
    assert charts and not charts[0].spec["title"].startswith("Press Release")


def test_two_stacked_tables_in_one_passage_are_charted_apart():
    # telecom live run: a state table under the subscriber table became part of the subscriber chart.
    from types import SimpleNamespace

    from utils.chart_lint import quality_reason
    from utils.section_charts import _focused_charts, _split_stacked_tables

    text = ("TABLE 38\nS. No. | Period ending | Wireless Subscribers (in crores)\n1 | 31.03.2021 | 118.09\n"
            "2 | 31.03.2022 | 114.20\n3 | 31.03.2023 | 114.39\n4 | 31.03.2024 | 116.54\n"
            "State | Number of Districts where 5G has been rolled out | Number of villages with 5G BTSs\n"
            "Andhra Pradesh | 26 | 3,797\nAssam | 35 | 1,757\nBihar | 38 | 4,601\nGoa | 2 | 111")
    passage = SimpleNamespace(passage_id="S8.3", source_id="S8", kind="table", reliability=1.0, publisher="",
                              title="", text=text)
    assert len(_split_stacked_tables(passage)) == 2
    merged = SimpleNamespace(**{**vars(passage), "text": "TABLE 1\nCrop | 2023 | 2024\nKHARIF | KHARIF | KHARIF\n"
                                                         "Paddy | 2183 | 2300\nRABI | RABI | RABI\nWheat | 2275 | 2425"})
    assert len(_split_stacked_tables(merged)) == 1
    charts = _focused_charts(passage, 1, None, [], 2021, 2025)
    subscriber = [c for c in charts if "Wireless" in c.spec["title"]]
    assert subscriber and quality_reason(subscriber[0].spec, text) == ""
    assert all(d["label"].startswith("31.03.") for s in subscriber[0].spec["series"] for d in s["data"])


def test_value_context_uses_the_rows_own_header_and_stops_at_its_caption():
    # IPO: stacked tables gave a sector row the first table's header; page prose two lines above a captioned
    # table ("listed from FY2012 to FY2022") dated the XIRR table out of the question's window.
    import utils.chart_evidence_audit as cea

    stacked = ("FY 2023-24 | FY 2024-25 | Total\nMaharashtra | 65 | 65 | 130\n"
               "Sector | Total No. of SME IPOs | Issue size of sector (₹ cr)\nAgriculture | 11 | 270\nServices | 220 | 6935")
    context = cea._value_windows("220", stacked)[0][1]
    assert "Total No. of SME IPOs" in context and "FY 2023-24" in context
    captioned = ("The cohort covers companies listed from FY2012 to FY2022.\nTABLE 9 — Value Creation Framework\n"
                 "Company | XIRR Returns %\nAditya Vision | 106.5%\nSky Gold | 104.3%")
    context = cea._value_windows("106.5", captioned)[0][1]
    assert "FY2012" not in context and "TABLE 9" in context


def test_full_width_and_round_citation_brackets_become_square():
    # pharma live run: "【S23.2】" citations reached the report unlinked; pms used "(S12)".
    from utils.section_writer import normalize_citation_brackets

    assert normalize_citation_brackets("exports rose 9%【S23.2】 in FY25") == "exports rose 9%[S23.2] in FY25"
    assert normalize_citation_brackets("AUM grew (S12, S3.1).") == "AUM grew [S12, S3.1]."
    assert normalize_citation_brackets("Schedule (S) and (see S3 notes)") == "Schedule (S) and (see S3 notes)"


def test_month_without_year_is_dated_by_the_article_and_checked():
    # inflation live run: an article of 17 Nov 2026 said "rose to 6.09 percent in October"; the writer wrote
    # "In October 2024, CPI inflation rose to [[F67]]". "6 .09" also gave a "6%" fact.
    from types import SimpleNamespace

    from utils.section_writer import fact_block, numbered_facts, resolve_fact_refs

    text = ("Rising rate hike bets cloud the outlook.\nConsumer Price Index (CPI)-based inflation rose to 6 .09 percent "
            "in October, moving above the RBI's tolerance band for the first time in two years.")
    passage = SimpleNamespace(passage_id="S30.2", text=text, published="2026-11-17T03:30:03.000Z")
    facts = numbered_facts([passage])
    cpi = next(i for i, f in enumerate(facts, 1) if f.value == "6.09")
    assert not any(f.value == "6" for f in facts)
    assert facts[cpi - 1].period == "October 2026" and "October 2026, from the article date" in fact_block([passage], facts=facts)
    wrong, _ = resolve_fact_refs(f"In October 2024, CPI inflation rose to [[F{cpi}]] [S30.2].", facts)
    right, _ = resolve_fact_refs(f"In October 2026, CPI inflation rose to [[F{cpi}]] [S30.2].", facts)
    plain, _ = resolve_fact_refs(f"CPI inflation rose to [[F{cpi}]] in October [S30.2].", facts)
    assert "PERIOD_FLIP" in wrong and "6.09" in right and "6.09" in plain
    january = SimpleNamespace(passage_id="S1.1", published="2026-11-17",
                              text="Exports rose to $45.2 billion in December, a record.")
    assert numbered_facts([january])[0].period == "December 2025"


def test_europe_gas_footnoted_year_spaced_percent_and_repeated_id():
    from types import SimpleNamespace

    from utils.section_writer import normalize_fact_refs, numbered_facts

    passage = SimpleNamespace(passage_id="S31.1", published="2024-10-10", text=(
        "The EU maintains high levels of gas storage ( 94 % or 1,083 TWh on 1 October 2024 ), above target. "
        "Gas consumption decreased year-to-date compared to 20231 levels."))
    values = [f.value for f in numbered_facts([passage])]
    assert "1083" in [v.replace(",", "") for v in values] and "20231" not in values
    assert normalize_fact_refs("storage of [[F7]] ([[F7]] TWh) in October", 50) == "storage of [[F7]] in October"


def _chart_minimum_store():
    from types import SimpleNamespace

    text = ("TABLE 1 — Subscribers by operator\nOperator | Subscribers (million) | Share (%)\n"
            "Jio | 476.58 | 40.5\nAirtel | 289.31 | 33.6\nVodafone Idea | 127.40 | 17.9\nBSNL | 34.26 | 7.2")
    passage = SimpleNamespace(passage_id="S5.1", source_id="S5", kind="table", reliability=1.0, publisher="",
                              title="TRAI subscriber data", text=text, published="2025-05-01")
    return SimpleNamespace(get=lambda pid: passage if pid == "S5.1" else None, passages=[passage]), passage


def test_report_number_chart_from_a_sentence_comparing_facts():
    # Chart minimum: "Jio had [[F1]] and Airtel [[F3]]" becomes a chart of exactly those two source rows.
    from types import SimpleNamespace

    import utils.chart_evidence_audit as cea
    from utils.chart_minimum import place_after_sentence, report_number_charts
    from utils.section_writer import numbered_facts, resolve_fact_refs

    store, passage = _chart_minimum_store()
    facts = numbered_facts([passage])
    ids = {(f.label, f.metric): i for i, f in enumerate(facts, 1)}
    jio, airtel = ids[("Jio", "Subscribers (million)")], ids[("Airtel", "Subscribers (million)")]
    body, used = resolve_fact_refs(f"Jio led with [[F{jio}]] subscribers while Airtel had [[F{airtel}]] [S5.1].", facts)
    draft = SimpleNamespace(section_id="s1", fact_refs_used=used, body=body)
    charts = report_number_charts([draft], store, start_index=900, limit=3)
    assert len(charts) == 1 and charts[0].spec["fromReportText"] and charts[0].section_id == "s1"
    points = [p for s in charts[0].spec["series"] for p in s["data"]]
    assert {p["label"] for p in points} == {"Jio", "Airtel"}
    for series in charts[0].spec["series"]:
        for p in series["data"]:
            assert cea._quoted_point_mismatch(charts[0].spec, series["name"], p["label"], p["value"], p,
                                              p["evidenceQuote"], passage.text) == ""
    placed = place_after_sentence("Intro.\n\n" + body.replace("⁠", "") + "\n\nLater.", charts[0].claim, 301,
                                  "Jio has the most subscribers.")
    assert placed.index("[[FIGURE 301]]") < placed.index("Later.")


def test_context_chart_is_labelled_and_exempt_only_from_the_window():
    from types import SimpleNamespace

    import utils.chart_evidence_audit as cea
    from utils.chart_minimum import CONTEXT_NOTE, context_charts
    from utils.section_charts import charts_from_passage

    text = "TABLE 1 — Exports (USD bn)\nYear | Exports (USD bn)\n2012 | 10.5\n2013 | 11.2\n2014 | 12.9\n2015 | 13.1"
    passage = SimpleNamespace(passage_id="S9.1", source_id="S9", kind="table", reliability=1.0, publisher="",
                              title="", text=text)
    chart = charts_from_passage(passage, 1)[0]
    far = charts_from_passage(SimpleNamespace(**{**vars(passage), "text": text.replace("201", "199")}), 2)[0]
    picked = context_charts([far, chart], [], min_year=2018, max_year=2024, limit=1)
    assert len(picked) == 1 and "2015" in str(picked[0].spec["series"])  # nearest to the window first
    spec = picked[0].spec
    assert spec["contextOutsideWindow"] and "context: outside the requested period" in spec["title"]
    assert CONTEXT_NOTE in spec["basisNote"]
    source = {"id": "S9", "url": "https://example.org/x", "page_fetch_status": "ok", "evidence_origin": "publisher_page",
              "evidenceExcerpt": text, "publisherEvidenceExcerpt": text}
    question = "How did exports change from 2018 to 2024?"
    plain = dict(chart.spec, evidencePassages=["S9.1"])
    flagged = dict(spec, evidencePassages=["S9.1"])
    window = "falls outside the time window"
    plain_reasons = [f.get("reason") for f in cea.audit_chart_evidence_consistency("", [plain], [source], question=question)
                     .get("chartEvidenceFailures") or []]
    flagged_reasons = [f.get("reason") for f in cea.audit_chart_evidence_consistency("", [flagged], [source], question=question)
                       .get("chartEvidenceFailures") or []]
    assert any(window in str(r) for r in plain_reasons)
    assert not any(window in str(r) for r in flagged_reasons)


def test_a_chart_whose_best_section_is_full_goes_to_the_next_fitting_section():
    from types import SimpleNamespace

    from utils.section_charts import assign_charts

    def chart(cid, pids, text):
        return SimpleNamespace(chart_id=cid, section_id="", passage_ids=pids, points=[("a", "b", 1.0)], match_text=text)

    sections = [SimpleNamespace(section_id="s1", title="Exports", goal="exports", queries=["exports"], passage_ids=["S1.1"]),
                SimpleNamespace(section_id="s2", title="Export markets", goal="markets", queries=["exports"], passage_ids=["S2.1"])]
    charts = [chart("c1", ["S1.1"], "exports"), chart("c2", ["S1.1"], "exports"), chart("c3", ["S1.1", "S2.1"], "exports")]
    assign_charts(charts, sections, per_section=2)
    assert sorted(c.section_id for c in charts) == ["s1", "s1", "s2"]
    weak = [chart("w1", ["S9.1"], "exports")]
    assign_charts(weak, sections, per_section=2)
    assert weak[0].section_id == ""  # a weak fit is not placed when the report is not short of charts
    assign_charts(weak, sections, per_section=2, fill_to=8)
    assert weak[0].section_id in {"s1", "s2"}


def test_defect_scan_rules_catch_the_live_defects_and_not_ordinary_text():
    from utils.defect_scan import scan_report

    bad = ("GNPA fell by F61 in H1 [S8]. Storage hit 94% (94% TWh) [S3]. Coal 2,19,610 MW% [S2]. "
           "It grew to Rs [S14]. 70,253 crore. Rate 7.04 %【S9.2】. Fell by 20231 versus last year. "
           "Left [[UNKNOWN_FACT]] here.\n| 1,000 | ----------*---- |")
    found = scan_report(bad)
    for rule in ("leaked_fact_id", "doubled_value", "broken_unit", "split_rupee", "odd_citation", "footnoted_year",
                 "leaked_marker", "ascii_chart"):
        assert rule in found, rule
    good = ("| Year | Value |\n| --- | --- |\n| 2024 | 1,234 |\nThe F-16 and F&O volumes rose 12% in 2024 [S1]. "
            "Values ranged from -19337 to -9767 [S2]. [[FIGURE 2]]")
    assert scan_report(good) == {}


def test_rs_and_currency_after_number_are_fixed_before_fact_ids_resolve():
    draft, facts = _fact_postprocess("The MSP for paddy rose to [[F3]] while costs hit Rs. 1,700 by 2019-20 [S2.1]. "
                                     "Its reserve reached 1,750 ₹ Cr in 2018-19 [S2.1].")
    assert "Rs [S" not in draft.body and "₹ Cr" not in draft.body


def test_shared_evidence_text_rules():
    import re

    from utils.evidence_text import SENTENCE_END, is_header_row, normalize_numbers, split_sentences, stacked_table_starts

    text = "PAT rose to Rs. 4.67 lakh crore in Q1. Item No. 5 was cut, e.g. the levy. Next sentence here."
    assert split_sentences(text) == ["PAT rose to Rs. 4.67 lakh crore in Q1.", "Item No. 5 was cut, e.g. the levy.",
                                     "Next sentence here."]
    assert re.split(SENTENCE_END + r"\s+", "A. B") == ["A.", "B"]
    assert normalize_numbers("rose to 6 .09 percent") == "rose to 6.09 percent"
    assert is_header_row("Sector | Total No. of SME IPOs | Issue size (₹ cr)")
    assert is_header_row("Particulars | Mar-20 | Mar-21") and is_header_row("State | Districts with 5G | Villages")
    assert not is_header_row("Maharashtra | 65 | 130") and not is_header_row("KHARIF | KHARIF | KHARIF")
    assert not is_header_row("1 | 31.03.2021 | 118.09") and not is_header_row("Coal | 12 GW | 45%")
    lines = ["S. No. | Period | Subscribers", "1 | 31.03.2021 | 118.09", "State | Districts | Villages", "Goa | 2 | 111"]
    assert stacked_table_starts(lines) == [2]


def test_no_module_keeps_its_own_sentence_boundary_regex():
    # One rule for every checker: a module with its own "(?<=[.!?])\s+" drifts (the "Rs." split was fixed 4x).
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[1]
    offenders = []
    for path in list((root / "utils").glob("*.py")) + list((root / "routes").glob("*.py")):
        if path.name.startswith("test_") or path.name == "evidence_text.py":
            continue
        if re.search(r'r"\(\?<=\[\.!\?\]\)\(?\s\+', path.read_text(encoding="utf-8")):
            offenders.append(path.name)
    assert offenders == []


def test_claim_check_judges_weak_sentences_and_removes_unsupported_ones():
    import asyncio
    from types import SimpleNamespace

    from utils.claim_check import candidates, check_section, parse_verdicts

    passages = {"S1.1": "Retail inflation eased to 4.3 percent in March as vegetable prices fell sharply across cities.",
                "S2.1": "The central bank kept the repo rate unchanged at 6.5 percent at its April meeting."}
    body = ("## Inflation\nRetail inflation eased to 4.3 percent in March as vegetable prices fell [S1.1]. "
            "The decline was caused mainly by a collapse in global crude oil demand and shipping costs [S1.1].\n\n"
            "The central bank kept the repo rate unchanged at 6.5 percent in April [S2.1].\n\n| Year | Rate |\n| 2024 | 6.5 |")
    items, checked = candidates(body, passages)
    assert checked == 3 and [i["sentence"][:16] for i in items] == ["The decline was "]

    class Gateway:
        async def generate(self, **kwargs):
            return SimpleNamespace(ok=True, text='[{"i": 1, "verdict": "UNSUPPORTED", "reason": "cause not in source"}]',
                                   json=lambda: [{"i": 1, "verdict": "UNSUPPORTED", "reason": "cause not in source"}])

    draft = SimpleNamespace(section_id="s1", body=body)
    entry = asyncio.run(check_section(Gateway(), draft, passages))
    assert len(entry["removed"]) == 1 and "collapse in global crude" not in draft.body
    assert "eased to 4.3 percent" in draft.body and "| 2024 | 6.5 |" in draft.body

    class Broken:
        async def generate(self, **kwargs):
            return SimpleNamespace(ok=False, error="timeout", text="")

    draft2 = SimpleNamespace(section_id="s1", body=body)
    assert asyncio.run(check_section(Broken(), draft2, passages))["removed"] == [] and draft2.body == body
    assert parse_verdicts('[{"i": 9, "verdict": "UNSUPPORTED"}, {"i": 1, "verdict": "maybe"}]', 2) == {}


def test_made_up_double_bracket_tokens_and_id_only_citations_are_removed():
    from utils.section_writer import normalize_fact_refs

    assert normalize_fact_refs("lagged effects [[F15], [F16]] and more [S3].", 50) == "lagged effects  and more [S3]."
    draft, _ = _fact_postprocess("The MSP rose to 1815 in 2019-20 [S2.1] [[Part IV]]. It rose again later [S2.1].")
    assert "[[" not in draft.body and "1815" in draft.body


def test_question_year_window_reads_month_names_and_split_years():
    # inflation: "from January 2022 to December 2024" read as 2024-2026 (no 2022-2023 chart or dataset);
    # agri: "from 2018-19 to 2024-25" read as 2018-2026.
    from datetime import date

    from utils.data_angles import requested_year_bounds

    today = date(2026, 11, 17)
    assert requested_year_bounds("move from January 2022 to December 2024", today) == (2022, 2024)
    assert requested_year_bounds("changed from 2018-19 to 2024-25?", today) == (2018, 2025)
    assert requested_year_bounds("between March 2023 and June 2025", today) == (2023, 2025)
    assert requested_year_bounds("in the 2023-24 fiscal year", today) == (2023, 2024)
    assert requested_year_bounds("from FY2019 to FY2024", today) == (2018, 2024)
    assert requested_year_bounds("from 2015 to 2023", today) == (2015, 2023)


def test_official_data_planner_falls_back_to_the_questions_own_series():
    # telecom / education / Europe gas: the planner chose no dataset although the catalog holds the subject's series.
    import asyncio
    from datetime import date
    from types import SimpleNamespace

    from utils.official_data import plan_datasets

    class Gateway:
        async def generate(self, **kwargs):
            return SimpleNamespace(ok=True, json=lambda: {"datasets": []})

    plan, note = asyncio.run(plan_datasets(
        "How have gross enrolment ratios in higher education in India changed from 2018 to 2024?", Gateway(),
        today=date(2026, 11, 17)))
    assert plan and plan[0]["id"] == "wb.tertiary_enrolment" and "fallback" in note["method"]
    steel, _ = asyncio.run(plan_datasets("How has India's crude steel production grown from 2019 to 2024?",
                                         Gateway(), today=date(2026, 11, 17)))
    assert not any(item["id"].endswith("oil_prod") for item in steel)


def test_chart_meaning_rule_is_shared_by_creation_and_publication():
    # The final gate removed "Value: 4 periods" / off-topic charts after the writer had written around them.
    from utils.chart_evidence_audit import chart_meaning_reason

    question = "How have electric vehicle sales and public charging infrastructure in India grown from FY2021 to FY2025?"
    good = {"title": "Electric vehicle sales in India (units), FY2021 to FY2025", "type": "line",
            "series": [{"name": "EV sales", "data": [{"label": "FY2021", "value": 1}, {"label": "FY2022", "value": 2}]}]}
    off_topic = {"title": "Steel prices: TMT, HR coil and CR coil", "type": "bar",
                 "series": [{"name": "Price", "data": [{"label": "TMT", "value": 1}, {"label": "HR", "value": 2}]}]}
    untitled = {"title": "Value: 4 periods", "type": "line",
                "series": [{"name": "Value", "data": [{"label": "2022", "value": 1}, {"label": "2023", "value": 2}]}]}
    assert chart_meaning_reason(good, question) == ""
    assert chart_meaning_reason(off_topic, question)
    assert chart_meaning_reason(untitled, question)


def test_insurance_density_values_are_all_offered_and_a_reused_id_is_caught():
    # insurance live run: only "USD 58" of "USD 78 in 2019 (Life- USD 58 and Non-Life - USD 20)" was a fact; the
    # writer used that one id for 78, 58 and 20 (and "from [[F104]] in FY15 to [[F104]] in FY23").
    from types import SimpleNamespace

    from utils.section_writer import numbered_facts, resolve_fact_refs

    passage = SimpleNamespace(passage_id="S8.2", published="2024-01-01", text=(
        "Insurance density was USD 11.5 in 2001, reached to USD 78 in 2019 (Life- USD 58 and Non-Life - USD 20)."))
    facts = numbered_facts([passage])
    values = {f.value for f in facts}
    assert {"78", "58", "20"} <= values
    f58 = next(i for i, f in enumerate(facts, 1) if f.value == "58")
    prose, _ = resolve_fact_refs(f"Density rose from [[F{f58}]] to [[F{f58}]] by 2019 [S8.2].", facts)
    table, _ = resolve_fact_refs(f"| Life | [[F{f58}]] |\n| Non-Life | [[F{f58}]] |", facts)
    single, _ = resolve_fact_refs(f"Life density was [[F{f58}]] in 2019 [S8.2].", facts)
    assert "REPEATED_FACT" in prose and table.count("REPEATED_FACT") == 2 and "58" in single


def test_short_detailed_outline_is_filled_to_its_section_minimum():
    # Live sector-rotation run: 4 sections against a minimum of 5; repair only handled in-depth outlines.
    from utils.report_outline import Section, repair_outline, validate_outline

    sections = [Section(section_id=f"sec-{i}", title=t, goal="g", queries=["q"]) for i, t in enumerate(
        ["Quarterly Performance Review", "Analysis of Sector Dispersion", "Drivers of Sector Rotation",
         "Current Market Status"], 1)]
    question = "How can you explain & analyze the sector rotation in Indian stock markets in the past 8 quarters"
    decisions: list[str] = []
    repaired = repair_outline(sections, "detailed", None, decisions, question=question)
    assert len(repaired) >= 5 and not [e for e in validate_outline(repaired, "detailed", None, question=question)
                                        if "analysis sections" in e]
    titles = [s.title for s in repaired]
    assert "Key Drivers and Causes" not in titles  # the outline already has a drivers section
    assert any("sector rotation" in q.casefold() for s in repaired[4:] for q in s.queries) and decisions


def test_pdf_tables_keep_their_rows_and_columns():
    # 15 SEBI/NSE PDFs were read as flat text; their tables could not be charted or tied to periods.
    import fitz

    from utils.source_extractor import _extract_pdf

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 60), "Table 2: Sectoral index returns (per cent)", fontsize=11)
    rows = [["Index", "Q1 FY26", "Q2 FY26"], ["Nifty Pharma", "4.2", "-3.1"], ["Nifty Bank", "6.5", "2.4"],
            ["Nifty IT", "-5.0", "1.8"]]
    y = 90
    for row in rows:
        x = 50
        for cell in row:
            page.draw_rect(fitz.Rect(x, y, x + 140, y + 22))
            page.insert_text((x + 4, y + 15), cell, fontsize=10)
            x += 140
        y += 22
    text = _extract_pdf(doc.tobytes(), 20000, 8.0)
    assert "TABLE 1 — Table 2: Sectoral index returns (per cent) (PDF page 1)" in text
    assert "Index | Q1 FY26 | Q2 FY26" in text and "Nifty Pharma | 4.2 | -3.1" in text
