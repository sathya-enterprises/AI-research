"""PDF layout metrics from geometry (PyMuPDF): font sizes and positions, not only text (Stage D eval)."""
from __future__ import annotations

import re
from collections import Counter

SOURCE_LIST_RE = re.compile(
    r"^(?:appendix(?:\s+[a-z])?\s*[—–-]\s*)?(?:data sources|source reliability|sources|references|bibliography|"
    r"source list|complete data sources)$", re.I)
KICKER_RE = re.compile(r"^(?:section\s+\d+|\d{1,2}|[↗•·])$", re.I)
BLOCK_TITLE_RE = re.compile(r"(?i)^(?:key takeaways|what to watch|how to read this report|note|table:.*|figure \d+:.*)$")
PROMPT_LEAK_RE = re.compile(r"(?i)\bgoal:|\(~?\d+\s+words\)|<<<SUMMARY>>>|\[\[FIGURE|\[CHART_\d+\]")


def _lines(page) -> list[dict]:
    out = []
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            spans = [s for s in line.get("spans", []) if s.get("text", "").strip()]
            if not spans:
                continue
            text = " ".join(s["text"].strip() for s in spans).strip()
            size = max(s.get("size", 0) for s in spans)
            bold = any("bold" in str(s.get("font", "")).casefold() or (s.get("flags", 0) & 16) for s in spans)
            out.append({"text": text, "size": round(size, 1), "bold": bold, "bbox": line["bbox"]})
    out.sort(key=lambda ln: (ln["bbox"][1], ln["bbox"][0]))
    # A heading that wraps onto a second line is one heading: merge adjacent lines of the
    # same large size (body text is ~10pt, so only display sizes are merged).
    merged: list[dict] = []
    for ln in out:
        prev = merged[-1] if merged else None
        if (prev is not None and ln["size"] >= 11.5 and abs(ln["size"] - prev["size"]) < 0.3
                and ln["bold"] == prev["bold"] and -ln["size"] * 0.5 <= ln["bbox"][1] - prev["bbox"][3] < ln["size"] * 0.7
                and not prev["text"].endswith((".", ";"))):
            prev["text"] = f'{prev["text"]} {ln["text"]}'
            prev["bbox"] = (min(prev["bbox"][0], ln["bbox"][0]), prev["bbox"][1],
                            max(prev["bbox"][2], ln["bbox"][2]), ln["bbox"][3])
            continue
        merged.append(dict(ln))
    return merged


def content_drawings(page, top: float, bottom: float) -> list:
    """Vector shapes of tables, charts, callouts and cards; page backdrops, header/footer rules,
    bullets and full-width section rules are furniture, not blocks."""
    width = page.rect.width
    out = []
    for d in page.get_drawings():
        r = d["rect"]
        if not (top < r.y0 < bottom):
            continue
        if r.width > width * 0.8 and r.height > 300:      # page / content backdrop
            continue
        if r.width < 8 and r.height < 8:                  # bullets and icons
            continue
        if r.width > width * 0.8 and r.height <= 2.5:     # section rule
            continue
        out.append(d)
    return out


