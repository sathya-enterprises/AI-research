"""Outline stage for the section-by-section pipeline (REPORT_PIPELINE_V2).

1. One JSON LLM call proposes a title, a thesis, the topic's natural entities
   (e.g. sector indices, companies) and dimensions (quarters, metrics), and
   analysis sections with goals and search queries. In-depth asks for an
   entity-first structure with subsections.
2. Per-entity retrieval: every entity gets its own search over the evidence
   store; coverage (passages, sources) is reported.
3. Evidence assignment: each leaf section (subsection, or a section without
   subsections) retrieves its own passages; a passage serves at most two
   leaves, preferring the one it matches best, so sections do not share one pile.
4. Thin evidence is merged or dropped, never padded: a thin subsection merges
   into its parent; a thin section merges into its closest neighbour or is
   dropped. Every decision is logged with its reason.
5. Word budgets are sized from the evidence each section actually has, then
   capped per tier. Detailed aims at ~25–30 pages; in-depth scales with the
   evidence (100+ pages only when the evidence supports it).

Executive summary, methodology and appendices are added deterministically;
the LLM outline only covers analysis sections.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

log = logging.getLogger("report_outline")

WORDS_PER_PAGE = 380
TIER_RULES = {
    "detailed": {"sections": "6-8", "subsections": False, "leaf_cap": 1150, "leaf_floor": 380, "total_cap": 8200,
                 "k_leaf": 12, "exec_words": 450},
    "in_depth": {"sections": "12-20", "subsections": True, "leaf_cap": 950, "leaf_floor": 330, "total_cap": 34000,
                 "k_leaf": 14, "exec_words": 900},
}
MIN_LEAF_PASSAGES = 3
MIN_LEAF_SOURCES = 2
MIN_ENTITY_PASSAGES = 2


@dataclass
class Section:
    section_id: str
    title: str
    goal: str = ""
    queries: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    subsections: list["Section"] = field(default_factory=list)
    kind: str = "analysis"   # analysis | executive_summary | methodology | appendix_sources | appendix_data
    passage_ids: list[str] = field(default_factory=list)
    word_budget: int = 0
    status: str = "keep"
    note: str = ""

    def leaves(self) -> list["Section"]:
        return [s for s in self.subsections if s.status == "keep"] or [self]

    def to_dict(self) -> dict:
        return {"id": self.section_id, "title": self.title, "goal": self.goal, "queries": self.queries,
                "entities": self.entities, "kind": self.kind, "passages": len(self.passage_ids),
                "wordBudget": self.word_budget, "status": self.status, "note": self.note,
                "subsections": [s.to_dict() for s in self.subsections]}


@dataclass
class Outline:
    title: str
    thesis: str
    sections: list[Section]
    entities: list[dict] = field(default_factory=list)
    dimensions: list[str] = field(default_factory=list)
    entity_coverage: list[dict] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    def analysis_sections(self) -> list[Section]:
        return [s for s in self.sections if s.kind == "analysis" and s.status == "keep"]

    def to_dict(self) -> dict:
        return {"title": self.title, "thesis": self.thesis, "entities": self.entities, "dimensions": self.dimensions,
                "entityCoverage": self.entity_coverage, "decisions": self.decisions, "meta": self.meta,
                "sections": [s.to_dict() for s in self.sections]}


class OutlineError(RuntimeError):
    pass


# ── prompt ────────────────────────────────────────────────────────────────────
def evidence_overview(store, *, max_sources: int = 60, max_tables: int = 40) -> str:
    sources = sorted(store.source_summary(), key=lambda r: -float(r.get("reliability") or 0))[:max_sources]
    lines = ["SOURCES (id | publisher | authority | date | passages | title):"]
    for s in sources:
        lines.append(f"- {s['sourceId']} | {s.get('publisher') or '-'} | {s.get('authorityTier')} | {s.get('published') or 'undated'}"
                     f" | {s['passages']} | {str(s.get('title') or '')[:110]}")
    tables = [p for p in store.passages if p.kind in {"table", "calculated"}][:max_tables]
    if tables:
        lines.append("\nDATA TABLES AVAILABLE (passage id | heading | columns):")
        for p in tables:
            text_lines = [ln for ln in p.text.splitlines() if ln.strip()]
            header = next((ln for ln in text_lines if " | " in ln), "")
            lines.append(f"- {p.passage_id} | {text_lines[0][:90] if text_lines else ''} | {header[:140]}")
    return "\n".join(lines)


def outline_prompt(question: str, tier: str, overview: str, today: str, window_note: str) -> tuple[str, str]:
    rules = TIER_RULES.get(tier, TIER_RULES["detailed"])
    system = (
        "You are the research director planning an evidence-grounded investment research report. "
        "You plan structure only; you do not write the report. Use only what the listed sources and tables can support. "
        "Return valid JSON only."
    )
    if tier == "in_depth":
        structure = (
            "ENTITY-FIRST PLAN (Deep Research):\n"
            "1. First list every natural ENTITY this topic is made of and that the sources actually cover (for example each "
            "sector index, each company, each region). Use the names as they appear in the sources; give aliases.\n"
            "2. List the DIMENSIONS the entities are compared on (e.g. quarterly returns, FPI/DII flows, valuation, earnings, drivers).\n"
            f"3. Plan {rules['sections']} analysis sections: cross-cutting dimension sections (one per material dimension), "
            "entity deep-dive sections that GROUP related entities with one subsection per entity, then risks/scenarios and outlook. "
            "Every section has 2-5 subsections. Each subsection must be answerable from the sources listed."
        )
    else:
        structure = (
            f"Plan {rules['sections']} analysis sections that answer the question as one argument: context, the main evidence, "
            "the drivers, comparisons, risks and an outlook/implications section. Subsections are optional (use 0-3 per section)."
        )
    prompt = f"""QUESTION: {question}
