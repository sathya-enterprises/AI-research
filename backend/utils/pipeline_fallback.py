"""No silent fallback to the legacy writer (Stage H).

When the V2 pipeline was requested but could not run (live run G: a network failure on the outline
calls), report.py falls back to the single-call writer. That report is shorter and skips the fact
sheet, dataset charts, self-healing gates and coverage note, so it must never look like a normal
deep-research result:

  * every response gets ``pipeline`` ("v2" | "legacy_fallback" | "legacy") and, for a fallback,
    ``fallbackReason``;
  * an in-depth fallback becomes a labelled draft (failedStage "pipeline_fallback") with the reason
    in ``failureSummary``; the existing frontend shows its draft banner and "Download draft PDF";
  * a detailed fallback keeps its gate outcome but is labelled in the response and the publication
    report.
"""
from __future__ import annotations


def label_fallback(payload: dict, outcome: dict, *, v2_used: bool, v2_requested: bool, fallback_reason: str,
                   tier: str) -> dict:
    if v2_used:
        payload["pipeline"] = "v2"
        return outcome
    if not (v2_requested or fallback_reason):
        payload["pipeline"] = "legacy"
        return outcome
    reason = fallback_reason or "the V2 pipeline did not run"
    payload["pipeline"] = "legacy_fallback"
    payload["fallbackReason"] = reason
    if str(tier or "").casefold() != "in_depth":
        return outcome
    item = {"stage": "pipeline_fallback",
            "issue": ("The in-depth research pipeline could not run (" + reason[:200] + "); this shorter report was "
                      "written by the fallback writer and is not a full deep-research report. Please run it again.")}
    payload["failedStage"] = "pipeline_fallback"
    payload["failureSummary"] = [item] + list(payload.get("failureSummary") or [])
    payload["draftAvailable"] = True
    return {**outcome, "outcome": "draft", "failedStage": "pipeline_fallback",
            "blockingCategories": ["pipeline_fallback"],
            "failureSummary": [item] + list(outcome.get("failureSummary") or [])}
