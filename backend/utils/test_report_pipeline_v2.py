"""REPORT_PIPELINE_V2: charts, section post-processing, outline sizing and the end-to-end assembly (fake LLM)."""
import asyncio
import json
import re

import pytest

from utils.evidence_store import build_evidence_store
from utils.llm_gateway import GatewayStats, LLMResult
from utils.report_outline import Section, resolve_thin_evidence, size_budgets
from utils.report_pipeline_v2 import PipelineV2Error, run_pipeline_v2
from utils.section_charts import build_chart_candidates, charts_from_passage, verify_chart
from utils.section_writer import SectionDraft, postprocess, unsupported_numbers

QUARTERS = ["Q3 FY26", "Q4 FY26", "Q1 FY27", "Q2 FY27"]
SECTORS = ["NIFTY IT", "NIFTY BANK", "NIFTY PHARMA", "NIFTY AUTO", "NIFTY FMCG", "NIFTY METAL", "NIFTY REALTY",
           "NIFTY ENERGY", "NIFTY MEDIA", "NIFTY PSU BANK"]


def _matrix(rows=SECTORS):
    lines = ["COMPLETED CALENDAR-QUARTER NSE SECTOR INDEX RETURNS (calculated)", "Sector | " + " | ".join(QUARTERS)]
    for i, name in enumerate(rows):
        lines.append(name + " | " + " | ".join(f"{(i + 1) * (1 if q % 2 else -1) * 1.1 + q:+.2f}%" for q in range(4)))
    return "\n".join(lines)


def _sources():
    narrative = ("Foreign portfolio investors sold financial services shares worth 11,704 crore in September while "
                 "domestic institutions bought heavily through SIP flows. ") * 6
    fpi_table = ("TABLE 1 — FPI net investment by sector (Rs crore)\nSector | Net investment\n"
                 "Financial Services | -11,704\nInformation Technology | -6,210\nCapital Goods | 3,480\nHealthcare | 1,950\n")
    rows = [{"url": "internal://verified-sector-quarterly-returns", "title": "CALCULATED NSE SECTOR RETURNS", "fullContent": _matrix(),
             "authorityTier": "primary_official", "reliabilityScore": 1.0}]
    for i in range(2, 13):
        rows.append({"url": f"https://www.nseindia.com/report-{i}", "title": f"NSE sector flows report {i}", "publisher": "NSE",
                     "publication_date": "2026-10-01", "pageFetchStatus": "ok",
                     "pageContent": (fpi_table if i == 2 else "") + narrative + f" Banks rallied {i}.5% in the quarter.",
                     "authorityTier": "primary_official", "reliabilityScore": 0.9})
    return rows


def _store():
    rows = _sources()
    manifest = [{"id": f"S{i}", "url": r["url"] if not r["url"].startswith("internal") else "", "title": r["title"],
                 "authority_tier": r["authorityTier"], "reliability_score": r["reliabilityScore"], "publication_date": r.get("publication_date", ""),
                 "publisher": r.get("publisher", "Calculated")} for i, r in enumerate(rows, 1)]
    return build_evidence_store(rows, manifest=manifest), manifest


# ── charts ────────────────────────────────────────────────────────────────────
def test_charts_are_built_from_stored_tables_with_exact_cell_evidence():
    store, _ = _store()
    charts, dropped = build_chart_candidates(store)
    types = {c.spec["type"] for c in charts}
    assert "heatmap" in types and "bar" in types  # 10 sectors x 4 quarters -> heatmap; FPI table -> bar
    bar = next(c for c in charts if c.spec["type"] == "bar")
    point = bar.spec["series"][0]["data"][0]
    assert point["evidenceValue"] == "-11,704" and point["value"] == -11704.0 and "Financial Services" in point["evidenceQuote"]
    heat = next(c for c in charts if c.spec["type"] == "heatmap")
    assert heat.spec["columns"] == QUARTERS and len(heat.spec["rows"]) == 10 and heat.spec["unit"] == "%"


