"""Mixed-topic scorecard over saved research captures - no LLM, search or fetch calls.

Every capture (GG_RESEARCH_CAPTURE=1) already holds the final report, its charts and
the chart pipeline stages. This script reads them and reports, per question:

  words / est. pages   - length of the final report (~250 words per page)
  sources              - canonical sources in the final manifest
  charts               - charts in the final report
  chartable tables     - report tables with >= 3 rows that carry a number (each one
                         could have been a chart, so charts << chartable means missed charts)
  dropped              - charts that reached a stage and were removed later
  first drop reason    - the most common removal reason

Usage:
  python scripts/topic_scorecard.py [--json out.json] [capture.json ...]
With no capture paths, every final-output capture in diagnostics/ is scored, one per question
(the newest run of each question).
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import re
import sys

WORDS_PER_PAGE = 250
_NUM = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")


def _stage(stages: dict, key: str):
    value = stages.get(key)
    if isinstance(value, list):
        value = value[-1] if value else None
    return (value or {}).get("data") if isinstance(value, dict) else None


def chartable_tables(report: str) -> int:
    """Markdown tables with >= 3 body rows whose cells (after the first) carry a number."""
    count = 0
    rows: list[str] = []
    for line in str(report or "").splitlines() + [""]:
        if line.strip().startswith("|"):
            rows.append(line)
            continue
        if rows:
            body = [r for r in rows[1:] if not re.fullmatch(r"\s*\|[\s:|-]+\|\s*", r)]
            numeric = [r for r in body if _NUM.search("|".join(r.strip().strip("|").split("|")[1:]))]
            if len(numeric) >= 3:
                count += 1
            rows = []
    return count


def score(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as fh:
            capture = json.load(fh)
    except Exception:
        return None
    stages = capture.get("stages") or capture
    final = _stage(stages, "final_output")
    meta = _stage(stages, "request_metadata") or {}
    if not isinstance(final, dict) or not final.get("report"):
        return None
    report = str(final.get("report") or "")
    words = len(re.findall(r"[A-Za-z]+", report))
    pipeline = final.get("chartPipeline") or {}
    peak = 0
    reasons: collections.Counter = collections.Counter()
    for stage in pipeline.get("stages") or []:
        peak = max(peak, int(stage.get("count") or 0))
        for removed in stage.get("removed") or []:
            reason = re.sub(r"\s+", " ", str((removed or {}).get("reason") or "")).strip()[:70]
            if reason:
                reasons[reason] += 1
    charts = len(final.get("charts") or [])
    return {
        "capture": os.path.basename(path),
        "question": str(meta.get("question") or final.get("title") or "")[:80],
        "tier": meta.get("reportTier") or "",
        "words": words,
        "pages": round(words / WORDS_PER_PAGE),
        "sources": int(final.get("sourceManifestCount") or len(final.get("sources") or [])),
        "charts": charts,
        "chartableTables": chartable_tables(report),
        "dropped": max(0, peak - charts),
        "dropReason": reasons.most_common(1)[0][0] if reasons else "",
    }


def default_captures() -> list[str]:
    newest: dict[str, str] = {}
    for path in sorted(glob.glob(os.path.join("diagnostics", "research_capture_*.json"))):
        if "publication" in path or os.path.getsize(path) < 300_000:
            continue
        row = score(path)
        if row:
            newest[row["question"]] = path  # sorted by time: the last one wins
    return list(newest.values())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("captures", nargs="*")
    ap.add_argument("--json")
    args = ap.parse_args()
    rows = [r for r in (score(p) for p in (args.captures or default_captures())) if r]
    header = f"{'tier':9} {'pages':>5} {'src':>4} {'charts':>6} {'tables':>6} {'drop':>4}  question"
    print(header)
    print("-" * len(header))
    for r in rows:
        print(f"{r['tier']:9} {r['pages']:>5} {r['sources']:>4} {r['charts']:>6} {r['chartableTables']:>6} {r['dropped']:>4}  {r['question']}")
    n = len(rows) or 1
    print("-" * len(header))
    print(f"reports={len(rows)}  zero-chart reports={sum(1 for r in rows if r['charts'] == 0)}  "
          f"avg charts={sum(r['charts'] for r in rows) / n:.1f}  avg chartable tables={sum(r['chartableTables'] for r in rows) / n:.1f}  "
          f"avg pages={sum(r['pages'] for r in rows) / n:.0f}")
    reasons = collections.Counter(r["dropReason"] for r in rows if r["dropReason"])
    if reasons:
        print("most common chart-removal reasons:")
        for reason, count in reasons.most_common(6):
            print(f"  {count:>3}  {reason}")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, indent=2, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
