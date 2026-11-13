"""Stage C1: entity tiers, outline validation/repair, per-entity search (no network)."""
import asyncio
import json

from utils.entity_research import plan_entity_queries, run_entity_search
from utils.entity_tiers import EntityEvidence, canonical, entity_tiers
from utils.evidence_store import build_evidence_store
from utils.llm_gateway import GatewayStats, LLMResult
from utils.report_outline import Section, build_outline, repair_outline, tiers_block, validate_outline


def _store():
    table = ("TABLE 1 — Sector returns\nSector | Q3 FY26 | Q4 FY26\nNifty Bank | +3.4% | +1.1%\nNifty IT | -4.1% | +2.2%\n"
             "Nifty Media | +7.1% | +7.0%\nNifty 50 | +1.0% | +0.5%\nMomentum | 1.2 | 0.8\n")
    rows = []
    for i in range(1, 5):
        rows.append({"url": f"https://www.nseindia.com/t{i}", "title": f"Sector table {i}", "pageFetchStatus": "ok",
                     "pageContent": table.replace("+3.4%", f"+3.{i}%"), "authorityTier": "primary_official", "reliabilityScore": 0.9})
    bank_text = "Banks rallied on credit growth. Bank stocks outperformed as Nifty Bank rose and banks re-rated. " * 5
    for i in range(5, 9):
        rows.append({"url": f"https://economictimes.indiatimes.com/b{i}", "title": f"Bank story {i}", "pageFetchStatus": "ok",
                     "pageContent": bank_text, "authorityTier": "established_media", "reliabilityScore": 0.8})
    it_text = "Information technology lagged; it was a weak quarter for the IT sector, and IT services margins fell. " * 5
    rows.append({"url": "https://blog.example/it", "title": "IT view", "pageFetchStatus": "ok", "pageContent": it_text,
                 "authorityTier": "unknown", "reliabilityScore": 0.6})
    return build_evidence_store(rows)


def test_canonical_names_merge_aliases_and_drop_benchmarks_and_metrics():
    assert canonical("Nifty IT") == canonical("Information Technology") == "IT"
    assert canonical("NIFTY BANK") == canonical("Banks") == "Bank"
    assert canonical("Nifty 50") is None and canonical("Momentum") is None and canonical("Total") is None
    assert canonical("Nifty India Defence") == canonical("Defence") == "Defence"


def test_tiers_follow_evidence_thresholds():
    tiers = entity_tiers(_store())
    deep = {e.name for e in tiers["deep_dive"]}
    compact = {e.name for e in tiers["compact"]}
    assert deep == {"Bank"}                  # 4 good sources of substantive text
    assert {"IT", "Media"} <= compact         # tables (+1 text source for IT)
    assert "Momentum" not in deep | compact   # metric row label, not an entity


def test_validator_rejects_small_outline_and_repair_adds_missing_entities():
    tiers = entity_tiers(_store())
    sections = [Section(f"sec-{i}", f"Cross-cutting {i}") for i in range(1, 6)]
    errors = validate_outline(sections, "in_depth", tiers)
    assert any("at least 12" in e for e in errors)
    assert any("deep-dive entity 'Bank'" in e for e in errors)
    assert any("compact entities not covered" in e for e in errors)
    decisions = []
    repaired = repair_outline(sections, "in_depth", tiers, decisions)
    assert any(s.role == "deep_dive" and s.entity == "Bank" and len(s.subsections) == 4 for s in repaired)
    assert any(s.role == "compact" for s in repaired)
    assert not any("deep-dive" in e or "compact" in e for e in validate_outline(repaired, "in_depth", tiers))
    assert len(decisions) == 2


