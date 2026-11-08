"""Text-art clean-up (Stage I, FIX 3 and FIX 6).

The PDF renders Markdown, not monospace art, so writer-produced diagrams arrive flattened:
``- ├─> Elevated yields ──> Higher term premiums [S10]`` or ``Rates > Margins > Earnings``. Charts already
carry the datasets, so text-art bars are removed too.

``convert_text_art(text) -> (text, actions)``
  * a chain of arrows (``──>``, ``->``, ``→``, ``⇒``, or two or more `` > `` between words) becomes a sentence:
    "A, leading to B, leading to C." (citations kept at the end);
  * a leading tree branch (``├─>``, ``└─>``) is a list marker; connector-only lines (``│``) are dropped;
  * a line with two or more branch markers is a side-by-side diagram that cannot be linearised faithfully
    and is dropped;
  * a line holding a text-art bar (a run of block characters) is dropped;
  * code fences that contained only text art are removed with it.
Markdown tables and ``>`` block quotes / directives are never touched.

``rendered_flow_findings(text)`` is the rendered-PDF gate: arrow chains and flattened table residue
("| --- |", rows of pipe-separated cells) left in the extracted PDF text.
"""
from __future__ import annotations

import re

_BOX = "├└┌┐┘┤┬┴┼│─━┃┏┓┗┛┣┫┳┻╋═║╔╗╚╝╠╣╦╩╬"
_BRANCH_RE = re.compile(r"[├└┣┗][─━-]*>?")
_ARROW_RE = re.compile(r"\s*(?:[─━]+>|-{1,2}>|=>|→|⇒|➜|➔|⟶)\s*")
_GT_CHAIN_RE = re.compile(r"(?<=[A-Za-z)\]%])\s+>\s+(?=[A-Za-z(])")
_BAR_RE = re.compile(r"[█▇▆▅▄▃▂▁■▓▒░]{3,}|(?<![|\w])#{6,}(?![\w])")
_CITE_TAIL_RE = re.compile(r"\s*(\[\s*S\d+(?:\s*[,;]\s*S\d+)*\s*\])\s*$")
_CONNECTOR_ONLY_RE = re.compile(rf"^[\s\-*•{_BOX}|]*$")


def _clean(part: str) -> str:
    part = re.sub(rf"[{_BOX}]+", " ", part)
    return re.sub(r"\s+", " ", part).strip(" -:;,.")


def _chain_sentence(parts: list[str], cite: str) -> str:
    parts = [p for p in (_clean(x) for x in parts) if p]
    if not parts:
        return ""
    if len(parts) == 1:
        sentence = parts[0]
    else:
        sentence = parts[0] + "".join(f", leading to {p}" for p in parts[1:])
    return sentence.rstrip(".") + (f" {cite}" if cite else "") + "."


def _convert_line(line: str) -> tuple[str | None, str | None]:
    """Return (new_line or None to drop, action) for one line outside tables/quotes."""
    if _BAR_RE.search(line):
        return None, "removed a text-art bar"
    stripped = line.strip()
    bullet = ""
    m = re.match(r"^([-*•]\s+|\d+[.)]\s+)", stripped)
    if m:
        bullet, stripped = m.group(1), stripped[m.end():]
    if bullet and stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") == 2:
        stripped = stripped.strip("| ").strip()  # a framed diagram row flattened into a bullet
        line = bullet + stripped
        framed = True
    else:
        framed = False
    has_box = framed or any(ch in stripped for ch in _BOX)
    if has_box and _CONNECTOR_ONLY_RE.match(stripped):
        return None, "removed a diagram connector line"
    branches = _BRANCH_RE.findall(stripped)
    if len(branches) >= 2:
        return None, "removed a side-by-side text diagram line"
    if branches and stripped.startswith(branches[0]):
        stripped = stripped[len(branches[0]):].strip()
        bullet = bullet or "- "
    if not has_box and not branches:
        # prose (or a plain bullet) with an inline chain: rewrite the arrows in place
        new, n = _ARROW_RE.subn(", leading to ", line)
        if not n and len(_GT_CHAIN_RE.findall(line)) >= 2:
            new, n = _GT_CHAIN_RE.subn(", leading to ", line)
        return (new, "converted an arrow chain into a sentence") if n else (line, None)
    cite = ""
    cm = _CITE_TAIL_RE.search(stripped)
    if cm:
        cite, stripped = cm.group(1), stripped[:cm.start()]
    parts = _ARROW_RE.split(stripped)
    if len(parts) < 2:
        gt = _GT_CHAIN_RE.split(stripped)
        parts = gt if len(gt) >= (2 if has_box else 3) else parts
    if len(parts) >= 2:
        return (bullet or "") + _chain_sentence(parts, cite), "converted an arrow chain into a sentence"
    if has_box or branches:
        cleaned = _clean(stripped)
        if re.match(r"^\d+[.)]\s", cleaned):
            bullet = ""  # keep the diagram's own numbering
        return ((bullet + cleaned + (f" {cite}" if cite else "")) if cleaned else None), "removed diagram box characters"
    return line, None


