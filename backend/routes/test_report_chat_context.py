"""Report follow-up questions stay grounded in the full report and its chart points."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes import chat
from utils.orchestrator import OrchestrationResult
from utils.report_chat_context import (
    build_report_chat_context,
    compact_paginated_file_context,
    is_report_reference_question,
    report_followup_needs_live_sources,
    report_followup_system_prompt,
)


def _report_context() -> dict:
    return {
        "title": "Home prices: report",
        "question": "Analyze the uploaded home-price data.",
        "summary": "Prices increased across the sampled areas; the sample is small.",
        "report": (
            "## Data and scope\nThe report uses six rows from the supplied CSV.\n\n"
            "## Price trend\nMedian price rises across the sample. [CHART_1]\n\n"
            "## Bedroom comparison\nThe four-bedroom home is higher priced. [CHART_2]\n"
        ),
        "keyStats": [{"label": "Median price", "value": "$565,000"}],
        "charts": [
            {"title": "Median price by area", "type": "line", "unit": "USD", "xLabel": "Area", "yLabel": "Price",
             "series": [{"name": "Homes", "data": [
                 {"label": "2,600 sq ft", "value": 550000},
                 {"label": "3,000 sq ft", "value": 565000},
                 {"label": "3,200 sq ft", "value": 610000},
             ]}]},
            {"title": "Home price by bedroom count", "type": "bar", "unit": "USD",
             "series": [{"name": "Median price", "data": [
                 {"label": "3 bedrooms", "value": 550000},
                 {"label": "4 bedrooms", "value": 565000},
             ]}]},
        ],
    }


def test_report_question_detection_separates_followups_from_new_topics():
    assert is_report_reference_question("Summarize the whole report")
    assert is_report_reference_question("Explain chart 2")
    assert is_report_reference_question("Walk me through this inch by inch")
    assert is_report_reference_question("Why did that happen?", [{"role": "assistant", "content": "Report preview"}])
    assert not is_report_reference_question("What is the weather forecast in Boston?")
    assert report_followup_needs_live_sources("Compare this report with the latest data")
    assert not report_followup_needs_live_sources("Explain the current report's conclusion")


def test_report_summary_context_includes_sections_and_chart_overviews():
    context = build_report_chat_context(_report_context(), "Give me a summary of the whole report")
    assert "Data and scope" in context
    assert "Price trend" in context
    assert "Bedroom comparison" in context
    assert "Median price by area" in context
    assert "Home price by bedroom count" in context
    assert "2,600 sq ft=550000" in context
    assert len(context) <= 9_000


def test_chart_question_gets_that_chart_values_and_nearby_report_section():
    context = build_report_chat_context(_report_context(), "Explain chart 2")
    assert "Chart 2: Home price by bedroom count" in context
    assert "3 bedrooms=550000" in context
    assert "4 bedrooms=565000" in context
    assert "Bedroom comparison" in context
    assert "Related report section" in context


def test_scatter_chart_followup_keeps_x_and_y_values_paired_by_source_row():
    payload = _report_context()
    payload["charts"] = [{
        "title": "Price vs area - homeprices", "type": "scatter",
        "xLabel": "Area", "yLabel": "Price",
        "series": [
            {"name": "area", "data": [
                {"label": "Row 2", "value": 2600}, {"label": "Row 3", "value": 3000},
            ]},
            {"name": "price", "data": [
                {"label": "Row 2", "value": 550000}, {"label": "Row 3", "value": 565000},
            ]},
        ],
    }]

    context = build_report_chat_context(payload, "Explain the price vs area chart")

    assert "Row 2: area=2600; price=550000" in context
    assert "Row 3: area=3000; price=565000" in context


def test_heatmap_followup_includes_every_sector_and_quarter_value():
    payload = _report_context()
    payload["report"] = "## Sector rotation\nLeadership changed between sectors. [CHART_1]"
    payload["charts"] = [{
        "title": "Quarterly sector returns across the Indian market",
        "type": "heatmap", "unit": "%", "xLabel": "Fiscal quarter", "yLabel": "Sector",
        "columns": ["Q3 FY25", "Q4 FY25", "Q1 FY26"],
        "rows": ["Nifty Auto", "Nifty Bank"],
        "values": [[-4.25, 3.1, 8.6], [1.5, -2.4, 5.2]],
    }]

    context = build_report_chat_context(payload, "Explain the heatmap chart")

    assert "Chart 1: Quarterly sector returns" in context
    assert "Nifty Auto: Q3 FY25=-4.25; Q4 FY25=3.1; Q1 FY26=8.6" in context
    assert "Nifty Bank: Q3 FY25=1.5; Q4 FY25=-2.4; Q1 FY26=5.2" in context
    assert "Related report section" in context


def test_sector_rotation_chart_reference_includes_the_full_eight_quarter_matrix():
    payload = _report_context()
    payload["question"] = "How can you explain and analyse the sector rotation in Indian stock markets in the past 8 quarters?"
    payload["report"] = "## Sector rotation\nLeadership changed between sectors. [CHART_1]"
    sectors = ["Nifty Auto", "Nifty Bank", "Nifty Energy", "Nifty FMCG", "Nifty IT",
               "Nifty Metal", "Nifty Pharma", "Nifty PSU Bank", "Nifty Realty", "Nifty Media", "Nifty 50"]
    quarters = ["Q3 FY25", "Q4 FY25", "Q1 FY26", "Q2 FY26", "Q3 FY26", "Q4 FY26", "Q1 FY27", "Q2 FY27"]
    values = [[round((row_index * len(quarters) + column_index) / 3 - 10, 2)
               for column_index in range(len(quarters))] for row_index in range(len(sectors))]
    payload["charts"] = [{
        "title": "Quarterly sector returns (%): 10 sectors and the Nifty 50 benchmark, Q3 FY25 to Q2 FY27",
        "type": "heatmap", "unit": "%", "xLabel": "Fiscal quarter", "yLabel": "Sector index",
        "columns": quarters, "rows": sectors, "values": values,
    }]

    context = build_report_chat_context(payload, "Explain the sector rotation chart")

    assert "Chart 1: Quarterly sector returns" in context
    for sector, row in zip(sectors, values):
        expected = sector + ": " + "; ".join(
            f"{quarter}={float(value):.8g}" for quarter, value in zip(quarters, row)
        )
        assert expected in context
    assert len(context) <= 28_000


def test_whole_report_heatmap_overview_stays_compact_but_shows_matrix_edges():
    payload = _report_context()
    payload["charts"] = [{
        "title": "Quarterly sector returns", "type": "heatmap", "unit": "%",
        "columns": ["Q3 FY25", "Q4 FY25", "Q1 FY26"],
        "rows": ["Nifty Auto", "Nifty Bank", "Nifty IT"],
        "values": [[-4.25, 3.1, 8.6], [1.5, -2.4, 5.2], [2.2, 4.0, -1.3]],
    }]

    context = build_report_chat_context(payload, "Summarize the whole report")

    assert "Nifty Auto: Q3 FY25=-4.25; Q1 FY26=8.6" in context
    assert "Nifty Bank: Q3 FY25=1.5; Q1 FY26=5.2" in context
    assert "1 more rows" in context
    assert len(context) <= 9_000


def test_report_chat_prompt_does_not_infer_unstated_currency_or_units():
    payload = _report_context()
    payload["charts"][0]["unit"] = "as reported"
    context = build_report_chat_context(payload, "Explain chart 1")
    prompt = report_followup_system_prompt(context)
    assert "unit/currency is not specified in the source" in context
    assert "never infer dollars, rupees, or another unit" in prompt


def test_report_context_omits_report_for_an_unrelated_question_and_caps_long_reports():
    payload = _report_context()
    assert build_report_chat_context(payload, "What is the weather in Boston?") == ""
    payload["report"] = "\n".join(f"## Section {i}\nEvidence {i} [CHART_1]" for i in range(100))
    payload["summary"] = "summary " * 5_000
    detailed_context = build_report_chat_context(payload, "Walk me through the report inch by inch")
    assert len(detailed_context) <= 28_000
    assert "Section 99" in detailed_context and "Evidence 99" in detailed_context


def test_pdf_chat_context_keeps_all_page_markers_and_requested_page_detail():
    text = "[Attached File: report.pdf]\n\n" + "\n\n".join(
        f"[Page {page}] start of page {page} " + ("details " * 180) + f" end marker page-{page}"
        for page in range(1, 13)
    )
    compacted = compact_paginated_file_context(text, "Explain page 10", max_chars=9_000)
    assert len(compacted) <= 9_000
    assert all(f"[Page {page}]" in compacted for page in range(1, 13))
    assert "end marker page-10" in compacted


def test_chat_route_passes_report_reference_without_triggering_research(monkeypatch):
    captured: dict = {}

    async def fake_orchestrate(**kwargs):
        captured["skip"] = kwargs["skip"]
        return OrchestrationResult()

    async def fake_rewrite(*args, **kwargs):
        raise AssertionError("report explanation should not spend a search-rewrite call")

    async def fake_stream(system_prompt, messages):
        captured["prompt"] = system_prompt
        captured["messages"] = messages
        return None

    monkeypatch.setattr(chat, "orchestrate", fake_orchestrate)
    monkeypatch.setattr(chat, "rewrite_query_for_search", fake_rewrite)
    monkeypatch.setattr(chat, "stream_groq", fake_stream)
    monkeypatch.setattr(chat, "get_gemini_keys", lambda: [])

    app = FastAPI()
    app.include_router(chat.router, prefix="/api/chat")
    with TestClient(app) as client:
        response = client.post("/api/chat", json={
            "messages": [
                {"role": "assistant", "content": "Report ready."},
                {"role": "user", "content": "Explain chart 2"},
            ],
            "reportContext": _report_context(),
        })

    assert response.status_code == 200
    assert captured["skip"] is True
    assert "4 bedrooms=565000" in captured["prompt"]
    assert "REPORT FOLLOW-UP REFERENCE" in captured["prompt"]
    assert "CURRENT EXTERNAL EVIDENCE" not in captured["prompt"]


def test_unrelated_question_does_not_inherit_the_last_report(monkeypatch):
    captured: dict = {}

    async def fake_orchestrate(**kwargs):
        captured["skip"] = kwargs["skip"]
        return OrchestrationResult()

    async def fake_rewrite(question, *args, **kwargs):
        captured["rewrite"] = question
        return [question]

    async def fake_stream(system_prompt, messages):
        captured["prompt"] = system_prompt
        return None

    monkeypatch.setattr(chat, "orchestrate", fake_orchestrate)
    monkeypatch.setattr(chat, "rewrite_query_for_search", fake_rewrite)
    monkeypatch.setattr(chat, "stream_groq", fake_stream)
    monkeypatch.setattr(chat, "get_gemini_keys", lambda: [])

    app = FastAPI()
    app.include_router(chat.router, prefix="/api/chat")
    with TestClient(app) as client:
        response = client.post("/api/chat", json={
            "messages": [
                {"role": "assistant", "content": "Report ready."},
                {"role": "user", "content": "What is the weather forecast in Boston?"},
            ],
            "reportContext": _report_context(),
        })

    assert response.status_code == 200
    assert captured["skip"] is False
    assert captured["rewrite"] == "What is the weather forecast in Boston?"
    assert "REPORT FOLLOW-UP REFERENCE" not in captured["prompt"]

