"""Replay a research capture's deterministic checks offline with the current code.

A live report run takes ~9 minutes and depends on search results and model
output that change every time. A capture (GG_RESEARCH_CAPTURE=1) already holds
the writer's raw section text, the evidence store, the fetched page texts, the
final report, its source manifest and its charts. This script re-runs the
deterministic checks on that saved material - no LLM, search or page fetches -
so a fix can be judged against a real run in seconds, and the same capture can
be replayed before and after a change.

Checks replayed:
  1. V2 number policy on every section / extension / executive-summary draft,
     against the whole evidence store (the run checks a section against its own
     passages first and then the whole store, so this is the store-wide view).
  2. Source evidence: claim-aligned excerpts + citation-group check on the final
     report (``_attach_audit_source_evidence`` + ``_source_evidence_quality_audit``).
  3. Chart evidence: the strict publication chart audit on the final charts.

Usage:
  python scripts/replay_capture.py diagnostics/research_capture_....json [--json out.json] [--verbose]
Exit code is 0; the comparison is printed (captured run vs. replay).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _stage(capture: dict, name: str) -> dict:
    stage = (capture.get("stages") or {}).get(name) or {}
    return stage.get("data", stage) if isinstance(stage, dict) else {}


def replay_number_policy(capture: dict) -> dict:
    from utils.number_policy import apply_number_policy

    passages = _stage(capture, "evidence_store").get("passages") or {}
    texts = {pid: (p.get("text") if isinstance(p, dict) else str(p)) or "" for pid, p in passages.items()}
    drafts = [call for call in capture.get("llmCalls") or []
              if call.get("purpose") in {"section", "section_extend", "executive_summary"}
              and call.get("ok") and call.get("llmResponse")]
    removed: list[dict] = []
    for call in drafts:
        section = str(call.get("sectionId") or call.get("purpose"))
        result = apply_number_policy(str(call["llmResponse"]), passage_texts=texts, allowed_numbers=set(),
                                     section_id=section)
        removed.extend(result.removed)
    captured = ((_stage(capture, "pipeline_v2").get("numberPolicy") or {}).get("sentencesRemoved") or [])
    return {"drafts": len(drafts), "captured": captured, "replayed": removed}


def replay_source_evidence(capture: dict) -> dict:
    from routes.report import _attach_audit_source_evidence, _source_evidence_quality_audit

    final = _stage(capture, "final_output")
    manifest = [row for row in final.get("sources") or [] if isinstance(row, dict)]
    enriched, exact = audit_source_rows(capture)
    audited = _attach_audit_source_evidence(manifest, enriched, str(final.get("report") or ""))
    audit = _source_evidence_quality_audit(audited)
    captured = [finding for finding in ((final.get("audit") or {}).get("findings") or [])
                if "lacks source evidence" in str(finding.get("issue") or "")]
    return {"captured": captured, "replayed": list(audit.get("mismatchedCitationGroups") or []), "audited": audited,
            "exactSourceRows": exact, "sourceRows": len(enriched)}


def audit_source_rows(capture: dict) -> tuple[list[dict], bool]:
    """The source rows the run's audits read; (rows, exact).

    Captures since the ``audit_sources`` stage hold the exact list. Older ones are
    rebuilt from the fetch, crawl and official-dataset stages; chart gap-fill and
    entity-search pages were not captured, so claims citing them can replay as
    unsupported (the result says the rows are approximate).
    """
    exact = _stage(capture, "audit_sources").get("sources")
    if isinstance(exact, list) and exact:
        return [row for row in exact if isinstance(row, dict)], True
    rows: list[dict] = []
    seen: set[str] = set()
    for stage, key in (("official_datasets", "rows"), ("sources_after_article_fetch", "sources"),
                       ("sources_after_site_crawl", "sources"), ("sources_after_post_fetch_retry", "sources")):
        for row in _stage(capture, stage).get(key) or []:
            if not isinstance(row, dict):
                continue
            url = str(row.get("url") or "").rstrip("/")
            if url and url in seen:
                continue
            seen.add(url)
            row = dict(row)
            if stage == "official_datasets":
                # Official rows are captured without their fetch fields; they were fetched from the API.
                row.setdefault("pageContent", row.get("fullContent") or "")
                row.setdefault("pageFetchStatus", "ok")
                row.setdefault("evidenceOrigin", "publisher_page")
                row.setdefault("sourceType", "Official dataset")
            rows.append(row)
    return rows, False


def replay_charts(capture: dict, audited: list) -> dict:
    from routes.report import _merge_source_evidence_statuses
    from utils.chart_evidence_audit import audit_chart_evidence_consistency

    final = _stage(capture, "final_output")
    charts = [chart for chart in final.get("charts") or [] if isinstance(chart, dict)]
    manifest = [row for row in final.get("sources") or [] if isinstance(row, dict)]
    merged = _merge_source_evidence_statuses(manifest, audited)
    result = audit_chart_evidence_consistency(str(final.get("report") or ""), charts, merged,
                                              question=str(capture.get("question") or ""),
                                              require_publisher_verification=True)
    return {"charts": len(charts), "failures": list(result.get("chartEvidenceFailures") or []),
            "conflicts": list(result.get("chartTableVerificationConflicts") or [])}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("capture")
    parser.add_argument("--json", help="write the full replay result here")
    parser.add_argument("--verbose", action="store_true", help="list every removal / finding")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    logging.disable(logging.INFO)

    capture = json.loads(Path(args.capture).read_text(encoding="utf-8"))
    numbers = replay_number_policy(capture)
    sources = replay_source_evidence(capture)
    charts = replay_charts(capture, sources.pop("audited"))

    limit = None if args.verbose else 8
    print(f"# Replay of {Path(args.capture).name}")
    print(f"Question: {capture.get('question')}\nTier: {capture.get('reportTier')}\n")

    print("## 1. Number policy (sentences removed)")
    print(f"captured run: {len(numbers['captured'])} | replay with current code: {len(numbers['replayed'])} "
          f"(over {numbers['drafts']} drafts)")
    for reason, count in Counter(r["reason"].split(" ", 1)[-1][:60] for r in numbers["replayed"]).most_common():
        print(f"  {count} x {reason}")
    for item in numbers["replayed"][:limit]:
        print(f"  - [{item['section']}] {item['number']}: {item['sentence'][:150]}")

    print("\n## 2. Source evidence (claims no cited source supports)")
    print(f"captured run: {len(sources['captured'])} findings | replay: {len(sources['replayed'])} citation groups "
          f"| source rows: {sources['sourceRows']} ({'exact' if sources['exactSourceRows'] else 'rebuilt from stages; approximate'})")
    for group in sources["replayed"][:limit]:
        print(f"  - {', '.join(group.get('sourceIds') or [])}: {', '.join(group.get('unmatchedNumericClaims') or [])} "
              f"| {str(group.get('claim') or '')[:140]}")

    print("\n## 3. Chart evidence (final charts, strict publication audit)")
    print(f"charts: {charts['charts']} | point failures: {len(charts['failures'])} | table conflicts: {len(charts['conflicts'])}")
    for failure in charts["failures"][:limit]:
        print(f"  - {str(failure.get('chartTitle'))[:60]} / {failure.get('label')} = {failure.get('value')}: "
              f"{str(failure.get('reason'))[:110]}")

    if args.json:
        Path(args.json).write_text(json.dumps({"numberPolicy": numbers, "sourceEvidence": sources, "charts": charts},
                                              ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"\nfull result: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
