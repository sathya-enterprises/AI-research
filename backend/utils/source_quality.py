"""Source-quality assessment: authority tier, recency, reliability, dedup, boilerplate.

The relevance gate in routes/report.py decides whether a page is *about* the
question. This module decides how much the page should be *trusted*, and
records why, so the writer, the evidence store and the source appendix can
all use the same reliability judgement:

  authority tier   primary_official > institutional_research > established_media
                   > data_aggregator > unknown > low_quality; denylisted sources
                   (look-alike brands, known content farms) are dropped.
  recency          relative to the requested window ("last 4 quarters" etc.);
                   undated sources score neutral, not high.
  evidence         directly fetched publisher text with numbers/tables beats a
                   search-index excerpt.
  reliability      0.55·authority + 0.25·recency + 0.20·evidence, in [0, 1].

It also removes near-duplicate pages (syndicated copies) and boilerplate lines
that repeat across pages of the same site (navigation, tickers, footers).

Extend lists without code changes:
  GG_SOURCE_DENYLIST="host1=reason;host2=reason"
  GG_SOURCE_LOW_QUALITY="host1,host2"
"""
from __future__ import annotations

import logging
import os
import re
from collections import Counter, defaultdict
from datetime import date, datetime
from difflib import SequenceMatcher
from urllib.parse import urlparse

log = logging.getLogger("source_quality")

TIER_SCORES = {
    "primary_official": 1.0,
    "institutional_research": 0.82,
    "established_media": 0.72,
    "data_aggregator": 0.62,
    "unknown": 0.45,
    "low_quality": 0.2,
}

_PRIMARY = (
    "nseindia.com", "nseindia.in", "niftyindices.com", "bseindia.com", "nsdl.co.in", "cdslindia.com",
    "sebi.gov.in", "rbi.org.in", "amfiindia.com", "mospi.gov.in", "pib.gov.in", "dea.gov.in", "finmin.nic.in",
    "imf.org", "worldbank.org", "bis.org", "oecd.org", "adb.org", "sec.gov", "federalreserve.gov", "ecb.europa.eu",
)
_PRIMARY_SUFFIXES = (".gov.in", ".nic.in", ".gov", ".gov.uk", ".europa.eu")
_INSTITUTIONAL = (
    "motilaloswal.com", "icra.in", "icraresearch.in", "crisil.com", "careratings.com", "indiaratings.co.in",
    "kotaksecurities.com", "kotak.com", "hdfcsec.com", "hdfcfund.com", "icicidirect.com", "icicipruamc.com",
    "miraeassetmf.co.in", "sbimf.com", "nipponindiamf.com", "axismf.com", "dspim.com", "ppfas.com",
    "privatebank.barclays.com", "morganstanley.com", "goldmansachs.com", "jpmorgan.com", "msci.com",
    "spglobal.com", "morningstar.in", "morningstar.com", "nomuraholdings.com", "jefferies.com", "ambit.co",
    "iiflsecurities.com", "edelweissmf.com", "sparkcapital.in", "eatonvance.com", "valueresearchonline.com", "nism.ac.in", "doi.org", "ssrn.com",
)
_MEDIA = (
    "economictimes.indiatimes.com", "economictimes.com", "livemint.com", "business-standard.com", "moneycontrol.com",
    "financialexpress.com", "thehindubusinessline.com", "reuters.com", "bloomberg.com", "ft.com", "wsj.com",
    "cnbctv18.com", "ndtvprofit.com", "businesstoday.in", "fortuneindia.com", "thehindu.com",
    "timesofindia.indiatimes.com", "indianexpress.com", "outlookbusiness.com", "forbesindia.com",
    "zeebiz.com", "bqprime.com", "theprint.in", "cnbc.com",
)
_AGGREGATORS = (
    "trendlyne.com", "screener.in", "tickertape.in", "marketsmojo.com", "investing.com", "indiainfoline.com",
    "stockedge.com", "chittorgarh.com", "niftytrader.in", "groww.in", "upstox.com", "zerodha.com",
    "angelone.in", "5paisa.com", "smallcase.com", "finology.in", "ticker.finology.in", "equitymaster.com",
)
_LOW_QUALITY_SUFFIXES = (".blogspot.com", ".wordpress.com", ".medium.com", ".substack.com", ".wixsite.com", ".weebly.com", ".tumblr.com")
_LOW_QUALITY = (
    "medium.com", "quora.com", "reddit.com", "scribd.com", "slideshare.net", "issuu.com", "pinterest.com",
    "ijrpr.com", "ijcrt.org", "ijsr.net", "jetir.org", "irjmets.com", "ijnrd.org", "ijrti.org", "ijsem.org",  # pay-to-publish journals
    "tipsheet.markets", "useflock.in",
)
# Look-alike / impersonation domains: dropped, with the reason logged.
_DENYLIST = {
    "downstox.com": "look-alike of upstox.com (brand impersonation; not the Upstox publisher)",
}
# Brands whose look-alike hosts are treated as impersonation when the label is very similar.
_BRANDS = ("upstox", "zerodha", "groww", "angelone", "moneycontrol", "economictimes", "nseindia", "bseindia",
           "niftyindices", "livemint", "trendlyne", "screener", "tickertape", "valueresearchonline")