_FLAT_SEP_RE = re.compile(r"^\s*-{3}(?:\s+—\s+-{3})+\s*$")
_FLAT_ROW_RE = re.compile(r"^\s*[^—\n]+(?:\s+—\s+[^—\n]+){2,}\s*$")


def drop_flattened_tables(lines: list[str]) -> tuple[list[str], int]:
    """Remove a table already flattened to "--- — ---" / "a — b — c" text, with its caption and Source line."""
    drop: set[int] = set()
    blocks = 0
    i = 0
    while i < len(lines):
        if not _FLAT_SEP_RE.match(lines[i]):
            i += 1
            continue
        start, end = i, i
        while end + 1 < len(lines) and _FLAT_ROW_RE.match(lines[end + 1]):
            end += 1
        before = start - 1
        while before >= 0 and not lines[before].strip():
            before -= 1
        if before >= 0 and (_FLAT_ROW_RE.match(lines[before]) or re.match(r"^\s*\*\*Table\b", lines[before])):
            start = before  # a header row printed as text, or the caption
            if before - 1 >= 0 and re.match(r"^\s*\*\*Table\b", lines[before - 1]):
                start = before - 1
        after = end + 1
        if after < len(lines) and re.match(r"^\s*\*?Source:", lines[after]):
            end = after
        drop.update(range(start, end + 1))
        blocks += 1
        i = end + 1
    return [line for n, line in enumerate(lines) if n not in drop], blocks


def convert_text_art(text: str) -> tuple[str, list[str]]:
    lines = str(text or "").split("\n")
    lines, flattened = drop_flattened_tables(lines)
    actions_pre = ["dropped a flattened table without a header row"] * flattened
    out: list[str] = []
    actions: list[str] = list(actions_pre)
    fence_start: int | None = None
    fence_changed = False
    for line in lines:
        s = line.lstrip()
        if s.startswith("```"):
            if fence_start is None:
                fence_start, fence_changed = len(out), False
                out.append(line)
            else:
                if fence_changed:  # the fence held text art: drop both fence markers
                    out.pop(fence_start)
                    actions.append("removed a code fence around text art")
                else:
                    out.append(line)
                fence_start = None
            continue
        if s.startswith("|") or s.startswith(">") or s.startswith("#") or not s:
            out.append(line)
            continue
        new, action = _convert_line(line)
        if action:
            actions.append(action)
            if fence_start is not None:
                fence_changed = True
        if new is not None:
            out.append(new)
    # tidy what diagram clean-up leaves: empty bullets, and citation-only bullets merged into the line above
    tidy: list[str] = []
    for line in out:
        bare = line.strip()
        if bare in {"-", "*", "•"}:
            continue
        cite_only = re.fullmatch(r"[-*•]\s*(\[\s*S\d+(?:\s*[,;]\s*S\d+)*\s*\])\.?", bare)
        if cite_only and tidy and tidy[-1].strip():
            tidy[-1] = tidy[-1].rstrip().rstrip(".") + f" {cite_only.group(1)}"
            continue
        tidy.append(line)
    result = "\n".join(tidy)
    # a header bullet left dangling right before converted branches reads as a lead-in
    return result, actions


_RENDERED_CHAIN_RE = re.compile(r"[A-Za-z)\]]\s*(?:>|→|⇒|──>|->)\s*[A-Za-z(][^\n]*?(?:>|→|⇒|──>|->)\s*[A-Za-z(]")
_RENDERED_TABLE_RESIDUE_RE = re.compile(
    r"\|\s*-{3,}\s*\||-{3}\s+—\s+-{3}|"  # a separator row printed as text
    r"(?:^|\s)[A-Za-z][\w &.-]{1,30}(?:\s+[—|]\s+[+\-−]?\d[\d.,]*%?){3,}")  # "Metal — -7.34% — +26.36% — +7.96%"


def rendered_flow_findings(rendered_text: str) -> list[dict]:
    findings: list[dict] = []
    for n, raw in enumerate(str(rendered_text or "").splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        if _RENDERED_CHAIN_RE.search(line):
            findings.append({"severity": "critical", "issue": "The final PDF shows a flattened arrow diagram.",
                             "line": n, "text": line[:200]})
        elif _RENDERED_TABLE_RESIDUE_RE.search(line):
            findings.append({"severity": "critical", "issue": "The final PDF shows flattened table text.",
                             "line": n, "text": line[:200]})
    return findings[:20]
