"""Stage I gates on the final report and on the rendered PDF, run inside the job before publication.

Text gates (self-healing on the Markdown):
  * leak            - writer/prompt instructions (``leak_gate``)            -> removed
  * text_art        - arrow chains, box art, flattened tables (``flow_text``) -> converted / dropped
  * period          - a table whose period ends before the question's window, outside the labelled
                      historical block                                        -> table dropped
Rendered gates (on the PDF text layer, PyMuPDF):
  * rendered_flow   - arrow chains / flattened table text                     -> healed via text_art
  * split_words     - a word broken across a line ("MON THS")                 -> warning
  * label_collision - two text spans drawn on top of each other               -> warning

``rendered_check(payload, build, audit)`` renders the payload, audits it (``routes.pdf``'s audit plus the
gates above), heals for up to 3 rounds (re-rendered and re-audited each time) and returns a summary;
blocking findings that remain keep the report a draft.
"""
from __future__ import annotations

import logging
import re
from datetime import date

log = logging.getLogger("stage_i_gates")

_HISTORICAL_RE = re.compile(r"(?i)^\*\*Historical reference\b")


# ── text gates ───────────────────────────────────────────────────────────────────────────────────
def period_findings(report: str, window_start: date | None) -> list[dict]:
    if window_start is None:
        return []
    from utils.period_hygiene import in_window, table_period
    lines = str(report or "").split("\n")
    out, historical = [], False
    for i, line in enumerate(lines):
        if _HISTORICAL_RE.match(line.strip()):
            historical = True
        elif re.match(r"^##\s", line):
            historical = False
        if historical or not (line.strip().startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|?\s*:?-{3,}", lines[i + 1])):
            continue
        k = i - 1
        while k >= 0 and not lines[k].strip():
            k -= 1
        caption = lines[k] if k >= 0 and re.match(r"^\s*(?:\*\*Table|#{3,4}\s)", lines[k]) else ""
        # "since 2012" / "from 2012" is where a measure starts, not when the data ends
        start_only = re.compile(r"(?i)\b(?:since|from)\s+(?:\w+\s+)?\d{4}")
        _label, end = table_period(start_only.sub("", caption), [start_only.sub("", c.strip()) for c in line.split("|")])
        if end is not None and not in_window(end, window_start):
            out.append({"gate": "period", "line": i, "caption": caption.strip()[:120], "ends": str(end)})
    return out


def _drop_table_at(lines: list[str], header_index: int) -> list[str]:
    j = header_index + 2
    while j < len(lines) and lines[j].strip().startswith("|"):
        j += 1
    start = header_index
    k = header_index - 1
    while k >= 0 and not lines[k].strip():
        k -= 1
    if k >= 0 and re.match(r"^\s*(?:\*\*Table|#{3,4}\s)", lines[k]):
        start = k
    after = j
    while after < len(lines) and not lines[after].strip():
        after += 1
    if after < len(lines) and re.match(r"^\s*\*?Source:", lines[after]):
        j = after + 1
    return lines[:start] + lines[j:]


def heal_text(report: str, window_start: date | None = None) -> tuple[str, list[str]]:
    """Run the text gates and their remedies; returns (report, actions)."""
    actions: list[str] = []
    from utils.leak_gate import remove_leaks
    report, leaks = remove_leaks(report)
    actions += [f"leak: {a if isinstance(a, str) else a.get('action', a)}"[:200] for a in leaks]
    from utils.flow_text import convert_text_art
    report, art = convert_text_art(report)
    actions += [f"text_art: {a}" for a in art]
    for finding in reversed(period_findings(report, window_start)):
        report = "\n".join(_drop_table_at(report.split("\n"), finding["line"]))
        actions.append(f"period: dropped an out-of-window table ({finding['caption'] or 'uncaptioned'}, ends {finding['ends']})")
    return re.sub(r"\n{3,}", "\n\n", report), actions


# ── rendered gates ───────────────────────────────────────────────────────────────────────────────
def split_word_findings(rendered_text: str, source_text: str) -> list[dict]:
    """'MON THS': two fragments that are not words on their own but join into a word of the report."""
    vocab = {w.casefold() for w in re.findall(r"[A-Za-z]{5,}", source_text or "")}
    small = {w.casefold() for w in re.findall(r"[A-Za-z]{2,4}", source_text or "")}
    out = []
    for m in re.finditer(r"(?=\b([A-Za-z]{2,5}) ([A-Za-z]{2,6})\b)", rendered_text or ""):  # overlapping pairs
        a, b = m.group(1), m.group(2)
        joined = (a + b).casefold()
        if joined in vocab and a.casefold() not in small and b.casefold() not in small:
            out.append({"severity": "warning", "issue": "A word is split in the rendered PDF.", "text": f"{a} {b}"})
    return out[:20]


def collision_findings(pdf_bytes: bytes, min_overlap: float = 0.35) -> list[dict]:
    try:
        import pymupdf as fitz
    except ImportError:  # pragma: no cover
        try:
            import fitz
        except ImportError:
            return []
    out = []
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception:
        return []
    try:
        for page_no, page in enumerate(doc, 1):
            spans = []
            for block in page.get_text("dict").get("blocks", []):
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        text = str(span.get("text") or "").strip()
                        if text:
                            spans.append((fitz.Rect(span["bbox"]), text))
            spans.sort(key=lambda s: s[0].y0)
            for i, (ra, ta) in enumerate(spans):
                for rb, tb in spans[i + 1:]:
                    if rb.y0 > ra.y1:
                        break
                    inter = ra & rb
                    if inter.is_empty or ta == tb:
                        continue
                    small = min(ra.get_area(), rb.get_area()) or 1
                    if inter.get_area() / small >= min_overlap:
                        out.append({"severity": "warning", "issue": "Two labels overlap in the rendered PDF.",
                                    "page": page_no, "text": f"{ta[:40]} / {tb[:40]}"})
                        if len(out) >= 20:
                            return out
    finally:
        doc.close()
    return out


def rendered_findings(pdf_bytes: bytes, rendered_text: str, source_text: str) -> list[dict]:
    from utils.flow_text import rendered_flow_findings
    from utils.leak_gate import INSTRUCTION_RE
    findings = list(rendered_flow_findings(rendered_text))
    for m in INSTRUCTION_RE.finditer(rendered_text or ""):
        findings.append({"severity": "critical", "issue": "The final PDF shows writer instructions.",
                         "text": rendered_text[max(0, m.start() - 40):m.end() + 40]})
        break
    findings += split_word_findings(rendered_text, source_text)
    findings += collision_findings(pdf_bytes)
    return findings


# ── the in-job rendered check ────────────────────────────────────────────────────────────────────
def rendered_check(payload: dict, *, build, audit, window_start: date | None = None, rounds: int = 3,
                   repairs=None) -> dict:
    """``build(payload) -> pdf bytes``; ``audit(pdf_bytes, payload) -> {"findings": [...]}`` (routes.pdf's
    rendered audit). Heals and re-renders up to ``rounds`` times. Mutates payload["report"/"charts"]."""
    from utils.pdf_export_healing import heal_rendered_duplicates
    summary = {"rounds": 0, "actions": [], "blocking": [], "warnings": [], "pages": None}
    report, text_actions = heal_text(str(payload.get("report") or ""), window_start)
    payload["report"] = report
    summary["actions"] += text_actions
    for round_no in range(rounds + 1):
        try:
            pdf_bytes = build(payload)
        except Exception as exc:
            summary["error"] = f"render failed: {type(exc).__name__}"
            log.warning("Stage I rendered check: render failed (%s)", type(exc).__name__)
            return summary
        try:
            import pymupdf as fitz
        except ImportError:  # pragma: no cover
            import fitz
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        rendered_text = "\n".join(page.get_text("text") or "" for page in doc)
        summary["pages"] = len(doc)
        doc.close()
        base = audit(pdf_bytes, payload) or {}
        findings = [f for f in base.get("findings") or [] if f.get("severity", "critical") == "critical"]
        seen = {str(f.get("issue")) + str(f.get("text") or f.get("line") or "") for f in findings}
        for f in rendered_findings(pdf_bytes, rendered_text, payload.get("report") or ""):
            key = str(f.get("issue")) + str(f.get("text") or f.get("line") or "")
            if key not in seen:
                findings.append(f)
                seen.add(key)
        blocking = [f for f in findings if f.get("severity", "critical") == "critical"]
        summary["warnings"] = [f for f in findings if f.get("severity") == "warning"]
        summary["blocking"] = blocking
        if not blocking or round_no == rounds:
            break
        summary["rounds"] = round_no + 1
        actions = []
        report = str(payload.get("report") or "")
        # flattened arrows / tables or instructions seen in the PDF: heal the source text again, and
        # rewrite any rendered arrow chain found verbatim in the source
        for f in blocking:
            text = str(f.get("text") or "")
            if "arrow" in str(f.get("issue")) and text:
                from utils.flow_text import convert_text_art
                for line in report.split("\n"):
                    if text[:40] and text[:40] in line:
                        new, _ = convert_text_art(line + "\n")
                        report = report.replace(line, new.rstrip("\n"))
                        actions.append("rewrote a rendered arrow chain")
        report, more = heal_text(report, window_start)
        actions += more
        report, charts, dup_actions = heal_rendered_duplicates(report, payload.get("charts") or [], blocking)
        actions += dup_actions
        if repairs is not None:  # the export path's own repairs (chart-audit repair, exact duplicate lines)
            try:
                report, charts, more = repairs(report, charts, blocking)
                actions += more
            except Exception as exc:
                log.warning("Stage I rendered check: export repairs failed (%s)", type(exc).__name__)
        if not actions:
            break
        payload["report"], payload["charts"] = report, charts
        summary["actions"] += actions
        log.warning("Stage I rendered check: round %d healed %d item(s)", round_no + 1, len(actions))
    return summary
