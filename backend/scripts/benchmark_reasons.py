"""Count WHY numbers are removed and chart candidates rejected across all benchmark prompts.

The scorecard lists reasons per prompt; this ranks them across the whole benchmark so work goes to
the causes that recur in many sectors, not to the last report looked at.

Usage: python scripts/benchmark_reasons.py [--out benchmark/results/reasons.md]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))


def _family(reason: str) -> str:
    """Group reasons that differ only in their numbers ("input(s) 41758 not in ..." -> "input(s) N not in ...")."""
    text = re.sub(r"[-+−]?\d[\d,]*(?:\.\d+)?", "N", str(reason))
    text = re.sub(r"N(?:, N)+", "N", text)
    return text.split(" ", 1)[-1][:90] if text.startswith("N ") else text[:90]


def one(args: tuple[str, str]) -> dict:
    prompt_id, capture_file = args
    import logging

    logging.disable(logging.INFO)
    import replay_capture as replay

    capture = json.loads((ROOT / "diagnostics" / capture_file).read_text(encoding="utf-8"))
    replay.reset_run_state()
    numbers = replay.replay_number_policy(capture)["replayed"]
    sources = replay.replay_source_evidence(capture)
    audited = sources.pop("audited")
    candidates = replay.replay_chart_candidates(capture, audited, replay.audit_source_rows(capture)[0])["candidates"]
    return {"id": prompt_id,
            "numbers": [_family(r["reason"]) for r in numbers],
            "charts": [c["verdict"][:90] for c in candidates if c["verdict"] != "verified"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", default=str(ROOT / "benchmark" / "results" / "reasons.md"))
    parser.add_argument("--jobs", type=int, default=3)
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    captures = json.loads((ROOT / "benchmark" / "captures.json").read_text(encoding="utf-8"))["captures"]
    jobs = [(pid, row["capture"]) for pid, row in captures.items() if row.get("capture")]
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        rows = list(pool.map(one, jobs))

    lines = ["# Why numbers are removed and charts rejected (all benchmark prompts, current code)", ""]
    for kind, title in (("numbers", "Numbers removed by the number policy"), ("charts", "Chart candidates rejected before writing")):
        counts, prompts = Counter(), defaultdict(set)
        for row in rows:
            for reason in row[kind]:
                counts[reason] += 1
                prompts[reason].add(row["id"])
        total = sum(counts.values())
        lines += [f"## {title}: {total} across {len(rows)} prompts", "", "| Reason | Count | Prompts affected |", "|---|---:|---:|"]
        for reason, count in counts.most_common():
            lines.append(f"| {reason} | {count} | {len(prompts[reason])} |")
        lines.append("")
    Path(args.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