def _host(url: str) -> str:
    host = (urlparse(str(url or "")).hostname or "").casefold()
    return host[4:] if host.startswith("www.") else host


def _matches(host: str, domains) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def _env_denylist() -> dict[str, str]:
    out = dict(_DENYLIST)
    for part in os.getenv("GG_SOURCE_DENYLIST", "").split(";"):
        if "=" in part:
            host, reason = part.split("=", 1)
            if host.strip():
                out[host.strip().casefold()] = reason.strip() or "listed in GG_SOURCE_DENYLIST"
    return out


def _env_low_quality() -> tuple[str, ...]:
    extra = tuple(h.strip().casefold() for h in os.getenv("GG_SOURCE_LOW_QUALITY", "").split(",") if h.strip())
    return _LOW_QUALITY + extra


def lookalike_brand(host: str) -> str | None:
    """Return the impersonated brand when a host label is a near-copy of a known brand."""
    label = host.split(".")[0] if host else ""
    if len(label) < 5:
        return None
    for brand in _BRANDS:
        if label == brand:
            return None
        if SequenceMatcher(None, label, brand).ratio() >= 0.8 and abs(len(label) - len(brand)) <= 3:
            return brand
    return None


def authority(url: str) -> tuple[str, str]:
    """(tier, reason) for a URL; tier "denylisted" means drop."""
    host = _host(url)
    if not host:
        return "unknown", "no public URL"
    deny = _env_denylist()
    for listed, reason in deny.items():
        if host == listed or host.endswith("." + listed):
            return "denylisted", reason
    brand = lookalike_brand(host)
    if brand:
        return "denylisted", f"host label looks like the {brand} brand but is a different domain"
    if _matches(host, _PRIMARY) or host.endswith(_PRIMARY_SUFFIXES):
        return "primary_official", "regulator / exchange / official statistics"
    if _matches(host, _INSTITUTIONAL):
        return "institutional_research", "broker, AMC, rating agency or research institution"
    if _matches(host, _MEDIA):
        return "established_media", "established financial / news publisher"
    if _matches(host, _AGGREGATORS):
        return "data_aggregator", "market-data aggregator or broker portal"
    if host.endswith(_LOW_QUALITY_SUFFIXES) or _matches(host, _env_low_quality()):
        return "low_quality", "personal blog, forum, document-sharing or pay-to-publish site"
    return "unknown", "publisher not on a recognised list"


_DATE_KEYS = ("publication_date", "publicationDate", "publisherPagePublishedAt", "published_at", "publishedAt", "published", "date")


