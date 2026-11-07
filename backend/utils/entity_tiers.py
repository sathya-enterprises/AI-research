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

    def to_dict(self) -> dict:
        return {"entity": self.name, "tier": self.tier, "tablePassages": len(self.table_passages),
                "textPassages": len(self.text_passages), "goodTextSources": len(self.good_text_sources),
                "aliases": sorted(self.aliases)[:8]}


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


def discover_entities(store, *, max_entities: int = 40) -> list[EntityEvidence]:
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
    ranked = sorted(found.values(), key=lambda e: (-len(e.good_text_sources), -len(e.table_passages), e.name))
    return ranked[:max_entities]


def assign_tiers(entities: list[EntityEvidence], *, max_deep_dives: int = 10) -> dict[str, list[EntityEvidence]]:
    tiers = {"deep_dive": [], "compact": [], "coverage_note": []}
    names = [e.name.casefold() for e in entities]

    def ambiguous(entity: EntityEvidence) -> bool:
        # "Services" is a word inside "Financial Services"/"IT Services": its text
        # matches are mostly other entities, so it cannot earn a deep-dive.
        own = entity.name.casefold()
        return entity.name not in SYNONYMS and any(
            other != own and re.search(rf"(?<![a-z]){re.escape(own)}(?![a-z])", other) for other in names)

    for entity in entities:
        if (len(entity.good_text_sources) >= DEEP_DIVE_SOURCES and len(tiers["deep_dive"]) < max_deep_dives
                and not ambiguous(entity)):
            entity.tier = "deep_dive"
        elif entity.table_passages or entity.good_text_sources:
            entity.tier = "compact"
        else:
            entity.tier = "coverage_note"
        tiers[entity.tier].append(entity)
    return tiers


def entity_tiers(store, *, tier: str = "in_depth") -> dict[str, list[EntityEvidence]]:
    # Every entity meeting the deep-dive evidence rule gets one, up to a configurable
    # ceiling (ranked by evidence) that keeps the report within the LLM call budget.
    import os
    try:
        max_deep = int(os.getenv("GG_V2_MAX_DEEP_DIVES", "16")) if tier == "in_depth" else 3
    except ValueError:
        max_deep = 16 if tier == "in_depth" else 3
    return assign_tiers(discover_entities(store), max_deep_dives=max_deep)


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
            tiers.setdefault(tier, []).append(entity)
    return tiers
