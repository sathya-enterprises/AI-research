"""Evidence-first chart selection.

Until now charts were *proposed* (by the writer or by a keyword list in
``chart_gallery.gallery_types_for``) and then *pruned* by audits when the
evidence could not support them. That order wastes the report: the chart the
reader needs most (e.g. an 8-quarter sector matrix) is dropped late, and nothing
goes back to fetch the data that was missing.

This module reverses the order. From the question it decides which charts the
report should contain, checks the retrieved sources for the figures each one
needs, asks for targeted searches when a required dataset is absent, and gives
the writer a plan bound to the canonical source IDs it may cite.

It is deliberately deterministic and cheap (regexes over source text, no LLM, no
network); the only network step - the gap fetch - is performed by the caller
with the queries produced here, so the same helpers work for the main report
pipeline and for tests.
"""
from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from utils.source_ids import normalize_sid, sid_number

_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*"
_PERIOD_RE = re.compile(
    rf"(?i)\b(?:Q[1-4]\s*(?:FY\s*)?['’]?\d{{2,4}}|(?:FY|CY)\s*['’]?\d{{2,4}}|H[12]\s*(?:FY\s*)?\d{{2,4}}|"
    rf"{_MONTH}[\s,.'’-]*(?:20)?\d{{2}}|20\d{{2}}(?:[-–/]\d{{2,4}})?)\b"
)
_NUMBER_RE = re.compile(r"\d")
_MONTH_NUMBER = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _period_key(raw: object) -> str:
    """Normalize equivalent date labels before counting distinct periods."""
    text = re.sub(r"\s+", " ", str(raw or "").strip()).replace("’", "'")
    quarter = re.fullmatch(r"Q([1-4])\s*(?:(FY|CY)\s*)?'?(\d{2,4})", text, re.I)
    if quarter:
        year = int(quarter.group(3))
        year = 2000 + year if year < 100 else year
        return f"{(quarter.group(2) or 'CY').upper()}-Q{quarter.group(1)}-{year}"
    half = re.fullmatch(r"H([12])\s*(?:(FY|CY)\s*)?'?(\d{2,4})", text, re.I)
    if half:
        year = int(half.group(3))
        year = 2000 + year if year < 100 else year
        return f"{(half.group(2) or 'CY').upper()}-H{half.group(1)}-{year}"
    year_range = re.fullmatch(r"((?:19|20)\d{2})\s*[-–/]\s*((?:19|20)?\d{2})", text)
    if year_range:
        first = int(year_range.group(1))
        last_raw = year_range.group(2)
        last = int(last_raw) if len(last_raw) == 4 else (first // 100) * 100 + int(last_raw)
        return f"Y-{first}-{last}"
    fiscal = re.fullmatch(r"FY\s*'?(\d{2,4})", text, re.I)
    if fiscal:
        year = int(fiscal.group(1))
        year = 2000 + year if year < 100 else year
        return f"FY-{year}"
    calendar = re.fullmatch(r"CY\s*'?(\d{2,4})", text, re.I)
    if calendar:
        year = int(calendar.group(1))
        year = 2000 + year if year < 100 else year
        return f"CY-{year}"
    month_year = re.fullmatch(r"([A-Za-z]+)[\s,.'-]*(?:20)?(\d{2})", text)
    if month_year:
        month = _MONTH_NUMBER.get(month_year.group(1).casefold()[:4]) or _MONTH_NUMBER.get(month_year.group(1).casefold()[:3])
        if month:
            return f"M-{2000 + int(month_year.group(2)):04d}-{month:02d}"
    year = re.fullmatch(r"(?:19|20)\d{2}", text)
    if year:
        return f"Y-{text}"
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


@dataclass(frozen=True)
class ChartNeed:
    key: str
    title: str
    chart_types: tuple[str, ...]
    terms: tuple[str, ...]                    # a figure line must match ANY of these
    entity_terms: tuple[str, ...] = ()        # distinct matches = distinct entities covered
    min_lines: int = 3
    min_periods: int = 0
    min_entities: int = 0
    hosts: tuple[str, ...] = ()               # official publishers (lower the evidence bar)
    queries: tuple[str, ...] = ()
    fetcher: str = ""                         # named structured fetcher the caller may run
    required: bool = True


@dataclass
class NeedCoverage:
    need: ChartNeed
    status: str = "missing"                   # satisfied | partial | missing
    lines: int = 0
    periods: int = 0
    entities: int = 0
    source_ids: list[str] = field(default_factory=list)
    reason: str = ""

    def as_dict(self) -> dict:
        return {
            "key": self.need.key, "title": self.need.title, "status": self.status,
            "chartTypes": list(self.need.chart_types), "lines": self.lines, "periods": self.periods,
            "entities": self.entities, "sourceIds": list(self.source_ids), "reason": self.reason,
            "fetcher": self.need.fetcher,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Catalogue
# ─────────────────────────────────────────────────────────────────────────────
_SECTOR_NAMES = (
    r"nifty\s+(?:bank|it|pharma|fmcg|auto|metal|realty|energy|media|psu\s+bank|private\s+bank|"
    r"financial\s+services|fin\s+service|healthcare|oil\s*(?:&|and)\s*gas|infra\w*|consumer\s+durables|commodities)|"
    r"\b(?:banking|pharma(?:ceuticals?)?|it\s+services|fmcg|auto(?:mobile)?|metals?|realty|real\s+estate|energy|"
    r"capital\s+goods|power|telecom|healthcare|consumer\s+durables|oil\s*(?:&|and)\s*gas|chemicals?|cement|"
    r"financial\s+services|infrastructure|psu\s+banks?)\b"
)
_YEAR = _dt.date.today().year

_SECTOR_QUARTERLY = ChartNeed(
    key="sector_quarterly_returns", title="Sector index returns by completed quarter",
    chart_types=("heatmap", "line", "bar", "grouped_bar"),
    terms=(r"(?:return|performance|gain|fell|rose|change|%)",),
    entity_terms=(_SECTOR_NAMES,), min_lines=6, min_periods=4, min_entities=5,
    hosts=("niftyindices.com", "nseindia.com", "nseindia.in"),
    queries=("NSE sectoral indices quarterly returns last 8 quarters Nifty Bank IT Pharma FMCG Auto Metal Realty",
             "niftyindices.com sectoral index performance quarter-wise returns"),
    fetcher="sector_quarterly_returns",
)
_FPI_SECTOR = ChartNeed(
    key="fpi_sector_flows", title="FPI net investment by sector",
    chart_types=("horizontal_bar", "bar", "stacked_bar"),
    terms=(
        r"\b(?:fpi|fii|foreign\s+(?:portfolio|institutional)\s+investors?)\b.{0,140}"
        r"\b(?:net\s+)?(?:equity\s+)?(?:investment|investments|flows?|inflows?|outflows?|buying|selling|purchases?)\b",
        r"\b(?:net\s+)?(?:equity\s+)?(?:investment|investments|flows?|inflows?|outflows?|buying|selling|purchases?)\b.{0,140}"
        r"\b(?:fpi|fii|foreign\s+(?:portfolio|institutional)\s+investors?)\b",
    ),
    entity_terms=(_SECTOR_NAMES,), min_lines=4, min_periods=2, min_entities=4,
    hosts=("nsdl.co.in", "sebi.gov.in", "nseindia.com"),
    queries=("NSDL FPI sector-wise net investment equity crore latest",
             "FPI sector-wise assets under custody and net investment India NSDL fortnightly"),
)
_FPI_FLOW_SERIES = ChartNeed(
    key="fpi_net_flow_series", title="FPI / DII net flows over time",
    chart_types=("line", "bar", "area", "combo"),
    terms=(r"\bnet\s+(?:buy|sell|sold|bought|invest\w*|outflow|inflow|purchase)\w*", r"\b(?:fpi|fii|dii)s?\b.{0,40}(?:outflow|inflow)"),
    entity_terms=(r"\bfpi|fii\b", r"\bdii\b"), min_lines=4, min_periods=4, min_entities=2,
    hosts=("nsdl.co.in", "sebi.gov.in", "nseindia.com"),
    queries=(f"FPI DII monthly net equity flows India {_YEAR} crore NSDL NSE provisional data",
             f"foreign portfolio investors net selling buying India {_YEAR - 1} {_YEAR} month-wise"),
)
_SECTOR_VALUATION = ChartNeed(
    key="sector_valuation", title="Sector valuation (P/E) comparison",
    chart_types=("bar", "scatter", "horizontal_bar"),
    terms=(r"\b(?:p/?e|price[- ]to[- ]earnings|pb|p/b|valuation|earnings\s+yield)\b",),
    entity_terms=(_SECTOR_NAMES,), min_lines=4, min_entities=4, required=False,
    hosts=("niftyindices.com", "nseindia.com"),
    queries=("Nifty sectoral indices P/E P/B dividend yield factsheet",),
)
_PROMOTER_SALES = ChartNeed(
    key="promoter_sales_series", title="Promoter stake sales by quarter",
    chart_types=("bar", "line", "stacked_bar"),
    terms=(r"\bpromoter\w*\b.{0,80}(?:sold|sale|sell|stake|reduc|offload|exit|dilut)\w*",
           r"(?:sold|sale|offload|exit|dilut)\w*.{0,80}\bpromoter\w*"),
    entity_terms=(r"\b[A-Z][A-Za-z&.\- ]{2,40}\s+(?:Ltd|Limited|Industries|Bank|Technologies|Pharma|Motors)\b",),
    min_lines=4, min_periods=3, min_entities=3,
    hosts=("bseindia.com", "nseindia.com", "sebi.gov.in"),
    queries=(f"promoter stake sale bulk block deal India {_YEAR} list of promoter exits quarter",
             "promoter holding decrease shareholding pattern quarter NSE BSE"),
)
_PROMOTER_HOLDING = ChartNeed(
    key="promoter_holding_change", title="Promoter holding change by company",
    chart_types=("horizontal_bar", "bar", "grouped_bar"),
    terms=(r"\bpromoter\w*\s+(?:holding|shareholding|stake)\b", r"\bpledg\w+\b"),
    entity_terms=(r"\b[A-Z][A-Za-z&.\- ]{2,40}\s+(?:Ltd|Limited|Industries|Bank|Technologies|Pharma|Motors)\b",),
    min_lines=4, min_entities=3, required=False,
    hosts=("bseindia.com", "nseindia.com"),
    queries=("promoter shareholding pattern change percent quarter largest decrease",),
)
_FTA_TRADE = ChartNeed(
    key="fta_trade_flows", title="Trade with FTA partners before and after the agreement",
    chart_types=("grouped_bar", "bar", "line"),
    terms=(r"\b(?:exports?|imports?|trade)\b.{0,60}(?:billion|bn|crore|%|\$)",),
    entity_terms=(r"\b(?:UAE|Australia|EFTA|Switzerland|Norway|Iceland|Liechtenstein|UK|United\s+Kingdom|Oman|Mauritius|"
                  r"Japan|Korea|ASEAN|New\s+Zealand|European\s+Union|EU|Chile|Peru|Canada|USA|United\s+States)\b",),
    min_lines=4, min_entities=3, hosts=("commerce.gov.in", "pib.gov.in", "tradestat.commerce.gov.in"),
    queries=("India FTA partner exports imports before after agreement commerce ministry data",),
)
_FTA_TARIFF = ChartNeed(
    key="fta_tariff_lines", title="Tariff lines and duty reductions under the agreements",
    chart_types=("bar", "horizontal_bar", "timeline"),
    terms=(r"\btariff\s+lines?\b", r"\bduty\b.{0,40}(?:%|per\s*cent)", r"\bsigned\b"),
    min_lines=3, required=False, hosts=("pib.gov.in", "commerce.gov.in"),
    queries=("India free trade agreement signed tariff lines eliminated percent PIB",),
)
_BANK_SERIES = ChartNeed(
    key="bank_credit_series", title="Bank credit and deposit growth over time",
    chart_types=("line", "bar", "grouped_bar"),
    terms=(r"\b(?:credit|deposit|advances|loan)\w*\s+growth\b", r"\bgross\s+npa\b", r"\bnet\s+npa\b", r"\bcredit[- ]deposit\b"),
    entity_terms=(r"\b(?:sbi|hdfc|icici|axis|kotak|pnb|bank\s+of\s+baroda|canara|public\s+sector|private\s+sector|scheduled\s+commercial)\b",),
    min_lines=4, min_periods=4, min_entities=1, hosts=("rbi.org.in", "dbie.rbi.org.in"),
    queries=("RBI scheduled commercial banks credit deposit growth gross NPA latest statistical data",),
)
_GENERIC_TREND = ChartNeed(
    key="headline_trend", title="Headline metric over time",
    chart_types=("line", "bar"),
    terms=(r"(?:%|crore|billion|million|lakh|\bbn\b|\bmn\b|\brs\.?\b|₹|\$)",),
    min_lines=6, min_periods=3, min_entities=1, required=False,
)
_GENERIC_COMPARE = ChartNeed(
    key="peer_comparison", title="Comparison across the main entities",
    chart_types=("bar", "horizontal_bar", "grouped_bar"),
    terms=(r"(?:%|crore|billion|million|lakh|\bbn\b|\bmn\b|\brs\.?\b|₹|\$)",),
    min_lines=6, min_periods=1, min_entities=2, required=False,
)

_DOMAINS: tuple[tuple[str, re.Pattern, tuple[ChartNeed, ...]], ...] = (
    ("sector_rotation",
     re.compile(r"(?i)sector[ -]rotation|rotation across sectors|sectoral\s+(?:returns?|performance|leadership)|"
                r"nse sector|sector indices|sector index|sector (?:returns?|leadership|performance)"),
     (_SECTOR_QUARTERLY, _FPI_SECTOR, _FPI_FLOW_SERIES, _SECTOR_VALUATION)),
    ("fpi_flows",
     re.compile(r"(?i)\b(?:fpi|fii|foreign\s+(?:portfolio|institutional)\s+invest\w*)\b"),
     (_FPI_FLOW_SERIES, _FPI_SECTOR)),
    ("promoter",
     re.compile(r"(?i)promoter|pledg\w+|stake\s+sales?|block\s+deals?|bulk\s+deals?"),
     (_PROMOTER_SALES, _PROMOTER_HOLDING)),
    ("fta",
     re.compile(r"(?i)\bfree\s+trade\b|\bFTAs?\b|trade\s+agreements?|\bCEPA\b|\bTEPA\b"),
     (_FTA_TRADE, _FTA_TARIFF)),
    ("banking",
     re.compile(r"(?i)banking\s+sector|scheduled\s+commercial|bank\s+credit|deposit\s+growth|gross\s+npa|net\s+npa|indian\s+banks|credit\s+growth"),
     (_BANK_SERIES,)),
)
_TIER_MAX_NEEDS = {"brief": 3, "detailed": 5, "in_depth": 6}


def plan_chart_needs(question: str, report_tier: str = "detailed") -> list[ChartNeed]:
    """The charts this question calls for, most important first, bounded by tier."""
    text = str(question or "")
    needs: list[ChartNeed] = []
    seen: set[str] = set()
    for _name, pattern, domain_needs in _DOMAINS:
        if pattern.search(text):
            for need in domain_needs:
                if need.key not in seen:
                    seen.add(need.key)
                    needs.append(need)
    if not needs and re.search(r"(?i)\b(?:trend|over\s+(?:the\s+)?(?:past|last)|quarter|year[- ]on[- ]year|growth|history|historical)\b", text):
        needs = [_GENERIC_TREND, _GENERIC_COMPARE]
    elif not needs:
        needs = [_GENERIC_COMPARE, _GENERIC_TREND]
    needs.sort(key=lambda n: (not n.required,))
    return needs[:_TIER_MAX_NEEDS.get(report_tier, 5)]


# ─────────────────────────────────────────────────────────────────────────────
# Coverage
# ─────────────────────────────────────────────────────────────────────────────
def _host(url: object) -> str:
    try:
        host = (urlparse(str(url or "")).hostname or "").casefold()
    except Exception:
        return ""
    return host[4:] if host.startswith("www.") else host


def _lines(text: str) -> list[str]:
    out: list[str] = []
    for raw in str(text or "").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        if len(raw) > 400 and "|" not in raw:
            out.extend(part.strip() for part in re.split(r"(?<=[.!?])\s+", raw) if part.strip())
        else:
            out.append(raw)
    return out


def _is_calculated_sector_source(source: dict) -> bool:
    title = str(source.get("title") or "").upper()
    origin = str(source.get("evidence_origin") or source.get("evidenceOrigin") or "").casefold()
    return "CALCULATED NSE SECTOR RETURNS" in title or origin == "calculated_from_daily_market_data"


def _period_context_for_line(lines: list[str], index: int) -> tuple[set[str], list[str]]:
    """Return periods and nearby metric/header text that belong to one data row.

    Periods commonly live in a table header or caption rather than beside each
    number. Attach those periods only to the immediately associated table rows;
    never aggregate every year mentioned somewhere on a page into one coverage
    count. A source title/publication date cannot supply a period for every
    otherwise undated line on that page.
    """
    line = lines[index]
    context_lines = [line]
    periods = {_period_key(m.group(0)) for m in _PERIOD_RE.finditer(line)}

    if "|" in line:
        table_start = index
        while table_start > 0 and "|" in lines[table_start - 1]:
            table_start -= 1
        # The first pipe row is usually the header. A short caption immediately
        # before it may carry the single date shared by all rows in the table.
        header_line = lines[table_start]
        context_lines.append(header_line)
        periods.update(_period_key(m.group(0)) for m in _PERIOD_RE.finditer(header_line))
        for caption in lines[max(0, table_start - 2):table_start]:
            if _PERIOD_RE.search(caption) and re.search(
                r"(?i)period|quarter|month|as of|as at|coverage|performance|return|investment|flow|data for|ended",
                caption,
            ):
                context_lines.append(caption)
                periods.update(_period_key(m.group(0)) for m in _PERIOD_RE.finditer(caption))
    elif index > 0 and _PERIOD_RE.search(lines[index - 1]) and len(lines[index - 1]) <= 240:
        # A period/date caption directly above a prose observation is valid
        # context. Keep this one-line association local.
        context_lines.append(lines[index - 1])
        periods.update(_period_key(m.group(0)) for m in _PERIOD_RE.finditer(lines[index - 1]))

    return periods, context_lines


def assess_chart_coverage(needs: list[ChartNeed], sources: list[dict]) -> list[NeedCoverage]:
    """How well do ``sources`` (dicts with id/text/url/title) cover each planned chart?

    A source line counts only when a numeric observation can be tied to an
    entity and a reporting period. Table headers/captions may provide the
    period for their own rows, but unrelated dates elsewhere on the page do not.
    """
    prepared: list[tuple[str, list[str]]] = []
    for source in sources or []:
        if not isinstance(source, dict):
            continue
        sid = normalize_sid(source.get("id"))
        text = str(source.get("text") or source.get("pageContent") or source.get("fullContent") or source.get("snippet") or "")
        if sid and text.strip():
            prepared.append((sid, _lines(text)))

    results: list[NeedCoverage] = []
    for need in needs:
        term_res = [re.compile(t, re.I | re.S) for t in need.terms]
        entity_res = [re.compile(t, re.I) for t in need.entity_terms]
        per_sid: dict[str, int] = {}
        periods: set[str] = set()
        entities: set[str] = set()
        for sid, lines in prepared:
            hits = 0
            for line_index, line in enumerate(lines):
                if not _NUMBER_RE.search(line):
                    continue
                row_periods, row_context = _period_context_for_line(lines, line_index)
                if not row_periods:
                    continue
                evidence_line = " ".join(row_context)
                if not any(r.search(evidence_line) for r in term_res):
                    continue
                row_entities: set[str] = set()
                for er in entity_res:
                    row_entities.update(re.sub(r"\s+", " ", m.group(0)).casefold() for m in er.finditer(line))
                if not row_entities and "|" in line:
                    # Structured tables identify the entity in their first
                    # cell. Use it for needs without a fixed entity catalogue,
                    # and for calculated sector rows with provider-specific
                    # labels that the catalogue does not yet recognize.
                    row_label = line.strip().strip("|").split("|", 1)[0].strip()
                    if row_label and not re.search(
                        r"(?i)^(?:sector|index|entity|period|quarter coverage|metric|description|name)$", row_label,
                    ) and not _PERIOD_RE.fullmatch(row_label):
                        row_entities.add(re.sub(r"\s+", " ", row_label).casefold())
                if not row_entities:
                    continue
                hits += 1
                periods.update(row_periods)
                entities.update(row_entities)
            if hits:
                per_sid[sid] = hits
        total = sum(per_sid.values())
        cov = NeedCoverage(need=need, lines=total, periods=len(periods), entities=len(entities),
                           source_ids=sorted(per_sid, key=lambda s: (-per_sid[s], sid_number(s)))[:6])
        entities_ok = len(entities) >= need.min_entities
        if total >= need.min_lines and len(periods) >= need.min_periods and entities_ok:
            cov.status = "satisfied"
        elif total > 0:
            cov.status = "partial"
            gaps = []
            if total < need.min_lines:
                gaps.append(f"{total}/{need.min_lines} figure lines")
            if len(periods) < need.min_periods:
                gaps.append(f"{len(periods)}/{need.min_periods} periods")
            if not entities_ok:
                gaps.append(f"{len(entities)}/{need.min_entities} entities")
            cov.reason = "has " + ", ".join(gaps)
        else:
            cov.reason = "no retrieved source carries these figures"
        results.append(cov)
    return results


def unmet_needs(coverages: list[NeedCoverage]) -> list[NeedCoverage]:
    return [c for c in coverages if c.status != "satisfied" and (c.need.required or c.status == "missing")]


def gap_queries(coverages: list[NeedCoverage], limit: int = 4) -> list[str]:
    """Targeted searches for the planned charts whose data is not yet in the evidence."""
    queries: list[str] = []
    candidates = [list(dict.fromkeys(cov.need.queries)) for cov in unmet_needs(coverages)]
    # Spread the bounded search budget across unmet datasets first. Taking the
    # first N queries directly starved later required needs (e.g. FPI/DII flows)
    # whenever the sector-return need also had several query variants.
    depth = 0
    while len(queries) < max(0, limit) and any(depth < len(items) for items in candidates):
        for items in candidates:
            if depth >= len(items):
                continue
            query = items[depth]
            if query not in queries:
                queries.append(query)
                if len(queries) >= max(0, limit):
                    break
        depth += 1
    return queries[: max(0, limit)]


def gaps_needing_fetcher(coverages: list[NeedCoverage], fetcher: str) -> bool:
    return any(c.need.fetcher == fetcher and c.status != "satisfied" for c in coverages)


# ─────────────────────────────────────────────────────────────────────────────
# Consumers: gallery selection, writer prompt, diagnostics
# ─────────────────────────────────────────────────────────────────────────────
def planned_chart_types(coverages: list[NeedCoverage]) -> list[str]:
    """Chart types whose planned data coverage meets its minimum requirements."""
    ordered: list[str] = []
    for cov in coverages:
        if cov.status == "satisfied":
            for chart_type in cov.need.chart_types:
                if chart_type not in ordered:
                    ordered.append(chart_type)
    return ordered


def bind_charts_to_plan(charts: list | None, coverages: list[NeedCoverage]) -> tuple[list, int]:
    """Attach planned source IDs to charts that carry none.

    A writer chart with no ``source`` fails the audit outright ("no source IDs
    attached") even when the planned evidence exists. A chart is bound only when
    its title/axis/series text matches a planned need that has sources, so a
    value still has to be found in those sources by the grounding step; binding
    never marks anything verified.
    """
    from utils.source_ids import chart_source_ids

    usable = [c for c in coverages if c.status in {"satisfied", "partial"} and c.source_ids]
    out: list = []
    bound = 0
    for chart in charts or []:
        if not isinstance(chart, dict) or chart_source_ids(chart) or not usable:
            out.append(chart)
            continue
        kind = str(chart.get("type") or chart.get("chartType") or "").casefold()
        if kind in {"table", "data_table", "supporting_table"}:
            out.append(chart)
            continue
        names = " ".join(str(chart.get(k) or "") for k in ("title", "yLabel", "xLabel", "unit"))
        names += " " + " ".join(str((s or {}).get("name") or (s or {}).get("label") or "")
                                for s in (chart.get("series") or []) if isinstance(s, dict))
        match = next((c for c in usable if any(re.search(t, names, re.I | re.S) for t in c.need.terms)
                      or any(len(re.findall(t, names, re.I)) >= 2 for t in c.need.entity_terms)), None)
        if not match:
            out.append(chart)
            continue
        spec = dict(chart)
        spec["sourceIds"] = list(match.source_ids)
        spec["source"] = list(match.source_ids)
        out.append(spec)
        bound += 1
    return out, bound


def writer_plan_block(coverages: list[NeedCoverage]) -> str:
    """Prompt block binding each planned chart to the canonical IDs that can support it."""
    if not coverages:
        return ""
    lines = [
        "CHART PLAN (chosen by the server from the evidence actually retrieved - follow it):",
    ]
    number = 0
    for cov in coverages:
        number += 1
        types = "/".join(cov.need.chart_types[:3])
        if cov.status in {"satisfied", "partial"} and cov.source_ids:
            ids = ", ".join(f"[{sid}]" for sid in cov.source_ids)
            qualifier = "" if cov.status == "satisfied" else f" PARTIAL ({cov.reason}) - plot only the values present and say what is missing."
            lines.append(
                f"{number}. {cov.need.title} - chart type: {types}. Evidence in: {ids}.{qualifier} "
                f"Set the chart's \"source\" to these IDs only; every plotted value must be quoted from them."
            )
        else:
            lines.append(
                f"{number}. {cov.need.title} - NOT AVAILABLE: {cov.reason or 'no usable source'} even after a targeted search. "
                "Do NOT draw this chart and do not estimate its values; name the gap under 'Source coverage and limitations'."
            )
    lines.append(
        "Do not draw a chart that is not on this plan unless a table in a cited source supports every value. "
        "Source IDs are fixed: use only the S-numbers shown in SOURCES and never renumber them."
    )
    return "\n".join(lines) + "\n\n"


def plan_meta(coverages: list[NeedCoverage]) -> dict:
    return {
        "needs": [c.as_dict() for c in coverages],
        "satisfied": sum(1 for c in coverages if c.status == "satisfied"),
        "partial": sum(1 for c in coverages if c.status == "partial"),
        "missing": sum(1 for c in coverages if c.status == "missing"),
    }
