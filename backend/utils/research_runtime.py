"""Canonical runtime orchestration for targeted refreshes.

Provider access is injected as a callback so production workers and offline
tests share the same state transitions without making network calls in tests.
"""
from __future__ import annotations

import hashlib
import inspect
from datetime import datetime, timezone
from typing import Any, Callable

from utils.query_contract import TargetedRefreshTask, ResearchContract, adjudicate_claim_evidence, freshness_for, plan_targeted_refresh


def assess_refresh_findings(contract: ResearchContract, evidence_records: dict[str, dict[str, Any]], claims: list[dict[str, Any]], now: datetime | None = None) -> list[dict[str, Any]]:
    """Turn required-dimension freshness/evidence findings into refresh inputs."""
    findings = []
    for dimension in contract.required_dimensions:
        rows = [row for row in evidence_records.values() if str(row.get("dimension_id") or "") == dimension.dimension_id]
        claim_ids = [str(claim.get("claim_id")) for claim in claims if str(claim.get("dimension") or "") == dimension.dimension_id]
        if not rows:
            findings.append({"dimension_id": dimension.dimension_id, "refresh_required": True, "reason": "missing_required_evidence", "claim_ids": claim_ids, "evidence_ids": []})
            continue
        freshness = [freshness_for(data_as_of=row.get("data_as_of"), published_at=row.get("published_at"), retrieved_at=row.get("retrieved_at"), requirement=dimension.freshness_requirement, now=now) for row in rows]
        stale = [row for row, decision in zip(rows, freshness) if decision.get("status") in {"stale", "unknown"}]
        if stale:
            findings.append({"dimension_id": dimension.dimension_id, "refresh_required": True, "reason": "stale_current_metric" if any(x.get("status") == "stale" for x in freshness) else "provenance_gap", "claim_ids": claim_ids, "evidence_ids": [str(row.get("evidence_id")) for row in stale]})
    return findings


def acquire_evidence(store: Any, run_id: str, source: dict[str, Any], dimension_id: str) -> dict[str, Any]:
    source = dict(source)
    source_id = str(source.get("source_id") or source.get("id") or "")
    version_id = store.save_source_version(run_id, source)
    source["source_version_id"] = version_id
    if source_id:
        store.upsert_source(run_id, {**source, "source_id": source_id}, "retained")
    text = str(source.get("fullContent") or source.get("text") or source.get("snippet") or "").strip()
    document_id = store.save_document(run_id, source_id, text, metadata={"dimension_id": dimension_id, "source_version_id": version_id})
    chunk_ids = store.save_chunks(run_id, source_id, document_id, text, {"dimension_id": dimension_id, "source_version_id": version_id})
    evidence_ids = []
    for index, chunk_id in enumerate(chunk_ids):
        chunk_text = text[index * 1600:index * 1600 + 1800] or text[:1800]
        evidence_ids.append(store.save_evidence(run_id, source_id, chunk_id, chunk_text, topic=dimension_id, provenance={"document_id": document_id, "source_version_id": version_id}, source_version_id=version_id))
    return {"source_id": source_id, "source_version_id": version_id, "document_id": document_id, "chunk_ids": chunk_ids, "evidence_ids": evidence_ids}


def execute_targeted_refresh(
    task: TargetedRefreshTask,
    contract: ResearchContract,
    store: Any,
    provider: Callable[[str, list[str]], list[dict[str, Any]]],
    claims: list[dict[str, Any]],
    claim_text_by_id: dict[str, str],
    parent_revision_id: str | None = None,
) -> dict[str, Any]:
    """Run one bounded refresh task; unaffected dimensions are not touched."""
    if task.dimension_id not in {d.dimension_id for d in contract.required_dimensions}:
        raise ValueError("targeted refresh may only execute for a required contract dimension")
    if hasattr(store, "save_refresh_task"):
        store.save_refresh_task({"refresh_task_id": task.refresh_task_id, "research_run_id": task.research_run_id, "dimension_id": task.dimension_id, "claim_ids": task.claim_ids, "reason": task.reason, "status": "running", "query": task.query})
    candidates = provider(task.query, task.preferred_source_roles)
    acquired = [acquire_evidence(store, task.research_run_id, source, task.dimension_id) for source in candidates]
    evidence_rows = store.evidence_snapshot(task.research_run_id)
    changed_claims = []
    for claim_id in task.claim_ids:
        text = claim_text_by_id.get(claim_id, "")
        cited = {str(row.get("source_id")) for row in evidence_rows.values()}
        status, relations = adjudicate_claim_evidence(claim_id, text, cited, evidence_rows)
        for relation in relations:
            store.relate_claim_evidence(relation.claim_id, relation.evidence_id, relation.relationship)
        changed_claims.append({"claim_id": claim_id, "support_status": status, "relation_count": len(relations)})
    content_hash = hashlib.sha256(repr((task.refresh_task_id, acquired, changed_claims)).encode()).hexdigest()
    revision_id = store.create_report_revision(task.research_run_id, content_hash, parent_revision_id=parent_revision_id, qa_status="recomposition_required", artifact_identity=f"report:{task.research_run_id}:{content_hash[:16]}")
    artifact_id = store.save_artifact(revision_id, "report-json-refresh", f"report:{task.research_run_id}:{content_hash[:16]}", content_hash) if hasattr(store, "save_artifact") else ""
    task.status = "completed"
    if hasattr(store, "update_refresh_task"):
        store.update_refresh_task(task.refresh_task_id, "completed", {"acquired": acquired, "changed_claims": changed_claims, "revision_id": revision_id})
    return {"refresh_task_id": task.refresh_task_id, "sources_acquired": acquired, "changed_claims": changed_claims, "unaffected_dimensions": [d.dimension_id for d in contract.required_dimensions if d.dimension_id != task.dimension_id], "full_research_restarted": False, "revision_id": revision_id, "artifact_id": artifact_id, "artifact_identity": f"report:{task.research_run_id}:{content_hash[:16]}", "parent_revision_id": parent_revision_id, "recomposition_required": True}


async def orchestrate_targeted_refresh(contract: ResearchContract, run_id: str, evidence_records: dict[str, dict[str, Any]], claims: list[dict[str, Any]], claim_text_by_id: dict[str, str], store: Any, provider: Callable[[str, list[str]], Any], parent_revision_id: str | None = None) -> dict[str, Any]:
    """Canonical refresh lifecycle; provider may be sync or async."""
    findings = assess_refresh_findings(contract, evidence_records, claims)
    tasks = plan_targeted_refresh(contract, findings, run_id)
    results = []
    current_parent = parent_revision_id
    for task in tasks:
        async def invoke(query: str, roles: list[str]):
            value = provider(query, roles)
            return await value if inspect.isawaitable(value) else value
        candidates = await invoke(task.query, task.preferred_source_roles)
        result = execute_targeted_refresh(task, contract, store, lambda _q, _roles, rows=candidates: rows, claims, claim_text_by_id, parent_revision_id=current_parent)
        results.append(result)
        current_parent = result["revision_id"]
    return {"findings": findings, "tasks": tasks, "results": results, "full_research_restarted": False, "recomposition_required": bool(results)}