def test_unverifiable_chart_is_dropped_before_writing():
    store, _ = _store()
    chart = next(c for c in build_chart_candidates(store)[0] if c.spec["type"] == "bar")
    assert verify_chart(chart, "a different passage without those cells") .startswith("value")
    chart.points = chart.points[:2]
    assert verify_chart(chart, "") == "fewer than 3 data points"


# ── section post-processing ───────────────────────────────────────────────────
class _Fig:
    def __init__(self, n):
        self.figure_number, self.status, self.claim, self.drop_reason, self.passage_ids, self.points = n, "planned", "", "", [], []


def test_postprocess_validates_citations_figures_and_numbers():
    raw = ("### Flows\nFPIs sold 11,704 crore of financials [S2.1]. A claim with a bad id [S99.1] and mixed [S2.1, S7.4].\n\n"
           "Figure 1 shows the sector spread, with banks up 42.42% [S2.1].\n\n<<<SUMMARY>>>\nFPIs sold 11,704 crore [S2.1].")
    figs = [_Fig(1), _Fig(2)]
    draft = postprocess(SectionDraft("sec-1", "Flows"), raw, allowed_passages={"S2.1"}, allowed_numbers={"11704"}, figures=figs, min_words=10)
    assert draft.ok and draft.invalid_citations == 2
    assert "[S99.1]" not in draft.body and "[S2.1]" in draft.body
    assert "[[FIGURE 1]]" in draft.body and figs[0].status == "referenced" and "Figure 1 shows" in figs[0].claim
    assert figs[1].status == "dropped" and 2 in draft.figures_dropped and "[[FIGURE 2]]" not in draft.body
    assert draft.unsupported_numbers == ["42.42"]
    assert draft.cited_passages == ["S2.1"] and draft.summary.startswith("FPIs sold")


def test_unsupported_numbers_ignore_years_quarters_citations_and_figure_numbers():
    body = "In Q2 FY27 and 2026, Figure 3 shows 4.5% growth [S1.2] versus 7.1%."
    assert unsupported_numbers(body, {"4.5"}) == ["7.1"]


# ── outline sizing ────────────────────────────────────────────────────────────
def test_thin_sections_are_merged_or_dropped_and_budgets_come_from_evidence():
    store, _ = _store()
    ids = [p.passage_id for p in store.passages]
    strong = Section("sec-1", "Sector returns by quarter", queries=["sector returns quarter"], passage_ids=ids[:6])
    prose_only = [p.passage_id for p in store.passages if p.kind == "narrative"][:1]
    thin_related = Section("sec-2", "Sector returns outliers", queries=["sector returns outliers"], passage_ids=prose_only)
    thin_unrelated = Section("sec-3", "Weather patterns", queries=["monsoon rainfall"], passage_ids=[])
    decisions = []
    resolve_thin_evidence(store, [strong, thin_related, thin_unrelated], decisions)
    assert thin_related.status == "merged" and thin_unrelated.status == "dropped"
    assert any("Merged section 'Sector returns outliers'" in d for d in decisions)
    assert any("Dropped section 'Weather patterns'" in d for d in decisions)
    meta = {}
    size_budgets(store, [strong, thin_related, thin_unrelated], "detailed", meta)
    assert 380 <= strong.word_budget <= 1150 and meta["plannedAnalysisWords"] == strong.word_budget


