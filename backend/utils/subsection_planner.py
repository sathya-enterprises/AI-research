"""Per-sector deep-dive subsections chosen from each sector's own evidence (Stage D).

Every deep-dive used to get the same four subsections (performance, flows,
valuation, drivers/risks). Here the subsections are the facets that sector's
evidence actually covers, ordered by how much evidence each has, so a bank
deep-dive can be about credit growth and asset quality while a metals one is
about commodity prices and global demand. Thin sectors get fewer subsections;
budgets are then sized from each subsection's evidence, so lengths vary.
"""
from __future__ import annotations

import re
from collections import Counter

FACETS = (
    # (title, evidence pattern, retrieval query terms)
    ("Returns and relative strength", r"\breturns?\b|outperform|underperform|relative strength|momentum|rall(?:y|ied)",
     "returns relative strength quarter performance"),
    ("Foreign and domestic flows", r"\bFPIs?\b|\bFIIs?\b|\bDIIs?\b|inflows?|outflows?|net (?:buy|sell|investment)|mutual funds?",
     "FPI DII flows net investment ownership"),
    ("Valuation versus history", r"P/E|\bPE\b|P/B|valuation|multiples?\b|premium to|discount to",
     "valuation P/E multiple history premium"),
    ("Earnings and margins", r"earnings|\bEPS\b|revenue|margins?\b|profit|EBITDA|\bPAT\b|quarterly results",
     "earnings revenue margin profit results"),
    ("Demand, volumes and pricing", r"\bdemand\b|volumes?\b|pricing|tariffs?\b|consumption|\bsales\b|realisations?",
     "demand volume pricing sales consumption"),
    ("Credit growth and asset quality", r"credit growth|\bNPAs?\b|asset quality|deposits?\b|\bNIMs?\b|loan growth|slippages?",
     "credit growth asset quality NIM deposits"),
    ("Input costs and commodity prices", r"crude|commodit|input costs?|steel prices?|metal prices?|raw materials?|\bLME\b|coal",
     "crude commodity input costs prices"),
    ("Policy and regulation", r"\bRBI\b|monetary policy|regulat|government|\bGST\b|budget|\bPLI\b|\bSEBI\b",
     "policy regulation government RBI GST"),
    ("Orders, capex and execution", r"order (?:book|inflows?|backlog)|capex|execution|contracts? (?:won|awarded)|defence orders?",
     "order book capex execution contracts"),
    ("Global linkages", r"\bUS\b|global|exports?\b|dollar|\bFed\b|China|tariff war",
     "global US exports dollar China"),
)
RISK_FACET = ("Risks and what to watch", r"\brisks?\b|headwinds?|downside|uncertain|could (?:weigh|hurt|slow)|watch",
              "risks headwinds outlook what to watch")
MIN_FACET_PASSAGES = 3
MAX_FACETS = 3


def _entity_pattern(entity: str) -> re.Pattern:
    from utils.entity_tiers import SYNONYMS
    names = sorted({entity, *SYNONYMS.get(entity, [])}, key=len, reverse=True)
    alts = "|".join(re.escape(n) if (n.isupper() and len(n) <= 4) else f"(?i:{re.escape(n)})" for n in names if len(n) >= 2)
    return re.compile(rf"(?<![A-Za-z])(?:{alts})(?![A-Za-z])")


def facet_counts(store, entity: str) -> Counter:
    """Passages that discuss ``entity`` and each facet (one count per passage per facet)."""
    pattern = _entity_pattern(entity)
    counts: Counter = Counter()
    for passage in store.passages:
        text = str(passage.text or "")
        if not pattern.search(text):
            continue
        for title, regex, _ in (*FACETS, RISK_FACET):
            if re.search(regex, text):
                counts[title] += 1
    return counts


def plan_subsections(store, entity: str, baseline: dict | None = None) -> list[tuple[str, str, str]]:
    """[(title, goal, query terms)] for one sector from its evidence, risks last.

    Facets are ranked by how distinctive they are for this sector (its passage count
    against the average across the report's sectors, times the square root of its
    count), so a facet every sector mentions does not crowd out the sector-specific
    ones. The chosen facets are then ordered by how much evidence each has.
    """
    counts = facet_counts(store, entity)
    eligible = [f for f in FACETS if counts[f[0]] >= MIN_FACET_PASSAGES]

    def score(facet) -> float:
        avg = float((baseline or {}).get(facet[0], counts[facet[0]]))
        return (counts[facet[0]] + 1) / (avg + 1) * counts[facet[0]] ** 0.5

    chosen = sorted(eligible, key=score, reverse=True)[:MAX_FACETS]
    chosen.sort(key=lambda f: -counts[f[0]])
    if counts[RISK_FACET[0]] >= MIN_FACET_PASSAGES:
        chosen.append(RISK_FACET)
    return [(title, f"{title.lower()} for {entity}, from the evidence that covers it", f"{entity} {terms}")
            for title, _regex, terms in chosen]


def facet_baseline(store, entities: list[str]) -> dict:
    """Average passages per facet across the report's deep-dive sectors."""
    totals: Counter = Counter()
    for entity in entities:
        totals.update(facet_counts(store, entity))
    return {k: v / max(1, len(entities)) for k, v in totals.items()}


def _set_key(section) -> tuple[str, ...]:
    entity = re.escape(section.entity or "")
    titles = []
    for sub in section.subsections:
        title = re.sub(rf"(?i)^{entity}\s*[:—-]\s*", "", sub.title) if entity else sub.title
        titles.append(re.sub(r"[^a-z]+", " ", title.casefold()).strip())
    return tuple(titles)


def uniform_deep_dives(sections) -> bool:
    """True when at least half the deep-dives share one subsection set (the template problem)."""
    keys = [_set_key(s) for s in sections if s.role == "deep_dive" and s.status == "keep"]
    if len(keys) < 2:
        return False
    most = Counter(keys).most_common(1)[0][1]
    return most >= max(2, len(keys) / 2)


def diversify_deep_dives(store, sections, decisions: list[str]) -> int:
    """Replace a uniform deep-dive subsection template with evidence-chosen subsections. Returns sections changed."""
    from utils.report_outline import Section

    deep = [s for s in sections if s.role == "deep_dive" and s.status == "keep" and s.entity]
    if not deep or not (uniform_deep_dives(sections) or any(not s.subsections for s in deep)):
        return 0
    changed = 0
    baseline = facet_baseline(store, [s.entity for s in deep])
    for sec in deep:
        plan = plan_subsections(store, sec.entity, baseline)
        if len(plan) < 1:
            continue
        sec.subsections = [Section(section_id=f"{sec.section_id}.{i}", title=title, goal=goal, queries=[query],
                                   entities=[sec.entity], role="deep_dive", entity=sec.entity)
                           for i, (title, goal, query) in enumerate(plan, 1)]
        sec.passage_ids = []
        changed += 1
    if changed:
        decisions.append(f"Deep-dive subsections chosen per sector from each sector's evidence ({changed} sections), "
                         "instead of one shared template.")
    return changed


def distinct_sets(sections) -> int:
    return len({_set_key(s) for s in sections if s.role == "deep_dive" and s.status == "keep"})
