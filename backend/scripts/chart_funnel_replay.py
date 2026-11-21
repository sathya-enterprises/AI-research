"""Offline chart funnel per saved run, with the CURRENT code: evidence store -> table chart candidates
-> pre-writing quality gate -> source verification. No LLM, search or fetch calls.

The store is rebuilt from the run's fetched pages (so changes to passage splitting are measured), and the
same chart builder, quality gate and evidence audit the report writer uses are applied.

Usage:
  python scripts/chart_funnel_replay.py [--json out.json] [--compare old.json] [capture.json ...]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from collections import Counter
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.disable(logging.CRITICAL)


def funnel(raw: dict) -> dict | None:
    import routes.report as report
    import scripts.replay_capture as rc
    from scripts.topic_scorecard import _stage
    from utils.chart_evidence_audit import audit_chart_evidence_consistency, chart_meaning_reason
    from utils.chart_lint import quality_reason
    from utils.data_angles import requested_year_bounds
    from utils.evidence_store import build_evidence_store
    from utils.report_pipeline_v2 import CANDIDATE_CAP
    from utils.section_charts import build_chart_candidates, clip_to_question_window
    from utils.series_checks import chart_data_problem
    from utils.table_focus import focus_entities

    stages = raw.get("stages") or raw
    final = _stage(stages, "final_output") or {}
    meta = _stage(stages, "request_metadata") or {}
    question = str(meta.get("question") or "")
    tier = str(meta.get("reportTier") or "detailed")
    manifest = [r for r in final.get("sources") or [] if isinstance(r, dict)]
    if not final.get("report") or not manifest:
        return None
    rows, _exact = rc.audit_source_rows(raw)
    store = build_evidence_store(rows, question=question, manifest=manifest)
    today = str(raw.get("startedAt") or "2026-11-17")[:10]
    try:
        min_year, max_year = requested_year_bounds(question, date.fromisoformat(today))
    except Exception:
        min_year, max_year = None, None
    built, _drops = build_chart_candidates(store, max_charts=CANDIDATE_CAP.get(tier, 60), min_year=min_year,
                                           max_year=max_year, today=today, focus=focus_entities(question))
    built = [c for c in built if not clip_to_question_window(c, question)]
    earlier: list = []
    quality = []
    reasons: Counter = Counter()
    for chart in built:
        text = "\n".join(store.get(pid).text for pid in chart.passage_ids if store.get(pid))
        reason = (quality_reason(chart.spec, text[:400], earlier) or chart_data_problem(chart.spec, question)
                  or chart_meaning_reason(dict(chart.spec, evidencePassages=chart.passage_ids), question, manifest))
        if reason:
            reasons["quality: " + re.sub(r"\d[\d,.]*", "#", reason)[:60]] += 1
            continue
        earlier.append(sorted(round(float(p.get("value") or 0), 4) for s in chart.spec.get("series") or []
                              for p in s.get("data") or []))
        quality.append(chart)
    rc.reset_run_state()
    specs = [dict(c.spec, evidencePassages=c.passage_ids) for c in quality]
    report._register_v2_chart_evidence(specs, store)
    public = report._merge_source_evidence_statuses(manifest, report._attach_audit_source_evidence(manifest, rows, ""))
    result = audit_chart_evidence_consistency("", specs, public, question=question, require_publisher_verification=True)
    bad: dict[int, str] = {}
    for f in list(result.get("chartEvidenceFailures") or []) + list(result.get("chartTableVerificationConflicts") or []):
        index = int(f.get("chartIndex") or 0) - 1
        bad.setdefault(index, str(f.get("reason") or ""))
    for reason in bad.values():
        reasons["verify: " + reason[:60]] += 1
    verified = [s for i, s in enumerate(specs) if i not in bad]
    return {
        "question": question[:80], "tier": tier,
        "tablePassages": sum(1 for p in store.passages if p.kind == "table"),
        "built": len(built), "quality": len(quality), "verified": len(verified),
        "shippedInRun": len(final.get("charts") or []),
        "verifiedTitles": [str(s.get("title") or "")[:90] for s in verified],
        "reasons": dict(reasons.most_common(6)),
    }


def main() -> int:
    from scripts.topic_scorecard import default_captures
    ap = argparse.ArgumentParser()
    ap.add_argument("captures", nargs="*")
    ap.add_argument("--json")
    ap.add_argument("--compare")
    args = ap.parse_args()
    before = {}
    if args.compare:
        before = {r["question"]: r for r in json.load(open(args.compare, encoding="utf-8"))}
    rows = []
    for path in args.captures or default_captures():
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
        try:
            row = funnel(raw)
        except Exception as exc:  # one bad capture must not stop the scorecard
            print(f"ERROR {os.path.basename(path)}: {type(exc).__name__}: {str(exc)[:120]}")
            continue
        if not row:
            continue
        rows.append(row)
        old = before.get(row["question"])
        delta = f" (was {old['verified']})" if old else ""
        print(f"tables={row['tablePassages']:>4} built={row['built']:>3} quality={row['quality']:>3} "
              f"verified={row['verified']:>3}{delta:9} shipped-in-run={row['shippedInRun']:>2} | {row['question'][:60]}")
    n = len(rows) or 1
    print(f"\nreports={len(rows)} verified total={sum(r['verified'] for r in rows)} avg={sum(r['verified'] for r in rows) / n:.1f} "
          f"reports with <2 verified={sum(1 for r in rows if r['verified'] < 2)}")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, indent=2, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