# ── end to end with a fake LLM ────────────────────────────────────────────────
class FakeGateway:
    def __init__(self, outline):
        self.outline = outline
        self.stats = GatewayStats()

    async def generate(self, *, purpose, prompt, system="", section_id="", **kwargs):
        self.stats.calls += 1
        self.stats.by_purpose[purpose] += 1
        self.stats.models["fake"] += 1
        if purpose == "outline":
            return LLMResult(ok=True, text=json.dumps(self.outline), model="fake")
        if purpose == "editorial_transitions":
            sids = re.findall(r"(?m)^(sec-\d+) \|", prompt)
            return LLMResult(ok=True, text=json.dumps({sid: "The next section turns to the evidence behind this shift." for sid in sids[1:]}), model="fake")
        if purpose == "executive_summary":
            cites = re.findall(r"\[(S\d+\.\d+)\]", prompt)
            return LLMResult(ok=True, text=f"The rotation favoured banks over IT, and FPIs sold 11,704 crore of financials [{cites[0]}].", model="fake")
        ids = re.findall(r"(?m)^\[(S\d+\.\d+)\]", prompt)
        figs = [int(n) for n in re.findall(r"(?m)^Figure (\d+):", prompt)]
        body = f"### Evidence\nFPIs sold 11,704 crore of financial services shares in September [{ids[0]}]. " + (
            "Domestic institutions absorbed the selling through steady SIP inflows, which kept index drawdowns shallow "
            f"and shifted leadership towards domestically owned sectors [{ids[-1]}]. ") * 4
        for n in figs:
            # Interpret the figure from its own data (series/row and column labels in its brief).
            brief = prompt.split(f"Figure {n}:", 1)[1]
            m = re.search(r"\n  - ([^:\n]+): ([^=,\n]+)=", brief)
            names = f", including {m.group(1).strip()} and {m.group(2).strip()}" if m else ""
            body += f"\n\nFigure {n} shows how the sectors compare across the window{names} [{ids[0]}].\n\n[[FIGURE {n}]]"
        return LLMResult(ok=True, text=body + f"\n<<<SUMMARY>>>\nFPIs sold 11,704 crore [{ids[0]}].", model="fake")


OUTLINE = {
    "title": "Indian Sector Rotation Over Four Quarters",
    "thesis": "Leadership rotated from IT to financials as foreign selling met domestic buying.",
    "entities": [{"name": "NIFTY BANK", "aliases": ["banks"]}, {"name": "NIFTY OIL", "aliases": []}],
    "dimensions": ["quarterly returns", "FPI flows"],
    "sections": [
        {"title": "Sector returns by quarter", "goal": "Show the return matrix", "queries": ["sector index returns quarter NIFTY"],
         "subsections": [{"title": "Leaders and laggards", "goal": "rank", "queries": ["NIFTY returns Q2 FY27"]}]},
        {"title": "Foreign and domestic flows", "goal": "Explain flows", "queries": ["FPI net investment sector crore", "domestic institutions SIP"]},
        {"title": "Banks versus IT", "goal": "Compare", "queries": ["banks rallied quarter", "information technology"]},
        {"title": "Weather patterns", "goal": "irrelevant", "queries": ["monsoon rainfall forecast"]},
    ],
}


@pytest.mark.parametrize("tier", ["detailed", "in_depth"])
def test_end_to_end_assembly_with_fake_llm(tier):
    store, manifest = _store()
    gateway = FakeGateway(OUTLINE)
    result = asyncio.run(run_pipeline_v2(runtime=None, question="Sector rotation in India over the last 4 quarters", tier=tier,
                                         store=store, manifest=manifest, today="2026-11-07", gateway=gateway))
    out = json.loads(result["raw"])
    assert set(out) >= {"title", "report", "charts", "keyStats", "summary", "images"}
    report = out["report"]
    assert report.startswith("## Executive Summary")
    assert "## Methodology and Evidence Base" in report and "Weather patterns" not in report.split("## Methodology")[0]
    assert not re.search(r"\[S\d+\.\d+\]", report)  # passage ids converted to source-level for the renderer
    assert re.search(r"\[S\d+\]", report)
    assert out["charts"] and all(c["title"].startswith(f"Figure {i}:") for i, c in enumerate(out["charts"], 1))
    for i in range(1, len(out["charts"]) + 1):
        assert f"[CHART_{i}]" in report and f"Figure {i} shows" in report
    meta = result["meta"]
    assert meta["llm"]["calls"] == gateway.stats.calls
    assert any("Weather patterns" in d for d in meta["outline"]["decisions"])
    coverage = {r["entity"]: r for r in meta["outline"]["entityCoverage"]}
    assert coverage["NIFTY BANK"]["status"] == "covered" and coverage["NIFTY OIL"]["status"] == "insufficient_evidence"
    if tier == "in_depth":
        # Sources are listed once, by the renderer (Appendix B — Data Sources); the pipeline adds only the data tables,
        # and (Stage N) never a table already drawn as a figure: here every data table is charted, so no Appendix A.
        assert "Source Reliability" not in report
        charted = {pid for c in out["charts"] for pid in c.get("evidencePassages") or []}
        appendix = report.split("## Appendix A — Data Tables", 1)[1] if "## Appendix A — Data Tables" in report else ""
        assert charted and not any(f"[{pid}]" in appendix for pid in charted)
    else:
        assert "Appendix A" not in report


