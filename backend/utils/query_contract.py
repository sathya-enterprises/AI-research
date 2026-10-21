"""Query-driven research contracts and evidence controls.

This module is deliberately provider- and renderer-neutral.  It turns a user
question into an explicit contract, but never treats a vocabulary hit as a
mandatory dimension by itself.  The existing report pipeline can consume the
serialisable contract alongside its legacy scope fingerprint while callers
migrate incrementally.
"""
from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Literal

Classification = Literal["required", "derived", "optional", "not_applicable"]
FreshnessStatus = Literal["current", "recent", "stale", "unknown", "not_applicable"]
Relationship = Literal["supports", "contradicts", "contextualizes"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z][a-z0-9+/-]{2,}", text.lower()))


@dataclass
class ResearchDimension:
    dimension_id: str
    name: str
    classification: Classification
    reason: str
    time_scope: str = ""
    freshness_requirement: str = "not_applicable"
    entities: list[str] = field(default_factory=list)
    required_metrics: list[str] = field(default_factory=list)
    required_comparisons: list[str] = field(default_factory=list)
    required_perspectives: list[str] = field(default_factory=list)
    preferred_source_roles: list[str] = field(default_factory=list)
    evidence_requirements: list[str] = field(default_factory=list)
    status: str = "planned"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ResearchContract:
    contract_id: str
    original_query: str
    entities: list[str]
    industries: list[str]
    geography: list[str]
    time_period: str
    current_as_of_required: bool
    required_dimensions: list[ResearchDimension]
    derived_dimensions: list[ResearchDimension]
    optional_dimensions: list[ResearchDimension]
    not_applicable_dimensions: list[ResearchDimension]
    hypotheses: list[str]
    questions: list[str]
    required_metrics: list[str]
    required_comparisons: list[str]
    required_perspectives: list[str]
    management_perspective: bool
    analyst_perspective: bool
    stopping_criteria: dict[str, Any]
    planning_confidence: str = "medium"
    clarification_required: bool = False
    ambiguity_reason: str = ""
    planner_mode: str = "fallback"
    created_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        for key in ("required_dimensions", "derived_dimensions", "optional_dimensions", "not_applicable_dimensions"):
            value[key] = [item.to_dict() for item in getattr(self, key)]
        return value

    @property
    def completeness_dimensions(self) -> list[ResearchDimension]:
        return self.required_dimensions


_METRIC_PATTERNS: dict[str, tuple[str, ...]] = {
    "revenue": ("revenue", "sales", "turnover"),
    "earnings": ("earnings", "profit", "ebitda", "eps"),
    "margins": ("margin", "profitability"),
    "demand": ("demand", "consumption", "volume", "orders"),
    "pricing": ("pricing", "price", "tariff", "arpu"),
    "capacity": ("capacity", "utilisation", "utilization", "throughput"),
    "cash_flow": ("cash flow", "cash generation", "free cash flow"),
    "subscriber_counts": ("subscriber", "users", "customers"),
    "regulatory_metric": ("regulatory metric", "capital requirement", "compliance ratio"),
    "capex": ("capex", "capital expenditure", "investment cycle"),
    "market_share": ("market share", "share"),
}


def _has(q: str, *phrases: str) -> bool:
    def present(phrase: str) -> bool:
        # Short tokens such as AI/EV/US must be word-bounded; substring
        # matching "ai" in "maintain" is exactly the keyword-taxonomy bug
        # this planner is intended to eliminate.
        if len(phrase) <= 3 or phrase.isalpha() and len(phrase) <= 4:
            return bool(re.search(r"\b" + re.escape(phrase) + r"\b", q))
        return phrase in q
    return any(present(p) for p in phrases)


