"""Report editing engine: element addressing, page mapping, location resolution, edit plans, evidence sufficiency,
chart explanations, edit-made chart checks, claim checks on new facts, scoped source re-verification, scheduling,
concurrency and metrics. Mocked model calls; real splicing, planning, evidence and audit code."""
import asyncio
import json
import time

import fitz
import pytest

import routes.report as R
from utils import chart_explain as CE
from utils import edit_evidence as EV
from utils import edit_location as EL
from utils import edit_plan as EP
from utils import pdf_page_map as PM
from utils import report_elements as E

REPORT = """## Market Overview

Electric two-wheeler registrations reached 1.15 million in FY25 [S1].
TVS led with a 23% share while Ola fell to 19% [S2].
Battery prices kept falling through the year [S3].
[CHART_1]
Registrations grew in every quarter of the year [S1].

### Regional Trends

Maharashtra and Karnataka led adoption [S4].
| State | Share |
|---|---|
| Maharashtra | 14% |
| Karnataka | 12% |
[CHART_2]

## Policy

PM E-DRIVE pays Rs 5,000 per vehicle [S5].
"""

CHARTS = [
    {"type": "line", "title": "Figure 1: Electric two-wheeler registrations (lakh units), FY21 to FY25", "unit": "lakh units",
     "xLabel": "Year", "yLabel": "Registrations (lakh units)", "sourceIds": ["S1"],
     "series": [{"name": "Registrations", "data": [{"label": "FY21", "value": 0.4}, {"label": "FY22", "value": 2.5},
                                                   {"label": "FY23", "value": 7.3}, {"label": "FY24", "value": 9.5},
                                                   {"label": "FY25", "value": 11.5}]}]},
    {"type": "bar", "title": "Figure 2: Share of electric two-wheeler sales by state (%)", "unit": "%", "sourceIds": ["S4"],
     "xLabel": "State", "yLabel": "Share (%)",
     "series": [{"name": "Share", "data": [{"label": "Maharashtra", "value": 14}, {"label": "Karnataka", "value": 12},
                                           {"label": "Tamil Nadu", "value": 9}]}]},
]

SOURCES = [
    {"id": "S1", "title": "Vahan registrations", "url": "https://vahan.example.gov.in/e2w",
     "evidenceExcerpt": "Electric two-wheeler registrations reached 1.15 million in FY25, up from 0.95 million in FY24.",
     "fetchedAt": "2099-01-01T00:00:00Z"},
    {"id": "S2", "title": "Market share note", "url": "https://news.example.com/share",
     "evidenceExcerpt": "TVS Motor held a 23% share of electric two-wheeler sales in FY25; Ola Electric had 19%.",
     "fetchedAt": "2099-01-01T00:00:00Z"},
    {"id": "S3", "title": "Battery costs", "url": "https://battery.example.org/prices",
     "evidenceExcerpt": "Battery pack prices fell 20% in 2025 as cell costs declined.", "fetchedAt": "2099-01-01T00:00:00Z"},
    {"id": "S4", "title": "State adoption", "url": "https://states.example.org/adoption",
     "evidenceExcerpt": "Maharashtra held 14% and Karnataka 12% of electric two-wheeler sales in FY25.",
     "fetchedAt": "2099-01-01T00:00:00Z"},
    {"id": "S5", "title": "PM E-DRIVE", "url": "https://heavyindustries.example.gov.in/pm-e-drive",
     "evidenceExcerpt": "PM E-DRIVE provides Rs 5,000 per electric two-wheeler.", "fetchedAt": "2099-01-01T00:00:00Z"},
]


def _body(instruction, **extra):
    return {"report": REPORT, "title": "EV report", "charts": json.loads(json.dumps(CHARTS)),
            "sources": json.loads(json.dumps(SOURCES)), "images": [], "editInstruction": instruction,
            "reportId": "r1", "revision": 1, "reportTier": "detailed", **extra}


def _pdf(pages: list[str]) -> bytes:
    doc = fitz.open()
    for text in pages:
        page = doc.new_page()
        page.insert_textbox(fitz.Rect(40, 40, 560, 800), text, fontsize=9)
    return doc.tobytes()


