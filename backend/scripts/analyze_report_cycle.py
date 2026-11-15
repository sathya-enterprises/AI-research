"""Full-cycle analysis of one observed report run: backend log + research capture + API result.

usage: python scripts/analyze_report_cycle.py OBSERVED_DIR CAPTURE_JSON  (OBSERVED_DIR holds backend.log and result/ from live_report_run.py)
Writes OBSERVED_DIR/cycle_analysis.md and prints it.
"""
import collections
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

sys.stdout.reconfigure(encoding="utf-8")
root = Path(sys.argv[1])
capture = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
log_lines = (root / "backend.log").read_text(encoding="utf-8", errors="replace").splitlines()
summary = json.loads((root / "result" / "summary.json").read_text(encoding="utf-8"))
result = json.loads((root / "result" / "report.json").read_text(encoding="utf-8"))
stages = capture.get("stages") or {}
data = lambda name: (stages.get(name) or {}).get("data") or {}
out: list[str] = []
say = out.append

# ---------- 1. Timeline from the backend log ----------
TS = re.compile(r"^(\d\d:\d\d:\d\d) \[GG-Backend\] (\w+)\s+(\S+)\s+\S\s+(.*)$")
events = []
for line in log_lines:
    m = TS.match(line)
    if m:
        events.append((m.group(1), m.group(2), m.group(3), m.group(4)))
start = next((e[0] for e in events if "report/start" in e[3] or "Report job" in e[3] or "report_start" in e[3]), events[0][0] if events else "")


def secs(t):
    a, b = datetime.strptime(start, "%H:%M:%S"), datetime.strptime(t, "%H:%M:%S")
    return int((b - a).total_seconds()) % 86400


MILESTONES = [
    ("intent / plan", r"intent|measure plan|Measure plan"),
    ("search", r"exa_search|Exa search|search_multi|web search"),
    ("relevance gate", r"relevan"),
    ("page fetch", r"fetch_source|enrich|Fetched|fetch status"),
    ("official data", r"Official data"),
    ("site crawl", r"crawl"),
    ("evidence store", r"evidence store"),
    ("V2 outline", r"outline"),
    ("V2 sections", r"V2:|section"),
    ("charts", r"[Cc]hart"),
    ("audit", r"audit"),
    ("publication", r"publication outcome|Report integrity|Publication report"),
    ("pdf", r"/pdf|PDF"),
]
say("# Full-cycle analysis\n")
say(f"**Question:** {capture.get('question')}  \n**Tier:** {capture.get('reportTier')} | **capture:** {Path(sys.argv[2]).name}\n")
say("## 1. Timeline (first / last log line per phase, seconds from job start)\n")
say("| Phase | First | Last | Lines |\n|---|---:|---:|---:|")
for label, pattern in MILESTONES:
    hits = [e for e in events if re.search(pattern, e[3])]
    if hits:
        say(f"| {label} | {secs(hits[0][0])} | {secs(hits[-1][0])} | {len(hits)} |")
say(f"\nTotal wall time (driver): **{summary.get('seconds')} s**\n")

# ---------- 2. External requests ----------
HTTP = re.compile(r'HTTP Request: (\w+) (\S+) "HTTP/[\d.]+ (\d{3})')
reqs = [(m.group(1), urlparse(m.group(2)).hostname or "?", m.group(3), m.group(2)) for e in events
        for m in [HTTP.search(e[3])] if m]
say("## 2. External HTTP requests (from httpx log lines)\n")
by_host = collections.defaultdict(collections.Counter)
for method, host, status, _ in reqs:
    by_host[host][status] += 1
say("| Host | Requests | Status codes |\n|---|---:|---|")
for host, counter in sorted(by_host.items(), key=lambda kv: -sum(kv[1].values()))[:25]:
    say(f"| {host} | {sum(counter.values())} | {dict(counter)} |")
gem = [r for r in reqs if "generativelanguage" in r[1]]
gem_models = collections.Counter((re.search(r"models/([^:]+)", r[3]) or [None, "?"])[1] + f" {r[2]}" for r in gem)
say(f"\nGemini calls by model and status: {dict(gem_models)}\n")

# ---------- 3. LLM calls captured (gateway) ----------
calls = capture.get("llmCalls") or []
say("## 3. LLM calls through the gateway (full prompts/responses in the capture)\n")
say("| # | Purpose | Model | OK | ms | Tokens in/out | Prompt chars | Response chars |\n|---:|---|---|---|---:|---|---:|---:|")
for c in calls:
    say(f"| {c['seq']} | {c['purpose']}{(' ' + c['sectionId']) if c.get('sectionId') else ''} | {c['model']} | "
        f"{'yes' if c['ok'] else 'NO: ' + c['error'][:40]} | {c['elapsedMs']} | {c.get('tokensIn')}/{c.get('tokensOut')} | "
        f"{len(c.get('llmPrompt') or '')} | {len(c.get('llmResponse') or '')} |")
total_ms = sum(c["elapsedMs"] for c in calls)
say(f"\nGateway calls: {len(calls)}, failed {sum(not c['ok'] for c in calls)}, total model time {total_ms/1000:.0f} s, "
    f"models {dict(collections.Counter(c['model'] for c in calls))}\n")