def _dimension(name: str, classification: Classification, reason: str, q: str, *, metrics: Iterable[str] = (), roles: Iterable[str] = (), freshness: str = "not_applicable", comparisons: Iterable[str] = (), perspectives: Iterable[str] = ()) -> ResearchDimension:
    return ResearchDimension(
        dimension_id=re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_"),
        name=name,
        classification=classification,
        reason=reason,
        time_scope="current/as-of" if freshness == "high" else "query-specified",
        freshness_requirement=freshness,
        required_metrics=list(metrics),
        required_comparisons=list(comparisons),
        required_perspectives=list(perspectives),
        preferred_source_roles=list(roles),
        evidence_requirements=["at least one explicit evidence relationship"],
    )


def _freshness(q: str, metric: str) -> str:
    current_language = _has(q, "current", "latest", "today", "as of", "now", "recent", "q1", "q2", "q3", "q4")
    high_sensitivity = metric in {"pricing", "subscriber_counts", "earnings", "regulatory_metric", "revenue"}
    if current_language and high_sensitivity:
        return "high"
    if current_language:
        return "medium"
    if _has(q, "historical", "history", "2008", "2009", "cycle", "long-term"):
        return "low"
    return "not_applicable"


def plan_query(query: str) -> ResearchContract:
    """Build a conservative contract from semantic query signals.

    This is the deterministic fallback for a future model-backed planner. It
    never adds generic filler. Queries with no reliable research object are
    explicitly marked for clarification instead of receiving arbitrary
    required dimensions.
    """
    raw = re.sub(r"\s+", " ", str(query or "")).strip()
    q = raw.lower()
    entities = [x for x in re.findall(r"\b[A-Z][A-Za-z0-9&+.-]{2,}(?:\s+[A-Z][A-Za-z0-9&+.-]{2,}){0,2}", raw) if x.lower() not in {"what", "how", "compare", "assess", "analyze"}]
    geography = [x for x in ("india", "china", "united states", "us", "europe", "japan", "global") if x in q]
    time_period = ", ".join(re.findall(r"\b(?:19|20)\d{2}(?:[-/]\d{2,4})?\b", q)) or ("historical" if _has(q, "historical", "cycle", "history", "long-term") else ("current" if _has(q, "current", "latest", "today") else ""))
    current = bool(_has(q, "current", "latest", "today", "as of", "now", "recent"))
    required: list[ResearchDimension] = []
    derived: list[ResearchDimension] = []
    optional: list[ResearchDimension] = []
    not_applicable: list[ResearchDimension] = []

    def add(target: list[ResearchDimension], name: str, reason: str, *, metrics: Iterable[str] = (), roles: Iterable[str] = (), freshness: str | None = None, comparisons: Iterable[str] = (), perspectives: Iterable[str] = ()):
        target.append(_dimension(name, "required" if target is required else "derived" if target is derived else "optional", reason, q, metrics=metrics, roles=roles, freshness=freshness or _freshness(q, next(iter(metrics), "")), comparisons=comparisons, perspectives=perspectives))

    if _has(q, "earnings", "quarter", "financial performance", "profit", "revenue", "sales", "turnover"):
        add(required, "financial performance" if _has(q, "earnings", "quarter", "profit") else "revenue", "explicit financial question", metrics=("revenue", "earnings", "margins") if _has(q, "earnings", "quarter", "profit") else ("revenue",), roles=("company_filing", "earnings_release"), freshness=_freshness(q, "earnings" if _has(q, "earnings", "quarter", "profit") else "revenue"))
    if _has(q, "cement", "industry structure", "structural economics", "production economics"):
        add(required, "industry economics", "explicit industry/economic structure question", metrics=("demand", "costs", "margins"), roles=("government", "company_filing", "industry_data"))
    if _has(q, "historical", "history", "cycle") and (_has(q, "compare", "versus", " vs ") or len(re.findall(r"\b(?:19|20)\d{2}", q)) >= 2):
        add(required, "historical cycle comparison", "explicit multi-period historical comparison", roles=("government", "regulator", "company_filing"), comparisons=re.findall(r"\b(?:19|20)\d{2}(?:[-/]\d{2,4})?\b", q), freshness="low")
    if _has(q, "ai", "genai", "technology disruption", "ev", "disruption", "platform"):
        add(required, "technology mechanism", "explicit technology/disruption question", metrics=("adoption", "productivity"), roles=("company_presentation", "regulator", "institutional_research"))
    if _has(q, "demand", "consumer", "household", "spending"):
        add(required, "consumer demand", "explicit consumer-demand question", metrics=("demand",), roles=("government", "company_filing", "survey"))
    if _has(q, "lng", "energy economics", "fuel-price", "power costs", "renewable-power"):
        add(required, "energy economics", "explicit energy economics question", metrics=("pricing", "costs", "demand"), roles=("government", "regulator", "company_filing"))
    if _has(q, "real estate", "real-estate", "vacancy", "rents", "office"):
        add(required, "vacancy and rents", "explicit real-estate vacancy/rent question", metrics=("vacancy", "rents"), roles=("government", "property_registry", "market_data"))
        if _has(q, "demand", "supply"):
            add(required, "property demand and supply", "explicit property demand/supply question", metrics=("demand", "supply"), roles=("government", "market_data"))
    if _has(q, "regulation", "regulatory", "policy", "law", "mandate", "emissions"):
        add(required, "policy impact", "explicit policy or regulatory impact question", metrics=("regulatory_metric",), roles=("regulator", "government"), freshness=_freshness(q, "regulatory_metric"))
    if _has(q, "supply chain", "supplier", "sourcing", "china+1", "manufacturing"):
        add(required, "supply-chain structure", "explicit supply-chain question", metrics=("capacity", "costs"), roles=("company_filing", "government", "trade_data"))
    if _has(q, "china+1"):
        add(required, "China+1 exposure", "explicit China+1 question", metrics=("sourcing", "capacity"), roles=("company_filing", "trade_data", "government"))
    if _has(q, "compare", "versus", " vs ", "cross-country", "between"):
        add(required, "comparison frame", "explicit comparison requires a like-for-like frame", roles=("government", "regulator", "company_filing"), comparisons=geography or ["query subjects"])
    if _has(q, "management", "analyst", "sell-side"):
        add(required, "management and analyst perspectives", "explicit perspective comparison", roles=("earnings_call", "company_presentation", "institutional_research"), perspectives=("management", "analyst"))
    if _has(q, "niche", "sparse evidence"):
        add(required, "evidence availability", "explicit sparse-evidence constraint", roles=("primary_data", "institutional_research"))
    if _has(q, "contradictory", "conflicting"):
        add(required, "evidence conflict", "explicit contradiction-resolution request", roles=("primary_data", "institutional_research"))
    if _has(q, "reported", "what was") and _has(q, "revenue", "number", "count") and not required:
        add(required, "reported fact", "narrow factual question", metrics=("reported_value",), roles=("company_filing", "government"), freshness=_freshness(q, "revenue"))

    if "technology mechanism" in {d.name for d in required} and _has(q, "genai", "it services", "software services") and not _has(q, "capacity"):
        add(derived, "capacity", "derived operational consequence of this technology/services question", metrics=("capacity",), roles=("company_filing",))
    if ("industry economics" in {d.name for d in required} or "energy economics" in {d.name for d in required}) and not _has(q, "cash flow", "free cash flow"):
        add(derived, "cash flow", "derived financial consequence of an economics question; not a completeness requirement", metrics=("cash_flow",), roles=("company_filing",))
    if _has(q, "software", "ai", "genai") and not _has(q, "telecom", "subscriber", "mobile", "wireless"):
        not_applicable.append(_dimension("ARPU/tariff economics", "not_applicable", "subscriber-tariff economics is outside the stated software/AI scope", q))
    if _has(q, "risk", "downside", "threat"):
        add(optional, "risk and uncertainty", "risk was explicitly requested as enrichment", roles=("company_filing", "institutional_research"))
    if _has(q, "competition", "competitive", "market share"):
        add(optional, "competitive context", "competitive context was explicitly requested", metrics=("market_share",), roles=("regulator", "company_filing", "market_data"))

    ambiguous = not required
    confidence = "low" if ambiguous else "high" if len(required) <= 3 else "medium"
    return ResearchContract(
        contract_id="contract-" + uuid.uuid4().hex[:16], original_query=raw,
        entities=entities, industries=[x for x in ("technology", "energy", "real estate", "automobile", "chemicals", "banking", "telecom") if x in q], geography=geography,
        time_period=time_period, current_as_of_required=current, required_dimensions=required, derived_dimensions=derived, optional_dimensions=optional, not_applicable_dimensions=not_applicable,
        hypotheses=[], questions=[raw] if not ambiguous else [], required_metrics=sorted({m for d in required for m in d.required_metrics}), required_comparisons=sorted({x for d in required for x in d.required_comparisons}), required_perspectives=sorted({p for d in required for p in d.required_perspectives}),
        management_perspective="management" in q, analyst_perspective="analyst" in q or "sell-side" in q,
        stopping_criteria={"all_required_dimensions": True, "no_fixed_page_or_word_floor": True, "bounded_provider_retries": True},
        planning_confidence=confidence, clarification_required=ambiguous, ambiguity_reason="No explicit entity, metric, domain, comparison, or analytical objective was reliably identified." if ambiguous else "",
    )