PDF_PAGES = [
    "Contents\nMarket Overview\nRegional Trends\nPolicy",
    "Market Overview\nElectric two-wheeler registrations reached 1.15 million in FY25.\nTVS led with a 23% share while Ola fell to 19%.\n"
    "Battery prices kept falling through the year.",
    "Figure 1: Electric two-wheeler registrations (lakh units), FY21 to FY25\nRegistrations grew in every quarter of the year.",
    "Regional Trends\nMaharashtra and Karnataka led adoption.\nState Share\nMaharashtra 14%\n"
    "Figure 2: Share of electric two-wheeler sales by state (%)\nPolicy\nPM E-DRIVE pays Rs 5,000 per vehicle.",
]


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    async def enrich(item, *a, **k):
        return dict(item)
    monkeypatch.setattr(R, "_enrich_report_source", enrich)
    R._ACTION_JOBS.clear()
    R._ACTION_REPORT_REVISIONS.clear()
    yield
    R._ACTION_JOBS.clear()


def _run(body):
    return asyncio.run(R.edit_report_section(R._ReportJobRequest(body)))


def _payload(resp):
    return resp.status_code, json.loads(resp.body.decode("utf-8"))


# ── elements, pages, locations ─────────────────────────────────────────────────────────────────────────────
def test_elements_have_stable_ids_and_splice_touches_nothing_else():
    els = E.parse(REPORT)
    kinds = [e.kind for e in els]
    assert kinds.count("chart") == 2 and kinds.count("table") == 1 and kinds.count("heading") == 3
    assert E.by_id(els, "chart:1") and E.by_id(els, "p:market-overview:2").text.startswith("TVS led")
    third = E.by_id(els, "p:market-overview:3")
    out = E.splice(REPORT, third, "New paragraph [S3].", mode="after")
    assert out.replace("\nNew paragraph [S3].\n", "", 1) == REPORT
    assert [e.id for e in E.parse(REPORT)] == [e.id for e in els]                    # deterministic


def test_page_map_skips_the_contents_page_and_follows_reading_order():
    els = E.parse(REPORT)
    pm = PM.build(_pdf(PDF_PAGES), els, CHARTS)
    assert pm.contents_pages == {1}
    assert E.by_id(els, "h:market-overview").page == 2
    assert E.by_id(els, "chart:1").page == 3 and E.by_id(els, "chart:2").page == 4
    assert E.by_id(els, "h:policy").page == 4


def test_page_map_is_rebuilt_after_pagination_changes():
    els = E.parse(REPORT)
    shifted = PDF_PAGES[:2] + ["(new explanation filling a page)"] + PDF_PAGES[2:]
    assert PM.build(_pdf(PDF_PAGES), els, CHARTS).page_of["chart:1"] == 3
    assert PM.build(_pdf(shifted), E.parse(REPORT), CHARTS).page_of["chart:1"] == 4


@pytest.mark.parametrize("instruction,expected", [
    ("Explain the chart on page 3", ("chart:1", "after")),
    ("Replace the second chart", ("chart:2", "replace")),
    ("Add a paragraph below the third paragraph on page 2", ("p:market-overview:3", "after")),
    ("Explain Figure 2 in detail", ("chart:2", "after")),
    ("Insert a table before the Policy section", ("h:policy", "before")),
    ("Explain the chart below the Regional Trends heading", ("chart:2", "after")),
])
def test_positions_resolve_to_exact_elements(instruction, expected):
    els = E.parse(REPORT)
    PM.build(_pdf(PDF_PAGES), els, CHARTS)
    loc = EL.resolve(instruction, els, CHARTS)
    assert loc is not None and loc.resolved and (loc.element.id, loc.relation) == expected


@pytest.mark.parametrize("instruction", ["Explain the chart on page 4 and the table", "Explain the third chart on page 3",
                                         "Add a graph on page 40"])