def test_invalid_outline_raises_so_report_py_falls_back():
    store, manifest = _store()

    class Broken(FakeGateway):
        async def generate(self, **kwargs):
            return LLMResult(ok=False, error="http_503")

    with pytest.raises(PipelineV2Error):
        asyncio.run(run_pipeline_v2(runtime=None, question="q", tier="detailed", store=store, manifest=manifest,
                                    today="2026-11-07", gateway=Broken(OUTLINE)))


def test_offline_replay_reproduces_the_run_from_captured_calls():
    from utils.evidence_store import store_from_passages
    from utils.llm_replay import ReplayGateway

    store, manifest = _store()
    calls = []

    class Recording(FakeGateway):
        async def generate(self, *, purpose, prompt, system="", section_id="", **kwargs):
            result = await super().generate(purpose=purpose, prompt=prompt, system=system, section_id=section_id)
            calls.append({"purpose": purpose, "sectionId": section_id, "ok": result.ok, "model": "fake",
                          "llmPrompt": prompt, "llmResponse": result.text})
            return result

    live = asyncio.run(run_pipeline_v2(runtime=None, question="Sector rotation", tier="in_depth", store=store,
                                       manifest=manifest, today="2026-11-07", gateway=Recording(OUTLINE)))
    capture = {"llmCalls": calls}
    rebuilt = store_from_passages({p.passage_id: p.to_dict() for p in store.passages})
    replay_gateway = ReplayGateway(capture)
    replayed = asyncio.run(run_pipeline_v2(runtime=None, question="Sector rotation", tier="in_depth", store=rebuilt,
                                           manifest=manifest, today="2026-11-07", gateway=replay_gateway))
    assert replayed["raw"] == live["raw"]
    assert replay_gateway.missing == [] and replay_gateway.prompt_changed == []
    assert replay_gateway.recorded_calls == len(calls)


def test_chart_unit_comes_from_the_plotted_column_and_old_periods_are_dropped():
    from utils.evidence_store import Passage
    from utils.section_charts import build_chart_candidates as build
    table = ("TABLE 1 — Sector Performance Scorecard\nSector | Index Level | 1 Week\n"
             "NIFTY IT | 35,210.5 | +1.2%\nNIFTY BANK | 58,900.0 | -0.4%\nNIFTY PHARMA | 22,410.9 | +2.1%\n")
    old = ("TABLE 2 — Sectoral Indices performance in April, 2024\nIndex | 1-month Returns (%)\n"
           "NIFTY IT | 3.1\nNIFTY BANK | -1.2\nNIFTY AUTO | 4.4\n")

    class S:
        passages = [Passage("S1.1", "S1", table, "table", reliability=0.8),
                    Passage("S2.1", "S2", old, "table", reliability=0.8),
                    # Different data (identical data would be deduplicated before the reliability check).
                    Passage("S3.1", "S3", table.replace("+1.2%", "+1.5%"), "table", reliability=0.3)]

    charts, dropped = build(S(), min_year=2025)
    assert len(charts) == 1
    # Index levels of different sectors are not comparable: the 1-week return column is plotted, with its own unit.
    assert charts[0].spec["unit"] == "%" and "1 Week" in charts[0].spec["title"] and "Index Level" not in charts[0].spec["title"]
    reasons = " | ".join(d["reason"] for d in dropped)
    assert "outside the requested window" in reasons and "reliability 0.30 below 0.50" in reasons


