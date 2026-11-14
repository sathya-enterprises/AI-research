"""Entity discovery and evidence tiers for the V2 outline (Stage C1).

Entities are discovered from the first column of data tables in the evidence
store (sector indices, companies, regions ...), aliases are merged ("Nifty IT",
"IT", "Information Technology"), broad benchmarks are excluded, and each entity
is placed in an evidence tier:

  deep_dive      substantive text from >= 3 good sources (reliability >= 0.5,
                 entity mentioned at least twice in the passage) -> its own
                 3-5 page section;
  compact        table rows (or 1-2 good text sources) -> a short entry in a
                 compact comparison section;
  coverage_note  neither -> listed in the methodology's coverage note, not padded.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

GOOD_RELIABILITY = 0.5
DEEP_DIVE_SOURCES = 3
_BENCHMARK_RE = re.compile(
    r"^(?:nifty|bse|s&p bse|sensex)?\s*(?:50|100|200|500|next 50|midcap(?:\s*\d+)?|smallcap(?:\s*\d+)?|largemidcap\s*\d*|"
    r"total market|sensex|midcap select|microcap\s*\d*|india vix|vix)$", re.I)
_GENERIC_LABELS = {
    "total", "sector", "sectors", "index", "indices", "name", "date", "period", "quarter", "month", "year", "others",
    "other", "grand total", "all sectors", "category", "particulars", "metric", "value", "change", "returns", "market",
    # factor / metric row labels are dimensions, not entities
    "nifty", "momentum", "quality", "value", "growth", "low volatility", "alpha", "beta", "breadth", "benchmark",
    "recovery", "relative strength", "earnings revisions", "earnings upgrades", "earnings downgrades", "volatility",
    "leaders", "laggards", "leading", "lagging", "improving", "weakening", "average", "median", "fii", "dii", "fpi",
    # comparison words and financial line items
    "better", "worse", "higher", "lower", "same", "neutral", "positive", "negative", "up", "down", "inline",
    "turnover", "inventories", "inventory", "revenue", "revenues", "sales", "net profit", "profit", "ebitda", "pat",
    "eps", "debt", "assets", "liabilities", "equity", "cash", "margin", "margins", "capex", "receivables",
    "payables", "dividend", "book value", "income", "expenses", "interest", "borrowings", "reserves",
    "cost of inputs", "employees", "full-time employees", "headcount", "raw materials", "power and fuel",
    # valuation metrics are dimensions, not entities
    "p/e ratio", "pe ratio", "p/e", "pe", "p/b ratio", "pb ratio", "p/b", "pb", "dividend yield", "earnings yield",
    "roe", "roce", "market cap", "market capitalisation", "market capitalization", "ev/ebitda", "price to earnings",
    "price to book", "beta value", "free float",
}
_PERIOD_LABEL_RE = re.compile(
    r"^(?:\d+\s*(?:years?|yrs?|y|months?|mths?|m|weeks?|w|days?|d)|ytd|mtd|qtd|since inception|inception"
    r"|[qh][1-4]\s*[:\-/ ]?\s*(?:fy|cy)?\s*'?\d{2,4}(?:\s*[-/]\s*\d{2,4})?|fy\s*'?\d{2,4}(?:\s*[-/]\s*\d{2,4})?)$", re.I)
_COMPANY_HEADER_RE = re.compile(r"(?i)^(?:company|companies|stock|stocks|scrip|symbol|name of (?:the )?company|constituent|issuer)$")
# Common Indian sector synonyms; keys are canonical display names.
SYNONYMS = {
    "IT": ["information technology", "it services", "technology", "it & telecom"],
    "Bank": ["banks", "banking", "bank nifty", "banknifty"],
    "Private Bank": ["private banks", "private sector banks"],
    "PSU Bank": ["psu banks", "public sector banks", "public sector bank"],
    "Financial Services": ["financials", "financial", "finance", "fin services", "finserv", "financial services 25/50"],
    "FMCG": ["fast moving consumer goods", "consumer staples"],
    "Pharma": ["pharmaceuticals", "pharmaceutical"],
    "Healthcare": ["healthcare index", "health care", "healthcare services"],
    "Auto": ["automobile", "automobiles", "automobile and auto components", "autos", "automotive"],
    "Metal": ["metals", "metals & mining", "metals and mining"],
    "Realty": ["real estate", "realty index"],
    "Energy": ["power", "energy index"],
    "Oil & Gas": ["oil and gas", "oil, gas & consumable fuels", "oil gas"],
    "Media": ["media & entertainment", "media and entertainment", "media, entertainment & publication"],
    "Consumer Durables": ["consumer durable"],
    "Infrastructure": ["infra"],
    "Capital Goods": ["capital good", "industrials"],
    "Telecom": ["telecommunication", "telecom services", "telecommunications"],
    "Chemicals": ["chemical"],
    "Defence": ["defense", "india defence", "aerospace & defence"],
    "Capital Markets": ["capital market"],
}
_ALIAS_TO_CANON = {alias: canon for canon, aliases in SYNONYMS.items() for alias in [canon.casefold(), *aliases]}


@dataclass
class EntityEvidence:
    name: str
    aliases: set[str] = field(default_factory=set)
    table_passages: set[str] = field(default_factory=set)
    table_sources: set[str] = field(default_factory=set)
    company_rows: int = 0
    text_passages: set[str] = field(default_factory=set)
    good_text_sources: set[str] = field(default_factory=set)
    tier: str = "coverage_note"
    role: str = ""        # "body": a publisher / governing body (the source of data, not a subject)
    seeded: bool = False  # named by the measure plan (the planner expects data for it)

    def to_dict(self) -> dict:
        out = {"entity": self.name, "tier": self.tier, "tablePassages": len(self.table_passages),
               "textPassages": len(self.text_passages), "goodTextSources": len(self.good_text_sources),
               "aliases": sorted(self.aliases)[:8]}
        if self.role:
            out["role"] = self.role
        if self.seeded:
            out["seeded"] = True
        return out


def canonical(label: str) -> str | None:
    text = re.sub(r"\s+", " ", re.sub(r"[*_`\[\]()]|\bindex\b|\b(?:tri|pr)\b", " ", str(label or ""), flags=re.I)).strip(" -–—:|")
    if not text or len(text) > 60 or re.search(r"\d{3,}|\d+(?:\.\d+)?%", text):
        return None
    stripped = re.sub(r"^(?:nifty|s&p bse|bse)\s+", "", text, flags=re.I).strip()
    key = stripped.casefold()
    if not key or key in _GENERIC_LABELS or _BENCHMARK_RE.match(text) or _BENCHMARK_RE.match(stripped) or _PERIOD_LABEL_RE.match(key):
        return None
    if key in _ALIAS_TO_CANON:
        return _ALIAS_TO_CANON[key]
    if not re.search(r"[a-z]", key):
        return None
    return stripped if stripped.isupper() and len(stripped) <= 5 else stripped.title()


def _pattern(entity: EntityEvidence) -> re.Pattern:
    names = sorted({entity.name, *entity.aliases, *[a for a in SYNONYMS.get(entity.name, [])]}, key=len, reverse=True)
    alts = "|".join(re.escape(n) for n in names if len(n) >= 2)
    flags = 0 if entity.name.isupper() and len(entity.name) <= 4 else re.I  # "IT" must not match "it"
    return re.compile(rf"(?<![A-Za-z]){'(?:' + alts + ')'}(?![A-Za-z])", flags)


_REGIONS = ("Sub-Saharan Africa", "Africa", "South Asia", "East Asia", "Southeast Asia", "South-East Asia", "Latin America",
            "Middle East", "North Africa", "Eastern Mediterranean", "Western Pacific", "Americas", "Europe", "Central Asia",
            "North America", "South America", "Caribbean", "Oceania")
_TEXT_ENTITY_STOP = {"The", "This", "These", "That", "In", "On", "For", "From", "With", "And", "But", "However", "While",
                     "According", "Figure", "Table", "Source", "Sources", "Report", "Section", "Data", "Note", "January",
                     "February", "March", "April", "May", "June", "July", "August", "September", "October", "November",
                     "December", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday", "Since",
                     "During", "After", "Before", "Between", "Although", "Despite", "Overall", "Globally", "Global",
                     "World", "COVID", "Covid", "Executive Summary", "Key", "New", "Total", "Our World", "World Bank",
                     "Our World in Data", "International Monetary Fund", "PDF", "URL", "API", "CEO", "GDP", "USD", "INR"}


_GENERIC_ACRONYMS = {"TABLE", "OEM", "OEMS", "CEO", "CFO", "API", "PDF", "URL", "FAQ", "USD", "INR", "GDP", "YOY", "QTD", "YTD",
                     "CAGR", "FY", "CY", "EV", "EVS", "AI", "IT", "US", "UK", "EU", "UN", "NA", "ID", "KPI", "ROI", "R&D"}
# publishers / statistical and governing bodies: usually the SOURCE of the data, not a subject of the report
_BODIES = {"WHO", "UNICEF", "World Bank", "IMF", "IEA", "OECD", "WTO", "UNCTAD", "NITI", "NITI Aayog", "PIB", "MHI", "RBI",
           "SEBI", "Ministry", "Government", "Government of India", "Member States", "Statista", "Reuters", "Bloomberg"}
_PLACE_QUESTION_RE = re.compile(r"(?i)\b(?:regions?|countries|country|nations?|continents?)\b")
_PLACE_ALIASES = {"United States": {"U.S.", "US", "USA", "United States of America"}, "United Kingdom": {"UK", "U.K.", "Britain"},
                  "European Union": {"EU", "E.U."}, "South Korea": {"Korea", "Republic of Korea"}, "China": {"PRC"}}


def _stem_words(text: str) -> set[str]:
    return {w[:-1] if len(w) > 4 and w.endswith("s") else w for w in re.findall(r"[a-z0-9]+", str(text or "").casefold())}


def discover_text_entities(store, question: str = "", *, min_sources: int = 3, max_entities: int = 24,
                           seeds: list[str] | None = None, scope: str = "") -> list[EntityEvidence]:
    """Stage K: entities of a non-market topic from the TEXT (countries, regions, recurring named entities such as
    programmes, policies, companies or technologies), counted only when at least ``min_sources`` independent sources
    discuss them. Publishers / statistical bodies are at most compact; places come first when the question asks
    about countries or regions. Market topics keep the table-based discovery (sector names)."""
    try:
        from utils.official_data import COUNTRIES
        places = [n for n in COUNTRIES.values() if n not in {"World"}] + list(_REGIONS)
    except Exception:  # pragma: no cover
        places = list(_REGIONS)
    q_stems = _stem_words(question)
    place_first = bool(_PLACE_QUESTION_RE.search(str(question or "")))
    # a question comparing several places ("India, China, Brazil and Nigeria") is about each of them
    asked_places = [n for n in places if re.search(rf"(?<![A-Za-z]){re.escape(n)}(?![A-Za-z])", str(question or ""))]
    from utils.publisher_roles import PublisherRoles
    roles = PublisherRoles(store, question)
    counts: dict[str, set[str]] = {}
    for passage in store.passages:
        if passage.kind in {"calculated"}:
            continue
        text = str(passage.text or "")
        for name in places:
            variants = [name, *_PLACE_ALIASES.get(name, ())]
            if any(re.search(rf"(?<![A-Za-z]){re.escape(v)}(?![A-Za-z])", text) for v in variants):
                counts.setdefault(name, set()).add(passage.source_id)
        for m in re.finditer(r"\b(?:[A-Z]{2,6}\d?|[A-Z][a-z]+)(?:(?:\s+(?:of|and|for|the)\s+|\s+)(?:[A-Z]{2,6}\d?|[A-Z][a-z]+)){0,4}\b", text):
            name = m.group(0).strip()
            first = name.split()[0]
            if (name in _TEXT_ENTITY_STOP or first in _TEXT_ENTITY_STOP or len(name) < 3 or name.upper() in _GENERIC_ACRONYMS
                    or (" " not in name and not name.isupper())):  # a single capitalised word is usually a sentence start
                continue
            counts.setdefault(name, set()).add(passage.source_id)
    # case duplicates ("INDIA" / "India") fold into the mixed-case form; fragments of a longer name are dropped
    folded: dict[str, set[str]] = {}
    for name, sources in counts.items():
        key = next((k for k in folded if k.casefold() == name.casefold()), None)
        if key is None:
            folded[name] = set(sources)
        else:
            folded[key] |= sources
            if name[1:].islower() or not key[1:].islower():
                folded[name] = folded.pop(key)
    # Stage N: entities the measure plan names (states, companies, programmes) are candidates with one source;
    # they still need the deep-dive evidence rule to earn a section, and entity search looks for more.
    seed_names = []
    for seed in seeds or []:
        seed = re.sub(r"\s+", " ", str(seed or "")).strip()
        if len(seed) < 2 or any(seed.casefold() == s.casefold() for s in seed_names):
            continue
        hit = re.compile(rf"(?<![A-Za-z]){re.escape(seed)}(?![A-Za-z])", re.I)
        found = {p.source_id for p in store.passages if p.kind != "calculated" and hit.search(str(p.text or ""))}
        key = next((k for k in folded if k.casefold() == seed.casefold()), seed)
        folded[key] = folded.get(key, set()) | found
        seed_names.append(key)
    names = sorted(folded, key=lambda n: -len(folded[n]))
    qualifying = [n for n in names if len(folded[n]) >= min_sources or (n in seed_names and folded[n])]
    keep = []
    for name in qualifying:
        if any(name != other and re.search(rf"(?<![A-Za-z]){re.escape(name)}(?![A-Za-z])", other)
               and len(folded[other]) >= 0.6 * len(folded[name]) for other in qualifying):
            continue  # "CHIPS" inside "CHIPS and Science Act"
        if name not in places and name not in seed_names and _stem_words(name) <= q_stems:
            continue  # the topic itself ("Electric Vehicles" in an EV question)
        if name in places and not place_first and _stem_words(name) <= q_stems and len(asked_places) < 2:
            continue  # the question's own scope ("India" in "India's EV market") is not an entity within it
        keep.append(name)
    keep.sort(key=lambda n: (0 if (place_first and n in places) else 1, -len(folded[n])))
    # seeds are kept even when the text-discovered list is full
    keep = keep[:max_entities] + [n for n in keep[max_entities:] if n in seed_names]
    out: list[EntityEvidence] = []
    for name in keep:
        entity = EntityEvidence(name=name, aliases=set(_PLACE_ALIASES.get(name, ())), seeded=name in seed_names)
        pattern = _pattern(entity)
        for passage in store.passages:
            if passage.kind in {"table", "calculated"}:
                continue
            mentions = len(pattern.findall(passage.text))
            if mentions:
                entity.text_passages.add(passage.passage_id)
                if mentions >= 2 and passage.reliability >= GOOD_RELIABILITY:
                    entity.good_text_sources.add(passage.source_id)
        if name in places and place_first and len(entity.good_text_sources) < DEEP_DIVE_SOURCES:
            # a place the question asks about qualifies with 2 good sources (most sources name places once)
            entity.good_text_sources |= {passage.source_id for passage in store.passages
                                         if passage.passage_id in entity.text_passages}
        # Stage N: publishers / governing bodies found from the run's own sources, not only the fixed list
        # an entity the measure plan names (a company to compare) is a subject even when it publishes its own pages
        entity.aliases.add("__body__" if name not in seed_names and (name in _BODIES or roles.is_body(name)) else "")
        # Stage N: the plan's scope is one country; another country the question does not name is context
        # (comparisons), at most a compact entity, never a deep-dive of an India-only report
        if (scope and name in places and name.casefold() not in scope.casefold() and name not in seed_names
                and name.casefold() not in str(question or "").casefold()):
            entity.aliases.add("__out_of_scope__")
        out.append(entity)
    return out


def discover_entities(store, *, max_entities: int = 40, question: str = "",
                      seeds: list[str] | None = None, scope: str = "") -> list[EntityEvidence]:
    found: dict[str, EntityEvidence] = {}
    for passage in store.passages:
        if passage.kind not in {"table", "calculated"}:
            continue
        table_lines = [ln for ln in passage.text.splitlines() if " | " in ln]
        first_header = table_lines[0].strip().strip("|").split("|")[0].strip() if table_lines else ""
        company_table = bool(_COMPANY_HEADER_RE.match(first_header))
        rows = table_lines[1:]  # skip the header row
        for row in rows:
            label = row.strip().strip("|").split("|")[0].strip()
            canon = canonical(label)
            if not canon:
                continue
            entity = found.setdefault(canon, EntityEvidence(name=canon))
            entity.aliases.add(label.strip())
            entity.table_passages.add(passage.passage_id)
            entity.table_sources.add(passage.source_id)
            if company_table:
                entity.company_rows += 1
    # A label is an entity if it is a known sector name, or it labels table rows
    # in at least two different sources (one table's row labels may be metrics).
    found = {k: e for k, e in found.items() if k in SYNONYMS or len(e.table_sources) >= 2}
    # When the entities are mostly sectors, single companies (TCS, GAIL, ONGC) are
    # constituents, not peer entities: drop company-table rows and ticker-like labels.
    if sum(1 for k in found if k in SYNONYMS) >= 6:
        found = {k: e for k, e in found.items() if k in SYNONYMS or (
            e.company_rows < len(e.table_passages) and not re.fullmatch(r"[A-Z&]{2,6}", k))}
    for entity in found.values():
        pattern = _pattern(entity)
        for passage in store.passages:
            if passage.kind in {"table", "calculated"}:
                continue
            mentions = len(pattern.findall(passage.text))
            if mentions:
                entity.text_passages.add(passage.passage_id)
                if mentions >= 2 and passage.reliability >= GOOD_RELIABILITY:
                    entity.good_text_sources.add(passage.source_id)
    # In a sector-dominated set, a label that is not a known sector must also be
    # discussed in text (survey parameters and constituents appear only in tables).
    if sum(1 for k in found if k in SYNONYMS) >= 6:
        found = {k: e for k, e in found.items() if k in SYNONYMS or e.good_text_sources}
    if question:
        from utils.report_outline import is_market_topic
        if not is_market_topic(question):
            for entity in discover_text_entities(store, question, seeds=seeds, scope=scope):
                if entity.name not in found:
                    found[entity.name] = entity
    ranked = sorted(found.values(), key=lambda e: (-len(e.good_text_sources), -len(e.table_passages), e.name))
    return ranked[:max_entities] + [e for e in ranked[max_entities:] if e.seeded]


# a sub-sector reported inside its parent: one deep-dive covers both when their evidence overlaps
_SUBSECTOR_OF = {"Pharma": "Healthcare", "Power": "Utilities", "Oil & Gas": "Energy", "Consumer Services": "Services"}


def assign_tiers(entities: list[EntityEvidence], *, max_deep_dives: int = 10) -> dict[str, list[EntityEvidence]]:
    tiers = {"deep_dive": [], "compact": [], "coverage_note": []}
    names = [e.name.casefold() for e in entities]

    def ambiguous(entity: EntityEvidence) -> bool:
        # "Services" is a word inside "Financial Services"/"IT Services": its text
        # matches are mostly other entities, so it cannot earn a deep-dive.
        own = entity.name.casefold()
        return entity.name not in SYNONYMS and not entity.seeded and any(
            other != own and re.search(rf"(?<![a-z]){re.escape(own)}(?![a-z])", other) for other in names)

    for entity in entities:
        is_body = "__body__" in entity.aliases or entity.name in _BODIES
        out_of_scope = "__out_of_scope__" in entity.aliases
        if is_body:
            entity.role = "body"
        elif out_of_scope:
            entity.role = "out_of_scope"
        is_body = is_body or out_of_scope
        entity.aliases.discard("__body__")
        entity.aliases.discard("__out_of_scope__")
        entity.aliases.discard("")
        if (len(entity.good_text_sources) >= DEEP_DIVE_SOURCES and len(tiers["deep_dive"]) < max_deep_dives
                and not ambiguous(entity) and not is_body):
            entity.tier = "deep_dive"
        elif entity.table_passages or entity.good_text_sources:
            entity.tier = "compact"
        else:
            entity.tier = "coverage_note"
        tiers[entity.tier].append(entity)
    # Stage L: a deep-dive whose evidence is mostly another deep-dive's evidence (Pharma inside
    # Healthcare) would retell the same facts in a second section; it is covered as a compact entity.
    kept: list[EntityEvidence] = []
    for entity in list(tiers["deep_dive"]):
        own = entity.text_passages
        parent = _SUBSECTOR_OF.get(entity.name)
        # (an entity the plan compares is not a sub-part of another: India and China share many passages)
        host = None if entity.seeded else next((k for k in kept if own and (
            len(own & k.text_passages) >= 0.6 * len(own)
            or (k.name == parent and len(own & k.text_passages) >= 0.4 * len(own)))), None)
        if host is not None:
            tiers["deep_dive"].remove(entity)
            entity.tier = "compact"
            tiers["compact"].append(entity)
        else:
            kept.append(entity)
    return tiers


def entity_tiers(store, *, tier: str = "in_depth", question: str = "",
                 seeds: list[str] | None = None, scope: str = "") -> dict[str, list[EntityEvidence]]:
    # Every entity meeting the deep-dive evidence rule gets one, up to a configurable
    # ceiling (ranked by evidence) that keeps the report within the LLM call budget.
    import os
    try:
        max_deep = int(os.getenv("GG_V2_MAX_DEEP_DIVES", "16")) if tier == "in_depth" else 3
    except ValueError:
        max_deep = 16 if tier == "in_depth" else 3
    if question and tier == "in_depth":
        from utils.report_outline import is_market_topic, is_multi_quarter_sector_rotation_request
        if not is_market_topic(question):
            max_deep = min(max_deep, 8)  # Stage K: a non-market in-depth report has at most 8 entity deep-dives
        if is_multi_quarter_sector_rotation_request(question):
            max_deep = min(max_deep, 4)  # the matrix covers the full sector universe; deep dives focus on standout evidence
    entities = discover_entities(store, question=question, seeds=seeds, scope=scope)
    _seed_keys = {str(x).casefold() for x in seeds or []}
    for _e in entities:  # an entity the plan named is seeded however it was discovered (table or text)
        if _e.name.casefold() in _seed_keys:
            _e.seeded = True
    if question:
        from utils.report_outline import is_market_topic
        if not is_market_topic(question):
            entities = _non_market_hygiene(entities, question=question, seeds=seeds or [], scope=scope, store=store)
    return assign_tiers(entities, max_deep_dives=max_deep)


def _measure_name(name: str) -> bool:
    from utils.chart_titles import _MEASURE_WORDS
    return bool(_MEASURE_WORDS.search(name) or re.search(
        r"(?i)\b(?:mortality|expectancy|density|prevalence|incidence|coverage|expenditure|spending|beds|literacy|"
        r"enrolment|enrollment|emissions?|consumption|output|yield)\b", name))


_METADATA_ENTITY_RE = re.compile(
    r"(?i)\b(?:also\s+known\s+as|aka|(?:unique\s+)?identifier|codes?|calculation(?:\s+type)?|"
    r"confidence\s+intervals?|direction\s+of\s+progress|frequency(?:\s+of\s+data\s+release)?|periodicity|"
    r"(?:first|most\s+recent|last)\s+available\s+data|geographic\s+entities(?:\s+and\s+groups)?|metadata|"
    r"short\s+description|definition|unit\s+of\s+measure|value\s+display\s+label|semantic\s+value|"
    r"subject\s+of\s+measure|sdg\s+goal|data\s+release)\b")


def _table_peers(store, named: list[str]) -> set[str]:
    """Labels that sit in the same table rows / header as at least two of the named entities: peers of the same
    kind (a country table that lists India and China also lists Hong Kong and Lao PDR)."""
    from utils.table_focus import _key, matches
    peers: set[str] = set()
    by_source: dict[str, list[str]] = {}  # a long table is split across passages of one source
    for passage in getattr(store, "passages", None) or []:
        if getattr(passage, "kind", "") != "table":
            continue
        lines = [ln for ln in str(passage.text or "").splitlines() if " | " in ln]
        if len(lines) < 2:
            continue
        cells = [c.strip() for c in lines[0].split("|")] + [ln.split("|")[0].strip() for ln in lines[1:]]
        cells += [ln.split("|")[1].strip() for ln in lines[1:] if ln.count("|") >= 1]  # after a rank column
        by_source.setdefault(str(getattr(passage, "source_id", "")), []).extend(cells)
    for cells in by_source.values():
        if sum(1 for n in named if any(matches(c, n) for c in cells)) >= 2:
            peers |= {_key(c) for c in cells if c}
    return peers


def _non_market_hygiene(entities: list[EntityEvidence], *, question: str, seeds: list[str], scope: str,
                        store=None) -> list[EntityEvidence]:
    """Stage N: table row labels are not all entities. A live health report gave deep-dives to "Female",
    "Life Expectancy" (the question's own measure) and Indonesia (not asked about).

    * a single ordinary word ("Female", "Population", "Definition") is not an entity unless the plan named it
      or it is an acronym / a place;
    * a name made only of the question's own words is the topic, not an entity within it;
    * a place outside the scope the question names is out of scope (compact at most), however it was found."""
    try:
        from utils.official_data import COUNTRIES
        places = set(COUNTRIES.values()) | set(_REGIONS)
    except Exception:  # pragma: no cover
        places = set(_REGIONS)
    seeded = {s.casefold() for s in seeds}
    q_stems = _stem_words(question)
    in_scope = f"{scope} {question}".casefold()
    from utils.table_focus import _key, focus_entities
    named = focus_entities(f"{scope} {question}")
    peers = _table_peers(store, named) if (store is not None and len(named) >= 2) else set()
    kept = []
    for e in entities:
        name = e.name
        if name.casefold() in seeded or e.seeded or name in SYNONYMS:
            kept.append(e)
            continue
        if name not in places and not e.good_text_sources and _METADATA_ENTITY_RE.search(name):
            continue  # schema labels belong to the source metadata, not the report's research entities
        if name not in places and " " not in name.strip() and not name.isupper() and not re.search(r"\d", name):
            continue  # one ordinary capitalised word from a table row
        if name not in places and _stem_words(name) <= q_stems:
            continue  # the question's own subject / measure
        if name not in places and not name.isupper() and _measure_name(name):
            continue  # "Infant Mortality Rate", "Population Density": a measure, not an entity
        if scope and (name in places or _key(name) in peers) and name.casefold() not in in_scope:
            e.aliases.add("__out_of_scope__")
        kept.append(e)
    return kept


def tier_summary(tiers: dict[str, list[EntityEvidence]]) -> dict:
    return {"counts": {k: len(v) for k, v in tiers.items()},
            "entities": {k: [e.to_dict() for e in v] for k, v in tiers.items()}}


def tiers_from_summary(store, summary: dict) -> dict[str, list[EntityEvidence]]:
    """Rebuild the tiers a run recorded (tier_summary output) against a store, for faithful offline replay."""
    found = {e.name: e for e in discover_entities(store, max_entities=200)}
    tiers: dict[str, list[EntityEvidence]] = {"deep_dive": [], "compact": [], "coverage_note": []}
    for tier, rows in (summary.get("entities") or {}).items():
        for row in rows or []:
            name = str(row.get("entity") or "")
            entity = found.get(name) or EntityEvidence(name=name)
            entity.tier = tier
            entity.role = str(row.get("role") or entity.role or "")
            entity.seeded = bool(row.get("seeded") or entity.seeded)
            tiers.setdefault(tier, []).append(entity)
    return tiers
