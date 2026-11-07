"""V2 number check = report.py's source-evidence check (Stage E).

V2's own token check ignored units (crore / lakh crore / trillion / billion / %)
and what a number measures, so it passed claims that report.py's publication
check then failed. Rather than re-implement that check, V2 calls the SAME
functions report.py uses (_attach_audit_source_evidence ->
_merge_source_evidence_statuses -> _source_evidence_quality_audit) on its own
assembled text, and fixes what they flag BEFORE report.py's final check runs:

  * a flagged claim is dropped with a log line (a bullet loses its line, a table
    row its row); the flag is never hidden -- whatever is left is checked again
    and anything still flagged stays visible to report.py.

Live, report.py passes an auditor bound to its own manifest and fetched pages.
Offline (replay, tests) the auditor reads the evidence-store passages instead.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger("evidence_check")

CITE_SRC_RE = re.compile(r"\[(S\d+(?:\s*[,;]\s*S\d+)*)\]")


def store_auditor(store, manifest: list[dict], fetched_rows: list[dict] | None = None, charts: list | None = None):
    """An auditor for offline replays and tests: the live run's fetched page rows where the capture
    has them, the evidence-store passages for every source, and chart rows registered on the
    manifest the way report.py registers V2 chart evidence."""
    from routes import report as R

    texts: dict[str, list[str]] = {}
    for passage in store.passages:
        texts.setdefault(passage.source_id, []).append(str(passage.text or ""))
    rows = [m for m in manifest if isinstance(m, dict)]  # shared: chart evidence registered on them counts
    if charts:
        R._register_v2_chart_evidence(charts, store)
    by_url = {str(r.get("url") or "").rstrip("/"): r for r in (fetched_rows or []) if isinstance(r, dict)}
    enriched = []
    for m in rows:
        sid = str(m.get("id") or "").upper()
        fetched = by_url.get(str(m.get("url") or "").rstrip("/"), {})
        body = "\n\n".join([str(fetched.get("fullContent") or ""), *texts.get(sid, [])]).strip()
        if not body:
            continue
        enriched.append({**fetched, **m, "id": sid, "fullContent": body, "pageContent": body,
                         "snippet": str(fetched.get("snippet") or body[:600]), "pageFetchStatus": "ok"})
    return manifest_auditor(rows, enriched)


def manifest_auditor(manifest: list[dict], enriched: list[dict]):
    """An auditor bound to a source manifest and fetched source rows (what report.py's final check uses)."""
    from routes import report as R

    def audit(report_text: str) -> list[dict]:
        # Exactly what report.py's final check runs (no status merge in between).
        attached = R._attach_audit_source_evidence(manifest, enriched, report_text)
        result = R._source_evidence_quality_audit(attached)
        return [g for g in (result.get("mismatchedCitationGroups") or []) if isinstance(g, dict)]

    return audit


def _norm(text: str) -> str:
    text = CITE_SRC_RE.sub(" ", re.sub(r"[*_`]", "", str(text or "")))
    return re.sub(r"\W+", " ", text.casefold()).strip()


def _locate(report_md: str, claim: str) -> tuple[int, int] | None:
    """(start, end) of the sentence / bullet / table row in ``report_md`` that the audit's claim text came from."""
    target = _norm(claim)[:160]
    if len(target) < 12:
        return None
    for line_match in re.finditer(r"(?m)^.*$", report_md):
        line = line_match.group(0)
        if not line.strip():
            continue
        if line.lstrip().startswith(("|", "-", "*", ">")) and target[:60] in _norm(line):
            return line_match.start(), line_match.end()
        # Whole sentences only: a full stop inside a number ("0.5% to 0.7%") does not end a sentence.
        # The audit's claim text can be a fragment of one (it splits at decimals), so the sentence
        # that CONTAINS the claim fragment is the one removed.
        bounds = [0] + [m.end() for m in re.finditer(r"(?<!\d)[.!?](?:\s*\[[^\]]*\])*(?=\s+[A-Z*\[(\"'₹$]|\s*$)", line)]
        for start, end in zip(bounds, bounds[1:] + ([len(line)] if bounds[-1] < len(line) else [])):
            sentence = line[start:end]
            norm = _norm(sentence)
            if norm and (target[:80] in norm or (len(norm) >= 24 and norm[:80] in target)):
                lead = len(sentence) - len(sentence.lstrip())
                return line_match.start() + start + lead, line_match.start() + end
    return None


def fix_flagged_claims(report_md: str, auditor, *, max_rounds: int = 2) -> tuple[str, list[dict], list[dict]]:
    """Drop claims report.py's evidence check flags; returns (text, removed, still_flagged)."""
    removed: list[dict] = []
    flagged: list[dict] = []
    for _round in range(max_rounds):
        try:
            flagged = auditor(report_md)
        except Exception as exc:  # the check must never break the report
            log.warning("V2 evidence check failed: %s", type(exc).__name__)
            return report_md, removed, []
        if not flagged:
            break
        changed = False
        for group in flagged:
            span = _locate(report_md, str(group.get("claim") or ""))
            if not span:
                continue
            start, end = span
            snippet = report_md[start:end]
            values = list(group.get("unmatchedNumericClaims") or [])[:6]
            reason = ("cited sources hold the number for a different metric" if group.get("subjectMismatchTokens")
                      else "number not found in the cited sources (value or unit)")
            line_start = report_md.rfind("\n", 0, start) + 1
            line_end = report_md.find("\n", end)
            line_end = len(report_md) if line_end < 0 else line_end
            whole_line = report_md[line_start:line_end]
            if whole_line.lstrip().startswith(("|", "-", "*", ">")) or whole_line.strip() == snippet.strip():
                report_md = report_md[:line_start] + report_md[line_end + 1:]
            else:
                report_md = report_md[:start] + report_md[end:]
            removed.append({"claim": re.sub(r"\s+", " ", snippet).strip()[:240], "values": values, "reason": reason,
                            "sources": list(group.get("sourceIds") or [])[:6]})
            log.info("V2 evidence check removed a claim (%s): %s", reason, ", ".join(map(str, values))[:120])
            changed = True
        report_md = re.sub(r"[ \t]{2,}", " ", re.sub(r"\n{3,}", "\n\n", report_md))
        if not changed:
            break
    else:
        try:
            flagged = auditor(report_md)
        except Exception:
            flagged = []
    return report_md, removed, flagged
