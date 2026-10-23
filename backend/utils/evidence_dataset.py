"""Deterministic numeric-observation extraction from scraped source evidence.

The report writer is an LLM and its charts are later pruned by strict evidence
gates. When the writer drops or paraphrases figures, the scraped evidence still
contains them (publisher tables, search-index extracts, news prose). This module
reads that evidence directly and records every comparable observation as

    entity · metric · period · value · unit · source ID · exact quoted line

No value is estimated, interpolated or converted beyond explicit unit scaling
(lakh crore → crore). Every observation carries the exact source line it came
from so downstream chart audits can verify it against the same text.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable

# ─────────────────────────────────────────────────────────────────────────────
# Metric lexicon. Order matters: more specific phrases first.
# kind: money (amounts), ratio (percent levels), multiple (x), price, count
# ─────────────────────────────────────────────────────────────────────────────
_METRICS: list[tuple[str, str, str, tuple[str, ...]]] = [
    # Domain metrics for event-driven market and trade research. These let the
    # evidence gallery plot sourced transaction values, stake percentages and
    # tariff/export measures instead of falling back to unrelated generic KPIs.
    ("transaction_value", "Transaction value", "money", (
        r"transaction\s+(?:value|valued|worth)", r"deal\s+value", r"block\s+deal\s+worth",
        r"bulk\s+deal\s+worth", r"shares?\s+(?:sold|offloaded|divested)\s+for",
        r"stake\s+worth", r"worth\s+(?:₹|rs\.?|inr|us\$|\$)",
    )),
    ("stake_percentage", "Stake sold / changed", "ratio", (
        r"stake\s+(?:of|sale|sold|offloaded|divested|reduction|reduced|increased|acquired)",
        r"shareholding\s+(?:fell|rose|reduced|increased|changed|to)",
        r"promoter\s+(?:holding|ownership|stake)",
    )),
    ("tariff_coverage", "Tariff coverage", "ratio", (
        r"tariff[- ]line\s+(?:coverage|access|concessions?)", r"duty[- ]free\s+access",
        r"tariff\s+coverage", r"export\s+value\s+coverage",
    )),
    ("export_value", "Export value", "money", (
        r"merchandise\s+exports?", r"exports?\s+(?:reached|rose|grew|stood|totalled|totaled|valued)",
        r"bilateral\s+trade", r"trade\s+value",
    )),
    ("sector_return", "Sector / index return", "ratio", (
        r"quarterly\s+(?:sector|index)\s+returns?", r"(?:sector|index)\s+returns?",
        r"relative\s+performance", r"\b1\s*yr\s+change\b", r"\b3\s*yr\s+change\b",
        r"\b10\s*[- ]\s*yr\s+cagr\b",
    )),
    # Banking-sector operating indicators must be recognized explicitly. If
    # they are left to generic money metrics, a nearby verb such as "increased"
    # can incorrectly turn a term-deposit interest rate into deposit growth.
    ("credit_growth", "Bank credit growth", "ratio", (
        r"(?:bank\s+)?credit\s+growth", r"growth\s+in\s+(?:bank\s+)?credit",
        r"bank\s+credit\s+(?:grew|increased|expanded|rose|accelerated)",
        # RBI prose: "buoyant bank credit, which increased by 16.0 per cent".
        r"bank\s+credit,?\s+which\s+(?:grew|increased|expanded|rose|accelerated)",
    )),
    ("deposit_growth", "Deposit growth", "ratio", (
        r"deposit\s+growth", r"growth\s+in\s+(?:bank\s+)?deposits?",
        r"deposit\s+growth\s+rate",
    )),
    ("repo_rate", "RBI repo rate", "ratio", (
        r"policy\s+repo\s+rate", r"repo\s+rate", r"policy\s+rate",
    )),
    ("term_deposit_rate", "Term deposit rate", "ratio", (
        r"weighted\s+average\s+domestic\s+term\s+deposit\s+rates?",
        r"(?:fresh|outstanding|domestic)\s+term\s+deposit\s+rates?",
        r"term\s+deposit\s+rates?",
    )),
    ("lending_rate", "Bank lending rate", "ratio", (
        r"weighted\s+average\s+(?:lending|lending\s+rate)",
        r"fresh\s+(?:rupee\s+)?lending\s+rates?", r"lending\s+rates?",
    )),
    ("upi_value", "UPI transaction value", "money", (
        r"upi\s+(?:transaction|payment|transactions|payments)\s+value",
        r"value\s+of\s+upi\s+(?:transactions|payments)",
    )),
    ("upi_volume", "UPI transaction volume", "count", (
        r"upi\s+(?:transaction|transactions|txns?)\s+(?:volume|count|number)",
        r"upi\s+transactions?",
    )),
    # Ratios / multiples first so "credit-deposit ratio" or "return on total
    # assets" are never claimed by the generic "deposits"/"total assets" terms.
    ("net_profit_margin", "Net profit margin", "ratio", (r"net\s+profit\s+margins?",)),
    ("gross_npa", "Gross NPA", "npa", (r"gross\s+npas?", r"\bgnpas?\b", r"gross\s+non[- ]performing\s+(?:assets?|advances|loans?)", r"gross\s+bad[- ]loans?")),
    ("net_npa", "Net NPA", "npa", (r"net\s+npas?", r"\bnnpas?\b", r"net\s+non[- ]performing\s+(?:assets?|advances|loans?)")),
    ("provision_coverage", "Provision coverage ratio", "ratio", (r"provision(?:ing)?\s+coverage(?:\s+ratio)?", r"\bpcr\b")),
    ("cd_ratio", "Credit-deposit ratio", "ratio", (r"credit[- ]deposit(?:\s+\(c/d\))?\s+ratio", r"\bc/d\s+ratio", r"\bcd\s+ratio")),
    ("cost_income", "Cost-to-income ratio", "ratio", (r"cost[- ]to[- ]income(?:\s+ratio)?", r"cost[- ]income(?:\s+ratio)?")),
    ("roe", "Return on equity", "ratio", (r"return\s+on\s+(?:net\s+)?(?:equity|worth)", r"\broe\b", r"\bronw\b")),
    ("roa", "Return on assets", "ratio", (r"return\s+(?:net\s+)?on\s+(?:total\s+|average\s+)?assets?", r"\broa\b")),
    ("nim", "Net interest margin", "ratio", (r"net\s+interest\s+margins?", r"\bnims?\b")),
    ("casa", "CASA ratio", "ratio", (r"\bcasa\b(?:\s+ratio)?",)),
    ("car", "Capital adequacy ratio", "ratio", (r"capital\s+to\s+risk[- ]weighted\s+assets?(?:\s+ratio)?", r"capital\s+adequacy(?:\s+ratio)?", r"\bcrar\b", r"\bcar\b")),
    ("slippage_ratio", "Slippage ratio", "ratio", (r"(?:half[- ]yearly\s+|annual\s+|gross\s+)?slippage\s+ratios?",)),
    ("interest_spread", "Interest spread", "ratio", (r"interest\s+spread",)),
    ("dividend_yield", "Dividend yield", "ratio", (r"dividend\s+yield",)),
    ("pe", "P/E ratio", "multiple", (r"\bp/?e\b(?:\s+ratio)?", r"price[- ]to[- ]earnings(?:\s+ratio)?")),
    ("pb", "P/B ratio", "multiple", (r"\bp/?b\b(?:\s+ratio)?", r"price[- ]to[- ]book(?:\s+value)?(?:\s+ratio)?")),
    ("market_cap", "Market capitalisation", "money", (r"market\s+cap(?:itali[sz]ation)?", r"\bm[\s.-]?cap\b", r"\bm\.\s?c\.?(?=\s|$|\|)")),
    ("net_profit", "Net profit", "money", (r"(?:standalone\s+|consolidated\s+)?net\s+profits?", r"profit\s+after\s+tax", r"\bpat\b", r"net\s+income")),
    ("operating_profit", "Operating profit", "money", (r"(?:pre[- ]provision\s+)?operating\s+profits?", r"\bppop\b")),
    ("net_interest_income", "Net interest income", "money", (r"net\s+interest\s+income", r"\bnii\b")),
    ("total_income", "Total income", "money", (r"total\s+income", r"total\s+revenues?", r"\brevenues?\b")),
    ("total_assets", "Total assets", "money", (r"total\s+assets", r"balance[- ]sheet\s+size", r"asset\s+size")),
    # Keep balance-sheet deposit components separate from total deposits so the
    # chart gallery never labels demand/savings/term deposits as the whole deposit
    # base merely because they share the word "deposits".
    ("demand_deposits", "Demand deposits", "money", (r"\bdemand\s+deposits?\b",)),
    ("savings_deposits", "Savings bank deposits", "money", (r"\bsavings?\s+(?:bank\s+)?deposits?\b",)),
    ("term_deposits", "Term deposits", "money", (r"\bterm\s+deposits?\b",)),
    ("deposits", "Deposits", "money", (r"(?:total\s+)?deposits?(?!\s+(?:rates?|pricing|yields?|costs?))",)),
    # Term loans are a component of advances, not total advances. They get a
    # separate metric key so a term-loan table cannot be titled "Advances".
    ("term_loans", "Term loans", "money", (r"\bterm\s+loans?\b",)),
    ("advances", "Advances", "money", (r"(?:total\s+)?loans?\s+and\s+advances", r"(?:gross\s+|net\s+|total\s+)?advances", r"loan\s+(?:book|portfolio)", r"(?:total\s+)?loans?(?!\s+loss)", r"credit\s+book")),
    ("provisions", "Provisions", "money", (r"provisions?(?:\s+and\s+contingencies)?",)),
    ("eps", "Earnings per share", "price", (r"\beps\b", r"earnings\s+per\s+share")),
    ("share_price", "Share price", "price", (r"\bcmp\b(?:\s*\(rs\))?", r"(?:current\s+)?(?:share|stock|market)\s+price")),
    ("shares_transacted", "Shares transacted", "count", (
        r"shares?\s+(?:sold|offloaded|divested|acquired|purchased|changed\s+hands)",
        r"share\s+sale", r"shares?\s+transacted",
    )),
    ("branches", "Branches", "count", (r"\bbranch(?:es)?\b",)),
]
_METRIC_BY_KEY = {key: (label, kind) for key, label, kind, _ in _METRICS}
_METRIC_PATTERNS = [(key, re.compile(rf"(?<![A-Za-z]){'|'.join(f'(?:{p})' for p in pats)}(?![A-Za-z])", re.I))
                    for key, _l, _k, pats in _METRICS]

_GROWTH_WORDS = re.compile(
    r"\b(?:grow(?:th|n|s|ing)?|grew|rise|rising|rose|increase[ds]?|increasing|up|jump(?:ed|s)?|surge[ds]?|"
    r"expand(?:ed|s)?|expansion|climb(?:ed|s)?|gain(?:ed|s)?|declin(?:e|ed|es|ing)|fell|fall(?:ing|s)?|"
    r"drop(?:ped|s)?|contract(?:ed|ion|s)?|decrease[ds]?|plunge[ds]?|yoy|y-o-y|year[- ]on[- ]year)\b", re.I)
_DECLINE_WORDS = re.compile(r"\b(?:declin(?:e|ed|es|ing)|fell|fall(?:ing|s)?|drop(?:ped|s)?|contract(?:ed|ion|s)?|decrease[ds]?|plunge[ds]?|slump(?:ed)?)\b", re.I)
_SEGMENT_QUALIFIER = re.compile(
    r"\b(?:sme|msme|small|mid[- ]market|retail|corporate|wholesale|business\s+banking|segment|home|housing|personal|"
    r"credit\s+card|cards?|agri(?:culture)?|rural|unsecured|vehicle|auto|gold|mortgage|commercial|international|overseas|"
    r"domestic\s+corporate|term|fcnr|casa|current\s+account|savings)\b", re.I)

_NUM_TOKEN = re.compile(
    r"(?P<cur>₹|\bRs\.?|\bINR|US\$|\$|\bUSD)?\s*(?P<sign>[+\-−–])?\s*"
    r"(?P<num>\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*"
    r"(?P<unit>%|per\s?cent\b|crore\s+shares?|lakh\s+shares?|shares?\b|lakh\s+crores?|lakh\s+cr\b|crores?\b|cr\b|lakhs?\b|bn\b|billion\b|mn\b|million\b|trillion\b|bps\b|basis\s+points?\b|times\b|x\b)?",
    re.I,
)
_PERIOD_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"(?P<q>Q[1-4])\s*['’]?\s*FY\s*['’]?(?P<qfy>\d{2,4})"
    r"|(?P<h>H[12])\s*FY\s*['’]?(?P<hfy>\d{2,4})"
    r"|(?P<nm>9M)\s*FY\s*['’]?(?P<nmfy>\d{2,4})"
    r"|FY\s*['’]?(?P<fy>\d{2,4})(?:\s*[-–/]\s*(?P<fy2>\d{2,4}))?"
    r"|(?P<y1>(?:19|20)\d{2})\s*[-–/]\s*(?P<y2>\d{2,4})(?![\d/-])"
    r"|(?P<cy>(?:19|20)\d{2})"
    r")(?![A-Za-z0-9])",
    re.I,
)
_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"], 1)}
_MONTH_ABBR = {k[:3]: v for k, v in _MONTHS.items()}
_QUARTER_PHRASE = re.compile(
    r"\b(?:(?:(?P<a>april|july|october|january)\s*[-–]\s*(?P<b>june|september|december|march)|(?P<end>june|september|december|march))\s+quarter"
    r"|(?:end\s+of|ended)\s+(?P<end2>june|september|december|march))\b",
    re.I,
)
_YEAR_AGO = re.compile(r"\b(?:a\s+year\s+(?:ago|earlier|back)|year[- ]ago|same\s+(?:period|quarter)\s+(?:last|a)\s+year|previous\s+year|last\s+year|corresponding\s+(?:period|quarter)\s+(?:of\s+the\s+)?(?:previous|last)\s+year)\b", re.I)
_PREV_QUARTER = re.compile(r"\b(?:preceding|previous|prior|sequential(?:ly)?|last)\s+quarter\b|\bq-?o-?q\b|\bsequentially\b", re.I)
_DATE_IN_TEXT = re.compile(
    r"\b(?:(?P<d1>\d{1,2})(?:st|nd|rd|th)?\s+(?P<m1>jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?,?\s+(?P<y1>20\d{2})"
    r"|(?P<m2>jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+(?P<d2>\d{1,2})(?:st|nd|rd|th)?,?\s+(?P<y2>20\d{2}))\b",
    re.I,
)
_AGGREGATE_RE = re.compile(r"\b(?:total|average|avg|median|industry|sector|system|all\s+banks|aggregate|overall|scbs?|psbs?|pvbs?)\b", re.I)
_CITATION_RE = re.compile(r"\[\s*S\d+(?:\s*[,;]\s*S\d+)*\s*\]", re.I)
_MONTH_YEAR_RE = re.compile(
    r"(?<![A-Za-z])(?P<end>end[- ]|end\s+of\s+)?(?P<mon>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
    r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?,?\s+(?:(?P<day>\d{1,2})(?:st|nd|rd|th)?,?\s+)?(?P<year>(?:19|20)\d{2})(?![\d])",
    re.I,
)
_HALF_RANGE_RE = re.compile(r"(?<![A-Za-z0-9])(?P<h>H[12])\s*[:\-]?\s*(?P<y1>(?:19|20)\d{2})\s*[-–/]\s*(?P<y2>\d{2,4})(?![\d])", re.I)
_MONTH_LABEL_RE = re.compile(r"\s*(?P<mon>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(?P<year>(?:19|20)\d{2})\s*", re.I)
_PERIOD_PREFIX_OK = re.compile(
    r"[\s,;:()\-–]*(?:(?:at|in|as|on|of|by|for|during|till|until|up\s*to|the|end|as\s+at|as\s+on|as\s+of|y-o-y|yoy|year[- ]on[- ]year)[\s,;:()\-–]*)*",
    re.I,
)
# Prose that describes a non-target institution group or a loan segment must
# not inherit the bank-group entity of an earlier sentence (an NBFC balance
# sheet or a large-borrower GNPA ratio is not an SCB system-level value).
_NON_TARGET_GROUP_RE = re.compile(
    r"(?i)\b(?:nbfcs?|non[- ]banking\s+financial|ucbs?|urban\s+co[- ]?operative|co[- ]?operative\s+banks?|rrbs?|"
    r"regional\s+rural|aifis?|all[- ]india\s+financial\s+institutions?|hfcs?|housing\s+finance\s+compan\w*|stcbs?|dccbs?|"
    r"mutual\s+funds?|insurers?|insurance\s+compan\w*)\b"
)
_SEGMENT_SUBJECT_RE = re.compile(
    r"(?i)\b(?:large\s+borrowers?|borrowers?['’]?\s+(?:portfolio|cohort)|top\s+\d+\s+borrowers?|portfolios?|msmes?|"
    r"retail\s+(?:loans?|credit|segment)|agricultur\w*|industr(?:y|ial)\s+(?:credit|loans?)|services\s+sector|"
    r"personal\s+loans?|credit\s+cards?|housing\s+loans?|vehicle\s+loans?|gold\s+loans?|unsecured)\b"
)
_SYSTEM_LEVEL_METRICS = {
    "gross_npa", "net_npa", "car", "provision_coverage", "roa", "roe", "nim", "credit_growth",
    "deposit_growth", "cd_ratio", "slippage_ratio", "casa", "cost_income",
}


def _month_period(mon: str, year: int, end_of_month: bool) -> tuple[str, float, str] | None:
    month = _MONTH_ABBR.get(str(mon or "")[:3].casefold())
    if not month:
        return None
    # "end-March 2025" is the Indian fiscal-year end and is reported by RBI as
    # the FY figure; keep it on the FY axis so it de-duplicates with "2024-25".
    if month == 3 and end_of_month:
        return f"FY{year % 100:02d}", float(year), "FY"
    abbr = [k for k, v in _MONTH_ABBR.items() if v == month][0].title()
    return f"{abbr} {year}", year + (month - 3) / 12.0, "M"


_UNIT_HINT_RE = re.compile(r"(?:\(|\b)(?:in\s+)?(?:₹|rs\.?|inr)?\s*(?P<u>lakh\s+crores?|crores?|cr\b|lakhs?|bn|billion|mn|million|%|per\s?cent)", re.I)


@dataclass
class Observation:
    entity: str
    entity_key: str
    metric: str
    metric_label: str
    value: float
    unit: str
    period: str
    period_sort: float
    period_kind: str
    source_id: str
    quote: str
    raw_value: str
    origin: str  # table | prose
    evidence_origin: str = ""
    is_target: bool = False
    is_aggregate: bool = False
    context_lines: list[str] = field(default_factory=list)
    suspect: str = ""
    order: int = 0

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.entity_key, self.metric, self.period)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity": self.entity, "metric": self.metric, "metricLabel": self.metric_label,
            "value": self.value, "unit": self.unit, "period": self.period, "periodKind": self.period_kind,
            "sourceId": self.source_id, "quote": self.quote[:400], "rawValue": self.raw_value,
            "origin": self.origin, "evidenceOrigin": self.evidence_origin,
            "isTarget": self.is_target, "isAggregate": self.is_aggregate, "suspect": self.suspect,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Entities
# ─────────────────────────────────────────────────────────────────────────────
_ENTITY_SUFFIX = re.compile(r"\b(?:ltd\.?|limited|plc|inc\.?|corp\.?|corporation|co\.)\s*$", re.I)
_GENERIC_ENTITY_WORDS = {"bank", "banks", "company", "group", "the", "of", "and", "&", "india", "ltd", "limited"}


def _clean_cell(value: object) -> str:
    text = str(value or "")
    text = _CITATION_RE.sub("", text)
    text = re.sub(r"`{1,3}|\*{1,3}|_{2}", "", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&amp;", "&").replace("&nbsp;", " ").replace(" ", " ")
    return re.sub(r"\s+", " ", text).strip(" :;-–")


def entity_key(name: str) -> str:
    text = _clean_cell(name).casefold()
    text = re.sub(r"[’'`]s\b", "", text)
    text = re.sub(r"[^a-z0-9& ]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    while True:
        stripped = _ENTITY_SUFFIX.sub("", text).strip()
        if stripped == text:
            break
        text = stripped
    text = re.sub(r"^the\s+", "", text)
    return text


def _acronym(key: str) -> str:
    words = [w for w in key.split() if w not in {"of", "and", "the", "&", "for"}]
    return "".join(w[0] for w in words if w) if len(words) >= 2 else ""


_TRAILING_TOPIC_WORDS = {
    "valuation", "valuations", "analysis", "performance", "comparison", "stock", "stocks", "shares", "share",
    "report", "reports", "outlook", "results", "financials", "financial", "fundamentals", "growth", "metrics",
    "data", "profile", "overview", "strategy", "prospects", "price", "prices", "earnings", "trends", "trend",
    "revenue", "revenues", "profit", "profits", "margin", "margins", "sales", "income", "assets", "npa", "npas",
}


def extract_requested_entities(question: str) -> list[str]:
    """Pull an explicit enumeration of named entities from the request.

    "Compare SBI, HDFC Bank, ICICI Bank, and Axis Bank using …" →
    ["SBI", "HDFC Bank", "ICICI Bank", "Axis Bank"]. Generic: any list of
    capitalised names/acronyms joined by commas, "and", "vs", "/" or "&".
    """
    text = re.sub(r"\s+", " ", str(question or "")).strip()
    if not text:
        return []
    # The entity list is often not in the first sentence ("Analyze the sector from
    # FY2020... Compare SBI, HDFC Bank, ICICI Bank and Axis Bank using ..."). Try
    # the first sentence as before, then later sentences that open with an explicit
    # comparison verb, and return the first enumeration of >= 2 entities.
    sentences = [x for x in re.split(r"(?<=[.!?])\s+|\n", text) if x.strip()]
    for index, sentence in enumerate(sentences[:8]):
        if index and not re.match(r"^\s*(?:please\s+)?(?:compare|comparing|contrast|benchmark|rank)\b", sentence, re.I):
            continue
        found = _extract_entities_from_sentence(sentence)
        if len(found) >= 2:
            return found
    return []


def _extract_entities_from_sentence(first: str) -> list[str]:
    lead = re.compile(
        r"^\s*(?:please\s+)?(?:can\s+you\s+)?(?:compare|comparing|comparison\s+of|contrast|analy[sz]e|analysis\s+of|evaluate|assess|benchmark|rank|review|"
        r"research|study|examine|look\s+at|give\s+me|create|prepare|write|build|show)\b[^A-Z0-9]*", re.I)
    body = lead.sub("", first)
    body = re.sub(r"^(?:a|an|the)\s+(?:\w+\s+){0,4}?(?:of|on|for|between)\s+", "", body, flags=re.I)
    body = re.sub(r"^between\s+", "", body, flags=re.I)
    stop = re.search(r"\s(?:using|based\s+on|on\s+the\s+basis|in\s+terms\s+of|across|over|during|from|with\s+(?:the|their|latest)|for\s+(?:the|their|fy|q[1-4]|20\d\d)|on\s+(?:the|their)|by\s+(?:the|their))\b", body, re.I)
    if stop:
        body = body[:stop.start()]
    parts = re.split(r"\s*,\s*(?:and\s+|or\s+)?|\s+(?:and|or|vs\.?|versus|against)\s+|\s*/\s*|\s+&\s+", body)
    out: list[str] = []
    for part in parts:
        candidate = _clean_cell(part).strip(" .")
        words = candidate.split()
        # A name ends at its corporate suffix: "NTPC Limited renewable projects" → "NTPC Limited".
        for idx, word in enumerate(words[1:], 1):
            if word.casefold().strip(".,") in {"limited", "ltd", "inc", "corp", "corporation", "plc", "bank", "industries", "motors", "holdings"}:
                words = words[:idx + 1]
                break
        while words and words[-1].casefold().strip(".,") in _TRAILING_TOPIC_WORDS:
            words.pop()
        candidate = " ".join(words)
        if not candidate or len(candidate) > 60 or len(candidate.split()) > 5:
            continue
        if not re.match(r"^[A-Z0-9]", candidate):
            continue
        key = entity_key(candidate)
        if not key or key in _GENERIC_ENTITY_WORDS:
            continue
        if candidate not in out:
            out.append(candidate)
    return out if len(out) >= 2 else []


class EntityResolver:
    """Alias-aware matcher: 'State Bank of India' ≡ 'SBI', 'HDFC Bank Ltd' ≡ 'HDFC Bank'."""

    def __init__(self, targets: Iterable[str]):
        self.targets: list[str] = []
        self._target_keys: list[str] = []
        for name in targets:
            key = entity_key(name)
            if key and key not in self._target_keys:
                self.targets.append(_clean_cell(name))
                self._target_keys.append(key)
        self.display: dict[str, str] = {}
        for name, key in zip(self.targets, self._target_keys):
            self.display[key] = name
        self._prose_patterns: list[tuple[str, re.Pattern]] = []
        for name, key in zip(self.targets, self._target_keys):
            forms = {key}
            acronym = _acronym(key)
            if acronym and len(acronym) >= 2:
                forms.update({acronym, acronym + "s"})
            # Bank-group names appear in official publications as both full
            # labels and acronyms. Resolve those labels to one canonical group
            # instead of charting SCBs/PSBs/PVBs as separate entities.
            bank_aliases = {
                "scheduled commercial banks": {"scb", "scbs", "all scbs", "all scheduled commercial banks", "commercial banks", "commercial bank"},
                "public sector banks": {"psb", "psbs", "public sector bank"},
                "private sector banks": {"pvb", "pvbs", "private sector bank"},
                "foreign banks": {"fb", "fbs", "foreign bank"},
                "small finance banks": {"sfb", "sfbs", "small finance bank"},
                "payments banks": {"pb", "pbs", "payment banks", "payments bank"},
            }
            forms.update(bank_aliases.get(key, set()))
            words = key.split()
            if len(words) >= 2 and words[-1] in {"bank", "motors", "industries", "group", "company"}:
                core = " ".join(words[:-1])
                if len(core) >= 3 and core not in _GENERIC_ENTITY_WORDS:
                    forms.add(core)
            pattern = re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(
                re.escape(f).replace(r"\ ", r"[\s-]+") for f in sorted(forms, key=len, reverse=True)
            ) + r")(?:\s+(?:ltd\.?|limited))?(?![A-Za-z0-9])", re.I)
            self._prose_patterns.append((key, pattern))

    def resolve(self, name: str) -> tuple[str, str, bool]:
        """Return (canonical key, display name, is_target) for a table/prose label."""
        key = entity_key(name)
        if not key:
            return "", "", False
        bank_alias_to_canonical = {
            "scb": "scheduled commercial banks", "scbs": "scheduled commercial banks",
            "all scbs": "scheduled commercial banks", "all scheduled commercial banks": "scheduled commercial banks",
            "commercial bank": "scheduled commercial banks", "commercial banks": "scheduled commercial banks",
            "psb": "public sector banks", "psbs": "public sector banks", "public sector bank": "public sector banks",
            "pvb": "private sector banks", "pvbs": "private sector banks", "private sector bank": "private sector banks",
            "fb": "foreign banks", "fbs": "foreign banks", "foreign bank": "foreign banks",
            "sfb": "small finance banks", "sfbs": "small finance banks", "small finance bank": "small finance banks",
            "pb": "payments banks", "pbs": "payments banks", "payment banks": "payments banks", "payments bank": "payments banks",
        }
        canonical_alias = bank_alias_to_canonical.get(key)
        if canonical_alias and canonical_alias in self.display:
            return canonical_alias, self.display[canonical_alias], True
        for tkey in self._target_keys:
            if key == tkey:
                return tkey, self.display[tkey], True
            if _acronym(key) and _acronym(key) == tkey:
                return tkey, self.display[tkey], True
            if _acronym(tkey) and _acronym(tkey) == key:
                return tkey, self.display[tkey], True
            words, twords = key.split(), tkey.split()
            if len(twords) >= 2 and twords[-1] in {"bank", "motors", "industries", "group"} and key == " ".join(twords[:-1]):
                return tkey, self.display[tkey], True
            if len(words) > len(twords) and words[:len(twords)] == twords and all(w in {"bank", "ltd", "limited"} for w in words[len(twords):]):
                return tkey, self.display[tkey], True
        display = _clean_cell(name)
        display = _ENTITY_SUFFIX.sub("", display).strip() or display
        return key, display, False

    def mentions(self, text: str) -> list[tuple[int, str]]:
        found: list[tuple[int, str]] = []
        for key, pattern in self._prose_patterns:
            for match in pattern.finditer(text):
                found.append((match.start(), key))
        found.sort()
        return found


# ─────────────────────────────────────────────────────────────────────────────
# Periods / units
# ─────────────────────────────────────────────────────────────────────────────
def _fy_end_year(raw: str) -> int | None:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    if value < 100:
        value += 2000
    return value if 1950 <= value <= 2100 else None


def parse_period(text: str) -> tuple[str, float, str] | None:
    """Return (label, sort key, kind) for the first fiscal/calendar period in text."""
    month_label = _MONTH_LABEL_RE.fullmatch(str(text or ""))
    if month_label:
        return _month_period(month_label.group("mon"), int(month_label.group("year")), False)
    match = _PERIOD_RE.search(str(text or ""))
    if not match:
        return None
    g = match.groupdict()
    if g.get("q"):
        fy = _fy_end_year(g["qfy"])
        if fy is None:
            return None
        q = int(g["q"][1])
        return f"Q{q} FY{fy % 100:02d}", fy - 1 + q * 0.25 - 0.01, "Q"
    if g.get("h"):
        fy = _fy_end_year(g["hfy"])
        if fy is None:
            return None
        h = int(g["h"][1])
        return f"H{h} FY{fy % 100:02d}", fy - 1 + h * 0.5 - 0.02, "H"
    if g.get("nm"):
        fy = _fy_end_year(g["nmfy"])
        if fy is None:
            return None
        return f"9M FY{fy % 100:02d}", fy - 0.26, "9M"
    if g.get("fy"):
        first = _fy_end_year(g["fy"])
        second = _fy_end_year(g.get("fy2") or "") if g.get("fy2") else None
        if first is None:
            return None
        fy = second if second and second == first + 1 else first
        if g.get("fy2") and second is None:
            return None
        return f"FY{fy % 100:02d}", float(fy), "FY"
    if g.get("y1"):
        y1 = int(g["y1"])
        y2 = _fy_end_year(g["y2"])
        if y2 is not None and y2 == y1 + 1:
            return f"FY{y2 % 100:02d}", float(y2), "FY"
        return None
    if g.get("cy"):
        year = int(g["cy"])
        return str(year), year + 0.005, "CY"
    return None


def _quarter_from_phrase(match: re.Match, ref: date | None, year_hint: int | None) -> tuple[int, int] | None:
    end_name = (match.group("b") or match.group("end") or match.group("end2") or "").casefold()
    end_month = _MONTHS.get(end_name)
    if not end_month:
        return None
    q = {6: 1, 9: 2, 12: 3, 3: 4}[end_month]
    if year_hint:
        end_year = year_hint
    elif ref:
        end_year = ref.year if end_month <= ref.month else ref.year - 1
    else:
        return None
    fy = end_year + 1 if end_month in (6, 9, 12) else end_year
    return q, fy


def _shift_quarter(q: int, fy: int, delta: int) -> tuple[int, int]:
    index = fy * 4 + (q - 1) + delta
    return index % 4 + 1, index // 4


def _period_from_quarter(q: int, fy: int) -> tuple[str, float, str]:
    return f"Q{q} FY{fy % 100:02d}", fy - 1 + q * 0.25 - 0.01, "Q"


def period_date_range(period: str) -> tuple[str, str] | None:
    """ISO start/end dates for an Indian fiscal/calendar period label."""
    parsed = parse_period(period)
    if not parsed:
        return None
    label, _sort, kind = parsed
    if kind == "FY":
        fy = 2000 + int(label[2:])
        return f"{fy - 1}-04-01", f"{fy}-03-31"
    if kind == "Q":
        q, fy = int(label[1]), 2000 + int(label.split("FY")[1])
        starts = {1: (fy - 1, 4), 2: (fy - 1, 7), 3: (fy - 1, 10), 4: (fy, 1)}
        ends = {1: (fy - 1, 6, 30), 2: (fy - 1, 9, 30), 3: (fy - 1, 12, 31), 4: (fy, 3, 31)}
        sy, sm = starts[q]
        ey, em, ed = ends[q]
        return f"{sy}-{sm:02d}-01", f"{ey}-{em:02d}-{ed:02d}"
    if kind == "H":
        h, fy = int(label[1]), 2000 + int(label.split("FY")[1])
        return (f"{fy - 1}-04-01", f"{fy - 1}-09-30") if h == 1 else (f"{fy - 1}-10-01", f"{fy}-03-31")
    if kind == "9M":
        fy = 2000 + int(label.split("FY")[1])
        return f"{fy - 1}-04-01", f"{fy - 1}-12-31"
    if kind == "CY":
        return f"{label}-01-01", f"{label}-12-31"
    if kind == "M":
        month_label = _MONTH_LABEL_RE.fullmatch(label)
        if month_label:
            month = _MONTH_ABBR[month_label.group("mon")[:3].casefold()]
            year = int(month_label.group("year"))
            last = 31 if month in {1, 3, 5, 7, 8, 10, 12} else 30 if month != 2 else (29 if year % 4 == 0 else 28)
            return f"{year}-{month:02d}-01", f"{year}-{month:02d}-{last:02d}"
    return None


def _date_in_text(text: str) -> date | None:
    best = None
    for match in _DATE_IN_TEXT.finditer(str(text or "")[:4000]):
        month = _MONTH_ABBR.get((match.group("m1") or match.group("m2") or "")[:3].casefold())
        day = int(match.group("d1") or match.group("d2") or 1)
        year = int(match.group("y1") or match.group("y2"))
        try:
            found = date(year, month, min(day, 28))
        except (TypeError, ValueError):
            continue
        best = found if best is None or found > best else best
    return best


def _parse_ref_date(value: object) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")[:25]).date()
    except ValueError:
        pass
    match = re.match(r"(20\d{2})-(\d{2})-(\d{2})", text)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None
    return _date_in_text(text)


def _unit_family(unit_token: str, currency: str) -> tuple[str, float]:
    """Map a source unit token to (display unit, multiplier into that unit)."""
    u = re.sub(r"\s+", " ", (unit_token or "").casefold()).strip()
    cur = (currency or "").casefold().replace(".", "")
    if u in {"%", "percent", "per cent", "percent"} or u.startswith("per"):
        return "%", 1.0
    if u in {"bps", "basis point", "basis points"}:
        return "bps", 1.0
    if u in {"share", "shares"}:
        return "shares", 1.0
    if u.startswith("lakh share"):
        return "shares", 100000.0
    if u.startswith("crore share"):
        return "shares", 10000000.0
    if u.startswith("lakh cr"):
        return "₹ crore", 100000.0
    if u in {"crore", "crores", "cr"}:
        return "₹ crore", 1.0
    if u in {"lakh", "lakhs"}:
        return "₹ crore", 0.01
    if u in {"billion", "bn"}:
        if cur in {"₹", "rs", "inr"}:
            return "₹ crore", 100.0
        if cur in {"$", "us$", "usd"}:
            return "US$ million", 1000.0
        return "billion (as reported)", 1.0
    if u in {"million", "mn"}:
        if cur in {"₹", "rs", "inr"}:
            return "₹ crore", 0.1
        if cur in {"$", "us$", "usd"}:
            return "US$ million", 1.0
        return "million (as reported)", 1.0
    if u == "trillion":
        if cur in {"₹", "rs", "inr"}:
            return "₹ crore", 100000.0
        if cur in {"$", "us$", "usd"}:
            return "US$ million", 1_000_000.0
        return "trillion (as reported)", 1.0
    if u in {"x", "times"}:
        return "x", 1.0
    if cur in {"₹", "rs", "inr"}:
        return "₹", 1.0
    if cur in {"$", "us$", "usd"}:
        return "US$", 1.0
    return "", 1.0


def _to_float(raw: str) -> float | None:
    try:
        value = float(str(raw).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _metric_in(text: str) -> list[tuple[int, int, str]]:
    """Find metric mentions without conflating rate changes with deposit growth.

    RBI prose often says that the weighted-average domestic term-deposit rate
    changed while separately mentioning "fresh deposits" in the same sentence.
    The generic deposits pattern used to claim the rate percentage as deposit
    growth. Suppress that generic match when it is subordinate to a nearby,
    explicit rate metric; explicit "deposit growth" remains independently valid.
    """
    found: list[tuple[int, int, str]] = []
    occupied: list[tuple[int, int]] = []
    raw_matches: list[tuple[int, int, str]] = []
    for key, pattern in _METRIC_PATTERNS:
        for match in pattern.finditer(text):
            raw_matches.append((match.start(), match.end(), key))
    raw_matches.sort(key=lambda x: (x[0], -(x[1] - x[0])))
    rate_mentions = [m for m in raw_matches if m[2] in {"term_deposit_rate", "lending_rate", "repo_rate"}]
    for start, end, key in raw_matches:
        if key in {"deposits", "deposit_growth"}:
            explicit_growth = re.search(r"(?i)\bdeposit\s+growth\b|\bgrowth\s+in\s+deposits?\b", text[max(0, start - 45):min(len(text), end + 45)])
            if key == "deposits" and not explicit_growth and any(
                r_end <= start and start - r_end <= 110 for _r_start, r_end, _r_key in rate_mentions
            ):
                continue
        span = (start, end)
        if any(not (span[1] <= a or span[0] >= b) for a, b in occupied):
            continue
        occupied.append(span)
        found.append((start, end, key))
    found.sort()
    return found


_NON_METRIC_HEADER = re.compile(
    r"\b(?:regulation|exchange\s+broadcast|broadcast\s+date|transaction\s+date|establish(?:ed)?|founded|incorporat\w*|since|headquarters?|hq|website|url|refs?|references?|rank(?:ing)?|"
    r"s\.?\s?no|sr\.?\s?no|serial|sector\s+type|ownership|ticker|symbol|isin|ceo|md|chairman)\b", re.I)


def _metric_from_header(header: str) -> tuple[str, str, str]:
    """Return (metric key, label, kind) for a table header, falling back to the header text."""
    cleaned = _clean_cell(header)
    if _NON_METRIC_HEADER.search(cleaned):
        return "", "", ""
    # Separate shares-count from stake-percent columns so multi-column
    # exchange tables do not mark both as ambiguous duplicates and drop them.
    if re.search(r"\b(?:number\s+of\s+)?(?:equity\s+)?shares?\s+(?:acquired|disposed|sold|offloaded)", cleaned, re.I):
        if "%" in cleaned or re.search(r"\bpercent(?:age)?\b", cleaned, re.I):
            return "stake_percentage", "Stake sold / changed (%)", "ratio"
        return "shares_transacted", "Shares transacted", "count"
    hits = _metric_in(cleaned)
    if hits:
        key = hits[0][2]
        label, kind = _METRIC_BY_KEY[key]
        # "Total Assets Turnover ratio" / "Cash Deposit Ratio" are ratios, not
        # the money metric whose name they contain.
        if kind == "money" and re.search(r"\b(?:ratio|turnover|margin|yield|to\s+\w+\s+ratio|per\s+(?:share|employee|branch))\b", cleaned, re.I):
            pass
        else:
            return key, label, kind
    stripped = _PERIOD_RE.sub("", cleaned)
    stripped = re.sub(r"\((?:in\s+)?(?:₹|rs\.?|inr)?\s*(?:lakh\s+)?(?:crores?|cr|%|bn|mn|million|billion)?\)", "", stripped, flags=re.I)
    stripped = re.sub(r"\s+", " ", stripped).strip(" -–:()")
    if not stripped or len(stripped) > 48 or not re.search(r"[A-Za-z]{2,}", stripped):
        return "", "", ""
    slug = re.sub(r"[^a-z0-9]+", "_", stripped.casefold()).strip("_")[:40]
    return f"x_{slug}", stripped[:48], "any"


def _metric_variant(key: str, label: str, kind: str, unit: str, growth_context: bool) -> tuple[str, str] | None:
    """Resolve level vs growth vs ratio variants from the observed unit."""
    if kind == "npa":
        if unit == "%":
            return f"{key}_ratio", f"{label} ratio"
        if unit.startswith("₹") or unit == "":
            return f"{key}_amount", f"{label} (amount)"
        return None
    if kind == "money":
        if unit == "%":
            return (f"{key}_growth", f"{label} growth (YoY)") if growth_context else None
        if unit in {"x", "bps"}:
            return None
        return key, label
    if kind == "ratio":
        if unit in {"%", ""}:
            return key, label
        if unit == "bps":
            return f"{key}_change_bps", f"{label} change"
        return None
    if kind == "multiple":
        if unit in {"", "x"}:
            return key, label
        return None
    if kind == "price":
        if unit in {"", "₹", "US$"}:
            return key, label
        return None
    if kind == "count":
        # Count observations may be reported in millions/billions (especially
        # UPI). Preserve the stated unit rather than silently dropping them.
        return (key, label) if unit in {"", "count", "billion (as reported)", "million (as reported)"} else None
    return key, label


def _unit_for_metric(kind: str, unit: str) -> str:
    if kind == "multiple" and unit == "":
        return "x"
    if kind == "price" and unit == "":
        return "₹"
    if kind == "count" and unit == "":
        return "count"
    if kind == "ratio" and unit == "":
        return "%"
    return unit


# ─────────────────────────────────────────────────────────────────────────────
# Dataset
# ─────────────────────────────────────────────────────────────────────────────
class EvidenceDataset:
    def __init__(self, observations: list[Observation], targets: list[str], resolver: EntityResolver,
                 source_meta: dict[str, dict] | None = None):
        self.observations = observations
        self.targets = targets
        self.resolver = resolver
        self.source_meta = source_meta or {}

    def __len__(self) -> int:
        return len(self.observations)

    # Each (entity, metric, period) keeps every distinct reported value; the
    # representative value prefers table rows from fetched publisher pages.
    def grouped(self) -> dict[tuple[str, str, str], list[Observation]]:
        groups: dict[tuple[str, str, str], list[Observation]] = {}
        for obs in self.observations:
            groups.setdefault(obs.key, []).append(obs)
        return groups

    @staticmethod
    def _rank(obs: Observation) -> tuple:
        return (
            1 if obs.origin == "table" else 0,
            1 if "publisher" in obs.evidence_origin else 0,
            1 if obs.is_target else 0,
            -len(obs.quote),
        )

    def best(self) -> dict[tuple[str, str, str], Observation]:
        out = {}
        for key, items in self.grouped().items():
            clean = [o for o in items if not o.suspect]
            if clean:
                out[key] = max(clean, key=self._rank)
        return out

    def metrics(self) -> list[str]:
        return sorted({obs.metric for obs in self.observations})

    def metric_label(self, metric: str) -> str:
        for obs in self.observations:
            if obs.metric == metric:
                return obs.metric_label
        return metric

    def metric_unit(self, metric: str) -> str:
        units: dict[str, int] = {}
        for obs in self.observations:
            if obs.metric == metric:
                units[obs.unit] = units.get(obs.unit, 0) + 1
        return max(units.items(), key=lambda kv: kv[1])[0] if units else ""

    def entity_display(self, key: str) -> str:
        for obs in self.observations:
            if obs.entity_key == key:
                return obs.entity
        return key

    def periods(self, metric: str, unit: str | None = None) -> list[str]:
        values = {}
        for obs in self.observations:
            if obs.metric == metric and (unit is None or obs.unit == unit):
                values[obs.period] = obs.period_sort
        return [p for p, _ in sorted(values.items(), key=lambda kv: kv[1])]

    def cross_section(self, metric: str, *, period: str | None = None, targets_only: bool = False,
                      include_aggregates: bool = False) -> tuple[str, list[Observation]]:
        """Best single-period comparison across entities for one metric."""
        unit = self.metric_unit(metric)
        best = self.best()
        by_period: dict[str, list[Observation]] = {}
        for (ekey, mkey, per), obs in best.items():
            if mkey != metric or obs.unit != unit:
                continue
            if targets_only and not obs.is_target:
                continue
            if obs.is_aggregate and not include_aggregates:
                continue
            by_period.setdefault(per, []).append(obs)
        if not by_period:
            return "", []
        if period is not None:
            return period, by_period.get(period, [])
        chosen = max(by_period.items(), key=lambda kv: (
            sum(1 for o in kv[1] if o.is_target), len(kv[1]), kv[1][0].period_sort))
        return chosen[0], sorted(chosen[1], key=lambda o: (not o.is_target, -o.value))

    def time_series(self, metric: str) -> dict[str, list[Observation]]:
        """Per-entity ordered observations for one metric/unit/period-kind."""
        unit = self.metric_unit(metric)
        best = self.best()
        kinds: dict[str, int] = {}
        for obs in best.values():
            if obs.metric == metric and obs.unit == unit and obs.period:
                kinds[obs.period_kind] = kinds.get(obs.period_kind, 0) + 1
        if not kinds:
            return {}
        kind = max(kinds.items(), key=lambda kv: kv[1])[0]
        out: dict[str, list[Observation]] = {}
        for obs in best.values():
            if obs.metric == metric and obs.unit == unit and obs.period and obs.period_kind == kind and not obs.is_aggregate:
                out.setdefault(obs.entity_key, []).append(obs)
        return {k: sorted(v, key=lambda o: o.period_sort) for k, v in out.items()}

    def source_ids(self) -> list[str]:
        return sorted({o.source_id for o in self.observations}, key=lambda s: int(re.sub(r"\D", "", s) or 0))

    def evidence_lines_by_source(self) -> dict[str, list[str]]:
        lines: dict[str, list[str]] = {}
        for obs in self.observations:
            bucket = lines.setdefault(obs.source_id, [])
            for line in [*obs.context_lines, obs.quote]:
                line = str(line or "").strip()
                if line and line not in bucket:
                    bucket.append(line)
        return lines


# ─────────────────────────────────────────────────────────────────────────────
# Extraction
# ─────────────────────────────────────────────────────────────────────────────
def _split_cells(line: str) -> list[str]:
    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|"):
        text = text[:-1]
    return [c.strip() for c in text.split("|")]


def _is_separator(line: str) -> bool:
    return bool(re.fullmatch(r"\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)*\|?\s*", line or ""))


def _parse_value_cell(cell: str) -> tuple[float, str, str, str] | None:
    """Return (value, unit token, currency, raw text) for a numeric table cell."""
    text = _CITATION_RE.sub("", str(cell or ""))
    text = re.sub(r"`{1,3}|\*{1,3}|_{2}|<[^>]+>", "", text).replace("\u00a0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    if not text or text in {"—", "-", "–", "NA", "N/A", "n/a", "nil", "Nil"}:
        return None
    match = re.match(
        r"^[~≈]?\s*(?P<cur>₹|Rs\.?|INR|US\$|\$|USD)?\s*(?P<sign>[+\-−–])?\s*"
        r"(?P<num>\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*"
        r"(?P<unit>%|per\s?cent|lakh\s+crores?|lakh\s+cr|crores?|cr|lakhs?|bn|billion|mn|million|trillion|bps|x|times)?\.?"
        r"(?P<rest>.*)$", text, re.I)
    if not match:
        return None
    rest = match.group("rest").strip()
    # A currency conversion in brackets, e.g. "(US$170 billion)", is not a
    # second observation in this cell.
    rest_check = re.sub(r"\((?:approx\.?\s*)?(?:US\$|\$|USD|₹|Rs\.?|INR)[^)]*\)", "", rest).strip()
    # Allow short qualifiers such as "(GNPA level)" or "gain"/"loss"; reject sentences.
    if rest_check and (len(rest_check) > 30 or re.search(r"\d{3,}", rest_check)):
        return None
    value = _to_float(match.group("num"))
    if value is None:
        return None
    if match.group("sign") in {"-", "−", "–"}:
        value = -value
    if re.match(r"(?i)\s*(?:net\s+)?loss\b", rest_check):
        value = -abs(value)
    return value, match.group("unit") or "", match.group("cur") or "", text


def _unit_hint(text: str) -> tuple[str, str]:
    """(unit token, currency) hinted by a header or caption, e.g. '(Rs Cr)' or '(In Crores)'."""
    t = str(text or "")
    if re.search(r"\(\s*%\s*\)|\bin\s*%|\(\s*per\s?cent\s*\)|%\s*$", t, re.I):
        return "%", ""
    match = re.search(r"(?:\(|\bin\s+|\b)(?P<cur>₹|rs\.?|inr|us\$|usd|\$)?\s*(?P<u>lakh\s+crores?|lakh\s+cr|crores?|cr\b|lakhs?|bn\b|billion|mn\b|million)", t, re.I)
    if match:
        return match.group("u"), match.group("cur") or ("₹" if re.search(r"₹|\brs\b|\binr\b|crore|\bcr\b|lakh", t, re.I) else "")
    if re.search(r"\((?:rs\.?|₹|inr)\)", t, re.I):
        return "", "₹"
    return "", ""


# Row labels that are list items, buckets or line items rather than named entities.
_NON_ENTITY_ROW = re.compile(
    r"^\(?(?:[a-h]|[ivx]{1,4}|\d{1,2})[).]\s|\b\d+\s*(?:years?|months?|days?|quarters?)\b|^(?:up\s+to|over|above|below|less\s+than|more\s+than)\b|"
    r"^(?:of\s+which|memo|note|source)\b",
    re.I,
)


class _Extractor:
    def __init__(self, resolver: EntityResolver):
        self.resolver = resolver
        self.observations: list[Observation] = []

    def add(self, *, entity_name: str, metric_key: str, metric_label: str, kind: str, value: float,
            unit_token: str, currency: str, period: tuple[str, float, str] | None, source_id: str,
            quote: str, raw: str, origin: str, evidence_origin: str, growth_context: bool,
            context_lines: list[str] | None = None, entity_resolved: tuple[str, str, bool] | None = None) -> None:
        ekey, display, is_target = entity_resolved or self.resolver.resolve(entity_name)
        if not ekey or not display:
            return
        unit, scale = _unit_family(unit_token, currency)
        variant = _metric_variant(metric_key, metric_label, kind, unit, growth_context)
        if not variant:
            return
        key, label = variant
        unit = _unit_for_metric(_METRIC_BY_KEY.get(metric_key, ("", kind))[1] if metric_key in _METRIC_BY_KEY else kind, unit)
        scaled = value * scale
        bounded = {"gross_npa_ratio", "net_npa_ratio", "nim", "roa", "casa", "dividend_yield", "interest_spread",
                   "provision_coverage", "car", "roe", "net_profit_margin", "slippage_ratio"}
        if unit == "%" and key in bounded and not (0 <= abs(scaled) <= 100):
            return
        if unit == "%" and abs(scaled) > 1000:
            return
        if kind in {"money", "npa"} and unit.startswith("₹ crore") and scaled < 0 and "profit" not in key:
            return
        per_label, per_sort, per_kind = period or ("", 0.0, "")
        self._order = getattr(self, "_order", 0) + 1
        self.observations.append(Observation(order=self._order, 
            entity=display, entity_key=ekey, metric=key, metric_label=label,
            value=round(scaled, 6), unit=unit, period=per_label, period_sort=per_sort, period_kind=per_kind,
            source_id=source_id, quote=quote.strip()[:600], raw_value=raw[:80], origin=origin,
            evidence_origin=evidence_origin, is_target=is_target,
            is_aggregate=bool(_AGGREGATE_RE.search(display)) and not is_target,
            context_lines=[c.strip()[:300] for c in (context_lines or []) if c and c.strip()],
        ))

    # ── tables ─────────────────────────────────────────────────────────────
    def dominant_entity(self, text: str, minimum: int = 3) -> str | None:
        """Return the clearly dominant target group of a source, or None.

        Requires at least three target mentions and a 60% share, so a source
        that discusses several bank groups never gets a default entity.
        """
        cache = getattr(self, "_dominant_cache", None)
        if cache is None:
            cache = self._dominant_cache = {}
        key = (hash(str(text or "")[:60000]), minimum)
        if key in cache:
            return cache[key]
        mentions = [k for _pos, k in self.resolver.mentions(str(text or "")[:60000])]
        result = None
        if len(mentions) >= max(2, minimum):
            top = max(set(mentions), key=mentions.count)
            share = mentions.count(top) / len(mentions)
            # Two mentions are enough only when they are unanimous.
            if share >= (1.0 if len(mentions) < 3 else 0.6):
                result = top
        cache[key] = result
        return result

    def tables(self, text: str, source_id: str, evidence_origin: str, ref: date | None) -> None:
        self._table_source_entity = (
            (getattr(self, "source_entities", {}) or {}).get(source_id) or self.dominant_entity(text)
        ) if self.resolver.targets else None
        lines = str(text or "").replace("\r", "\n").split("\n")
        i = 0
        while i < len(lines):
            if lines[i].count("|") < 2:
                i += 1
                continue
            start = i
            block: list[str] = []
            while i < len(lines) and (
                lines[i].count("|") >= 2
                # A one-pipe section line inside a table ("1 | Balance Sheet
                # Operations#") must not split the table from its header.
                or (block and lines[i].count("|") == 1 and i + 1 < len(lines) and lines[i + 1].count("|") >= 2)
            ):
                block.append(lines[i])
                i += 1
            caption_lines = [ln.strip() for ln in lines[max(0, start - 3):start] if ln.strip()][-2:]
            self._table_block(block, caption_lines, source_id, evidence_origin, ref)

    @staticmethod
    def _matrix_period(cell: str) -> tuple[str, float, str] | None:
        """A column-header period; "Mar-2025" keeps its month (parse_period
        reduces it to the bare year, which merges Mar/Jun/Sep/Dec columns)."""
        match = re.fullmatch(r"([A-Za-z]{3,9})[\s\-/'’]+(\d{4}|\d{2})", cell.strip())
        if match and match.group(1)[:3].casefold() in _MONTH_ABBR:
            year = int(match.group(2)) + (2000 if len(match.group(2)) == 2 else 0)
            return _month_period(match.group(1), year, end_of_month=False)
        return parse_period(cell) if re.search(r"\d", cell) else None

    def _period_matrix(self, rows: list[list[str]], caption_lines: list[str], source_id: str,
                       evidence_origin: str) -> bool:
        """Indicator x period tables (RBI "Average for Mar-2025 | Jun-2025 | …"):
        one header row holds three or more distinct periods and each later row is
        a named series whose trailing numeric cells align with those periods.
        Rows are series (entities) of one table metric, plotted across periods."""
        for h in range(min(3, len(rows) - 1)):
            cells = [_clean_cell(c) for c in rows[h]]
            periods = [(i, self._matrix_period(c)) for i, c in enumerate(cells) if c]
            periods = [(i, p) for i, p in periods if p]
            labels = [c for c in cells if c]
            if (len(periods) < 3 or len(periods) < len(labels) - 2
                    or len({p[0] for _i, p in periods}) != len(periods)):
                continue
            header_text = " ".join(" | ".join(r) for r in rows[:h + 1])
            # Lines before the table that contain cells are header rows (or the
            # tail of a previous table), not a prose caption.
            caption = " ".join(line for line in caption_lines if "|" not in line)
            header_lines = [line for line in caption_lines[-1:] if "|" in line and not re.search(r"\d", line)]
            unit_tok, currency = _unit_hint(f"{caption} {header_text}")
            # Header words such as "Indicator | Average for" are table furniture,
            # not a metric name; keep only what remains after removing them.
            label_words = [c for c in cells if c and not self._matrix_period(c)]
            label_words += [_clean_cell(c) for line in header_lines for c in line.split("|") if c.strip()]
            furniture = r"\b(?:indicators?|items?|particulars?|variables?|average\s+for|as\s+on|as\s+at|period|month|year|s\.?\s?no)\b"
            remainder = re.sub(r"\s+", " ", re.sub(furniture, " ", " ".join(label_words), flags=re.I)).strip(" |-:")
            averaged = re.search(r"\baverage\b", " ".join(label_words), re.I)
            metric_text = (caption if re.search(r"[A-Za-z]{3,}", caption)
                           else remainder or ("Period average" if averaged else ""))
            metric_key, metric_label, kind = _metric_from_header(metric_text) if metric_text else ("", "", "")
            if not metric_key:
                metric_key, metric_label, kind = "x_reported_value", "Reported value", "any"
            context = [*caption_lines, *[" | ".join(r) for r in rows[:h + 1]]]
            emitted = 0
            for row in rows[h + 1:]:
                row_cells = [_clean_cell(c) for c in row]
                parsed_cells = [_parse_value_cell(c) if c else None for c in row_cells]
                tail: list[tuple[float, str, str, str]] = []
                for value in reversed(parsed_cells):
                    if value is None:
                        break
                    tail.append(value)
                tail.reverse()
                if len(tail) != len(periods):
                    continue
                names = [c for c, v in zip(row_cells, parsed_cells) if c and v is None]
                name = names[-1] if names else ""
                if not name or not re.search(r"[A-Za-z]{2,}", name) or _NON_ENTITY_ROW.search(name):
                    continue
                for (_col, period), (value, cell_unit, cell_cur, raw) in zip(periods, tail):
                    self.add(
                        entity_name=name, metric_key=metric_key, metric_label=metric_label, kind=kind,
                        value=value, unit_token=cell_unit or unit_tok, currency=cell_cur or currency,
                        period=period, source_id=source_id, quote=" | ".join(row), raw=raw,
                        origin="table", evidence_origin=evidence_origin, growth_context=False,
                        context_lines=context,
                    )
                    emitted += 1
            if emitted:
                return True
        return False

    def _table_block(self, block: list[str], caption_lines: list[str], source_id: str,
                     evidence_origin: str, ref: date | None) -> None:
        raw_table_rows = [line for line in block if not _is_separator(line)]
        rows = [_split_cells(line) for line in raw_table_rows]
        rows = [r for r in rows if any(c.strip() for c in r)]
        if len(rows) < 2:
            return
        caption = " ".join(caption_lines)
        header_lines = [block[0]]
        # A sector-index matrix stores one series per sector and calendar-quarter
        # return in each column (e.g. Q4 2024, Q1 2025). Treat those column
        # labels as periods, not as custom metric names; otherwise no numeric
        # observations are emitted and the line-chart builder sees empty axes.
        # The source row is retained as the quoted evidence; the column headers
        # are retained as context so every value is auditable against both.
        if len(rows) >= 2 and len(rows[0]) >= 4 and re.search(r"\b(?:sector|index)\b", _clean_cell(rows[0][0]), re.I):
            quarter_cols: list[tuple[int, str, tuple[str, float, str]]] = []
            for col, cell in enumerate(rows[0][1:], start=1):
                match = re.fullmatch(r"\s*Q([1-4])\s+(20\d{2})\s*", _clean_cell(cell), re.I)
                if not match:
                    continue
                quarter = int(match.group(1))
                year = int(match.group(2))
                label = f"Q{quarter} {year}"
                period = (label, float(year) + (quarter - 1) / 4.0, "CAL_Q")
                quarter_cols.append((col, label, period))
            if len(quarter_cols) >= 3:
                for row in rows[1:]:
                    if not row:
                        continue
                    sector = _clean_cell(row[0])
                    if (not sector or _parse_value_cell(sector) or _NON_ENTITY_ROW.search(sector)
                            or not re.search(r"[A-Za-z]{2,}", sector)):
                        continue
                    for col, _label, period in quarter_cols:
                        if col >= len(row):
                            continue
                        parsed = _parse_value_cell(row[col])
                        if not parsed:
                            continue
                        value, unit_tok, cur, raw = parsed
                        # Return matrices are comparable percentage observations.
                        # Require an explicit percent marker rather than assuming
                        # an unlabelled index level is a return.
                        header_unit, header_cur = _unit_hint(rows[0][col])
                        effective_unit = unit_tok or header_unit
                        if effective_unit != "%":
                            continue
                        self.add(
                            entity_name=sector, metric_key="sector_return", metric_label="Sector / index return",
                            kind="ratio", value=value, unit_token=effective_unit, currency=cur or header_cur,
                            period=period, source_id=source_id, quote=" | ".join(row), raw=raw,
                            origin="table", evidence_origin=evidence_origin, growth_context=False,
                            context_lines=[" | ".join(rows[0]), *caption_lines],
                        )
                # This matrix is fully represented by the custom path above;
                # do not reinterpret Q1/Q2/Q3/Q4 headers as generic metrics.
                return
        if self._period_matrix(rows, caption_lines, source_id, evidence_origin):
            return
        # A sparse first row like "| (In Crores) | | | |" is a caption, not a header.
        while len(rows) >= 3:
            populated = [c for c in rows[0] if c.strip()]
            if len(populated) <= max(1, len(rows[0]) // 3):
                caption = f"{caption} {' '.join(populated)}".strip()
                header_lines.append(" | ".join(rows[1]))
                rows = rows[1:]
                continue
            break
        header = [_clean_cell(c) for c in rows[0]]
        body = rows[1:]
        # Exchange bulk/block-deal tables commonly put ISIN in column 1 and
        # the issuer/security name in column 2. Treat the issuer as the entity,
        # not the ISIN identifier, and keep the remaining numeric columns aligned.
        if (len(header) >= 3
                and re.search(r"\bisin\b", header[0], re.I)
                and re.search(r"\b(?:company|security|issuer|scrip|name)\b", header[1], re.I)):
            header = [header[1]] + header[2:]
            body = [[row[1]] + row[2:] for row in body if len(row) >= 2]
        width = len(header)
        if width < 2:
            return
        caption_unit, caption_cur = _unit_hint(caption)
        caption_period = parse_period(caption)
        if caption_period and caption_period[2] not in {"FY", "Q", "H", "9M"}:
            caption_period = None
        context_lines = [*caption_lines, *[h for h in header_lines if h]]

        # RBI statistical tables frequently use two-tier headers: bank group in
        # the first header row (each group spans two columns), reporting year in
        # the next row, and a numeric column-index row before the observations.
        # Treating that first row as ordinary metric headers turned group names,
        # table indexes, PDF size markers and unrelated row labels into metrics.
        # Decode the group/year alignment explicitly before generic table logic.
        if len(header) >= 9 and len(body) >= 3:
            group_names: list[str] = []
            active_group = ""
            for col in range(1, len(header)):
                cell = _clean_cell(header[col])
                if cell:
                    active_group = cell
                group_names.append(active_group)
            year_row = body[0] if body else []
            numeric_index_row = body[1] if len(body) > 1 else []
            def looks_like_period(value: str) -> bool:
                value = _clean_cell(value)
                return bool(re.fullmatch(r"(?i)(?:(?:19|20)\d{2}|FY\s*['’]?\s*\d{2,4}(?:\s*[-–/]\s*\d{2,4})?|(?:19|20)\d{2}\s*[-–/]\s*(?:19|20)?\d{2})", value))
            period_columns = [col for col in range(1, min(len(header), len(year_row)))
                              if looks_like_period(year_row[col])]
            index_row_is_sequence = (
                len(numeric_index_row) >= len(period_columns)
                and len(numeric_index_row) >= 6
                and all(re.fullmatch(r"\d+", _clean_cell(v)) for v in numeric_index_row[:min(len(header), len(numeric_index_row))])
                and [int(_clean_cell(v)) for v in numeric_index_row[:min(len(header), len(numeric_index_row))]]
                    == list(range(1, min(len(header), len(numeric_index_row)) + 1))
            )
            period_row_valid = len(period_columns) >= 4
            # Header rows must contain real bank groups and repeated period
            # labels, not a normal entity-versus-metric table.
            groups = {entity_key(g) for g in group_names if g}
            known_bank_groups = {
                "public sector banks", "private sector banks", "foreign banks",
                "small finance banks", "payments banks", "scheduled commercial banks",
                "psbs", "pvbs", "fbs", "sfbs", "pbs", "scbs",
            }
            group_hit_count = sum(1 for g in group_names if entity_key(g) in known_bank_groups)
            if period_row_valid and group_hit_count >= 4:
                data_start = 2 if index_row_is_sequence else 1
                table_added = 0
                for body_index, row in enumerate(body[data_start:], start=data_start):
                    if not row or len(row) < 3:
                        continue
                    metric_header = _clean_cell(row[0])
                    if not metric_header or _NON_ENTITY_ROW.search(metric_header):
                        # Item labels such as "3. Deposits" are meaningful in
                        # bank statistical tables; the generic row filter rejects
                        # numbered labels, so peel only the leading item number.
                        metric_header = re.sub(r"^\s*\d+(?:\.\d+)*\.?\s*", "", metric_header).strip()
                    else:
                        metric_header = re.sub(r"^\s*\d+(?:\.\d+)*\.?\s*", "", metric_header).strip()
                    mkey, mlabel, kind = _metric_from_header(metric_header)
                    if not mkey or mkey.startswith("x_"):
                        continue
                    # Ignore percent-change rows that are expressed as brackets
                    # without an explicit label/unit; they are not independently
                    # typed observations unless their cells carry a % unit.
                    h_unit, h_cur = _unit_hint(metric_header)
                    row_added = 0
                    for col in range(1, min(len(row), len(header), len(year_row))):
                        group = group_names[col - 1] if col - 1 < len(group_names) else ""
                        if not group or entity_key(group) not in known_bank_groups:
                            continue
                        period_text = _clean_cell(year_row[col])
                        if not looks_like_period(period_text):
                            continue
                        period = parse_period(period_text)
                        if not period:
                            continue
                        parsed = _parse_value_cell(row[col])
                        if not parsed:
                            continue
                        value, unit_tok, cur, raw = parsed
                        # Units apply to the complete official table, not just
                        # to labels that happen to contain a currency marker.
                        effective_unit = unit_tok or h_unit or caption_unit
                        effective_cur = cur or h_cur or caption_cur
                        # Preserve the source's literal row, including dash cells.
                        # Rebuilding from cleaned cells used to erase '-' markers,
                        # making a valid row no longer an exact source substring.
                        # The old synthetic caption/group/period string was not a
                        # contiguous source excerpt and could also push the numeric
                        # cell beyond the chart-evidence length limit.
                        row_label_key = _clean_cell(row[0]).casefold() if row else ""
                        raw_row = next((line for line in raw_table_rows
                                        if _clean_cell(_split_cells(line)[0] if _split_cells(line) else "").casefold() == row_label_key), "")
                        quote = str(raw_row).strip() if raw_row else " | ".join(_clean_cell(cell) for cell in row)
                        observation_context = [*context_lines, " | ".join(_clean_cell(cell) for cell in year_row),
                                               f"{group} | {metric_header} | {period_text} | {raw}"]
                        self.add(entity_name=group, metric_key=mkey, metric_label=mlabel, kind=kind,
                                 value=value, unit_token=effective_unit, currency=effective_cur,
                                 period=period, source_id=source_id, quote=quote, raw=raw, origin="table",
                                 evidence_origin=evidence_origin,
                                 growth_context=bool(re.search(r"growth|change|yoy|y-o-y|rise|increase", metric_header, re.I)),
                                 context_lines=observation_context)
                        row_added += 1
                    table_added += row_added
                if table_added:
                    return

        if self._indicator_period_table(header, body, raw_table_rows, caption, caption_lines, header_lines,
                                        source_id, evidence_origin):
            return

        # Orientation: entities in header → transpose (metrics in rows).
        header_entities = sum(1 for h in header[1:] if self.resolver.resolve(h)[2])
        first_col = [_clean_cell(r[0]) for r in body if r]
        period_rows = sum(1 for c in first_col if parse_period(c) and len(c) <= 14)
        if header_entities >= 2:
            transposed = [[header[0]] + first_col]
            for col in range(1, width):
                transposed.append([header[col]] + [(r[col] if col < len(r) else "") for r in body])
            header, body = [_clean_cell(c) for c in transposed[0]], transposed[1:]
            width = len(header)
        elif period_rows >= max(2, int(len(first_col) * 0.6)):
            # Period-indexed table for one subject: subject comes from caption context.
            mention = self.resolver.mentions(caption)
            subject = self.resolver.display.get(mention[0][1]) if mention else ""
            if not subject:
                return
            for row in body:
                period = parse_period(_clean_cell(row[0]))
                for col in range(1, min(width, len(row))):
                    parsed = _parse_value_cell(row[col])
                    if not parsed:
                        continue
                    mkey, mlabel, kind = _metric_from_header(header[col])
                    if not mkey:
                        continue
                    value, unit_tok, cur, raw = parsed
                    h_unit, h_cur = _unit_hint(header[col])
                    self.add(entity_name=subject, metric_key=mkey, metric_label=mlabel, kind=kind, value=value,
                             unit_token=unit_tok or h_unit or caption_unit, currency=cur or h_cur or caption_cur,
                             period=period, source_id=source_id, quote=" | ".join(row), raw=raw, origin="table",
                             evidence_origin=evidence_origin, growth_context="growth" in header[col].casefold(),
                             context_lines=context_lines)
            return

        # Ambiguous mapping: several columns resolving to the same metric and
        # period (e.g. bank-group columns under a statistical table) cannot be
        # attributed to one observation, so those columns are skipped.
        col_keys: dict[tuple[str, str], list[int]] = {}
        for col in range(1, width):
            mkey, _l, kind = _metric_from_header(header[col])
            if mkey:
                per = parse_period(header[col]) or caption_period
                col_keys.setdefault((mkey, per[0] if per else ""), []).append(col)
        ambiguous_cols = {c for cols in col_keys.values() if len(cols) > 1 for c in cols}
        for row in body:
            if not row:
                continue
            label = _clean_cell(row[0])
            if not label or not re.search(r"[A-Za-z]{2,}", label) or len(label) > 70 or _parse_value_cell(label):
                continue
            if _NON_ENTITY_ROW.search(label):
                continue
            for col in range(1, min(width, len(row))):
                if col in ambiguous_cols:
                    continue
                parsed = _parse_value_cell(row[col])
                if not parsed:
                    continue
                mkey, mlabel, kind = _metric_from_header(header[col])
                if not mkey:
                    continue
                value, unit_tok, cur, raw = parsed
                h_unit, h_cur = _unit_hint(header[col])
                period = parse_period(header[col]) or caption_period
                if period is None:
                    row_date = _date_in_text(" | ".join(row))
                    if row_date:
                        period = (row_date.isoformat(), float(row_date.toordinal()), "DATE")
                cell_lower = raw.casefold()
                # Explicit qualifier inside the cell overrides the column metric,
                # e.g. "1.54% (GNPA level)" in an amount column → GNPA ratio.
                if kind == "npa" or "npa" in cell_lower:
                    mkey = "gross_npa" if "net" not in cell_lower and mkey != "net_npa" else mkey
                    if mkey in {"gross_npa", "net_npa"}:
                        kind = "npa"
                        mlabel = _METRIC_BY_KEY[mkey][0]
                self.add(entity_name=label, metric_key=mkey, metric_label=mlabel, kind=kind, value=value,
                         unit_token=unit_tok or h_unit or caption_unit, currency=cur or h_cur or caption_cur,
                         period=period, source_id=source_id, quote=" | ".join(row), raw=raw, origin="table",
                         evidence_origin=evidence_origin,
                         growth_context=bool(re.search(r"growth|change|yoy|y-o-y|rise|increase", header[col], re.I)),
                         context_lines=context_lines)

    def _indicator_period_table(self, header: list[str], body: list[list[str]], raw_table_rows: list[str],
                                caption: str, caption_lines: list[str], header_lines: list[str],
                                source_id: str, evidence_origin: str) -> bool:
        """Decode RBI "key indicator" tables: metric rows x period columns.

        Layout (Report on Trend and Progress, FSR annex tables)::

            Sr. No | Items | Amount Outstanding (At end-March) | Percentage Variation
            2024 | 2025 P | 2024 | 2025 P
            2.2 Return on Asset (RoA) (Per cent) * | 1.3 | 1.4
            1.4 Loans and advances | 1,71,42,309 | 1,91,19,608 | 19.7 | 11.5

        The header groups span the period row evenly. A row with as many values
        as the period row maps one-to-one; a row with only the first group's
        width (ratio rows) maps to the first group. Units are never assumed: a
        value is kept only when the row label declares per cent, or when it sits
        in a percentage-variation group. The entity is the bank group named in
        the caption, else the source's clearly dominant group; otherwise the
        table is skipped rather than guessed.
        """
        if not body or not any(re.search(r"(?i)\b(?:items?|particulars|indicators?)\b", h) for h in header):
            return False
        period_row = [_clean_cell(c) for c in body[0]]
        period_cells = [c for c in period_row if re.fullmatch(r"(?i)(?:19|20)\d{2}(?:\s*[-–/]\s*\d{2,4})?(?:\s*[A-Z*#@^]{1,2})?", c)]
        if len(period_cells) < 2 or len(period_cells) != len([c for c in period_row if c]):
            return False
        items_index = next(i for i, h in enumerate(header) if re.search(r"(?i)\b(?:items?|particulars|indicators?)\b", h))
        groups = [h for h in header[items_index + 1:] if h]
        if not groups or len(period_cells) % len(groups):
            return False
        span = len(period_cells) // len(groups)
        mention = self.resolver.mentions(caption)
        entity_key_value = mention[0][1] if mention else getattr(self, "_table_source_entity", None)
        if not entity_key_value:
            return False
        entity_name = self.resolver.display.get(entity_key_value, entity_key_value)
        end_march = bool(re.search(r"(?i)end[- ]march|\bmarch\b", " ".join(header) + " " + caption))

        def period_for(cell: str) -> tuple[str, float, str] | None:
            year_match = re.match(r"((?:19|20)\d{2})(?:\s*[-–/]\s*(\d{2,4}))?", cell)
            if not year_match:
                return None
            if year_match.group(2):
                return parse_period(year_match.group(0))
            year = int(year_match.group(1))
            return (f"FY{year % 100:02d}", float(year), "FY") if end_march else (str(year), year + 0.005, "CY")

        data_rows = body[1:]
        if data_rows and all(re.fullmatch(r"\d+", _clean_cell(c)) for c in data_rows[0] if _clean_cell(c)):
            data_rows = data_rows[1:]  # column index row "1 | 2 | 3 | 4 | 5 | 6"
        added = 0
        for row in data_rows:
            cells = [_clean_cell(c) for c in row]
            if len(cells) < 2:
                continue
            label = re.sub(r"^\s*\d+(?:\.\d+)*\.?\s*", "", cells[0]).strip(" *#@^")
            values = cells[1:]
            if not label or not re.search(r"[A-Za-z]{3,}", label) or _parse_value_cell(label):
                continue
            if len(values) not in {len(period_cells), span}:
                continue
            mkey, mlabel, kind = _metric_from_header(label)
            if not mkey or mkey.startswith("x_"):
                continue
            # Sub-measures must not be labelled as the headline metric:
            # "CRAR (tier I)" is not total CRAR; "Personal loans" is a segment,
            # not total advances.
            if mkey == "car" and re.search(r"(?i)\btier\s*(?:i{1,2}|1|2)\b|\bcet\s*-?\s*1\b|common\s+equity", label):
                continue
            if kind == "money" and _SEGMENT_QUALIFIER.search(label) and not re.search(r"(?i)\b(?:total|gross|loans\s+and\s+advances)\b", label):
                continue
            label_is_percent = bool(re.search(r"(?i)\bper\s?cent\b|\bpercent(?:age)?\b|%", label)) or bool(
                # RBI states these named ratios in per cent by convention.
                mkey in {"car", "cd_ratio", "provision_coverage", "slippage_ratio", "casa", "cost_income", "nim", "roa", "roe"}
                and re.search(r"(?i)\bratio\b|\(crar\)|\b(?:roa|roe|nim)\b", label)
            )
            raw_row = next((line for line in raw_table_rows if _clean_cell(_split_cells(line)[0] if _split_cells(line) else "") == cells[0]), "")
            quote = str(raw_row).strip() or " | ".join(cells)
            raw_values = list(row[1:])
            for j, cell in enumerate(values):
                # Parse the untouched cell: the cleaner strips leading dashes,
                # which turned "-15.9" growth into +15.9.
                parsed = _parse_value_cell(raw_values[j] if j < len(raw_values) else cell)
                if not parsed:
                    continue
                group = groups[j // span] if j // span < len(groups) else ""
                group_is_percent = bool(re.search(r"(?i)percent(?:age)?\s+(?:variation|change|growth)|\bvariation\b|\bgrowth\b", group))
                value, unit_tok, cur, raw = parsed
                if group_is_percent:
                    if kind == "npa":
                        # Growth of the NPA *amount* is not an NPA ratio.
                        continue
                    unit, growth = "%", True
                elif label_is_percent or unit_tok == "%":
                    unit, growth = "%", False
                else:
                    continue  # level amount without a stated unit: not charted
                period = period_for(period_cells[j])
                if not period:
                    continue
                self.add(entity_name=entity_name, metric_key=mkey, metric_label=mlabel, kind=kind, value=value,
                         unit_token=unit, currency=cur, period=period, source_id=source_id, quote=quote, raw=raw,
                         origin="table", evidence_origin=evidence_origin, growth_context=growth,
                         context_lines=[*caption_lines, *[h for h in header_lines if h], " | ".join(period_row),
                                        f"{entity_name} | {label} | {group} | {period_cells[j]} | {raw}"],
                         entity_resolved=(entity_key_value, entity_name, True))
                added += 1
        return added > 0

    # ── prose ──────────────────────────────────────────────────────────────
    def prose(self, text: str, source_id: str, evidence_origin: str, ref: date | None) -> None:
        if not self.resolver.targets:
            return
        # News/results articles normally report one period; carry the most
        # recent explicit period across paragraphs of the same source.
        source_period = None
        # Dominant target group of the whole source (e.g. SCBs in an RBI
        # banking release): needs at least three mentions and 60% of all
        # target mentions, otherwise no default entity is used.
        source_entity = (getattr(self, "source_entities", {}) or {}).get(source_id) or self.dominant_entity(text)
        for block in re.split(r"\n\s*\n|\n(?=#)", str(text or "")):
            if block.count("|") >= 4:
                continue
            state = {"entity": None, "period": source_period, "source_entity": source_entity}
            for raw_line in block.split("\n"):
                line = raw_line.strip()
                if not line:
                    continue
                if line.startswith("#"):
                    heads = self.resolver.mentions(line)
                    state["entity"] = heads[0][1] if len({k for _, k in heads}) == 1 else None
                    period = self._sentence_period(line, ref)
                    if period:
                        state["period"] = period
                    continue
                for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z(])", line):
                    self._sentence(sentence, source_id, evidence_origin, ref, state)
            source_period = state["period"] or source_period

    @staticmethod
    def _sentence_period(s: str, ref: date | None) -> tuple[str, float, str] | None:
        year_match = re.search(r"\b(20\d{2})\b", s)
        year_hint = int(year_match.group(1)) if year_match else None
        # "H1:2025-26" is a half year, not the full FY26.
        half = _HALF_RANGE_RE.search(s)
        explicit = parse_period(s)
        if half and (not explicit or half.start() <= s.find(str(half.group("y1")))):
            fy = _fy_end_year(half.group("y2"))
            if fy and fy == int(half.group("y1")) + 1:
                h = int(half.group("h")[1])
                return f"H{h} FY{fy % 100:02d}", fy - 1 + h * 0.5 - 0.02, "H"
        if explicit and explicit[2] in {"Q", "FY", "H", "9M"}:
            return explicit
        quarter_match = _QUARTER_PHRASE.search(s)
        if quarter_match:
            qfy = _quarter_from_phrase(quarter_match, ref, year_hint)
            if qfy:
                return _period_from_quarter(*qfy)
        # RBI dates stocks as "at end-March 2025" / "end-September 2025" /
        # "as on December 13, 2024". Without this every such observation had
        # no period and was discarded by the gallery's period gate.
        month_match = _MONTH_YEAR_RE.search(s)
        if month_match:
            return _month_period(month_match.group("mon"), int(month_match.group("year")), bool(month_match.group("end")))
        return None

    def _period_after(self, s: str, pos: int, ref: date | None) -> tuple[str, float, str] | None:
        """Return the period written immediately after a value ("2.1 per cent at end-September 2025").

        Only a period that starts within a short prepositional lead-in counts,
        so a value is never assigned a period from a different clause.
        """
        tail = s[pos:pos + 70]
        candidates = []
        for regex in (_HALF_RANGE_RE, _MONTH_YEAR_RE, _PERIOD_RE, _QUARTER_PHRASE):
            match = regex.search(tail)
            if match:
                candidates.append(match)
        if not candidates:
            return None
        match = min(candidates, key=lambda m: m.start())
        lead = tail[:match.start()]
        if match.start() > 26 or not _PERIOD_PREFIX_OK.fullmatch(lead):
            return None
        snippet = tail[max(0, match.start() - 4):match.end() + 2]
        return self._sentence_period(snippet, ref)

    def _emit(self, ent: str, mkey: str, value: float, unit: str, cur: str, period, source_id: str, quote: str,
              raw: str, evidence_origin: str, growth: bool) -> None:
        label, kind = _METRIC_BY_KEY[mkey]
        self.add(entity_name=self.resolver.display.get(ent, ent), metric_key=mkey, metric_label=label, kind=kind,
                 value=value, unit_token=unit, currency=cur, period=period, source_id=source_id, quote=quote,
                 raw=raw, origin="prose", evidence_origin=evidence_origin, growth_context=growth,
                 entity_resolved=(ent, self.resolver.display.get(ent, ent), ent in self.resolver.display))

    def _comparison_period(self, base_period, follow: str):
        if not base_period:
            return None
        if base_period[2] == "Q":
            q, fy = int(base_period[0][1]), 2000 + int(base_period[0].split("FY")[1])
            if _YEAR_AGO.search(follow):
                return _period_from_quarter(*_shift_quarter(q, fy, -4))
            if _PREV_QUARTER.search(follow):
                return _period_from_quarter(*_shift_quarter(q, fy, -1))
        elif base_period[2] == "FY" and (_YEAR_AGO.search(follow) or re.search(r"previous\s+(?:fiscal|financial)\s+year", follow, re.I)):
            fy = 2000 + int(base_period[0][2:])
            return f"FY{(fy - 1) % 100:02d}", float(fy - 1), "FY"
        return None

    def _sentence(self, sentence: str, source_id: str, evidence_origin: str, ref: date | None, state: dict) -> None:
        s = sentence.strip()
        mentions = self.resolver.mentions(s)
        unique = list(dict.fromkeys(k for _, k in mentions))
        own_period = self._sentence_period(s, ref)
        if own_period:
            state["period"] = own_period
        base_period = own_period or state["period"]
        context_entity = state.get("entity")
        if unique:
            state["entity"] = unique[-1] if len(unique) == 1 else None
        if len(s) < 12 or len(s) > 700 or not re.search(r"\d", s):
            return
        if re.search(r"\brespectively\b", s, re.I):
            return
        metrics = _metric_in(s)
        if not metrics:
            return
        # "provisioning coverage ratio (PCR)" / "gross non-performing assets
        # (GNPA) ratio" name one metric twice. Treat the acronym as part of the
        # first mention so the value after it is not outside the first window.
        collapsed: list[tuple[int, int, str]] = []
        for start, end, key in metrics:
            if collapsed and collapsed[-1][2] == key and re.fullmatch(r"[\s(]*", s[collapsed[-1][1]:start]):
                close = end + 1 if s[end:end + 1] == ")" else end
                collapsed[-1] = (collapsed[-1][0], close, key)
                continue
            collapsed.append((start, end, key))
        metrics = collapsed
        non_target_subject = bool(_NON_TARGET_GROUP_RE.search(s))
        segment_subject = bool(_SEGMENT_SUBJECT_RE.search(s))
        if not unique and non_target_subject:
            # The sentence is about NBFCs/UCBs/etc.; stop carrying the bank
            # group from earlier sentences into it or the next ones.
            state["entity"] = None
        source_entity = state.get("source_entity")

        def entity_at(pos: int, mkey: str = "", end: int | None = None) -> str | None:
            prior = [k for p, k in mentions if p <= pos]
            if prior:
                return prior[-1]
            # "The provisioning coverage ratio (PCR) of SCBs improved ... by
            # PSBs": the group directly governed by the metric ("of/for/among
            # SCBs") is the subject even when another group appears later.
            if end is not None:
                for p, k in mentions:
                    if p >= end and p - end <= 40 and re.fullmatch(
                        r"[\s)(]*(?:[A-Za-z]+\s+){0,2}?(?:of|for|among|across|in)\s+(?:the\s+|all\s+)?", s[end:p], re.I,
                    ):
                        return k
            if not mentions:
                if non_target_subject or segment_subject:
                    return None
                if context_entity:
                    return context_entity
                # A sentence in an RBI release about the banking system often
                # omits the group name ("Asset quality strengthened, with the
                # GNPA ratio ..."). Use the source's dominant bank group only for
                # system-level ratios and only when one group clearly dominates.
                if source_entity and mkey in _SYSTEM_LEVEL_METRICS:
                    return source_entity
                return None
            return mentions[0][1] if len(unique) == 1 else None

        numbers = []
        for match in _NUM_TOKEN.finditer(s):
            value = _to_float(match.group("num"))
            if value is None:
                continue
            unit = match.group("unit") or ""
            cur = match.group("cur") or ""
            before = s[max(0, match.start() - 4):match.start()]
            after = s[match.end():match.end() + 3]
            if not unit and not cur:
                if value.is_integer() and 1900 <= value <= 2100:
                    continue
                if re.search(r"(?:FY|Q[1-4]|H[12]|9M)\s*['’]?\s*$", before, re.I) or re.match(r"\s*(?:st|nd|rd|th)\b", after):
                    continue
            if re.match(r"\s*(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", s[match.end():match.end() + 10], re.I):
                continue
            if re.search(r"(?:Q|FY|H)$", s[max(0, match.start() - 2):match.start()], re.I):
                continue
            # The second half of a fiscal range ("2023-24") is a period label.
            if not unit and not cur and re.search(r"(?:19|20)\d{2}\s*[-–/]\s*$", s[max(0, match.start("num") - 8):match.start("num")]):
                continue
            sign = match.group("sign") or ""
            if sign and not re.search(r"[\s(]$|^$", s[max(0, match.start() - 1):match.start()]):
                sign = ""  # hyphen in a word like "Q2-FY26", not a sign
            numbers.append({"start": match.start(), "end": match.end(), "value": value, "unit": unit, "cur": cur,
                            "raw": match.group(0).strip(), "sign": sign})

        def is_pct(n: dict) -> bool:
            return n["unit"] == "%" or n["unit"].lower().startswith("per")

        used: set[int] = set()
        for index, (m_start, m_end, mkey) in enumerate(metrics):
            label, kind = _METRIC_BY_KEY[mkey]
            next_metric = metrics[index + 1][0] if index + 1 < len(metrics) else len(s)
            prev_metric_end = metrics[index - 1][1] if index > 0 else 0
            prev_text = s[max(prev_metric_end, m_start - 34):m_start]
            if kind == "money" and (_SEGMENT_QUALIFIER.search(prev_text) or re.search(r"\bexcl(?:uding|\.)?\b|\bex-", prev_text, re.I)):
                continue
            ent = entity_at(m_start, mkey, m_end)
            if not ent:
                continue
            window_cap = 160 if mkey in {"term_deposit_rate", "lending_rate", "repo_rate"} else 95
            window = [n for n in numbers if m_end <= n["start"] <= min(next_metric, m_end + window_cap) and n["start"] not in used]
            # "a 4.7% rise in net profit" — percentage before the metric.
            pre = None
            for n in reversed(numbers):
                if n["end"] > m_start or m_start - n["end"] > 42 or n["start"] in used or n["start"] < prev_metric_end:
                    continue
                gap = s[n["end"]:m_start]
                if is_pct(n) and _GROWTH_WORDS.search(gap) and re.search(r"\bin\b", gap, re.I) and not _SEGMENT_QUALIFIER.search(gap) \
                        and not re.search(r"\bexcl", gap, re.I):
                    pre = n
                break
            emitted_growth = False
            if pre and kind == "money":
                value = -abs(pre["value"]) if (_DECLINE_WORDS.search(s[pre["end"]:m_start]) or pre["sign"] in {"-", "−", "–"}) else pre["value"]
                used.add(pre["start"])
                self._emit(ent, mkey, value, pre["unit"], pre["cur"], base_period, source_id, s, pre["raw"], evidence_origin, True)
                emitted_growth = True
            level = None
            for n in window:
                gap = s[m_end:n["start"]]
                if re.search(r"\bexcl(?:uding|\.)?\b", gap, re.I) and kind == "money":
                    break
                if is_pct(n):
                    if mkey in {"term_deposit_rate", "lending_rate", "repo_rate"}:
                        rate_gap = s[m_end:n["start"]]
                        # A rate percentage should have an explicit grammatical
                        # link to the named rate; do not adopt the next unrelated
                        # percentage elsewhere in the same sentence.
                        if not re.search(r"(?i)\b(?:at|of|to|was|is|stood|stands|remained|held|cut|reduced|lowered|increased|declined|set)\b", rate_gap):
                            continue
                        if len(rate_gap) > 90 or re.search(r"(?i)\b(?:growth|deposits? increased|loans? grew|profit|npa)\b", rate_gap):
                            continue
                    if kind == "money":
                        if emitted_growth or not _GROWTH_WORDS.search(gap + " " + s[n["end"]:n["end"] + 20]):
                            continue
                        value = -abs(n["value"]) if (_DECLINE_WORDS.search(gap) or n["sign"] in {"-", "−", "–"}) else n["value"]
                        used.add(n["start"])
                        self._emit(ent, mkey, value, n["unit"], n["cur"], self._period_after(s, n["end"], ref) or base_period,
                                   source_id, s, n["raw"], evidence_origin, True)
                        emitted_growth = True
                        continue
                    if kind in {"ratio", "npa"}:
                        level = n
                        break
                    continue
                # Ratios must be reported as percentages (or an explicit rate
                # unit); never infer "1%" from a footnote/chart counter such as
                # Chart IV.1b or from a year/month embedded in a sentence.
                if kind in {"ratio", "npa"} and mkey in {
                    "credit_growth", "deposit_growth", "term_deposit_rate", "lending_rate", "repo_rate",
                    "gross_npa", "net_npa", "provision_coverage", "cd_ratio", "cost_income",
                    "roe", "roa", "nim", "casa", "car", "interest_spread", "net_profit_margin", "slippage_ratio",
                }:
                    continue
                if kind in {"money", "npa"} and not (n["unit"] or n["cur"]):
                    continue
                if kind == "ratio" and not n["unit"] and not re.search(r"\b(?:at|of|to|stood|was|is)\b", gap, re.I):
                    continue
                if kind == "multiple" and n["cur"]:
                    continue
                level = n
                break
            if not level:
                continue
            used.add(level["start"])
            value = level["value"]
            if level["sign"] in {"-", "−", "–"}:
                value = -value
            level_period = self._period_after(s, level["end"], ref) or base_period
            self._emit(ent, mkey, value, level["unit"], level["cur"], level_period, source_id, s, level["raw"], evidence_origin, False)
            # Follow-on values of the same metric that carry their own explicit
            # period: "2.2 per cent at end-March 2025 and 2.1 per cent at
            # end-September 2025", "from 14.3 per cent at end-March 2019 to 14.7
            # per cent at end-March 2020", "16.0 per cent in 2023-24 ... on top of
            # 17.0 per cent in 2022-23". A value without its own period is left
            # to the relative-comparison logic below; nothing is inferred.
            follow_cursor = level["end"]
            follow_count = 0
            for n in numbers:
                if n["start"] < follow_cursor or n["start"] in used:
                    continue
                if n["start"] >= next_metric or follow_count >= 4:
                    break
                if is_pct(n) != is_pct(level) or (n["cur"] and not level["cur"]):
                    break
                follow_gap = s[follow_cursor:n["start"]]
                if len(follow_gap) > 90 or not re.search(
                    r"(?i)(?:\band|,|\bto|\bfrom|\bwhile|\bversus|\bvs\.?|\bagainst|\bcompared\s+(?:with|to)|\bon\s+top\s+of|\bbefore|\bafter)"
                    r"\s*(?:further\s+)?(?:to\s+)?(?:a\s+)?(?:(?:multi-decadal\s+|record\s+)?(?:low|high)\s+of\s+)?$",
                    follow_gap,
                ):
                    break
                if kind == "money" and re.search(r"(?i)\bexcl", follow_gap):
                    break
                follow_period = self._period_after(s, n["end"], ref)
                if not follow_period or follow_period == level_period:
                    break
                follow_value = -n["value"] if n["sign"] in {"-", "−", "–"} else n["value"]
                used.add(n["start"])
                self._emit(ent, mkey, follow_value, n["unit"] or level["unit"], n["cur"] or level["cur"], follow_period,
                           source_id, s, n["raw"], evidence_origin, False)
                follow_cursor = n["end"]
                follow_count += 1
            # Comparative figures in the same clause: "… from 1.24% a year earlier
            # and 1.17% in the preceding quarter", "as against Rs 18,641 crore a year ago".
            tail_start = level["end"]
            tail = s[tail_start:min(len(s), tail_start + 150)]
            comparator = re.match(r".{0,40}?\b(?:from|against|versus|vs\.?|compared\s+(?:with|to))\b", tail, re.I)
            if not comparator or not base_period:
                continue
            for n in numbers:
                if n["start"] in used or n["start"] < tail_start + comparator.end() or n["start"] > tail_start + comparator.end() + 30:
                    continue
                if is_pct(level) != is_pct(n):
                    break
                follow = s[n["end"]:n["end"] + 60]
                shifted = self._comparison_period(base_period, follow)
                if not shifted:
                    break
                used.add(n["start"])
                self._emit(ent, mkey, n["value"], n["unit"] or level["unit"], n["cur"] or level["cur"], shifted,
                           source_id, s, n["raw"], evidence_origin, False)
                more = re.match(r".{0,24}?\band\s+(?:₹|Rs\.?\s*)?(\d[\d,]*(?:\.\d+)?)\s*(%|crores?|cr)?\s+(?:in|at\s+the\s+end\s+of)\s+the\s+(?:preceding|previous|prior|sequential)\s+quarter",
                                s[n["end"]:n["end"] + 90], re.I)
                if more and base_period[2] == "Q":
                    q, fy = int(base_period[0][1]), 2000 + int(base_period[0].split("FY")[1])
                    val = _to_float(more.group(1))
                    if val is not None:
                        self._emit(ent, mkey, val, more.group(2) or level["unit"], level["cur"],
                                   _period_from_quarter(*_shift_quarter(q, fy, -1)), source_id, s, more.group(0),
                                   evidence_origin, False)
                break

def _dedupe(observations: list[Observation]) -> list[Observation]:
    seen: set[tuple] = set()
    out: list[Observation] = []
    for obs in observations:
        sig = (obs.entity_key, obs.metric, obs.period, round(obs.value, 4), obs.unit, obs.source_id)
        if sig in seen:
            continue
        seen.add(sig)
        out.append(obs)
    return out


def _merge_entity_variants(observations: list[Observation]) -> None:
    """Unify spelling variants of the same non-target entity across sources.

    "Kotak Mahindra" / "Kotak Mahindra Bank", "YesBank" / "Yes Bank",
    "Bank Of Baroda" / "Bank of Baroda" → one key and display name.
    """
    def squash(key: str) -> str:
        key = re.sub(r"\s+", "", key)
        return key[:-4] if key.endswith("bank") and len(key) > 6 else key
    groups: dict[str, list[Observation]] = {}
    for obs in observations:
        groups.setdefault(squash(obs.entity_key), []).append(obs)
    for items in groups.values():
        target = next((o for o in items if o.is_target), None)
        if target is None:
            displays: dict[str, int] = {}
            for o in items:
                displays[o.entity] = displays.get(o.entity, 0) + 1
            # Most frequent spelling; prefer "Bank of Baroda" over "Bank Of Baroda".
            display = max(displays, key=lambda d: (displays[d], " " in d, sum(1 for w in d.split() if w.islower())))
            key = max((o.entity_key for o in items), key=len)
        else:
            display, key = target.entity, target.entity_key
        for obs in items:
            obs.entity_key, obs.entity = key, display
            obs.is_target = obs.is_target or target is not None


def _flag_suspect_duplicates(observations: list[Observation]) -> None:
    """Flag a later table row that repeats another entity's exact large value.

    Example: a source table listing SBI's total assets as 1892697.09 — the
    exact figure on ICICI Bank's row above it. The first occurrence is kept;
    the copy is retained in tables with a note but excluded from charts.
    """
    seen: dict[tuple, Observation] = {}
    for obs in sorted(observations, key=lambda o: o.order):
        if obs.origin != "table":
            continue
        digits = len(re.sub(r"\D", "", f"{abs(obs.value):.2f}").lstrip("0"))
        if digits < 6:
            continue
        sig = (obs.source_id, obs.metric, obs.period, round(obs.value, 2))
        prior = seen.get(sig)
        if prior is None:
            seen[sig] = obs
        elif prior.entity_key != obs.entity_key:
            obs.suspect = f"identical to {prior.entity}'s value in the same source table (probable source error)"


def build_evidence_dataset(sources: list[dict], question: str, targets: list[str] | None = None) -> EvidenceDataset:
    """Extract observations from source dicts.

    Each source dict: {"id": "S1", "text": str, "origin": "publisher_page"|"search_extract"|...,
    "published": iso-date?, "title": str?}
    """
    requested = list(targets or []) or extract_requested_entities(question)
    resolver = EntityResolver(requested)
    extractor = _Extractor(resolver)
    meta: dict[str, dict] = {}
    # The same source ID can arrive as a fetched publisher page and a separate
    # search extract. A table page often omits the bank-group name that its
    # extract/title states, so the dominant group is decided across all text
    # for that ID (it is still only used when one group clearly dominates).
    combined_by_id: dict[str, str] = {}
    for source in sources or []:
        if isinstance(source, dict):
            sid = str(source.get("id") or "").strip().upper()
            if sid:
                combined_by_id[sid] = combined_by_id.get(sid, "") + "\n" + str(source.get("title") or "") + "\n" + str(source.get("text") or "")[:30000]
    extractor.source_entities = {sid: extractor.dominant_entity(text, minimum=2) for sid, text in combined_by_id.items()} if resolver.targets else {}
    for source in sources or []:
        if not isinstance(source, dict):
            continue
        sid = str(source.get("id") or "").strip().upper()
        text = str(source.get("text") or "")
        if not sid or not text.strip():
            continue
        origin = str(source.get("origin") or "")
        ref = _parse_ref_date(source.get("published")) or _date_in_text(text)
        meta[sid] = {"title": source.get("title") or "", "published": ref.isoformat() if ref else "",
                     "origin": origin, "url": source.get("url") or ""}
        try:
            extractor.tables(text, sid, origin, ref)
            extractor.prose(text, sid, origin, ref)
        except Exception:  # pragma: no cover - extraction is best effort per source
            continue
    _merge_entity_variants(extractor.observations)
    observations = _dedupe(extractor.observations)
    # For promoter-sale/pledge research, do not count unrelated institutional
    # or non-promoter rows from exchange-wide bulk-deal tables as promoter
    # activity. Preserve rows whose role is not disclosed, but exclude explicit
    # Non-Promoter rows.
    if re.search(r"\b(?:promoter|promoters|pledge|pledging|stake\s+sale)\b", str(question or ""), re.I):
        observations = [
            obs for obs in observations
            if not re.search(r"\bnon[- ]promoter\b", obs.quote, re.I)
        ]
    _flag_suspect_duplicates(observations)
    # When the request named no entities, promote the most frequently observed
    # table row labels so cross-sectional charts still have a subject set.
    if not resolver.targets and observations:
        counts: dict[str, int] = {}
        for obs in observations:
            if not obs.is_aggregate:
                counts[obs.entity_key] = counts.get(obs.entity_key, 0) + 1
        top = [k for k, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:8]]
        for obs in observations:
            obs.is_target = obs.entity_key in top
    return EvidenceDataset(observations, resolver.targets, resolver, meta)
