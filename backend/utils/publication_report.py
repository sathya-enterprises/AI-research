"""Publication report (Stage F): one file per run, every publication gate with PASS/FAIL and the
exact items that failed it, so "draft" is never a mystery.

Built from what report.py already computed (final deterministic audits, the publication outcome)
and the V2 pipeline meta (number policy, evidence-check loop, chart funnel). Nothing here decides
publication; it only reports the decision and its reasons. Values are report text and source ids;
no credentials or request data are included.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

log = logging.getLogger("publication_report")

_STRUCTURAL_KEYS = ("controlMarkerCount", "danglingChartRefs", "danglingImageRefs", "unresolvedCitationIds",
                    "missingSourceRecords", "emptyChartIndices", "untitledChartIndices", "incompleteNumericFragments")
_CONSISTENCY_KEYS = ("duplicateTableCount", "duplicateObservations", "orphanedChartMentions",
                     "crossMetricProjectionBasisIssues", "timeframeLabelMismatches", "currencyConversionAnomalies")
_CHART_KEYS = ("chartEvidenceFailures", "chartTableVerificationConflicts", "chartEvidenceGateFindings")


def _short(value, limit: int = 220) -> str:
    if isinstance(value, dict):
        claim = value.get("claim") or value.get("chartTitle") or value.get("title") or value.get("reason") or value.get("issue")
        extra = value.get("values") or value.get("unmatchedNumericClaims") or value.get("sourceIds") or value.get("sources")
        text = f"{claim}" + (f" — {', '.join(map(str, extra[:6]))}" if isinstance(extra, list) and extra else "")
        if not claim:
            text = json.dumps(value, ensure_ascii=False, default=str)
    else:
        text = str(value)
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _gate(name: str, items: list, *, note: str = "", info: list | None = None) -> dict:
    return {"gate": name, "status": "FAIL" if items else "PASS", "failingItems": [_short(i) for i in items[:40]],
            "failingCount": len(items), "note": note, "info": [_short(i) for i in (info or [])[:40]]}


def _as_list(value) -> list:
    if not value:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, (int, float)):
        return [f"count {value}"]
    return [value]


def build_publication_report(*, outcome: dict, audits: dict | None, v2_meta: dict | None = None,
                             question: str = "", tier: str = "", report_id: str = "", healing: dict | None = None,
                             pipeline_note: str = "") -> dict:
    audits = audits if isinstance(audits, dict) else {}
    meta = v2_meta if isinstance(v2_meta, dict) else {}
    publication = audits.get("publicationAudit") if isinstance(audits.get("publicationAudit"), dict) else {}
    evidence = audits.get("sourceEvidenceAudit") if isinstance(audits.get("sourceEvidenceAudit"), dict) else {}
    scope = audits.get("scopeCoverageAudit") if isinstance(audits.get("scopeCoverageAudit"), dict) else {}
    policy = meta.get("numberPolicy") or {}
    check = meta.get("evidenceCheck") or {}
    gates = []
    if meta:
        gates.append(_gate("V2 number policy (fact-sheet lookup)", list(policy.get("unresolvedTableValues") or []),
                           note=(f"{len(policy.get('sentencesRemoved') or [])} sentence(s)/row(s) removed, "
                                 f"{len(policy.get('numbersRewritten') or [])} number(s) rewritten, "
                                 f"{len(policy.get('calculatedLabelled') or [])} calculated in code"),
                           info=[f"removed: {r.get('reason', '')} | {r.get('sentence', '')}" for r in policy.get("sentencesRemoved") or []]
                           + [f"rewritten: {r.get('number')} — {r.get('change')}" for r in policy.get("numbersRewritten") or []]))
        gates.append(_gate("V2 evidence check (rewrite-or-remove loop, report.py's check)", list(check.get("stillFlagged") or []),
                           note=(f"{len(check.get('removedClaims') or [])} claim(s) removed, "
                                 f"{len(check.get('rewrittenClaims') or [])} rewritten, "
                                 f"{len(check.get('factSheetOverrides') or [])} report.py flag(s) cleared by the fact sheet"),
                           info=[f"{a.get('action', 'removed')}: {a.get('reason', '')} | {a.get('claim', '')}"
                                 for a in (check.get("removedClaims") or []) + (check.get("rewrittenClaims") or [])]
                           + [f"cleared: {o.get('claim', '')} — {', '.join(map(str, o.get('sources') or []))}"
                              for o in check.get("factSheetOverrides") or []]))
    gates.append(_gate("Source evidence (report.py final check)", list(evidence.get("mismatchedCitationGroups") or []),
                       note="each numeric claim must be supported by a source cited with it",
                       info=[f"cleared by the fact sheet: {o.get('claim', '')}" for o in evidence.get("factSheetOverrides") or []]))
    gates.append(_gate("Chart evidence", [i for k in _CHART_KEYS for i in _as_list(publication.get(k))]))
    gates.append(_gate("Report structure", [f"{k}: {_short(publication.get(k), 160)}" for k in _STRUCTURAL_KEYS if publication.get(k)]))
    gates.append(_gate("Consistency", [f"{k}: {_short(publication.get(k), 160)}" for k in _CONSISTENCY_KEYS if publication.get(k)]))
    gates.append(_gate("Scope coverage (soft: qualified, never blocks)", list(scope.get("flags") or []) if scope.get("blocking") else [],
                       info=list(scope.get("softenedFlags") or scope.get("flags") or []) if not scope.get("blocking") else []))
    charts = meta.get("charts") or {}
    funnel = meta.get("chartFunnel") or {}
    return {
        "reportId": report_id, "question": question, "tier": tier,
        "outcome": str((outcome or {}).get("outcome") or ""),
        "failedStage": (outcome or {}).get("failedStage") or "",
        "failureSummary": [_short(s) for s in (outcome or {}).get("failureSummary") or []],
        "gates": gates,
        "charts": {"candidates": charts.get("candidates"), "used": charts.get("used"),
                   "droppedBeforeWriting": len(charts.get("droppedBeforeWriting") or []),
                   "droppedAfterWriting": len(charts.get("droppedAfterWriting") or []),
                   "droppedAfterAssembly": len(charts.get("droppedAfterAssembly") or []), **({"funnel": funnel} if funnel else {})},
        "coverage": meta.get("coverage") or {},
        "selfHealing": healing or {},
        "deadline": meta.get("deadline") or {},
        "pipelineNote": pipeline_note,
    }


def render_markdown(report: dict) -> str:
    lines = [f"# Publication report — {report.get('outcome', '?').upper()}", ""]
    if report.get("question"):
        lines += [f"Question: {report['question']}", ""]
    lines += [f"Tier: {report.get('tier') or '?'} | report id: {report.get('reportId') or '?'}", ""]
    if report.get("pipelineNote"):
        lines += [f"**PIPELINE WARNING:** {report['pipelineNote']}", ""]
    if report.get("failedStage"):
        lines += [f"Blocked at: {report['failedStage']}", ""]
    lines += ["| Gate | Status | Failing items |", "|---|---|---|"]
    for gate in report.get("gates") or []:
        lines.append(f"| {gate['gate']} | {gate['status']} | {gate['failingCount']} |")
    for gate in report.get("gates") or []:
        lines += ["", f"## {gate['gate']}: {gate['status']}"]
        if gate.get("note"):
            lines.append(gate["note"])
        for item in gate.get("failingItems") or []:
            lines.append(f"- FAIL: {item}")
        if gate.get("info"):
            lines.append("")
            lines.append("Logged actions:")
            lines += [f"- {i}" for i in gate["info"]]
    funnel = (report.get("charts") or {}).get("funnel")
    if funnel:
        lines += ["", "## Chart funnel"]
        lines += [f"- {k}: {v}" for k, v in funnel.items() if not isinstance(v, (list, dict))]
        for drop in funnel.get("drops") or []:
            lines.append(f"- dropped: {_short(drop, 200)}")
    deadline = report.get("deadline") or {}
    if deadline.get("waves"):
        lines += ["", "## Runtime deadline (in-depth: target 40 min, hard stop 50 min)"]
        lines += [f"- sections {w['sections']}: {w['mode']} ({w['seconds']} s)" for w in deadline["waves"]]
        lines.append(f"- extension calls skipped: {deadline.get('extensionsSkipped', 0)}; written concisely: "
                     f"{', '.join(deadline.get('concise') or []) or 'none'}; transitions call skipped: "
                     f"{'yes' if deadline.get('transitionsSkipped') else 'no'}")
    healing = report.get("selfHealing") or {}
    if healing:
        from utils.gate_healing import gate_table
        lines += ["", "## Self-healing gates", "", "| Gate | Blocking | Automatic remedy |", "|---|---|---|"]
        lines += [f"| {g['gate']} | {'hard (blocks)' if g['blocking'] else 'soft (qualifier)'} | {g['remedy']} |" for g in gate_table()]
        lines += ["", f"Rounds: {healing.get('rounds', 0)} | removed: {healing.get('removed', 0)} | rewritten: "
                      f"{healing.get('rewritten', 0)} | qualified: {healing.get('qualified', 0)} | still failing: "
                      f"{', '.join(healing.get('stillFailing') or []) or 'none'}"]
        for action in healing.get("actions") or []:
            lines.append(f"- [{action.get('gate')}] {action.get('action')}: {_short(action.get('detail'), 220)}")
        for q in healing.get("qualifiers") or []:
            lines.append(f"- qualifier shown in the report: {_short(q, 220)}")
    coverage = report.get("coverage") or {}
    if coverage.get("note"):
        lines += ["", "## Coverage note", coverage["note"]]
    return "\n".join(lines).strip() + "\n"


def save_publication_report(report: dict, capture_path: str | None, fallback_dir: Path) -> str | None:
    """Write <capture>.publication_report.{json,md} next to the research capture (or under fallback_dir)."""
    try:
        if capture_path:
            base = Path(capture_path)
            stem = base.with_suffix("")
            json_path, md_path = Path(f"{stem}.publication_report.json"), Path(f"{stem}.publication_report.md")
        else:
            fallback_dir.mkdir(parents=True, exist_ok=True)
            name = re.sub(r"[^A-Za-z0-9_-]", "", str(report.get("reportId") or "report"))[:40] or "report"
            json_path, md_path = fallback_dir / f"{name}.publication_report.json", fallback_dir / f"{name}.publication_report.md"
        json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        md_path.write_text(render_markdown(report), encoding="utf-8")
        return str(md_path)
    except OSError as exc:
        log.warning("Publication report could not be saved: %s", type(exc).__name__)
        return None
