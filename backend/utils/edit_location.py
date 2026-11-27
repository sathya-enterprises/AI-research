"""Where in the report an edit should happen: a page, an element kind and ordinal, a heading, a figure number, and
a relation (after / before / replace / into).

    "add a graph on page 30"                       -> page 30 (the section it falls in; insert after its last element)
    "replace the second chart on page 20"          -> chart elements on page 20, the 2nd, replace
    "add a paragraph below the third paragraph on page 12" -> 3rd paragraph on page 12, after
    "explain the chart below the Market Trends heading on page 15" -> first chart under that heading, after
    "insert a table before the conclusion"         -> the Conclusion heading, before
    "explain Figure 7"                             -> chart with figure number 7, after

Nothing is guessed: if several elements fit and no ordinal picks one, or nothing fits, the result carries a
question for the user instead of a location.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from utils.report_elements import Element, figure_numbers, section_elements

_ORDINALS = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4,
             "fifth": 5, "5th": 5, "sixth": 6, "6th": 6, "last": -1, "final": -1}
_KIND_WORDS = {
    "chart": r"charts?|graphs?|figures?|visuali[sz]ations?|plots?|visuals?",
    "table": r"tables?",
    "paragraph": r"paragraphs?|para",
    "list": r"lists?|bullets?|bullet\s+points?",
    "image": r"images?|pictures?|photos?",
    "heading": r"headings?|sections?|subsections?|chapters?",
}
_AFTER = r"below|under|underneath|beneath|after|following|immediately\s+after|right\s+after|next\s+to"
_BEFORE = r"above|before|preceding|ahead\s+of"
_REPLACE = r"replace|swap|substitute|instead\s+of"


@dataclass
class Location:
    element: Element | None = None
    relation: str = "after"            # after | before | replace | into
    page: int | None = None
    reason: str = ""
    question: str = ""                  # set when the target cannot be resolved with confidence
    candidates: list = field(default_factory=list)

    @property
    def resolved(self) -> bool:
        return self.element is not None and not self.question


def _words(text: str) -> set[str]:
    stop = {"the", "a", "an", "of", "and", "or", "in", "on", "to", "for", "section", "subsection", "heading",
            "chapter", "part"}
    return {w for w in re.findall(r"[a-z0-9]+", str(text or "").casefold()) if w not in stop}


def _named_heading(instruction: str, elements: list[Element]) -> Element | None:
    """A heading the instruction names ("the Market Trends heading", "before the conclusion", a quoted title)."""
    quoted = re.findall(r"[\"“”']([^\"“”']{3,120})[\"“”']", instruction)
    headings = [e for e in elements if e.kind == "heading"]
    best, best_score = None, 0.0
    for h in headings:
        title_words = _words(h.title)
        if not title_words:
            continue
        for q in quoted:
            if _words(q) and _words(q) <= title_words | _words(q) and len(_words(q) & title_words) / len(title_words) >= 0.8:
                return h
        hit = len(title_words & _words(instruction)) / len(title_words)
        if hit > best_score:
            best, best_score = h, hit
    return best if best_score >= 0.75 else None


def describe(element: Element, charts: list | None = None) -> str:
    if element.kind == "chart":
        spec = (charts or [])[element.index - 1] if 0 < element.index <= len(charts or []) else {}
        return str((spec or {}).get("title") or f"chart {element.index}")[:100]
    return f"{element.kind} \"{element.title[:70]}\""


def resolve(instruction: str, elements: list[Element], charts: list | None = None) -> Location | None:
    """None when the instruction names no position at all (the caller keeps its section-level targeting)."""
    text = str(instruction or "")
    lower = text.casefold()
    page_match = re.search(r"\bpage\s*(?:no\.?|number)?\s*(\d{1,4})\b", lower)
    page = int(page_match.group(1)) if page_match else None
    figure_match = re.search(r"\bfig(?:ure)?\.?\s*(\d{1,3})\b", lower)
    kinds_alt = "|".join(_KIND_WORDS.values())
    relation = ""
    for name, pattern in (("replace", _REPLACE), ("before", _BEFORE), ("after", _AFTER)):
        for m in re.finditer(rf"\b(?:{pattern})\b", lower):
            # a relation word is a position only when it points at an element or a heading within a few words
            # ("below the chart", "before the conclusion") - not in phrases like "instead of failing"
            tail = lower[m.end():m.end() + 70]
            if re.match(rf"\s+(?:(?:the|this|that|existing|\w+)\s+){{0,4}}(?:{kinds_alt})\b", tail) \
                    or _named_heading(tail, elements) is not None:
                relation = name
                break
        if relation:
            break
    # the element kind the position refers to: the noun right after an ordinal, or after below/above/the
    kind, ordinal = None, None
    ref = re.search(rf"\b({'|'.join(_ORDINALS)})\s+({'|'.join(_KIND_WORDS.values())})\b", lower)
    if ref:
        ordinal = _ORDINALS[ref.group(1)]
        kind = next(k for k, pat in _KIND_WORDS.items() if re.fullmatch(pat, ref.group(2)))
    else:
        rel_ref = re.search(rf"\b(?:{_AFTER}|{_BEFORE}|{_REPLACE}|explain|describe|interpret)\s+(?:the|this|that|existing)?\s*"
                            rf"(?:existing\s+)?({'|'.join(_KIND_WORDS.values())})\b", lower)
        if rel_ref:
            kind = next(k for k, pat in _KIND_WORDS.items() if re.fullmatch(pat, rel_ref.group(1)))
    headings_named = [h for h in elements if h.kind == "heading" and _words(h.title)
                      and len(_words(h.title) & _words(text)) / len(_words(h.title)) >= 0.75]
    if len(headings_named) >= 2 and page is None and figure_match is None:
        return None  # several sections: the section editor orchestrates multi-section edits
    if page is None and figure_match is None and not relation and kind is None:
        # A named heading alone is not a position: the section editor already resolves headings (including
        # several at once), so section-level instructions keep that path.
        return None
    heading = _named_heading(text, elements)

    # Figure k: unambiguous
    if figure_match:
        figures = figure_numbers(elements, charts or [])
        number = int(figure_match.group(1))
        chart = next((e for e in elements if e.kind == "chart" and figures.get(e.index) == number), None)
        if chart is None:
            return Location(question=f"There is no Figure {number} in this report. Which chart do you mean?")
        return Location(chart, relation or "after", chart.page, reason=f"Figure {number}")

    pool = elements
    if heading is not None and kind not in (None, "heading"):
        pool = section_elements(elements, heading.id)[1:]
    if page is not None:
        if not any(e.page for e in elements):
            return Location(page=page, question="The page layout is not available. Which heading or nearby text "
                                                "should the change go to?")
        pool = [e for e in pool if e.page == page]
        if not pool:
            return Location(page=page, question=f"Page {page} has no report content I can place the change on. "
                                                "Which heading or nearby text do you mean?")
    if kind == "heading" or (kind is None and heading is not None and page is None):
        if heading is None:
            heads = [e for e in pool if e.kind == "heading"]
            heading = heads[(ordinal or 1) - 1 if ordinal and ordinal > 0 else -1] if heads and (ordinal or len(heads) == 1) else None
            if heading is None:
                return Location(page=page, candidates=heads, question="Which heading do you mean? " + "; ".join(
                    describe(h) for h in heads[:6]))
        return Location(heading, relation or "into", heading.page, reason=f"heading {heading.title!r}")
    if kind is not None:
        matches = [e for e in pool if e.kind == kind]
        if not matches:
            where = f" on page {page}" if page else (f" under {heading.title!r}" if heading else "")
            return Location(page=page, question=f"I could not find a {kind}{where}. Which one do you mean?")
        if ordinal:
            if abs(ordinal) > len(matches):
                return Location(page=page, candidates=matches, question=f"There are only {len(matches)} "
                                f"{kind}s there. Which one do you mean? " + "; ".join(describe(m, charts) for m in matches))
            chosen = matches[ordinal - 1 if ordinal > 0 else -1]
        elif len(matches) == 1 or (heading is not None and page is None):
            chosen = matches[0]   # "the chart below the X heading": the first one under it
        else:
            return Location(page=page, candidates=matches, question=f"There are {len(matches)} {kind}s"
                            + (f" on page {page}" if page else "") + ". Which one do you mean? "
                            + "; ".join(f"({i + 1}) {describe(m, charts)}" for i, m in enumerate(matches[:6])))
        return Location(chosen, relation or "after", chosen.page, reason=f"{kind} {chosen.id}")
    # a page (and maybe a relation) but no element kind: the last element on that page, inside its section
    if page is not None:
        body = [e for e in pool if e.kind != "heading"] or pool
        return Location(body[-1], relation or "after", page, reason=f"end of page {page}")
    if heading is not None:
        return Location(heading, relation or "into", heading.page, reason=f"heading {heading.title!r}")
    return None
