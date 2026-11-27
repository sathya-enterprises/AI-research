"""Before/after measurements for report edits on a saved report (no LLM calls).

1. Waiting for an unreachable uploaded-document (RAG) service: the real wake loop with the old budget (100 s) and
   the edit budget (GG_ACTION_RAG_WAKE_S, 6 s). Run with the RAG service stopped.
2. Source re-verification for a one-paragraph edit: the real audit with real publisher fetches, full scope (before)
   vs changed-paragraph scope (after).
3. PDF render time for page mapping (the export handler on the same report).

usage: python scripts/edit_benchmark.py diagnostics/deep4/<run>/report.json [--skip-rag]
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.disable(logging.WARNING)


async def rag_wait(budget: float) -> float:
    from utils import rag_client as C
    t0 = time.perf_counter()
    await C.rag_report("5f0c2a9e-1d3b-4c7a-9e21-6b8d4f3a2c10", "benchmark", "action", wake_wait=budget)
    return round(time.perf_counter() - t0, 1)


async def audit(report: dict, scope: str) -> dict:
    import routes.report as R
    os.environ["GG_ACTION_AUDIT_SCOPE"] = scope
    fetched = []
    real = R._enrich_report_source

    async def counting(item, *a, **k):
        fetched.append(item.get("id"))
        return await real(item, *a, **k)

    R._enrich_report_source = counting
    text = report["report"]
    # a one-paragraph edit: append a sentence to the first cited prose line
    line = next(l for l in text.splitlines() if re.search(r"\[S\d+\]", l) and not l.startswith(("|", "#")))
    candidate = text.replace(line, line + " This trend continued into the next period " + re.findall(r"\[S\d+\]", line)[-1] + ".", 1)
    t0 = time.perf_counter()
    try:
        await R._audit_action_candidate(original_report=text, candidate_report=candidate, title=report.get("title", ""),
                                        original_charts=report.get("charts") or [], candidate_charts=report.get("charts") or [],
                                        original_images=[], candidate_images=[], original_sources=report.get("sources") or [],
                                        candidate_sources=report.get("sources") or [], report_tier=report.get("reportTier") or "in_depth")
    finally:
        R._enrich_report_source = real
    return {"scope": scope, "seconds": round(time.perf_counter() - t0, 1), "sourcesFetched": len(fetched),
            "citedSources": len(set(re.findall(r"\[(S\d+)\]", text)))}


async def render(report: dict) -> float:
    import routes.report as R
    t0 = time.perf_counter()
    pdf = await R._action_render_current_pdf(report)
    return round(time.perf_counter() - t0, 1), (len(pdf) if pdf else 0)


def main() -> None:
    report = json.load(open(sys.argv[1], encoding="utf-8"))
    out = {}
    if "--skip-rag" not in sys.argv:
        out["ragWaitSeconds"] = {"before_100s_budget": asyncio.run(rag_wait(100.0)), "after_6s_budget": asyncio.run(rag_wait(6.0))}
        print(json.dumps(out), flush=True)
    out["audit"] = [asyncio.run(audit(report, "full")), asyncio.run(audit(report, "changed"))]
    print(json.dumps(out["audit"]), flush=True)
    seconds, size = asyncio.run(render(report))
    out["pdfRenderSeconds"] = seconds
    out["pdfBytes"] = size
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
