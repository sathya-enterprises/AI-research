"""Addressable elements of a report's markdown: headings, paragraphs, tables, lists, quotes, charts and images.

Report edits need to point at more than a whole section ("the third paragraph on page 12", "the chart below the
Market Trends heading"). Elements are computed on the fly from the markdown, so the stored report format does not
change:

* every non-empty line is a paragraph, except runs of table rows (one table), list items (one list) and quote lines
  (one quote); chart and image markers (``[CHART_3]``, ``[WEB_IMG_1]``) on their own line are chart/image elements;
* each element knows its character span, its section (the nearest H2 and H3 above it) and a deterministic id:
  ``h:<slug>`` for headings, ``chart:<n>`` / ``image:<n>`` for visuals (their registry index, stable across edits),
  ``p:<section-slug>:<k>`` / ``table:<section-slug>:<k>`` for the k-th paragraph / table of a section;
* ``splice`` replaces or inserts text at an element's span and leaves every other character untouched.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(\S.*?)\s*#*\s*$")
_MARKER_RE = re.compile(r"^\s*\[(CHART|WEB_IMG|FILE_IMG)_(\d+)\]\s*$", re.I)
_CITE_RE = re.compile(r"\[S\d+(?:\.\d+)?(?:\s*[,;]\s*S\d+(?:\.\d+)?)*\]")


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text or "").casefold()).strip("-")[:48] or "section"


def plain(text: str) -> str:
    """Text as it reads in the PDF: no markdown emphasis, links, citations or table pipes."""
    text = _CITE_RE.sub(" ", str(text or ""))
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[*_`#>|]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


@dataclass
class Element:
    id: str
    kind: str                 # heading | paragraph | table | list | quote | chart | image
    start: int
    end: int                  # exclusive; includes the element's trailing newline
    text: str
    level: int = 0            # heading level
    section_id: str = ""      # id of the enclosing heading (H2/H3/H4)
    section_path: list = field(default_factory=list)   # heading titles from H2 down
    index: int = 0            # chart/image registry number, or k-th paragraph/table in its section
    page: int | None = None   # PDF page (1-based) once mapped

    @property
    def title(self) -> str:
        return self.text.lstrip("#").strip() if self.kind == "heading" else plain(self.text)[:120]


def parse(report: str) -> list[Element]:
    text = str(report or "")
    lines = text.splitlines(keepends=True)
    elements: list[Element] = []
    offset = 0
    heading_stack: list[tuple[int, str, str]] = []  # (level, title, id)
    counters: dict[tuple[str, str], int] = {}
    used_ids: set[str] = set()
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        start = offset
        if not stripped:
            offset += len(line)
            i += 1
            continue
        heading = _HEADING_RE.match(stripped)
        if heading:
            level, title = len(heading.group(1)), heading.group(2).strip()
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            base = "h:" + slug(title)
            hid, n = base, 2
            while hid in used_ids:
                hid, n = f"{base}-{n}", n + 1
            used_ids.add(hid)
            parent = heading_stack[-1][2] if heading_stack else ""
            elements.append(Element(hid, "heading", start, start + len(line), line.rstrip("\n"), level=level,
                                    section_id=parent, section_path=[t for _, t, _ in heading_stack] + [title]))
            heading_stack.append((level, title, hid))
            offset += len(line)
            i += 1
            continue
        section_id = heading_stack[-1][2] if heading_stack else ""
        path = [t for _, t, _ in heading_stack]
        marker = _MARKER_RE.match(stripped)
        if marker:
            kind = "chart" if marker.group(1).upper() == "CHART" else "image"
            number = int(marker.group(2))
            elements.append(Element(f"{kind}:{number}", kind, start, start + len(line), stripped,
                                    section_id=section_id, section_path=path, index=number))
            offset += len(line)
            i += 1
            continue
        # runs: table rows, list items, quote lines
        run_kind = ("table" if stripped.startswith("|") else
                    "list" if re.match(r"^([-*+]|\d+[.)])\s+", stripped) else
                    "quote" if stripped.startswith(">") else "paragraph")
        j = i + 1
        if run_kind != "paragraph":
            while j < len(lines):
                nxt = lines[j].strip()
                same = (nxt.startswith("|") if run_kind == "table" else
                        bool(re.match(r"^([-*+]|\d+[.)])\s+", nxt)) or (lines[j].startswith(("  ", "\t")) and nxt)
                        if run_kind == "list" else nxt.startswith(">"))
                if not nxt or not same:
                    break
                j += 1
        block = "".join(lines[i:j])
        key = (section_id, run_kind)
        counters[key] = counters.get(key, 0) + 1
        prefix = {"paragraph": "p", "table": "table", "list": "list", "quote": "quote"}[run_kind]
        sec_slug = section_id[2:] if section_id.startswith("h:") else "top"
        elements.append(Element(f"{prefix}:{sec_slug}:{counters[key]}", run_kind, start, start + len(block),
                                block.rstrip("\n"), section_id=section_id, section_path=path, index=counters[key]))
        offset += len(block)
        i = j
    return elements


def by_id(elements: list[Element], element_id: str) -> Element | None:
    return next((e for e in elements if e.id == element_id), None)


def section_elements(elements: list[Element], heading_id: str) -> list[Element]:
    """The heading and everything until the next heading of the same or a higher level."""
    head = by_id(elements, heading_id)
    if head is None or head.kind != "heading":
        return []
    out, inside = [], False
    for e in elements:
        if e is head:
            inside = True
            out.append(e)
            continue
        if inside:
            if e.kind == "heading" and e.level <= head.level:
                break
            out.append(e)
    return out


def splice(report: str, element: Element, new_text: str, *, mode: str = "after") -> str:
    """Insert ``new_text`` after/before ``element`` or replace it; every other character stays as it was."""
    text = str(report or "")
    block = str(new_text or "").strip("\n")
    if mode == "replace":
        tail = "\n" if text[element.start:element.end].endswith("\n") else ""
        return text[:element.start] + block + tail + text[element.end:]
    if mode == "before":
        return text[:element.start] + block + "\n\n" + text[element.start:]
    if mode == "after":
        sep = "" if text[:element.end].endswith("\n") else "\n"
        return text[:element.end] + sep + "\n" + block + "\n" + text[element.end:]
    raise ValueError(f"unknown splice mode: {mode}")


def figure_numbers(elements: list[Element], charts: list) -> dict[int, int]:
    """Chart registry number -> figure number as printed in the PDF ("Figure k: ..."), read from the chart title
    when it carries one, otherwise the order of appearance."""
    out: dict[int, int] = {}
    order = 0
    for e in elements:
        if e.kind != "chart":
            continue
        order += 1
        spec = charts[e.index - 1] if 0 < e.index <= len(charts or []) and isinstance(charts[e.index - 1], dict) else {}
        match = re.match(r"\s*Figure\s+(\d+)\b", str(spec.get("title") or ""), re.I)
        out[e.index] = int(match.group(1)) if match else order
    return out
