"""Run benchmark prompts live (one at a time) and record which capture belongs to which prompt.

The backend must already be running with research capture on:
    GG_RESEARCH_CAPTURE=1 python -m uvicorn main:app --host 127.0.0.1 --port 10000

Usage:
    python scripts/benchmark_run.py                 # every prompt without a capture
    python scripts/benchmark_run.py --ids a,b,c     # these prompts (re-run even if captured)
    python scripts/benchmark_run.py --limit 5       # at most five prompts this time

Runs are sequential on purpose: parallel reports compete for the same model quota, so their timing
and model choices would not be representative. Captures stay in backend/diagnostics/ (not committed);
benchmark/captures.json maps prompt ids to them.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROMPTS = ROOT / "benchmark" / "prompts.json"
CAPTURES = ROOT / "benchmark" / "captures.json"
RUNS = ROOT / "benchmark" / "runs"


LOCK = ROOT / "benchmark" / "runs" / "runner.lock"


def load_captures() -> dict:
    if CAPTURES.exists():
        return json.loads(CAPTURES.read_text(encoding="utf-8"))
    return {"about": "prompt id -> capture file in backend/diagnostics/ (local, not committed)", "captures": {}}


def save_captures(state: dict) -> None:
    """Write via a temporary file and rename, so a crash never leaves half a JSON file."""
    tmp = CAPTURES.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(tmp, CAPTURES)


def acquire_lock() -> bool:
    """One runner at a time: two runners corrupted captures.json and competed for model quota."""
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    if LOCK.exists():
        try:
            pid = int(LOCK.read_text().strip() or 0)
        except ValueError:
            pid = 0
        if pid and _alive(pid):
            print(f"another benchmark runner (pid {pid}) is running; stop it or delete {LOCK}", flush=True)
            return False
    LOCK.write_text(str(os.getpid()))
    return True


def _alive(pid: int) -> bool:
    try:
        if os.name == "nt":
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True).stdout
            return str(pid) in out
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def capture_for_report(report_id: str) -> str:
    """The capture whose final output is this report (capture names are start times, not report ids)."""
    for path in sorted(glob.glob(str(ROOT / "diagnostics" / "research_capture_*.json")), reverse=True):
        if "publication_report" in path:
            continue
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception:
            continue
        final = ((data.get("stages") or {}).get("final_output") or {}).get("data") or {}
        if final.get("reportId") == report_id:
            return Path(path).name
    return ""


def run_prompt(prompt: dict, base: str) -> dict:
    out = RUNS / prompt["id"]
    out.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, str(ROOT / "scripts" / "live_report_run.py"), "--question", prompt["question"],
               "--tier", prompt.get("tier") or "detailed", "--out", str(out), "--base", base]
    if prompt.get("file"):
        command += ["--file", str(ROOT / prompt["file"]), "--name", prompt.get("file_name") or Path(prompt["file"]).name]
    started = time.time()
    with open(out / "run.out", "w", encoding="utf-8") as log:
        code = subprocess.call(command, cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT)
    report_path = out / "report.json"
    report_id = ""
    if report_path.exists():
        try:
            report_id = str(json.loads(report_path.read_text(encoding="utf-8")).get("reportId") or "")
        except Exception:
            report_id = ""
    capture = capture_for_report(report_id) if report_id else ""
    return {"capture": capture, "questionMatches": True, "exitCode": code, "reportId": report_id,
            "seconds": round(time.time() - started), "ranAt": time.strftime("%Y-%m-%dT%H:%M:%S")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--ids", default="", help="comma-separated prompt ids to (re)run")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--base", default="http://127.0.0.1:10000")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    if not acquire_lock():
        return 1
    prompts = json.loads(PROMPTS.read_text(encoding="utf-8"))["prompts"]
    state = load_captures()
    wanted = {i.strip() for i in args.ids.split(",") if i.strip()}
    todo = [p for p in prompts if (p["id"] in wanted) or (not wanted and not state["captures"].get(p["id"], {}).get("capture"))]
    if args.limit:
        todo = todo[:args.limit]
    print(f"{len(todo)} prompt(s) to run: {', '.join(p['id'] for p in todo)}", flush=True)
    for prompt in todo:
        print(f"=== {time.strftime('%H:%M:%S')} START {prompt['id']}", flush=True)
        result = run_prompt(prompt, args.base)
        state = load_captures()  # re-read: another process may have recorded a run meanwhile
        state["captures"][prompt["id"]] = result
        save_captures(state)
        print(f"=== {time.strftime('%H:%M:%S')} DONE {prompt['id']} capture={result['capture'] or 'MISSING'} "
              f"exit={result['exitCode']} {result['seconds']}s", flush=True)
    print("=== ALL DONE", flush=True)
    LOCK.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
