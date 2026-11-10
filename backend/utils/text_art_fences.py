"""Stage L: code-fenced text art from the section writer, resolved before line-level clean-up.

The writer sometimes puts an ASCII bar chart, a box-drawn table, a tree diagram or a "key: value"
panel inside a ``` fence. Converted line by line, those became junk bullets in the PDF
("Consumer Services [$$$$$$] $1,063 million", "Metric FY16 FY26", "$0 $400M $800M $1,200M").
Now each fenced block is handled as a whole:

  * a table (box-drawn with │, or whitespace-aligned columns under a dashed rule) becomes a real
    Markdown table with a caption and a Source line (citations from the block or the paragraph
    before it); without any citation it is dropped (an unsourced table may not be published);
  * anything else (bars, trees, panels) is dropped when the surrounding text already gives its
    numbers (the usual case - the writer restates them), otherwise its labelled lines are kept as
    plain bullets under a bold lead-in;
  * a block that looks like real code is left alone.

Also repaired here: a heading broken over two lines, a table row broken over two lines, and an
italic "*Data Source: [S1]*" line (becomes the standard "Source: [S1]").
"""
from __future__ import annotations

import re

_FENCE_RE = re.compile(r"^\s*```")
_CITE_RE = re.compile(r"\[\s*S\d+(?:\.\d+)?(?:\s*[,;]\s*S\d+(?:\.\d+)?)*\s*\]")
_NUM_RE = re.compile(r"(?<![\w.])[-+−]?\d[\d,]*(?:\.\d+)?")
_BOX_ROW_RE = re.compile(r"[│┃|]")
_BORDER_RE = re.compile(r"^[\s┌┐└┘├┤┬┴┼─━═╔╗╚╝╠╣╦╩╬+|:-]+$")
_CODE_HINT_RE = re.compile(r"(?m)^\s*(?:def |class |import |from \w+ import|function\b|const |let |var |SELECT |#include)"
                           r"|[;{}]\s*$")
_BAR_HINT_RE = re.compile(r"\[[$#*=|█▇■\- ]{3,}\]|[█▇■]{3,}|#{6,}")


def _cites(text: str) -> list[str]:
    ids: list[str] = []
    for group in _CITE_RE.findall(text):
        for sid in re.split(r"\s*[,;]\s*", group.strip("[] ")):
            if sid and sid not in ids:
                ids.append(sid)
    return ids


def _numbers(text: str) -> set[str]:
    out = set()
    for raw in _NUM_RE.findall(_CITE_RE.sub(" ", text)):
        val = raw.replace(",", "").replace("−", "-").lstrip("+")
        try:
            num = float(val)
        except ValueError:
            continue
        if abs(num) in {0.0} or (1900 <= abs(num) <= 2100 and float(num).is_integer()):
            continue  # zero ticks and years are not data
        out.add(f"{abs(num):g}")
    return out


def _box_table(body: list[str]) -> tuple[str, list[list[str]]] | None:
    """(title, rows) for a box-drawn or pipe table; None otherwise."""
    rows: list[list[str]] = []
    title = ""
    for line in body:
        s = line.strip()
        if not s:
            continue
        if _BORDER_RE.match(s):
            continue
        if _BOX_ROW_RE.search(s):
            cells = [c.strip() for c in re.split(r"[│┃|]", s)]
            if cells and not cells[0]:
                cells = cells[1:]
            if cells and not cells[-1]:
                cells = cells[:-1]
            if len(cells) >= 2:
                rows.append(cells)
        elif not rows and not title:
            title = s
    if len(rows) >= 3 and len({len(r) for r in rows}) == 1:
        return title, rows
    return None


def _aligned_table(body: list[str]) -> tuple[str, list[list[str]]] | None:
    """(title, rows) for whitespace-aligned columns with a dashed rule under the header."""
    lines = [l for l in body if l.strip()]
    rule = next((i for i, l in enumerate(lines) if re.fullmatch(r"\s*-{6,}\s*", l)), None)
    if rule is None or rule == 0:
        return None
    header = re.split(r"\s{2,}", lines[rule - 1].strip())
    title = lines[rule - 2].strip() if rule >= 2 else ""
    rows = [header]
    for l in lines[rule + 1:]:
        cells = re.split(r"\s{2,}", l.strip())
        if len(cells) != len(header):
            return None
        rows.append(cells)
    if len(header) >= 2 and len(rows) >= 2:
        return title, rows
    return None


_MD_SEP_RE = re.compile(r"^\s*\|(?:\s*:?-{3,}:?\s*\|)+\s*$")


def _markdown_table_in(body: list[str]) -> list[str]:
    """A real Markdown table (header row + '| --- |' rule + rows) found inside a fence, verbatim."""
    for k in range(1, len(body)):
        if _MD_SEP_RE.match(body[k]) and body[k - 1].strip().startswith("|"):
            end = k + 1
            while end < len(body) and body[end].strip().startswith("|") and not _BORDER_RE.match(body[end].strip()):
                end += 1
            return [body[k - 1].strip()] + [l.strip() for l in body[k:end]]
    return []


def _as_markdown_table(title: str, rows: list[list[str]], cites: list[str]) -> list[str]:
    header, data = rows[0], rows[1:]
    clean_title = re.sub(r"\s+", " ", title).strip(" :")
    if clean_title.isupper():
        clean_title = clean_title.capitalize()
    out = []
    if clean_title:
        out += [f"**Table: {clean_title}**", ""]
    out.append("| " + " | ".join(header) + " |")
    out.append("| " + " | ".join("---" for _ in header) + " |")
    for r in data:
        out.append("| " + " | ".join(_CITE_RE.sub("", c).strip() for c in r) + " |")
    if cites:
        out.append(f"Source: [{', '.join(cites)}]")
    return out