def test_unclear_positions_ask_instead_of_guessing(instruction):
    els = E.parse(REPORT)
    PM.build(_pdf(PDF_PAGES), els, CHARTS)
    if "page 4" in instruction:  # two visuals of different kinds is fine; make the chart reference ambiguous instead
        els_two = E.parse(REPORT + "\n[CHART_1]\n")
        PM.build(_pdf(PDF_PAGES[:-1] + [PDF_PAGES[-1] + "\nFigure 1: Electric two-wheeler registrations (lakh units)"]), els_two, CHARTS)
        loc = EL.resolve("Explain the chart on page 4", els_two, CHARTS)
    else:
        loc = EL.resolve(instruction, els, CHARTS)
    assert loc is not None and loc.question and not loc.resolved


def test_section_level_and_multi_section_wording_keeps_the_section_editor():
    els = E.parse(REPORT)
    assert EL.resolve("Expand the Policy section with more detail", els, CHARTS) is None
    assert EL.resolve("Improve the Market Overview and Policy sections", els, CHARTS) is None
    assert EL.resolve("Use a table instead of failing the whole edit", els, CHARTS) is None


def test_plans_route_by_operation_and_target():
    els = E.parse(REPORT)
    plan = lambda q: EP.plan(q, els, CHARTS, resolve_location=EL.resolve)
    assert plan("Explain Figure 1 for a non-technical reader").route == "explain_element"
    assert plan("Add a paragraph after the second paragraph about battery warranties").route == "insert_element"
    assert plan("Add a chart below the Regional Trends heading comparing states").route == "section"
    assert plan("Rewrite the Policy section more concisely").route == "section"
    assert "simplify" in EP.operations("Make the conclusion easier to read for non-technical readers")
    assert EP.operations("Do something unusual with this section") == ["modify"]       # unforeseen wording still maps
    needs = EP.evidence_needs("Add China's 2025 EV sales next to India's")
    assert {n.entity for n in needs} >= {"China", "India"} and all(n.metric == "sales" and n.periods == ["2025"] for n in needs)


# ── evidence sufficiency ──────────────────────────────────────────────────────────────────────────────────────
def test_existing_evidence_covers_a_fact_and_a_missing_fact_is_named():
    store = EV.build_store(REPORT, SOURCES, CHARTS)
    covered = EV.assess(store, EP.evidence_needs("Add TVS market share in FY25"))
    assert covered.sufficient
    missing = EV.assess(store, EP.evidence_needs("Compare with China EV sales in 2025"))
    assert not missing.sufficient and "China" in missing.research_instruction("x")
    assert all(p.url for p in EV.passages_for(store, "TVS share FY25") if p.source_id.startswith("S"))


def test_sufficient_evidence_means_no_web_search_and_missing_evidence_searches_only_for_the_gap(monkeypatch):
    calls = []

    async def research(instruction, heading, title, sources):
        calls.append(instruction)
        return "[S9] China EV data\nURL: https://china.example.org\nEvidence: China sold 7.1 million electric two-wheelers in 2025.", list(sources)

    async def llm(system, user):
        return "Electric two-wheeler registrations reached 1.15 million in FY25 [S1]."

    monkeypatch.setattr(R, "_action_web_research", research)
    monkeypatch.setattr(R, "_call_llm_plain", llm)
    _run(_body("Add a paragraph after the first paragraph with TVS market share in FY25"))
    assert calls == []                                                                   # covered: no search
    _run(_body("Add a paragraph after the first paragraph comparing China EV sales in 2025"))
    assert len(calls) == 1 and calls[0].startswith("Find reliable, comparable figures for: China")


# ── element-level edits end to end ────────────────────────────────────────────────────────────────────────────
def test_a_chart_explanation_is_inserted_right_after_the_chart_and_nothing_else_changes(monkeypatch):
    prompts = []

    async def llm(system, user):
        prompts.append(user)
        return ("Registrations rose from 0.4 lakh units in FY21 to 11.5 lakh units in FY25, the highest point on the "
                "chart, with the steepest climb between FY22 and FY23.")

    monkeypatch.setattr(R, "_call_llm_plain", llm)
    status, payload = _payload(_run(_body("Explain Figure 1 in more detail")))
    assert status == 200 and payload["explanationAdded"] and payload["editedElement"] == "chart:1"
    assert "highest 11.5 (FY25)" in prompts[0]                                           # the chart's own data
    assert payload["report"].index("[CHART_1]") < payload["report"].index("Registrations rose from 0.4")
    before, after = REPORT.split("[CHART_1]\n")
    assert payload["report"].startswith(before + "[CHART_1]\n") and payload["report"].endswith(after)
    assert payload["charts"] == CHARTS                                                   # the chart itself unchanged