def test_short_section_is_extended_only_from_its_unused_passages():
    from utils.report_outline import Section
    from utils.section_writer import write_section

    store, _ = _store()
    ids = [p.passage_id for p in store.passages if p.kind == "narrative"][:6]
    section = Section("sec-9", "Flows", goal="flows", queries=["flows"], passage_ids=ids, word_budget=900)
    seen = {}

    class Gw(FakeGateway):
        async def generate(self, *, purpose, prompt, system="", section_id="", **kwargs):
            seen[purpose] = prompt
            if purpose == "section":  # cites only the first passage, ~80 words
                return LLMResult(ok=True, text=f"### A\n" + f"Foreign investors sold financials heavily [{ids[0]}]. " * 16
                                 + f"\n<<<SUMMARY>>>\nSold [{ids[0]}].", model="fake")
            return LLMResult(ok=True, text="Domestic SIP inflows offset the selling across the quarter "
                             f"[{ids[1]}]. " * 8 + f"\n<<<SUMMARY>>>\nSIP offset [{ids[1]}].", model="fake")

    draft = asyncio.run(write_section(Gw(OUTLINE), store, section, [], question="q", thesis="t",
                                      already_covered=[], other_sections=[]))
    assert "section_extend" in seen
    extend_prompt = seen["section_extend"]
    unused_block = extend_prompt.split("UNUSED EVIDENCE PASSAGES (cite by id):")[1]
    assert f"[{ids[0]}]" not in unused_block and f"[{ids[1]}]" in unused_block
    assert draft.calls == 2 and ids[1] in draft.cited_passages and "Domestic SIP inflows" in draft.body

    full = Section("sec-10", "Flows", goal="flows", queries=["flows"], passage_ids=ids, word_budget=60)
    seen.clear()
    asyncio.run(write_section(Gw(OUTLINE), store, full, [], question="q", thesis="t", already_covered=[], other_sections=[]))
    assert "section_extend" not in seen  # met its budget: no extension call


def test_chart_claim_has_no_marker_and_uses_the_final_figure_number():
    from utils.report_pipeline_v2 import _renumber_figures
    figs = [_Fig(3)]
    raw = "### Leaders\n[[FIGURE 3]]\nFigure 3 shows telecom leading at 14.08% [S2.1].\n<<<SUMMARY>>>\nok"
    postprocess(SectionDraft("s", "t"), raw, allowed_passages={"S2.1"}, allowed_numbers={"14.08"}, figures=figs, min_words=5)
    assert figs[0].claim == "Figure 3 shows telecom leading at 14.08%."
    assert _renumber_figures(figs[0].claim, {3: 1}).startswith("Figure 1 shows")


def test_citation_repair_points_numbers_at_the_passage_that_holds_them():
    from utils.section_writer import repair_citations
    numbers = {"S6.1": {"12.5"}, "S14.2": {"10915", "883"}}
    body = ("IT absorbed 10,915 crore of FPI inflows [S6.1]. Pharma rose 12.5% [S6.1]. "
            "A figure found nowhere: 77.7% [S6.1].")
    fixed, repaired = repair_citations(body, numbers)
    assert repaired == 1
    assert "10,915 crore of FPI inflows [S6.1, S14.2]." in fixed
    assert "Pharma rose 12.5% [S6.1]." in fixed and "77.7% [S6.1]." in fixed  # unchanged


def test_finalize_figures_renumbers_survivors_and_neutralises_removed_refs():
    from utils.report_pipeline_v2 import finalize_figures
    text = ("Figure 3 shows the ranking [S1].\n\n[CHART_1]\n\nFigure 6 shows flows (removed later) [S2].\n\n"
            "Figure 9 shows OI build-up [S3].\n\n[CHART_2]")
    charts = [{"title": "Figure 3: Ranking", "takeaway": "Figure 3 shows the ranking."},
              {"title": "Figure 9: OI build-up", "takeaway": "Figure 9 shows OI build-up."}]
    out, charts_out, stats = finalize_figures(text, charts)
    assert [c["title"] for c in charts_out] == ["Figure 1: Ranking", "Figure 2: OI build-up"]
    assert "Figure 1 shows the ranking" in out and "Figure 2 shows OI build-up" in out
    assert "Figure 6" not in out and "underlying data" not in out
    assert "Flows (removed later) [S2]." in out  # claim kept, figure framing removed
    assert charts_out[1]["takeaway"] == "Figure 2 shows OI build-up."
    assert stats == {"figures": 2, "danglingRefsNeutralized": 1, "mapping": {"3": 1, "9": 2}, "reordered": False}