# ---------- 4. Evidence funnel ----------
say("## 4. Evidence funnel\n")
funnel = [
    ("search results before relevance", data("sources_before_relevance_filter").get("count")),
    ("after relevance gate", data("sources_after_relevance_filter").get("count")),
    ("queued for page fetch", data("sources_queued_for_enrichment").get("totalRelevantSources")),
    ("after article fetch", data("sources_after_article_fetch").get("sourceCount")),
    ("canonical manifest (sources)", data("canonical_source_manifest").get("sourceCount")),
    ("evidence store passages", len((data("evidence_store").get("passages") or {}))),
    ("writer source blocks", data("writer_source_evidence").get("sourceBlockCount")),
    ("sources cited in final report", len(set(re.findall(r"\[S(\d+)\]", result.get("report") or "")))),
]
for label, value in funnel:
    say(f"- {label}: **{value}**")
fs = data("fetch_status")
say(f"- fetch status: {fs.get('all')} | official: {fs.get('official')}")
od = data("official_datasets")
if od:
    say(f"- official datasets planned {[p.get('id') for p in od.get('plan', [])]}, fetched {[f.get('id') for f in od.get('fetched', [])]}, skipped {od.get('skipped')}")
sq = data("source_quality")
if sq:
    say(f"- source quality: {json.dumps({k: v for k, v in sq.items() if k != 'denylisted_hosts'})[:300]}")
searches = capture.get("searchCalls") or []
say(f"- search calls captured: {len(searches)}; queries: " +
    "; ".join(q for s in searches for q in (s.get("queries") or [])[:12])[:900])

# ---------- 5. V2 writing, numbers, charts ----------
pv = data("pipeline_v2")
say("\n## 5. V2 pipeline\n")
if pv.get("error"):
    say(f"**V2 FAILED:** {pv['error']}")
else:
    meta = (pv.get("outline") or {}).get("meta") or {}
    say(f"- outline: {[s['title'] for s in (pv.get('outline') or {}).get('sections', [])]}")
    say(f"- planned words {meta.get('plannedAnalysisWords')}, written {pv.get('words')} "
        f"({(pv.get('words') or 0) / max(1, meta.get('plannedAnalysisWords') or 1):.0%}), est. pages {pv.get('estimatedPages')}")
    np_ = pv.get("numberPolicy") or {}
    rem = np_.get("sentencesRemoved") or []
    say(f"- number policy: removed {len(rem)}, rewritten {len(np_.get('numbersRewritten') or [])}, "
        f"calculated {len(np_.get('calculatedLabelled') or [])}, unresolved table values {np_.get('unresolvedTableValues')}")
    for reason, n in collections.Counter(r["reason"].split(" ", 1)[1][:50] for r in rem).most_common():
        say(f"    - {n} x {reason}")
    for r in rem[:15]:
        say(f"    - `{r.get('number')}` — {r.get('sentence', '')[:150]}")
    ch = pv.get("charts") or {}
    say(f"- charts: candidates {ch.get('candidates')}, used {ch.get('used')}, target {(pv.get('chartTarget') or {}).get('target')}")
    for reason, n in collections.Counter(re.sub(r':.*', '', d["reason"])[:70] for d in ch.get("droppedBeforeWriting") or []).most_common():
        say(f"    - dropped before writing: {n} x {reason}")
    say(f"- chart funnel: {json.dumps(pv.get('chartFunnel'))[:500]}")
    say(f"- source disagreements: {pv.get('sourceDisagreements')}")
cp = ((stages.get("final_output") or {}).get("data") or {}).get("chartPipeline") or {}
say("- report-level chart pipeline: " + " -> ".join(f"{s['stage']}={s['count']}" for s in cp.get("stages") or []))
for s in cp.get("stages") or []:
    for d in s.get("removed") or []:
        say(f"    - {s['stage']} removed '{str(d.get('title'))[:60]}': {str(d.get('reason'))[:110]}")

# ---------- 6. Outcome ----------
say("\n## 6. Outcome\n")
for key in ("pipeline", "fallbackReason", "publicationStatus", "publicationOutcome", "failedStage", "failureSummary",
            "words", "charts", "sources", "citedIds", "disagreeNotes", "pdf"):
    say(f"- {key}: {summary.get(key)}")
say(f"- gates: {[(g['gate'], g['status'], g['failing']) for g in summary.get('publicationGates') or []]}")

# ---------- 7. Warnings / errors in the backend log ----------
say("\n## 7. Backend warnings and errors (deduplicated)\n")
issues = collections.Counter(re.sub(r"\d+", "N", e[3])[:150] for e in events if e[1] in {"WARNING", "ERROR", "CRITICAL"})
for text, n in issues.most_common(40):
    say(f"- {n} x {text}")
tracebacks = sum("Traceback" in line for line in log_lines)
say(f"\nTracebacks in log: {tracebacks}")

report = "\n".join(out)
(root / "cycle_analysis.md").write_text(report, encoding="utf-8")
print(report)
