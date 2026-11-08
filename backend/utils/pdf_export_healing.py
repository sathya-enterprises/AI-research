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
    report, dropped = drop_overlapping_tables(report, flagged=targets)
    actions += [f"dropped a table repeating the rows of an earlier one ({d})" for d in dropped]
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


def _row_numbers(line: str) -> frozenset:
    cells = [c.strip() for c in line.strip().strip("|").split("|")][1:]
    return frozenset(n.replace(",", "").lstrip("+") for n in re.findall(r"[-+−]?\d[\d,]*(?:\.\d+)?", " ".join(cells)))


def _row_label(line: str) -> str:
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    return re.sub(r"[^a-z0-9%]+", " ", re.sub(r"\*\*", "", cells[0]).casefold()).strip() if cells else ""


def drop_overlapping_tables(report: str, *, min_rows: int = 3, overlap: float = 0.75,
                            flagged: list[str] | None = None) -> tuple[str, list[str]]:
    """Two tables listing (mostly) the same rows - e.g. a writer's table and an evidence table placed from
    the same source in another section - are one observation shown twice (live run H: the rendered-PDF
    audit blocked export). The later table and its caption are dropped; returns what was dropped."""
    lines = report.split("\n")
    blocks, i = [], 0
    while i < len(lines):
        if lines[i].strip().startswith("|"):
            start = i
            while i < len(lines) and lines[i].strip().startswith("|"):
                i += 1
            rows = [ln for ln in lines[start + 1:i] if not re.fullmatch(r"\s*\|[\s:|-]+\|?\s*", ln)]
            labels = {_row_label(r): _row_numbers(r) for r in rows if _row_label(r)}
            blocks.append((start, i, labels))
        else:
            i += 1
    drop, dropped = [], []
    for j, (start, end, labels) in enumerate(blocks):
        if len(labels) < min_rows:
            continue
        for _s, _e, earlier in blocks[:j]:
            shared = [k for k in labels if k in earlier]
            # Same rows AND the same values: the same observation twice (not the same sectors on another metric).
            same_values = [k for k in shared if labels[k] and earlier[k]
                           and len(labels[k] & earlier[k]) >= min(2, len(labels[k]), len(earlier[k]))]
            if len(earlier) >= min_rows and len(same_values) >= overlap * min(len(labels), len(earlier)):
                if flagged is not None and not any(_norm(f) and any(_norm(f) in k or k in _norm(f) for k in labels) for f in flagged):
                    continue  # only a table holding a line the rendered-PDF audit flagged
                cap = start
                while cap > 0 and (not lines[cap - 1].strip() or lines[cap - 1].startswith("**Table")):
                    cap -= 1
                    if lines[cap].startswith("**Table"):
                        break
                drop.append((cap if lines[cap].startswith("**Table") else start, end))
                dropped.append(", ".join(sorted(labels))[:120])
                break
    for start, end in reversed(drop):
        del lines[start:end]
    return "\n".join(lines), dropped
