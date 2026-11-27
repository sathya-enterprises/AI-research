"""Which PDF page each report element is on.

A PDF page is not a markdown section: a section can span pages and an edit can move everything after it. The map is
therefore always built from a PDF rendered from the CURRENT report (callers render it again after an edit) and is
never cached across report versions.

Matching works on text as it reads in the PDF: each element's opening words (headings, paragraphs, lists, quotes),
its first table cells, or "Figure k" plus its title for charts, compared with letters and digits only so line
breaks, hyphenation and spacing do not matter. Elements are located in reading order (a match must not be earlier
than the previous element's page); the table of contents is skipped for headings, since it lists them all. An
element whose text cannot be found inherits the previous element's page and is marked approximate.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from utils.report_elements import Element, figure_numbers, plain


def _key(text: str, chars: int = 44) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text or "").casefold())[:chars]


def page_texts(pdf_bytes: bytes) -> list[str]:
    import fitz
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        return [page.get_text() for page in doc]


@dataclass
class PageMap:
    pages: int
    page_of: dict          # element id -> 1-based page
    approximate: set       # element ids located by inheritance, not by their own text
    contents_pages: set

    def elements_on(self, elements: list[Element], page: int) -> list[Element]:
        return [e for e in elements if self.page_of.get(e.id) == page]

    def span(self, elements: list[Element], heading_id: str) -> tuple[int, int] | None:
        """First and last page of a section (heading through its last element)."""
        from utils.report_elements import section_elements
        pages = [self.page_of[e.id] for e in section_elements(elements, heading_id) if e.id in self.page_of]
        return (min(pages), max(pages)) if pages else None


def _element_key(element: Element, figures: dict[int, int], charts: list) -> str:
    if element.kind == "chart":
        spec = charts[element.index - 1] if 0 < element.index <= len(charts or []) else {}
        title = re.sub(r"^\s*Figure\s+\d+\s*[:.\-–]\s*", "", str((spec or {}).get("title") or ""), flags=re.I)
        return _key(f"Figure {figures.get(element.index, element.index)} {title}", 30)
    if element.kind == "table":
        cells = [c.strip() for c in element.text.splitlines()[0].strip().strip("|").split("|") if c.strip()]
        return _key(" ".join(cells[:3]), 30)
    if element.kind == "heading":
        return _key(element.title, 40)
    return _key(plain(element.text), 44)


def build(pdf_bytes: bytes | None, elements: list[Element], charts: list | None = None,
          texts: list[str] | None = None) -> PageMap:
    texts = texts if texts is not None else page_texts(pdf_bytes or b"")
    keys = [_key(t, 10**9) for t in texts]
    headings = [e for e in elements if e.kind == "heading"]
    contents = set()
    for n, t in enumerate(keys):
        if "contents" in t[:4000] and sum(1 for h in headings if _key(h.title, 30) and _key(h.title, 30) in t) >= 3:
            contents.add(n)
    figures = figure_numbers(elements, charts or [])
    page_of: dict[str, int] = {}
    approximate: set[str] = set()
    cursor = 0
    for element in elements:
        key = _element_key(element, figures, charts or [])
        found = None
        if len(key) >= 8:
            for n in range(cursor, len(keys)):
                if element.kind == "heading" and n in contents:
                    continue
                if key in keys[n]:
                    found = n
                    break
        if found is None:
            approximate.add(element.id)
            page_of[element.id] = cursor + 1
            continue
        page_of[element.id] = found + 1
        cursor = found
    for element in elements:
        element.page = page_of.get(element.id)
    return PageMap(pages=len(texts), page_of=page_of, approximate=approximate, contents_pages={n + 1 for n in contents})
