"""Reader-facing citations (Stage E).

Inside the pipeline every claim keeps its passage citation ([S12.3] / [S12]):
the writer, the number checks, citation repair, the published/draft decision, the
capture and the audit log all see them. Only the FINAL render step removes the
inline markers from the reader-facing report, after every check has run:

  * body text, table cells, callouts, takeaway boxes, figure captions and the
    executive summary lose their [S#] / [S#.#] markers (runs like [S5][S37] and
    comma lists included); nothing else in brackets is touched, and the
    leftovers of stripping (double spaces, " .", empty parentheses, a sentence
    that was only a citation) are cleaned up;
  * each section ends with one "Sources:" line naming its publishers (deduped,
    at most 6), each chart/table keeps its one-line "Source:" caption, and the
    single Data Sources section lists every cited source with its [S#] id.

REPORT_INLINE_CITATIONS=1 keeps the inline markers (an audit copy).
"""
from __future__ import annotations

import os
import re

# Any horizontal space before a marker goes with it (writers also use no-break / narrow no-break spaces).
MARKER_RE = re.compile(r"[^\S\n]*\[\s*S\d+(?:\.\d+)?(?:\s*[,;]\s*S\d+(?:\.\d+)?)*\s*\]")
_EMPTY_PARENS_RE = re.compile(r"\s*\(\s*(?:(?:see|source|sources|cf\.?|via|per)\s*)?[,;]?\s*\)")


def inline_citations_enabled() -> bool:
    return os.getenv("REPORT_INLINE_CITATIONS", "0").strip().lower() in {"1", "true", "yes", "on"}


def strip_citation_markers(text: str) -> str:
    """Remove [S#] markers and tidy what is left (only citation brackets are removed)."""
    if not text or "[" not in text:
        return text
    out = MARKER_RE.sub("", text)
    out = _EMPTY_PARENS_RE.sub("", out)
    out = re.sub(r"[^\S\n]+([.,;:!?])", r"\1", out)        # "growth [S3] ." -> "growth."
    # "[S1], [S2]." and "[S1] and [S2]." leave a comma or "and" before the full stop.
    out = re.sub(r"(?:\s*[,;]|\s+(?:and|&))+(?=\s*[.!?:)](?:\s|$))", "", out)
    out = re.sub(r"([,;:])\1+", r"\1", out)                # ",," left by "[S1], [S2]," style runs
    out = re.sub(r"(?<=[.!?])[,;]", "", out)               # ".," after a removed trailing citation
    out = re.sub(r"[ \t]{2,}", " ", out)
    return out.strip() if text.strip() == text else out


def only_citations(text: str) -> bool:
    """True when a paragraph / cell / callout held nothing but citations and punctuation."""
    return bool(text.strip()) and not re.sub(r"[\s.,;:·|()\-–—]+", "", strip_citation_markers(text))


def section_source_names(source_ids, registry: dict[str, dict], max_names: int = 6) -> list[str]:
    """Deduplicated publisher names for a section's cited sources, in citation order."""
    from utils.source_list import display_publisher

    names: list[str] = []
    for sid in source_ids:
        source = registry.get(str(sid).upper())
        if not source:
            continue
        name = display_publisher(source) or str(source.get("title") or "").strip()
        if name and name.casefold() not in {n.casefold() for n in names}:
            names.append(name)
        if len(names) >= max_names:
            break
    return names
