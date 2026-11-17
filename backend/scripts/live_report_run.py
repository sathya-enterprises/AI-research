"""Drive one report like the UI (start -> poll status -> PDF) against a running backend; saves report.json, report.pdf, summary.json.

usage: python scripts/live_report_run.py --question "..." --out OUTDIR [--tier detailed|in_depth] [--file data.txt --name data.xlsx]
Run the server with GG_RESEARCH_CAPTURE=1 and its log redirected to OUTDIR/backend.log, then use analyze_report_cycle.py.
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def call(base, path, payload=None, timeout=60):
    data = json.dumps(payload).encode() if payload is not None else None
    req = Request(base + path, data=data, headers={"Content-Type": "application/json"},
                  method="POST" if payload is not None else "GET")
    try:
        with urlopen(req, timeout=timeout) as response:
            body = response.read()
            return response.status, (body if response.headers.get_content_type() == "application/pdf" else json.loads(body))
    except HTTPError as exc:
        body = exc.read()
        try:
            return exc.code, json.loads(body)
        except Exception:
            return exc.code, {"raw": body[:500].decode(errors="replace")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--question", required=True)
    ap.add_argument("--tier", default="detailed")
    ap.add_argument("--out", required=True)
    ap.add_argument("--file", help="optional text/CSV file to send as the uploaded attachment")
    ap.add_argument("--base", default="http://127.0.0.1:10000")
    ap.add_argument("--name", help="display filename for --file (defaults to its basename)")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    payload = {"question": args.question, "reportTier": args.tier}
    if args.file:
        name = args.name or Path(args.file).name
        text = Path(args.file).read_text(encoding="utf-8")
        payload.update({"fileContext": f"[File: {name}]\n{text}",
                        "sourceDocuments": [{"name": name, "text": text, "file_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if name.endswith(".xlsx") else "text/csv"}]})
    started = time.time()
    status, start = call(args.base, "/api/chat/report/start", payload)
    job = start.get("jobId") or start.get("job_id")
    print("start", status, job, flush=True)
    result = None
    # in-depth reports run to a 50-minute hard stop plus the checks and render after it; under API rate limits
    # they finished after the client had given up (ev_india_in_depth: done server-side, recorded as failed)
    while time.time() - started < (4500 if args.tier == "in_depth" else 3000):
        time.sleep(15)
        status, body = call(args.base, f"/api/chat/report/status/{job}")
        state = body.get("status") or body.get("state")
        print(f"{int(time.time() - started)}s status={state} {str(body.get('progress') or body.get('message') or '')[:90]}", flush=True)
        if state in {"done", "completed", "complete", "failed", "error"}:
            result = body.get("result") or body
            break
    (out / "report.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    if not isinstance(result, dict) or not result.get("report"):
        print("NO REPORT:", json.dumps(result, ensure_ascii=False)[:1500])
        return 1
    status, pdf = call(args.base, "/api/chat/report/pdf", result, timeout=600)
    pdf_ok = isinstance(pdf, (bytes, bytearray))
    if pdf_ok:
        (out / "report.pdf").write_bytes(pdf)
    report = result.get("report") or ""
    summary = {
        "seconds": int(time.time() - started),
        "title": result.get("title"),
        "pipeline": result.get("pipeline"), "fallbackReason": result.get("fallbackReason"),
        "publicationStatus": result.get("publicationStatus"), "publicationOutcome": result.get("publicationOutcome"),
        "failedStage": result.get("failedStage"),
        "failureSummary": [str(f.get("issue"))[:160] for f in (result.get("failureSummary") or [])][:6],
        "reportChars": len(report), "words": len(re.findall(r"[A-Za-z]+", report)),
        "maxLineLen": max((len(l) for l in report.splitlines()), default=0),
        "charts": [c.get("title") for c in result.get("charts") or []],
        "chartPipeline": [(s.get("stage"), s.get("count")) for s in (result.get("chartPipeline") or {}).get("stages") or []],
        "sources": len(result.get("sources") or []),
        "citedIds": len(set(re.findall(r"\[S(\d+)\]", report))),
        "disagreeNotes": re.findall(r"(?im)^.*sources disagree.*$", report)[:3],
        "pdf": f"{status} {'ok ' + str(len(pdf)) + ' bytes' if pdf_ok else str(pdf)[:300]}",
        "publicationGates": result.get("publicationGates"),
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
