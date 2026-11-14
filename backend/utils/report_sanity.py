"""Last-line checks that a finished report body is real, readable prose.

Every content gate checks *claims* (numbers, citations, charts). None of them noticed when the
whole body was mangled: a recovery bug once put every character on its own line, and a provider
outage produced a 1.2k-character report from the no-LLM evidence digest, both published as PASS.
These checks look only at the shape of the text and at who wrote it, so they hold for any topic.
"""
from __future__ import annotations

import re

# The provider-independent digest written when every LLM writer failed (routes/report.py).
NO_LLM_WRITER_MODELS = {"local:evidence-fallback"}

# A report below this many prose words is not a detailed/in-depth research report.
MIN_PROSE_WORDS = {"detailed": 350, "in_depth": 600}

_TABLE_RULE_RE = re.compile(r"^\s*\|?\s*:?-{2,}")
_MARKER_RE = re.compile(r"^\s*(?:\[CHART_\d+\]|\[FILE_IMG_\d+\]|[-*_]{3,}|#{1,6}|[-*+>]|\d+[.)])\s*$")


def _prose_lines(text: str) -> list[str]:
    return [
        line for line in str(text or "").splitlines()
        if line.strip() and not _TABLE_RULE_RE.match(line) and not _MARKER_RE.match(line)
    ]


def fragmented_text(text: str) -> bool:
    """True when most lines hold one or two characters (text split character by character)."""
    lines = _prose_lines(text)
    if len(lines) < 40:
        return False
    tiny = sum(1 for line in lines if len(line.strip()) <= 2)
    return tiny / len(lines) >= 0.35


def prose_word_count(text: str) -> int:
    """Words in the body text and table cells (attachment reports are legitimately table-heavy)."""
    body = []
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "[CHART_", "[FILE_IMG_")) or _TABLE_RULE_RE.match(stripped):
            continue
        body.append(stripped)
    return len(re.findall(r"[A-Za-z][A-Za-z'’-]+", " ".join(body)))


def report_integrity_issues(report_text: str, tier: str, writer_model: str = "") -> list[dict]:
    """Issues that must stop a report from being presented as a normal published result."""
    issues: list[dict] = []
    if str(writer_model or "").strip() in NO_LLM_WRITER_MODELS:
        issues.append({
            "stage": "writer_unavailable",
            "issue": "Every report-writing model was unavailable, so this text is an automatic evidence digest "
                     "rather than a written analysis. Please run the report again.",
        })
    if fragmented_text(report_text):
        issues.append({
            "stage": "report_integrity",
            "issue": "The report text is broken into single characters per line and is not readable.",
        })
    minimum = MIN_PROSE_WORDS.get(str(tier or "").casefold())
    words = prose_word_count(report_text)
    if minimum and words < minimum:
        issues.append({
            "stage": "report_integrity",
            "issue": f"The report body has only {words} words of prose (minimum {minimum} for this tier).",
        })
    return issues


def apply_integrity_outcome(outcome: dict, payload: dict, issues: list[dict]) -> dict:
    """Make a report with integrity issues a labelled draft and keep the status fields consistent."""
    if not issues:
        return outcome
    categories = list(dict.fromkeys(issue["stage"] for issue in issues))
    summary = issues + [item for item in (outcome.get("failureSummary") or []) if item not in issues]
    if outcome.get("outcome") == "structural_failure":
        return {**outcome, "failureSummary": summary,
                "blockingCategories": list(dict.fromkeys([*categories, *(outcome.get("blockingCategories") or [])]))}
    updated = {**outcome, "outcome": "draft", "failedStage": categories[0],
               "blockingCategories": list(dict.fromkeys([*categories, *(outcome.get("blockingCategories") or [])])),
               "failureSummary": summary}
    payload["failedStage"] = updated["failedStage"]
    payload["failureSummary"] = updated["failureSummary"]
    payload["blockingCategories"] = updated["blockingCategories"]
    payload["draftAvailable"] = True
    if str(payload.get("publicationStatus") or "").upper() == "PASS":
        payload["publicationStatus"] = "WARNING"
    return updated