TODAY: {today}
{window_note}
REPORT TIER: {tier}

{structure}

Do NOT include an executive summary, methodology, sources or appendix sections; those are added automatically.
Sections must not overlap: give each a distinct goal. The thesis is the report's single central claim, phrased so the
evidence can support or qualify it.
For every section and subsection give 2-4 search queries made of words likely to appear in the sources (index names,
metric names, periods), not generic phrases.

EVIDENCE AVAILABLE:
{overview}

Return JSON:
{{"title": "<noun-phrase title, max 12 words>",
  "thesis": "<1-2 sentences>",
  "entities": [{{"name": "...", "type": "...", "aliases": ["..."]}}],
  "dimensions": ["..."],
  "sections": [{{"title": "...", "goal": "<what this section must establish>", "queries": ["..."], "entities": ["..."],
                 "subsections": [{{"title": "...", "goal": "...", "queries": ["..."], "entities": ["..."]}}]}}]}}"""
    return system, prompt


# ── evidence sizing ───────────────────────────────────────────────────────────
def _terms(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]{3,}", str(text or "").casefold())}


def _clean_list(values, limit: int) -> list[str]:
    out = []
    for value in values or []:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        if text and text not in out:
            out.append(text[:160])
    return out[:limit]


def _sections_from_json(data: dict, tier: str) -> list[Section]:
    sections = []
    for i, raw in enumerate(data.get("sections") or [], 1):
        if not isinstance(raw, dict) or not str(raw.get("title") or "").strip():
            continue
        sec = Section(section_id=f"sec-{i}", title=str(raw["title"]).strip()[:140], goal=str(raw.get("goal") or "")[:400],
                      queries=_clean_list(raw.get("queries"), 4) or [str(raw["title"])], entities=_clean_list(raw.get("entities"), 12))
        if TIER_RULES.get(tier, {}).get("subsections") or raw.get("subsections"):
            for j, sub in enumerate(raw.get("subsections") or [], 1):
                if isinstance(sub, dict) and str(sub.get("title") or "").strip():
                    sec.subsections.append(Section(
                        section_id=f"{sec.section_id}.{j}", title=str(sub["title"]).strip()[:140], goal=str(sub.get("goal") or "")[:400],
                        queries=_clean_list(sub.get("queries"), 4) or [str(sub["title"])], entities=_clean_list(sub.get("entities"), 8)))
        sections.append(sec)
    return sections


def _leaf_hits(store, leaf: Section, k: int) -> list[tuple[str, float]]:
    scores: dict[str, float] = {}
    for query in leaf.queries + ([" ".join(leaf.entities)] if leaf.entities else []) + [leaf.title]:
        for passage, score in store.search(query, k=k, max_per_source=3):
            scores[passage.passage_id] = max(scores.get(passage.passage_id, 0.0), score)
    if not scores:
        return []
    ranked = sorted(scores.items(), key=lambda x: -x[1])
    top = ranked[0][1]
    # Weak lexical matches are not evidence for this leaf, but a single
    # table passage with a huge score must not crowd out relevant prose.
    return [(pid, s) for rank, (pid, s) in enumerate(ranked) if rank < 6 or s >= 0.2 * top]


def assign_passages(store, sections: list[Section], k_leaf: int) -> None:
    candidates = []
    for sec in sections:
        for leaf in sec.leaves():
            for pid, score in _leaf_hits(store, leaf, k_leaf * 2):
                candidates.append((score, leaf, pid))
    uses: dict[str, int] = {}
    for score, leaf, pid in sorted(candidates, key=lambda x: -x[0]):
        if uses.get(pid, 0) >= 2 or len(leaf.passage_ids) >= k_leaf or pid in leaf.passage_ids:
            continue
        leaf.passage_ids.append(pid)
        uses[pid] = uses.get(pid, 0) + 1
    for sec in sections:
        if sec.subsections:
            sec.passage_ids = list(dict.fromkeys(pid for leaf in sec.leaves() for pid in leaf.passage_ids))


def _evidence_strength(store, passage_ids: list[str]) -> tuple[int, int]:
    passages = [store.get(pid) for pid in passage_ids]
    passages = [p for p in passages if p]
    # A data table (or calculated dataset) can carry a section on its own; a
    # search-index excerpt is weaker than a read publisher page.
    weight = sum(3 if p.kind in {"table", "calculated"} else 0.5 if p.kind == "search_excerpt" else 1 for p in passages)
    return weight, len({p.source_id for p in passages})


def _is_thin(store, passage_ids: list[str]) -> bool:
    """Thin = too little weight, or one source of prose only. A data table may stand alone."""
    weight, n_sources = _evidence_strength(store, passage_ids)
    has_data = any((p := store.get(pid)) and p.kind in {"table", "calculated"} for pid in passage_ids)
    return weight < MIN_LEAF_PASSAGES or (n_sources < MIN_LEAF_SOURCES and not has_data and weight < 4)


def resolve_thin_evidence(store, sections: list[Section], decisions: list[str]) -> None:
    for sec in sections:
        for sub in sec.subsections:
            weight, n_sources = _evidence_strength(store, sub.passage_ids)
            if _is_thin(store, sub.passage_ids):
                sub.status = "merged"
                sub.note = f"thin evidence ({len(sub.passage_ids)} passages, {n_sources} sources) - merged into parent section"
                decisions.append(f"Merged subsection '{sub.title}' into '{sec.title}': {sub.note.split(' - ')[0]}.")
                sec.queries = list(dict.fromkeys(sec.queries + sub.queries))[:6]
                sec.passage_ids = list(dict.fromkeys(sec.passage_ids + sub.passage_ids))
        if sec.subsections and not any(s.status == "keep" for s in sec.subsections):
            sec.subsections = []
    live = [s for s in sections if s.status == "keep"]
    for sec in list(live):
        weight, n_sources = _evidence_strength(store, sec.passage_ids)
        if not _is_thin(store, sec.passage_ids):
            continue
        others = [o for o in live if o is not sec and o.status == "keep"]
        best = max(others, key=lambda o: len(_terms(o.title + " " + " ".join(o.queries)) & _terms(sec.title + " " + " ".join(sec.queries))), default=None)
        overlap = len(_terms(best.title + " " + " ".join(best.queries)) & _terms(sec.title + " " + " ".join(sec.queries))) if best else 0
        reason = f"{len(sec.passage_ids)} passages from {n_sources} sources"
        if best and overlap >= 2 and sec.passage_ids:
            sec.status = "merged"
            sec.note = f"thin evidence ({reason}) - merged into '{best.title}'"
            best.queries = list(dict.fromkeys(best.queries + sec.queries))[:6]
            target = best.leaves()[-1]
            target.passage_ids = list(dict.fromkeys(target.passage_ids + sec.passage_ids))
            if best.subsections:
                best.passage_ids = list(dict.fromkeys(best.passage_ids + sec.passage_ids))
            decisions.append(f"Merged section '{sec.title}' into '{best.title}': thin evidence ({reason}).")
        else:
            sec.status = "dropped"
            sec.note = f"thin evidence ({reason}) and no related section"
            decisions.append(f"Dropped section '{sec.title}': thin evidence ({reason}) and no related section to merge into.")


def size_budgets(store, sections: list[Section], tier: str, meta: dict) -> None:
    rules = TIER_RULES.get(tier, TIER_RULES["detailed"])
    leaves = [leaf for sec in sections if sec.status == "keep" for leaf in sec.leaves()]
    for leaf in leaves:
        weight, _ = _evidence_strength(store, leaf.passage_ids)
        leaf.word_budget = max(rules["leaf_floor"], min(rules["leaf_cap"], 95 * weight))
    for sec in sections:
        if sec.status == "keep" and sec.subsections:
            sec.word_budget = sum(s.word_budget for s in sec.leaves()) + 120
    total = sum(s.word_budget for s in sections if s.status == "keep")
    evidence_words = total
    if total > rules["total_cap"]:
        factor = rules["total_cap"] / total
        for leaf in leaves:
            leaf.word_budget = max(rules["leaf_floor"], int(leaf.word_budget * factor))
        for sec in sections:
            if sec.status == "keep" and sec.subsections:
                sec.word_budget = sum(s.word_budget for s in sec.leaves()) + 120
        total = sum(s.word_budget for s in sections if s.status == "keep")
    meta.update({"evidenceSupportedWords": evidence_words, "plannedAnalysisWords": total, "tierWordCap": rules["total_cap"]})


def estimate_pages(analysis_words: int, exec_words: int, charts: int, appendix_sources: int, data_tables: int) -> float:
    return round((analysis_words + exec_words) / WORDS_PER_PAGE + charts * 0.45 + 1.5  # cover + contents
                 + appendix_sources / 14 + data_tables * 0.5, 1)


def entity_coverage(store, entities: list[dict], dimensions: list[str]) -> list[dict]:
    rows = []
    dim_terms = " ".join(dimensions[:4])
    for entity in entities:
        name = str(entity.get("name") or "").strip()
        if not name:
            continue
        query = " ".join([name, *[str(a) for a in (entity.get("aliases") or [])][:3]])
        hits = store.search(query, k=20, max_per_source=4)
        if hits:
            top = hits[0][1]
            hits = [(p, s) for p, s in hits if s >= 0.3 * top and _terms(name) & _terms(p.text + " " + p.title)]
        dim_hits = store.search(f"{query} {dim_terms}", k=10, max_per_source=3) if dim_terms else []
        # A data table carrying the entity's own row is per-entity evidence on its own.
        in_table = any(p.kind in {"table", "calculated"} and re.search(rf"(?im)^\s*{re.escape(name)}\s*\|", p.text)
                       for p, _ in hits)
        rows.append({"entity": name, "type": entity.get("type") or "", "passages": len(hits),
                     "sources": len({p.source_id for p, _ in hits}),
                     "passageIds": [p.passage_id for p, _ in hits][:20],
                     "dimensionPassages": len(dim_hits), "inDataTable": in_table,
                     "status": "covered" if len(hits) >= MIN_ENTITY_PASSAGES or in_table else "insufficient_evidence"})
    return rows


async def build_outline(store, gateway, *, question: str, tier: str, today: str, window_note: str = "") -> Outline:
    overview = evidence_overview(store)
    system, prompt = outline_prompt(question, tier, overview, today, window_note)
    result = await gateway.generate(purpose="outline", section_id="outline", system=system, prompt=prompt,
                                    max_tokens=8192, temperature=0.2, json_mode=True, timeout=120, retries=1)
    data = result.json() if result.ok else None
    if not isinstance(data, dict):
        raise OutlineError(f"outline call failed: {result.error or 'invalid JSON'}")
    sections = _sections_from_json(data, tier)
    if len(sections) < 3:
        raise OutlineError(f"outline returned {len(sections)} usable sections")
    rules = TIER_RULES.get(tier, TIER_RULES["detailed"])
    decisions: list[str] = []
    entities = [e for e in (data.get("entities") or []) if isinstance(e, dict)][:40]
    dimensions = _clean_list(data.get("dimensions"), 10)
    coverage = entity_coverage(store, entities, dimensions)
    for row in coverage:
        if row["status"] != "covered":
            decisions.append(f"Entity '{row['entity']}' has insufficient evidence ({row['passages']} passages) - no dedicated coverage.")
    assign_passages(store, sections, rules["k_leaf"])
    resolve_thin_evidence(store, sections, decisions)
    meta: dict = {"tier": tier, "outlineModel": result.model}
    size_budgets(store, sections, tier, meta)
    outline = Outline(title=str(data.get("title") or "")[:160], thesis=str(data.get("thesis") or "")[:600],
                      sections=sections, entities=entities, dimensions=dimensions, entity_coverage=coverage,
                      decisions=decisions, meta=meta)
    # Fixed sections around the analysis.
    outline.sections.insert(0, Section(section_id="exec", title="Executive Summary", kind="executive_summary",
                                       word_budget=rules["exec_words"]))
    outline.sections.append(Section(section_id="method", title="Methodology and Evidence Base", kind="methodology"))
    if tier == "in_depth":
        outline.sections.append(Section(section_id="appx-sources", title="Appendix A — Source Reliability", kind="appendix_sources"))
        outline.sections.append(Section(section_id="appx-data", title="Appendix B — Data Tables", kind="appendix_data"))
    log.info("V2 outline: sections=%d kept=%d entities=%d covered=%d decisions=%d planned_words=%d",
             len(sections), len(outline.analysis_sections()), len(coverage),
             sum(1 for r in coverage if r["status"] == "covered"), len(decisions), meta["plannedAnalysisWords"])
    return outline


def outline_json(outline: Outline) -> str:
    return json.dumps(outline.to_dict(), ensure_ascii=False)