def test_an_explanation_with_invented_numbers_or_unsourced_causes_is_refused(monkeypatch):
    async def llm(system, user):
        return "Registrations hit 99 lakh units because consumers loved scooters."

    monkeypatch.setattr(R, "_call_llm_plain", llm)
    status, payload = _payload(_run(_body("Explain Figure 1")))
    assert status == 422 and payload["preserved"] is True
    assert any("unsupported number 99" in d for d in payload["details"])
    assert any("unsourced causal claim" in d for d in payload["details"])


def test_a_paragraph_is_inserted_exactly_after_the_requested_paragraph(monkeypatch):
    async def llm(system, user):
        return "TVS's lead reflects its 23% share in FY25 [S2]."

    monkeypatch.setattr(R, "_call_llm_plain", llm)
    status, payload = _payload(_run(_body("Add a paragraph below the second paragraph about TVS's position")))
    assert status == 200
    text = payload["report"]
    assert text.index("TVS led with") < text.index("TVS's lead reflects") < text.index("Battery prices kept")


def test_page_references_use_the_rendered_pdf_and_ambiguity_asks(monkeypatch):
    async def render(body):
        return _pdf(PDF_PAGES)

    async def llm(system, user):
        return "The state chart shows Maharashtra ahead at 14% and Karnataka at 12% [S4]."

    monkeypatch.setattr(R, "_action_render_current_pdf", render)
    monkeypatch.setattr(R, "_call_llm_plain", llm)
    status, payload = _payload(_run(_body("Explain the chart on page 4")))
    assert status == 200 and payload["editedElement"] == "chart:2"
    assert payload["pagePlacement"]["editedElementPage"] == 4
    status, payload = _payload(_run(_body("Explain the third chart on page 3")))
    assert status == 422 and payload.get("clarification") and payload["preserved"] is True


# ── chart checks, claim checks, source re-verification ────────────────────────────────────────────────────────
def test_edit_made_charts_pass_the_report_chart_rules():
    meta = {"type": "bar", "title": "x", "xLabel": "Variable", "yLabel": "Coefficient",
            "series": [{"name": "Coefficient", "data": [{"label": f"v{i}", "value": i + 1.5} for i in range(4)]}]}
    assert not R._action_new_chart_ok(meta, CHARTS)                                      # regression output
    assert not R._action_new_chart_ok(json.loads(json.dumps(CHARTS[0])), CHARTS)         # duplicate
    mixed = {"type": "line", "title": "India vs China sales", "unit": "million", "xLabel": "Year", "yLabel": "Sales (million)",
             "series": [{"name": "India", "data": [{"label": "2025", "value": 1.2}, {"label": "2026", "value": 1.4}]},
                        {"name": "China", "data": [{"label": "2023", "value": 6.0}, {"label": "2024", "value": 6.8}]}]}
    assert not R._action_new_chart_ok(mixed, CHARTS)                                     # different periods
    two = {"type": "bar", "title": "EV sales, India and China, 2025", "unit": "million", "xLabel": "Country",
           "yLabel": "Sales (million)", "series": [{"name": "Sales", "data": [{"label": "India", "value": 1.2},
                                                                             {"label": "China", "value": 7.1}]}]}
    assert R._action_new_chart_ok(two, CHARTS)                                           # a requested 2-point comparison


