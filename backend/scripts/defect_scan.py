"""Scan every report of a benchmark run for known defect classes and print a scorecard.

Usage:
  python scripts/defect_scan.py benchmark/captures.json            # a capture map
  python scripts/defect_scan.py diagnostics/runner30b.out          # a runner log ("DONE <id> capture=<file>")
  python scripts/defect_scan.py <map or log> --json out.json
Exit code 1 when any report has a critical text defect (leaked marker / fact id) - usable as a release gate.

Finish line for the engine (all 30 prompts): 0 critical text defects, 0 critical audit findings, every report
at its chart minimum.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from utils.defect_scan import scan_capture  # noqa: E402


def captures_from(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        data = json.loads(text)
        return {k: v["capture"] for k, v in (data.get("captures") or {}).items() if v.get("capture")}
    return dict(re.findall(r"DONE (\w+) capture=(\S+\.json)", text))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("source")
    parser.add_argument("--json", default="")
    args = parser.parse_args()
    rows = {}
    for pid, capture_file in captures_from(Path(args.source)).items():
        path = ROOT / "diagnostics" / capture_file
        if not path.exists():
            continue
        rows[pid] = scan_capture(json.loads(path.read_text(encoding="utf-8")))
    print(f"{'report':26s} {'status':8s} charts  removed  audit c/h/m  text defects")
    for pid, r in rows.items():
        audit = r["auditFindings"]
        defects = ", ".join(f"{k}:{n}" for k, n in r["textDefects"].items()) or "-"
        print(f"{pid:26s} {str(r['status']):8s} {r['charts']:2d}/{r['chartMinimum']:<2d}  {r['sentencesRemoved']:5d}   "
              f"{audit['critical']}/{audit['high']}/{audit['medium']:<3d}     {defects}")
    total = {
        "reports": len(rows),
        "pass": sum(1 for r in rows.values() if r["status"] == "PASS"),
        "criticalTextDefects": sum(r["criticalTextDefects"] for r in rows.values()),
        "criticalAuditFindings": sum(r["auditFindings"]["critical"] for r in rows.values()),
        "highAuditFindings": sum(r["auditFindings"]["high"] for r in rows.values()),
        "reportsBelowChartMinimum": sum(1 for r in rows.values() if r["chartsBelowMinimum"]),
        "charts": sum(r["charts"] for r in rows.values()),
        "sentencesRemoved": sum(r["sentencesRemoved"] for r in rows.values()),
    }
    print("\nTOTAL " + " | ".join(f"{k} {v}" for k, v in total.items()))
    if args.json:
        Path(args.json).write_text(json.dumps({"total": total, "reports": rows}, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
    return 1 if total["criticalTextDefects"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
