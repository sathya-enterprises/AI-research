"""Export-time self-healing for the rendered-PDF audit (Stage G).

The rendered-PDF audit blocks an export when the final PDF repeats the same numeric observation.
Besides an exact duplicate Markdown line (repaired in routes/pdf.py), two cases came from live runs:

  * one sentence used as the caption / takeaway of two figures ("Figure 21 and Figure 22 compare ...");
  * the same table header (and its rows) shown twice ("QTD RETURN TO NOV 2026 (%)").

Remedy: keep the first occurrence; the later figure gets a caption naming its own data, the later
table copy is dropped. Values are never edited. Returns what changed so the caller can re-render and
re-audit; an unresolved finding still blocks (nothing is hidden).
"""
from __future__ import annotations

import re


def _norm(text) -> str:
    text = re.sub(r"\[\s*S\d+(?:\.\d+)?(?:\s*[,;]\s*S\d+(?:\.\d+)?)*\s*\]", "", str(text or ""), flags=re.I)
    return re.sub(r"[^a-z0-9%₹$]+", " ", text.casefold()).strip()


def _duplicate_texts(findings: list[dict]) -> list[str]:
    return [_norm(f.get("text")) for f in findings or []
            if isinstance(f, dict) and "repeats the same numeric observation" in str(f.get("issue") or "").casefold()
            and _norm(f.get("text"))]


def heal_rendered_duplicates(report: str, charts: list, findings: list[dict]) -> tuple[str, list, list[str]]:
    targets = _duplicate_texts(findings)
    actions: list[str] = []
    if not targets:
        return report, charts, actions
    charts = [dict(c) if isinstance(c, dict) else c for c in charts or []]
    for target in targets:
        # 1) A caption / takeaway shared by two figures: the later figure names its own data.
        holders = [i for i, c in enumerate(charts) if isinstance(c, dict)
                   and any(target and target in _norm(c.get(k)) for k in ("takeaway", "rationale", "caption", "subtitle"))]
        for i in holders[1:]:
            title = re.sub(r"^\s*Figure\s+\d+\s*:\s*", "", str(charts[i].get("title") or "this chart"))
            for key in ("takeaway", "rationale", "caption", "subtitle"):
                if target in _norm(charts[i].get(key)):
                    charts[i][key] = f"Shows {title}." if key != "rationale" else f"Supports: the data in {title}."
            actions.append(f"figure {i + 1}: own caption instead of a repeated one")
        # 2) The same table (header + rows) twice: drop the later copy.
        lines = report.split("\n")
        header_rows = [n for n, line in enumerate(lines) if line.strip().startswith("|") and target in _norm(line)]
        if len(header_rows) >= 2:
            first_block = _table_block(lines, header_rows[0])
            later = _table_block(lines, header_rows[-1])
            if later and first_block and _norm("\n".join(lines[later[0]:later[1]])) == _norm("\n".join(lines[first_block[0]:first_block[1]])):
                start = later[0] - 2 if later[0] >= 2 and lines[later[0] - 2].startswith("**Table") else later[0]
                del lines[start:later[1]]
                report = "\n".join(lines)
                actions.append("dropped a repeated table")
    return report, charts, actions


def _table_block(lines: list[str], row: int) -> tuple[int, int] | None:
    if not (0 <= row < len(lines)) or not lines[row].strip().startswith("|"):
        return None
    start = row
    while start > 0 and lines[start - 1].strip().startswith("|"):
        start -= 1
    end = row
    while end < len(lines) and lines[end].strip().startswith("|"):
        end += 1
    return start, end


def export_failure_payload(audit: dict, findings: list[dict]) -> dict:
    """A clear, actionable error for a report that passed publication but whose PDF export failed."""
    first = next((f for f in findings or [] if isinstance(f, dict)), {})
    issue = str(first.get("issue") or "an integrity/evidence check on the rendered PDF failed")
    where = str(first.get("text") or first.get("chartTitle") or "")[:160]
    return {
        "error": "PDF export blocked by the final check on the rendered file (the report itself is unchanged and still "
                 "available). Reason: " + issue + (f" Example: \"{where}\"." if where else ""),
        "code": "pdf_export_audit_failed",
        "reportAvailable": True,
        "draftExportAvailable": True,
        "nextStep": "Open the report on screen, or export it as a labelled draft (draftExport=true).",
        "publicationStatus": "FAIL", "exported": False,
        "renderedPdfAudit": audit, "findings": list(findings or [])[:20],
    }