def pdf_layout_metrics(pdf_bytes: bytes) -> dict:
    try:
        import pymupdf as fitz
    except ImportError:  # pragma: no cover
        import fitz  # type: ignore
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pages = [_lines(p) for p in doc]
    sizes = Counter(ln["size"] for lines in pages for ln in lines if len(ln["text"]) > 40)
    body_size = sizes.most_common(1)[0][0] if sizes else 10.0

    def is_heading(ln: dict) -> bool:
        text = ln["text"]
        if len(text) > 140 or KICKER_RE.match(text) or text.endswith((".", ",", ";")):
            return False
        if len(re.findall(r"[A-Za-z]", text)) < 4:
            return False          # a stat-card value ("+7.42%", "100.3"), not a heading
        if BLOCK_TITLE_RE.match(text):
            return False          # the title inside a callout / block, not a document heading
        return ln["size"] >= body_size * 1.12 or (ln["bold"] and ln["size"] >= body_size * 1.02 and len(text) < 100)

    # Appendix and back matter start at the first appendix / source-list heading.
    appendix_start = len(doc)
    source_lists = []
    for index, lines in enumerate(pages):
        for ln in lines:
            if not is_heading(ln):
                continue
            if SOURCE_LIST_RE.match(ln["text"]):
                source_lists.append((index + 1, ln["text"]))
            if re.match(r"(?i)^appendix\b", ln["text"]) and index < appendix_start:
                appendix_start = index
    # Body pages: after the cover/contents (first page with a section heading) and before the appendix.
    first_body = next((i for i, lines in enumerate(pages) if i > 0 and any(is_heading(ln) and ln["size"] >= body_size * 1.5 for ln in lines)), 1)

    stranded, heading_runs, prose_only, non_prose, near_empty, overflow, leaks, fences = [], [], [], [], [], [], [], []
    previous_heading = None
    for index, page in enumerate(doc):
        lines = pages[index]
        height, width = page.rect.height, page.rect.width
        top, bottom = height * 0.06, height * 0.92     # page furniture (running header / footer rules) excluded
        body_lines = [ln for ln in lines if top < ln["bbox"][1] < bottom]
        words = sum(len(ln["text"].split()) for ln in body_lines)
        content_bottom = max((ln["bbox"][3] for ln in body_lines), default=0)
        for ln in body_lines:
            if PROMPT_LEAK_RE.search(ln["text"]) and is_heading(ln):
                leaks.append((index + 1, ln["text"][:80]))
            if "```" in ln["text"]:
                fences.append(index + 1)
            if ln["bbox"][2] > width - 18:
                overflow.append((index + 1, ln["text"][:50]))
        # Stranded: any heading that starts in the last ~10% of the page's content area.
        for ln in body_lines:
            if is_heading(ln) and not KICKER_RE.match(ln["text"].replace(" ", ""))                     and ln["bbox"][1] > bottom - (bottom - top) * 0.10:
                stranded.append((index + 1, ln["text"][:60]))
        # Heading directly followed by another heading (reading order, across pages).
        for ln in (body_lines if index >= first_body else []):   # the cover and contents are not report body
            if KICKER_RE.match(ln["text"].replace(" ", "")) or KICKER_RE.match(ln["text"]):
                continue
            if is_heading(ln):
                if previous_heading is not None and not previous_heading.get("_text_after"):
                    heading_runs.append((index + 1, previous_heading["text"][:50], ln["text"][:50]))
                previous_heading = dict(ln, _text_after=False)
            elif previous_heading is not None and len(ln["text"]) > 2:
                previous_heading["_text_after"] = True
        if first_body <= index < appendix_start:
            has_block = bool(page.get_images()) or len(content_drawings(page, top, bottom)) >= 3
            (non_prose if has_block else prose_only).append(index + 1)
        if 0 < index < len(doc) - 1 and words < 60:
            near_empty.append(index + 1)
    # Inline citation markers left in the reader-facing PDF (the Data Sources list may show ids).
    sources_page = min((page for page, title in source_lists if "data sources" in title.casefold()), default=len(doc) + 1)
    marker_pages, malformed_pdf = [], []
    for index, lines in enumerate(pages):
        if index + 1 >= sources_page:
            break
        hits = sum(len(re.findall(r"\[\s*S\d+", ln["text"])) for ln in lines)
        if hits:
            marker_pages.append((index + 1, hits))
        for ln in lines:   # leftovers of citation stripping in the rendered text (letter-spaced captions excluded)
            if re.search(r"(?:\b\w\s){4,}", ln["text"]):
                continue
            if re.search(r"[,;]\s*\.(?:\s|$)|\(\s*\)|\s,(?=\s)", ln["text"]):
                malformed_pdf.append((index + 1, ln["text"][:90]))
    sections = sum(1 for lines in pages[:appendix_start] for ln in lines
                   if re.fullmatch(r"S\s*E\s*C\s*T\s*I\s*O\s*N\s*\d.*", ln["text"].strip(), re.I))
    sources_lines = sum(1 for lines in pages[:appendix_start] for ln in lines if ln["text"].strip().startswith("Sources:"))
    body_pages = sorted(non_prose + prose_only)
    streak = best = 0
    for page_no in body_pages:
        streak = streak + 1 if page_no in prose_only else 0
        best = max(best, streak)
    return {
        "pages": len(doc), "body_pages": len(body_pages), "body_font_size": body_size,
        "pct_body_pages_non_prose": round(100 * len(non_prose) / len(body_pages), 1) if body_pages else None,
        "prose_only_streak": best,
        "source_lists": len({t.casefold() for _, t in source_lists}), "source_list_headings": source_lists,
        "appendix_pages": len(doc) - appendix_start if appendix_start < len(doc) else 0,
        "stranded_headings": stranded, "heading_then_heading": heading_runs,
        "near_empty_pages": near_empty, "table_overflow": overflow[:10], "prompt_leak_headings": leaks, "code_fences": fences,
        "citation_markers": sum(h for _p, h in marker_pages), "citation_marker_pages": marker_pages[:10],
        "malformed_pdf_lines": malformed_pdf[:20],
        "sections": sections, "sections_with_sources_line": min(sources_lines, sections),
    }


_TOPICS = (
    ("performance", r"perform|return|momentum|relative strength|rally|leadership"),
    ("flows", r"flow|ownership|fpi|fii|dii|institutional|fund"),
    ("valuation", r"valuation|multiple|p/?e\b|re-?rating"),
    ("earnings", r"earning|margin|profit|revenue|result"),
    ("risks/outlook", r"risk|outlook|driver|watch|catalyst|scenario"),
    ("credit", r"credit|asset quality|npa|deposit|loan"),
    ("commodities", r"commodit|crude|input cost|metal price|steel"),
    ("demand", r"demand|volume|pricing|consumption|tariff"),
    ("policy", r"policy|regulat|government|budget"),
    ("orders", r"order|capex|execution"),
    ("global", r"global|export|\bus\b|dollar"),
)


def _topics(title: str) -> frozenset:
    text = title.casefold()
    found = {name for name, pattern in _TOPICS if re.search(pattern, text)}
    return frozenset(found) or frozenset({re.sub(r"[^a-z]+", " ", text).strip()})


def distinct_deep_dive_sets(report_md: str) -> tuple[int, int]:
    """(distinct subsection TOPIC sets, deep-dive sections), from 'Entity: Subsection' H3 prefixes.

    Subsections are compared by topic, not wording: "Performance and Momentum" and
    "Performance and Momentum across Quarters" are the same subsection.
    """
    sets = []
    for section in re.split(r"(?m)^##\s+", report_md or "")[1:]:
        h3 = re.findall(r"(?m)^###\s+(.+?)\s*$", section)
        prefixed = [re.match(r"^([^:]{2,40}):\s+(.+)$", h) for h in h3]
        if h3 and all(prefixed):
            # The planned subsections come first; extension calls may append more after them.
            sets.append(tuple(_topics(m.group(2)) for m in prefixed[:4]))
    return len(set(sets)), len(sets)