def source_date(row: dict) -> date | None:
    for key in _DATE_KEYS:
        raw = str(row.get(key) or "").strip()
        if not raw:
            continue
        match = re.search(r"((?:19|20)\d{2})-(\d{2})-(\d{2})", raw)
        if match:
            try:
                return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            except ValueError:
                continue
        for fmt in ("%d %B %Y", "%B %d, %Y", "%d %b %Y", "%b %d, %Y"):
            try:
                return datetime.strptime(raw[:30], fmt).date()
            except ValueError:
                continue
    return None


def window_start(question: str, today: date) -> date:
    """Start of the period the question asks about (default: two years back)."""
    import re as _re
    since = _re.search(r"(?i)\b(?:since|from|after)\s+(?:the\s+(?:start|beginning)\s+of\s+)?((?:19|20)\d{2})\b(?!\s*(?:to|until|till|through|and|-|–|—)\s*(?:19|20)?\d{2})", str(question or ""))
    if since and int(since.group(1)) <= today.year:
        return date(int(since.group(1)), 1, 1)  # Stage J: "since 2022" starts in January 2022
    span = _re.search(r"(?i)\b(?:from|between)\s+((?:19|20)\d{2})\s*(?:to|until|till|through|and|-|–|—)\s*(?:19|20)\d{2}\b",
                      str(question or ""))
    if span and int(span.group(1)) <= today.year:
        return date(int(span.group(1)), 1, 1)  # "from 2022 to 2024" starts in January 2022
    try:
        from utils.deep_research_coverage import requested_indian_fy_quarters, requested_quarter_count
        periods = requested_indian_fy_quarters(question, today)
        if periods:
            return periods[0][1]
        quarters = requested_quarter_count(question, today, default=8)
    except Exception:  # pragma: no cover - import safety
        quarters = 8
    return date(today.year - (quarters // 4), today.month, min(today.day, 28))


def recency(row: dict, start: date, today: date) -> tuple[float, str]:
    published = source_date(row)
    if not published:
        return 0.5, "undated"
    if published > today:
        return 0.5, f"dated {published.isoformat()} (future date; treated as undated)"
    if published >= start:
        return 1.0, f"dated {published.isoformat()} (inside the requested window)"
    years_before = (start - published).days / 365.25
    score = max(0.05, 0.5 ** years_before)  # half-life of one year before the window
    return round(score, 3), f"dated {published.isoformat()} ({years_before:.1f} years before the requested window)"


def evidence_strength(row: dict) -> tuple[float, str]:
    page = str(row.get("pageContent") or "")
    status = str(row.get("pageFetchStatus") or "")
    if status == "ok" and page:
        digits = sum(ch.isdigit() for ch in page[:20000])
        table = "TABLE " in page or "| " in page
        score = 0.7 + (0.15 if digits >= 150 else 0.0) + (0.15 if table else 0.0)
        return min(1.0, score), "publisher page fetched" + (" with tables" if table else "")
    if str(row.get("fullContent") or row.get("snippet") or "").strip():
        return 0.35, "search-index excerpt only (publisher page not read)"
    return 0.1, "metadata only"


def _shingles(text: str, size: int = 5) -> set[int]:
    words = re.findall(r"[a-z0-9]+", text.casefold())[:3000]
    return {hash(" ".join(words[i:i + size])) for i in range(max(0, len(words) - size + 1))}


def strip_cross_page_boilerplate(rows: list[dict], min_pages: int = 3) -> int:
    """Remove lines that repeat across >= min_pages pages of the same host. Returns lines removed."""
    by_host: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if str(row.get("pageContent") or "").strip():
            by_host[_host(str(row.get("url") or ""))].append(row)
    removed = 0
    for host, group in by_host.items():
        if len(group) < min_pages:
            continue
        counts = Counter()
        for row in group:
            counts.update({line.strip() for line in str(row["pageContent"]).splitlines() if len(line.strip()) >= 12})
        repeated = {line for line, n in counts.items() if n >= min_pages and not re.search(r"\d{2,}.*\d", line)}
        if not repeated:
            continue
        for row in group:
            kept = []
            for line in str(row["pageContent"]).splitlines():
                if line.strip() in repeated:
                    removed += 1
                    continue
                kept.append(line)
            row["pageContent"] = "\n".join(kept)
    return removed


def assess_sources(rows: list[dict], *, question: str = "", report_tier: str = "detailed",
                   today: date | None = None, near_duplicate_threshold: float = 0.8) -> tuple[list[dict], dict]:
    """Annotate rows with authority/recency/reliability; drop denylisted rows and near-duplicates.

    Row order is preserved (source IDs are assigned later from this order).
    Internal/calculated sources (no public URL) are trusted as computed data.
    """
    today = today or date.today()
    start = window_start(question, today)
    stats = Counter()
    denied: list[str] = []
    kept: list[dict] = []
    boilerplate_lines = strip_cross_page_boilerplate([r for r in rows if isinstance(r, dict)])
    for row in rows:
        if not isinstance(row, dict):
            continue
        url = str(row.get("url") or "")
        internal = not url.startswith(("http://", "https://"))
        if internal:
            tier, tier_reason = "primary_official", "calculated / internal dataset with recorded provenance"
        else:
            tier, tier_reason = authority(url)
        if tier == "denylisted":
            stats["denylisted"] += 1
            denied.append(f"{_host(url)}: {tier_reason}")
            log.warning("Source quality: dropped %s — %s", _host(url), tier_reason)
            continue
        if not str(row.get("publication_date") or "").strip() and str(row.get("published") or "").strip():
            row["publication_date"] = str(row["published"])[:120]
            row.setdefault("publicationDateSource", "search_provider_metadata")
        rec, rec_reason = (1.0, "calculated for the requested window") if internal else recency(row, start, today)
        ev, ev_reason = (1.0, "computed dataset") if internal else evidence_strength(row)
        reliability = round(0.55 * TIER_SCORES[tier] + 0.25 * rec + 0.20 * ev, 3)
        row["authorityTier"] = tier
        row["recencyScore"] = rec
        row["evidenceStrength"] = round(ev, 3)
        row["reliabilityScore"] = reliability
        row["reliabilityNote"] = f"{tier.replace('_', ' ')} ({tier_reason}); {rec_reason}; {ev_reason}"
        stats[f"tier_{tier}"] += 1
        stats["dated"] += 1 if source_date(row) else 0
        kept.append(row)

    # Near-duplicates (syndicated copies, mirrors): keep the more reliable row.
    signatures = [(_shingles(str(r.get("pageContent") or r.get("fullContent") or "")), r) for r in kept]
    dropped_ids: set[int] = set()
    for i in range(len(signatures)):
        a, row_a = signatures[i]
        if id(row_a) in dropped_ids or len(a) < 40:
            continue
        for j in range(i + 1, len(signatures)):
            b, row_b = signatures[j]
            if id(row_b) in dropped_ids or len(b) < 40:
                continue
            overlap = len(a & b) / max(1, min(len(a), len(b)))
            if overlap >= near_duplicate_threshold:
                loser = row_b if row_a["reliabilityScore"] >= row_b["reliabilityScore"] else row_a
                winner = row_a if loser is row_b else row_b
                loser_url = str(loser.get("url") or "")
                dropped_ids.add(id(loser))
                log.info("Source quality: near-duplicate %s dropped in favour of %s (%.0f%% shared text)",
                         _host(loser_url), _host(str(winner.get("url") or "")), overlap * 100)
                if loser is row_a:
                    break
    final = [r for r in kept if id(r) not in dropped_ids]
    stats["near_duplicates"] = len(dropped_ids)
    stats["boilerplate_lines_removed"] = boilerplate_lines
    stats["kept"] = len(final)
    summary = dict(stats)
    summary["denylisted_hosts"] = denied
    if final:
        summary["mean_reliability"] = round(sum(r["reliabilityScore"] for r in final) / len(final), 3)
    return final, summary