def _context(lines: list[str], start: int, end: int, span: int = 12) -> str:
    before = [l for l in lines[max(0, start - span):start] if not _FENCE_RE.match(l)]
    after = [l for l in lines[end + 1:end + 1 + span] if not _FENCE_RE.match(l)]
    return "\n".join(before + after)


_AXIS_RE = re.compile(r"(?:[$₹]?\s?[-+]?\d[\d,.]*\s?[%A-Za-z]{0,2}\s*){2,}")


def _is_axis(line: str) -> bool:
    """An axis line: only tick values ("$0  $400M  $800M", "0%  10%  20%") and rule characters."""
    s = re.sub(r"[+\-─━|┼┬┴]+", " ", line).strip()
    s = re.sub(r"\bfor\s+\w+\s*", "", s)
    return bool(s) and bool(_AXIS_RE.fullmatch(s))


def _panel_lines(body: list[str]) -> list[str]:
    """A bars / tree / key-value panel kept as plain bullets (labelled lines with a number only)."""
    out: list[str] = []
    lead = ""
    for line in body:
        s = re.sub(r"[├└┌┐┘┤┬┴┼│─━┃]+", " ", line).strip()
        s = re.sub(r"\[[$#*=|█▇■\- ]{2,}\]|[█▇■#]{3,}", " ", s)
        s = re.sub(r"\s{2,}", " ", s).strip(" -+|")
        if not s or _BORDER_RE.match(s):
            continue
        if not _NUM_RE.search(_CITE_RE.sub(" ", s)):
            if not out and not lead:
                lead = s.strip("[]: ")
            continue
        if re.fullmatch(r"(?:[$₹]?\s?\d[\d,.]*\s?[%A-Za-z]{0,2}\s*){2,}", s):
            continue  # an axis: "$0 $400M $800M $1,200M", "0% 10% 20%"
        out.append(f"- {s}")
    if not out:
        return []
    return ([f"**{lead}**", ""] if lead else []) + out


def resolve_fences(text: str) -> tuple[str, list[str]]:
    lines = str(text or "").split("\n")
    out: list[str] = []
    actions: list[str] = []
    i = 0
    while i < len(lines):
        if not _FENCE_RE.match(lines[i]):
            out.append(lines[i])
            i += 1
            continue
        j = i + 1
        while j < len(lines) and not _FENCE_RE.match(lines[j]):
            j += 1
        if j >= len(lines):  # unclosed fence: leave the rest to the line-level clean-up
            out.extend(lines[i:])
            break
        body = lines[i + 1:j]
        block = "\n".join(body)
        if _CODE_HINT_RE.search(block) and not _BAR_HINT_RE.search(block):
            out.extend(lines[i:j + 1])
            i = j + 1
            continue
        md_table = _markdown_table_in(body)
        if md_table:
            out += [""] + md_table + [""]
            actions.append("kept a Markdown table that sat inside a code fence")
            i = j + 1
            continue
        table = _box_table(body) or _aligned_table(body)
        if table:
            prev_para = next((l for l in reversed(out) if l.strip()), "")
            cites = _cites(block) or _cites(prev_para)
            # without a citation the table is still a table; the table gate decides whether it may publish
            out += [""] + _as_markdown_table(table[0], table[1], cites) + [""]
            actions.append("converted a text-art table into a Markdown table")
            i = j + 1
            continue
        data_lines = [l for l in body if not _is_axis(l)]
        nums = _numbers("\n".join(data_lines))
        around = _numbers(_context(lines, i, j))
        if not nums:  # a label-only diagram: the line rules turn it into a list
            out.extend(lines[i:j + 1])
            i = j + 1
            continue
        if len(nums & around) >= 0.6 * len(nums):
            actions.append("dropped text art whose numbers the text already gives")
        else:
            out += [""] + _panel_lines(body) + [""]
            actions.append("kept a text-art panel as plain bullets")
        i = j + 1
    return "\n".join(out), actions


_DATA_SOURCE_RE = re.compile(r"^\s*[*_]*\s*(?:Data\s+)?Sources?\s*:\s*(\[[^\]]+\])\s*[*_]*\s*$", re.I)


def repair_broken_lines(text: str) -> tuple[str, list[str]]:
    lines = str(text or "").split("\n")
    out: list[str] = []
    actions: list[str] = []
    for line in lines:
        prev = out[-1] if out else ""
        s = line.strip()
        # a heading broken over two lines: "#### FII Positioning and Under" + "weight Allocations"
        if (prev.lstrip().startswith("#") and s and s[0].islower() and len(s) <= 60
                and not re.search(r"[.!?:;]\s*$", s) and not s.startswith(("-", "*", "|"))):
            out[-1] = prev.rstrip() + s
            actions.append("joined a heading broken over two lines")
            continue
        # a table row broken over two lines: "| **" + "Indian Pension Funds** | Restricted | ... |"
        ps = prev.strip()
        if ps.startswith("|") and not ps.endswith("|") and s.endswith("|") and not s.startswith("|"):
            out[-1] = prev.rstrip() + s
            actions.append("joined a table row broken over two lines")
            continue
        m = _DATA_SOURCE_RE.match(line)
        if m:
            last = next((x for x in reversed(out) if x.strip()), "")
            if last.strip().startswith("Source:") and not line.strip().startswith("Source:"):
                actions.append("dropped a 'Data Source' line repeating the table's Source line")
                continue
            out.append(f"Source: {m.group(1)}")
            if not line.strip().startswith("Source:"):
                actions.append("normalised a 'Data Source' line")
            continue
        out.append(line)
    return "\n".join(out), actions
