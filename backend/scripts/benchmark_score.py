"""Score the research engine on the fixed benchmark: replay every captured prompt with the current code.

Two kinds of numbers per prompt:
  * live  - what the run itself produced (outcome, words vs plan, charts published, gates). These are
            fixed at run time; they change only when the prompt is re-run (scripts/benchmark_run.py).
  * replay - the deterministic checks re-run on the saved evidence with the CURRENT code. These move
            with every code change and are what a fix is judged on:
              numbersRemoved      number-policy removals over the writer's drafts (lower is better)
              unsupportedClaims   citation groups no cited source supports, after the fact-sheet check
              chartFailures       strict chart-gate failures on the published charts
              candidatesBuilt / candidatesVerified   table + official-dataset chart candidates and how
                                  many pass pre-writing verification (higher is better)

Usage:
    python scripts/benchmark_score.py --label baseline
    python scripts/benchmark_score.py --label after-x --compare benchmark/results/baseline.json
    python scripts/benchmark_score.py --ids banking_asset_quality,healthcare_sea   # a subset
Writes benchmark/results/<label>.json and .md.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

METRICS = [  # (key, label, better)
    ("numbersRemoved", "numbers removed", "lower"),
    ("unsupportedClaims", "unsupported claims", "lower"),
    ("chartFailures", "chart gate failures", "lower"),
    ("candidatesVerified", "chart candidates verified", "higher"),
]


def _stage(capture: dict, name: str) -> dict:
    stage = (capture.get("stages") or {}).get(name) or {}
    return stage.get("data", stage) if isinstance(stage, dict) else {}


def live_metrics(capture: dict, capture_path: Path) -> dict:
    final = _stage(capture, "final_output")
    v2 = _stage(capture, "pipeline_v2")
    planned = ((v2.get("outline") or {}).get("meta") or {}).get("plannedAnalysisWords")
    words = {"written": v2.get("words"), "planned": round(planned) if isinstance(planned, (int, float)) else None}
    report_text = str(final.get("report") or "")
    import re
    cited = set(re.findall(r"\[(S\d+)", report_text)) | {
        sid for group in re.findall(r"\[(S\d+(?:\s*[,;]\s*S\d+)+)\]", report_text) for sid in re.split(r"\s*[,;]\s*", group)}
    publication_path = capture_path.with_name(capture_path.stem + ".publication_report.json")
    publication = json.loads(publication_path.read_text(encoding="utf-8")) if publication_path.exists() else {}
    gates = publication.get("gates") or []
    return {
        "outcome": publication.get("outcome") or "",
        "status": final.get("publicationStatus") or "",
        "chartsPublished": len(final.get("charts") or []),
        "wordsWritten": words.get("written") or words.get("actual"),
        "wordsPlanned": words.get("planned") or words.get("target"),
        "gatesFailing": [g.get("gate") for g in gates if str(g.get("status")).upper() == "FAIL"],
        "sourcesCited": len(cited),
        "exactSourceRows": bool(_stage(capture, "audit_sources").get("sources")),
    }


def replay_metrics(capture: dict) -> dict:
    import logging

    logging.disable(logging.INFO)
    import replay_capture as replay

    replay.reset_run_state()  # each capture starts from a clean report state, as a live run does
    numbers = replay.replay_number_policy(capture)
    sources = replay.replay_source_evidence(capture)
    audited = sources.pop("audited")
    charts = replay.replay_charts(capture, audited)
    candidates = replay.replay_chart_candidates(capture, audited, replay.audit_source_rows(capture)[0])
    built = candidates["candidates"]
    return {
        "numbersRemoved": len(numbers["replayed"]),
        "numbersRemovedReasons": sorted({r["reason"].split(" ", 1)[-1][:60] for r in numbers["replayed"]}),
        "unsupportedClaims": len(sources["replayed"]),
        "chartFailures": len(charts["failures"]) + len(charts["conflicts"]),
        "candidatesBuilt": len(built),
        "candidatesVerified": sum(1 for c in built if c["verdict"] == "verified"),
        "candidateRejections": sorted({c["verdict"][:90] for c in built if c["verdict"] != "verified"}),
    }


def score_one(prompt_id: str, capture_file: str, diagnostics: str = "") -> dict:
    path = (Path(diagnostics) if diagnostics else ROOT / "diagnostics") / capture_file
    started = time.time()
    try:
        capture = json.loads(path.read_text(encoding="utf-8"))
        row = {"id": prompt_id, "capture": capture_file, **live_metrics(capture, path), **replay_metrics(capture)}
    except Exception as exc:  # one broken capture must not stop the scorecard
        row = {"id": prompt_id, "capture": capture_file, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    row["replaySeconds"] = round(time.time() - started, 1)
    return row


def to_markdown(label: str, rows: list[dict], previous: dict | None) -> str:
    before = {r["id"]: r for r in (previous or {}).get("rows", [])}

    def cell(row: dict, key: str) -> str:
        value = row.get(key)
        if value is None:
            return "–"
        old = before.get(row["id"], {}).get(key)
        if isinstance(value, int) and isinstance(old, int) and old != value:
            return f"{value} ({'+' if value > old else ''}{value - old})"
        return str(value)

    lines = [f"# Benchmark scorecard: {label}", "",
             f"{len(rows)} prompts | replayed with the current code on {time.strftime('%Y-%m-%d %H:%M')}"
             + (f" | compared with `{previous.get('label')}`" if previous else ""), "",
             "| Prompt | Live outcome | Charts published | Words (written/plan) | Numbers removed | Unsupported claims "
             "| Chart gate failures | Chart candidates verified |",
             "|---|---|---:|---|---:|---:|---:|---|"]
    for row in rows:
        if row.get("error"):
            lines.append(f"| {row['id']} | error: {row['error'][:60]} | | | | | | |")
            continue
        words = f"{row.get('wordsWritten') or '–'}/{row.get('wordsPlanned') or '–'}"
        lines.append(f"| {row['id']} | {row.get('outcome') or row.get('status')} | {row.get('chartsPublished')} | {words} "
                     f"| {cell(row, 'numbersRemoved')} | {cell(row, 'unsupportedClaims')} | {cell(row, 'chartFailures')} "
                     f"| {cell(row, 'candidatesVerified')}/{row.get('candidatesBuilt')} |")
    ok = [r for r in rows if not r.get("error")]
    totals = {key: sum(int(r.get(key) or 0) for r in ok) for key, _label, _better in METRICS}
    totals["candidatesBuilt"] = sum(int(r.get("candidatesBuilt") or 0) for r in ok)
    lines += ["", "## Totals (replay, current code)", "",
              f"- numbers removed: **{totals['numbersRemoved']}**",
              f"- unsupported claims after the fact-sheet check: **{totals['unsupportedClaims']}**",
              f"- chart gate failures on published charts: **{totals['chartFailures']}**",
              f"- chart candidates verified: **{totals['candidatesVerified']} / {totals['candidatesBuilt']}**"]
    if previous:
        old = previous.get("totals") or {}
        # Compare like with like: only the prompts scored in both scorecards (new prompts are added over time).
        common = [r for r in ok if r["id"] in before and not before[r["id"]].get("error")]
        lines += ["", f"## Change against the previous scorecard ({len(common)} prompts in both)", ""]
        for key, name, better in METRICS:
            now = sum(int(r.get(key) or 0) for r in common)
            was = sum(int(before[r["id"]].get(key) or 0) for r in common)
            delta = now - was
            verdict = "same" if delta == 0 else ("better" if (delta < 0) == (better == "lower") else "WORSE")
            lines.append(f"- {name}: {was} -> {now} ({verdict})")
        new_ids = [r["id"] for r in ok if r["id"] not in before]
        if new_ids:
            lines.append(f"- prompts new in this scorecard (not compared): {', '.join(new_ids)}")
        worse = [r["id"] for r in ok if r["id"] in before and any(
            (int(r.get(k) or 0) > int(before[r["id"]].get(k) or 0)) if b == "lower" else
            (int(r.get(k) or 0) < int(before[r["id"]].get(k) or 0)) for k, _n, b in METRICS)]
        lines.append(f"- prompts worse on any metric: {', '.join(worse) if worse else 'none'}")
    lines += ["", "## Why charts were rejected / numbers removed (replay)", ""]
    for row in ok:
        reasons = row.get("candidateRejections") or []
        removed = row.get("numbersRemovedReasons") or []
        if reasons or removed:
            lines.append(f"- **{row['id']}**: charts: {'; '.join(reasons) or '–'} | numbers: {'; '.join(removed) or '–'}")
    return "\n".join(lines) + "\n", totals


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--label", required=True)
    parser.add_argument("--compare", help="a previous results JSON to compare with")
    parser.add_argument("--ids", default="")
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--diagnostics", default="", help="captures folder (default backend/diagnostics)")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    prompts = json.loads((ROOT / "benchmark" / "prompts.json").read_text(encoding="utf-8"))["prompts"]
    captures = json.loads((ROOT / "benchmark" / "captures.json").read_text(encoding="utf-8"))["captures"]
    wanted = {i.strip() for i in args.ids.split(",") if i.strip()}
    jobs = [(p["id"], captures[p["id"]]["capture"]) for p in prompts
            if captures.get(p["id"], {}).get("capture") and (not wanted or p["id"] in wanted)]
    missing = [p["id"] for p in prompts if not captures.get(p["id"], {}).get("capture")]
    print(f"scoring {len(jobs)} prompt(s); not yet captured: {', '.join(missing) or 'none'}", flush=True)

    rows: dict[str, dict] = {}
    with ProcessPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        futures = {pool.submit(score_one, pid, cap, args.diagnostics): pid for pid, cap in jobs}
        for future in as_completed(futures):
            row = future.result()
            rows[row["id"]] = row
            print(f"  {row['id']}: {'ERROR ' + row['error'] if row.get('error') else 'ok'} ({row['replaySeconds']}s)", flush=True)
    ordered = [rows[pid] for pid, _ in jobs if pid in rows]

    previous = json.loads(Path(args.compare).read_text(encoding="utf-8")) if args.compare else None
    markdown, totals = to_markdown(args.label, ordered, previous)
    out = ROOT / "benchmark" / "results"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{args.label}.json").write_text(json.dumps({"label": args.label, "scoredAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                                        "notCaptured": missing, "totals": totals, "rows": ordered},
                                                       indent=2, ensure_ascii=False), encoding="utf-8")
    (out / f"{args.label}.md").write_text(markdown, encoding="utf-8")
    print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
