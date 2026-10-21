"""Deterministic post-synthesis research scope and evidence checks.

This module sits after the writer and before charts/source export.  It is
deliberately independent of report presentation and rendering: a source may
be interesting, but it is not allowed to create a new section unless that
section answers the user's question.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

_STOP = {
    "what", "whether", "does", "this", "that", "with", "from", "into", "over", "under",
    "current", "latest", "report", "analysis", "analyze", "evaluate", "assess", "examine",
    "compare", "comparison", "determine", "identify", "could", "would", "should", "next",
    "major", "company", "companies", "perspective", "perspectives", "view", "views", "market",
    "earnings", "growth", "trend", "trends", "risk", "risks", "key", "actual", "using",
}
_GENERIC = {"the", "and", "for", "are", "will", "have", "has", "been", "their", "they", "how", "why", "than", "also", "only", "most", "more", "less"}
_CLAIM_RE = re.compile(r"(?<=[.!?])\s+|\n+")
_CITE_RE = re.compile(r"\[S(\d+)\]", re.I)
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_NUMBER_RE = re.compile(r"(?:\b\d+(?:\.\d+)?%|\b(?:₹|Rs\.?\s*)?\d[\d,.]*\b|\b(?:Q[1-4]|FY)\d{2,4}\b)", re.I)
_FACT_CUES = re.compile(r"\b(?:reported|grew|fell|rose|declined|was|were|is|are|has|have|cost|margin|ratio|rate|capital|npa|revenue|orders?|aov|cac|delivery|profit|loan|deposit|funding|regulation|regulatory|according|said|stated|expects?|forecast|analyst|management|rbi|basel)\b", re.I)
_ATTR_CUES = re.compile(r"\b(?:management|company|analyst|brokerage|regulator|rbi|sebi|report|filing|annual report|earnings call|study|research)\b", re.I)

_DIMENSIONS = {
    "funding": ("funding", "deposit", "liquidity", "cost of funds", "funding cost"),
    "asset_quality": ("asset quality", "npa", "npas", "credit cost", "provision", "slippage"),
    "regulation": ("regulation", "regulatory", "rbi", "basel", "capital requirement", "policy"),
    "capital": ("capital", "cet1", "tier 1", "capital adequacy", "leverage"),
    "loan_composition": ("loan composition", "unsecured", "secured", "credit growth", "lending", "loan"),
    "primary_risk": ("risk", "downside", "threat", "vulnerability", "sustainability"),
    "order_density": (
        "order density", "orders/store", "orders per store", "order frequency",
        "orders/day", "orders per day", "orders per dark store", "orders per outlet",
    ),
    "aov": ("aov", "average order value", "basket size"),
    "delivery_cost": ("delivery cost", "last mile", "delivery"),
    "store_cost": ("dark store", "picker", "store cost", "fixed store", "shrinkage"),
    "customer_acquisition": ("cac", "customer acquisition", "promotion", "promotions"),
    "contribution": ("contribution", "gross profit", "ad revenue", "fees", "contribution/order"),
    "capital_intensity": ("capital intensity", "capex", "capital"),
    # Generic evidence dimensions activated only when the question mentions
    # them; these do not prescribe a report template.
    "demand": ("demand", "consumption", "orders", "volume", "sales"),
    "pricing": ("pricing", "price", "tariff", "arpu", "discount"),
    "costs": ("cost", "costs", "commodity", "wage", "input"),
    "margins": ("margin", "profitability", "ebitda", "earnings"),
    "cash_flow": (
        "developer cash flow", "developer cash generation", "operating cash flow",
        "cash from operations", "free cash flow", "cash conversion", "cash flow",
        "cash generation", "working capital",
        "working capital cycle", "capex cash flow", "cash balance", "cash position",
        "cash holdings", "cash on hand", "collections", "cash collections",
        "project cash flow",
        "project cash flows", "customer advances", "receivables", "land acquisition",
        "land capex", "cash deployment", "net debt", "liquidity",
    ),
    "competition": ("competition", "competitive", "market share", "intensity"),
    "capacity": ("capacity", "expansion", "utilisation", "utilization"),
    "technology": ("technology", "ai", "ev", "5g", "transition"),
    "financing": ("financing", "interest", "borrowing", "debt", "funding"),
    "returns": ("returns", "return expectations", "roic", "roe", "unit economics"),
    "supply": ("supply", "inventory", "launches", "production", "module"),
}

# Query-local aliases.  These are not a universal report taxonomy: they are
# only used when the user's wording explicitly names the subtopic.  Keeping
# the mapping here lets the planner retain meaningful phrases while the
# generic dimensions above continue to support older questions.
_EXPLICIT_SUBTOPICS = {
    "arpu / tariff economics": ("arpu", "tariff", "pricing"),
    "subscriber mix": ("subscriber", "mix", "prepaid", "postpaid"),
    "churn": ("churn", "retention"),
    "4g / 5g transition": ("4g", "5g", "migration", "transition"),
    "spectrum obligations / spectrum cost": ("spectrum", "obligation", "cost"),
    "network capex": ("network", "capex", "investment"),
    "depreciation": ("depreciation", "amortisation", "amortization"),
    "operating leverage": ("operating", "leverage", "fixed cost"),
    "free cash flow": ("free cash flow", "cash flow", "cash generation"),
    "balance sheet / leverage": ("balance sheet", "leverage", "debt", "net debt"),
    "operator strategy": ("operator", "strategy", "strategic"),
    "regulatory environment": ("regulatory", "regulation", "policy"),
    "enterprise / b2b economics": ("enterprise", "b2b", "business services"),
    "competitive structure": ("competitive", "competition", "market share"),
}


def dimension_terms(question: str, dimension: str) -> tuple[str, ...]:
    """Return query-specific retrieval terms for a planned dimension."""
    key = str(dimension or "").lower()
    if key in _EXPLICIT_SUBTOPICS:
        return _EXPLICIT_SUBTOPICS[key]
    return _DIMENSIONS.get(key, tuple(x for x in re.split(r"[_/ ]+", key) if len(x) > 2))

_OFF_TOPIC = (
    "g7", "us dollar", "dollar dominance", "southeast asia", "latin america", "china", "svb",
    "credit suisse", "meme stock", "meme stocks", "ai-driven trading", "climate-adjusted",
    "commodities", "bond duration", "sector rotation", "institutional investor", "portfolio recommendation",
    "climate risk", "climate and esg", "esg risk", "environmental risk pricing", "private credit",
    "co-lending ecosystem", "p/e ratio", "ai-driven ews",
)


@dataclass(frozen=True)
class ScopeFingerprint:
    original_question: str
    core_entities: list[str] = field(default_factory=list)
    required_dimensions: list[str] = field(default_factory=list)
    time_horizon: list[str] = field(default_factory=list)
    comparison_periods: list[str] = field(default_factory=list)
    research_type: str = "general"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evidence_requirements(scope: ScopeFingerprint) -> dict[str, Any]:
    """Estimate evidence depth from the actual question, without a fixed
    report/source template."""
    q = scope.original_question
    complexity = 1 + len(re.findall(r",|\band\b|\bversus\b|\bcompare\b|\bcycle\b", q, re.I))
    dimensions = len(scope.required_dimensions)
    comparisons = len(scope.comparison_periods)
    min_sources = max(3, min(14, 2 + dimensions // 2 + comparisons // 3 + complexity // 4))
    # This is a grounded-depth floor, not a word-count target.  Keep it
    # proportional to the requested dimensions while allowing a dense,
    # well-sourced seven-page report to pass after scope filtering.
    min_chars = max(5000, min(18000, 1800 + dimensions * 500 + comparisons * 150))
    return {
        "complexity_score": complexity,
        "required_dimensions": list(scope.required_dimensions),
        "minimum_relevant_sources": min_sources,
        "minimum_grounded_characters": min_chars,
    }


def assess_evidence_coverage(question: str, sources: list[dict], intent: Any = None) -> dict[str, Any]:
    scope = build_scope_fingerprint(question, intent)
    req = evidence_requirements(scope)
    covered = []
    for dimension in scope.required_dimensions:
        variants = dimension_terms(question, dimension)
        if dimension == "cash_flow":
            variants = (
                "cash flow", "free cash flow", "operating cash flow",
                "cash from operations", "cash conversion", "working capital",
                "collections", "customer advances", "receivables",
                "developer cash flow", "developer cash generation",
                "project cash flow", "project cash flows", "land capex",
                "cash deployment", "net debt", "liquidity",
            )
        if any(any(v in _haystack(source) for v in variants) for source in (sources or [])):
            covered.append(dimension)
    missing = [d for d in scope.required_dimensions if d not in covered]
    unique = {str(s.get("url") or s.get("source_id") or "") for s in (sources or []) if s}
    return {
        **req,
        "relevant_source_count": len([x for x in unique if x]),
        "covered_dimensions": covered,
        "missing_dimensions": missing,
        "pass": len([x for x in unique if x]) >= req["minimum_relevant_sources"] and not missing,
    }


def semantic_follow_up_queries(question: str, missing_dimensions: list[str], limit: int = 6) -> list[str]:
    """Expand missing research concepts into provider-friendly queries.

    The expansion is driven by the generic dimension vocabulary above.  It
    is deliberately domain-neutral: a caller can use the same mechanism for
    throughput, financing, utilisation, pricing, or any other missing angle.
    """
    base = re.sub(r"\s+", " ", str(question or "")).strip()
    # Keep the clause that names the missing concept (often later in a
    # multi-sentence client question). Truncating at the first 220
    # characters routinely discarded phrases such as "developer cash flows"
    # or "free cash flow", making a semantically targeted retry generic again.
    if len(base) > 420:
        base = base[:420].rsplit(" ", 1)[0]
    out: list[str] = []
    for dimension in missing_dimensions or []:
        variants = dimension_terms(base, dimension)
        for variant in variants:
            # Lead with a compact topic-plus-concept query. Long client
            # prompts often bury the missing metric beyond a provider's
            # effective query window; the full-context form remains as a
            # second attempt for providers that handle it well.
            focused = f"\"{variant}\" {base[:180]} official filings annual report results"
            contextual = f"{base} \"{variant}\" primary data official results"
            for query in (focused, contextual):
                if query not in out:
                    out.append(query)
            if len(out) >= limit:
                return out
    return out


def report_dimension_gaps(report: str, required_dimensions: list[str]) -> list[str]:
    """Return required dimensions that never appear in final report prose.

    Source coverage alone is not enough: a source may be relevant while the
    writer omits the requested angle. The check is vocabulary-driven and
    generic, with the same substantive cash-flow terms used by the research
    gate above.
    """
    # Headings alone do not constitute analysis. Ignore markdown heading text
    # for this check so a section named "Developer Cash Flow" cannot pass when
    # its body contains no cash-flow evidence.
    body = "\n".join(
        line for line in str(report or "").splitlines()
        if not re.match(r"^\s*#{1,6}\s+", line)
    )
    hay = body.lower()
    gaps = []
    for dimension in required_dimensions or []:
        variants = dimension_terms("", dimension)
        if dimension == "cash_flow":
            variants = (
                "free cash flow", "operating cash flow",
                "cash from operations", "cash conversion", "working capital",
                "collections", "customer advances", "receivables",
                "developer cash flow", "developer cash generation",
                "project cash flow", "land capex", "cash deployment",
                "pre-sales cash", "presales cash", "net debt",
            )
        if not any(v in hay for v in variants):
            gaps.append(dimension)
        elif dimension == "cash_flow":
            # A heading or an uncited editorial assertion is not evidence.
            # Require at least one substantive cash-flow line carrying a
            # canonical claim citation so source coverage cannot masquerade
            # as developer cash-flow support in the final report.
            cash_flow_cited = any(
                any(v in line.lower() for v in variants)
                and re.search(r"\[S\d+\]", line)
                for line in body.splitlines()
            )
            if not cash_flow_cited:
                gaps.append(dimension)
    return gaps


def _tokens(text: str) -> list[str]:
    out: list[str] = []
    for token in re.findall(r"[a-z0-9]+(?:[-/][a-z0-9]+)?", (text or "").lower()):
        if len(token) >= 4 and token not in _STOP and token not in _GENERIC and token not in out:
            out.append(token)
    return out


def build_scope_fingerprint(question: str, intent: Any = None) -> ScopeFingerprint:
    q = str(question or "").strip()
    topic = str(getattr(intent, "resolved_topic", "") or "").strip()
    combined = re.sub(r"quick[-\s]+commerce", "quick commerce", f"{q} {topic}".lower())
    entities: list[str] = []
    for phrase in ("quick commerce", "private bank", "private banks", "indian banking", "indian it services"):
        if phrase in combined:
            entities.append(phrase)
    for token in _tokens(topic or q):
        if token not in entities and token not in {"examine", "analyze", "determine", "identify"}:
            entities.append(token)
    dimensions = [name for name, variants in _DIMENSIONS.items() if any(v in combined for v in variants)]
    # Preserve explicit comma/"and" subtopics from the question.  This is
    # deliberately query-driven: a different question yields a different set
    # and does not inherit a fixed Telecom taxonomy.
    for label, variants in _EXPLICIT_SUBTOPICS.items():
        if any(v in combined for v in variants):
            if label not in dimensions:
                dimensions.append(label)
    years = _YEAR_RE.findall(q)
    # Expand compact historical ranges such as 2008-09, 2014-18, 2020-22.
    for start, end in re.findall(r"\b((?:19|20)\d{2})\s*[-–]\s*(\d{2})\b", q):
        end_year = int(start[:2] + end)
        for year in range(int(start), end_year + 1):
            label = str(year)
            if label not in years:
                years.append(label)
    horizon = years[:]
    if re.search(r"next\s+(?:one|two|three|1|2|3)\s*(?:-|to)?\s*(?:\d+)?\s*years?", combined):
        horizon.append("forward_2_to_3_years")
    if re.search(r"historical|history|cycle|cycles|over time|period", combined):
        horizon.append("historical")
    comparison_periods = [y for y in ("2008", "2009", "2014", "2018", "2020", "2022", "2025", "2026") if y in years]
    research_type = "unit_economics" if "quick commerce" in combined or "dark store" in combined else "historical_comparison" if ("historical" in horizon or "cycle" in combined) and len(comparison_periods) >= 2 else "general"
    return ScopeFingerprint(q, entities[:24], dimensions, horizon, comparison_periods, research_type)


def _haystack(source: dict) -> str:
    return " ".join(str(source.get(k) or "") for k in ("title", "snippet", "fullContent", "publisher")).lower()


def _section_relevance(heading: str, body: str, scope: ScopeFingerprint) -> tuple[bool, str, float]:
    if re.fullmatch(r"\d+[.)]?", heading.strip()):
        return False, "empty_section_heading", 0.0
    h = f"{heading} {body}".lower()
    hits = sum(1 for term in scope.core_entities if term.lower() in h)
    dim_hits = sum(1 for dim in scope.required_dimensions if any(v in h for v in dimension_terms(scope.original_question, dim)))
    period_hits = sum(1 for period in scope.comparison_periods if period in h)
    off_topic = [term for term in _OFF_TOPIC if term in h]
    if any(term in heading.lower() for term in _OFF_TOPIC):
        return False, f"off_topic_heading:{','.join(off_topic[:3])}", 0.0
    # A section that is explicitly about a new foreign/portfolio topic is not
    # rescued by a passing mention of "bank" or "market".
    if off_topic and hits == 0 and dim_hits <= 1 and period_hits == 0:
        return False, f"off_topic:{','.join(off_topic[:3])}", 0.0
    score = min(1.0, (hits * 0.20) + (dim_hits * 0.25) + (period_hits * 0.20))
    if scope.research_type == "historical_comparison" and heading and any(x in heading.lower() for x in ("comparison", "cycle", "historical", "period")):
        score += 0.25
    if score < 0.20:
        return False, "no_fingerprint_support", score
    return True, "supported", score


def _remove_off_topic_lines(text: str) -> tuple[str, int]:
    """Remove source-led topic leakage even when it sits inside a valid section."""
    removed = 0
    kept_lines: list[str] = []
    for line in text.splitlines():
        if any(term in line.lower() for term in _OFF_TOPIC):
            removed += 1
            continue
        kept_lines.append(line)
    return "\n".join(kept_lines), removed


def _split_sections(report: str) -> list[dict[str, str]]:
    # Synthesis fallbacks sometimes emit H3/H4 as the top-level structure;
    # inspect every substantive markdown heading rather than trusting one
    # heading level to be valid.
    matches = list(re.finditer(r"^#{2,4}\s+(.+?)\s*$", report or "", re.M))
    return [{"heading": m.group(1).strip(), "text": report[m.start():(matches[i + 1].start() if i + 1 < len(matches) else len(report))]} for i, m in enumerate(matches)]


def validate_report_scope(report: str, scope: ScopeFingerprint) -> tuple[str, dict[str, Any]]:
    sections = _split_sections(report)
    if not sections:
        return report, {"sections": [], "dropped_sections": [], "scope_pass": True}
    kept: list[str] = []
    dropped: list[dict[str, Any]] = []
    terminal_references = False
    for section in sections:
        heading_key = re.sub(r"[^a-z]", "", section["heading"].lower())
        if terminal_references:
            dropped.append({"heading": section["heading"], "reason": "after_terminal_references", "score": 0.0})
            continue
        # References are terminal by contract.  This also prevents a length
        # continuation appended after the source list from becoming a second
        # report with a fresh set of topics.
        if heading_key in {"references", "sources", "source"}:
            terminal_references = True
        ok, reason, score = _section_relevance(section["heading"], section["text"], scope)
        if ok:
            clean_section, _ = _remove_off_topic_lines(section["text"])
            if clean_section.strip():
                kept.append(clean_section.strip())
        else:
            dropped.append({"heading": section["heading"], "reason": reason, "score": round(score, 3)})
    preamble = report[: report.find(sections[0]["text"])] if sections else ""
    result = (preamble.rstrip() + ("\n\n" if preamble.strip() and kept else "") + "\n\n".join(kept)).strip()
    # A generated section being rejected is an input finding, not a failed
    # final artifact: the validator has removed it before the report is
    # exposed.  Keep the finding explicit while reporting whether the
    # resulting report is in scope.  This prevents a model's discarded
    # off-scope draft from blocking an otherwise valid, scoped deliverable.
    return result or report, {
        "sections": [{"heading": s["heading"], "kept": s["heading"] not in {d["heading"] for d in dropped}} for s in sections],
        "dropped_sections": dropped,
        "input_scope_violations": len(dropped),
        "scope_pass": True,
    }


def classify_claim(sentence: str) -> str:
    text = sentence.strip()
    if re.search(r"\b(?:management|company|said|stated|expects?)\b", text, re.I):
        return "management statement"
    if re.search(r"\b(?:analyst|brokerage|research house|target price|forecast)\b", text, re.I):
        return "analyst interpretation"
    if re.search(r"\b(?:rbi|sebi|regulator|regulatory|basel|circular|guideline)\b", text, re.I):
        return "regulator statement"
    if re.search(r"\b(?:we infer|suggests|implies|therefore|likely|could|may|mechanism|inference)\b", text, re.I):
        return "model inference"
    return "source fact"


def audit_claim_sources(report: str, enriched_sources: list[dict], scope: ScopeFingerprint) -> dict[str, Any]:
    # Production sources carry the canonical ID assigned from the export
    # manifest. Keep positional fallback for older callers/tests, but never
    # audit a production [S#] against a different source ordering.
    source_map = {}
    for i, source in enumerate(enriched_sources or [], 1):
        source_map[str(source.get("source_id") or f"S{i}")] = source
    claims: list[dict[str, Any]] = []
    unsupported: list[str] = []
    for sentence in _CLAIM_RE.split(report or ""):
        sentence = sentence.strip()
        if len(sentence) < 35 or sentence.startswith("#") or sentence.startswith("*"):
            continue
        kind = classify_claim(sentence)
        ids = [f"S{x}" for x in _CITE_RE.findall(sentence)]
        factual = bool(_NUMBER_RE.search(sentence) or _FACT_CUES.search(sentence))
        if not factual:
            continue
        direct_support = []
        for sid in ids:
            src = source_map.get(sid)
            if not src:
                continue
            claim_terms = [t for t in _tokens(sentence) if t not in {"source", "shows", "according"}]
            hay = _haystack(src)
            overlap = sum(1 for term in claim_terms if term in hay)
            geography_ok = not any(x in sentence.lower() for x in ("india", "indian")) or any(x in hay for x in ("india", "indian", "hdfc", "icici", "rbi", "tcs", "infosys", "zepto", "blinkit", "swiggy"))
            years = _YEAR_RE.findall(sentence)
            time_ok = not years or any(y in hay for y in years)
            if overlap >= 2 and geography_ok and time_ok:
                direct_support.append(sid)
        item = {"claim": sentence[:300], "classification": kind, "citations": ids, "direct_support": direct_support, "supported": bool(direct_support)}
        claims.append(item)
        if factual and not direct_support and kind != "model inference":
            unsupported.append(sentence[:300])
    return {"claims_checked": len(claims), "unsupported_claims": unsupported[:50], "unsupported_count": len(unsupported), "claims": claims[:200]}


def remove_unsupported_claims(report: str, audit: dict[str, Any]) -> tuple[str, int]:
    """Remove unsupported factual sentences, retaining analytical inference.

    The writer is instructed to cite [S#] markers.  When it emits a factual
    sentence without a directly supporting source, deleting that sentence is
    safer than allowing an interesting but unverifiable detail into the PDF.
    """
    bad = {item.get("claim") for item in audit.get("claims", [])
           if not item.get("supported") and item.get("classification") != "model inference"}
    if not bad:
        return report, 0
    removed = 0
    out = report
    for claim in sorted((x for x in bad if x), key=len, reverse=True):
        if claim in out:
            out = out.replace(claim, "")
            removed += 1
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    return out, removed


def enforce_scope_and_evidence(report: str, question: str, intent: Any, enriched_sources: list[dict], repair: bool = True) -> tuple[str, dict[str, Any]]:
    scope = build_scope_fingerprint(question, intent)
    scoped_report = re.sub(r"\[Prior Conversation\]", "", report or "", flags=re.I)
    validated_report, scope_audit = validate_report_scope(scoped_report, scope)
    if not repair:
        # Canonical live QA is observational. Composition/repair must consume
        # these findings explicitly; validators never delete research.
        scoped_report = scoped_report
    else:
        scoped_report = validated_report
    claim_audit = audit_claim_sources(scoped_report, enriched_sources, scope)
    initial_unsupported = claim_audit.get("unsupported_count", 0)
    if repair:
        scoped_report, removed = remove_unsupported_claims(scoped_report, claim_audit)
    else:
        removed = 0
    final_claim_audit = audit_claim_sources(scoped_report, enriched_sources, scope)
    final_claim_audit["initial_unsupported_count"] = initial_unsupported
    final_claim_audit["removed_unsupported_claims"] = removed
    return scoped_report, {"fingerprint": scope.to_dict(), "scope": scope_audit, "claims": final_claim_audit}
