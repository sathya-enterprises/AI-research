"""In-body layout blocks for report Markdown (Stage D).

The renderer already has structured blocks (metric strips, callouts, timelines,
tables) but they could only be attached to a whole section, after its prose.
These one-line Markdown forms place an existing block exactly where it belongs
in the text, and degrade to an ordinary blockquote anywhere else:

    > [!STATS] Q3 2026 return: -4.2% [S2] | FPI net flow, Oct 2026: -883 crore [S14]
    > [!TAKEAWAYS] Two to four sentences with citations.
    > [!WATCH] What could change the view, with citations.
    > [!TIMELINE] Sep 2026 — RBI holds rates [S7] | Oct 2026 — FPI outflows peak [S14]
    > [!NOTE:How to read this report] Plain explanatory text.

They render through the existing block functions in routes/html_report.py
(_render_metrics, _render_evidence_block, _render_timeline_block): no new block
types, classes, fonts, colours or spacing.
"""
from __future__ import annotations

import re

ADMONITION_RE = re.compile(r"^>\s*\[!(STATS|TAKEAWAYS|WATCH|TIMELINE|NOTE)(?::([^\]]{1,80}))?\]\s*(.*)$")
CALLOUT_HEADINGS = {"TAKEAWAYS": ("Key takeaways", "info"), "WATCH": ("What to watch", "warning")}


def _split_items(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"\s+\|\s+", text or "") if part.strip()]


def render_admonition(line: str, source_registry: list[dict] | None = None) -> str | None:
    """HTML for an admonition line, or None when the line is an ordinary blockquote."""
    match = ADMONITION_RE.match(str(line or "").strip())
    if not match:
        return None
    from routes import html_report as H

    kind, label, body = match.group(1), (match.group(2) or "").strip(), match.group(3).strip()
    if not body:
        return ""
    if kind == "STATS":
        items = []
        for part in _split_items(body):
            name, sep, value = part.rpartition(": ")
            if not sep:
                continue
            # The citation travels with the label so each number shows its source.
            cites = " ".join(re.findall(r"\[S\d+(?:\s*,\s*S\d+)*\]", value))
            value = re.sub(r"\s*\[S\d+(?:\s*,\s*S\d+)*\]", "", value).strip()
            items.append({"label": f"{name.strip()} {cites}".strip(), "value": value})
        return H._render_metrics(items, source_registry, limit=4) if items else ""
    if kind == "TIMELINE":
        events = []
        for part in _split_items(body):
            date, sep, description = part.partition(" — ")
            if sep:
                events.append({"date_label": date.strip(), "title": "", "description": description.strip()})
        return H._render_timeline_block({"events": events}, source_registry) if events else ""
    heading, tone = CALLOUT_HEADINGS.get(kind, (label or "Note", "info"))
    return H._render_evidence_block({"heading": heading, "body": body, "tone": tone}, source_registry)


_HEADING_START = ('<h3 class="gg-reveal gg-section-h3"', '<h4 class="gg-reveal gg-subsection-h4"')
_KEEPABLE_START = ("<p", "<aside", '<div class="gg-block gg-metrics', '<div class="gg-table-wrap', '<div class="gg-block gg-timeline')


def keep_headings_with_next(out: list[str]) -> list[str]:
    """Bind each H3/H4 to the block after it so no heading is stranded at a page bottom.

    Uses the existing ``gg-keep-with-next`` wrapper (break-inside: avoid). Lists and
    multi-part elements are left to the heading's break-after rule.
    """
    merged: list[str] = []
    i = 0
    while i < len(out):
        item = out[i]
        nxt = out[i + 1] if i + 1 < len(out) else ""
        if item.lstrip().startswith(_HEADING_START) and nxt.lstrip().startswith(_KEEPABLE_START):
            merged.append(f'<div class="gg-keep-with-next">{item}{nxt}</div>')
            i += 2
            continue
        merged.append(item)
        i += 1
    return merged