def test_outline_ignores_unrequested_places_but_keeps_explicitly_named_publishers_and_peers():
    tiers = {"deep_dive": [], "compact": [
        EntityEvidence(name="Canada", role="out_of_scope", tier="compact", table_passages={"S1"}),
        EntityEvidence(name="United States", aliases={"US"}, role="out_of_scope", tier="compact",
                       table_passages={"S1"}),
        EntityEvidence(name="World Health Organization", aliases={"WHO"}, role="body", tier="compact",
                       table_passages={"S2"}),
    ], "coverage_note": []}
    question = "Compare India and the US; use WHO indicators, with other countries only as context."
    block = tiers_block(tiers, question)
    assert "Canada" not in block
    assert "United States" in block and "World Health Organization" in block

    sections = [Section(f"sec-{i}", f"Cross-cutting {i}") for i in range(1, 12)]
    sections.append(Section("sec-12", "Named comparison", role="compact",
                            entities=["United States", "World Health Organization"]))
    assert validate_outline(sections, "in_depth", tiers, question=question) == []

    repaired = repair_outline([Section(f"sec-{i}", f"Cross-cutting {i}") for i in range(1, 13)],
                              "in_depth", tiers, [], question=question)
    compact = [s for s in repaired if s.role == "compact"]
    assert len(compact) == 1
    assert set(compact[0].entities) == {"United States", "World Health Organization"}


class _Gw:
    def __init__(self, outlines):
        self.outlines = list(outlines)
        self.prompts = []
        self.stats = GatewayStats()

    async def generate(self, *, purpose, prompt, **kwargs):
        self.prompts.append(prompt)
        return LLMResult(ok=True, text=json.dumps(self.outlines.pop(0)), model="fake")


def test_outline_is_retried_once_with_the_rejection_reasons():
    tiers = entity_tiers(_store())
    small = {"title": "t", "thesis": "x", "sections": [{"title": f"S{i}", "queries": ["bank"]} for i in range(5)]}
    good = {"title": "t", "thesis": "x", "sections":
            [{"title": "Bank: deep-dive", "role": "deep_dive", "entity": "Bank", "queries": ["bank"],
              "subsections": [{"title": "Performance", "queries": ["bank returns"]}]},
             {"title": "Other sectors", "role": "compact", "entities": ["IT", "Media"], "queries": ["IT Media"]}]
            + [{"title": f"Cross {i}", "queries": ["sector returns"]} for i in range(10)]}
    gw = _Gw([small, good])
    outline = asyncio.run(build_outline(_store(), gw, question="q", tier="in_depth", today="2026-11-07", tiers=tiers))
    assert len(gw.prompts) == 2 and "PREVIOUS OUTLINE WAS REJECTED" in gw.prompts[1]
    assert outline.meta["outlineAttempts"][0]["errors"] and outline.meta["outlineAttempts"][1]["errors"] == []
    assert outline.meta["tierCounts"]["deep_dive"] == 1


def test_entity_queries_are_capped_and_compact_entities_come_first():
    tiers = entity_tiers(_store())
    plan = plan_entity_queries(tiers, cap=4, window="last 4 quarters")
    assert len(plan) == 4
    assert plan[0][0] in {e.name for e in tiers["compact"]}
    assert "last 4 quarters" in plan[0][1]
    assert plan_entity_queries(tiers, cap=0) == []


def test_entity_search_runs_the_stage_a_path_and_reports_per_entity():
    plan = [("IT", "IT sector earnings"), ("IT", "IT valuation"), ("Media", "Media flows")]
    calls = {}

    async def search(queries):
        calls["queries"] = queries
        return [{"url": "https://a.example/1", "searchQuery": "IT sector earnings"},
                {"url": "https://a.example/known", "searchQuery": "IT valuation"},
                {"url": "https://a.example/2", "searchQuery": "Media flows"}]

    def filt(rows, require_context=False):
        return rows

    async def enrich(rows):
        return [dict(r, pageFetchStatus="ok", pageContent="text") for r in rows]

    def quality(rows):
        return rows[:1], {"kept": 1}

    kept, stats = asyncio.run(run_entity_search(plan, search_fn=search, filter_fn=filt, enrich_fn=enrich, quality_fn=quality,
                                                existing_urls={"https://a.example/known"}))
    assert calls["queries"] == ["IT sector earnings", "IT valuation", "Media flows"]
    assert stats["queries"] == 3 and stats["results"] == 3 and stats["new_urls"] == 2 and stats["kept"] == 1
    assert stats["perEntity"]["IT"] == {"queries": 2, "results": 1, "kept": 1}
    assert kept[0]["entitySearch"] == "IT"


def test_period_labels_line_items_and_quarter_labels_are_not_entities():
    for label in ("5 Years", "1 Year", "YTD", "Q2:25-26", "Q4 FY26", "Better", "Turnover", "Inventories", "Cost Of Inputs"):
        assert canonical(label) is None, label
    assert canonical("Media, Entertainment & Publication") == "Media"