def evaluate_completeness(contract: ResearchContract, evidence_by_dimension: dict[str, list[dict[str, Any]]], claims: Iterable[dict[str, Any]] = ()) -> dict[str, Any]:
    """Evaluate required dimensions only, with explicit relationship checks."""
    claim_map = {str(c.get("claim_id")): c for c in claims}
    rows = []
    for dim in contract.required_dimensions:
        evidence = evidence_by_dimension.get(dim.dimension_id, [])
        valid = [e for e in evidence if e.get("evidence_id") and any(str(r.get("relationship", "")).lower() in {"supports", "contextualizes", "contradicts"} for r in e.get("relationships", []))]
        rows.append({"dimension_id": dim.dimension_id, "classification": dim.classification, "status": "covered" if valid else "uncovered", "evidence_ids": [e.get("evidence_id") for e in valid], "reason": "explicit claim/evidence relationship present" if valid else "no valid relationship"})
    statuses = {r["status"] for r in rows}
    overall = "PASS" if rows and statuses == {"covered"} else "PARTIAL" if "covered" in statuses else "UNCOVERED"
    return {"status": overall, "required_dimensions": rows, "ignored_non_required": [d.dimension_id for d in contract.derived_dimensions + contract.optional_dimensions + contract.not_applicable_dimensions]}


