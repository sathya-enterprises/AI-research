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
