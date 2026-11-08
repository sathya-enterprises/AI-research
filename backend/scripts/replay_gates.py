"""Replay every publication gate (and the self-healing loop) over saved live captures, offline.

For each run directory with a status_final.json (the delivered result) and a research capture:
  * findings BEFORE: the hard gates and scope flags of the saved final report, re-run with the
    current gate code;
  * findings AFTER: the same gates after utils.gate_healing.heal (rewrite / remove / qualify);
  * would publish: no hard finding left;
  * claims removed / rewritten / qualified, and sections left thin (< 150 words, or losing more
    than 30% of their words to removals).
No LLM call and no network: the evidence comes from the capture.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’-]*")


def _capture_for(run: Path) -> Path | None:
    if (run / "capture.json").exists():
        return run / "capture.json"
    caps = sorted(p for p in (run.parent / "captures").glob("research_capture_*.json") if "publication" not in p.name) \
        if (run.parent / "captures").exists() else []
    return caps[-1] if caps else None


def _sections(text: str) -> dict[str, int]:
    out, title = {}, "front"
    for line in text.splitlines():
        m = re.match(r"^##\s+(.+)$", line)
        if m:
            title = m.group(1).strip()
            continue
        out[title] = out.get(title, 0) + len(WORD_RE.findall(re.sub(r"\[S[\d.,\s;S]+\]", " ", line)))
    return out


def replay_run(run: Path) -> dict:
    from routes import report as R
    from utils.evidence_store import store_from_passages
    from utils.gate_healing import hard_findings, heal

    final = json.loads((run / "status_final.json").read_text(encoding="utf-8"))
    result = final.get("result") or {}
    cap_path = _capture_for(run)
    cap = json.loads(cap_path.read_text(encoding="utf-8")) if cap_path else {}
    stages = cap.get("stages") or {}
    question = str(result.get("question") or cap.get("question") or (cap.get("request") or {}).get("question") or "")
    tier = str(result.get("reportTier") or cap.get("reportTier") or run.name)
    manifest = [m for m in (result.get("sources") or []) if isinstance(m, dict)]
    enriched = [r for r in (((stages.get("sources_after_article_fetch") or {}).get("data") or {}).get("sources") or []) if isinstance(r, dict)]
    passages = ((stages.get("evidence_store") or {}).get("data") or {}).get("passages") or {}
    store = store_from_passages(passages) if passages else None
    if store is not None:
        # Captures before Stage G redacted the fetched market datasets; rebuild the quarterly matrix
        # from the captured rows (the exact rows the live run fetched).
        from scripts.eval_report import restore_fetched_dataset
        restore_fetched_dataset(store, stages, manifest)
        texts: dict[str, list[str]] = {}
        for p in store.passages:
            texts.setdefault(p.source_id, []).append(p.text)
        by_url = {str(r.get("url") or "").rstrip("/"): r for r in enriched}
        merged = []
        for m in manifest:
            sid = str(m.get("id") or "").upper()
            f = by_url.get(str(m.get("url") or "").rstrip("/"), {})
            body = "\n\n".join([str(f.get("fullContent") or ""), *texts.get(sid, [])]).strip()
            merged.append({**f, **m, "id": sid, **({"fullContent": body, "pageContent": body} if body else {})})
        enriched = merged or enriched
    text = str(result.get("report") or "")
    charts = [c for c in (result.get("charts") or []) if isinstance(c, dict)]
    images = list(result.get("images") or [])
    from utils.chart_evidence_audit import trust_validated_charts
    trust_validated_charts(charts if store is not None else [])

    def rerun(t, chs):
        srcs = R._attach_audit_source_evidence(manifest, enriched, t)
        return {"chartDensity": R._chart_density_audit(chs, tier),
                "publicationAudit": R.audit_report_publication(t, chs, images, srcs, question=question),
                "sourceEvidenceAudit": R._source_evidence_quality_audit(srcs),
                "structure": R.audit_report_structure(t),
                "scopeCoverageAudit": R.audit_requested_report_scope(question, t, srcs)}

    before = rerun(text, charts)
    before_hard = hard_findings(before)
    before_scope = list((before.get("scopeCoverageAudit") or {}).get("flags") or []) if (before.get("scopeCoverageAudit") or {}).get("blocking") else []
    groups_before = len((before.get("sourceEvidenceAudit") or {}).get("mismatchedCitationGroups") or [])
    words_before = _sections(text)
    healed, charts_after, after, summary = heal(text, charts, before, rerun, store=store)
    words_after = _sections(healed)
    thin = [t for t, w in words_after.items() if t != "front" and words_before.get(t, 0) >= 150
            and (w < 150 or w < 0.7 * words_before.get(t, 0))]
    removed_detail = [a for a in summary["actions"] if a["action"].startswith(("removed", "dropped"))]
    return {
        "run": f"{run.parent.name}/{run.name}", "tier": tier, "question": question[:70],
        "deliveredOutcome": result.get("publicationOutcome"),
        "hardBefore": len(before_hard) + groups_before - (1 if "mismatchedCitationGroups" in before_hard else 0),
        "hardBeforeGates": before_hard, "scopeBlockingBefore": len(before_scope),
        "hardAfter": len(summary["stillFailing"]), "hardAfterGates": summary["stillFailing"],
        "wouldPublish": not summary["stillFailing"],
        "removed": summary["removed"], "rewritten": summary["rewritten"], "qualified": summary["qualified"],
        "chartsBefore": len(charts), "chartsAfter": len(charts_after), "thinSections": thin,
        "removedDetail": removed_detail[:12], "qualifiers": summary["qualifiers"][:6], "rounds": summary["rounds"],
    }


def main(runs: list[Path]) -> int:
    rows = []
    for run in runs:
        try:
            rows.append(replay_run(run))
        except Exception as exc:  # one bad capture must not hide the others
            rows.append({"run": f"{run.parent.name}/{run.name}", "error": f"{type(exc).__name__}: {str(exc)[:160]}"})
    print("| Capture | Delivered | Hard findings before | Scope blocking before | Hard after | Would publish | "
          "Removed | Rewritten | Qualified | Charts kept | Thin sections |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        if "error" in r:
            print(f"| {r['run']} | ERROR {r['error']} |")
            continue
        print(f"| {r['run']} | {r['deliveredOutcome']} | {r['hardBefore']} ({', '.join(r['hardBeforeGates']) or '-'}) | "
              f"{r['scopeBlockingBefore']} | {r['hardAfter']} | {'yes' if r['wouldPublish'] else 'NO'} | {r['removed']} | "
              f"{r['rewritten']} | {r['qualified']} | {r['chartsAfter']}/{r['chartsBefore']} | {len(r['thinSections'])} |")
    out = BACKEND / "diagnostics" / "replay_gates.json"
    out.write_text(json.dumps(rows, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"\ndetails: {out}")
    return 0 if all(r.get("wouldPublish") for r in rows if "error" not in r) else 1


if __name__ == "__main__":
    base = BACKEND / "diagnostics" / "eval_runs"
    runs = [Path(p) for p in sys.argv[1:]] or sorted(p.parent for p in base.glob("*/*/status_final.json"))
    sys.exit(main(runs))
