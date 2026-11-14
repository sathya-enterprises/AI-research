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