def validate_contract(contract: ResearchContract) -> list[str]:
    """Application-side validation for model or fallback planner output."""
    errors: list[str] = []
    groups = [contract.required_dimensions, contract.derived_dimensions, contract.optional_dimensions, contract.not_applicable_dimensions]
    all_dimensions = [d for group in groups for d in group]
    ids = [d.dimension_id for d in all_dimensions]
    if len(ids) != len(set(ids)):
        errors.append("duplicate dimension_id")
    allowed = {"company_filing", "earnings_release", "company_presentation", "earnings_call", "regulator", "government", "institutional_research", "primary_data", "industry_data", "trade_data", "property_registry", "market_data", "survey", "academic", "expert_interview", "newswire"}
    for d in all_dimensions:
        if d.classification not in {"required", "derived", "optional", "not_applicable"}:
            errors.append(f"invalid classification: {d.dimension_id}")
        if d.classification == "required" and not d.reason.strip():
            errors.append(f"required dimension missing reason: {d.dimension_id}")
        if any(role not in allowed for role in d.preferred_source_roles):
            errors.append(f"unsupported source role: {d.dimension_id}")
    return errors


PRIMARY_PLANNER_INSTRUCTIONS = """Return JSON only for this user research query. Propose a semantic ResearchContract, not a fixed report template. Include required_dimensions, derived_dimensions, optional_dimensions, not_applicable_dimensions, hypotheses, required_metrics, required_comparisons, required_perspectives, management_perspective, analyst_perspective. Every dimension needs dimension_id, name, classification, reason, freshness_requirement, preferred_source_roles, required_metrics, required_comparisons, required_perspectives, evidence_requirements. Use required only when explicit or logically necessary; do not add generic risk, competition, cash flow, ARPU, or other filler. If ambiguous, return clarification_required=true, planning_confidence=low, and no arbitrary required dimensions."""