def test_removed_figure_references_lose_their_framing_without_filler():
    from utils.report_pipeline_v2 import rewrite_removed_figure_refs
    text = ("Figure 6 shows that FPIs sold 883 crore [S2]. As Figure 6 shows, banks led [S3]. "
            "Banks led again (see Figure 6). Figure 2 shows IT [S4].")
    out = rewrite_removed_figure_refs(text, {6})
    assert out == "FPIs sold 883 crore [S2]. Banks led [S3]. Banks led again. Figure 2 shows IT [S4]."
    assert "underlying data" not in out


def test_code_fences_become_tables_lists_or_text():
    from utils.section_writer import unfence
    assert "| A | B |\n| --- | --- |\n| 1 | 2 |" in unfence("```\nA | B\n1 | 2\n3 | 4\n```")
    listed = unfence("Intro.\n```text\nMARKET HEALTH INDEX\nMicro - GDP\n```\nAfter.")
    assert "```" not in listed and "- MARKET HEALTH INDEX" in listed and "- Micro - GDP" in listed
    assert unfence("```\nsingle line\n```").strip() == "single line"
    assert "```" not in unfence("text\n```\nunclosed")


def test_charts_failing_on_the_assembled_text_are_dropped_and_references_rewritten():
    store, manifest = _store()
    calls = []

    def verifier(specs, report_text=""):
        calls.append(bool(report_text))
        # Before writing everything passes; on the assembled text the first chart fails.
        return [("table marks it unverified" if report_text and i == 0 else "") for i in range(len(specs))]

    result = asyncio.run(run_pipeline_v2(runtime=None, question="Sector rotation", tier="in_depth", store=store,
                                         manifest=manifest, today="2026-11-07", gateway=FakeGateway(OUTLINE),
                                         chart_verifier=verifier))
    out = json.loads(result["raw"])
    meta = result["meta"]["charts"]
    assert calls[0] is False and calls[-1] is True  # pre-writing and post-assembly
    assert len(meta["droppedAfterAssembly"]) == 1 and meta["droppedAfterAssembly"][0]["reason"] == "table marks it unverified"
    report = out["report"]
    assert all(c["title"].startswith(f"Figure {i}:") for i, c in enumerate(out["charts"], 1))
    for i in range(1, len(out["charts"]) + 1):
        assert f"[CHART_{i}]" in report
    assert f"[CHART_{len(out['charts']) + 1}]" not in report
    assert "underlying data" not in report


def test_sections_lost_to_a_network_outage_are_rewritten_when_it_ends(monkeypatch):
    """Stage N: a DNS outage mid-report used to fail every section and drop to the fallback writer."""
    from utils import net_recovery

    class OutageGateway(FakeGateway):
        down = True

        async def generate(self, *, purpose, **kwargs):
            if self.down and purpose == "section":
                self.stats.calls += 1
                return LLMResult(ok=False, text="", model="fake", error="network:ConnectError model=fake")
            return await super().generate(purpose=purpose, **kwargs)

    gateway = OutageGateway(OUTLINE)
    waits = []

    async def fake_wait(max_seconds, **_):
        waits.append(max_seconds)
        gateway.down = False  # the network comes back while we wait
        return 30.0, True

    monkeypatch.setenv("GG_NETWORK_WAIT_SECONDS", "300")
    monkeypatch.setattr(net_recovery, "wait_for_network", fake_wait)
    store, manifest = _store()
    result = asyncio.run(run_pipeline_v2(runtime=None, question="Sector rotation in India over the last 4 quarters",
                                         tier="detailed", store=store, manifest=manifest, today="2026-11-07", gateway=gateway))
    assert waits and json.loads(result["raw"])["report"].startswith("## Executive Summary")
    assert result["meta"]["deadline"]["networkWaits"][0]["networkBack"] is True