def test_new_facts_are_claim_checked_and_wrong_arithmetic_is_corrected():
    candidate = REPORT.replace("Battery prices kept falling through the year [S3].",
                               "Battery prices kept falling through the year [S3]. Registrations rose from 0.95 million in FY24 "
                               "to 1.15 million in FY25, a 50% increase [S1].")
    revised, summary = asyncio.run(R._action_claim_check(REPORT, candidate, _body("x")))
    assert summary is not None and summary["corrected"]
    assert "21%" in revised or "21.1%" in revised
    unchanged, none = asyncio.run(R._action_claim_check(REPORT, REPORT, _body("x")))
    assert unchanged == REPORT and none is None


def test_only_sources_the_edit_can_affect_are_fetched_again(monkeypatch):
    fetched = []

    async def enrich(item, *a, **k):
        fetched.append(item.get("id"))
        return dict(item)

    monkeypatch.setattr(R, "_enrich_report_source", enrich)
    candidate = REPORT.replace("PM E-DRIVE pays Rs 5,000 per vehicle [S5].",
                               "PM E-DRIVE pays Rs 5,000 per vehicle [S5], capped by battery size [S5].")
    args = dict(original_report=REPORT, candidate_report=candidate, title="t", original_charts=CHARTS,
                candidate_charts=CHARTS, original_images=[], candidate_images=[], original_sources=SOURCES,
                candidate_sources=SOURCES, report_tier="detailed")
    asyncio.run(R._audit_action_candidate(**args))
    assert fetched == ["S5"]                                                             # only the changed paragraph
    fetched.clear()
    monkeypatch.setenv("GG_ACTION_AUDIT_SCOPE", "full")
    asyncio.run(R._audit_action_candidate(**args))
    assert sorted(fetched) == ["S1", "S2", "S3", "S4", "S5"]
    fetched.clear()
    monkeypatch.delenv("GG_ACTION_AUDIT_SCOPE")
    stale = [dict(s, fetchedAt="2001-01-01T00:00:00Z") if s["id"] == "S2" else s for s in SOURCES]
    asyncio.run(R._audit_action_candidate(**{**args, "original_sources": stale, "candidate_sources": stale}))
    assert sorted(fetched) == ["S2", "S5"]                                               # stale evidence re-verified


# ── scheduling, concurrency, metrics, RAG waits ───────────────────────────────────────────────────────────────
def test_edit_model_calls_go_through_one_scheduled_gateway_call(monkeypatch):
    calls = []

    class Gateway:
        async def generate(self, **kwargs):
            calls.append(kwargs["purpose"])
            from utils.llm_gateway import LLMResult
            return LLMResult(ok=True, text='{"a": 1}' if kwargs.get("json_mode") else "text", model="gemini")

    token = R._ACTION_GATEWAY.set(Gateway())
    try:
        assert asyncio.run(R._call_llm_plain("s", "u")) == "text"
        assert asyncio.run(R._call_llm_json("s", "u")) == {"a": 1}
    finally:
        R._ACTION_GATEWAY.reset(token)
    assert calls == ["action_edit", "action_json"]                                       # one call each, no waves


def test_concurrent_edits_of_one_report_conflict_but_other_reports_run(monkeypatch):
    submitted = []
    monkeypatch.setattr(R, "_submit_action_job", lambda job_id, body: submitted.append(job_id))
    start = lambda body: asyncio.run(R.start_action_edit_job(R._ReportJobRequest(body)))
    assert start(_body("Expand the Policy section")).status_code == 202
    assert start(_body("Shorten the Policy section")).status_code == 409                 # same report, still running
    assert start({**_body("Expand"), "reportId": "r2"}).status_code == 202               # another report
    R._ACTION_JOBS.clear()
    R._ACTION_REPORT_REVISIONS["r1"] = 3
    response = start(_body("Expand the Policy section", revision=2))
    assert response.status_code == 409 and json.loads(response.body)["conflict"] == "stale_revision"
    assert R._action_workers() == 3