def _contract_from_primary_proposal(query: str, proposal: dict[str, Any]) -> ResearchContract:
    raw = re.sub(r"\s+", " ", str(query or "")).strip()
    groups: dict[str, list[ResearchDimension]] = {"required": [], "derived": [], "optional": [], "not_applicable": []}
    for key, target in (("required_dimensions", "required"), ("derived_dimensions", "derived"), ("optional_dimensions", "optional"), ("not_applicable_dimensions", "not_applicable")):
        for item in proposal.get(key) or []:
            if not isinstance(item, dict):
                continue
            classification = str(item.get("classification") or target).lower()
            if classification not in groups:
                classification = target
            name = str(item.get("name") or item.get("dimension_id") or "").strip()
            if not name:
                continue
            groups[classification].append(ResearchDimension(
                dimension_id=re.sub(r"[^a-z0-9]+", "_", str(item.get("dimension_id") or name).lower()).strip("_"),
                name=name, classification=classification, reason=str(item.get("reason") or "").strip(),
                time_scope=str(item.get("time_scope") or "query-specified"), freshness_requirement=str(item.get("freshness_requirement") or "not_applicable"),
                entities=list(item.get("entities") or []), required_metrics=list(item.get("required_metrics") or []), required_comparisons=list(item.get("required_comparisons") or []), required_perspectives=list(item.get("required_perspectives") or []), preferred_source_roles=list(item.get("preferred_source_roles") or []), evidence_requirements=list(item.get("evidence_requirements") or []), status="planned"))
    return ResearchContract(
        contract_id="contract-" + uuid.uuid4().hex[:16], original_query=raw,
        entities=list(proposal.get("entities") or []), industries=list(proposal.get("industries") or []), geography=list(proposal.get("geography") or []), time_period=str(proposal.get("time_period") or ""), current_as_of_required=bool(proposal.get("current_as_of_required", False)),
        required_dimensions=groups["required"], derived_dimensions=groups["derived"], optional_dimensions=groups["optional"], not_applicable_dimensions=groups["not_applicable"], hypotheses=list(proposal.get("hypotheses") or []), questions=list(proposal.get("questions") or [raw]), required_metrics=list(proposal.get("required_metrics") or []), required_comparisons=list(proposal.get("required_comparisons") or []), required_perspectives=list(proposal.get("required_perspectives") or []), management_perspective=bool(proposal.get("management_perspective", False)), analyst_perspective=bool(proposal.get("analyst_perspective", False)), stopping_criteria=dict(proposal.get("stopping_criteria") or {"all_required_dimensions": True, "bounded_provider_retries": True}), planning_confidence=str(proposal.get("planning_confidence") or "medium"), clarification_required=bool(proposal.get("clarification_required", False)), ambiguity_reason=str(proposal.get("ambiguity_reason") or ""), planner_mode="primary_llm")


async def plan_query_primary(query: str, llm_planner: Any = None) -> ResearchContract:
    """Use a validated LLM proposal when available; otherwise fallback safely."""
    fallback = plan_query(query)
    if llm_planner is None:
        return fallback
    try:
        proposal = await llm_planner(query, PRIMARY_PLANNER_INSTRUCTIONS)
        if not isinstance(proposal, dict):
            return fallback
        candidate = _contract_from_primary_proposal(query, proposal)
        errors = validate_contract(candidate)
        if errors or (not candidate.required_dimensions and not candidate.clarification_required):
            return fallback
        return candidate
    except Exception:
        return fallback


def contract_dimension_terms(dimension: ResearchDimension) -> tuple[str, ...]:
    """Return retrieval terms owned by a contract dimension, not a taxonomy."""
    values = [dimension.name, *dimension.required_metrics, *dimension.required_comparisons, *dimension.required_perspectives]
    terms: list[str] = []
    for value in values:
        for token in re.findall(r"[a-z0-9][a-z0-9+/-]{2,}", str(value).lower()):
            if token not in terms:
                terms.append(token)
    return tuple(terms)


@dataclass
class ClaimEvidenceRelation:
    claim_id: str
    evidence_id: str
    relationship: Relationship
    rationale: str
    created_at: str = field(default_factory=_now)


def adjudicate_claim(claim: dict[str, Any], relations: Iterable[ClaimEvidenceRelation], evidence: dict[str, dict[str, Any]]) -> str:
    rels = [r for r in relations if r.claim_id == claim.get("claim_id")]
    support = [r for r in rels if r.relationship == "supports" and r.evidence_id in evidence]
    contra = [r for r in rels if r.relationship == "contradicts" and r.evidence_id in evidence]
    if support and contra: return "contested"
    if support: return "supported"
    if contra: return "contradicted"
    return "unsupported"


def adjudicate_claim_evidence(claim_id: str, claim_text: str, cited_source_ids: Iterable[str], evidence: dict[str, dict[str, Any]], semantic_assessor: Any = None) -> tuple[str, list[ClaimEvidenceRelation]]:
    """Filter candidates deterministically, with an optional semantic hook.

    The current production route leaves ``semantic_assessor`` unset. A future
    model/provider may assess only these filtered candidates and return one of
    supports/contradicts/contextualizes; it cannot promote unrelated evidence.
    """
    stop = {"the", "and", "for", "with", "that", "this", "from", "were", "was", "into", "reported"}
    claim_terms = {x for x in re.findall(r"[a-z0-9]{4,}", claim_text.lower()) if x not in stop}
    cited = {str(x) for x in cited_source_ids}
    relations: list[ClaimEvidenceRelation] = []
    for evidence_id, item in evidence.items():
        if cited and str(item.get("source_id")) not in cited:
            continue
        text = str(item.get("text") or "").lower()
        overlap = {x for x in claim_terms if x in text}
        if len(overlap) < 2 and not any(str(n) in text for n in re.findall(r"\b\d+(?:\.\d+)?%?\b", claim_text)):
            continue
        semantic_hint = semantic_assessor(claim_text, item) if semantic_assessor else None
        hint = str(semantic_hint or item.get("relationship_hint") or "").lower()
        if semantic_assessor and semantic_hint and hint not in {"supports", "contradicts", "contextualizes"}:
            continue
        relationship: Relationship = "contradicts" if hint == "contradicts" else "contextualizes" if hint == "contextualizes" else "supports"
        relations.append(ClaimEvidenceRelation(claim_id, evidence_id, relationship, f"claim/evidence lexical overlap: {', '.join(sorted(overlap)[:6])}"))
    status = adjudicate_claim({"claim_id": claim_id}, relations, evidence)
    return status, relations


def freshness_for(*, data_as_of: str | None, published_at: str | None, retrieved_at: str | None, requirement: str, now: datetime | None = None) -> dict[str, Any]:
    if requirement == "not_applicable": return {"status": "not_applicable", "reason": "dimension has no freshness requirement"}
    if not data_as_of and not published_at and not retrieved_at: return {"status": "unknown", "reason": "no source date is available"}
    if requirement == "low": return {"status": "recent", "reason": "historical/structural evidence remains valid for its stated period"}
    # Date parsing is intentionally conservative: unparseable dates are not
    # treated as current.
    try:
        stamp = datetime.fromisoformat((data_as_of or published_at or retrieved_at or "").replace("Z", "+00:00"))
        age = (now or datetime.now(timezone.utc)) - stamp
    except (ValueError, TypeError):
        return {"status": "unknown", "reason": "source date cannot be parsed"}
    days = age.total_seconds() / 86400
    limit = 30 if requirement == "high" else 90
    return {"status": "current" if days <= limit else "stale", "age_days": round(days, 1), "reason": f"{requirement}-sensitivity threshold of {limit} days"}