def test_a_job_records_queue_and_stage_metrics(monkeypatch):
    async def edit(request):
        R._action_metric("research", 1.5, count=1)
        from fastapi.responses import JSONResponse
        return JSONResponse({"report": "## A\nx", "reportId": "r1", "revision": 2})

    monkeypatch.setattr(R, "edit_report_section", edit)
    monkeypatch.setenv("GG_ACTION_GATEWAY", "0")
    R._ACTION_JOBS["j1"] = {"status": "queued", "createdAt": time.time() - 2, "reportKey": "r1"}
    asyncio.run(R._run_action_job_async("j1", _body("x")))
    metrics = R._ACTION_JOBS["j1"]["result"]["editMetrics"]
    assert metrics["queueSeconds"] >= 1.9 and metrics["seconds"]["research"] == 1.5 and "totalSeconds" in metrics
    assert R._ACTION_REPORT_REVISIONS["r1"] == 2


def test_an_unreachable_document_service_does_not_hold_up_an_edit(monkeypatch):
    from utils import rag_client as C
    waits = []

    async def wake(max_wait=100.0, poll_interval=5.0):
        waits.append(max_wait)
        return False

    monkeypatch.setattr(C, "_wake_rag_service", wake)
    started = time.perf_counter()
    result = asyncio.run(C.rag_report("5f0c2a9e-1d3b-4c7a-9e21-6b8d4f3a2c10", "q", "action", wake_wait=6))
    assert waits == [6] and result["has_content"] is False and result["reason"] == "service_unavailable"
    assert time.perf_counter() - started < 2


# ── chart facts ───────────────────────────────────────────────────────────────────────────────────────────────
def test_chart_facts_and_explanation_checks():
    facts = CE.chart_facts(CHARTS[0])
    s = facts["series"][0]
    assert s["highest"] == ("FY25", 11.5) and s["lowest"] == ("FY21", 0.4) and s["cagrPct"] == pytest.approx(131.57, 0.01)
    ok = "Registrations rose from 0.4 to 11.5 lakh units, a compound annual rate of about 131.6%."
    assert CE.check_explanation(ok, CHARTS[0], facts, {}) == []
    assert CE.check_explanation("Sales grew due to subsidies.", CHARTS[0], facts, {}) == [
        "unsourced causal claim: Sales grew due to subsidies."]
    assert CE.check_explanation("Sales grew due to subsidies [S5].", CHARTS[0], facts, {"S5": "subsidy"}) == []
    assert "citation S9 is not in the supplied evidence" in CE.check_explanation("Up [S9].", CHARTS[0], facts, {"S5": "x"})


def test_re_verified_sources_are_stamped_and_reused_by_the_next_edit(monkeypatch):
    fetched = []

    async def enrich(item, *a, **k):
        fetched.append(item.get("id"))
        return {**item, "page_fetch_status": "ok"}

    monkeypatch.setattr(R, "_enrich_report_source", enrich)
    candidate = REPORT.replace("PM E-DRIVE pays Rs 5,000 per vehicle [S5].",
                               "PM E-DRIVE pays Rs 5,000 per vehicle [S5], per the scheme notice [S5].")
    plain = [{k: v for k, v in s.items() if k != "fetchedAt"} for s in SOURCES]   # saved without a timestamp
    from fastapi.responses import JSONResponse
    first = asyncio.run(R._finalize_action_response(
        JSONResponse({"report": candidate, "charts": CHARTS, "sources": plain}), _body("x", sources=plain)))
    sources = json.loads(first.body)["sources"]
    stamped = {s["id"]: s.get("evidenceVerifiedAt") for s in sources}
    assert fetched == ["S5"] and stamped["S5"] and not stamped["S1"]
    assert R._action_saved_evidence_fresh(next(s for s in sources if s["id"] == "S5"))
    assert not R._action_saved_evidence_fresh({"evidenceExcerpt": "x", "evidenceVerifiedAt": "2001-01-01T00:00:00+00:00"})
    fetched.clear()
    again = candidate.replace("per the scheme notice [S5].", "per the scheme notice [S5]. It runs to 2026 [S5].")
    asyncio.run(R._audit_action_candidate(original_report=candidate, candidate_report=again, title="t",
                                          original_charts=CHARTS, candidate_charts=CHARTS, original_images=[],
                                          candidate_images=[], original_sources=sources, candidate_sources=sources,
                                          report_tier="detailed"))
    assert fetched == ["S5"]   # changed paragraph again; untouched sources keep their saved evidence