@dataclass
class TargetedRefreshTask:
    refresh_task_id: str
    research_run_id: str
    dimension_id: str
    claim_ids: list[str]
    reason: str
    freshness_requirement: str
    preferred_source_roles: list[str]
    query: str
    status: str = "queued"
    parent_evidence_ids: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=_now)


def plan_targeted_refresh(contract: ResearchContract, findings: Iterable[dict[str, Any]], research_run_id: str) -> list[TargetedRefreshTask]:
    by_id = {d.dimension_id: d for d in contract.required_dimensions + contract.derived_dimensions + contract.optional_dimensions}
    tasks = []
    for finding in findings:
        dim = by_id.get(str(finding.get("dimension_id")))
        if not dim or not finding.get("refresh_required") or dim.classification != "required": continue
        task_query = f"targeted research for {dim.name}: {' '.join(dim.required_metrics or dim.required_comparisons or [dim.name])}"
        tasks.append(TargetedRefreshTask("refresh-" + uuid.uuid4().hex[:12], research_run_id, dim.dimension_id, list(finding.get("claim_ids") or []), str(finding.get("reason") or "missing_required_evidence"), dim.freshness_requirement, dim.preferred_source_roles, task_query, parent_evidence_ids=list(finding.get("evidence_ids") or [])))
    return tasks


def chart_data_point_provenance(point: dict[str, Any], evidence: dict[str, dict[str, Any]], claims: dict[str, dict[str, Any]]) -> dict[str, Any]:
    evidence_ids = list(point.get("evidence_ids") or [])
    claim_ids = list(point.get("claim_ids") or [])
    return {**point, "evidence_ids": evidence_ids, "claim_ids": claim_ids, "source_ids": sorted({str(evidence[eid].get("source_version_id") or evidence[eid].get("source_id")) for eid in evidence_ids if eid in evidence}), "provenance_complete": bool(evidence_ids and all(eid in evidence for eid in evidence_ids) and all(cid in claims for cid in claim_ids))}


def source_hygiene(sources: Iterable[dict[str, Any]], claim_relations: Iterable[ClaimEvidenceRelation], evidence: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    used = {r.evidence_id for r in claim_relations}
    return [{"source_id": s.get("source_id"), "issue": "retained_source_supports_no_claim", "severity": "warning"} for s in sources if s.get("stage") in {"retained", "directly_cited"} and not any(e.get("source_id") == s.get("source_id") for eid, e in evidence.items() if eid in used)]


def validate_editorial(content: dict[str, Any]) -> list[dict[str, Any]]:
    findings = []
    title = str(content.get("title") or "").strip()
    body = str(content.get("report") or "")
    if not title or title.lower() == "research report?": findings.append({"type": "generic_or_empty_title"})
    if "Research Report?" in body: findings.append({"type": "placeholder_leakage"})
    if re.search(r"^##?\s*$", body, re.M): findings.append({"type": "empty_heading"})
    charts = content.get("charts") or []
    for i, chart in enumerate(charts):
        if not any(str(chart.get("id") or chart.get("placeholder") or "") in body for _ in [0]): findings.append({"type": "orphan_chart", "index": i})
    return findings


def calibrate_thesis(claim: dict[str, Any], relations: Iterable[ClaimEvidenceRelation], evidence: dict[str, dict[str, Any]]) -> str:
    status = adjudicate_claim(claim, relations, evidence)
    qualities = [str(evidence[r.evidence_id].get("quality") or "unknown") for r in relations if r.relationship == "supports" and r.evidence_id in evidence]
    if status == "contested": return "contested"
    if status != "supported": return "insufficiently_supported"
    return "well_supported" if qualities and all(x in {"strong", "primary"} for x in qualities) else "supported_with_qualification"


def safe_external_content(text: str) -> dict[str, Any]:
    """Mark retrieved text as data; no instruction-bearing fields are emitted."""
    return {"content": str(text or ""), "trust_boundary": "untrusted_external_data", "instructions": [], "tool_calls": [], "authorized": False}


def stable_hash(value: Any) -> str:
    return hashlib.sha256(repr(value).encode("utf-8", "ignore")).hexdigest()
