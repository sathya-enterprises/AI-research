"""
POST /api/chat/report/html — Generate an animated, interactive HTML report.

Body: { report, title, question, summary, keyStats, charts, images, logoB64 }
(identical shape to /api/chat/report/pdf's body — this is a second renderer
for the SAME structured report data, not a second report-generation step.)

Why this exists as a separate route rather than an option on the PDF one:
PDF export uses the same presentation-driven HTML/CSS composition but converts the chart
specifications to static SVG before print rendering. This route keeps the full interactive
Chart.js version with hover/tooltips and scroll-aware animation. Both paths consume the
same report/title/charts/keyStats/images payload; there is no second report-generation step.

Nothing about the report-generation step (routes/report.py) changes: the
LLM still writes the same markdown with the same [CHART_n]/[WEB_IMG_n]
placeholders and the same charts/keyStats/images arrays it always has. This
file only changes what those get turned INTO — same data, different (and in
this one dimension, genuinely more capable) renderer.
"""
import html
import difflib
import json
import logging
import math
import re
import time
from difflib import SequenceMatcher
from datetime import datetime, timezone
from urllib.parse import urlparse, unquote

from fastapi import APIRouter
from fastapi.requests import Request
from fastapi.responses import JSONResponse, Response

# Re-validate theme here rather than trusting the client's echoed-back copy
# as-is: this route is the one place customCss gets concatenated into a real
# <style> tag, so re-running the same hex/font-name/CSS-escape checks used
# when the theme was first produced (routes/report.py) costs nothing and
# closes off a client that edits the JSON it sends back before re-download.
from routes.report import _sanitize_theme, _enforce_brief_section_cap
from utils.presentation_schema import ReportPresentationSpec
from utils.report_document import build_report_document, ReportSectionModel
from routes.house_styles import (
    DEFAULT_REPORT_STYLE, house_closing, house_css, house_theme, normalize_report_style, render_house_cover, running_title,
    transform_section_html,
)
from utils.chart_catalog import CHART_TYPES, MAX_DISPLAY_SLICES, fold_slice_tail_for_display, normalize_chart_type
from utils.markdown_normalizer import normalize_report_markdown, strip_excluded_report_sections
from utils.report_scanability import strip_generation_markers
from routes.source_manifest import build_source_registry, normalise_source_manifest, ensure_cited_source_records
from utils.chart_evidence_audit import audit_chart_evidence_consistency, sanitize_charts_for_publication
from routes.report_design import BODY_CLASS as _EDITORIAL_BODY_CLASS, editorial_css as _editorial_css, editorial_render_theme as _editorial_render_theme, font_face_css as _editorial_font_faces

router = APIRouter()
log = logging.getLogger("html_report")

# Same "known failure sentinel" guard as routes/pdf.py — don't build a
# polished animated report around an error message.
_KNOWN_FAILURE_MESSAGES = (
    "All LLM keys exhausted or rate-limited. Try again in a minute.",
    "Could not retrieve data for this topic. Please try again.",
    "Invalid request body.",
)

BRAND_NAVY = "#0f1a3c"
BRAND_NAVY_DEEP = "#0a1230"
BRAND_GOLD = "#d4a24c"
BRAND_GOLD_LIGHT = "#e8c27e"


# ─────────────────────────────────────────────────────────────────────────
# Markdown → HTML, resolving [CHART_n] / [WEB_IMG_n] placeholders inline
# ─────────────────────────────────────────────────────────────────────────

_CHART_RE = re.compile(r"^\[CHART_(\d+)\]$")
_WEBIMG_RE = re.compile(r"^\[WEB_IMG_(\d+)\]$")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*]+?)\*(?!\*)")
_SOURCE_CITATION_RE = re.compile(r"(?:\[(S\d+(?:\s*[,;]\s*S\d+)*)\]|\((S\d+(?:\s*[,;]\s*S\d+)*)\))", re.IGNORECASE)


def _source_registry_map(source_registry: list[dict] | None) -> dict[str, dict]:
    return {
        str(source.get("id") or f"S{index}").upper(): source
        for index, source in enumerate(source_registry or [], start=1)
        if isinstance(source, dict)
    }


def _source_ids(value: object) -> list[str]:
    structured = isinstance(value, (dict, list, tuple, set))
    if isinstance(value, dict):
        value = " ".join(str(value.get(key) or "") for key in ("id", "sourceId", "citation", "source", "sources", "url", "href"))
    elif isinstance(value, (list, tuple, set)):
        value = " ".join(str(item) for item in value)
    found: list[str] = []
    for match in _SOURCE_CITATION_RE.finditer(str(value or "")):
        citation_text = match.group(1) or match.group(2) or ""
        found.extend(re.findall(r"S\d+", citation_text, flags=re.IGNORECASE))
    # Also accept bare IDs in structured chart/source fields, not in prose.
    if structured:
        found.extend(re.findall(r"(?<![A-Za-z0-9])S\d+(?![A-Za-z0-9])", str(value or ""), flags=re.IGNORECASE))
    return list(dict.fromkeys(item.upper() for item in found))


def _citation_html(source_id: str, registry: dict[str, dict]) -> str:
    """Link an in-text citation to its canonical appendix entry.

    The appendix entry then links out to the publisher. This two-step mapping
    makes a citation resolve to the exact source record named at the bottom of
    the report, rather than skipping the bibliography and opening an ambiguous
    publisher home/page directly.
    """
    canonical_id = str(source_id or "").strip().upper()
    label = f"[{canonical_id}]"
    source = registry.get(canonical_id)
    if not source:
        return html.escape(label)
    return (
        f'<a class="gg-source-cite" data-source-id="{html.escape(canonical_id, quote=True)}" '
        f'href="#source-{html.escape(canonical_id, quote=True)}" '
        f'aria-label="Jump to source {html.escape(canonical_id, quote=True)}">'
        f'{html.escape(label)}</a>'
    )


def _link_source_citations(text: str, source_registry: list[dict] | None = None) -> str:
    registry = _source_registry_map(source_registry)

    def replace(match: re.Match) -> str:
        citation_text = match.group(1) or match.group(2) or ""
        ids = re.findall(r"S\d+", citation_text, flags=re.IGNORECASE)
        return ", ".join(_citation_html(source_id.upper(), registry) for source_id in ids)

    return _SOURCE_CITATION_RE.sub(replace, text)


def _remove_orphan_citations(text: str, source_registry: list[dict] | None = None) -> str:
    """Remove source IDs that cannot resolve in the canonical registry.

    A broken citation is worse than an omitted citation in a client-facing
    report: it creates the impression that provenance exists when the appendix
    cannot actually open it. The research pipeline should normally prevent this
    through canonical source IDs; this is the renderer's final safety net.
    """
    valid = set(_source_registry_map(source_registry))
    if not valid:
        return text

    def replace(match: re.Match) -> str:
        # _SOURCE_CITATION_RE supports both [S1] and (S1).  Only one of the
        # two capture groups is populated for any given match, so never pass a
        # possibly-None group directly to re.findall.
        citation_text = match.group(1) or match.group(2) or ""
        ids = re.findall(r"S\d+", citation_text, flags=re.IGNORECASE)
        kept = [sid.upper() for sid in ids if sid.upper() in valid]
        return ", ".join(f"[{sid}]" for sid in kept)

    return _SOURCE_CITATION_RE.sub(replace, text)


def _source_ids_from_label(label: str, source_registry: list[dict] | None) -> list[str]:
    """Best-effort match a named publisher to the canonical registry.

    This only adds a clickable ID when the source name materially matches a
    registered title, publisher, or host; it never invents a destination URL.
    """
    generic = {
        "and", "the", "of", "for", "from", "source", "web", "report", "research", "analysis",
        "renewable", "energy", "power", "india", "investment", "capital", "cost", "estimates", "data",
    }
    label_text = str(label or "").lower()
    words = set(re.findall(r"[a-z0-9]+", label_text)) - generic
    if not words:
        return []
    matches: list[tuple[int, str]] = []
    for index, source in enumerate(source_registry or [], start=1):
        if str(source.get("kind") or "").casefold() == "web image":
            continue
        source_id = str(source.get("id") or f"S{index}").upper()
        url = str(source.get("url") or "")
        if url and url.casefold() in label_text:
            matches.append((100, source_id))
            continue
        host = re.sub(r"^www\.", "", re.sub(r"^https?://", "", url, flags=re.IGNORECASE).split("/", 1)[0].lower())
        haystack = " ".join((str(source.get("title") or ""), str(source.get("publisher") or ""), host)).lower()
        source_words = set(re.findall(r"[a-z0-9]+", haystack)) - generic - {"www", "com", "org", "gov", "in", "co"}
        overlap = words & source_words
        exact_domain = any(word in host for word in words if len(word) >= 3)
        if exact_domain or len(overlap) >= (1 if len(words) <= 2 else 2):
            matches.append((len(overlap) + (3 if exact_domain else 0), source_id))
    matches.sort(reverse=True)
    return [source_id for _, source_id in matches[:4]]


def _unlinked_attribution_records(labels: list[object], source_registry: list[dict] | None) -> list[dict]:
    """Register named visual/report attributions that arrived without a URL.

    A source label must not silently disappear just because research retrieval
    did not provide its URL. Keep it in the canonical appendix as an explicitly
    unlinked attribution, while avoiding a guessed link to a vaguely similar
    publisher.
    """
    records: list[dict] = []
    seen: set[str] = set()
    for raw_value in labels:
        if isinstance(raw_value, dict):
            raw_value = " ".join(str(raw_value.get(key) or "") for key in ("source", "publisher", "title", "name", "citation"))
        elif isinstance(raw_value, (list, tuple, set)):
            raw_value = " · ".join(str(item) for item in raw_value if item is not None)
        raw = re.sub(r"^\s*(?:sources?|data source)\s*:\s*", "", str(raw_value or ""), flags=re.IGNORECASE).strip()
        raw = re.sub(r"\bmethod(?:ology)?\s*:\s*.*$", "", raw, flags=re.IGNORECASE).strip(" ·;|,")
        if not raw or _source_ids(raw) or _safe_plain_url(raw):
            continue
        # Source lines commonly list publishers with commas, semicolons, pipes,
        # or middle dots. Keep slashes intact for names such as "CEA / MNRE".
        for candidate in re.split(r"\s*(?:[,;|·])\s*", raw):
            label = re.sub(r"\s+", " ", candidate).strip(" .:–—-")
            if len(label) < 3 or _source_ids(label) or _safe_plain_url(label):
                continue
            identity = label.casefold()
            if identity in seen or _source_ids_from_label(label, source_registry):
                continue
            seen.add(identity)
            records.append({
                "title": label[:300],
                "publisher": "Source attribution supplied in report",
                "kind": "Report-attributed source (URL not supplied)",
            })
    return records


def _collect_presentation_source_attributions(value: object) -> list[object]:
    """Read source labels only from source-bearing fields in dynamic blocks."""
    source_keys = {"source", "sources", "source_label", "sourcelabel", "sourcenote", "citation", "publisher"}
    labels: list[object] = []

    def visit(node: object, depth: int = 0) -> None:
        if depth > 12:
            return
        if isinstance(node, dict):
            for key, item in node.items():
                normalized_key = re.sub(r"[^a-z]", "", str(key).lower())
                if normalized_key in source_keys:
                    if isinstance(item, dict) and _safe_plain_url(item.get("url") or item.get("href")):
                        continue
                    labels.append(item)
                elif isinstance(item, (dict, list, tuple)):
                    visit(item, depth + 1)
        elif isinstance(node, (list, tuple)):
            for item in node:
                visit(item, depth + 1)

    visit(value)
    return labels


def _render_source_note(
    value: object = None,
    source_registry: list[dict] | None = None,
    recent_ids: list[str] | None = None,
    method: object = None,
    *,
    force: bool = False,
) -> str:
    if isinstance(value, dict):
        raw = " ".join(str(value.get(key) or "") for key in ("source", "sourceNote", "citation", "publisher", "title", "id", "sourceId", "sources", "url", "href"))
    elif isinstance(value, (list, tuple, set)):
        raw = " · ".join(str(item) for item in value if item is not None)
    else:
        raw = str(value or "").strip()
    raw = re.sub(r"^\s*source\s*:\s*", "", raw, flags=re.IGNORECASE).strip()
    method_text = str(method or "").strip()
    method_match = re.search(r"\bmethod\s*:\s*(.*)$", raw, flags=re.IGNORECASE)
    if method_match:
        method_text = method_text or method_match.group(1).strip()
        raw = raw[:method_match.start()].strip(" ·;|,")

    ids = [sid for sid in _source_ids(value) if sid in _source_registry_map(source_registry)]
    if not ids and raw:
        # Renderer-generated labels such as "Open source" are not provenance.
        # If the chart/image already carries recent canonical source IDs, resolve
        # those IDs so the visible attribution names the actual publisher/title.
        if re.fullmatch(r"open\s+source(?:\s*↗)?", raw, flags=re.IGNORECASE):
            ids = [sid.upper() for sid in (recent_ids or []) if sid.upper() in _source_registry_map(source_registry)]
        else:
            ids = _source_ids_from_label(raw, source_registry)
    if not ids and not raw:
        ids = [sid.upper() for sid in (recent_ids or []) if sid.upper() in _source_registry_map(source_registry)]
    ids = list(dict.fromkeys(ids))[:5]

    registry = _source_registry_map(source_registry)
    # v93: a note that is only source IDs ("S1", "[S1] · S3") should name the
    # publisher/title, not print "Source: S1 [S1]".
    if raw and ids and re.fullmatch(r"(?:\[?\s*S\d+\s*\]?[\s,·;|]*)+", raw, flags=re.IGNORECASE):
        raw = ""
    if raw:
        raw_url = _safe_plain_url(raw)
        if re.fullmatch(r"open\s+source(?:\s*↗)?", raw, flags=re.IGNORECASE) and ids:
            labels = []
            for sid in ids:
                source = registry.get(sid) or {}
                publisher = html.escape(str(source.get("publisher") or source.get("title") or "Source"))
                labels.append(f'{publisher} {_citation_html(sid, registry)}')
            source_html = " · ".join(labels)
        elif raw_url:
            # Prefer the canonical source title/publisher when this URL is
            # already present in the research registry. A bare "Open source"
            # label is not useful enough for an executive report: the reader
            # should immediately see what the linked source is.
            matched_source = next((src for src in (source_registry or [])
                                   if str(src.get("url") or "").rstrip("/").casefold() == raw_url.rstrip("/").casefold()), None)
            if matched_source:
                visible = html.escape(str(matched_source.get("publisher") or matched_source.get("title") or _safe_url_host(raw_url) or raw_url))
                source_html = f'<a href="{html.escape(raw_url, quote=True)}" target="_blank" rel="noopener noreferrer" title="Open original source">{visible} ↗</a>'
            else:
                source_html = f'<a href="{html.escape(raw_url, quote=True)}" target="_blank" rel="noopener noreferrer">{html.escape(_safe_url_host(raw_url) or raw_url)} ↗</a>'
        else:
            source_html = _inline_md(raw, source_registry)
        explicit = set(_source_ids(raw))
        inferred = [sid for sid in ids if sid not in explicit]
        if inferred:
            source_html += " " + " ".join(_citation_html(sid, registry) for sid in inferred)
    elif ids:
        labels = []
        for sid in ids:
            source = registry.get(sid) or {}
            publisher = html.escape(str(source.get("publisher") or source.get("title") or "Source"))
            labels.append(f'{publisher} {_citation_html(sid, registry)}')
        source_html = " · ".join(labels)
    elif force:
        # Never expose renderer-internal provenance gaps as client-facing prose.
        # If no source can be resolved, omit the note rather than printing
        # "Attribution was not supplied with this item." inside the report.
        source_html = ""
    else:
        source_html = ""

    if not source_html and not method_text:
        return ""
    bits = []
    if source_html:
        bits.append(f'<span class="gg-source-note-label">Source:</span> {source_html}')
    if method_text:
        bits.append(f'<span class="gg-source-method"><span class="gg-source-note-label">Method:</span> {_inline_md(method_text, source_registry)}</span>')
    return '<div class="gg-source-note">' + " <span class=\"gg-source-note-separator\">·</span> ".join(bits) + '</div>'


def _inline_md(text: str, source_registry: list[dict] | None = None) -> str:
    """Bold/italic only — headings/lists/blockquotes are handled per-line
    by the block-level parser below, not here."""
    text = html.escape(text)
    text = _BOLD_RE.sub(r"<strong>\1</strong>", text)
    text = _ITALIC_RE.sub(r"<em>\1</em>", text)
    return _link_source_citations(text, source_registry)


def _to_num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _strict_chart_number(value) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    raw = str(value).strip().replace(",", "")
    raw = re.sub(r"^[₹$€£]\s*", "", raw)
    raw = re.sub(r"\s*%$", "", raw)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _chart_type_js(chart_type: str) -> str:
    """Map our internal chart spec 'type' to a Chart.js chart type.

    bullet/boxplot ride the generic/floating-bar 'bar' path (see the
    dedicated blocks in _render_chart_block below); treemap/heatmap render
    as plain HTML/CSS grids with no Chart.js involvement at all (no matrix/
    treemap plugin is loaded); bubble and radar are native Chart.js types."""
    return {
        "bar": "bar", "grouped_bar": "bar", "stacked_bar": "bar", "horizontal_bar": "bar",
        "line": "line", "area": "line", "stacked_area": "line", "step_line": "line", "spline": "line",
        "combo": "bar", "waterfall": "bar", "pie": "doughnut", "doughnut": "doughnut", "scatter": "scatter",
        "arrow": "bar", "histogram": "bar", "bullet": "bar", "bubble": "bubble", "radar": "radar",
    }.get(chart_type, "bar")



def _svg_text(x, y, text, *, size=11, fill="#26324a", weight=400, anchor="start"):
    return f'<text x="{x:.1f}" y="{y:.1f}" font-family="system-ui,-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif" font-size="{size}px" font-weight="{weight}" fill="{fill}" text-anchor="{anchor}">{html.escape(str(text))}</text>'


def _svg_palette(theme: dict | None):
    theme = theme or {}
    vals = theme.get("chartColors") or []
    if vals:
        return vals
    return [theme.get("accentColor") or "#00A3E0", theme.get("primaryColor") or "#0f1a3c", "#7c8aa6", "#c4873d", "#5a8d6e", "#8a6aa7"]


def _render_waterfall_chart_svg(chart: dict, idx: int, theme: dict | None = None) -> str:
    """Render waterfall deltas as a real cumulative SVG chart."""
    series = chart.get("series") or []
    points = (series[0].get("data") or []) if series and isinstance(series[0], dict) else []
    points = [point for point in points if isinstance(point, dict) and _strict_chart_number(point.get("value")) is not None]
    if len(points) < 2:
        log.warning("HTML report: skipping waterfall chart '%s' — fewer than two numeric points", chart.get("title", "?"))
        return ""

    width, height = 860, 360
    left, right, top, bottom = 76, 22, 24, 76
    values = [_strict_chart_number(point.get("value")) or 0.0 for point in points]
    starts: list[float] = []
    ends: list[float] = []
    running = 0.0
    for point, value in zip(points, values):
        starts.append(0.0 if point.get("isTotal") or point.get("total") else running)
        ends.append(value if point.get("isTotal") or point.get("total") else running + value)
        running = ends[-1]

    low = min([0.0, *starts, *ends]); high = max([0.0, *starts, *ends])
    span = high - low or 1.0
    chart_height = height - top - bottom
    zero_y = top + (high / span) * chart_height
    step = (width - left - right) / len(points)
    bar_width = min(72.0, step * 0.60)
    stagger = step < 85
    slot_chars = max(5, int((step * (1.7 if stagger else 1.0) - 6) / 5.6))
    palette = _svg_palette(theme)
    positive = palette[0] if palette else "#2E7357"
    total_color = palette[1] if len(palette) > 1 else "#183B56"
    negative = "#A9473F"
    ink = (theme or {}).get("textColor") or "#263238"
    muted = (theme or {}).get("mutedColor") or "#52616B"
    parts = [f'<div class="gg-chart-inline-svg"><svg viewBox="0 0 {width} {height}" role="img" aria-label="{html.escape(str(chart.get("title") or "Waterfall chart"), quote=True)}">']
    for tick in range(5):
        value = high - span * tick / 4
        y = top + chart_height * tick / 4
        parts.append(f'<line x1="{left}" y1="{y:.1f}" x2="{width-right}" y2="{y:.1f}" stroke="#d7e0e5" stroke-width="1"/>')
        parts.append(_svg_text(left - 9, y + 4, f"{value:,.3g}", size=9, fill=muted, anchor="end"))
    parts.append(f'<line x1="{left}" y1="{zero_y:.1f}" x2="{width-right}" y2="{zero_y:.1f}" stroke="#71818a" stroke-width="1.2"/>')
    for index, (point, start, end) in enumerate(zip(points, starts, ends)):
        center_x = left + step * (index + .5)
        y1 = top + (high - max(start, end)) / span * chart_height
        y2 = top + (high - min(start, end)) / span * chart_height
        rect_y = min(y1, y2); rect_h = max(1.5, abs(y2 - y1))
        is_total = bool(point.get("isTotal") or point.get("total"))
        color = total_color if is_total else positive if end >= start else negative
        parts.append(f'<rect x="{center_x-bar_width/2:.1f}" y="{rect_y:.1f}" width="{bar_width:.1f}" height="{rect_h:.1f}" rx="2" fill="{color}"/>')
        if index < len(points) - 1:
            connector_y = top + (high - end) / span * chart_height
            parts.append(f'<line x1="{center_x+bar_width/2:.1f}" y1="{connector_y:.1f}" x2="{center_x+step-bar_width/2:.1f}" y2="{connector_y:.1f}" stroke="#84939b" stroke-dasharray="3 3"/>')
        label = str(point.get("label") or f"Item {index+1}")
        display_label = label if len(label) <= slot_chars else (label[:slot_chars - 1] + "…")
        label_y = height - 34 if (stagger and index % 2 == 1) else height - 22
        parts.append(_svg_text(center_x, label_y, display_label, size=9, fill=ink, anchor="middle"))
        shown = values[index] if not is_total else end
        label_y = rect_y - 6 if end >= start else rect_y + rect_h + 13
        parts.append(_svg_text(center_x, label_y, f"{shown:,.4g}", size=9, fill=ink, weight=600, anchor="middle"))
    x_label = str(chart.get("xLabel") or "Driver / Step")
    y_label = str(chart.get("yLabel") or f"Change{f' ({chart.get("unit")})' if chart.get("unit") else ''}")
    parts.append(_svg_text((left + width - right) / 2, height - 6, x_label, size=11, weight=700, anchor="middle", fill=muted))
    y_mid = (top + height - bottom) / 2
    parts.append(f'<text x="15" y="{y_mid:.1f}" transform="rotate(-90 15 {y_mid:.1f})" font-family="system-ui,-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif" font-size="12px" font-weight="700" fill="{html.escape(str(muted), quote=True)}" text-anchor="middle">{html.escape(y_label)}</text>')
    parts.append("</svg></div>")
    return "".join(parts)


def _render_extended_svg_chart(chart: dict, idx: int, theme: dict | None = None) -> str:
    """Render chart/diagram types that are not delegated to Chart.js.

    Renderer-level safety net: extended SVG charts may be called directly by
    tests or fallback paths, so they must still receive a meaningful title and
    axis labels rather than relying solely on report.py normalization.
    """
    chart = dict(chart or {})
    ctype = normalize_chart_type(chart.get("type") or "")
    supported = {
        "funnel","funnel_pyramid","polar_area","gauge","pictogram","pareto","dot_plot","lollipop",
        "violin","density","ridgeline","gantt","timeline","marimekko","sunburst","sankey","chord",
        "network","tree","flowchart","venn","population_pyramid","progress","kpi","error_bar","control_chart",
        "candlestick","ohlc","funnel","funnel_pyramid",
    }
    if ctype not in supported:
        return ""
    W,H=860,360; left,right,top,bottom=82,24,38,56
    # Single-value indicators need far less height than plotted charts.
    if ctype in {"progress", "kpi"}:
        H = 170
    elif ctype == "gauge":
        H = 300
    series=chart.get("series") or []; data=(series[0].get("data") if series and isinstance(series[0],dict) else []) or []
    # Dynamic left margin clearance for horizontal charts where category labels sit on the left,
    # ensuring labels never collide with or walk over the rotated Y-axis title at x=14.
    if ctype in {"dot_plot", "lollipop", "gantt", "timeline", "pictogram"}:
        _lbl_candidates = [str(p.get("label", "")) for p in data if isinstance(p, dict)]
        if ctype in {"gantt", "timeline"}:
            _lbl_candidates.extend([str(e.get("label", "")) for e in (chart.get("events") or []) if isinstance(e, dict)])
        _max_l = max([len(l) for l in _lbl_candidates] + [0])
        left = max(90, min(int(W * 0.38), 36 + int(_max_l * 6.5)))
    event_count = len(chart.get("events") or data) if ctype in {"gantt", "timeline"} else 0
    row_count = len(data) if ctype in {"dot_plot", "lollipop", "population_pyramid"} else 0
    if event_count:
        H = max(H, min(2100, 110 + min(event_count, 89) * 20))
    elif row_count:
        H = max(H, min(2100, 110 + min(row_count, 89) * 19))
    pal=_svg_palette(theme); ink=(theme or {}).get("textColor") or "#26324a"; muted=(theme or {}).get("mutedColor") or "#6f7b90"
    accent=pal[0]; accent2=pal[1] if len(pal)>1 else "#17345f"; negative="#b45b5b"; positive="#4f8f6f"
    unit=str(chart.get("unit") or "").strip()
    first_name=str((series[0].get("name") if series and isinstance(series[0],dict) else "") or "Value").strip()
    title=str(chart.get("title") or "").strip()
    if not title or re.fullmatch(r"(?:chart|graph|figure)\s*\d*", title, re.I):
        if ctype in {"candlestick", "ohlc"}:
            title=f"{first_name} Price Range"
        elif ctype in {"gantt", "timeline"}:
            title=f"{first_name} Timeline"
        elif ctype in {"line", "area", "stacked_area", "step_line", "spline"}:
            title=f"{first_name} Trend"
        elif ctype in {"scatter", "bubble"} and len(series) > 1:
            title=f"{first_name} vs {str(series[1].get('name') or 'Y metric').strip()}"
        else:
            title=f"{first_name} by Category"
    xlab=str(chart.get("xLabel") or "").strip()
    ylab=str(chart.get("yLabel") or "").strip()
    axis_types_ext = {
        "candlestick", "ohlc", "error_bar", "control_chart", "pareto", "dot_plot",
        "lollipop", "density", "violin", "ridgeline", "gantt", "timeline",
        "population_pyramid", "waterfall",
    }
    if ctype in axis_types_ext:
        labels_for_axis=[str(p.get("label", "")) for p in data if isinstance(p,dict)]
        blob=" ".join(labels_for_axis[:8]).lower()
        if not xlab:
            if ctype in {"gantt", "timeline"}: xlab="Time"
            elif ctype in {"candlestick", "ohlc"}: xlab="Session"
            elif ctype in {"histogram", "violin", "density", "ridgeline"}: xlab="Value range"
            elif ctype == "waterfall": xlab="Driver / Step"
            elif ctype == "population_pyramid": xlab=f"Value{f' ({unit})' if unit else ''}"
            elif any(token in blob for token in ("q1", "q2", "q3", "q4", "fy", "202", "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")): xlab="Period"
            else: xlab="Category"
        if not ylab:
            if ctype in {"candlestick", "ohlc"}: ylab=f"Price{f' ({unit})' if unit else ''}"
            elif ctype in {"histogram"}: ylab="Observation count"
            elif ctype in {"violin", "density", "ridgeline"}: ylab="Density"
            elif ctype == "population_pyramid": ylab="Category"
            else: ylab=f"{first_name}{f' ({unit})' if unit else ''}"
    size_class = _chart_size_class(chart)
    parts=[f'<div class="gg-reveal gg-chart-wrap gg-chart-wrap--extended gg-chart-size-{size_class} gg-chart-type--{html.escape(ctype, quote=True)}" data-chart-size="{size_class}" data-reveal>',
           (f'<div class="gg-chart-title">{html.escape(title)}</div>' if title else ''),
           f'<div class="gg-chart-inline-svg"><svg viewBox="0 0 {W} {H}" role="img" aria-label="{html.escape(title, quote=True)}">']
    def done():
        if ctype in axis_types_ext:
            x_text = xlab or "Category"
            y_text = ylab or "Value"
            # Keep axis titles in a reserved footer/left margin so custom SVG
            # charts carry the same explicit metadata guarantee as Chart.js.
            parts.append(_svg_text((left+W-right)/2, H-6, x_text, size=12, weight=700, anchor="middle", fill=muted))
            y_mid = (top+H-bottom)/2
            parts.append(
                f'<text x="14" y="{y_mid:.1f}" transform="rotate(-90 14 {y_mid:.1f})" '
                f'font-family="system-ui,-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif" '
                f'font-size="12px" font-weight="700" fill="{html.escape(str(muted), quote=True)}" '
                f'text-anchor="middle">{html.escape(str(y_text))}</text>'
            )
        parts.append('</svg></div></div>'); return ''.join(parts)

    import math
    if ctype in {"gauge","progress","kpi"}:
        p=data[0] if data and isinstance(data[0],dict) else {"label":chart.get("label",""),"value":chart.get("value",0),"target":chart.get("target")}
        val=float(p.get("value",0) or 0); target=p.get("target")
        mn=float(chart.get("min",0) or 0); mx=float(chart.get("max",100) or 100)
        if mx<=mn: mx=mn+1
        ratio=max(0,min(1,(val-mn)/(mx-mn)))
        if ctype=="gauge":
            cx,cy,r=430,185,105; circ=2*math.pi*r; arc=circ*.75; dash=arc*ratio
            parts.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="#e7ebf1" stroke-width="22" stroke-dasharray="{arc:.2f} {circ:.2f}" stroke-linecap="round" transform="rotate(135 {cx} {cy})"/>')
            parts.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{accent}" stroke-width="22" stroke-dasharray="{dash:.2f} {circ:.2f}" stroke-linecap="round" transform="rotate(135 {cx} {cy})"/>')
            parts.append(_svg_text(cx,cy+10,str(p.get("value",val))+unit,size=34,weight=700,anchor="middle",fill=ink))
            parts.append(_svg_text(cx,cy+35,p.get("label",title),size=12,weight=600,anchor="middle",fill=muted))
            if target is not None: parts.append(_svg_text(cx,cy+57,f"Target {target}{unit}",size=10,weight=600,anchor="middle",fill=muted))
            return done()
        x0,x1=125,735; y=min(142, int(H*0.42)); bh=30
        parts.append(f'<rect x="{x0}" y="{y}" width="{x1-x0}" height="{bh}" rx="15" fill="#e9edf3"/>')
        parts.append(f'<rect x="{x0}" y="{y}" width="{(x1-x0)*ratio:.1f}" height="{bh}" rx="15" fill="{accent}"/>')
        parts.append(_svg_text(x0,y-16,p.get("label",title),size=14,weight=700,fill=ink))
        shown = f"{val:,.0f}" if abs(val) >= 1000 else f"{val:,.2f}".rstrip("0").rstrip(".")
        parts.append(_svg_text(x1,y-16,f"{shown}{unit}",size=16,weight=700,anchor="end",fill=ink))
        if target is not None:
            tr=max(0,min(1,(float(target)-mn)/(mx-mn))); tx=x0+(x1-x0)*tr
            parts.append(f'<line x1="{tx:.1f}" y1="{y-8}" x2="{tx:.1f}" y2="{y+bh+8}" stroke="{accent2}" stroke-width="3"/>')
            parts.append(_svg_text(tx,y+bh+26,f"Target {target}{unit}",size=10,weight=600,anchor="middle",fill=muted))
        return done()


    if ctype == "pictogram":
        rows=[p for p in data if isinstance(p,dict)][:12]
        if not rows:return ""
        maxv=max([max(0,_to_num(p.get("value"))) for p in rows]+[1]); y=top+20
        for i,p in enumerate(rows):
            v=max(0,_to_num(p.get("value"))); units=min(24,max(1,int(round(12*v/maxv))))
            parts.append(_svg_text(left-10,y+12,p.get("label",""),size=10,anchor="end",fill=muted))
            for j in range(units):
                x=left+j*27
                # Simple consistent person-like pictogram glyph using circle + body rectangle.
                parts.append(f'<circle cx="{x+7}" cy="{y+2}" r="4" fill="{pal[i%len(pal)]}"/>')
                parts.append(f'<rect x="{x+3}" y="{y+8}" width="8" height="12" rx="3" fill="{pal[i%len(pal)]}"/>')
            parts.append(_svg_text(left+24*27,y+12,f"{v:g}{unit}",size=9,weight=600,anchor="start",fill=ink))
            y += 48
        return done()

    if ctype in {"gantt","timeline"}:
        events=chart.get("events") or data
        if not events:return ""
        rows=[e for e in events if isinstance(e,dict)][:89]
        def _time_value(value):
            try:
                return float(value)
            except (TypeError, ValueError):
                try:
                    raw = str(value or "").strip().replace("Z", "+00:00")
                    parsed = datetime.fromisoformat(raw)
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=timezone.utc)
                    return parsed.timestamp()
                except Exception:
                    return None
        nums=[]
        for e in rows:
            for k in ("start","end"):
                val = _time_value(e.get(k))
                if val is not None: nums.append(val)
        if not nums:return ""
        lo=min(nums); hi=max(nums); span=hi-lo or 1; row=(H-top-bottom)/max(1,len(rows))
        for i,e in enumerate(rows):
            st = _time_value(e.get("start"))
            en = _time_value(e.get("end",e.get("start")))
            if st is None: continue
            if en is None: en=st
            x=left+(st-lo)/span*(W-left-right); w=max(5,(en-st)/span*(W-left-right)); y=top+i*row+max(2,row*.12)
            lbl = str(e.get("label", ""))
            avail_chars = max(8, int((left - 36) / 6.0))
            display_lbl = lbl if len(lbl) <= avail_chars else (lbl[:avail_chars - 1] + "…")
            parts.append(_svg_text(left-10,y+15,display_lbl,size=9,anchor="end",fill=muted))
            bar_h = max(5, min(22, row * .66))
            parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{bar_h:.1f}" rx="4" fill="{pal[i%len(pal)]}"/>')
            label_stride = max(1, int(len(rows) / 45))
            if e.get("description") and (i % label_stride == 0 or i == len(rows)-1):
                parts.append(_svg_text(min(W-right-2,x+w+8),y+min(14,bar_h*.8),str(e.get("description"))[:55],size=8.5,fill=muted))
        return done()

    if ctype in {"dot_plot","lollipop"}:
        vals=sorted([(_to_num(p.get("value")),str(p.get("label",""))) for p in data if isinstance(p,dict)], key=lambda z:z[0])[:89]
        if not vals:return ""
        lo=min(v for v,_ in vals); hi=max(v for v,_ in vals); span=hi-lo or 1; row=(H-top-bottom)/len(vals)
        avail_chars = max(8, int((left - 36) / 6.2))
        for i,(v,lbl) in enumerate(vals):
            y=top+(i+.55)*row; x=left+(v-lo)/span*(W-left-right)
            display_lbl = lbl if len(lbl) <= avail_chars else (lbl[:avail_chars - 1] + "…")
            parts.append(_svg_text(left-10,y+4,display_lbl,size=10,anchor="end",fill=muted))
            if ctype=="lollipop": parts.append(f'<line x1="{left}" y1="{y:.1f}" x2="{x:.1f}" y2="{y:.1f}" stroke="#d7dde6" stroke-width="2"/>')
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="6" fill="{accent}"/>')
            parts.append(_svg_text(x,y-9,f"{v:g}{unit}",size=9,anchor="middle",weight=600,fill=ink))
        return done()

    if ctype=="pareto":
        vals=sorted([(_to_num(p.get("value")),str(p.get("label",""))) for p in data if isinstance(p,dict)],reverse=True)[:14]
        if not vals:return ""
        maxv=max(v for v,_ in vals) or 1; total=sum(max(v,0) for v,_ in vals) or 1; slot=(W-left-right)/len(vals); pts=[]; cum=0
        for i,(v,lbl) in enumerate(vals):
            x=left+i*slot+slot*.14; bw=slot*.72; h=max(2,(max(v,0)/maxv)*(H-top-bottom-28)); y=H-bottom-h
            parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{h:.1f}" fill="{pal[i%len(pal)]}"/>')
            parts.append(_svg_text(x+bw/2,y-6,f"{v:g}{unit}",size=8.5,anchor="middle",weight=600,fill=ink))
            parts.append(_svg_text(x+bw/2,H-bottom+13,lbl,size=8.5,anchor="middle",fill=muted))
            cum+=max(v,0); px=left+(i+.5)*slot; py=H-bottom-(cum/total)*(H-top-bottom-28); pts.append(f"{px:.1f},{py:.1f}")
        parts.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="{negative}" stroke-width="3"/>')
        for pt in pts:
            x,y=map(float,pt.split(',')); parts.append(f'<circle cx="{x}" cy="{y}" r="4" fill="{negative}"/>')
        return done()

    if ctype in {"funnel","funnel_pyramid"}:
        vals=[(_to_num(p.get("value")),str(p.get("label",""))) for p in data if isinstance(p,dict)][:MAX_DISPLAY_SLICES + 1]
        if not vals:return ""
        maxv=max(v for v,_ in vals) or 1; band=(H-top-bottom)/len(vals); center=W/2
        for i,(v,lbl) in enumerate(vals):
            w=(W-250)*(max(v,0)/maxv); y=top+i*band+4; h=band-8; x=center-w/2; narrow=max(18,w*.42)
            if ctype=="funnel": poly=f"{center-w/2:.1f},{y:.1f} {center+w/2:.1f},{y:.1f} {center+narrow/2:.1f},{y+h:.1f} {center-narrow/2:.1f},{y+h:.1f}"
            else: poly=f"{center-narrow/2:.1f},{y:.1f} {center+narrow/2:.1f},{y:.1f} {center+w/2:.1f},{y+h:.1f} {center-w/2:.1f},{y+h:.1f}"
            parts.append(f'<polygon points="{poly}" fill="{pal[i%len(pal)]}" opacity=".92"/>')
            parts.append(_svg_text(center,y+h/2+4,f"{lbl} · {v:g}{unit}",size=10,anchor="middle",weight=700,fill="#ffffff"))
        return done()

    if ctype=="population_pyramid":
        rows=[p for p in data if isinstance(p,dict)][:89]
        if not rows:return ""
        mx=max([abs(_to_num(p.get("left",p.get("value",0)))) for p in rows]+[abs(_to_num(p.get("right",0))) for p in rows]+[1]); row=(H-top-bottom)/len(rows); cx=W/2
        for i,p in enumerate(rows):
            y=top+i*row+5; lv=abs(_to_num(p.get("left",p.get("value",0))))/mx; rv=abs(_to_num(p.get("right",0)))/mx
            lw=(W-left-right)/2*.82*lv; rw=(W-left-right)/2*.82*rv
            parts.append(f'<rect x="{cx-lw:.1f}" y="{y:.1f}" width="{lw:.1f}" height="{row-9:.1f}" fill="{accent}" opacity=".78"/>')
            parts.append(f'<rect x="{cx:.1f}" y="{y:.1f}" width="{rw:.1f}" height="{row-9:.1f}" fill="{accent2}" opacity=".78"/>')
            parts.append(_svg_text(cx-6,y+row/2+3,p.get("label",""),size=9,anchor="end",fill=muted))
        return done()

    if ctype in {"candlestick","ohlc"}:
        rows=[p for p in data if isinstance(p,dict)]
        if not rows:return ""
        allv=[]
        for p in rows:
            allv += [_to_num(p.get("high")),_to_num(p.get("low")),_to_num(p.get("open")),_to_num(p.get("close"))]
        lo=min(allv); hi=max(allv); span=hi-lo or 1; slot=(W-left-right)/len(rows); ymap=lambda v: top+(hi-v)/span*(H-top-bottom)
        for i,p in enumerate(rows):
            x=left+(i+.5)*slot; op=_to_num(p.get("open")); cl=_to_num(p.get("close")); low=_to_num(p.get("low")); high=_to_num(p.get("high")); col=positive if cl>=op else negative
            parts.append(f'<line x1="{x:.1f}" y1="{ymap(high):.1f}" x2="{x:.1f}" y2="{ymap(low):.1f}" stroke="#7f889a" stroke-width="1.5"/>')
            if ctype=="candlestick":
                y=min(ymap(op),ymap(cl)); hh=max(2,abs(ymap(op)-ymap(cl))); parts.append(f'<rect x="{x-slot*.24:.1f}" y="{y:.1f}" width="{slot*.48:.1f}" height="{hh:.1f}" fill="{col}" opacity=".88"/>')
            else:
                parts.append(f'<line x1="{x-slot*.23:.1f}" y1="{ymap(op):.1f}" x2="{x:.1f}" y2="{ymap(op):.1f}" stroke="{col}" stroke-width="3"/>')
                parts.append(f'<line x1="{x:.1f}" y1="{ymap(cl):.1f}" x2="{x+slot*.23:.1f}" y2="{ymap(cl):.1f}" stroke="{col}" stroke-width="3"/>')
        return done()

    if ctype in {"error_bar","control_chart"}:
        rows=[p for p in data if isinstance(p,dict)]
        if not rows:return ""
        vals=[]
        for p in rows: vals += [_to_num(p.get("value")),_to_num(p.get("high",p.get("ucl",p.get("value")))),_to_num(p.get("low",p.get("lcl",p.get("value"))))]
        lo=min(vals); hi=max(vals); span=hi-lo or 1; step=(W-left-right)/max(1,len(rows)-1); pts=[]
        for i,p in enumerate(rows):
            x=left+i*step; v=_to_num(p.get("value")); h=_to_num(p.get("high",p.get("ucl",v))); l=_to_num(p.get("low",p.get("lcl",v)))
            y=top+(hi-v)/span*(H-top-bottom); yh=top+(hi-h)/span*(H-top-bottom); yl=top+(hi-l)/span*(H-top-bottom)
            parts.append(f'<line x1="{x:.1f}" y1="{yh:.1f}" x2="{x:.1f}" y2="{yl:.1f}" stroke="{accent2}" stroke-width="2"/>')
            parts.append(f'<line x1="{x-5:.1f}" y1="{yh:.1f}" x2="{x+5:.1f}" y2="{yh:.1f}" stroke="{accent2}"/>')
            parts.append(f'<line x1="{x-5:.1f}" y1="{yl:.1f}" x2="{x+5:.1f}" y2="{yl:.1f}" stroke="{accent2}"/>')
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{accent}"/>'); pts.append(f"{x:.1f},{y:.1f}")
            label_stride = max(1, int(len(rows) / 16))
            if p.get("label") and (i % label_stride == 0 or i == len(rows)-1):
                parts.append(_svg_text(x,H-bottom+14,p.get("label"),size=8.5,anchor="middle",fill=muted))
        parts.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="{accent}" stroke-width="2"/>')
        return done()

    if ctype in {"density","violin","ridgeline"}:
        # Density-like forms use raw observations only.
        groups=series if ctype=="ridgeline" else [{"name":"Distribution","data":data}]
        if not groups:return ""
        bins=24; max_groups=min(6,len(groups)); base_y=top+25
        for gi,sg in enumerate(groups[:max_groups]):
            vals=[]
            for p in (sg.get("data") or []):
                if isinstance(p,dict):
                    if isinstance(p.get("values"),list): vals += [_to_num(v) for v in p.get("values")]
                    elif p.get("value") is not None: vals.append(_to_num(p.get("value")))
            if len(vals)<3:continue
            lo=min(vals); hi=max(vals); sp=hi-lo or 1; counts=[0]*bins
            for v in vals: counts[min(bins-1,max(0,int((v-lo)/sp*bins)))] += 1
            mc=max(counts) or 1
            if ctype=="violin":
                cx=(left+W-right)/2; half=(W-left-right)*.22; lp=[]; rp=[]
                for i,c in enumerate(counts):
                    y=top+i/(bins-1)*(H-top-bottom); w=c/mc*half; lp.append((cx-w,y)); rp.append((cx+w,y))
                parts.append(f'<polygon points="{" ".join(f"{x:.1f},{y:.1f}" for x,y in lp)} {" ".join(f"{x:.1f},{y:.1f}" for x,y in reversed(rp))}" fill="{accent}" opacity=".28" stroke="{accent}"/>')
                med=sorted(vals)[len(vals)//2]; my=top+(hi-med)/sp*(H-top-bottom); parts.append(f'<line x1="{cx-half*.35:.1f}" y1="{my:.1f}" x2="{cx+half*.35:.1f}" y2="{my:.1f}" stroke="{ink}" stroke-width="3"/>')
            else:
                b=base_y+gi*52; pts=[]
                for i,c in enumerate(counts):
                    x=left+i/(bins-1)*(W-left-right); y=b-(c/mc)*36; pts.append(f"{x:.1f},{y:.1f}")
                parts.append(f'<polygon points="{left},{b} {" ".join(pts)} {W-right},{b}" fill="{pal[gi%len(pal)]}" opacity=".18"/>')
                parts.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="{pal[gi%len(pal)]}" stroke-width="2.5"/>')
                parts.append(_svg_text(left-8,b-8,sg.get("name",f"Series {gi+1}"),size=9,anchor="end",fill=muted))
        return done()

    if ctype=="marimekko":
        rows=[p for p in data if isinstance(p,dict)]
        if not rows:return ""
        widths=[max(_to_num(p.get("width",1)),0.01) for p in rows]; total=sum(widths) or 1; vmax=max([max(_to_num(p.get("value",0)),0) for p in rows]+[1]); x=left
        for i,p in enumerate(rows):
            w=(widths[i]/total)*(W-left-right); h=max(2,_to_num(p.get("value",0))/vmax*(H-top-bottom)); y=H-bottom-h
            parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" fill="{pal[i%len(pal)]}" opacity=".88"/>')
            if w>=36: parts.append(_svg_text(x+w/2,y+h/2+4,p.get("label",""),size=8.5,anchor="middle",weight=700,fill="#ffffff"))
            x+=w
        return done()

    if ctype=="polar_area":
        rows=[p for p in data if isinstance(p,dict)][:12]
        if not rows:return ""
        cx,cy=430,180; maxv=max([abs(_to_num(p.get("value"))) for p in rows]+[1]); n=len(rows); step=2*math.pi/n
        for i,p in enumerate(rows):
            val=abs(_to_num(p.get("value"))); r=35+85*(val/maxv); a0=-math.pi/2+i*step; a1=a0+step*.9; x1=cx+r*math.cos(a0); y1=cy+r*math.sin(a0); x2=cx+r*math.cos(a1); y2=cy+r*math.sin(a1); large=1 if a1-a0>math.pi else 0
            d=f'M {cx:.1f},{cy:.1f} L {x1:.1f},{y1:.1f} A {r:.1f},{r:.1f} 0 {large} 1 {x2:.1f},{y2:.1f} Z'
            parts.append(f'<path d="{d}" fill="{pal[i%len(pal)]}" opacity=".84"/>'); lx=cx+(r+12)*math.cos((a0+a1)/2); ly=cy+(r+12)*math.sin((a0+a1)/2); parts.append(_svg_text(lx,ly,p.get("label",""),size=8,anchor="middle",fill=ink))
        return done()

    if ctype in {"sunburst"}:
        rows=[p for p in data if isinstance(p,dict)][:18]
        if not rows:return ""
        cx,cy=430,180; inner=48; outer=132; vals=[max(_to_num(p.get("value")),0) for p in rows]; total=sum(vals) or 1; a=-math.pi/2
        for i,p in enumerate(rows):
            ang=2*math.pi*vals[i]/total; b=a+ang; x1=cx+outer*math.cos(a); y1=cy+outer*math.sin(a); x2=cx+outer*math.cos(b); y2=cy+outer*math.sin(b); large=1 if ang>math.pi else 0
            xi1=cx+inner*math.cos(a); yi1=cy+inner*math.sin(a); xi2=cx+inner*math.cos(b); yi2=cy+inner*math.sin(b)
            d=f'M {xi1:.1f},{yi1:.1f} L {x1:.1f},{y1:.1f} A {outer},{outer} 0 {large} 1 {x2:.1f},{y2:.1f} L {xi2:.1f},{yi2:.1f} A {inner},{inner} 0 {large} 0 {xi1:.1f},{yi1:.1f} Z'
            parts.append(f'<path d="{d}" fill="{pal[i%len(pal)]}" stroke="#ffffff" stroke-width="2"/>'); a=b
        return done()

    if ctype=="venn":
        sets=chart.get("sets") or []
        if len(sets)<2:return ""
        cx1,cy,cx2=345,180,515; r=96
        parts.append(f'<circle cx="{cx1}" cy="{cy}" r="{r}" fill="{accent}" fill-opacity=".28" stroke="{accent}" stroke-width="2"/>')
        parts.append(f'<circle cx="{cx2}" cy="{cy}" r="{r}" fill="{accent2}" fill-opacity=".28" stroke="{accent2}" stroke-width="2"/>')
        parts.append(_svg_text(cx1-12,cy-72,sets[0].get("label","Set A"),size=12,weight=700,anchor="middle",fill=ink))
        parts.append(_svg_text(cx2+12,cy-72,sets[1].get("label","Set B"),size=12,weight=700,anchor="middle",fill=ink))
        ov=chart.get("overlap",chart.get("intersection","")); parts.append(_svg_text(430,cy+6,str(ov),size=16,weight=700,anchor="middle",fill=ink))
        return done()

    if ctype in {"sankey","chord","network","tree","flowchart"}:
        nodes=chart.get("nodes") or []; links=chart.get("links") or []
        labels=[]
        for i,n in enumerate(nodes):
            if isinstance(n,dict): labels.append(str(n.get("label",n.get("id",i))))
            else: labels.append(str(n))
        if not labels:
            for l in links:
                if isinstance(l,dict):
                    for z in (l.get("source"),l.get("target")):
                        if str(z) not in labels: labels.append(str(z))
        pos={}
        if ctype=="chord":
            cx,cy,r=430,180,118; n=max(1,len(labels))
            for i,lb in enumerate(labels):
                a=-math.pi/2+2*math.pi*i/n; pos[lb]=(cx+r*math.cos(a),cy+r*math.sin(a)); parts.append(f'<circle cx="{pos[lb][0]:.1f}" cy="{pos[lb][1]:.1f}" r="10" fill="{pal[i%len(pal)]}"/>'); parts.append(_svg_text(pos[lb][0],pos[lb][1]-16,lb,size=8,anchor="middle",fill=ink))
            for l in links:
                if not isinstance(l,dict):continue
                a=pos.get(str(l.get("source"))); b=pos.get(str(l.get("target")))
                if a and b: parts.append(f'<path d="M{a[0]:.1f},{a[1]:.1f} Q430,180 {b[0]:.1f},{b[1]:.1f}" fill="none" stroke="#bfc7d3" stroke-width="2" opacity=".8"/>')
            return done()
        if ctype=="sankey":
            lefts=[]; rights=[]
            for lb in labels:
                if not any(isinstance(l,dict) and str(l.get("target"))==lb for l in links): lefts.append(lb)
                if not any(isinstance(l,dict) and str(l.get("source"))==lb for l in links): rights.append(lb)
            lefts=lefts or labels[:max(1,len(labels)//2)]; rights=rights or labels[-max(1,len(labels)//2):]
            for i,lb in enumerate(lefts): pos[lb]=(145,top+35+i*48)
            for i,lb in enumerate(rights): pos[lb]=(715,top+35+i*48)
            maxv=max([_to_num(l.get("value",1)) for l in links if isinstance(l,dict)]+[1])
            for l in links:
                if not isinstance(l,dict):continue
                a=pos.get(str(l.get("source"))); b=pos.get(str(l.get("target")))
                if not a or not b:continue
                sw=2+10*max(0,_to_num(l.get("value",1)))/maxv
                parts.append(f'<path d="M{a[0]+72},{a[1]:.1f} C380,{a[1]:.1f} 480,{b[1]:.1f} {b[0]-72},{b[1]:.1f}" fill="none" stroke="{pal[len(parts)%len(pal)]}" stroke-opacity=".35" stroke-width="{sw:.1f}"/>')
            for lb,(x,y) in pos.items(): parts.append(f'<rect x="{x-72}" y="{y-14}" width="144" height="28" rx="5" fill="#f7f8fb" stroke="#d8dee8"/>'); parts.append(_svg_text(x,y+4,lb,size=9,anchor="middle",weight=600,fill=ink))
            return done()
        if ctype=="flowchart":
            for i,lb in enumerate(labels[:10]): pos[lb]=(430,top+25+i*45)
        elif ctype=="tree":
            for i,lb in enumerate(labels): pos[lb]=(150+(i%4)*180,top+32+(i//4)*74)
        else:
            for i,lb in enumerate(labels): pos[lb]=(170+(i%4)*165,top+55+(i//4)*68)
        for l in links:
            if not isinstance(l,dict):continue
            a=pos.get(str(l.get("source"))); b=pos.get(str(l.get("target")))
            if not a or not b:continue
            parts.append(f'<line x1="{a[0]:.1f}" y1="{a[1]:.1f}" x2="{b[0]:.1f}" y2="{b[1]:.1f}" stroke="#c4ccd8" stroke-width="2"/>')
        for i,(lb,(x,y)) in enumerate(pos.items()):
            if ctype=="flowchart": parts.append(f'<rect x="{x-60}" y="{y-16}" width="120" height="32" rx="8" fill="#f7f8fb" stroke="{pal[i%len(pal)]}" stroke-width="2"/>'); parts.append(_svg_text(x,y+4,lb,size=9,anchor="middle",weight=600,fill=ink))
            else: parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="18" fill="{pal[i%len(pal)]}"/>'); parts.append(_svg_text(x,y+4,lb,size=8.5,anchor="middle",weight=700,fill="#ffffff"))
        return done()

    return ""

def _render_chart_block_inner(chart: dict, idx: int, theme: dict | None = None) -> str:
    ctype = normalize_chart_type(chart.get("type") or "bar")
    chart = dict(chart); chart["type"] = ctype
    chart = fold_slice_tail_for_display(chart)
    series = chart.get("series") or []

    def _category_label(point: object) -> str:
        if isinstance(point, dict):
            for key in ("label", "name", "category", "period", "date", "x", "key"):
                value = point.get(key)
                if value is not None and str(value).strip():
                    return str(value).strip()
        return ""

    def _category_value(point: object) -> object:
        if isinstance(point, dict):
            for key in ("value", "y", "count", "amount"):
                if key in point:
                    return point.get(key)  # explicit None remains a chart gap, never a fabricated zero
            return None
        return point

    first_series = series[0] if series and isinstance(series[0], dict) else {}
    first_name = str(first_series.get("name") or "Value").strip()
    raw_points = first_series.get("data") or []
    labels = [(_category_label(point) or f"Observation {i+1}") for i, point in enumerate(raw_points)]
    unit = str(chart.get("unit") or "").strip()
    raw_title = str(chart.get("title") or "").strip()
    if not raw_title or re.fullmatch(r"(?:chart|graph|figure)\s*\d*", raw_title, re.I):
        if ctype in {"line", "area", "stacked_area", "step_line", "spline"}:
            raw_title = f"{first_name} Trend"
        elif ctype in {"scatter", "bubble"} and len(series) > 1:
            raw_title = f"{first_name} vs {str(series[1].get('name') or 'Y metric').strip()}"
        elif ctype in {"candlestick", "ohlc"}:
            raw_title = f"{first_name} Price Range"
        else:
            raw_title = f"{first_name} by Category"
    title = html.escape(raw_title[:120])
    x_label_raw = str(chart.get("xLabel") or "").strip()
    y_label_raw = str(chart.get("yLabel") or "").strip()
    axis_types = {
        "bar", "line", "area", "stacked_bar", "grouped_bar", "horizontal_bar",
        "stacked_area", "step_line", "spline", "combo", "arrow", "scatter", "bubble",
        "waterfall", "candlestick", "ohlc", "histogram", "boxplot", "error_bar",
        "control_chart", "dot_plot", "lollipop", "pareto", "violin", "density",
        "ridgeline", "gantt", "timeline", "population_pyramid",
    }
    if ctype in axis_types:
        label_blob = " ".join(labels[:8]).lower()
        if not x_label_raw:
            if ctype in {"gantt", "timeline"}:
                x_label_raw = "Time"
            elif ctype in {"candlestick", "ohlc"}:
                x_label_raw = "Session"
            elif ctype in {"histogram", "violin", "density", "ridgeline"}:
                x_label_raw = "Value range"
            elif ctype == "waterfall":
                x_label_raw = "Driver / Step"
            elif ctype == "population_pyramid":
                x_label_raw = f"Value{f' ({unit})' if unit else ''}"
            elif ctype in {"scatter", "bubble"}:
                x_label_raw = first_name or "X metric"
            elif any(token in label_blob for token in ("q1", "q2", "q3", "q4", "fy", "202", "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")):
                x_label_raw = "Period"
            else:
                x_label_raw = "Category"
        if not y_label_raw:
            if ctype in {"scatter", "bubble"} and len(series) > 1:
                y_name = str(series[1].get("name") or "Y metric").strip()
                y_label_raw = f"{y_name}{f' ({unit})' if unit else ''}"
            elif ctype == "histogram":
                y_label_raw = "Observation count"
            elif ctype in {"violin", "density", "ridgeline"}:
                y_label_raw = "Density"
            elif ctype in {"candlestick", "ohlc"}:
                y_label_raw = f"Price{f' ({unit})' if unit else ''}"
            elif ctype in {"waterfall", "dot_plot", "lollipop", "pareto", "error_bar", "control_chart"}:
                y_label_raw = f"{first_name}{f' ({unit})' if unit else ''}"
            elif ctype == "boxplot":
                y_label_raw = f"{first_name}{f' ({unit})' if unit else ''}"
            elif ctype == "population_pyramid":
                y_label_raw = "Category"
            else:
                y_label_raw = f"{first_name}{f' ({unit})' if unit else ''}"
    x_label = html.escape(x_label_raw[:70])
    y_label = html.escape(y_label_raw[:70])

    if ctype == "waterfall":
        svg = _render_waterfall_chart_svg(chart, idx, theme)
        if not svg:
            return ""
        return f'<div class="gg-reveal gg-chart-wrap" data-reveal>{f"<div class=\"gg-chart-title\">{title}</div>" if title else ""}{svg}</div>'

    extended = _render_extended_svg_chart(chart, idx, theme)
    if extended:
        return extended

    if ctype == "table":
        columns = chart.get("columns") or []
        rows = chart.get("rows") or []
        if len(columns) < 2 or len(rows) < 2:
            log.warning("HTML report: skipping table chart '%s' — insufficient columns/rows", chart.get("title", "?"))
            return ""
        return _render_table_markup(columns, rows, title=chart.get("title"))
        head = "".join(f"<th>{html.escape(str(c))}</th>" for c in columns)
        body_rows = []
        for r_i, row in enumerate(rows):
            cells = "".join(f"<td>{html.escape(str(c))}</td>" for c in row)
            body_rows.append(f'<tr style="--row-i:{r_i}">{cells}</tr>')
        body = "".join(body_rows)
        return f"""
<div class="gg-reveal gg-table-wrap" data-reveal>
  {f'<div class="gg-chart-title">{title}</div>' if title else ''}
  <div class="gg-table-scroll">
    <table class="gg-table"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>
  </div>
</div>"""

    canvas_id = f"gg-chart-{idx}"
    theme = theme or {}
    theme_gold = theme.get("accentColor") or BRAND_GOLD
    theme_navy = theme.get("primaryColor") or BRAND_NAVY
    palette = theme.get("chartColors") or [theme_gold, theme_navy, "#6a8caf", "#b46a55", "#7c6f9f"]

    if ctype == "scatter":
        # Scatter shares report.py's "two-series-sharing-labels" shape
        # (series[0] = x-metric per label, series[1] = y-metric per label),
        # the same shape utils/datawrapper.py merges into (x, y) columns for
        # the PDF path. Chart.js's scatter type needs {x, y} point objects,
        # not the flat category-indexed arrays bar/line use — feeding it
        # those renders a chart with zero visible points.
        x_series = series[0] if len(series) > 0 else {}
        y_series = series[1] if len(series) > 1 else {}
        x_by_label = {(_category_label(pt) or f"Observation {i+1}"): _category_value(pt) for i, pt in enumerate(x_series.get("data") or [])}
        y_by_label = {(_category_label(pt) or f"Observation {i+1}"): _category_value(pt) for i, pt in enumerate(y_series.get("data") or [])}
        points = [
            {"x": _to_num(x_by_label[lbl]), "y": _to_num(y_by_label[lbl]), "label": lbl}
            for lbl in x_by_label
            if lbl in y_by_label
        ]
        if not points:
            log.warning("HTML report: skipping scatter chart '%s' — no matched (x,y) points", chart.get("title", "?"))
            return ""
        datasets = [{
            "label": f"{x_series.get('name', 'X')} vs {y_series.get('name', 'Y')}",
            "data": points,
            "backgroundColor": theme_gold,
            "borderColor": theme_gold,
            "pointRadius": 6,
            "pointHoverRadius": 8,
        }]
        chart_config = {
            "type": "scatter",
            "data": {"datasets": datasets},
            "options": {
                "responsive": True,
                "maintainAspectRatio": False,
                "animation": {"duration": 1400, "easing": "easeOutQuart"},
                "plugins": {
                    "legend": {"display": False},
                    "title": {"display": False},
                },
                "scales": {
                    "x": {"title": {"display": bool(x_label or x_series.get("name")), "text": x_label or x_series.get("name", ""), "color": "#9aa3c0"},
                          "ticks": {"color": "#9aa3c0"}, "grid": {"color": "rgba(255,255,255,0.06)"}},
                    "y": {"title": {"display": bool(y_label or y_series.get("name")), "text": y_label or y_series.get("name", ""), "color": "#9aa3c0"},
                          "ticks": {"color": "#9aa3c0"}, "grid": {"color": "rgba(255,255,255,0.06)"}},
                },
            },
        }
        return f"""
<div class="gg-reveal gg-chart-wrap gg-chart-size-{_chart_size_class(chart)}" data-chart-size="{_chart_size_class(chart)}" data-reveal>
  {f'<div class="gg-chart-title">{title}</div>' if title else ''}
  <div class="gg-chart-canvas-box"><canvas id="{canvas_id}"></canvas></div>
</div>
<script>
window.__ggCharts = window.__ggCharts || [];
window.__ggCharts.push({{ id: "{canvas_id}", config: {json.dumps(chart_config)} }});
</script>"""

    if ctype == "arrow":
        if len(series) < 2:
            log.warning("HTML report: skipping arrow chart '%s' — needs two states", chart.get("title", "?"))
            return ""
        prev = {(_category_label(p) or f"Observation {i+1}"): _to_num(_category_value(p)) for i, p in enumerate(series[0].get("data") or [])}
        curr = {(_category_label(p) or f"Observation {i+1}"): _to_num(_category_value(p)) for i, p in enumerate(series[1].get("data") or [])}
        labels = [lbl for lbl in prev if lbl in curr]
        if not labels:
            return ""
        w,h = 760,310; left, right, top, bottom = 72, 28, 56, 54
        values = [prev[l] for l in labels] + [curr[l] for l in labels]
        lo, hi = min(values), max(values)
        if lo == hi: lo -= 1; hi += 1
        span = hi-lo or 1
        y = lambda v: top + (hi-v)/span*(h-top-bottom)
        xstep = (w-left-right)/max(1,len(labels))
        col0 = palette[0]; col1 = palette[1] if len(palette)>1 else "#172033"
        svg=[]
        for gi in range(6):
            gy=top+(h-top-bottom)*gi/5
            gv=hi-span*gi/5
            svg.append(f'<line x1="{left}" y1="{gy:.1f}" x2="{w-right}" y2="{gy:.1f}" stroke="#d9dde7" stroke-width="1"/>')
            svg.append(f'<text x="{left-10}" y="{gy+4:.1f}" text-anchor="end" font-size="9" fill="#6b7280">{gv:,.1f}</text>')
        for i,lbl in enumerate(labels):
            x=left+(i+0.5)*xstep
            y0, y1 = y(prev[lbl]), y(curr[lbl])
            direction = 1 if y1 < y0 else -1
            color = col1 if curr[lbl] >= prev[lbl] else "#b55b5b"
            svg.append(f'<line x1="{x:.1f}" y1="{y0:.1f}" x2="{x:.1f}" y2="{y1:.1f}" stroke="{color}" stroke-width="3"/>')
            if curr[lbl] != prev[lbl]:
                head = 7
                pts = f'{x-head:.1f},{y1+direction*head:.1f} {x+head:.1f},{y1+direction*head:.1f} {x:.1f},{y1:.1f}'
                svg.append(f'<polygon points="{pts}" fill="{color}"/>')
            svg.append(f'<circle cx="{x:.1f}" cy="{y0:.1f}" r="5" fill="{col0}"/>')
            svg.append(f'<circle cx="{x:.1f}" cy="{y1:.1f}" r="5" fill="{col1}"/>')
            svg.append(f'<text x="{x:.1f}" y="{min(y0,y1)-10:.1f}" text-anchor="middle" font-size="8.5" fill="#374151">{prev[lbl]:,.1f} → {curr[lbl]:,.1f}</text>')
            svg.append(f'<text x="{x:.1f}" y="{h-18}" text-anchor="middle" font-size="9" fill="#5f6675">{html.escape(lbl)}</text>')
        svg.append(f'<text x="{w/2:.1f}" y="{h-2}" text-anchor="middle" font-size="9.5" font-weight="600" fill="#4b5563">{html.escape(str(chart.get("xLabel") or "Category"))}</text>')
        svg.append(f'<text x="15" y="{h/2:.1f}" transform="rotate(-90 15 {h/2:.1f})" text-anchor="middle" font-size="9.5" font-weight="600" fill="#4b5563">{html.escape(str(chart.get("yLabel") or "Value"))}</text>')
        for legend_index, (legend_series, legend_color) in enumerate(((series[0], col0), (series[1], col1))):
            legend_y = 18 + legend_index * 17
            legend_name = html.escape(str(legend_series.get("name") or ("Previous" if legend_index == 0 else "Current")))
            svg.append(f'<circle cx="{left}" cy="{legend_y}" r="4" fill="{legend_color}"/><text x="{left+10}" y="{legend_y+3}" font-size="9" fill="#5f6675">{legend_name}</text>')
        svg_html=''.join(svg)
        return f"""
<div class="gg-reveal gg-chart-wrap gg-chart-wrap--arrow" data-reveal>
  {f'<div class="gg-chart-title">{title}</div>' if title else ''}
  <div class="gg-chart-canvas-box gg-chart-inline-svg"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" role="img" aria-label="{title}">{svg_html}</svg></div>
</div>"""

    if ctype == "waterfall":
        # Running-total bridge (e.g. Opening AUM -> Inflows -> Redemptions ->
        # Closing AUM), rendered as a floating bar chart: each dataset entry
        # is a [bottom, top] pair (Chart.js's built-in "floating bar" shape),
        # so no extra plugin is needed beyond the vanilla bar controller
        # already loaded. A point with "isTotal": true is an anchor (resets
        # the running total to its own value); everything else is a delta
        # off the previous cumulative — same semantics as pdf.py's _waterfall.
        data_pts = [(d if isinstance(d, dict) else {"label": str(i + 1), "value": _to_num(d)}) for i, d in enumerate((series[0].get("data") or []) if series else [])]
        if len(data_pts) < 1:
            log.warning("HTML report: skipping waterfall chart '%s' — no data points", chart.get("title", "?"))
            return ""
        wf_labels = [str(d.get("label", "")) for d in data_pts]
        ranges, colors = [], []
        cum = 0.0
        GREEN_HEX, RED_HEX = "#178a4c", "#c0392b"
        for d in data_pts:
            v = _to_num(d.get("value", 0))
            is_total = bool(d.get("isTotal"))
            if is_total:
                bottom, top = 0.0, v
                cum = v
            else:
                bottom, top = cum, cum + v
                cum = top
            ranges.append([min(bottom, top), max(bottom, top)])
            colors.append(theme_navy if is_total else (GREEN_HEX if v >= 0 else RED_HEX))
        chart_config = {
            "type": "bar",
            "data": {"labels": wf_labels, "datasets": [{
                "label": (series[0].get("name") if series else None) or "Value",
                "data": ranges,
                "backgroundColor": colors,
                "borderRadius": 3,
                "barPercentage": 0.6,
            }]},
            "options": {
                "responsive": True,
                "maintainAspectRatio": False,
                "animation": {"duration": 1400, "easing": "easeOutQuart"},
                "plugins": {"legend": {"display": False}, "title": {"display": False}},
                "scales": {
                    "x": {"title": {"display": bool(x_label), "text": x_label, "color": "#9aa3c0"},
                          "ticks": {"color": "#9aa3c0"}, "grid": {"color": "rgba(255,255,255,0.06)"}},
                    "y": {"title": {"display": bool(y_label), "text": y_label, "color": "#9aa3c0"},
                          "ticks": {"color": "#9aa3c0"}, "grid": {"color": "rgba(255,255,255,0.06)"}},
                },
            },
        }
        return f"""
<div class="gg-reveal gg-chart-wrap" data-reveal>
  {f'<div class="gg-chart-title">{title}</div>' if title else ''}
  <div class="gg-chart-canvas-box"><canvas id="{canvas_id}"></canvas></div>
</div>
<script>
window.__ggCharts = window.__ggCharts || [];
window.__ggCharts.push({{ id: "{canvas_id}", config: {json.dumps(chart_config)} }});
</script>"""

    if ctype == "candlestick":
        # OHLC candlestick, built from two overlaid floating-bar datasets on
        # the SAME category position (a thin one for the high/low wick, a
        # wide one for the open/close body) rather than a separate financial-
        # chart plugin/date-adapter — "grouped: false" on the x scale is what
        # makes Chart.js stack multiple bar datasets at the same x position
        # instead of placing them side by side.
        raw_data = (series[0].get("data") or []) if series else []
        data_pts = [(d if isinstance(d, dict) else {"label": str(i + 1), "open": _to_num(d), "high": _to_num(d), "low": _to_num(d), "close": _to_num(d)}) for i, d in enumerate(raw_data)]
        if len(data_pts) < 2:
            log.warning("HTML report: skipping candlestick chart '%s' — need ≥2 sessions", chart.get("title", "?"))
            return ""
        cs_labels = [str(d.get("label", "")) for d in data_pts]
        wick_ranges, body_ranges, cs_colors = [], [], []
        GREEN_HEX, RED_HEX = "#178a4c", "#c0392b"
        for d in data_pts:
            o, hi, lo, cl = (_to_num(d.get(k, 0)) for k in ("open", "high", "low", "close"))
            wick_ranges.append([lo, hi])
            body_ranges.append([min(o, cl), max(o, cl)])
            cs_colors.append(GREEN_HEX if cl >= o else RED_HEX)
        chart_config = {
            "type": "bar",
            "data": {"labels": cs_labels, "datasets": [
                {"label": "Range", "data": wick_ranges, "backgroundColor": cs_colors,
                 "barPercentage": 0.12, "categoryPercentage": 0.8, "borderSkipped": False},
                {"label": "Open-Close", "data": body_ranges, "backgroundColor": cs_colors,
                 "barPercentage": 0.5, "categoryPercentage": 0.8, "borderSkipped": False},
            ]},
            "options": {
                "responsive": True,
                "maintainAspectRatio": False,
                "animation": {"duration": 1400, "easing": "easeOutQuart"},
                "plugins": {"legend": {"display": False}, "title": {"display": False}},
                "scales": {
                    "x": {"grouped": False,
                          "title": {"display": bool(x_label), "text": x_label, "color": "#9aa3c0"},
                          "ticks": {"color": "#9aa3c0"}, "grid": {"color": "rgba(255,255,255,0.06)"}},
                    "y": {"title": {"display": bool(y_label), "text": y_label, "color": "#9aa3c0"},
                          "ticks": {"color": "#9aa3c0"}, "grid": {"color": "rgba(255,255,255,0.06)"}},
                },
            },
        }
        return f"""
<div class="gg-reveal gg-chart-wrap" data-reveal>
  {f'<div class="gg-chart-title">{title}</div>' if title else ''}
  <div class="gg-chart-canvas-box"><canvas id="{canvas_id}"></canvas></div>
</div>
<script>
window.__ggCharts = window.__ggCharts || [];
window.__ggCharts.push({{ id: "{canvas_id}", config: {json.dumps(chart_config)} }});
</script>"""

    if ctype == "sparkline":
        # Minimal axis-less trend line — deliberately no gridlines/ticks, no
        # legend, and no axis titles even if the spec supplied xLabel/yLabel
        # (a sparkline's whole point is to be a quick inline cue, not a full
        # chart). Only the two endpoints get a visible point marker.
        raw_data = (series[0].get("data") or []) if series else []
        data_pts = [(d if isinstance(d, dict) else {"label": str(i + 1), "value": _to_num(d)}) for i, d in enumerate(raw_data)]
        if len(data_pts) < 2:
            log.warning("HTML report: skipping sparkline chart '%s' — need ≥2 points", chart.get("title", "?"))
            return ""
        sp_labels = [str(d.get("label", "")) for d in data_pts]
        sp_vals = [_to_num(d.get("value", 0)) for d in data_pts]
        up = sp_vals[-1] >= sp_vals[0]
        color = "#178a4c" if up else "#c0392b"
        point_radius = [0] * len(sp_vals)
        point_radius[0] = point_radius[-1] = 4
        chart_config = {
            "type": "line",
            "data": {"labels": sp_labels, "datasets": [{
                "label": (series[0].get("name") if series else None) or "Value",
                "data": sp_vals,
                "borderColor": color,
                "backgroundColor": "transparent",
                "borderWidth": 2.5,
                "pointRadius": point_radius,
                "pointBackgroundColor": color,
                "tension": 0.35,
                "fill": False,
            }]},
            "options": {
                "responsive": True,
                "maintainAspectRatio": False,
                "animation": {"duration": 1200, "easing": "easeOutQuart"},
                "plugins": {"legend": {"display": False}, "title": {"display": False}},
                "scales": {
                    "x": {"display": False, "grid": {"display": False}},
                    "y": {"display": False, "grid": {"display": False}},
                },
            },
        }
        return f"""
<div class="gg-reveal gg-chart-wrap" data-reveal>
  {f'<div class="gg-chart-title">{title}</div>' if title else ''}
  <div class="gg-chart-canvas-box"><canvas id="{canvas_id}"></canvas></div>
</div>
<script>
window.__ggCharts = window.__ggCharts || [];
window.__ggCharts.push({{ id: "{canvas_id}", config: {json.dumps(chart_config)} }});
</script>"""

    if ctype == "bullet":
        # Actual-vs-target KPI bar, built with the same floating-bar overlay
        # trick as waterfall/candlestick above — three "bar" datasets pinned
        # to the SAME category via "grouped": false (here on the y-scale,
        # since indexAxis is "y") so they overlap instead of sitting side by
        # side: a wide light "track" to full scale, a narrower colored
        # "actual" bar, and a thin near-zero-width floating slice at the
        # target value drawn last, which reads as a tick mark. Matches
        # pdf.py's _bullet semantics (red when below target, navy otherwise)
        # with no extra plugin needed.
        raw_data = (series[0].get("data") or []) if series else []
        data_pts = [(d if isinstance(d, dict) else {"label": str(i + 1), "value": _to_num(d), "target": _to_num(d)}) for i, d in enumerate(raw_data)]
        if not data_pts:
            log.warning("HTML report: skipping bullet chart '%s' — no KPI points", chart.get("title", "?"))
            return ""
        unit = html.escape(str(chart.get("unit") or ""))
        bl_labels = [str(d.get("label", "")) for d in data_pts]
        max_v = max((max(_to_num(d.get("value", 0)), _to_num(d.get("target", 0))) for d in data_pts), default=1) or 1
        max_v *= 1.08
        RED_HEX = "#c0392b"
        tick_w = max_v * 0.006
        track, actual, actual_colors, target_ticks = [], [], [], []
        for d in data_pts:
            v = _to_num(d.get("value", 0))
            t = _to_num(d.get("target", 0))
            track.append([0, max_v])
            actual.append([0, v])
            actual_colors.append(RED_HEX if v < t else theme_navy)
            target_ticks.append([max(0, t - tick_w), t + tick_w])
        chart_config = {
            "type": "bar",
            "unit": html.unescape(unit),
            "data": {"labels": bl_labels, "datasets": [
                {"label": "Scale", "data": track, "backgroundColor": "#e3e9e5",
                 "barPercentage": 0.9, "categoryPercentage": 0.7, "borderSkipped": False},
                {"label": f"Actual{f' ({unit})' if unit else ''}", "data": actual,
                 "backgroundColor": actual_colors, "barPercentage": 0.45, "categoryPercentage": 0.7,
                 "borderSkipped": False},
                {"label": "Target", "data": target_ticks, "backgroundColor": "rgba(0,0,0,0)",
                 "barPercentage": 0.9, "categoryPercentage": 0.7, "borderSkipped": False},
            ]},
            "options": {
                "indexAxis": "y",
                "responsive": True,
                "maintainAspectRatio": False,
                "animation": {"duration": 1400, "easing": "easeOutQuart"},
                "plugins": {"legend": {"display": False}, "title": {"display": False}},
                "scales": {
                    "x": {"max": max_v,
                          "title": {"display": bool(y_label or unit), "text": y_label or (f"Capacity ({unit})" if unit else ""), "color": "#9aa3c0"},
                          "ticks": {"color": "#9aa3c0"}, "grid": {"color": "rgba(255,255,255,0.06)"}},
                    "y": {"grouped": False,
                          "title": {"display": bool(x_label and x_label.lower() != "metric"), "text": x_label if x_label.lower() != "metric" else "", "color": "#9aa3c0"},
                          "ticks": {"color": "#9aa3c0"}, "grid": {"display": False}},
                },
            },
        }
        box_h = max(200, 46 * len(bl_labels) + 40)
        return f"""
<div class="gg-reveal gg-chart-wrap" data-reveal>
  {f'<div class="gg-chart-title">{title}</div>' if title else ''}
  <div class="gg-chart-canvas-box" style="height:{box_h}px"><canvas id="{canvas_id}"></canvas></div>
</div>
<script>
window.__ggCharts = window.__ggCharts || [];
window.__ggCharts.push({{ id: "{canvas_id}", config: {json.dumps(chart_config)} }});
</script>"""

    if ctype == "boxplot":
        # Vertical box-and-whisker per entity — the same floating-bar overlay
        # trick, this time with "grouped": false on the x/category scale (as
        # in candlestick): a thin whisker range (min-max), a wider IQR box
        # (q1-q3) drawn on top, and a thin near-zero-height floating slice at
        # the median drawn last so it reads as a bright line through the box.
        raw_data = (series[0].get("data") or []) if series else []
        data_pts = [(d if isinstance(d, dict) else {"label": str(i + 1), "min": _to_num(d), "q1": _to_num(d), "median": _to_num(d), "q3": _to_num(d), "max": _to_num(d)}) for i, d in enumerate(raw_data)]
        if not data_pts:
            log.warning("HTML report: skipping boxplot chart '%s' — no entities", chart.get("title", "?"))
            return ""
        unit = html.escape(str(chart.get("unit") or ""))
        bp_labels = [str(d.get("label", "")) for d in data_pts]
        all_v = [_to_num(d.get(k, 0)) for d in data_pts for k in ("min", "max")]
        span = ((max(all_v) if all_v else 1) - (min(all_v) if all_v else 0)) or 1
        median_eps = span * 0.008
        whiskers, boxes, medians, box_colors = [], [], [], []
        for i, d in enumerate(data_pts):
            mn, q1, med, q3, mx = (_to_num(d.get(k, 0)) for k in ("min", "q1", "median", "q3", "max"))
            whiskers.append([mn, mx])
            boxes.append([q1, q3])
            medians.append([max(mn, med - median_eps), min(mx, med + median_eps)])
            box_colors.append(palette[i % len(palette)])
        chart_config = {
            "type": "bar",
            "data": {"labels": bp_labels, "datasets": [
                {"label": "Range", "data": whiskers, "backgroundColor": box_colors,
                 "barPercentage": 0.12, "categoryPercentage": 0.7, "borderSkipped": False},
                {"label": f"IQR{f' ({unit})' if unit else ''}", "data": boxes, "backgroundColor": box_colors,
                 "barPercentage": 0.55, "categoryPercentage": 0.7, "borderSkipped": False},
                {"label": "Median", "data": medians, "backgroundColor": "#ffffff",
                 "barPercentage": 0.55, "categoryPercentage": 0.7, "borderSkipped": False},
            ]},
            "options": {
                "responsive": True,
                "maintainAspectRatio": False,
                "animation": {"duration": 1400, "easing": "easeOutQuart"},
                "plugins": {"legend": {"display": False}, "title": {"display": False}},
                "scales": {
                    "x": {"grouped": False,
                          "title": {"display": bool(x_label), "text": x_label, "color": "#9aa3c0"},
                          "ticks": {"color": "#9aa3c0"}, "grid": {"color": "rgba(255,255,255,0.06)"}},
                    "y": {"title": {"display": bool(y_label), "text": y_label, "color": "#9aa3c0"},
                          "ticks": {"color": "#9aa3c0"}, "grid": {"color": "rgba(255,255,255,0.06)"}},
                },
            },
        }
        return f"""
<div class="gg-reveal gg-chart-wrap" data-reveal>
  {f'<div class="gg-chart-title">{title}</div>' if title else ''}
  <div class="gg-chart-canvas-box"><canvas id="{canvas_id}"></canvas></div>
</div>
<script>
window.__ggCharts = window.__ggCharts || [];
window.__ggCharts.push({{ id: "{canvas_id}", config: {json.dumps(chart_config)} }});
</script>"""

    if ctype == "treemap":
        # No matrix/treemap Chart.js plugin is loaded, so this renders as a
        # plain HTML/CSS flexbox grid instead of a canvas — the same
        # alternating-row slice-and-dice idea as pdf.py's _treemap (one row
        # per group sized by the group's total, items inside a row sized by
        # their own value via flex-grow), just laid out by the browser
        # instead of computed pixel rects.
        data_pts = (series[0].get("data") or []) if series else []
        if len(data_pts) < 3:
            log.warning("HTML report: skipping treemap chart '%s' — fewer than 3 items", chart.get("title", "?"))
            return ""
        unit = str(chart.get("unit") or "")
        items = sorted(data_pts, key=lambda d: abs(_to_num(d.get("value", 0))), reverse=True)
        total = sum(abs(_to_num(d.get("value", 0))) for d in items) or 1

        def _tm_cell(d: dict, color: str) -> str:
            v = abs(_to_num(d.get("value", 0)))
            pct = v / total * 100
            lbl = html.escape(str(d.get("label", "")))
            vs = html.escape(f"{pct:.1f}%") if (unit == "%" or not unit) else html.escape(f"{v:,.0f}{unit}")
            grow = max(v, total * 0.01)
            return (f'<div class="gg-tm-cell" style="flex-grow:{grow};background:{color}">'
                    f'<span class="gg-tm-label">{lbl}</span><span class="gg-tm-value">{vs}</span></div>')

        groups: dict[str, list] = {}
        order: list[str] = []
        for d in items:
            g = str(d.get("group") or "")
            if g not in groups:
                groups[g] = []
                order.append(g)
            groups[g].append(d)

        color_i = 0
        if len(groups) > 1:
            rows_html = []
            for g in order:
                g_items = groups[g]
                g_total = sum(abs(_to_num(d.get("value", 0))) for d in g_items) or 1
                cells = []
                for d in g_items:
                    cells.append(_tm_cell(d, palette[color_i % len(palette)]))
                    color_i += 1
                group_label = f'<div class="gg-tm-group-label">{html.escape(g)}</div>' if g else ""
                rows_html.append(
                    f'<div class="gg-tm-row" style="flex-grow:{g_total}">{group_label}'
                    f'<div class="gg-tm-row-cells">{"".join(cells)}</div></div>'
                )
            body = f'<div class="gg-tm-wrap">{"".join(rows_html)}</div>'
        else:
            cells = [_tm_cell(d, palette[i % len(palette)]) for i, d in enumerate(items)]
            body = f'<div class="gg-tm-wrap gg-tm-flat">{"".join(cells)}</div>'

        return f"""
<div class="gg-reveal gg-chart-wrap" data-reveal>
  {f'<div class="gg-chart-title">{title}</div>' if title else ''}
  {body}
</div>"""

    if ctype == "heatmap":
        # Also a plain HTML/CSS grid rather than canvas — no Chart.js matrix
        # plugin is loaded, and a rows x columns colored grid is simpler and
        # more accessible as real DOM/table-like markup anyway. Color scale
        # mirrors pdf.py's _heatmap: red (low) -> cream -> green (high).
        rows = chart.get("rows") or []
        cols = chart.get("columns") or []
        values = chart.get("values") or []
        if not rows or not cols or not values:
            log.warning("HTML report: skipping heatmap chart '%s' — missing rows/columns/values", chart.get("title", "?"))
            return ""
        unit = str(chart.get("unit") or "")
        # Exclude unavailable/null values from the colour range. Treating None
        # as 0 makes missing observations look like real flat/zero returns.
        flat = []
        for _row in values:
            for _raw in (_row if isinstance(_row, list) else []):
                if _raw is None or (isinstance(_raw, str) and _raw.strip().casefold() in {"", "n/a", "na", "null", "none", "—", "-"}):
                    continue
                _num_value = _to_num(_raw)
                if math.isfinite(_num_value):
                    flat.append(_num_value)
        lo, hi = (min(flat), max(flat)) if flat else (0.0, 1.0)
        span = (hi - lo) or 1
        RED_RGB, LIGHT_RGB, GREEN_RGB = (192, 57, 43), (240, 243, 255), (23, 138, 76)

        def _mix(c1: tuple, c2: tuple, t: float) -> tuple:
            t = max(0.0, min(1.0, t))
            return tuple(round(c1[k] + (c2[k] - c1[k]) * t) for k in range(3))

        def _color_for(v: float) -> str:
            t = (v - lo) / span
            rgb = _mix(RED_RGB, LIGHT_RGB, t / 0.5) if t < 0.5 else _mix(LIGHT_RGB, GREEN_RGB, (t - 0.5) / 0.5)
            return f"rgb({rgb[0]},{rgb[1]},{rgb[2]})"

        # A real <table> with inline styles: the previous CSS-grid markup
        # collapsed into a stacked list once the print stylesheet removed grid.
        cell_style = "padding:6px 8px;border:1px solid #ffffff;text-align:center;font-size:10px;font-variant-numeric:tabular-nums;"
        header_cells = "".join(
            f'<th style="{cell_style}font-weight:700;color:#30424B;background:#F2F5F4;">{html.escape(str(c))}</th>' for c in cols)
        body_rows = []
        for ri, row_name in enumerate(rows):
            row_vals = values[ri] if ri < len(values) else []
            cells = []
            for ci in range(len(cols)):
                raw_v = row_vals[ci] if ci < len(row_vals) else None
                is_missing = raw_v is None or (isinstance(raw_v, str) and raw_v.strip().casefold() in {"", "n/a", "na", "null", "none", "—", "-"})
                if is_missing:
                    cells.append(f'<td title="No observed value for this period" style="{cell_style}background:#E8ECEB;color:#59666B;font-style:italic;">N/A</td>')
                    continue
                v = _to_num(raw_v)
                if not math.isfinite(v):
                    cells.append(f'<td title="No valid numeric observation" style="{cell_style}background:#E8ECEB;color:#59666B;font-style:italic;">N/A</td>')
                    continue
                vs = html.escape(f"{v:,.0f}{unit}" if abs(v) >= 100 else f"{v:.2f}".rstrip("0").rstrip(".") + unit)
                cells.append(f'<td style="{cell_style}background:{_color_for(v)};color:#1D2B33;">{vs}</td>')
            body_rows.append(
                f'<tr><th scope="row" style="{cell_style}text-align:left;font-weight:600;color:#30424B;background:#F7F9F8;">'
                f'{html.escape(str(row_name))}</th>{"".join(cells)}</tr>'
            )
        return f"""
<div class="gg-reveal gg-chart-wrap" data-reveal>
  {f'<div class="gg-chart-title">{title}</div>' if title else ''}
  <div class="gg-hm-wrap" style="overflow-x:auto;">
    <table class="gg-heatmap-table" style="width:100%;border-collapse:collapse;table-layout:fixed;">
      <thead><tr><th style="{cell_style}background:#F2F5F4;"></th>{header_cells}</tr></thead>
      <tbody>{"".join(body_rows)}</tbody>
    </table>
  </div>
</div>"""

    if ctype == "bubble":
        # Native Chart.js type. Series order is fixed per report.py's spec:
        # series[0]=x-metric, series[1]=y-metric, series[2]=size-metric, all
        # sharing the same labels — merged into {x, y, r} points here since
        # Chart.js's bubble controller needs that shape, not the flat
        # category-indexed arrays bar/line use.
        if len(series) < 3:
            log.warning("HTML report: skipping bubble chart '%s' — needs 3 series (x/y/size)", chart.get("title", "?"))
            return ""
        x_by = {(_category_label(p) or f"Observation {i+1}"): _to_num(_category_value(p)) for i, p in enumerate(series[0].get("data") or [])}
        y_by = {(_category_label(p) or f"Observation {i+1}"): _to_num(_category_value(p)) for i, p in enumerate(series[1].get("data") or [])}
        s_by = {(_category_label(p) or f"Observation {i+1}"): abs(_to_num(_category_value(p))) for i, p in enumerate(series[2].get("data") or [])}
        bb_labels = [l for l in x_by if l in y_by and l in s_by]
        if not bb_labels:
            log.warning("HTML report: skipping bubble chart '%s' — no matched entities across series", chart.get("title", "?"))
            return ""
        s_max = max(s_by[l] for l in bb_labels) or 1
        points = [{"x": x_by[l], "y": y_by[l], "r": 5 + (s_by[l] / s_max) * 22, "label": l} for l in bb_labels]
        bubble_colors = [palette[i % len(palette)] for i in range(len(points))]
        chart_config = {
            "type": "bubble",
            "data": {"datasets": [{
                "label": series[2].get("name", "Size"),
                "data": points,
                "backgroundColor": [f"{c}a6" for c in bubble_colors],
                "borderColor": bubble_colors,
                "borderWidth": 1.5,
            }]},
            "options": {
                "responsive": True,
                "maintainAspectRatio": False,
                "animation": {"duration": 1400, "easing": "easeOutQuart"},
                "plugins": {"legend": {"display": False}, "title": {"display": False}},
                "scales": {
                    "x": {"title": {"display": True, "text": x_label or series[0].get("name", ""), "color": "#9aa3c0"},
                          "ticks": {"color": "#9aa3c0"}, "grid": {"color": "rgba(255,255,255,0.06)"}},
                    "y": {"title": {"display": True, "text": y_label or series[1].get("name", ""), "color": "#9aa3c0"},
                          "ticks": {"color": "#9aa3c0"}, "grid": {"color": "rgba(255,255,255,0.06)"}},
                },
            },
        }
        return f"""
<div class="gg-reveal gg-chart-wrap" data-reveal>
  {f'<div class="gg-chart-title">{title}</div>' if title else ''}
  <div class="gg-chart-canvas-box"><canvas id="{canvas_id}"></canvas></div>
</div>
<script>
window.__ggCharts = window.__ggCharts || [];
window.__ggCharts.push({{ id: "{canvas_id}", config: {json.dumps(chart_config)} }});
</script>"""

    if ctype == "radar":
        # Native Chart.js type. Every series must share the same metric
        # labels in the same order (enforced upstream by report.py's
        # validator) — metrics come from series[0] and every other series is
        # re-keyed by label just in case ordering ever drifts.
        if not series:
            log.warning("HTML report: skipping radar chart '%s' — no series", chart.get("title", "?"))
            return ""
        metrics = [_category_label(p) or f"Metric {i+1}" for i, p in enumerate(series[0].get("data") or [])]
        if len(metrics) < 3:
            log.warning("HTML report: skipping radar chart '%s' — fewer than 3 metrics", chart.get("title", "?"))
            return ""
        radar_datasets = []
        for s_i, s in enumerate(series):
            by_label = {(_category_label(p) or f"Metric {i+1}"): _to_num(_category_value(p)) for i, p in enumerate(s.get("data") or [])}
            values = [by_label.get(m, 0) for m in metrics]
            color = palette[s_i % len(palette)]
            radar_datasets.append({
                "label": s.get("name", f"Series {s_i+1}"),
                "data": values,
                "backgroundColor": f"{color}33",
                "borderColor": color,
                "borderWidth": 2,
                "pointBackgroundColor": color,
            })
        chart_config = {
            "type": "radar",
            "data": {"labels": metrics, "datasets": radar_datasets},
            "options": {
                "responsive": True,
                "maintainAspectRatio": False,
                "animation": {"duration": 1400, "easing": "easeOutQuart"},
                "plugins": {
                    "legend": {"display": len(radar_datasets) > 1, "labels": {"color": "#e8e8f0"}},
                    "title": {"display": False},
                },
                "scales": {
                    "r": {
                        "angleLines": {"color": "rgba(255,255,255,0.08)"},
                        "grid": {"color": "rgba(255,255,255,0.08)"},
                        "pointLabels": {"color": "#9aa3c0", "font": {"size": 11}},
                        "ticks": {"color": "#9aa3c0", "backdropColor": "transparent"},
                    },
                },
            },
        }
        return f"""
<div class="gg-reveal gg-chart-wrap" data-reveal>
  {f'<div class="gg-chart-title">{title}</div>' if title else ''}
  <div class="gg-chart-canvas-box"><canvas id="{canvas_id}"></canvas></div>
</div>
<script>
window.__ggCharts = window.__ggCharts || [];
window.__ggCharts.push({{ id: "{canvas_id}", config: {json.dumps(chart_config)} }});
</script>"""

    labels: list[str] = []
    for s in series:
        for point in (s.get("data") or []):
            label = _category_label(point)
            # Do not merge multiple unlabelled observations into one blank
            # category. Use a stable, visible fallback so all points remain
            # represented without inventing a descriptive value.
            if not label:
                point_index = list(s.get("data") or []).index(point) + 1
                label = f"Observation {point_index}"
            if label not in labels:
                labels.append(label)

    datasets = []
    for s_i, s in enumerate(series):
        by_label = {}
        for point_index, point in enumerate(s.get("data") or [], start=1):
            label = _category_label(point) or f"Observation {point_index}"
            by_label[label] = _category_value(point)
        # Null is a missing observation, not a zero. Chart.js leaves gaps in
        # line/area series and blank bars for absent categories instead of
        # painting fabricated zeroes into a report.
        values = [by_label.get(label) for label in labels]
        color = palette[s_i % len(palette)]
        is_line = ctype in {"line", "area", "stacked_area", "step_line", "spline"}
        bg = color if not is_line else f"{color}33" if ctype in {"area", "stacked_area"} else "transparent"
        ds = {
            "label": s.get("name", f"Series {s_i+1}"),
            "data": values,
            "backgroundColor": bg,
            "borderColor": color,
            "borderWidth": 2,
            "fill": ctype in {"area", "stacked_area"},
            "tension": 0 if ctype == "step_line" else 0.32,
        }
        if ctype == "step_line": ds["stepped"] = True
        if ctype == "combo" and s_i > 0:
            ds["type"] = "line"; ds["backgroundColor"] = "transparent"; ds["fill"] = False
            # A ratio line over amount bars needs its own axis, otherwise one
            # of the two series is flattened against the other's scale.
            if str(chart.get("secondaryUnit") or "").strip() and str(chart.get("secondaryUnit")) != str(chart.get("unit") or ""):
                ds["yAxisID"] = "y1"
        datasets.append(ds)

    if ctype in {"pie", "doughnut", "polar_area"} and len(datasets) == 1:
        # v93: composition charts colour each slice (fixed categorical order)
        # with a 2px surface gap; one flat colour made the ring unreadable.
        datasets[0]["backgroundColor"] = [palette[i % len(palette)] for i in range(len(labels))]
        datasets[0]["borderColor"] = "#FFFFFF"
        datasets[0]["borderWidth"] = 2

    # House styles may colour a single bar series by sign (gains green, losses red).
    sign_colors = (theme or {}).get("barSignColors")
    if isinstance(sign_colors, dict) and ctype in {"bar", "horizontal_bar"} and len(datasets) == 1:
        signed = [_to_num(v) for v in datasets[0].get("data") or []]
        if any(v is not None and v < 0 for v in signed) and any(v is not None and v > 0 for v in signed):
            datasets[0]["backgroundColor"] = [sign_colors["neg"] if (v or 0) < 0 else sign_colors["pos"] for v in signed]
            datasets[0]["borderColor"] = datasets[0]["backgroundColor"]

    if ctype == "histogram":
        # Same shape as a single-series bar chart — the only thing that
        # makes it read as a histogram rather than a ranked bar chart is the
        # bars touching, so zero out the inter-bar/inter-category gap
        # instead of adding a separate render path (mirrors datawrapper.py,
        # which needs no special-casing here either).
        for ds in datasets:
            ds["barPercentage"] = 1.0
            ds["categoryPercentage"] = 0.98
            ds["borderColor"] = "#0a1230"
            ds["borderWidth"] = 1

    # Belt-and-braces: charts/labels normally arrive pre-validated by
    # _is_plausible_chart in report.py, but if a chart ever reaches here
    # with no labels or every dataset value missing/zero, emitting a
    # canvas would just render an empty box — skip it instead.
    if not labels or not any(v not in (None, 0) for ds in datasets for v in ds["data"]):
        log.warning("HTML report: skipping chart '%s' — no plottable data points", chart.get("title", "?"))
        return ""

    # Composition charts must be readable in a static PDF: every slice is
    # named in the legend together with its share of the total.
    if ctype in {"pie", "doughnut"} and datasets:
        try:
            _slice_values = [max(0.0, _to_num(v) or 0.0) for v in (datasets[0].get("data") or [])]
            _slice_total = sum(_slice_values) or 0.0
            if _slice_total > 0 and len(_slice_values) == len(labels):
                labels = [f"{label} — {value / _slice_total * 100:.1f}%" for label, value in zip(labels, _slice_values)]
        except Exception:
            pass
    chart_config = {
        "type": _chart_type_js(ctype),
        "data": {"labels": labels, "datasets": datasets},
        "options": {
            "responsive": True,
            "maintainAspectRatio": False,
            "animation": {"duration": 1400, "easing": "easeOutQuart"},
            "plugins": {
                "legend": {"display": (ctype in {"pie", "doughnut"} or len(datasets) > 1),
                           "position": ("right" if ctype in {"pie", "doughnut"} else "top"),
                           "labels": {"color": "#30424B", "usePointStyle": True}},
                "ggValueLabels": {"enabled": True},
                "title": {"display": False},
            },
            "scales": ({} if ctype == "pie" else {
                "x": {"title": {"display": bool(x_label), "text": x_label, "color": "#52616B", "font": {"size": 13, "weight": "bold"}},
                      "ticks": {"color": "#52616B", "font": {"size": 11}, "maxRotation": 35, "minRotation": 0}, "grid": {"color": "#D9E2DE"}},
                "y": {"title": {"display": bool(y_label), "text": y_label, "color": "#52616B", "font": {"size": 13, "weight": "bold"}},
                      "ticks": {"color": "#52616B", "font": {"size": 11}, "maxRotation": 0}, "grid": {"color": "#D9E2DE"}},
            }),
        },
    }


    # Horizontal bars plot the quantitative metric on the X axis and the
    # entity/category on the Y axis. The report schema keeps xLabel=category and
    # yLabel=metric so the same semantic fields work for vertical and horizontal
    # bars; map them to their visual axes here instead of swapping the stored data.
    if ctype == "horizontal_bar":
        _scales = chart_config["options"].setdefault("scales", {})
        _scales.setdefault("x", {}).setdefault("title", {}).update({"display": bool(y_label), "text": y_label, "font": {"size": 13, "weight": "bold"}})
        _scales.setdefault("y", {}).setdefault("title", {}).update({"display": bool(x_label), "text": x_label, "font": {"size": 13, "weight": "bold"}})

    # A validator may authorize a logarithmic scale only for comparable,
    # strictly-positive values. Reflect that instruction in the actual renderer;
    # merely annotating the data label is insufficient.
    if str(chart.get("axisScale") or "").casefold() in {"log", "logarithmic"} and ctype in {"bar", "horizontal_bar"}:
        _log_axis = "x" if ctype == "horizontal_bar" else "y"
        _axis = chart_config["options"].setdefault("scales", {}).setdefault(_log_axis, {})
        _axis["type"] = "logarithmic"
        _axis.pop("beginAtZero", None)
        _axis.setdefault("title", {})["display"] = True
        _axis.setdefault("title", {})["text"] = y_label or "Value (log scale)"
        _axis["title"]["font"] = {"size": 13, "weight": "bold"}
        _axis.setdefault("ticks", {})["font"] = {"size": 11}

    # Bar charts should start the quantitative axis at zero. Chart.js otherwise
    # auto-selects a positive minimum for datasets such as [150.26, 56.09, 51.41,
    # 11.75, 5.17], which makes the last two categories invisible or misleading.
    _log_axis_active = str(chart.get("axisScale") or "").casefold() in {"log", "logarithmic"}
    if ctype in {"bar", "stacked_bar", "grouped_bar", "histogram", "combo"}:
        numeric_values = [_to_num(value) for dataset in datasets for value in (dataset.get("data") or [])]
        if not _log_axis_active and numeric_values and all(value >= 0 for value in numeric_values):
            _y_axis = chart_config["options"].setdefault("scales", {}).setdefault("y", {})
            _y_axis["beginAtZero"] = True
            # A writer-supplied positive minimum (seen: 10.4 for a 50% bar)
            # crops bars and exaggerates differences; bars start at zero.
            for _key in ("min", "suggestedMin"):
                try:
                    if _key in _y_axis and float(_y_axis[_key]) > 0:
                        _y_axis.pop(_key, None)
                except (TypeError, ValueError):
                    _y_axis.pop(_key, None)
    if ctype == "horizontal_bar":
        chart_config["options"]["indexAxis"] = "y"
        numeric_values = [_to_num(value) for dataset in datasets for value in (dataset.get("data") or [])]
        if not _log_axis_active and numeric_values and all(value >= 0 for value in numeric_values):
            chart_config["options"].setdefault("scales", {}).setdefault("x", {})["beginAtZero"] = True
    # Capacity milestone line charts should also show the zero baseline so the
    # visual does not exaggerate changes by cropping away the lower range.
    if ctype in {"line", "area", "stacked_area", "step_line", "spline"}:
        capacity_axis = (
            unit.casefold() in {"gw", "mw", "kw", "tw", "gwh", "mwh", "twh"}
            or "capacity" in y_label_raw.casefold()
            or "generation" in y_label_raw.casefold()
            or "installed capacity" in raw_title.casefold()
        )
        numeric_values = [_to_num(value) for dataset in datasets for value in (dataset.get("data") or [])]
        if capacity_axis and numeric_values and all(value >= 0 for value in numeric_values):
            chart_config["options"].setdefault("scales", {}).setdefault("y", {})["beginAtZero"] = True
    if ctype in {"stacked_bar", "stacked_area"}:
        chart_config["options"].setdefault("scales", {}).setdefault("x", {})["stacked"] = True
        chart_config["options"].setdefault("scales", {}).setdefault("y", {})["stacked"] = True
    if ctype in {"pie", "doughnut"}:
        chart_config["options"]["scales"] = {}
    if ctype == "combo":
        chart_config["options"].setdefault("scales", {}).setdefault("y", {})["title"] = {"display": bool(y_label), "text": y_label, "color": "#52616B"}
        if any(ds.get("yAxisID") == "y1" for ds in datasets):
            secondary = html.escape(str(chart.get("secondaryLabel") or chart.get("secondaryUnit") or ""))[:60]
            chart_config["options"]["scales"]["y1"] = {
                "position": "right", "grid": {"drawOnChartArea": False},
                "title": {"display": bool(secondary), "text": secondary, "color": "#52616B"},
                "ticks": {"color": "#52616B"},
            }
    return f"""
<div class="gg-reveal gg-chart-wrap" data-reveal>
  {f'<div class="gg-chart-title">{title}</div>' if title else ''}
  <div class="gg-chart-canvas-box"><canvas id="{canvas_id}"></canvas></div>
</div>
<script>
window.__ggCharts = window.__ggCharts || [];
window.__ggCharts.push({{ id: "{canvas_id}", config: {json.dumps(chart_config)} }});
</script>"""


def _render_chart_block(
    chart: dict,
    idx: int,
    theme: dict | None = None,
    source_registry: list[dict] | None = None,
    source_override: object = None,
    recent_source_ids: list[str] | None = None,
    force_source_note: bool = False,
) -> str:
    """Render a chart plus a concise analytical takeaway.

    The LLM supplies rationale/takeaway metadata; this wrapper keeps those
    visible in both HTML and print so a figure never appears as unexplained
    decoration. The inner renderer remains responsible for the chart-specific
    DOM/canvas implementation.
    """
    rendered = _render_chart_block_inner(chart, idx, theme)
    if not rendered:
        return rendered
    inline_gallery = bool(chart.get("gallery"))
    if inline_gallery:
        # Evidence-gallery charts are blended into the narrative with their own
        # caption, so they render compact and without the Context/Takeaway boxes.
        rendered = rendered.replace("gg-chart-wrap", "gg-chart-wrap gg-chart-wrap--inline", 1)
    rationale = "" if inline_gallery else html.escape(str(chart.get("rationale") or "").strip())
    takeaway = "" if inline_gallery else html.escape(str(chart.get("takeaway") or "").strip())
    source_value = source_override if source_override is not None else (
        chart.get("source") or chart.get("sourceNote") or chart.get("sources") or chart.get("sourceIds")
    )
    generic_rationale = "This view isolates the relationship that is most decision-useful in the underlying evidence."
    generic_takeaway = "The figure makes the key comparison, trend, threshold, or distribution visible without relying on color alone."
    bits = []
    # A "Context" note that only describes the chart itself ("Bar chart
    # effectively compares…", "provides a clean visual ranking") adds nothing
    # for the reader; keep context that explains the data.
    if rationale and re.search(
        r"(?i)^\s*(?:the\s+|this\s+)?(?:horizontal\s+|vertical\s+|stacked\s+|grouped\s+)?(?:bar|line|pie|doughnut|area|scatter|bullet|gauge)?\s*(?:chart|graph|visual|plot)\b"
        r".{0,80}\b(?:provides?|offers?|effectively|clearly|clean|readable|purpose-built|is\s+used|helps|allows|compares|shows|displays|visuali[sz]es)\b",
        str(chart.get("rationale") or ""),
    ):
        rationale = ""
    if rationale and rationale.strip() != generic_rationale:
        bits.append(f'<div class="gg-chart-note gg-chart-note--why"><span class="gg-chart-note-label">Context</span>{_inline_md(str(chart.get("rationale") or ""), source_registry)}</div>')
    if takeaway and takeaway.strip() != generic_takeaway:
        bits.append(f'<div class="gg-chart-note gg-chart-note--takeaway"><span class="gg-chart-note-label">Key takeaway</span>{_inline_md(str(chart.get("takeaway") or ""), source_registry)}</div>')
    source_note = _render_source_note(
        source_value, source_registry, recent_source_ids,
        method=chart.get("method") or chart.get("methodology"),
        force=force_source_note,
    )
    if source_note:
        bits.append(source_note)
    note_html = '<div class="gg-chart-notes">' + ''.join(bits) + '</div>'
    # Chart blocks end with either </div></script> or just </div>. Insert the
    # notes immediately before the outer chart wrapper closes. The regex keeps
    # nested SVG/HTML elements intact.
    pos = rendered.rfind('</div>')
    if pos >= 0:
        return rendered[:pos] + note_html + rendered[pos:]
    return rendered + note_html


def _render_image_block(image: dict, idx: int, source_registry: list[dict] | None = None, force_source_note: bool = False) -> str:
    url = html.escape(str(image.get("url", "")))
    raw_caption = str(image.get("caption", "")).strip()
    generic_caption_patterns = (
        r"^(?:a|the)\s+(?:rising|declining|increasing|decreasing|upward|downward|orange|blue|green|bar|line|pie)\b.*\b(?:chart|graph|visual|figure)\b.*\b(?:shows|illustrates|highlights|displays|depicts)\b",
        r"^(?:this|the)\s+(?:chart|graph|image|visual)\s+(?:shows|illustrates|highlights|displays)\b",
        r"^image (?:source|from) (?:external|web)\b",
    )
    if any(re.search(pattern, raw_caption, re.I) for pattern in generic_caption_patterns):
        raw_caption = ""
    caption = html.escape(raw_caption)
    if not url:
        return ""
    image_asset_url = html.unescape(str(image.get("url") or "").strip())
    raw_image_source = image.get("source") or image.get("sources") or image.get("sourceId") or image.get("sourceIds")
    # IMPORTANT: image.url is the asset itself (often a CDN/proxy .png/.jpg),
    # not the publisher/source webpage. Never use the asset URL as a visible
    # Source link. Only sourceUrl/sourceURL is authoritative as a webpage URL.
    source_page_url = html.unescape(str(image.get("sourceUrl") or image.get("sourceURL") or "").strip())
    if source_page_url and (source_page_url.rstrip("/").casefold() == image_asset_url.rstrip("/").casefold() or _is_probable_image_asset_url(source_page_url)):
        source_page_url = ""
    image_method = image.get("method") or image.get("methodology")

    # A few search providers call the image asset `source` too. Treat URL-shaped
    # values there as untrusted asset metadata rather than source-webpage URLs.
    image_source = raw_image_source
    if isinstance(image_source, str) and _safe_plain_url(image_source):
        if not source_page_url and image_source.rstrip("/").casefold() != image_asset_url.rstrip("/").casefold():
            # Do not infer that an arbitrary URL in the image record is the
            # publisher page; only explicit sourceUrl/sourceURL may do that.
            image_source = None
        else:
            image_source = None

    # If the image has a source title/publisher but no source ID/URL, reconcile it
    # against the canonical report source registry before rendering the note.
    if not image_source and not _safe_plain_url(source_page_url):
        source_title = str(image.get("sourceTitle") or image.get("publisher") or "").strip().casefold()
        if source_title and source_registry:
            matched = next((src for src in source_registry if source_title == str(src.get("title") or src.get("publisher") or "").strip().casefold()
                            or source_title in str(src.get("title") or "").strip().casefold()
                            or source_title in str(src.get("publisher") or "").strip().casefold()), None)
            if matched:
                image_source = matched.get("source_id") or matched.get("id") or None
                if not source_page_url and _safe_plain_url(str(matched.get("url") or "")):
                    source_page_url = str(matched.get("url") or "").strip()
    if image_source:
        source_note = _render_source_note(
            image_source, source_registry, None,
            method=image_method,
            force=force_source_note,
        )
    elif _safe_plain_url(source_page_url):
        # The source link must open the source webpage/article, never the image asset.
        matched_source = next(
            (src for src in (source_registry or [])
             if str(src.get("url") or "").rstrip("/").casefold() == source_page_url.rstrip("/").casefold()),
            None,
        )
        label = html.escape(
            str(image.get("sourceTitle") or image.get("publisher") or "").strip()
            or str((matched_source or {}).get("publisher") or (matched_source or {}).get("title") or "").strip()
            or _display_source_host(source_page_url)
            or "Source"
        )
        source_note = (
            f'<div class="gg-source-note gg-source-page-link"><span class="gg-source-note-label">Source:</span> '
            f'<a href="{html.escape(source_page_url, quote=True)}" target="_blank" rel="noopener noreferrer">{label} ↗</a>'
            f'{f" <span class=\"gg-source-note-separator\">·</span> <span class=\"gg-source-method\"><span class=\"gg-source-note-label\">Method:</span> {_inline_md(str(image_method), source_registry)}</span>" if image_method else ""}</div>'
        )
    else:
        # External images without an identified publisher page are not eligible
        # for publication. A disclosure below an unattributed image is not enough:
        # it still exposes an unverifiable visual as report content. Suppress it
        # until a sourceUrl/sourceURL or a registry-backed source ID is available.
        log.warning("HTML report: suppressing image without identifiable publisher/source page")
        return ""
    return f"""
<figure class="gg-reveal gg-figure" data-reveal>
  <img src="{url}" alt="{caption}" loading="eager" decoding="async" />
  {f'<figcaption>{caption}</figcaption>' if caption else ''}
  {source_note}
</figure>"""


def _numeric_table_value(value: object) -> bool:
    raw = re.sub(r"\s+", " ", str(value or "").strip())
    if not raw:
        return False
    return re.fullmatch(
        r"(?:[₹$€£]\s*)?\(?[-+]?\d[\d,]*(?:\.\d+)?\)?\s*(?:%|bps?|GW|MW|kW|GWh|MWh|kWh|BU|MU|Cr\.?|crore|lakh|x|years?)?",
        raw,
        flags=re.IGNORECASE,
    ) is not None


def _render_table_markup(
    headers: list,
    rows: list,
    source_registry: list[dict] | None = None,
    source_value: object = None,
    recent_source_ids: list[str] | None = None,
    force_source_note: bool = False,
    title: object = None,
    method: object = None,
) -> str:
    clean_headers = [str(header or "") for header in headers]
    clean_rows = [[str(cell or "") for cell in row] for row in rows if isinstance(row, (list, tuple))]
    if len(clean_headers) < 2 or not clean_rows:
        return ""
    width = len(clean_headers)
    clean_rows = [(row + [""] * width)[:width] for row in clean_rows]
    table_class = "gg-table gg-table--wide" if width >= 5 else "gg-table"
    numeric_cols = []
    weights = []
    for column, header in enumerate(clean_headers):
        values = [row[column] for row in clean_rows if row[column].strip()]
        numeric_count = sum(_numeric_table_value(value) for value in values)
        numeric = bool(values) and numeric_count >= max(1, (len(values) + 1) // 2)
        numeric_cols.append(numeric)
        sample = [header, *[row[column] for row in clean_rows[:40]]]
        weights.append(max(8 if numeric else 12, min(42, max((len(value) for value in sample), default=12))))
    weight_total = sum(weights) or width
    colgroup = "<colgroup>" + "".join(f'<col style="width:{100 * value / weight_total:.1f}%" />' for value in weights) + "</colgroup>"
    head = "".join(
        f'<th class="{"gg-cell-numeric" if numeric_cols[index] else ""}">{_inline_md(header, source_registry)}</th>'
        for index, header in enumerate(clean_headers)
    )
    body_rows = []
    for row in clean_rows[:500]:
        cells = "".join(
            f'<td class="{"gg-cell-numeric" if numeric_cols[index] else ""}">{_inline_md(value, source_registry)}</td>'
            for index, value in enumerate(row)
        )
        body_rows.append(f"<tr>{cells}</tr>")
    title_html = f'<div class="gg-chart-title">{_inline_md(str(title), source_registry)}</div>' if title else ""
    note = _render_source_note(source_value, source_registry, recent_source_ids, method, force=force_source_note)
    return (
        f'<div class="gg-table-wrap gg-block" data-row-count="{len(clean_rows)}" data-col-count="{width}">' + title_html +
        f'<div class="gg-table-scroll"><table class="{table_class}">' + colgroup +
        f'<thead><tr>{head}</tr></thead><tbody>{"".join(body_rows)}</tbody></table></div>' +
        note + '</div>'
    )


def _chart_spec_from_json(raw_json: str) -> tuple[dict | None, bool]:
    """Parse inline JSON chart data through the shared chart catalog.

    Preserve chart-type-specific data shapes (OHLC, box plots, events,
    heatmaps, node/link diagrams, set overlaps and distributions) rather than
    reducing every point to a scalar value. Invalid chart-looking JSON is
    removed from prose so raw JSON does not leak into the report.
    """
    try:
        payload = json.loads(raw_json)
    except Exception:
        return None, bool(re.search(r'"(?:chart_type|chartType|x_labels|xLabels)"\s*:', raw_json))
    if not isinstance(payload, dict):
        return None, False
    spec = payload.get("chart") if isinstance(payload.get("chart"), dict) else payload
    raw_type = spec.get("type") or spec.get("chart_type") or spec.get("chartType")
    raw_series = spec.get("series") or []
    has_chart_data_field = any(key in spec for key in (
        "series", "data", "x_labels", "xLabels", "labels", "categories", "rows", "nodes",
        "events", "sets", "links", "values",
    ))
    looks_like_chart = bool(raw_type and has_chart_data_field)
    if not looks_like_chart:
        return None, False

    chart_type = normalize_chart_type(raw_type)
    if chart_type not in CHART_TYPES:
        log.warning("HTML report: removing inline chart JSON with unsupported type=%s", chart_type)
        return None, True

    chart = dict(spec)
    chart["type"] = chart_type
    chart["title"] = str(spec.get("title") or spec.get("name") or f"{chart_type.replace('_', ' ').title()} chart")[:120]
    chart["xLabel"] = str(spec.get("xLabel") or spec.get("x_label") or "")
    chart["yLabel"] = str(spec.get("yLabel") or spec.get("y_label") or spec.get("unit") or "")

    # Native structures: do not demand a scalar series for these types.
    if chart_type == "heatmap":
        rows, cols, values = chart.get("rows"), chart.get("columns"), chart.get("values")
        valid = (isinstance(rows, list) and len(rows) >= 2 and isinstance(cols, list) and len(cols) >= 2
                 and isinstance(values, list) and len(values) == len(rows)
                 and all(isinstance(row, list) and len(row) == len(cols) for row in values))
        return (chart, True) if valid else (None, True)
    if chart_type in {"sankey", "chord", "network", "tree", "flowchart"}:
        nodes, links = chart.get("nodes"), chart.get("links")
        valid = (isinstance(nodes, list) and isinstance(links, list) and bool(links)
                 and all(isinstance(link, dict) and link.get("source") is not None and link.get("target") is not None for link in links))
        return (chart, True) if valid else (None, True)
    if chart_type in {"gantt", "timeline"}:
        events = chart.get("events")
        if not isinstance(events, list):
            candidate_series = chart.get("series") if isinstance(chart.get("series"), list) else []
            candidate_data = candidate_series[0].get("data") if candidate_series and isinstance(candidate_series[0], dict) else []
            events = candidate_data if isinstance(candidate_data, list) and all(isinstance(e, dict) and e.get("start") is not None for e in candidate_data) else None
        valid = isinstance(events, list) and bool(events) and all(
            isinstance(event, dict) and event.get("start") is not None
            and event.get("end", event.get("start")) is not None for event in events
        )
        if valid:
            chart["events"] = events
            chart.setdefault("xLabel", "Time")
            chart.setdefault("yLabel", "Event")
            return chart, True
        return None, True
    if chart_type == "venn":
        return (chart, True) if isinstance(chart.get("sets"), list) and len(chart["sets"]) >= 2 else (None, True)
    if chart_type == "table":
        return (chart, True) if isinstance(chart.get("columns"), list) and isinstance(chart.get("rows"), list) else (None, True)

    labels = spec.get("x_labels") or spec.get("xLabels") or spec.get("labels") or spec.get("categories") or []
    if not isinstance(labels, list):
        labels = []
    labels = [str(label) for label in labels]

    if isinstance(raw_series, dict):
        raw_series = [{"name": key, "values": value} for key, value in raw_series.items()]
    if not isinstance(raw_series, list):
        raw_series = []
    raw_data = spec.get("data")
    if not raw_series and isinstance(raw_data, dict):
        if all(isinstance(values, list) for values in raw_data.values()):
            raw_series = [{"name": key, "values": values} for key, values in raw_data.items()]
    if not raw_series and isinstance(raw_data, list):
        if all(isinstance(row, dict) for row in raw_data):
            raw_series = [{"name": str(spec.get("seriesName") or "Value"), "data": raw_data}]
        elif all(_strict_chart_number(row) is not None for row in raw_data):
            raw_series = [{"name": str(spec.get("seriesName") or "Value"), "values": raw_data}]

    normalized_series = []
    for index, item in enumerate(raw_series[:20]):
        if isinstance(item, list):
            item = {"name": f"Series {index + 1}", "values": item}
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("label") or f"Series {index + 1}")[:80]
        data = item.get("data") or item.get("values") or item.get("points") or []
        if isinstance(data, dict):
            data = [{"label": key, "value": value} for key, value in data.items()]
        if not isinstance(data, list):
            continue
        points = []
        for point_index, point in enumerate(data):
            if isinstance(point, dict):
                point_copy = dict(point)
                point_copy["label"] = str(point.get("label") or point.get("x") or (labels[point_index] if point_index < len(labels) else f"Point {point_index + 1}"))
                value = point.get("value", point.get("y", point.get("actual", point.get("amount"))))
                number = _strict_chart_number(value)
                if number is not None:
                    point_copy["value"] = number
                    points.append(point_copy)
                elif chart_type in {"candlestick", "ohlc"} and all(point.get(key) is not None for key in ("open", "high", "low", "close")):
                    points.append(point_copy)
                elif chart_type == "boxplot" and all(point.get(key) is not None for key in ("min", "q1", "median", "q3", "max")):
                    points.append(point_copy)
                elif chart_type == "bullet" and point.get("target") is not None:
                    points.append(point_copy)
                elif chart_type in {"density", "violin", "ridgeline"} and isinstance(point.get("values"), list):
                    points.append(point_copy)
                elif chart_type == "population_pyramid" and (point.get("left") is not None or point.get("right") is not None):
                    points.append(point_copy)
            elif isinstance(point, (list, tuple)) and len(point) >= 2:
                number = _strict_chart_number(point[1])
                if number is not None:
                    points.append({"label": str(point[0]), "value": number})
            else:
                number = _strict_chart_number(point)
                if number is not None:
                    points.append({"label": labels[point_index] if point_index < len(labels) else f"Point {point_index + 1}", "value": number})
        if points:
            normalized_series.append({**item, "name": name, "data": points})

    if not normalized_series:
        return None, True
    chart["series"] = normalized_series
    chart["source"] = spec.get("source") or spec.get("sources") or spec.get("sourceIds")
    chart["method"] = spec.get("method") or spec.get("methodology")
    chart["rationale"] = spec.get("rationale")
    chart["takeaway"] = spec.get("takeaway")
    return chart, True


def _json_block_at(lines: list[str], start: int) -> tuple[str, int] | None:
    """Collect a balanced, possibly multiline JSON object beginning at start."""
    content: list[str] = []
    depth = 0
    in_string = False
    escaped = False
    started = False
    for index in range(start, min(len(lines), start + 500)):
        line = lines[index]
        content.append(line)
        for char in line:
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char in "{[":
                depth += 1
                started = True
            elif char in "}]":
                depth -= 1
                if started and depth == 0:
                    return "\n".join(content), index + 1
    return None


def _split_long_paragraph(text: str, threshold: int = 155) -> list[str]:
    """Split only long prose at sentence boundaries, favoring clear transitions."""
    sentence_parts = re.split(r"(?<=[.!?])\s+(?=(?:[A-Z0-9“\"(]))", text.strip())
    if len(sentence_parts) < 3 or len(re.findall(r"\S+", text)) <= threshold:
        return [text]
    transitions = re.compile(r"^(?:however|yet|by contrast|in contrast|that said|more importantly|meanwhile|operationally|financially|for investors|at the same time|in practice|as a result|consequently|additionally|furthermore|on the other hand|from a |at project level|at sector level|on balance)\b", re.IGNORECASE)
    result: list[str] = []
    pending: list[str] = []
    for index, sentence in enumerate(sentence_parts):
        pending.append(sentence.strip())
        word_count = len(re.findall(r"\S+", " ".join(pending)))
        remaining = len(re.findall(r"\S+", " ".join(sentence_parts[index+1:])))
        next_sentence = sentence_parts[index+1].strip() if index + 1 < len(sentence_parts) else ""
        semantic_break = bool(transitions.match(next_sentence)) and word_count >= 55
        density_break = word_count >= 125 and remaining >= 45
        if (semantic_break or density_break) and len(pending) >= 2:
            result.append(" ".join(pending).strip())
            pending = []
    if pending:
        result.append(" ".join(pending).strip())
    return [part for part in result if part] or [text]



def _diagram_label_lines(raw_lines: list[str]) -> list[str]:
    """Extract meaningful labels from ASCII/box-drawing flow diagrams.

    The report model occasionally emits a flowchart as plain text.  Rather than
    letting browser pagination split the raw text, normalize the diagram into a
    small native SVG visual.  The extractor deliberately favors preserving
    visible labels and their order over trying to infer a full graph topology.
    """
    labels: list[str] = []
    seen: set[str] = set()
    border_chars = "+-|_=─━│┃┌┐└┘├┤┬┴┼╭╮╰╯║═"
    arrow_only = re.compile(r"^[\s|\-_=+─━│┃┌┐└┘├┤┬┴┼╭╮╰╯║═<>←→↑↓▲▼⋮:·.]+$")

    for raw in raw_lines:
        line = str(raw).rstrip()
        st = line.strip()
        if not st:
            continue
        # Strip common border/pipe characters while keeping normal punctuation.
        cleaned = st.translate(str.maketrans({ch: " " for ch in border_chars}))
        cleaned = cleaned.replace("→", " → ").replace("←", " ← ").replace("↑", " ↑ ").replace("↓", " ↓ ")
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" -|:_")
        if not cleaned or arrow_only.fullmatch(cleaned):
            continue
        # Lines can contain two side-by-side labels separated by a large gap.
        chunks = [c.strip() for c in re.split(r"\s{3,}", cleaned) if c.strip()]
        if not chunks:
            chunks = [cleaned]
        for chunk in chunks:
            # Drop obvious box drawing/arrow residue.
            chunk = re.sub(r"^[\s|:;,]+|[\s|:;,]+$", "", chunk)
            if not chunk or len(chunk) < 2:
                continue
            key = re.sub(r"\s+", " ", chunk).casefold()
            if key in seen:
                continue
            seen.add(key)
            labels.append(chunk)
            if len(labels) >= 10:
                return labels
    return labels


def _render_ascii_diagram_svg(labels: list[str], *, title: str = "Structured flow diagram") -> str:
    """Render normalized diagram labels as one atomic SVG visual block."""
    if len(labels) < 2:
        return ""
    width = 860
    node_w = 650
    left = (width - node_w) / 2
    pad_x = 22
    node_h = 58
    gap = 22
    top = 18
    bottom = 24
    max_lines = 3

    def wrap_words(text: str, max_chars: int = 58) -> list[str]:
        words = text.split()
        rows: list[str] = []
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if len(candidate) <= max_chars:
                current = candidate
            else:
                if current:
                    rows.append(current)
                current = word
        if current:
            rows.append(current)
        if len(rows) > max_lines:
            rows = rows[:max_lines]
            if rows[-1] and len(rows[-1]) > max_chars - 1:
                rows[-1] = rows[-1][:max_chars - 1].rstrip() + "…"
            else:
                rows[-1] = rows[-1].rstrip(" .") + "…"
        return rows or [text[:max_chars]]

    height = top + len(labels) * node_h + max(0, len(labels) - 1) * gap + bottom
    parts = [
        f'<div class="gg-diagram-wrap" role="img" aria-label="{html.escape(title, quote=True)}">',
        f'<svg class="gg-inline-diagram" viewBox="0 0 {width} {height}" preserveAspectRatio="xMidYMin meet">',
    ]
    for i, label in enumerate(labels):
        y = top + i * (node_h + gap)
        fill = "#f7faf8" if i % 2 == 0 else "#ffffff"
        stroke = "#4f8f6f" if i % 2 == 0 else "#7a8a96"
        parts.append(f'<rect x="{left:.1f}" y="{y:.1f}" width="{node_w}" height="{node_h}" rx="10" fill="{fill}" stroke="{stroke}" stroke-width="1.7"/>')
        lines = wrap_words(label)
        line_h = 16
        start_y = y + node_h / 2 - (len(lines) - 1) * line_h / 2 + 5
        for j, row in enumerate(lines):
            parts.append(_svg_text(width / 2, start_y + j * line_h, row, size=11, weight=650, anchor="middle", fill="#263238"))
        if i < len(labels) - 1:
            x = width / 2
            y1 = y + node_h
            y2 = y + node_h + gap
            parts.append(f'<line x1="{x:.1f}" y1="{y1:.1f}" x2="{x:.1f}" y2="{y2-7:.1f}" stroke="#7a8a96" stroke-width="1.8"/>')
            parts.append(f'<polygon points="{x-5:.1f},{y2-10:.1f} {x+5:.1f},{y2-10:.1f} {x:.1f},{y2-2:.1f}" fill="#7a8a96"/>')
    parts.append("</svg></div>")
    return "".join(parts)


def _ascii_art_block_at(lines: list[str], start: int) -> tuple[str, int] | None:
    """Recover ASCII/box-drawing flow diagrams into one atomic SVG visual.

    Models occasionally emit flowcharts as plain text or fenced text.  Detect
    those cases before Markdown paragraph handling, extract the visible labels,
    and render them as a single SVG block so print pagination cannot split the
    diagram into unrelated fragments.
    """
    if start >= len(lines):
        return None

    # A Markdown pipe table can also satisfy the loose ASCII-diagram heuristic
    # (rows contain multiple pipes and the separator contains dashes). Give the
    # native table parser precedence so financial/benchmark tables never turn
    # into flow diagrams simply because they follow a heading without a blank.
    if start + 1 < len(lines):
        first = lines[start].strip()
        second = lines[start + 1].strip()
        markdown_separator = re.match(
            r"^\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?$", second
        )
        if first.startswith("|") and first.endswith("|") and markdown_separator:
            return None

    fenced = bool(re.fullmatch(r"```(?:text|plaintext|diagram|flowchart)?", lines[start].strip(), flags=re.IGNORECASE))
    if fenced:
        closing = next((j for j in range(start + 1, len(lines)) if re.fullmatch(r"```\s*", lines[j].strip())), None)
        if closing is None:
            return None
        candidate_lines = lines[start + 1:closing]
        end = closing + 1
    else:
        candidate_lines = []
        end = start
        while end < len(lines) and end - start < 60:
            st = lines[end].strip()
            if not st:
                break
            has_shape = any(ch in st for ch in ("+---", "┌", "┐", "└", "┘", "│", "─", "║", "═"))
            has_flow = any(ch in st for ch in ("→", "←", "↑", "↓", "▲", "▼", "->", "<-"))
            repeated_pipes = st.count("|") >= 2 and ("-" in st or "_" in st or "+" in st)
            if has_shape or has_flow or repeated_pipes:
                candidate_lines.append(lines[end])
                end += 1
                continue
            # Allow ordinary label lines only after a diagram has clearly begun.
            if candidate_lines and len(st.split()) <= 12:
                candidate_lines.append(lines[end])
                end += 1
                continue
            break
        if not candidate_lines:
            return None

    shape_count = sum(any(ch in line for ch in ("+---", "┌", "┐", "└", "┘", "│", "─", "║", "═")) for line in candidate_lines)
    flow_count = sum(any(ch in line for ch in ("→", "←", "↑", "↓", "▲", "▼", "->", "<-")) for line in candidate_lines)
    repeated_pipe_count = sum(line.count("|") >= 2 for line in candidate_lines)
    if shape_count == 0 and flow_count == 0 and repeated_pipe_count == 0:
        return None

    labels = _diagram_label_lines(candidate_lines)
    if len(labels) < 2:
        return None
    rendered = _render_ascii_diagram_svg(labels)
    # Preserve the legacy class name for existing integrations/tests while
    # adding the new atomic-diagram class used by print/layout rules.
    rendered = rendered.replace('class="gg-diagram-wrap"', 'class="gg-diagram-wrap gg-ascii-diagram"', 1)
    return rendered, end


def _markdown_to_html(
    md: str,
    charts: list,
    images: list,
    theme: dict | None = None,
    source_registry: list[dict] | None = None,
    force_source_notes: bool = False,
    evidence_asset_html: dict[str, str] | None = None,
) -> str:
    """Small safe Markdown renderer with native tables/charts/images."""
    md = normalize_report_markdown(md)
    lines = md.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    list_mode: str | None = None
    para_buf: list[str] = []
    reveal_counter = 0
    last_source_ids: list[str] = []
    inline_chart_index = len(charts)

    def remember_source_ids(value: object):
        nonlocal last_source_ids
        found = [sid for sid in _source_ids(value) if sid in _source_registry_map(source_registry)]
        if found:
            last_source_ids = found[-5:]

    def flush_para():
        nonlocal para_buf, reveal_counter
        if para_buf:
            text = " ".join(para_buf).strip()
            if text:
                for paragraph in _split_long_paragraph(text):
                    reveal_counter += 1
                    out.append(f'<p class="gg-reveal" data-reveal style="--d:{(reveal_counter % 4) * 60}ms">{_inline_md(paragraph, source_registry)}</p>')
                remember_source_ids(text)
            para_buf = []

    def close_list():
        nonlocal list_mode
        if list_mode:
            out.append(f"</{list_mode}>")
            list_mode = None

    def is_table_start(i: int) -> bool:
        if i + 1 >= len(lines):
            return False
        a = lines[i].strip(); b = lines[i + 1].strip()
        return a.startswith("|") and a.endswith("|") and re.match(r"^\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?$", b) is not None

    def local_source_lines(start: int) -> tuple[str, str, int]:
        source_text = ""
        method_text = ""
        cursor = start
        consumed = False
        while cursor < len(lines):
            candidate = lines[cursor].strip()
            if not candidate and not consumed:
                cursor += 1
                continue
            source_match = re.match(r"^(?:source|sources|data source)\s*:\s*(.*)$", candidate, flags=re.IGNORECASE)
            method_match = re.match(r"^(?:method|methodology)\s*:\s*(.*)$", candidate, flags=re.IGNORECASE)
            if source_match:
                source_text = " · ".join(part for part in (source_text, source_match.group(1).strip()) if part)
                consumed = True
            elif method_match:
                method_text = " · ".join(part for part in (method_text, method_match.group(1).strip()) if part)
                consumed = True
            else:
                break
            cursor += 1
        return source_text, method_text, cursor

    def ascii_box_table_at(start: int):
        """Parse a fenced +---+ ASCII table emitted by some report models."""
        if not re.fullmatch(r"```(?:text|plaintext)?", lines[start].strip(), flags=re.IGNORECASE):
            return None
        closing = next((j for j in range(start + 1, len(lines)) if re.fullmatch(r"```\s*", lines[j].strip())), None)
        if closing is None:
            return None
        block = lines[start + 1:closing]
        border_re = re.compile(r"^\s*\+(?:[-=]+\+)+\s*$")
        if not block or not border_re.fullmatch(block[0]):
            return None
        rows = []
        for candidate in block:
            stripped_row = candidate.strip()
            if border_re.fullmatch(candidate):
                continue
            if stripped_row.startswith("|") and stripped_row.endswith("|"):
                cells = [cell.strip() for cell in stripped_row[1:-1].split("|")]
                if cells:
                    if all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
                        continue
                    rows.append(cells)
        if rows and len(rows[0]) == 1:
            table_title = rows.pop(0)[0]
        else:
            table_title = ""
        if len(rows) < 2 or len(rows[0]) < 2:
            return None
        headers = rows[0]
        values = [(row + [""] * len(headers))[:len(headers)] for row in rows[1:]]
        if not values:
            return None
        return table_title, headers, values, closing

    def render_chart(chart: dict, chart_idx: int, source_override: str = "", method_override: str = ""):
        if source_override or method_override:
            source_override = " · ".join(part for part in (source_override, f"Method: {method_override}" if method_override else "") if part)
        rendered = _render_chart_block(
            chart, chart_idx, theme, source_registry,
            source_override if source_override else None,
            last_source_ids, force_source_notes,
        )
        if rendered:
            out.append(rendered)
        remember_source_ids(source_override or chart.get("source") or chart.get("sources"))

    i = 0
    while i < len(lines):
        raw_line = lines[i]
        line = raw_line.rstrip()
        stripped = line.strip()

        asset_m = re.fullmatch(r"\[ASSET:([A-Za-z0-9_-]{1,100})\]", stripped, flags=re.I)
        if asset_m:
            flush_para(); close_list()
            asset_id = asset_m.group(1)
            # Only explicitly selected, provenance-backed assets are rendered.
            # Unknown/rejected markers disappear rather than leaking internal
            # [ASSET:...] tokens into the final HTML/PDF.
            asset_html = (evidence_asset_html or {}).get(asset_id, "")
            if asset_html:
                out.append(asset_html)
            i += 1
            continue

        chart_m = _CHART_RE.match(stripped)
        webimg_m = _WEBIMG_RE.match(stripped)
        if chart_m:
            flush_para(); close_list(); n = int(chart_m.group(1))
            source_text, method_text, next_i = local_source_lines(i + 1)
            if 1 <= n <= len(charts): render_chart(charts[n - 1], n, source_text, method_text)
            i = max(i + 1, next_i); continue
        if webimg_m:
            flush_para(); close_list(); n = int(webimg_m.group(1))
            source_text, method_text, next_i = local_source_lines(i + 1)
            if 1 <= n <= len(images):
                image = dict(images[n - 1]) if isinstance(images[n - 1], dict) else {}
                if source_text:
                    image["source"] = " · ".join(part for part in (str(image.get("source") or ""), source_text) if part)
                if method_text:
                    image["method"] = method_text
                out.append(_render_image_block(image, n, source_registry, force_source_notes))
            i = max(i + 1, next_i); continue

        # A model may put a chart object in a fenced code block instead of
        # using the report's chart array/placeholder. Convert supported specs
        # to the same native renderer and suppress invalid chart JSON.
        if re.fullmatch(r"```(?:json|javascript|js)", stripped, flags=re.IGNORECASE):
            closing = next((j for j in range(i + 1, len(lines)) if re.fullmatch(r"```\s*", lines[j].strip())), None)
            if closing is not None:
                code_content = "\n".join(lines[i + 1:closing]).strip()
                inline_chart, chart_like = _chart_spec_from_json(code_content)
                if chart_like:
                    flush_para(); close_list()
                    source_text, method_text, after_chart = local_source_lines(closing + 1)
                    if inline_chart:
                        inline_chart_index += 1
                        render_chart(inline_chart, inline_chart_index, source_text, method_text)
                    i = max(closing + 1, after_chart)
                    continue
            # Non-chart fences remain ordinary report text, as before; only
            # chart-spec JSON is intercepted by this renderer.
            i += 1
            continue

        if stripped.startswith("{"):
            json_block = _json_block_at(lines, i)
            if json_block:
                raw_json, next_i = json_block
                inline_chart, chart_like = _chart_spec_from_json(raw_json)
                if chart_like:
                    flush_para(); close_list()
                    source_text, method_text, after_chart = local_source_lines(next_i)
                    if inline_chart:
                        inline_chart_index += 1
                        render_chart(inline_chart, inline_chart_index, source_text, method_text)
                    i = max(next_i, after_chart)
                    continue

        ascii_table = ascii_box_table_at(i)
        if ascii_table:
            flush_para(); close_list()
            table_title, heads, values, closing = ascii_table
            table_source, method_text, next_i = local_source_lines(closing + 1)
            table_text = " ".join([*heads, *(cell for row in values for cell in row)])
            table_ids = [sid for sid in _source_ids(table_text) if sid in _source_registry_map(source_registry)]
            source_value = table_source or (table_ids if table_ids else None)
            rendered_table = _render_table_markup(
                heads, values, source_registry, source_value,
                table_ids or last_source_ids, force_source_notes,
                title=table_title, method=method_text,
            )
            if rendered_table:
                out.append(rendered_table)
            remember_source_ids(table_text + " " + table_source)
            i = max(closing + 1, next_i)
            continue

        # Check data tables before generic ASCII diagrams. Both often use
        # bordered text, but pipe-delimited rows carry a clear tabular schema
        # and must reach the editable table renderer instead of being flattened
        # into a diagram or discarded.
        ascii_art = _ascii_art_block_at(lines, i)
        if ascii_art:
            flush_para(); close_list()
            art_html, next_i = ascii_art
            out.append(art_html)
            i = next_i
            continue

        if is_table_start(i):
            flush_para(); close_list()
            rows = []
            # Header + separator + following pipe rows.
            rows.append(lines[i].strip())
            i += 2
            while i < len(lines):
                candidate = lines[i].strip()
                if not (candidate.startswith("|") and candidate.endswith("|")):
                    break
                rows.append(candidate); i += 1
            def cells(row: str):
                return [c.strip() for c in row.strip("|").split("|")]
            heads = cells(rows[0])
            values = [cells(row) for row in rows[1:]]
            table_source, method_text, next_i = local_source_lines(i)
            i = max(i, next_i)
            table_text = " ".join([*heads, *(cell for row in values for cell in row)])
            table_ids = [sid for sid in _source_ids(table_text) if sid in _source_registry_map(source_registry)]
            source_value = table_source or (table_ids if table_ids else None)
            rendered_table = _render_table_markup(
                heads, values, source_registry, source_value,
                table_ids or last_source_ids, force_source_notes, method=method_text,
            )
            if rendered_table:
                out.append(rendered_table)
            remember_source_ids(table_text + " " + table_source)
            continue

        if not stripped:
            flush_para(); close_list(); i += 1; continue
        # Provider responses occasionally leave Markdown fence delimiters
        # around an inline figure or continuation block. They are syntax, not
        # report content, so never print them as visible prose in HTML/PDF.
        if re.fullmatch(r"```[A-Za-z0-9_-]*", stripped):
            flush_para(); close_list(); i += 1; continue
        # Treat deeper Markdown headings as a real subsection instead of
        # emitting the extra hash characters as body text.
        deep_heading = re.match(r"^#{3,}\s+(.+)$", stripped)
        if deep_heading:
            flush_para(); close_list(); out.append(f'<h4 class="gg-reveal gg-subsection-h4" data-reveal>{_inline_md(deep_heading.group(1), source_registry)}</h4>'); i += 1; continue
        if stripped.startswith("### "):
            flush_para(); close_list(); out.append(f'<h4 class="gg-reveal gg-subsection-h4" data-reveal>{_inline_md(stripped[4:], source_registry)}</h4>'); i += 1; continue
        if stripped.startswith("## "):
            # The report wrapper already owns the H2 hierarchy. Model-emitted
            # H2s inside a section are therefore rendered as H3 subsections.
            flush_para(); close_list(); out.append(f'<h3 class="gg-reveal gg-section-h3" data-reveal>{_inline_md(stripped[3:], source_registry)}</h3>'); i += 1; continue
        if stripped.startswith("# "):
            flush_para(); close_list(); out.append(f'<h3 class="gg-reveal gg-section-h3" data-reveal>{_inline_md(stripped[2:], source_registry)}</h3>'); i += 1; continue
        if stripped.startswith("> "):
            flush_para(); close_list()
            _status_label = _status_banner_label(stripped[2:])
            if _status_label:
                # Draft / warning notices are publication-status messages, not
                # pull quotes: render them as a labelled banner (text unchanged).
                out.append(f'<div class="gg-status-banner gg-status-banner--{_status_label[0]}" role="note" data-status-label="{html.escape(_status_label[1], quote=True)}">{_inline_md(stripped[2:], source_registry)}</div>')
            else:
                out.append(f'<blockquote class="gg-reveal gg-pullquote" data-reveal>{_inline_md(stripped[2:], source_registry)}</blockquote>')
            i += 1; continue
        if stripped in ("---", "***", "___"):
            flush_para(); close_list(); out.append('<hr class="gg-divider" />'); i += 1; continue

        bullet_m = re.match(r"^[-•*]\s+(.*)$", stripped)
        numbered_m = re.match(r"^\d+[.)]\s+(.*)$", stripped)
        if bullet_m:
            flush_para()
            if list_mode != "ul": close_list(); out.append('<ul class="gg-reveal gg-list" data-reveal>'); list_mode = "ul"
            out.append(f"<li>{_inline_md(bullet_m.group(1), source_registry)}</li>"); i += 1; continue
        if numbered_m:
            flush_para()
            if list_mode != "ol": close_list(); out.append('<ol class="gg-reveal gg-list" data-reveal>'); list_mode = "ol"
            out.append(f"<li>{_inline_md(numbered_m.group(1), source_registry)}</li>"); i += 1; continue

        close_list(); para_buf.append(stripped); i += 1

    flush_para(); close_list()
    return "\n".join(out)


# ─────────────────────────────────────────────────────────────────────────
# Key stats — animated count-up cards
# ─────────────────────────────────────────────────────────────────────────

_NUMERIC_RE = re.compile(r"-?[\d,]+\.?\d*")


def _render_key_stats(
    key_stats: list,
    source_registry: list[dict] | None = None,
    limit: int = 16,
) -> str:
    if not key_stats:
        return ""
    cards = []
    seen_labels = set()
    # Use a restrained six-colour palette so KPI cards are visually scannable
    # in browser and print, while keeping the same underlying report theme.
    # Inline custom properties survive the HTML-to-PDF preparation step.
    kpi_palette = [
        ("#EAF2FF", "#2F62B4", "#17385F"),  # blue
        ("#E6F6EF", "#22815A", "#174C38"),  # green
        ("#FFF3D9", "#A56B12", "#68450D"),  # amber
        ("#F1EAFF", "#7654B8", "#432D73"),  # violet
        ("#E4F6F8", "#167C89", "#14505A"),  # teal
        ("#FCEBE8", "#B94E43", "#783027"),  # coral
    ]
    # A global KPI list can contain the same metric more than once after a
    # planner/writer/continuation pass. Keep the first evidence-backed version
    # and never render duplicate KPI cards. This is presentation-only: the
    # underlying report text and source registry remain untouched.
    for i, stat in enumerate(key_stats[:max(1, min(int(limit or 16), 16))]):
        if not isinstance(stat, dict) or not str(stat.get("label") or "").strip() or not str(stat.get("value") or "").strip():
            continue
        label_key = re.sub(r"[^a-z0-9]+", " ", str(stat.get("label") or "").casefold()).strip()
        if label_key in seen_labels:
            continue
        seen_labels.add(label_key)
        label = _inline_md(str(stat.get("label", "")), source_registry)
        value = str(stat.get("value", ""))
        change = str(stat.get("change", "") or "")
        # The writer sometimes copies the displayed metric into its change
        # field (e.g. value="15.9%", change="+15.9%"). Suppress only exact
        # normalized duplicates; genuine deltas such as a CAGR stay visible.
        def _metric_fingerprint(raw: str) -> str:
            normalized = re.sub(r"\s+", "", str(raw or "")).casefold().replace(",", "")
            return normalized.lstrip("+−-")
        if change.strip() and _metric_fingerprint(change) == _metric_fingerprint(value):
            change = ""
        m = _NUMERIC_RE.search(value)
        if m:
            numeric_str = m.group(0).replace(",", "")
            prefix = html.escape(value[: m.start()])
            suffix = html.escape(value[m.end():])
            try:
                target = float(numeric_str)
                is_float = "." in numeric_str
                # Keep the source's own precision ("2.2%" must not animate to "2.20%").
                decimals = len(numeric_str.split(".", 1)[1]) if is_float else 0
                data_attrs = f'data-count-target="{target}" data-count-decimals="{min(decimals, 4)}"'
                # Keep the measured value visible without JavaScript; the browser
                # may animate from zero, while print can consume the same value.
                # v93: isolate the figure from its currency prefix and unit so
                # "₹2,41,47,183 crore" reads as a large number with a quiet unit.
                # "%" stays attached to the figure; the counter span is unchanged.
                raw_suffix = value[m.end():]
                pct = "%" if raw_suffix.lstrip().startswith("%") else ""
                unit_text = raw_suffix.lstrip()[1:] if pct else raw_suffix
                prefix_html = f'<span class="gg-stat-prefix">{prefix.strip()}</span>' if prefix.strip() else prefix
                unit_html = f'<span class="gg-stat-unit">{html.escape(unit_text.strip())}</span>' if unit_text.strip() else html.escape(unit_text)
                value_html = (f'{prefix_html}<span class="gg-stat-number"><span class="gg-count" {data_attrs}>{html.escape(m.group(0))}</span>{pct}</span>'
                              f'{unit_html}')
            except ValueError:
                value_html = html.escape(value)
        else:
            value_html = html.escape(value)
        value_html = _link_source_citations(value_html, source_registry)
        raw_source_ids = stat.get("sourceIds") or stat.get("source_ids") or stat.get("sourceId") or stat.get("sources") or stat.get("source") or []
        stat_source_ids = [sid for sid in _source_ids(raw_source_ids) if sid in _source_registry_map(source_registry)]
        source_markup = _link_source_citations(", ".join(f"[{sid}]" for sid in stat_source_ids), source_registry) if stat_source_ids else ""

        change_cls = ""
        if change.strip().startswith("-"):
            change_cls = "gg-change-down"
        elif change.strip().startswith("+"):
            change_cls = "gg-change-up"

        kpi_bg, kpi_accent, kpi_ink = kpi_palette[len(cards) % len(kpi_palette)]
        cards.append(f"""
<div class="gg-stat-card gg-reveal" data-reveal style="--d:{(i % 6) * 70}ms; --kpi-tint:{kpi_bg}; --kpi-accent:{kpi_accent}; --kpi-ink:{kpi_ink}; --accent:{kpi_accent}; --ink:{kpi_ink}; background-color:{kpi_bg}; border-color:{kpi_accent};">
  <div class="gg-stat-value">{value_html}</div>
  <div class="gg-stat-label">{label}</div>
  {f'<div class="gg-stat-change {change_cls}">{_inline_md(change, source_registry)}</div>' if change.strip() else ''}
  {f'<div class="gg-stat-source">{source_markup}</div>' if source_markup else ''}
</div>""")
    return f'<div class="gg-stats-grid gg-stats-count-{len(cards)}">{"".join(cards)}</div>' if cards else ""


def _brief_key_stats(key_stats: list) -> list:
    """Keep Brief KPI cards decision-useful: numeric metrics first, no prose disguised as KPI."""
    valid = [item for item in (key_stats or []) if isinstance(item, dict) and str(item.get("value") or "").strip()]
    numeric = []
    other = []
    for item in valid:
        value = str(item.get("value") or "")
        if re.search(r"(?:[$€£₹]|\d)", value):
            numeric.append(item)
        else:
            other.append(item)
    # Brief mode is an executive surface: three cards form a stable visual
    # unit. Four cards create an orphan/2+2 dashboard and compete with the
    # narrative. Prefer numeric signals first, then other useful KPIs.
    selected = (numeric + other)[:3]
    return selected


def _verified_metric_trend(key_stats: list, report: str, registry: list[dict]) -> dict | None:
    """Recover one native visual only for same-unit, sourced, dated KPI series.

    Never compare incompatible market estimates or infer a missing year/value.
    """
    groups = {}
    for stat in key_stats or []:
        if not isinstance(stat, dict):
            continue
        label, value = str(stat.get("label") or ""), str(stat.get("value") or "")
        year = re.search(r"\b(?:19|20)\d{2}\b", label)
        number = re.search(r"\$?\s*([\d,]+(?:\.\d+)?)\s*(billion|million|trillion|[bmk])\b", value, re.I)
        if not (year and number):
            continue
        metric = re.sub(r"\b(?:19|20)\d{2}\b|\b(projected|estimated|forecast|actual)\b", "", label, flags=re.I)
        metric = re.sub(r"[^a-z]+", " ", metric.lower()).strip()
        if not metric or len(metric) < 5:
            continue
        unit = number.group(2).lower()
        unit = {"b":"billion", "m":"million", "k":"thousand"}.get(unit, unit)
        if unit not in ("billion", "million", "trillion"):
            continue
        # A source must explicitly accompany the KPI, or the exact KPI value
        # must occur near a report citation. An appendix entry alone is not proof.
        source = stat.get("source") or stat.get("sources") or ""
        ids = _source_ids(source)
        if isinstance(source, str) and re.fullmatch(r"S\d+", source.strip(), re.I):
            ids = [source.strip().upper()]
        if not ids:
            escaped = re.escape(number.group(1)).replace(r"\,", "[,]?")
            near = re.search(escaped + r".{0,180}?\[S(\d+)\]", report, re.I | re.S)
            if near:
                ids = ["S" + near.group(1)]
        valid = {str(item.get("id")) for item in registry}
        ids = [sid for sid in ids if sid in valid]
        if not ids:
            continue
        groups.setdefault((metric, unit), []).append((int(year.group()), float(number.group(1).replace(",", "")), ids))
    for (metric, unit), items in groups.items():
        unique = {yr: (val, ids) for yr, val, ids in items}
        if len(unique) < 2:
            continue
        years = sorted(unique)
        citations = list(dict.fromkeys(sid for yr in years for sid in unique[yr][1]))
        if len(citations) > 4:
            continue
        return {"type":"bar", "title":metric.title() + " — sourced trend", "yLabel":unit.title(),
                "series":[{"name":metric.title(), "data":[{"label":str(yr), "value":unique[yr][0]} for yr in years]}],
                "source":citations}
    return None


# ─────────────────────────────────────────────────────────────────────────
# Full document
# ─────────────────────────────────────────────────────────────────────────


def _verified_report_trend(report: str, registry: list[dict]) -> dict | None:
    """Recover one chart from a source-cited year/value comparison in prose.

    This is deliberately conservative: it only converts two or more monetary
    values when each value has a nearby year and the same paragraph contains a
    valid [S#] citation. No interpolation, CAGR reconstruction, or unsupported
    values are created.
    """
    valid_ids = {str(item.get("id") or "").upper() for item in registry if isinstance(item, dict)}
    if not valid_ids:
        return None
    paragraphs = re.split(r"\n\s*\n", str(report or ""))
    money_re = re.compile(r"\$\s*([\d,]+(?:\.\d+)?)\s*(billion|bn|b|million|mn|m|trillion|tn|t)\b", re.I)
    year_re = re.compile(r"\b(?:19|20)\d{2}\b")
    for paragraph in paragraphs:
        if len(paragraph) < 40:
            continue
        values = []
        for match in money_re.finditer(paragraph):
            value = float(match.group(1).replace(",", ""))
            unit = match.group(2).lower()
            multiplier = {"billion":1,"bn":1,"b":1,"million":0.001,"mn":0.001,"m":0.001,"trillion":1000,"tn":1000,"t":1000}[unit]
            # Find the closest year within 70 characters before/after the value.
            nearby = []
            window_start = max(0, match.start() - 80)
            window_end = min(len(paragraph), match.end() + 80)
            for y in year_re.finditer(paragraph[window_start:window_end]):
                absolute = window_start + y.start()
                nearby.append((int(y.group(0)), absolute))
            if not nearby:
                continue
            year, _ = min(nearby, key=lambda item: abs(item[1] - match.end()))
            values.append((year, value * multiplier, match.group(0)))
        if len(values) < 2:
            continue
        # Prefer two distinct years and avoid mixing unrelated values.
        by_year = {}
        for year, value, raw in values:
            by_year.setdefault(year, (value, raw))
        if len(by_year) < 2:
            continue
        ordered = sorted(by_year.items())[:4]
        ids = [sid for sid in _source_ids(paragraph) if sid.upper() in valid_ids]
        if not ids:
            continue
        # The prose must describe an actual trajectory, not just two unrelated
        # dollar figures in a paragraph.
        lowered = paragraph.lower()
        if not re.search(r"\b(from|to|by|through|grow|expand|increase|rise|reach|project|forecast)\b", lowered):
            continue
        data = [{"label": str(year), "value": value} for year, (value, _) in ordered]
        if len(data) < 2:
            continue
        return {
            "type": "bar" if len(data) == 2 else "line",
            "title": "Market size trajectory",
            "unit": "$B",
            "x_label": "Year",
            "y_label": "Market size (US$B)",
            "series": [{"name": "Market size", "data": data}],
            "source": ids[:3],
            "rationale": "The report evidence provides source-cited market-size observations for distinct years.",
            "takeaway": f"The cited evidence moves from ${data[0]['value']:,.1f}B in {data[0]['label']} to ${data[-1]['value']:,.1f}B in {data[-1]['label']}.",
        }
    return None

def _clamp255(v: int) -> int:
    return max(0, min(255, v))


def _shade_hex(hex_color: str, amount: float) -> str:
    """Lighten (amount > 0) or darken (amount < 0) a #rrggbb hex color by a
    fraction of the remaining distance to white/black. Used to derive the
    'light'/'deep' variants of a user-requested theme color the same way
    the fixed BRAND_GOLD_LIGHT / BRAND_NAVY_DEEP variants were hand-picked
    for the default palette."""
    try:
        r = int(hex_color[1:3], 16)
        g = int(hex_color[3:5], 16)
        b = int(hex_color[5:7], 16)
    except (ValueError, IndexError):
        return hex_color
    if amount >= 0:
        r = _clamp255(int(r + (255 - r) * amount))
        g = _clamp255(int(g + (255 - g) * amount))
        b = _clamp255(int(b + (255 - b) * amount))
    else:
        r = _clamp255(int(r * (1 + amount)))
        g = _clamp255(int(g * (1 + amount)))
        b = _clamp255(int(b * (1 + amount)))
    return f"#{r:02x}{g:02x}{b:02x}"


def _google_font_link(font_family: str | None) -> str:
    """Build a <link> tag that loads a theme-requested Google Font, so
    theme.fontFamily (see report.py's THEME schema / _sanitize_theme) is
    actually available for the CSS to use instead of just naming a font the
    browser doesn't have. font_family is already validated upstream as
    alnum+space only (max 40 chars), so it's safe to drop straight into a
    URL path segment with spaces turned into '+'."""
    if not font_family:
        return ""
    family_param = font_family.strip().replace(" ", "+")
    href = f"https://fonts.googleapis.com/css2?family={family_param}:wght@400;600;700&display=swap"
    return f'<link rel="preconnect" href="https://fonts.googleapis.com">\n<link rel="stylesheet" href="{html.escape(href)}">'


def _build_css(theme: dict | None, report_tier: str = "detailed") -> str:
    """Build a neutral renderer stylesheet.

    Composition is driven by ``ReportPresentationSpec``; this stylesheet only
    supplies reusable primitives for the supported layouts.  It intentionally
    does not accept arbitrary CSS from the LLM.
    """
    theme = theme or {}
    in_depth = str(report_tier or "").lower() == "in_depth"
    navy = theme.get("primaryColor") or "#17384F"
    gold = theme.get("accentColor") or "#387C58"
    navy_deep = _shade_hex(navy, -0.22)
    gold_light = _shade_hex(gold, 0.28)
    # Derive secondary surfaces/rules from the report's own theme so the
    # renderer does not impose a single fixed palette.
    paper = theme.get("surfaceColor") or "#FFFFFF"
    paper_alt = theme.get("surfaceAltColor") or "#F2F6F3"
    ink = theme.get("textColor") or "#1D2B33"
    ink_soft = theme.get("mutedColor") or "#34464F"
    rule = theme.get("borderColor") or "#CCD8D1"
    font = theme.get("fontFamily")
    if in_depth:
        font_body = "Arial, 'Helvetica Neue', sans-serif"
        font_heading = "Georgia, 'Times New Roman', serif"
    elif font:
        font_body = f"'{font}', system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
        font_heading = f"'{font}', system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
    else:
        font_body = "system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
        font_heading = "system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
    return f"""
:root {{
  --accent: {gold}; --accent-soft: {gold_light}; --ink: {ink}; --ink-soft: {ink_soft};
  --paper: {paper}; --paper-alt: {paper_alt}; --rule: {rule}; --muted: {ink_soft};
  --negative: #b42318; --positive: #147a4b; --font-body: {font_body}; --font-heading: {font_heading};
  --primary: {navy}; --primary-dark: {navy_deep}; --secondary: #167C8A;
  --background: {paper}; --surface: #FFFFFF; --surface-alt: {paper_alt};
  --text: {ink}; --text-muted: {ink_soft}; --border: {rule};
  --success: #147a4b; --warning: #9a6700; --danger: #b42318;
  --type-display: clamp(2.3rem, 5vw, 4rem); --type-h1: clamp(1.8rem, 3vw, 2.6rem);
  --type-h2: clamp(1.45rem, 2.4vw, 2.1rem); --type-body: 1rem; --type-caption: .82rem;
  --content-max: 1180px;
}}
* {{ box-sizing: border-box; }}
html {{ scroll-behavior: smooth; background: var(--paper); }}
body {{ margin: 0; font-family: var(--font-body); color: var(--ink); background: var(--paper); line-height: 1.68; overflow-x: hidden; }}
a {{ color: var(--accent); }}
h1,h2,h3,h4 {{ font-family: var(--font-heading); color: var(--ink); line-height: 1.18; }}
p {{ font-size: 16px; color: var(--ink-soft); }}
.gg-section-body > p {{ width: 100%; max-width: none; text-align: justify; text-justify: inter-word; hyphens: auto; margin-left: auto; margin-right: auto; }}
main {{ width: min(var(--content-max), calc(100% - 40px)); margin: 0 auto; padding: 34px 0 90px; }}

/* Cover treatments — the renderer chooses one based on presentation.cover.treatment. */
.gg-cover {{ width: min(var(--content-max), calc(100% - 40px)); margin: 34px auto 0; border-bottom: 1px solid var(--rule); }}
.gg-title-band {{ margin: 24px 0 22px; padding: 18px 0 16px; border-bottom: 2px solid var(--accent); break-inside: avoid; break-after: avoid-page; }}
.gg-title-band .gg-eyebrow {{ margin-bottom: 8px; }}
.gg-title-band .gg-title {{ max-width: 880px; margin: 0 0 10px; font-size: 32px; line-height: 1.12; }}
.gg-title-meta {{ margin: 0; color: var(--muted); font-size: 11px; letter-spacing: .035em; }}
.gg-cover--minimal {{ padding: 20px 0 28px; }}
.gg-cover--classic {{ text-align: center; padding: 96px 8% 86px; background: var(--paper-alt); border: 1px solid var(--rule); }}
.gg-cover--classic .gg-title {{ max-width: 900px; margin-left: auto; margin-right: auto; }}
.gg-cover--bold_banner {{ padding: 54px 56px; background: linear-gradient(135deg, var(--ink), var(--ink-soft)); color: #fff; border: 0; }}
.gg-cover--bold_banner .gg-eyebrow, .gg-cover--bold_banner .gg-title, .gg-cover--bold_banner .gg-summary, .gg-cover--bold_banner .gg-date {{ color: #fff; }}
.gg-cover--data_driven {{ display: grid; grid-template-columns: minmax(0, 1.3fr) minmax(280px, .7fr); gap: 32px; align-items: end; padding: 48px 0; }}
.gg-cover__aside {{ padding: 20px; border-left: 3px solid var(--accent); background: var(--paper-alt); }}
.gg-cover__aside-label {{ margin: 0 0 8px; font: 700 11px var(--font-heading); letter-spacing: .08em; text-transform: uppercase; color: var(--accent); }}
.gg-eyebrow {{ margin: 0 0 12px; font: 700 11px var(--font-heading); letter-spacing: .14em; text-transform: uppercase; color: var(--accent); }}
.gg-title {{ margin: 0 0 16px; font-size: clamp(34px, 5vw, 62px); letter-spacing: -.025em; }}
.gg-summary {{ max-width: 72ch; margin: 0; font-size: 18px; }}
.gg-date {{ margin-top: 18px; color: var(--muted); font-size: 13px; }}

.gg-summary-wrap {{ margin: 0 0 32px; }}
.gg-summary-wrap--sidebar {{ display: grid; grid-template-columns: minmax(240px, .34fr) minmax(0, 1fr); gap: 28px; align-items: start; }}
.gg-summary-wrap--end {{ margin-top: 42px; }}
.gg-summary-card {{ padding: 26px 28px; border: 1px solid var(--rule); background: var(--paper-alt); }}
.gg-summary-card--high {{ border-left: 4px solid var(--accent); }}
.gg-summary-card--critical {{ border-left: 5px solid var(--negative); }}
.gg-summary-heading {{ margin: 0 0 14px; font-size: 24px; }}

.gg-report-sections {{ display: flex; flex-direction: column; gap: 26px; }}
.gg-composition--grid .gg-report-sections {{ gap: 18px; }}
.gg-composition--two_column .gg-report-sections {{ max-width: 1080px; }}
.gg-composition--sidebar_main .gg-report-sections {{ max-width: 1080px; }}

.gg-section {{ scroll-margin-top: 24px; break-inside: auto; page-break-inside: auto; }}
.gg-section--full_bleed {{ width: 100vw; margin-left: calc(50% - 50vw); padding: 42px max(24px, calc((100vw - var(--content-max)) / 2)); background: var(--paper-alt); }}
.gg-section--sidebar_main {{ display: grid; grid-template-columns: minmax(180px, .26fr) minmax(0, 1fr); gap: 30px; align-items: start; }}
.gg-section--sidebar_main .gg-section-heading {{ position: sticky; top: 20px; }}
.gg-section-heading {{ margin-bottom: 18px; break-inside: avoid; break-after: avoid-page; page-break-after: avoid; }}
.gg-section-heading h2 {{ margin: 0; font-size: clamp(24px, 3vw, 34px); break-after: avoid-page; page-break-after: avoid; }}
.gg-section-h3 {{ margin: 18px 0 7px; font-size: 16px; color: var(--ink); break-after: avoid-page; page-break-after: avoid; }}
.gg-subsection-h4 {{ margin: 14px 0 6px; font-size: 14px; color: var(--accent); break-after: avoid-page; page-break-after: avoid; }}
.gg-section-heading p {{ margin: 8px 0 0; font-size: 13px; color: var(--muted); }}
.gg-section--high .gg-section-heading {{ border-top: 3px solid var(--accent); padding-top: 14px; }}
.gg-section--critical .gg-section-heading {{ border-top: 4px solid var(--negative); padding-top: 14px; }}
.gg-section-body--two_column {{ columns: 1; column-gap: 0; width: 100%; }}
.gg-section-body--grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 20px; }}
.gg-section-body--sidebar_main {{ min-width: 0; }}
.gg-section[data-density="sparse"] {{ padding-top: 14px; padding-bottom: 14px; }}
.gg-section[data-density="dense"] {{ font-size: 15px; }}
.gg-section[data-density="dense"] p {{ font-size: 15px; }}
.gg-section[data-density="dense"] .gg-block {{ margin-bottom: 14px; }}
.gg-composition--hybrid .gg-section + .gg-section {{ border-top: 1px solid var(--rule); padding-top: 24px; }}
.gg-section[data-emphasis="critical"] {{ background: #fff5f4; padding: 16px; border-radius: 10px; }}
.gg-section[data-section-type="comparison"] .gg-table-wrap, .gg-section[data-section-type="financials"] .gg-table-wrap {{ overflow: auto; }}
.gg-section[data-section-type="timeline"] .gg-section-body, .gg-section[data-section-type="findings"] .gg-section-body {{ max-width: 82ch; }}

.gg-block {{ min-width: 0; }}
.gg-block + .gg-block {{ margin-top: 10px; }}
.gg-block--emphasis-high {{ border-left: 3px solid var(--accent); padding-left: 18px; }}
.gg-block--emphasis-critical {{ border-left: 4px solid var(--negative); padding-left: 18px; }}
.gg-list {{ font-size: 16px; color: var(--ink-soft); padding-left: 24px; }}
.gg-list li {{ margin: 6px 0; }}
.gg-pullquote {{ border-left: 3px solid var(--accent); margin: 26px 0; padding: 6px 0 6px 20px; font-style: italic; font-size: 19px; color: var(--ink); }}
.gg-divider {{ border: 0; border-top: 1px solid var(--rule); margin: 36px 0; }}

.gg-chart-wrap, .gg-table-wrap {{ margin: 20px 0; padding: 16px 18px; background: var(--paper-alt); border: 1px solid var(--rule); break-inside: avoid; page-break-inside: avoid; }}
.gg-chart-title {{ margin-bottom: 12px; font: 700 12px var(--font-heading); letter-spacing: .06em; text-transform: uppercase; color: var(--accent); }}
 .gg-chart-canvas-box {{ position: relative; height: 320px; }}
.gg-chart-inline-svg svg {{ width: 100%; height: 100%; display: block; }}
.gg-chart-wrap--arrow .gg-chart-canvas-box {{ height: 300px; }}
.gg-table-scroll {{ overflow-x: auto; }}
.gg-table {{ width: 100%; border-collapse: collapse; font-family: var(--font-heading); font-size: 14px; }}
.gg-table th {{ text-align: left; padding: 10px 12px; color: var(--ink); border-bottom: 2px solid var(--accent); }}
.gg-table td {{ padding: 10px 12px; border-bottom: 1px solid var(--rule); color: var(--ink-soft); vertical-align: top; }}
.gg-source-note {{ margin-top: 8px; color: var(--ink-soft); font-size: 11px; line-height: 1.35; text-align: left; overflow-wrap: anywhere; }}
.gg-source-note-label {{ color: var(--ink); font-weight: 700; }}
.gg-source-note-separator {{ color: var(--muted); padding: 0 2px; }}
.gg-source-method {{ color: var(--ink-soft); }}
.gg-source-unavailable {{ color: var(--muted); font-style: italic; }}
.gg-source-cite {{ color: var(--accent); font-weight: 700; text-decoration: none; white-space: nowrap; }}
.gg-source-cite:hover {{ text-decoration: underline; }}

.gg-metrics {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin: 18px 0; }}
.gg-metric {{ padding: 16px 17px; border: 1px solid var(--rule); background: var(--paper); }}
.gg-metric-value {{ font: 700 24px var(--font-heading); color: var(--ink); }}
.gg-metric-label {{ margin-top: 5px; font: 600 11px var(--font-heading); color: var(--muted); text-transform: uppercase; letter-spacing: .05em; }}
.gg-metric-change {{ margin-top: 5px; font-size: 12px; font-weight: 700; }}

/* Legacy/stat-dashboard primitive used by metrics_dashboard sections. */
.gg-stats-grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin: 18px 0; }}
.gg-stat-card {{ min-width: 0; padding: 14px 15px; border: 1px solid var(--rule); background: var(--paper); break-inside: avoid; }}
.gg-stat-value {{ font: 700 22px var(--font-heading); color: var(--ink); line-height: 1.12; overflow-wrap: anywhere; }}
.gg-stat-label {{ margin-top: 5px; font: 600 10px var(--font-heading); color: var(--muted); text-transform: uppercase; letter-spacing: .045em; line-height: 1.2; }}
.gg-stat-change {{ margin-top: 4px; font-size: 11px; font-weight: 700; }} .gg-stat-source {{ margin-top: 4px; font-size: 10px; line-height: 1.2; }}
.gg-change-up {{ color: var(--positive); }} .gg-change-down {{ color: var(--negative); }}

.gg-timeline {{ position: relative; display: grid; gap: 14px; margin: 18px 0; }}
.gg-timeline-item {{ display: grid; grid-template-columns: 120px minmax(0, 1fr); gap: 16px; padding: 14px 0; border-top: 1px solid var(--rule); }}
.gg-timeline-date {{ font: 700 12px var(--font-heading); color: var(--accent); }}
.gg-timeline-title {{ margin: 0; font: 700 15px var(--font-heading); }}
.gg-timeline-desc {{ margin: 5px 0 0; color: var(--ink-soft); font-size: 14px; }}

.gg-callout {{ margin: 20px 0; padding: 18px 20px; border: 1px solid var(--rule); border-left: 4px solid var(--accent); background: var(--paper-alt); break-inside: avoid; }}
.gg-callout--warning, .gg-callout--negative {{ border-left-color: var(--negative); }}
.gg-callout--positive {{ border-left-color: var(--positive); }}
.gg-callout h4 {{ margin: 0 0 6px; font-size: 14px; }}
.gg-callout p {{ margin: 0; }}

.gg-risk-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; margin: 18px 0; }}
.gg-risk {{ padding: 14px; border: 1px solid var(--rule); background: var(--paper-alt); }}
.gg-risk__name {{ font-weight: 700; }}
.gg-risk__meta {{ margin-top: 5px; color: var(--muted); font-size: 12px; }}
.gg-risk__mitigation {{ margin-top: 8px; font-size: 13px; color: var(--ink-soft); }}

.gg-unplaced-chart {{ display:block; width:100%; margin:18px 0; break-inside:avoid; page-break-inside:avoid; }}
.gg-figure {{ margin: 24px 0; text-align: center; break-inside: avoid; }}
.gg-figure img {{ max-width: 100%; height: auto; border: 1px solid var(--rule); }}
.gg-diagram-wrap {{ margin: 20px 0; padding: 14px 16px; background: var(--paper-alt); border: 1px solid var(--rule); border-left: 4px solid var(--accent); border-radius: 10px; break-inside: avoid !important; page-break-inside: avoid !important; display: block; overflow: visible; }}
.gg-inline-diagram {{ display: block; width: 100%; height: auto; max-width: 100%; }}
.gg-figure figcaption {{ margin-top: 8px; font-size: 13px; color: var(--muted); font-style: italic; }}

.gg-reveal {{ opacity: 0; transform: translateY(12px); transition: opacity .5s ease, transform .5s ease; transition-delay: var(--d, 0ms); }}
.gg-reveal.gg-visible {{ opacity: 1; transform: translateY(0); }}
.gg-print-page-break {{ display:none; }}
.gg-sources {{ margin-top: 36px; padding: 22px; border: 1px solid var(--rule); background: var(--paper-alt); }}
.gg-sources {{ break-before: auto; page-break-before: auto; break-inside: auto; page-break-inside: auto; }}
.gg-source-card {{ break-inside: avoid; page-break-inside: avoid; }}
.gg-source-card--url-only {{ display: block !important; width: 100%; padding: 8px 0 !important; border: 0 !important; background: transparent !important; box-shadow: none !important; }}
.gg-source-url-only {{ display: block; width: 100%; color: var(--accent); font-size: 10.5pt; line-height: 1.4; overflow-wrap: anywhere; word-break: normal; text-decoration: underline; }}
.gg-source-identity {{ margin: 0 0 4px; font-size: 10pt; line-height: 1.35; }}
.gg-source-id {{ display: inline-block; margin-right: 5px; color: var(--accent); font-weight: 800; white-space: nowrap; }}
.gg-source-title-link, .gg-source-title-text {{ color: var(--ink); font-weight: 750; text-decoration: none; }}
.gg-source-publisher {{ margin-left: 5px; color: var(--ink-soft); font-weight: 500; }}

.gg-sources h2 {{ margin-top: 0; }}
.gg-sources-intro {{ color: var(--muted); font-size: 13px; }}
.gg-sources-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }}
.gg-source-card {{ display: flex; align-items: flex-start; gap: 10px; padding: 12px; border: 1px solid var(--rule); background: var(--paper); break-inside: avoid; }}
.gg-source-number {{ box-sizing: border-box; flex: 0 0 44px; width: 44px; min-width: 44px; padding: 0 3px; height: 24px; white-space: nowrap; display: grid; place-items: center; border: 1px solid var(--accent); border-radius: 50%; color: var(--accent); font: 700 11px var(--font-heading); }}
.gg-source-number {{ text-decoration: none; }}
.gg-source-title {{ color: var(--ink); font: 600 13px var(--font-heading); line-height: 1.35; overflow-wrap: anywhere; }}
.gg-source-meta {{ color: var(--muted); font-size: 11px; margin-top: 4px; }}
.gg-source-link {{ color: var(--accent); font-size: 12px; text-decoration: none; overflow-wrap: anywhere; }}
.gg-source-link:hover {{ text-decoration: underline; }}
.gg-source-content {{ flex: 1 1 0; min-width: 0; width: auto; }}
.gg-source-title {{ overflow-wrap: break-word; word-break: normal; }}
.gg-source-meta {{ margin-bottom: 3px; overflow-wrap: break-word; word-break: normal; }}
.gg-source-link {{ display: block; max-width: 100%; overflow-wrap: anywhere; word-break: normal; }}
.gg-footer {{ text-align: center; padding: 34px 24px 48px; color: var(--muted); border-top: 1px solid var(--rule); font-size: 11px; }}

/* In-Depth editions use a restrained institutional palette and a clearer
   typographic hierarchy; the dynamic section order and layouts remain model-led. */
body.gg-tier--in_depth {{ line-height: 1.55; }}
body.gg-tier--in_depth p, body.gg-tier--in_depth li, body.gg-tier--in_depth td {{ color: var(--ink-soft); }}
body.gg-tier--in_depth .gg-section-body > p {{ text-align: left; font-size: 14px; line-height: 1.52; }}
body.gg-tier--in_depth .gg-section-heading h2 {{ font-family: var(--font-heading); color: #17384F; }}
body.gg-tier--in_depth .gg-section-h3 {{ color: #17384F; }}
body.gg-tier--in_depth .gg-subsection-h4 {{ color: #2F7354; }}
body.gg-tier--in_depth .gg-chart-wrap, body.gg-tier--in_depth .gg-table-wrap {{ background: #F5F8F6; border-color: #CCD8D1; border-radius: 3px; }}
body.gg-tier--in_depth .gg-chart-title {{ color: #245E48; letter-spacing: .04em; }}
body.gg-tier--in_depth .gg-table {{ table-layout: fixed; font-size: 11px; }}
body.gg-tier--in_depth .gg-table th {{ background: #17384F; color: #FFFFFF; border-color: #17384F; padding: 8px 9px; line-height: 1.25; overflow-wrap: anywhere; }}
body.gg-tier--in_depth .gg-table td {{ color: #263941; padding: 7px 9px; line-height: 1.35; overflow-wrap: anywhere; }}
body.gg-tier--in_depth .gg-table tbody tr:nth-child(even) {{ background: #EAF1EC; }}
body.gg-tier--in_depth .gg-table .gg-cell-numeric {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
body.gg-tier--in_depth .gg-source-note {{ color: #40525B; }}
body.gg-tier--in_depth .gg-source-note-label {{ color: #243D4B; }}
body.gg-tier--in_depth .gg-callout {{ background: #F1F6F2; border-color: #D1DDD5; border-left-color: #387C58; }}
body.gg-tier--in_depth .gg-figure figcaption {{ color: #40525B; }}
body.gg-tier--in_depth .gg-title-band {{ border-color: #387C58; }}
body.gg-tier--in_depth .gg-title-band .gg-title {{ font-size: 30px; color: #17384F; }}
body.gg-tier--in_depth .gg-sources[data-source-placement="end_of_report"] {{ break-before: page; page-break-before: always; }}


/* ── Report-specific composition modes ───────────────────────────────
   Stable Growth Gradual typography/page furniture remains intact, while
   the model can change the information architecture more than the palette. */
.gg-style--technical .gg-chart-wrap, .gg-style--technical .gg-table-wrap {{ border-radius: 2px; border-width: 1px; background: var(--paper); border-top: 3px solid var(--accent); }}
.gg-style--technical .gg-chart-title {{ letter-spacing: .1em; font-size: 11px; }}
.gg-style--technical .gg-section-heading {{ border-left: 4px solid var(--accent); padding-left: 16px; }}
.gg-style--journal .gg-chart-wrap, .gg-style--journal .gg-table-wrap {{ border-left: 0; border-right: 0; border-top: 1px solid var(--rule); border-bottom: 1px solid var(--rule); border-radius: 0; background: transparent; padding-left: 4px; padding-right: 4px; }}
.gg-style--journal .gg-chart-title {{ text-transform: none; letter-spacing: .01em; font-size: 15px; color: var(--ink); }}
.gg-style--journal .gg-section-heading {{ border-top: 2px solid var(--ink); padding-top: 14px; }}
.gg-style--market .gg-chart-wrap, .gg-style--market .gg-table-wrap {{ background: var(--paper); border-left: 4px solid var(--accent); border-radius: 2px; }}
.gg-style--market .gg-section-heading {{ display: flex; align-items: baseline; gap: 12px; }}
.gg-style--infrastructure .gg-chart-wrap {{ background: var(--paper-alt); border: 1px solid var(--rule); border-radius: 12px; }}
.gg-style--infrastructure .gg-section-heading {{ border-bottom: 1px solid var(--rule); padding-bottom: 12px; }}
.gg-style--scientific .gg-chart-wrap {{ background: var(--paper); border: 0; border-top: 1px solid var(--rule); border-bottom: 1px solid var(--rule); border-radius: 0; }}
.gg-style--scientific .gg-section-heading {{ border-left: 2px solid var(--accent); padding-left: 12px; }}
.gg-cards--flat .gg-metric, .gg-cards--flat .gg-stat-card {{ border-color: transparent; background: var(--paper-alt); }}
.gg-cards--tinted .gg-metric, .gg-cards--tinted .gg-stat-card {{ background: var(--paper-alt); border-color: transparent; }}
.gg-cards--minimal .gg-metric, .gg-cards--minimal .gg-stat-card {{ border: 0; border-top: 1px solid var(--rule); background: transparent; border-radius: 0; }}
.gg-section-treatment--rail .gg-section-heading {{ border-left: 4px solid var(--accent); padding-left: 16px; }}
.gg-section-treatment--band .gg-section-heading {{ background: var(--paper-alt); padding: 14px 16px; border: 1px solid var(--rule); }}
.gg-section-treatment--chapter .gg-section-heading {{ display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: end; border-bottom: 2px solid var(--ink); padding-bottom: 10px; }}
.gg-style--market .gg-section-type--financials .gg-chart-wrap {{ border-left-color: var(--positive); }}
.gg-style--market .gg-section-type--risk_assessment .gg-chart-wrap {{ border-left-color: var(--negative); }}
.gg-chart-notes {{ margin-top: 12px; display: grid; gap: 7px; }}
.gg-chart-note {{ font-size: 12px; line-height: 1.5; color: var(--ink-soft); padding: 8px 10px; border-left: 2px solid var(--rule); }}
.gg-chart-note--takeaway {{ color: var(--ink); border-left-color: var(--accent); background: var(--paper-alt); }}
.gg-chart-note-label {{ font: 700 9px var(--font-heading); text-transform: uppercase; letter-spacing: .08em; color: var(--accent); margin-right: 8px; }}
.gg-chart-wrap .gg-chart-title + .gg-chart-canvas-box {{ margin-top: 2px; }}

@media (max-width: 760px) {{
  main, .gg-cover {{ width: min(100% - 28px, var(--content-max)); }}
  .gg-cover--data_driven, .gg-summary-wrap--sidebar, .gg-section--sidebar_main {{ grid-template-columns: 1fr; }}
  .gg-section--sidebar_main .gg-section-heading {{ position: static; }}
  .gg-section-body--two_column {{ columns: 1; }}
  .gg-timeline-item {{ grid-template-columns: 1fr; gap: 5px; }}
}}
/* Premium composition layer: page rhythm, figures, and narrative components vary by data density. */
/* Premium publication layer: stronger hierarchy and deliberate visual pacing. */
.gg-editorial-rule {{ height: 2px; width: 72px; background: linear-gradient(90deg, var(--accent), transparent); margin: 12px 0 18px; }}
.gg-section-heading h2 {{  }}
.gg-section-heading h3 {{ color: var(--accent); }}
.gg-chart-wrap--extended .gg-chart-title {{ margin-bottom: 10px; }}
.gg-chart-wrap--extended {{ padding: 16px 18px 14px; }}
.gg-chart-wrap--extended .gg-chart-notes {{ margin-top: 10px; }}
.gg-table-wrap thead {{ background: var(--paper-alt); }}
.gg-table tbody tr:nth-child(even) {{ background: var(--paper-alt); }}
.gg-table tbody tr:hover {{ background: var(--paper-alt); }}
.gg-figure img {{ display: block; margin: 0 auto; }}
.gg-figure figcaption {{ max-width: 88ch; margin-left: auto; margin-right: auto; text-align: left; }}
.gg-section-type--overview .gg-section-heading h2,
.gg-section-type--findings .gg-section-heading h2 {{ letter-spacing: -.025em; }}
.gg-section-type--comparison .gg-chart-wrap,
.gg-section-type--financials .gg-chart-wrap {{ background: var(--paper); }}
.gg-section-type--risk_assessment {{ border-top: 1px solid var(--rule); padding-top: 16px; }}
.gg-section-type--risk_assessment .gg-chart-title {{ color: var(--negative); }}
.gg-section[data-density="dense"] .gg-chart-wrap {{ margin-top: 14px; margin-bottom: 14px; }}
.gg-section[data-density="sparse"] .gg-chart-wrap {{ margin-top: 24px; margin-bottom: 28px; }}
/* Presentation-driven visual direction. The research planner chooses these
   semantic modes; the renderer maps them to concrete, print-safe composition. */
.gg-section-kicker {{ display:block; margin-bottom:5px; color:var(--accent); font:800 9px var(--font-body); letter-spacing:.14em; text-transform:uppercase; }}
.gg-v-comparison-led .gg-section-heading {{ border-bottom:2px solid var(--accent); }}
.gg-v-matrix-led .gg-section {{ padding:14px 16px; background:var(--paper-alt); border-radius:10px; }}
.gg-v-metric-led .gg-dashboard-strip {{ margin-top:10px; }}
.gg-v-narrative-led .gg-section-heading {{ border-bottom:0; }}
.gg-v-capital-stack .gg-section-heading {{ border-top:3px solid var(--accent); padding-top:10px; }}
.gg-v-driver-chain-led .gg-section-heading {{ border-left:4px solid var(--accent); padding-left:12px; }}
.gg-v-full-bleed .gg-section-body {{ max-width:none; }}
.gg-v-tight .gg-section {{ margin-bottom:12px; }}
.gg-v-generous .gg-section {{ margin-bottom:34px; }}
.gg-v-compact .gg-section-body {{ font-size:13px; }}
.gg-v-airy .gg-section-body {{ line-height:1.75; }}
.gg-v-bold-banner .gg-title-band {{ padding:34px 28px; background:var(--ink); color:#fff; border-radius:12px; }}
.gg-v-bold-banner .gg-title-band .gg-title, .gg-v-bold-banner .gg-title-band .gg-report-brandbar, .gg-v-bold-banner .gg-title-band .gg-title-meta {{ color:#fff; }}
.gg-v-classic .gg-title-band {{ text-align:center; padding:42px 8%; }}
.gg-v-data-driven .gg-title-band {{ display:grid; grid-template-columns:minmax(0,1.35fr) minmax(220px,.65fr); gap:24px; align-items:end; }}
.gg-v-plain .gg-chart-wrap, .gg-v-open .gg-chart-wrap {{ box-shadow:none; border-color:var(--rule); }}
.gg-v-hero .gg-chart-wrap {{ padding:22px; }}
.gg-v-comparison-matrix .gg-table th {{ background:var(--ink); }}
.gg-v-scenario-matrix .gg-table {{ font-size:12px; }}
.gg-v-banded .gg-section-heading {{ background:var(--paper-alt); padding:12px 14px; border-radius:8px; }}
@media print {{ .gg-v-data-driven .gg-title-band {{ display:block; }} .gg-v-bold-banner .gg-title-band {{ -webkit-print-color-adjust:exact; print-color-adjust:exact; }} }}

@media print {{
  .gg-tier--brief .gg-sources-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
  .gg-section + .gg-section {{ padding-top: 20px; }}
  .gg-section-heading {{ break-inside: avoid; break-after: avoid-page; page-break-after: avoid; }}
  .gg-section-heading h2 {{ max-width: 24ch; }}
  .gg-block, .gg-chart-wrap, .gg-table-wrap, .gg-callout, .gg-figure, .gg-diagram-wrap {{ break-inside: avoid !important; page-break-inside: avoid !important; }}
  .gg-section-body--grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }}
  .gg-chart-wrap--extended {{ padding: 9px 11px 8px; }}
  .gg-chart-note {{ font-size: 8.6pt; }}
}}
.gg-cover {{ position: relative; overflow: hidden; }}
.gg-cover--classic {{ min-height: 330px; display: flex; align-items: center; justify-content: center; }}
.gg-cover--bold_banner {{ min-height: 300px; }}
.gg-title {{ max-width: 19ch;  }}
.gg-section {{ position: relative; }}
.gg-section + .gg-section {{ padding-top: 28px; }}
.gg-section-heading h2 {{ max-width: 28ch; letter-spacing: -.018em;  }}
.gg-section[data-density="dense"] {{ padding-bottom: 6px; }}
.gg-section[data-density="sparse"] {{ max-width: 900px; }}
.gg-chart-wrap {{ border-radius: 10px; }}
.gg-chart-wrap--extended {{ background: var(--paper-alt); }}
.gg-chart-inline-svg {{ width: 100%; min-height: 240px; }}
.gg-chart-notes {{ margin-top: 14px; gap: 6px; }}
.gg-chart-note--why {{ background: var(--paper-alt); }}
.gg-chart-note--source {{ color: var(--muted); font-size: 10.5px; border-left-color: var(--accent); }}
.gg-chart-note-label {{ display: inline-block; min-width: 70px; }}
.gg-table-wrap {{ }}
.gg-figure {{ border-radius: 10px; padding: 10px; }}
.gg-composition--hybrid .gg-section:nth-child(3n+1) {{ background: var(--paper); padding: 24px; border-radius: 12px; }}
.gg-composition--hybrid .gg-section:nth-child(3n+2) .gg-chart-wrap {{ border-left: 4px solid var(--accent); }}
.gg-composition--hybrid .gg-section:nth-child(3n) .gg-table-wrap {{ border-left: 0; border-right: 0; border-radius: 0; background: transparent; }}
.gg-section-type--comparison .gg-section-body,
.gg-section-type--financials .gg-section-body {{ gap: 18px; }}
.gg-section-type--risk_assessment .gg-section-heading h2::before {{ content: "Risk lens"; display: block; font: 700 9px var(--font-heading); letter-spacing: .15em; text-transform: uppercase; color: var(--negative); margin-bottom: 8px; }}
.gg-section-type--financials .gg-section-heading h2::before {{ content: "Economics"; display: block; font: 700 9px var(--font-heading); letter-spacing: .15em; text-transform: uppercase; color: var(--positive); margin-bottom: 8px; }}
.gg-section-type--timeline .gg-section-heading h2::before {{ content: "Timeline"; display: block; font: 700 9px var(--font-heading); letter-spacing: .15em; text-transform: uppercase; color: var(--accent); margin-bottom: 8px; }}
/* ── Adaptive report design system ─────────────────────────────────────
   The model supplies semantic content; these rules decide presentation from
   the evidence density rather than from a fixed report template. */
.gg-composition--adaptive .gg-report-sections {{ gap: 30px; }}
.gg-report-sections {{ min-width: 0; max-width: 100%; }}
.gg-section-body {{ min-width: 0; max-width: 100%; }}
.gg-section-body--grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); align-items: start; }}
.gg-section-body--two_column {{ min-width: 0; }}
.gg-section--two_column .gg-section-body > *,
.gg-section--grid .gg-section-body > *,
.gg-section--sidebar_main .gg-section-body > * {{ min-width: 0; max-width: 100%; }}
.gg-chart-wrap, .gg-table-wrap, .gg-callout, .gg-risk, .gg-figure, .gg-metric, .gg-stat-card {{
  min-width: 0; max-width: 100%; overflow-wrap: anywhere;
}}
.gg-chart-size-compact .gg-chart-canvas-box {{ height: 230px; }}
.gg-chart-wrap--inline {{ margin: 10px 0 4px; padding: 12px 14px 8px; }}
.gg-chart-wrap--inline .gg-chart-canvas-box {{ height: 230px; }}
.gg-chart-wrap--inline .gg-chart-inline-svg svg,
.gg-chart-wrap--inline .gg-pdf-chart-fallback svg {{ max-height: 250px; width: 100%; height: auto; }}
.gg-chart-wrap--inline .gg-chart-notes {{ margin-top: 4px; }}
.gg-chart-size-standard .gg-chart-canvas-box {{ height: 300px; }}
.gg-chart-size-large .gg-chart-canvas-box {{ height: 360px; }}
.gg-chart-size-compact .gg-chart-inline-svg {{ min-height: 180px; }}
.gg-chart-size-standard .gg-chart-inline-svg {{ min-height: 240px; }}
.gg-chart-size-large .gg-chart-inline-svg {{ min-height: 300px; }}
.gg-tier--brief main {{ padding-top: 26px; }}
.gg-tier--brief .gg-report-sections {{ gap: 22px; }}
.gg-tier--brief .gg-section-heading h2 {{ font-size: clamp(22px, 2.8vw, 30px); }}
.gg-tier--brief .gg-chart-size-large .gg-chart-canvas-box {{ height: 300px; }}
.gg-tier--detailed .gg-report-sections {{ gap: 28px; }}
.gg-tier--in_depth main {{ padding-top: 38px; }}
.gg-tier--in_depth .gg-report-sections {{ gap: 34px; }}
.gg-tier--in_depth .gg-section-heading h2 {{ font-size: clamp(25px, 3.2vw, 36px); }}
.gg-tier--in_depth .gg-chart-size-large .gg-chart-canvas-box {{ height: 390px; }}
.gg-tier--in_depth .gg-table {{ font-size: 13px; }}
.gg-section--full_bleed {{ overflow-x: hidden; }}
.gg-table-scroll {{ max-width: 100%; min-width: 0; }}
.gg-table {{ max-width: 100%; }}
.gg-table th, .gg-table td {{ overflow-wrap: anywhere; word-break: normal; }}
.gg-source-card {{ min-width: 0; max-width: 100%; }}

/* Shared report shell: HTML is the canonical composition; print changes only
   navigation/furniture and page-safe behavior in the PDF adapter. */
.gg-report-nav {{ position: sticky; top: 0; z-index: 20; border-block: 1px solid var(--border); background: rgba(255,255,255,.97); backdrop-filter: blur(12px); }}
.gg-report-nav details {{ width: min(var(--content-max), calc(100% - 48px)); margin: 0 auto; }}
.gg-report-nav summary {{ list-style: none; cursor: pointer; min-height: 46px; display: flex; align-items: center; color: var(--primary); font-size: 12px; font-weight: 750; letter-spacing: .06em; text-transform: uppercase; }}
.gg-report-nav summary::-webkit-details-marker {{ display: none; }}
.gg-report-nav summary::after {{ content: "⌄"; margin-left: auto; font-size: 16px; }}
.gg-report-nav ul {{ display: flex; flex-wrap: wrap; gap: 5px 18px; list-style: none; padding: 0 0 10px; margin: 0; }}
.gg-report-nav a {{ display: inline-block; padding: 5px 0; color: var(--text-muted); font-size: 12px; font-weight: 650; text-decoration: none; }}
.gg-report-nav a:hover, .gg-report-nav a:focus-visible {{ color: var(--primary); text-decoration: underline; text-underline-offset: 4px; }}
.gg-report-index {{ margin: 24px 0 30px; padding: 24px 26px; border: 1px solid var(--border); border-radius: 12px; background: var(--surface-alt); break-inside: avoid; }}
.gg-report-index h2 {{ margin: 0 0 14px; color: var(--primary); font-size: 20px; }}
.gg-report-index ol {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 3px 28px; list-style: none; padding: 0; margin: 0; counter-reset: report-index; }}
.gg-report-index li {{ min-width: 0; counter-increment: report-index; border-bottom: 1px solid var(--border); }}
.gg-report-index a {{ display: grid; grid-template-columns: 32px minmax(0,1fr); align-items: baseline; gap: 10px; padding: 9px 0; color: var(--text); text-decoration: none; }}
.gg-report-index a::before {{ content: counter(report-index, decimal-leading-zero); color: var(--accent); font-size: 11px; font-weight: 800; }}
.gg-report-index a:hover {{ color: var(--accent); }}
.gg-report-index-label {{ overflow-wrap: anywhere; }}
.gg-report-brandbar {{ display: flex; align-items: center; gap: 10px; color: var(--primary); font-size: 11px; font-weight: 800; letter-spacing: .13em; text-transform: uppercase; }}
.gg-report-brandbar::before {{ content: ""; width: 24px; height: 3px; background: var(--accent); }}
.gg-dashboard-strip {{ margin: 18px 0 28px; }}
.gg-dashboard-strip .gg-stats-grid {{ grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); margin: 0; }}
.gg-dashboard-strip .gg-stat-card {{ border-radius: 9px; box-shadow: 0 3px 12px rgba(23,56,79,.045); }}
body.gg-tier--brief .gg-dashboard-strip .gg-stats-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }}
body.gg-tier--brief .gg-dashboard-strip .gg-stat-card {{ min-width: 0; min-height: 78px; padding: 14px 15px; }}
body.gg-tier--brief .gg-dashboard-strip .gg-stat-value {{ font-size: clamp(20px, 2.2vw, 28px); line-height: 1.05; }}
body.gg-tier--brief .gg-dashboard-strip .gg-stat-label {{ font-size: 9px; line-height: 1.2; }}
.gg-source-meta {{ overflow-wrap: anywhere; }}
.gg-sources {{ scroll-margin-top: 72px; }}
.gg-report-footer {{ width: min(var(--content-max), calc(100% - 48px)); margin: 0 auto; padding: 18px 0 28px; border-top: 1px solid var(--border); display: flex; flex-wrap: wrap; justify-content: space-between; gap: 6px 18px; color: var(--text-muted); font-size: 11px; }}
.gg-report-footer strong {{ color: var(--primary); }}
.gg-section {{ scroll-margin-top: 76px; }}
.gg-chart-wrap, .gg-table-wrap, .gg-figure {{ border-radius: 10px; box-shadow: 0 5px 20px rgba(23,56,79,.035); }}
.gg-figure img {{ max-height: 540px; object-fit: contain; }}
.gg-table {{ font-family: var(--font-body); }}
.gg-table th {{ color: #fff; background: var(--primary); border-bottom-color: var(--primary); }}
.gg-table tbody tr:nth-child(even) {{ background: var(--surface-alt); }}
.gg-table tbody td:nth-child(n+2) {{ font-variant-numeric: tabular-nums; }}
.gg-source-note {{ color: #46545e; }}

@media (min-width: 761px) {{
  .gg-report-nav summary {{ min-height: 0; padding-top: 10px; }}
  .gg-report-nav summary::after {{ display: none; }}
  .gg-report-nav ul {{ padding-bottom: 8px; }}
}}
@media (max-width: 1024px) {{
  .gg-report-index ol {{ column-gap: 18px; }}
  .gg-dashboard-strip .gg-stats-grid {{ grid-template-columns: repeat(auto-fit, minmax(145px, 1fr)); }}
}}
@media (max-width: 760px) {{
  .gg-report-nav details {{ width: calc(100% - 28px); }}
  .gg-report-nav ul {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 2px 14px; padding-bottom: 12px; }}
  .gg-report-nav a {{ display: block; padding: 8px 0; border-top: 1px solid var(--border); }}
  .gg-report-index {{ padding: 18px; margin: 20px 0; }}
  .gg-report-index ol {{ grid-template-columns: 1fr; }}
  .gg-report-brandbar {{ font-size: 9px; letter-spacing: .1em; }}
  .gg-report-footer {{ width: calc(100% - 28px); display: grid; }}
  .gg-dashboard-strip .gg-stats-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
  .gg-stat-card {{ padding: 12px; }}
  .gg-stat-value {{ font-size: 19px; }}
  .gg-chart-wrap, .gg-table-wrap {{ padding: 12px; }}
  .gg-chart-canvas-box {{ height: 250px; }}
  .gg-figure img {{ max-height: 340px; }}
  .gg-section {{ scroll-margin-top: 60px; }}
}}

@media print {{
  .gg-report-nav {{ display: none !important; }}
  .gg-report-index {{ border-radius: 0; box-shadow: none; background: #fff; }}
  .gg-report-index a {{ color: #17384F; }}
  .gg-dashboard-strip .gg-stat-card {{ box-shadow: none; }}
  /* PDF/print layout: override browser-oriented widths so A4 content cannot overflow. */
  html, body {{ background: #fff; }}
  body {{ font-size: 10.5pt; line-height: 1.48; overflow-wrap: anywhere; }}
  p, .gg-list {{ font-size: 10.5pt; line-height: 1.48; }}
  h1 {{ break-after: avoid-page; }}
  h2, h3, h4 {{ break-after: avoid-page; page-break-after: avoid; }}
  /* Tier-aware print rhythm. The structure remains dynamic; only density and component sizing change. */
  body.gg-tier--brief main {{ padding-top: 12px; }}
  body.gg-tier--brief .gg-section {{ margin-bottom: 16px; }}
  body.gg-tier--brief .gg-section-heading h2 {{ font-size: 16pt; }}
  body.gg-tier--detailed .gg-section {{ margin-bottom: 20px; }}
  body.gg-tier--in_depth main {{ padding-top: 18px; }}
  body.gg-tier--in_depth .gg-section {{ margin-bottom: 26px; }}
  body.gg-tier--in_depth .gg-section-heading h2 {{ font-size: 19pt; }}
  body.gg-tier--in_depth .gg-section-heading {{ margin-bottom: 12px; }}
  .gg-section-body--grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }}
  .gg-section-body--grid > * {{ min-width: 0; width: 100% !important; max-width: 100% !important; }}
  .gg-section-body--two_column {{ columns: 1; column-gap: 0; width: 100%; }}
  .gg-chart-size-compact .gg-chart-canvas-box {{ height: 190px; }}
  .gg-chart-size-standard .gg-chart-canvas-box {{ height: 235px; }}
  .gg-chart-size-large .gg-chart-canvas-box {{ height: 285px; }}
  .gg-chart-size-compact .gg-chart-inline-svg {{ min-height: 160px; }}
  .gg-chart-size-standard .gg-chart-inline-svg {{ min-height: 205px; }}
  .gg-chart-size-large .gg-chart-inline-svg {{ min-height: 250px; }}
  p {{ orphans: 3; widows: 3; }}
  /* WeasyPrint lays out percentage-width blocks from the page content box.
     Keep every report primitive inside that box explicitly; the interactive
     renderer can use wider browser compositions, but PDF must never let a
     section/chart/table establish a width larger than the A4 content area. */
  *, *::before, *::after {{ box-sizing: border-box !important; min-width: 0; }}
  html, body {{ width: 100%; max-width: 100%; overflow: hidden; }}
  main, .gg-cover, .gg-report-sections, .gg-section, .gg-section-body,
  .gg-block, .gg-chart-wrap, .gg-table-wrap, .gg-chart-canvas-box,
  .gg-chart-inline-svg, .gg-figure, .gg-sources {{
    width: 100% !important; max-width: 100% !important; min-width: 0 !important;
    box-sizing: border-box !important;
  }}
  main {{ padding: 18px 0 38px; margin-left: 0 !important; margin-right: 0 !important; }}
  .gg-cover {{ margin-left: 0 !important; margin-right: 0 !important; }}
  .gg-section, .gg-section--full_bleed {{ margin-left: 0 !important; margin-right: 0 !important; }}
  .gg-section--full_bleed {{ width: 100% !important; padding-left: 0 !important; padding-right: 0 !important; }}
  img, svg, canvas, table {{ max-width: 100% !important; box-sizing: border-box !important; }}
  p, li, td, th, .gg-chart-title, .gg-chart-note, .gg-source-title, .gg-source-link {{
    max-width: 100%; overflow-wrap: anywhere; word-break: normal;
  }}
  .gg-cover {{ margin-top: 0; }}
  /* A selected cover is a true print cover; do not stack the title band beneath it. */
  body.gg-has-cover:not(.gg-tier--brief) .gg-cover {{ break-after: page; page-break-after: always; }}
  body.gg-has-cover.gg-cover-bold-banner:not(.gg-tier--brief) .gg-cover {{ min-height: 215mm; display: flex; align-items: center; }}
  .gg-title {{ font-size: 30pt; line-height: 1.08; }}
  .gg-summary {{ font-size: 12pt; line-height: 1.5; }}
  .gg-summary-card {{ padding: 16px 18px; }}
  .gg-report-sections {{ display: block; }}
  .gg-section {{ break-inside: auto; margin-bottom: 22px; }}
  /* Major generated sections are editorial units. Keep subsequent sections from
     starting in the last few lines of the previous page. */
  .gg-section + .gg-section {{ break-before: auto; page-break-before: auto; }}
  .gg-section-heading {{ break-inside: avoid; break-after: avoid-page; margin-bottom: 10px; }}
  .gg-section-heading h2 {{ font-size: 18pt; line-height: 1.15; }}
  .gg-section--full_bleed {{ width: 100% !important; margin-left: 0; padding-left: 0; padding-right: 0; }}
  .gg-section-body--two_column {{ columns: 1; column-gap: 0; width: 100%; }}
  .gg-chart-wrap, .gg-table-wrap {{ margin: 12px 0; padding: 10px 12px; }}
  .gg-chart-canvas-box {{ width: 100% !important; max-width: 100% !important; height: 240px; min-height: 0; overflow: hidden; }}
  .gg-chart-inline-svg {{ width: 100% !important; max-width: 100% !important; min-height: 220px; overflow: hidden; }}
  .gg-chart-inline-svg svg {{ width: 100% !important; max-width: 100% !important; height: auto !important; display: block; }}
  .gg-pdf-chart-fallback {{ width: 100% !important; max-width: 100% !important; overflow: hidden; }}
  .gg-pdf-chart-fallback svg {{ width: 100% !important; height: auto !important; max-width: 100% !important; display: block; }}
  .gg-table-scroll {{ overflow: visible; width: 100%; }}
  .gg-table {{ width: 100%; table-layout: fixed; font-size: 9.2pt; }}
  .gg-table th {{ padding: 6px 7px; font-size: 9pt; overflow-wrap: anywhere; }}
  .gg-table td {{ padding: 6px 7px; font-size: 9pt; overflow-wrap: anywhere; }}
  .gg-metrics {{ grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; margin: 12px 0; }}
  .gg-metric {{ padding: 9px 10px; }}
  .gg-metric-value {{ font-size: 17pt; line-height: 1.1; }}
  .gg-metric-label {{ font-size: 8pt; line-height: 1.2; }}
  .gg-metric-change {{ font-size: 8.5pt; }}
  .gg-stats-grid {{ grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; margin: 12px 0; }}
  .gg-stat-card {{ padding: 9px 10px; }}
  .gg-stat-value {{ font-size: 16pt; line-height: 1.08; }}
  .gg-stat-label {{ font-size: 7.8pt; line-height: 1.18; }}
  .gg-stat-change {{ font-size: 8pt; }}
  .gg-timeline-item {{ grid-template-columns: 86px minmax(0, 1fr); gap: 10px; padding: 10px 0; }}
  .gg-timeline-title {{ font-size: 10.5pt; }}
  .gg-timeline-desc {{ font-size: 9.5pt; }}
  .gg-callout {{ margin: 12px 0; padding: 10px 12px; }}
  .gg-risk-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; margin: 12px 0; }}
  .gg-risk {{ padding: 9px; }}
  .gg-risk__meta, .gg-risk__mitigation {{ font-size: 8.8pt; }}
  .gg-sources {{ margin-top: 24px; padding: 0; border: 0; background: transparent; break-before: auto; page-break-before: auto; break-inside: auto; page-break-inside: auto; }}
  .gg-sources-intro {{ font-size: 9pt; }}
  .gg-sources-grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 6px; align-items: start; }}
  .gg-sources[data-source-placement="appendix"], .gg-sources[data-source-placement="end_of_report"] {{ break-before: page; page-break-before: always; }}
.gg-sources {{ break-inside: auto; }}
  .gg-sources .gg-source-card {{ break-inside: avoid; page-break-inside: avoid; }}
  .gg-source-card {{ display: flex; align-items: flex-start; gap: 8px; padding: 6px; margin: 0; break-inside: avoid; page-break-inside: avoid; }}
  .gg-source-number {{ flex: 0 0 40px; box-sizing: border-box; width: 40px; min-width: 40px; height: 20px; padding: 0 2px; white-space: nowrap; font-size: 8pt; border-radius: 4px; }}
  .gg-source-title {{ font-size: 8.7pt; line-height: 1.22; }}
  .gg-source-meta {{ font-size: 7.6pt; margin-top: 2px; }}
  .gg-source-link {{ font-size: 7.8pt; line-height: 1.15; }}
  .gg-footer {{ padding: 18px 0 24px; font-size: 8pt; break-before: avoid; }}
  /* Premium brief: atomic, legible KPI dashboard and compact evidence cards. */
  body.gg-tier--brief .gg-stats-grid {{ display: flex !important; flex-wrap: wrap; gap: 7px; break-inside: avoid !important; page-break-inside: avoid !important; }}
  body.gg-tier--brief .gg-stat-card {{ flex: 1 1 43%; max-width: calc(50% - 4px); min-height: 65px; break-inside: avoid !important; padding: 10px 12px; }}
  body.gg-tier--brief .gg-stat-value {{ font-size: 17pt !important; line-height: 1.1; overflow-wrap: normal; word-break: normal; }}
  body.gg-tier--brief .gg-stat-label {{ font-size: 8.2pt; letter-spacing: .02em; }}
  body.gg-tier--brief .gg-dashboard-strip {{ break-inside: avoid !important; page-break-inside: avoid !important; }}
  body.gg-tier--brief .gg-cover {{ padding: 8px 0 14px !important; margin-bottom: 6px; min-height: 0 !important; }}
  body.gg-tier--brief .gg-title {{ font-size: 23pt !important; line-height: 1.13; }}
  body.gg-tier--brief .gg-summary-card {{ padding: 11px 15px; }}
  body.gg-tier--brief .gg-section-body p {{ text-align: left; hyphens: none; }}
  body.gg-tier--brief .gg-brief-trend {{ break-inside: avoid; page-break-inside: avoid; }}
  .gg-sources {{ break-inside: auto !important; page-break-inside: auto !important; }}
  .gg-source-card {{ break-inside: avoid !important; page-break-inside: avoid !important; }}
  .gg-reveal {{ opacity: 1 !important; transform: none !important; }}
}}

/* ================================================================
   PREMIUM RESEARCH INTELLIGENCE V2
   A restrained editorial system: stronger hierarchy, better scanability,
   evidence-first cards, and tighter print composition.  This layer changes
   presentation only; it never changes the report's research content.
   ================================================================ */
:root {{
  --gg-shadow: 0 10px 30px rgba(24, 43, 36, .055);
  --gg-shadow-soft: 0 4px 16px rgba(24, 43, 36, .045);
  --gg-radius: 14px;
  --gg-radius-sm: 9px;
}}
body {{ background: linear-gradient(180deg, #fbfcfa 0, var(--paper) 420px); }}
main {{ max-width: 1160px; }}
.gg-report-brandbar, .gg-eyebrow {{ font-weight: 800; }}
.gg-report-brandbar {{ display:flex; align-items:center; gap:9px; }}
.gg-report-brandbar::before {{ content:""; width:28px; height:3px; border-radius:99px; background:var(--accent); flex:0 0 auto; }}
.gg-title-band {{ padding: 22px 0 18px; border-bottom: 3px solid var(--accent); }}
.gg-title-band .gg-title {{ font-size: clamp(36px, 5vw, 58px); letter-spacing:-.035em; }}
.gg-summary-card {{ border:0; border-left:5px solid var(--accent); border-radius:var(--gg-radius); box-shadow:var(--gg-shadow-soft); background:linear-gradient(135deg,var(--paper-alt),#fff); }}
.gg-summary-heading {{ font-size:clamp(22px,2.4vw,30px); letter-spacing:-.02em; }}
.gg-dashboard-strip {{ margin: 22px 0 34px; }}
.gg-dashboard-strip .gg-stats-grid {{ gap:12px; }}
.gg-stat-card {{ border:1px solid rgba(28,50,42,.12); border-radius:var(--gg-radius-sm); background:#fff; box-shadow:var(--gg-shadow-soft); padding:17px 18px; position:relative; overflow:hidden; }}
.gg-stat-card::before {{ content:""; position:absolute; left:0; top:0; bottom:0; width:3px; background:var(--accent); opacity:.7; }}
.gg-stat-value {{ font-size:clamp(22px,2.5vw,29px); letter-spacing:-.025em; font-variant-numeric:tabular-nums; }}
.gg-stat-label {{ font-size:10px; letter-spacing:.085em; }}
.gg-stat-change {{ margin-top:7px; }}
.gg-section {{ position:relative; }}
.gg-section-heading {{ display:grid; grid-template-columns:42px minmax(0,1fr); gap:14px; align-items:start; padding:0 0 12px; margin-bottom:18px; border-bottom:1px solid var(--rule); }}
.gg-section-index {{ display:grid; place-items:center; width:34px; height:34px; border-radius:50%; background:var(--ink); color:#fff; font:800 10px var(--font-body); letter-spacing:.08em; }}
.gg-section-heading-copy h2 {{ margin:0; font-size:clamp(25px,3vw,36px); letter-spacing:-.025em; }}
.gg-section-heading-copy::before {{ content:"RESEARCH SECTION"; display:block; margin-bottom:5px; color:var(--accent); font:800 9px var(--font-body); letter-spacing:.14em; }}
.gg-section-body > p {{ max-width:76ch; }}
.gg-section[data-layout="grid"] .gg-section-body > p, .gg-section[data-layout="two_column"] .gg-section-body > p {{ max-width:none; }}
.gg-chart-wrap, .gg-table-wrap, .gg-figure {{ border-radius:var(--gg-radius-sm); box-shadow:var(--gg-shadow-soft); background:#fff; border-color:rgba(28,50,42,.12); }}
.gg-chart-wrap {{ padding:19px 20px 15px; }}
.gg-chart-title {{ display:flex; align-items:center; gap:8px; font-size:10px; letter-spacing:.12em; }}
.gg-chart-title::before {{ content:""; width:18px; height:2px; background:var(--accent); border-radius:99px; }}
.gg-chart-notes {{ border-top:1px solid var(--rule); padding-top:9px; }}
.gg-chart-note--takeaway {{ border-radius:7px; }}
.gg-table-wrap {{ overflow:hidden; }}
.gg-table {{ font-size:13px; }}
.gg-table th {{ padding:11px 12px; font-size:10px; letter-spacing:.06em; text-transform:uppercase; }}
.gg-table td {{ padding:10px 12px; }}
.gg-table tbody tr:nth-child(even) {{ background:#f7f9f7; }}
.gg-pullquote {{ margin:22px 0; padding:13px 18px; border-left-width:4px; border-radius:0 var(--gg-radius-sm) var(--gg-radius-sm) 0; background:linear-gradient(90deg,var(--paper-alt),transparent); font-size:20px; line-height:1.42; }}
.gg-block--emphasis-high {{ padding:15px 18px; border-radius:0 var(--gg-radius-sm) var(--gg-radius-sm) 0; background:var(--paper-alt); }}
.gg-figure {{ padding:10px; }}
.gg-figure img {{ border:0; border-radius:7px; }}
.gg-figure figcaption {{ padding:0 7px 5px; }}
.gg-source-note {{ border-top:1px dashed var(--rule); padding-top:6px; }}
.gg-source-cite {{ font-weight:800; }}
.gg-report-nav {{ width:min(1160px,calc(100% - 48px)); margin:0 auto 20px; }}
.gg-report-nav details {{ border:1px solid rgba(28,50,42,.12); border-radius:var(--gg-radius-sm); background:rgba(255,255,255,.92); box-shadow:var(--gg-shadow-soft); }}
.gg-report-nav summary {{ padding:11px 15px; font-weight:800; color:var(--ink); cursor:pointer; }}
.gg-report-index {{ border:1px solid rgba(28,50,42,.12); border-radius:var(--gg-radius); background:#fff; box-shadow:var(--gg-shadow-soft); }}
.gg-report-index h2 {{ letter-spacing:-.02em; }}
.gg-sources {{ border:0; border-top:3px solid var(--accent); border-radius:var(--gg-radius); box-shadow:var(--gg-shadow-soft); background:#fff; }}
.gg-sources h2 {{ font-size:28px; letter-spacing:-.02em; }}
.gg-source-card {{ border:1px solid rgba(28,50,42,.11); border-radius:var(--gg-radius-sm); box-shadow:none; transition:transform .18s ease, box-shadow .18s ease; }}
.gg-source-card:hover {{ transform:translateY(-1px); box-shadow:var(--gg-shadow-soft); }}
.gg-source-number {{ border-radius:7px; background:var(--paper-alt); }}
.gg-report-footer {{ padding-top:14px; }}
body.gg-tier--brief .gg-section-heading {{ grid-template-columns:34px minmax(0,1fr); margin-bottom:12px; padding-bottom:8px; }}
body.gg-tier--brief .gg-section-index {{ width:28px; height:28px; font-size:9px; }}
body.gg-tier--brief .gg-section-heading-copy h2 {{ font-size:22px; }}
body.gg-tier--brief .gg-section-body > p {{ font-size:14px; line-height:1.48; }}
body.gg-tier--brief .gg-chart-wrap {{ margin:12px 0; padding:12px 14px 10px; }}
body.gg-tier--brief .gg-table-wrap {{ margin:12px 0; }}
body.gg-tier--brief .gg-source-card {{ padding:8px 9px; }}
body.gg-tier--brief .gg-sources {{ margin-top:18px; padding:12px 14px; }}
body.gg-tier--brief .gg-sources-intro {{ margin:4px 0 8px; font-size:10px; }}
body.gg-tier--brief .gg-sources h2 {{ font-size:20px; }}
body.gg-tier--brief .gg-sources-grid {{ grid-template-columns:repeat(2,minmax(0,1fr)); gap:7px; }}
/* Brief KPI row: three equal cards, compact typography, and no aggressive
   word breaking. A long multi-company metric stays legible instead of
   becoming a visually dominant/tall card. */
body.gg-tier--brief .gg-metrics {{ grid-template-columns:repeat(3,minmax(0,1fr)); gap:8px; margin:10px 0 12px; }}
body.gg-tier--brief .gg-metric {{ min-width:0; min-height:72px; padding:10px 11px; break-inside:avoid; page-break-inside:avoid; }}
body.gg-tier--brief .gg-metric-value {{ font-size:20px; line-height:1.08; overflow-wrap:normal; word-break:normal; }}
body.gg-tier--brief .gg-metric-label {{ font-size:8.5px; line-height:1.18; letter-spacing:.025em; }}
body.gg-tier--brief .gg-metric-change {{ font-size:8.5px; }}
body.gg-tier--brief .gg-source-title {{ font-size:11px; line-height:1.25; }}
body.gg-tier--brief .gg-source-meta, body.gg-tier--brief .gg-source-link {{ font-size:9px; line-height:1.25; }}
body.gg-tier--in_depth .gg-section-heading-copy h2 {{ font-size:clamp(24px,2.7vw,32px); }}
body.gg-tier--in_depth .gg-section-body > p {{ max-width:78ch; }}
body.gg-tier--in_depth .gg-chart-wrap, body.gg-tier--in_depth .gg-table-wrap {{ box-shadow:none; }}
body.gg-tier--in_depth .gg-stat-card {{ box-shadow:none; }}
/* Avoid wasteful page breaks and keep evidence blocks with their headings. */
.gg-section-heading + .gg-section-body {{ break-before:avoid; }}
.gg-chart-wrap, .gg-table-wrap, .gg-figure, .gg-callout, .gg-risk, .gg-stat-card {{ break-inside:avoid; page-break-inside:avoid; }}
@media (min-width: 761px) {{
  body.gg-tier--in_depth .gg-section + .gg-section {{ margin-top:6px; }}
  body.gg-tier--detailed .gg-section + .gg-section {{ margin-top:2px; }}
}}
@media print {{
  body {{ background:#fff; }}
  .gg-section-heading {{ grid-template-columns:28px minmax(0,1fr); gap:9px; padding-bottom:7px; margin-bottom:9px; }}
  .gg-section-index {{ width:24px; height:24px; font-size:7.5pt; }}
  .gg-section-heading-copy::before {{ font-size:6.5pt; margin-bottom:2px; }}
  .gg-section-heading-copy h2 {{ font-size:17pt; }}
  .gg-summary-card {{ box-shadow:none; border-left-width:3px; }}
  .gg-stat-card {{ box-shadow:none; border-radius:5px; padding:8px 9px; }}
  .gg-stat-card::before {{ width:2px; }}
  .gg-chart-wrap, .gg-table-wrap, .gg-figure {{ box-shadow:none; border-radius:5px; }}
  .gg-chart-wrap {{ padding:9px 11px 8px; }}
  .gg-table th {{ padding:6px 7px; }}
  .gg-table td {{ padding:6px 7px; }}
  body.gg-tier--brief .gg-dashboard-strip .gg-stats-grid {{ display:grid !important; grid-template-columns:repeat(2,minmax(0,1fr)) !important; gap:8px !important; margin:10px 0 14px !important; break-inside:avoid !important; page-break-inside:avoid !important; }}
  body.gg-tier--brief .gg-dashboard-strip .gg-stat-card {{ width:auto !important; max-width:none !important; min-height:68px !important; padding:9px 10px !important; break-inside:avoid !important; page-break-inside:avoid !important; }}
  body.gg-tier--brief .gg-dashboard-strip .gg-stat-value {{ font-size:16pt !important; line-height:1.05 !important; }}
  body.gg-tier--brief .gg-dashboard-strip .gg-stat-label {{ font-size:7.5pt !important; line-height:1.15 !important; }}
  body.gg-tier--brief .gg-section {{ margin-bottom:10px; }}
  /* Let long risk tables flow to the next page without painting an empty pink
     continuation panel. Keep individual rows together, not the entire table. */
  .gg-section[data-emphasis="critical"] {{ background:transparent !important; padding:0 !important; border-radius:0 !important; break-inside:auto !important; page-break-inside:auto !important; }}
  .gg-section-type--risk_assessment .gg-chart-wrap,
  .gg-section-type--risk_assessment .gg-table-wrap,
  .gg-section-type--risk_assessment .gg-table-scroll {{ break-inside:auto !important; page-break-inside:auto !important; overflow:visible !important; }}
  .gg-section-type--risk_assessment .gg-table tr {{ break-inside:avoid !important; page-break-inside:avoid !important; }}
  body.gg-tier--brief .gg-section-body > p {{ font-size:9.4pt; line-height:1.38; }}
  body.gg-tier--brief .gg-section-heading-copy h2 {{ font-size:14pt; }}
  body.gg-tier--brief .gg-chart-canvas-box {{ height:180px; }}
  .gg-print-page-break {{ display:none; }}
  body.gg-tier--brief .gg-print-page-break {{ display:block; height:0; margin:0; padding:0; break-before:page; page-break-before:always; }}
  body.gg-tier--brief .gg-sources {{ margin-top:10px; padding:8px 10px; break-before:page !important; page-break-before:always !important; }}
  body.gg-tier--brief .gg-sources h2 {{ font-size:13pt; }}
  body.gg-tier--brief .gg-sources-intro {{ margin:2px 0 6px; }}
  body.gg-tier--brief .gg-sources-grid {{ grid-template-columns:repeat(4,minmax(0,1fr)); gap:4px; }}
  body.gg-tier--brief .gg-source-card {{ padding:5px 6px; }}
  body.gg-tier--brief .gg-source-title {{ font-size:7.7pt; line-height:1.15; }}
  body.gg-tier--brief .gg-source-meta, body.gg-tier--brief .gg-source-link {{ font-size:6.7pt; line-height:1.1; }}
  body.gg-tier--detailed .gg-section {{ margin-bottom:13px; }}
  body.gg-tier--in_depth .gg-section {{ margin-bottom:18px; }}
  /* Keep the terminal source appendix from producing a source-only tail page. */
  body:not(.gg-tier--brief) .gg-sources {{ break-before: page; page-break-before: always; }}
  .gg-sources {{ box-shadow:none; }}
  .gg-source-card {{ padding:5px; }}
}}

/* v19 publication hardening: keep complex data visuals out of narrow prose grids.
   Narrative can still use a deliberate two-column composition, but tables/charts
   get the full section width so headers and words never collapse to single letters. */
.gg-section-body--grid > .gg-table-wrap,
.gg-section-body--grid > .gg-chart-wrap,
.gg-section-body--grid > .gg-figure,
.gg-section-body--grid > .gg-diagram-wrap,
.gg-section-body--grid > .gg-metrics,
.gg-section-body--grid > .gg-stats-grid,
.gg-section-body--grid > .gg-risk-grid,
.gg-section-body--grid > .gg-timeline,
.gg-section-body--grid > .gg-callout,
.gg-section-body--grid > .gg-source-note {{
  grid-column: 1 / -1 !important;
  min-width: 0 !important;
  width: 100% !important;
  max-width: 100% !important;
}}
.gg-section-body--grid > .gg-table-wrap,
.gg-section-body--grid > .gg-chart-wrap,
.gg-section-body--grid > .gg-figure {{ break-inside: avoid; page-break-inside: avoid; }}
.gg-table-scroll {{ min-width: 0; width: 100%; max-width: 100%; overflow-x: auto; }}
.gg-table {{ width: 100%; max-width: 100%; table-layout: auto !important; }}
.gg-table th, .gg-table td {{ overflow-wrap: break-word !important; word-break: normal !important; hyphens: auto; }}
body.gg-tier--in_depth .gg-table {{ font-size: 10.5pt; }}
body.gg-tier--in_depth .gg-table th {{ overflow-wrap: break-word !important; word-break: normal !important; }}
body.gg-tier--in_depth .gg-section-body > p {{ text-align: justify !important; text-justify: inter-word; }}
.gg-sources-count {{ margin: 2px 0 8px; color: var(--muted); font-size: 9pt; font-weight: 600; }}
.gg-source-list {{ display: block !important; margin: 0 !important; padding-left: 22px !important; list-style: decimal !important; }}
.gg-source-line {{ display: list-item !important; min-width: 0; margin: 2px 0 !important; padding: 0 !important; border: 0 !important; background: transparent !important; font-size: 9pt !important; line-height: 1.3 !important; color: var(--ink); break-inside: avoid; page-break-inside: avoid; overflow-wrap: anywhere; }}
.gg-source-title-text {{ display: inline; font-weight: 400; overflow-wrap: anywhere; }}
.gg-source-id {{ display: none !important; }}
.gg-source-link {{ display: inline !important; margin: 0 !important; font-size: 9pt !important; line-height: inherit !important; white-space: nowrap; }}
.gg-source-unavailable {{ display: inline; font-size: 8.5pt; color: var(--muted); }}
@media print {{
  /* Source cards contain an ID column and a full-width content cell. Keep the
     bibliography readable rather than squeezing seven Brief sources into four
     narrow columns, which caused URL/title cards to spill across pages. */
  /* Six-source brief appendices otherwise leave the final citation alone on a
     mostly blank tail page. Use two readable columns only for this compact case;
     larger source sets remain full-width to keep long URLs legible. */
  body.gg-tier--brief .gg-sources[data-source-count="6"] .gg-sources-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)) !important; gap: 6px !important; }}
  body.gg-tier--brief .gg-sources:not([data-source-count="6"]) .gg-sources-grid {{ grid-template-columns: minmax(0, 1fr) !important; }}
  body.gg-tier--brief .gg-sources[data-source-count="6"] .gg-source-card {{ gap: 5px !important; padding: 4px 5px !important; }}
  body.gg-tier--brief .gg-sources[data-source-count="6"] .gg-source-number {{ flex-basis: 34px !important; width: 34px !important; min-width: 34px !important; max-width: 34px !important; font-size: 6.5pt !important; }}
  body.gg-tier--brief .gg-sources[data-source-count="6"] .gg-source-title {{ font-size: 7pt !important; line-height: 1.12 !important; overflow-wrap: anywhere !important; }}
  body.gg-tier--brief .gg-sources[data-source-count="6"] .gg-source-meta, body.gg-tier--brief .gg-sources[data-source-count="6"] .gg-source-link {{ font-size: 6pt !important; line-height: 1.1 !important; overflow-wrap: anywhere !important; }}
  body.gg-tier--brief .gg-sources .gg-source-card {{ display: flex !important; align-items: flex-start !important; gap: 8px !important; }}
  body.gg-tier--brief .gg-sources .gg-source-number {{ box-sizing: border-box !important; flex: 0 0 44px !important; width: 44px !important; min-width: 44px !important; max-width: 44px !important; padding: 0 2px !important; white-space: nowrap !important; }}
  body.gg-tier--brief .gg-source-content {{ flex: 1 1 0 !important; min-width: 0 !important; width: auto !important; }}
  body.gg-tier--brief .gg-section-body--grid,
  body.gg-tier--detailed .gg-section-body--grid,
  body.gg-tier--in_depth .gg-section-body--grid {{ display:block !important; grid-template-columns:none !important; columns:1 !important; gap:0 !important; width:100% !important; }}
  body.gg-tier--brief .gg-section-body--grid > *,
  body.gg-tier--detailed .gg-section-body--grid > *,
  body.gg-tier--in_depth .gg-section-body--grid > * {{ display:block !important; width:100% !important; max-width:100% !important; min-width:0 !important; }}
  .gg-table--wide th, .gg-table--wide td {{ padding:5px 6px; font-size:11px; line-height:1.25; }}
  .gg-section-body--grid > .gg-table-wrap,
  .gg-section-body--grid > .gg-chart-wrap,
  .gg-section-body--grid > .gg-figure,
  .gg-section-body--grid > .gg-diagram-wrap,
  .gg-section-body--grid > .gg-metrics,
  .gg-section-body--grid > .gg-stats-grid,
  .gg-section-body--grid > .gg-risk-grid,
  .gg-section-body--grid > .gg-timeline,
  .gg-section-body--grid > .gg-callout,
  .gg-section-body--grid > .gg-source-note {{ grid-column: 1 / -1 !important; }}
  .gg-sources-grid {{ grid-template-columns: minmax(0, 1fr) !important; }}
  .gg-source-url-only {{ font-size: 9pt; }}
  body.gg-tier--in_depth .gg-section-body > p {{ text-align: justify !important; }}
}}

/* V24 RESPONSIVE EDGE-BALANCED LAYOUT
   Browser/animated reports use the full available viewport with only a small,
   responsive safe gutter. This prevents fixed max-widths and prose measure caps
   from leaving conspicuous unused space on the right side. */
:root {{ --content-max: 100%; }}
html, body {{ width: 100%; max-width: 100%; min-width: 0; }}
body {{ overflow-x: hidden; }}
main, .gg-cover, .gg-report-nav, .gg-report-footer {{
  width: calc(100% - clamp(24px, 3.2vw, 56px)) !important;
  max-width: none !important;
  margin-left: auto !important;
  margin-right: auto !important;
  box-sizing: border-box !important;
}}
main {{ padding-top: clamp(16px, 2vw, 30px) !important; padding-bottom: clamp(24px, 3vw, 44px) !important; }}
.gg-cover {{ margin-top: clamp(12px, 1.8vw, 24px) !important; }}
.gg-title-band .gg-title, .gg-title, .gg-summary,
.gg-section-body, .gg-section-body > p,
.gg-section[data-section-type="timeline"] .gg-section-body,
.gg-section[data-section-type="findings"] .gg-section-body,
.gg-report-sections, .gg-composition--two_column .gg-report-sections,
.gg-composition--sidebar_main .gg-report-sections {{
  width: 100% !important; max-width: none !important; min-width: 0 !important;
}}
.gg-section-body > p {{ margin-left: 0 !important; margin-right: 0 !important; }}
.gg-section-body--grid {{
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 340px), 1fr)) !important;
  gap: clamp(12px, 1.6vw, 22px) !important;
  width: 100% !important; max-width: none !important;
}}
.gg-section-body--grid > * {{ min-width: 0 !important; max-width: 100% !important; }}
.gg-section-body--grid > .gg-table-wrap,
.gg-section-body--grid > .gg-chart-wrap,
.gg-section-body--grid > .gg-figure,
.gg-section-body--grid > .gg-diagram-wrap {{ width: 100%; }}
.gg-section--sidebar_main {{ grid-template-columns: minmax(200px, .28fr) minmax(0, 1fr); gap: clamp(14px, 2vw, 28px); }}
.gg-report-nav details {{ width: 100% !important; max-width: none !important; }}
.gg-report-footer {{ padding-top: 12px !important; padding-bottom: clamp(16px, 2vw, 28px) !important; }}
@media (max-width: 900px) {{
  .gg-section--sidebar_main {{ display: block !important; }}
  .gg-section--sidebar_main .gg-section-heading {{ position: static !important; }}
  .gg-cover--data_driven, .gg-summary-wrap--sidebar {{ grid-template-columns: minmax(0, 1fr) !important; }}
}}
@media (max-width: 600px) {{
  main, .gg-cover, .gg-report-nav, .gg-report-footer {{ width: calc(100% - 24px) !important; }}
  main {{ padding-top: 14px !important; padding-bottom: 24px !important; }}
  .gg-title-band {{ margin-top: 10px !important; padding-top: 14px !important; }}
  .gg-title-band .gg-title, .gg-title {{ font-size: clamp(28px, 8vw, 38px) !important; line-height: 1.08 !important; }}
  .gg-summary-card {{ padding: 16px !important; }}
  .gg-section-heading {{ grid-template-columns: 28px minmax(0,1fr) !important; gap: 9px !important; }}
  .gg-section-index {{ width: 26px !important; height: 26px !important; }}
  .gg-section-body--grid {{ grid-template-columns: minmax(0, 1fr) !important; gap: 12px !important; }}
  .gg-chart-wrap, .gg-table-wrap, .gg-figure {{ padding: 10px !important; }}
  .gg-report-index {{ padding: 14px !important; }}
  .gg-report-index ol {{ grid-template-columns: minmax(0, 1fr) !important; }}
}}
@media (prefers-reduced-motion: reduce) {{
  html {{ scroll-behavior: auto !important; }}
  *, *::before, *::after {{ animation-duration: .01ms !important; animation-iteration-count: 1 !important; transition-duration: .01ms !important; scroll-behavior: auto !important; }}
  .gg-reveal, [data-reveal] {{ opacity: 1 !important; transform: none !important; }}
}}


"""

_JS = """
document.addEventListener('DOMContentLoaded', function () {
  // Desktop navigation stays expanded; on narrow screens it becomes a
  // deliberate disclosure control and closes after a destination is chosen.
  var navDetails = document.querySelector('.gg-report-nav details');
  var compactNav = window.matchMedia('(max-width: 760px)');
  function syncNavDisclosure() { if (navDetails) navDetails.open = !compactNav.matches; }
  syncNavDisclosure();
  if (compactNav.addEventListener) compactNav.addEventListener('change', syncNavDisclosure);
  else if (compactNav.addListener) compactNav.addListener(syncNavDisclosure);
  document.querySelectorAll('.gg-report-nav a').forEach(function (link) {
    link.addEventListener('click', function () {
      if (compactNav.matches && navDetails) navDetails.open = false;
    });
  });

  // Scroll-reveal for anything marked data-reveal (paragraphs, headings,
  // charts, images, tables, stat cards).
  var revealEls = document.querySelectorAll('[data-reveal]');
  var io = new IntersectionObserver(function (entries) {
    entries.forEach(function (entry) {
      if (entry.isIntersecting) {
        entry.target.classList.add('gg-visible');
        io.unobserve(entry.target);
      }
    });
  }, { threshold: 0.12 });
  revealEls.forEach(function (el) { io.observe(el); });

  // Animated count-up for stat card values.
  var counters = document.querySelectorAll('.gg-count');
  var cio = new IntersectionObserver(function (entries) {
    entries.forEach(function (entry) {
      if (!entry.isIntersecting) return;
      var el = entry.target;
      cio.unobserve(el);
      var target = parseFloat(el.getAttribute('data-count-target'));
      var decimals = parseInt(el.getAttribute('data-count-decimals') || '0', 10);
      if (isNaN(target)) return;
      var duration = 1200, start = null;
      function step(ts) {
        if (!start) start = ts;
        var progress = Math.min((ts - start) / duration, 1);
        var eased = 1 - Math.pow(1 - progress, 3);
        var current = target * eased;
        el.textContent = decimals > 0
          ? current.toFixed(decimals).replace(/\\B(?=(\\d{3})+(?!\\d))/g, ',')
          : Math.round(current).toLocaleString('en-IN');
        if (progress < 1) requestAnimationFrame(step);
        else el.textContent = decimals > 0
          ? target.toFixed(decimals).replace(/\\B(?=(\\d{3})+(?!\\d))/g, ',')
          : Math.round(target).toLocaleString('en-IN');
      }
      requestAnimationFrame(step);
    });
  }, { threshold: 0.4 });
  counters.forEach(function (el) { cio.observe(el); });

  // Register one renderer-wide value-label plugin. It adapts the amount of
  // annotation to chart density: direct labels for compact charts and key
  // endpoints only for dense line/bubble/scatter charts. This makes values and
  // categories legible without making color the only encoding.
  if (window.Chart && !window.__ggValueLabelsRegistered) {
    var GGValueLabels = {
      id: 'ggValueLabels',
      afterDatasetsDraw: function(chart) {
        var type = chart.config.type;
        var ctx = chart.ctx;
        var labels = (chart.data && chart.data.labels) || [];
        var maxPoints = type === 'line' ? 12 : 14;
        if (type === 'doughnut' || type === 'pie') {
          var meta = chart.getDatasetMeta(0);
          var ds = (chart.data.datasets || [])[0] || {};
          var vals = ds.data || [];
          var total = vals.reduce(function(a,b){ var n = Number(b); return a + (isFinite(n) ? Math.max(0,n) : 0); }, 0) || 1;
          meta.data.forEach(function(el, i){
            if (i >= 12) return;
            var raw = Number(vals[i]);
            if (!isFinite(raw)) return;
            var pos = el.tooltipPosition();
            ctx.save(); ctx.fillStyle = '#24304d'; ctx.font = '600 9px system-ui'; ctx.textAlign = 'center';
            ctx.fillText((100 * raw / total).toFixed(1) + '%', pos.x, pos.y);
            ctx.restore();
          });
          return;
        }
        (chart.data.datasets || []).forEach(function(ds, di){
          // A bullet chart's target is a narrow floating range, not a data
          // value. Labelling its width produces misleading values such as
          // "6.5" beside a 500-GW target tick.
          if (chart.options.indexAxis === 'y' && String(ds.label || '').toLowerCase() === 'target') return;
          var meta = chart.getDatasetMeta(di);
          if (!meta || meta.hidden) return;
          var count = meta.data.length;
          meta.data.forEach(function(el, i){
            if (count > maxPoints && i !== 0 && i !== count - 1) return;
            var raw = ds.data[i];
            var val = Array.isArray(raw) ? (Number(raw[1]) - Number(raw[0])) : (raw && typeof raw === 'object' ? Number(raw.y) : Number(raw));
            if (!isFinite(val) || val === 0) return;
            var pos = el.tooltipPosition ? el.tooltipPosition() : {x:0,y:0};
            ctx.save(); ctx.fillStyle = '#2b344d'; ctx.font = '600 9px system-ui'; ctx.textAlign = 'center';
            var txt = Math.abs(val) >= 1000 ? (val/1000).toFixed(1) + 'k' : (Number.isInteger(val) ? String(val) : val.toFixed(1));
            ctx.fillText(txt, pos.x, pos.y - 7); ctx.restore();
          });
        });
      }
    };
    Chart.register(GGValueLabels);
    window.__ggValueLabelsRegistered = true;
  }

  // Bullet-chart targets are markers on the numerical x scale, not narrow
  // floating bars. Draw a crisp vertical tick across each category row so
  // the marker stays legible at every responsive width.
  if (typeof Chart !== 'undefined' && !window.__ggBulletTargetsRegistered) {
    var GGBulletTargets = {
      id: 'ggBulletTargets',
      afterDatasetsDraw: function(chart) {
        if (!chart.options || chart.options.indexAxis !== 'y') return;
        var targetIndex = (chart.data.datasets || []).findIndex(function(ds) {
          return String(ds.label || '').toLowerCase().indexOf('target') >= 0;
        });
        var actualIndex = (chart.data.datasets || []).findIndex(function(ds) {
          return String(ds.label || '').toLowerCase().indexOf('actual') >= 0;
        });
        if (targetIndex < 0 || actualIndex < 0 || !chart.scales.x) return;
        var targetData = chart.data.datasets[targetIndex].data || [];
        var actualMeta = chart.getDatasetMeta(actualIndex);
        if (!actualMeta || !actualMeta.data) return;
        var ctx = chart.ctx;
        ctx.save(); ctx.strokeStyle = '#243746'; ctx.lineWidth = 2;
        targetData.forEach(function(raw, i) {
          var lo = Array.isArray(raw) ? Number(raw[0]) : Number(raw);
          var hi = Array.isArray(raw) ? Number(raw[1]) : Number(raw);
          var target = (lo + hi) / 2;
          var actualBar = actualMeta.data[i];
          if (!isFinite(target) || !actualBar) return;
          var x = chart.scales.x.getPixelForValue(target);
          var half = Math.max(8, Number(actualBar.height || 20) * 0.55);
          ctx.beginPath(); ctx.moveTo(x, actualBar.y - half); ctx.lineTo(x, actualBar.y + half); ctx.stroke();
        });
        ctx.restore();
      }
    };
    Chart.register(GGBulletTargets);
    window.__ggBulletTargetsRegistered = true;
  }

  // Chart.js instances, deferred until each canvas scrolls into view so
  // charts animate in as the reader reaches them rather than all firing
  // at page load.
  // v93 editorial chart chrome: Inter type, warm muted ticks and hairline grid
  // on the light report canvas. Presentation only — data and series untouched.
  function ggEditorialChart(cfg) {
    if (!document.body || !document.body.classList.contains('gg-editorial') || !cfg) return cfg;
    try {
      if (window.Chart && Chart.defaults) {
        Chart.defaults.font.family = "Inter, system-ui, -apple-system, 'Segoe UI', Arial, sans-serif";
        Chart.defaults.font.size = 12;
        Chart.defaults.color = '#6F6B63';
      }
      var opts = cfg.options = cfg.options || {};
      var scales = opts.scales || {};
      Object.keys(scales).forEach(function (k) {
        var sc = scales[k] || {};
        sc.ticks = sc.ticks || {}; sc.ticks.color = '#6F6B63';
        if (sc.grid && sc.grid.display !== false) { sc.grid.color = '#ECE8E0'; }
        if (sc.title) { sc.title.color = '#4A4843'; sc.title.font = { weight: '600', size: 12 }; }
        sc.border = Object.assign({}, sc.border || {}, { color: '#E1DDD5' });
      });
      var plugins = opts.plugins = opts.plugins || {};
      if (plugins.legend && plugins.legend.labels) { plugins.legend.labels.color = '#4A4843'; }
      if (plugins.tooltip !== false) {
        plugins.tooltip = Object.assign({ backgroundColor: '#1C1C1A', titleColor: '#FFFFFF', bodyColor: '#F3EFE6', padding: 10, cornerRadius: 2 }, plugins.tooltip || {});
      }
      opts.maintainAspectRatio = false;
    } catch (e) { /* keep the original config */ }
    return cfg;
  }

  (window.__ggCharts || []).forEach(function (c) {
    c.config = ggEditorialChart(c.config);
    var canvas = document.getElementById(c.id);
    if (!canvas) return;
    var chartIO = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        chartIO.unobserve(canvas);
        new Chart(canvas.getContext('2d'), c.config);
      });
    }, { threshold: 0.2 });
    chartIO.observe(canvas);
  });
});
"""


def _safe_plain_url(value: object) -> str:
    url = str(value or "").strip()
    if not re.match(r"^https?://", url, re.IGNORECASE):
        return ""
    return url


def _is_probable_image_asset_url(value: object) -> bool:
    """Return True when a URL looks like an image asset/CDN rather than a source webpage."""
    raw = str(value or "").strip()
    if not raw:
        return False
    try:
        parsed = urlparse(raw)
        path = (parsed.path or "").lower()
        host = (parsed.netloc or "").lower()
        if re.search(r"\.(?:png|jpe?g|webp|gif|svg|avif|bmp|tiff?)(?:$|[?#])", path):
            return True
        # Common image-delivery/proxy hosts are assets unless explicit sourceUrl
        # metadata has been separately identified by the caller.
        return any(token in host for token in ("s.yimg.com", "images.unsplash.com", "miro.medium.com", "cdn.", "images.", "img."))
    except Exception:
        return False


def _safe_url_host(value: object) -> str:
    """Return a URL hostname/netloc without allowing malformed source/image URLs
    to abort an otherwise valid HTML/PDF report render."""
    try:
        url = _safe_plain_url(value)
        if not url:
            return ""
        return urlparse(url).netloc or ""
    except (TypeError, ValueError):
        return ""


def _display_source_host(value: object) -> str:
    """Prefer the meaningful origin inside common CDN/proxy image URLs.

    Search-result images are sometimes delivered through a CDN (for example a
    Yahoo image proxy) while the path contains the original publisher URL.
    Showing the CDN hostname makes a polished report look like it cites the
    infrastructure rather than the source. If no nested URL exists, fall back
    to the safe outer hostname.
    """
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        decoded = unquote(raw)
        nested = re.search(r"https?://([^/\s?&#]+)", decoded, re.IGNORECASE)
        if nested:
            return nested.group(1)
    except Exception:
        pass
    return _safe_url_host(raw)


def _slug_text(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _split_report_into_sections(report: str) -> list[dict]:
    """Split report markdown at H2 boundaries while preserving all content."""
    lines = report.replace("\r\n", "\n").split("\n")
    sections: list[dict] = []
    current_title = ""
    current: list[str] = []
    for line in lines:
        if re.match(r"^##\s+", line.strip()):
            if current_title or any(x.strip() for x in current):
                sections.append({"title": current_title or "Report", "body": "\n".join(current).strip()})
            current_title = re.sub(r"^##\s+", "", line.strip()).strip()
            current = []
        else:
            current.append(line)
    if current_title or any(x.strip() for x in current):
        sections.append({"title": current_title or "Report", "body": "\n".join(current).strip()})
    return [s for s in sections if s["body"] or s["title"]]


def _fallback_presentation(report: str, title: str, summary: str, key_stats: list, charts: list, report_tier: str = "detailed") -> ReportPresentationSpec:
    """Create a safe composition for legacy payloads that lack a presentation."""
    actual = _split_report_into_sections(report)
    planned = []
    for i, sec in enumerate(actual):
        kind = "narrative"
        lower = _slug_text(sec["title"])
        if any(x in lower for x in ("financial", "revenue", "profit", "valuation")):
            kind = "financials"
        elif any(x in lower for x in ("risk", "threat", "downside")):
            kind = "risk_assessment"
        elif any(x in lower for x in ("timeline", "chronology", "history")):
            kind = "timeline"
        elif any(x in lower for x in ("comparison", "versus", "vs")):
            kind = "comparison"
        elif any(x in lower for x in ("methodology", "methods")):
            kind = "methodology"
        elif any(x in lower for x in ("findings", "results")):
            kind = "findings"
        planned.append({
            "id": f"section-{i + 1}", "title": sec["title"] or f"Section {i + 1}",
            "section_type": kind, "layout": "single_column", "density": "standard",
            "emphasis": "normal", "order": i, "blocks": [],
        })
    if not planned:
        planned = [{"id": "overview", "title": "Report", "section_type": "narrative", "order": 0, "blocks": []}]
    preset = {
        "domain": "generic",
        # Legacy/fallback payloads do not get an invented cover. A cover is
        # only rendered when the planner explicitly requests one.
        "cover": {
            "enabled": False,
            "title": title or "Research Report",
            "subtitle": "",
            "treatment": "classic",
            "show_date": True,
        },
        "executive_summary": {"placement": "after_cover" if summary else "none", "heading": "Executive Summary", "key_metrics": []},
        "sections": planned,
        "source_appendix": {"placement": "end_of_report", "group_by_section": False, "include_appendix": True},
        "default_layout": "single_column", "default_density": "standard",
    }
    spec, _ = ReportPresentationSpec.from_llm_output(preset)
    return spec


def _normalise_presentation(presentation: object, report: str, title: str, summary: str, key_stats: list, charts: list, report_tier: str = "detailed") -> tuple[ReportPresentationSpec, list[str]]:
    if isinstance(presentation, ReportPresentationSpec):
        try:
            presentation.validate_strict()
            return presentation, []
        except Exception:
            pass
    if not isinstance(presentation, dict):
        return _fallback_presentation(report, title, summary, key_stats, charts, report_tier), ["No valid presentation supplied; adaptive fallback used."]
    spec, warnings = ReportPresentationSpec.from_llm_output(presentation)
    if not spec.sections:
        fallback = _fallback_presentation(report, title, summary, key_stats, charts, report_tier)
        warnings.append("Presentation had no valid sections; legacy-safe fallback sections used.")
        return fallback, warnings
    return spec, warnings


def _render_metrics(items: list[dict], source_registry: list[dict] | None = None, limit: int = 20) -> str:
    if not items:
        return ""
    cards = []
    for item in items[:max(1, min(int(limit or 20), 20))]:
        label = _inline_md(str(item.get("label", "")), source_registry)
        value = _inline_md(str(item.get("value", "")), source_registry)
        unit = html.escape(str(item.get("unit", "")))
        change = item.get("change_pct")
        trend = str(item.get("trend", "unknown"))
        change_html = ""
        if change is not None:
            cls = "gg-change-down" if trend == "down" else "gg-change-up" if trend == "up" else ""
            change_html = f'<div class="gg-metric-change {cls}">{_inline_md(str(change), source_registry)}%</div>'
        cards.append(
            f'<article class="gg-metric"><div class="gg-metric-value">{value}{(" " + unit) if unit else ""}</div>'
            f'<div class="gg-metric-label">{label}</div>{change_html}</article>'
        )
    return f'<div class="gg-block gg-metrics">{"".join(cards)}</div>'


def _render_table_block(block: dict, source_registry: list[dict] | None = None, recent_source_ids: list[str] | None = None, force_source_note: bool = False) -> str:
    columns = block.get("columns") or []
    rows = block.get("rows") or []
    if not columns or not rows:
        return ""
    return _render_table_markup(
        columns, rows, source_registry,
        block.get("source") or block.get("sources") or block.get("sourceIds"),
        recent_source_ids, force_source_note, block.get("title"),
        block.get("method") or block.get("methodology"),
    )


def _render_timeline_block(block: dict, source_registry: list[dict] | None = None, recent_source_ids: list[str] | None = None, force_source_note: bool = False) -> str:
    events = block.get("events") or []
    if not events:
        return ""
    items = []
    for event in events[:200]:
        items.append(
            '<article class="gg-block gg-timeline-item">'
            f'<div class="gg-timeline-date">{_inline_md(str(event.get("date_label", "")), source_registry)}</div>'
            f'<div><h3 class="gg-timeline-title">{_inline_md(str(event.get("title", "")), source_registry)}</h3>'
            f'<p class="gg-timeline-desc">{_inline_md(str(event.get("description", "")), source_registry)}</p></div></article>'
        )
    title = html.escape(str(block.get("title") or ""))
    source_note = _render_source_note(
        block.get("source") or block.get("sources") or block.get("sourceIds"),
        source_registry, recent_source_ids,
        block.get("method") or block.get("methodology"),
        force=force_source_note,
    )
    return f'<div class="gg-block gg-timeline">{f"<h3>{title}</h3>" if title else ""}{"".join(items)}{source_note}</div>'


def _render_comparison_block(block: dict, source_registry: list[dict] | None = None, recent_source_ids: list[str] | None = None, force_source_note: bool = False) -> str:
    items = block.get("items") or []
    dims = block.get("dimensions") or []
    if not items:
        return ""
    columns = dims or sorted({k for item in items for k in (item.get("values") or {}).keys()})
    if not columns:
        return ""
    rows = [[item.get("name", ""), *[(item.get("values") or {}).get(column, "") for column in columns]] for item in items[:100]]
    return _render_table_markup(
        ["Item", *columns], rows, source_registry,
        block.get("source") or block.get("sources") or block.get("sourceIds"),
        recent_source_ids, force_source_note, block.get("title"),
        block.get("method") or block.get("methodology"),
    )


def _render_risk_block(block: dict, source_registry: list[dict] | None = None, recent_source_ids: list[str] | None = None, force_source_note: bool = False) -> str:
    risks = block.get("risks") or []
    if not risks:
        return ""
    cards = []
    for risk in risks[:200]:
        cards.append(
            '<article class="gg-risk">'
            f'<div class="gg-risk__name">{_inline_md(str(risk.get("name", "")), source_registry)}</div>'
            f'<div class="gg-risk__meta">Likelihood: {html.escape(str(risk.get("likelihood", "medium")))} · Impact: {html.escape(str(risk.get("impact", "medium")))}</div>'
            f'<div class="gg-risk__mitigation">{_inline_md(str(risk.get("mitigation", "")), source_registry)}</div>'
            '</article>'
        )
    source_note = _render_source_note(
        block.get("source") or block.get("sources") or block.get("sourceIds"),
        source_registry, recent_source_ids,
        block.get("method") or block.get("methodology"),
        force=force_source_note,
    )
    return f'<div class="gg-block gg-risk-grid">{"".join(cards)}{source_note}</div>'


def _render_evidence_block(block: dict, source_registry: list[dict] | None = None, recent_source_ids: list[str] | None = None, force_source_note: bool = False) -> str:
    body = _inline_md(str(block.get("body", "")), source_registry)
    if not body:
        return ""
    tone = str(block.get("tone", "info"))
    heading = html.escape(str(block.get("heading", "")))
    source = _render_source_note(block.get("source_label") or block.get("source") or block.get("sources"), source_registry, recent_source_ids, force=force_source_note)
    return (
        f'<aside class="gg-block gg-callout gg-callout--{html.escape(tone, quote=True)}">'
        f'{f"<h4>{heading}</h4>" if heading else ""}<p>{body}</p>'
        f'{source}</aside>'
    )


def _render_bullets_block(block: dict, source_registry: list[dict] | None = None) -> str:
    items = block.get("items") or block.get("bullets") or []
    if not items:
        return ""
    lis = []
    for item in items[:200]:
        if isinstance(item, dict):
            text = item.get("text") or item.get("label") or item.get("body") or ""
        else:
            text = item
        text = _inline_md(str(text), source_registry)
        if text:
            lis.append(f'<li>{text}</li>')
    if not lis:
        return ""
    return '<div class="gg-block gg-bullets"><ul class="gg-list">' + "".join(lis) + '</ul></div>'


def _render_chart_spec_block(block: dict, idx: int, theme: dict | None = None, source_registry: list[dict] | None = None, recent_source_ids: list[str] | None = None, force_source_note: bool = False) -> str:
    chart_type = str(block.get("chart_type") or "line")
    labels = block.get("x_labels") or []
    series = block.get("series") or []
    if not labels or not series:
        return ""
    chart = {
        "type": chart_type, "title": block.get("title", ""),
        "source": block.get("source") or block.get("sources") or block.get("sourceIds"),
        "method": block.get("method") or block.get("methodology"),
        "rationale": block.get("rationale"), "takeaway": block.get("takeaway"),
    }
    converted = []
    for series_item in series[:10]:
        vals = series_item.get("values") or []
        converted.append({"name": series_item.get("name", "Series"), "data": [{"label": str(label), "value": vals[i] if i < len(vals) else 0} for i, label in enumerate(labels)]})
    chart["series"] = converted
    return _render_chart_block(chart, idx, theme, source_registry, recent_source_ids=recent_source_ids, force_source_note=force_source_note)


def _render_structured_blocks(blocks: list, charts: list, key_stats: list, theme: dict | None, chart_cursor: int, source_registry: list[dict] | None = None, recent_source_ids: list[str] | None = None, force_source_notes: bool = False, metric_limit: int = 20, suppress_fallback_metrics: bool = False, rendered_metric_fingerprints: set[str] | None = None) -> tuple[str, int, bool]:
    rendered = []
    meaningful = False
    metric_seen = rendered_metric_fingerprints if rendered_metric_fingerprints is not None else set()
    for raw in blocks or []:
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("kind", "")).lower()
        html_block = ""
        if kind == "metrics":
            items = raw.get("items") or []
            if items:
                fingerprint = "|".join(
                    f"{str(item.get('label') or item.get('name') or '').strip().casefold()}={str(item.get('value') or '').strip().casefold()}"
                    for item in items if isinstance(item, dict)
                )
                if fingerprint and fingerprint in metric_seen:
                    continue
                if fingerprint:
                    metric_seen.add(fingerprint)
                html_block = _render_metrics(items, source_registry, metric_limit)
            elif key_stats and not suppress_fallback_metrics:
                fingerprint = "|".join(
                    f"{str(item.get('label') or '').strip().casefold()}={str(item.get('value') or '').strip().casefold()}"
                    for item in (key_stats[:metric_limit] if isinstance(key_stats, list) else []) if isinstance(item, dict)
                )
                if fingerprint and fingerprint in metric_seen:
                    continue
                if fingerprint:
                    metric_seen.add(fingerprint)
                html_block = _render_key_stats(key_stats, source_registry, metric_limit)
        elif kind == "prose" and raw.get("body"):
            html_block = f'<div class="gg-block">{_markdown_to_html(str(raw.get("body")), charts, [], theme, source_registry, force_source_notes)}</div>'
        elif kind == "bullets":
            html_block = _render_bullets_block(raw, source_registry)
        elif kind == "table":
            html_block = _render_table_block(raw, source_registry, recent_source_ids, force_source_notes)
        elif kind == "timeline":
            html_block = _render_timeline_block(raw, source_registry, recent_source_ids, force_source_notes)
        elif kind == "comparison":
            html_block = _render_comparison_block(raw, source_registry, recent_source_ids, force_source_notes)
        elif kind == "risk_matrix":
            html_block = _render_risk_block(raw, source_registry, recent_source_ids, force_source_notes)
        elif kind == "evidence":
            html_block = _render_evidence_block(raw, source_registry, recent_source_ids, force_source_notes)
        elif kind == "chart":
            html_block = _render_chart_spec_block(raw, chart_cursor + 1, theme, source_registry, recent_source_ids, force_source_notes)
            if html_block:
                chart_cursor += 1
        if html_block:
            meaningful = True
            rendered.append(html_block)
    return "\n".join(rendered), chart_cursor, meaningful


def _match_planned_sections(spec: ReportPresentationSpec, actual: list[dict]) -> list[tuple[dict, dict]]:
    """Match presentation hints to real report sections without mislabeling content.

    The generated markdown is the source of truth for section identity and order.
    A presentation plan may change layout/density, but it must never attach the
    wrong body to a heading. Exact title matches are preferred; fuzzy matches are
    allowed only when they are clearly similar. Unmatched real sections are kept
    in their original order with a neutral presentation plan.
    """
    planned = sorted(spec.to_dict().get("sections", []), key=lambda s: int(s.get("order", 0)))
    remaining_plans = list(range(len(planned)))
    used_actual: set[int] = set()
    matches_by_actual: dict[int, tuple[dict, dict]] = {}

    def words(text: str) -> set[str]:
        stop = {"the", "and", "of", "for", "to", "in", "on", "vs", "a", "an", "with", "by", "from"}
        return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in stop and len(w) > 2}

    # Pass 1: exact normalized title matches.
    for ai, item in enumerate(actual):
        at = _slug_text(item.get("title"))
        if not at:
            continue
        chosen = None
        for pi in remaining_plans:
            pt = _slug_text(planned[pi].get("title"))
            if pt and pt == at:
                chosen = pi
                break
        if chosen is not None:
            remaining_plans.remove(chosen)
            used_actual.add(ai)
            matches_by_actual[ai] = (planned[chosen], item)

    # Pass 2: only strong fuzzy matches. Never force an unrelated plan title onto
    # a real section (the defect that previously produced "Risks & Near-Term
    # Outlook" above an Executive Takeaway body).
    for ai, item in enumerate(actual):
        if ai in used_actual:
            continue
        at = _slug_text(item.get("title"))
        if not at:
            continue
        aw = words(at)
        candidates = []
        for pi in remaining_plans:
            pt = _slug_text(planned[pi].get("title"))
            if not pt:
                continue
            pw = words(pt)
            overlap = len(aw & pw) / max(1, min(len(aw), len(pw)))
            ratio = SequenceMatcher(None, at, pt).ratio()
            score = max(overlap, ratio)
            if score >= 0.68:
                candidates.append((score, pi))
        if candidates:
            candidates.sort(reverse=True)
            score, chosen = candidates[0]
            if len(candidates) == 1 or score - candidates[1][0] >= 0.08:
                remaining_plans.remove(chosen)
                used_actual.add(ai)
                matches_by_actual[ai] = (planned[chosen], item)

    # Pass 3: preserve every real section that the planner failed to map. The
    # fallback plan controls only presentation primitives; the actual title/body
    # remain authoritative.
    for ai, item in enumerate(actual):
        if ai in matches_by_actual:
            continue
        fallback = {
            "id": f"auto-{ai+1}",
            "title": item.get("title") or f"Section {ai+1}",
            "order": ai + 1,
            "layout": "auto",
            "density": "standard",
            "emphasis": "standard",
            "section_type": "narrative",
            "blocks": [],
        }
        matches_by_actual[ai] = (fallback, item)

    return [matches_by_actual[i] for i in sorted(matches_by_actual)]

def _adaptive_layout_for_section(plan: dict, actual: dict, model_section: ReportSectionModel | None, tier: str) -> str:
    """Resolve the concrete web/print layout from the actual evidence mix.

    ``plan.layout`` is treated as a hint. ``auto`` lets the renderer adapt the
    composition without introducing a hard-coded report section template.
    """
    requested = str(plan.get("layout") or "auto").lower()
    sec = model_section
    body = str(actual.get("body") or "")
    charts = sec.chart_count if sec else len(re.findall(r"\[CHART_\d+\]", body, re.I))
    images = sec.image_count if sec else len(re.findall(r"\[(?:WEB_IMG|FILE_IMG|PAGE_IMG)_\d+\]", body, re.I))
    tables = sec.table_count if sec else len(re.findall(r"^\|.*\|$", body, re.M))
    bullets = sec.bullet_count if sec else len(re.findall(r"^(?:[-•*]|\d+[.)])\s+", body, re.M))
    words = sec.word_count if sec else len(re.findall(r"\S+", body))
    # Tables and single charts need predictable full-width print geometry. Do
    # not let an LLM-selected two-column layout squeeze them into narrow columns,
    # which causes split tables, orphan rows and large white gaps. Multiple
    # charts can still use the adaptive grid when the section is compact.
    if tables >= 1 or charts == 1:
        return "single_column"
    if charts >= 2 and words <= 650:
        return "grid"
    if requested != "auto":
        return requested
    title = str(plan.get("title") or actual.get("title") or "").lower()
    section_type = str(plan.get("section_type") or "").lower()
    block_kinds = {str(b.get("kind") or b.get("type") or "").lower() for b in (plan.get("blocks") or []) if isinstance(b, dict)}
    if section_type == "metrics_dashboard" or block_kinds & {"metrics", "metrics_dashboard", "kpi_grid", "stats"}:
        return "single_column"

    # Large tables and any single substantive chart get the full readable
    # width. A lone chart should not split the section heading from its text.
    if tables >= 1:
        return "single_column"
    if charts == 1:
        return "single_column"
    if charts >= 2 and words <= 650:
        return "grid"
    if images >= 2 and words <= 700:
        return "grid"
    if bullets >= 6 and words <= 500:
        return "two_column"
    if any(k in title for k in ("comparison", "benchmark", "risk", "timeline")) and words <= 750:
        return "grid"
    if words <= 260 and bullets >= 3:
        return "two_column"
    # Deep reports benefit from a little more visual pacing, but still avoid
    # forcing columns on long narrative sections.
    if tier == "in_depth" and words <= 420 and (charts or bullets or images):
        return "two_column"
    return "single_column"


def _adaptive_section_density(plan: dict, model_section: ReportSectionModel | None) -> str:
    requested = str(plan.get("density") or "auto").lower()
    if requested != "auto":
        return requested
    if not model_section:
        return "standard"
    if model_section.word_count > 1100 or model_section.table_count >= 2:
        return "dense"
    if model_section.word_count < 240 and not model_section.is_data_rich:
        return "sparse"
    return "standard"


def _chart_size_class(chart: dict) -> str:
    """Select compact/standard/large chart height from evidence cardinality."""
    labels = []
    for series in (chart.get("series") or []):
        if not isinstance(series, dict):
            continue
        for point in (series.get("data") or []):
            if isinstance(point, dict):
                label = str(point.get("label", ""))
            else:
                label = str(point or "")
            if label and label not in labels:
                labels.append(label)
    n = len(labels)
    if n <= 5:
        return "compact"
    if n >= 14:
        return "large"
    return "standard"


def _chart_has_renderable_payload(chart: object) -> bool:
    """Return whether a structured chart contains real data to draw.

    This is a renderer safety check, not a tier-density pass: sparse but valid
    charts remain renderable and are separately marked by the quality audit.
    Empty data must never become a title-only / empty chart canvas in the PDF.
    """
    if not isinstance(chart, dict):
        return False
    chart_type = normalize_chart_type(chart.get("type") or chart.get("chartType") or "bar")
    if chart_type == "table":
        return len(chart.get("columns") or []) >= 2 and len(chart.get("rows") or []) >= 2
    series = chart.get("series") or []
    point_values: list[float] = []
    total_points = 0
    for item in series:
        if not isinstance(item, dict):
            continue
        points = item.get("data") or []
        for point in points:
            if not isinstance(point, dict):
                continue
            total_points += 1
            raw = point.get("value", point.get("y", point.get("count")))
            try:
                if raw is not None and str(raw).strip() != "":
                    point_values.append(float(str(raw).replace(",", "")))
            except (TypeError, ValueError):
                continue
    if chart_type in {"pie", "doughnut", "polar_area"}:
        # Composition charts need at least two positive, numeric slices.
        return len(point_values) >= 2 and sum(max(0.0, value) for value in point_values) > 0
    if chart_type in {"gauge", "bullet"}:
        return bool(point_values)
    return total_points >= 2 and len(point_values) >= 2


def _recover_chart_placeholders_from_titles(report_text: str, charts: list) -> str:
    """Turn a stranded chart-title line back into its structured placeholder.

    Some section-edit responses remove `[CHART_n]` but leave the chart title as
    ordinary text. Matching an exact standalone title lets the shared renderer
    put the existing structured chart back at that position without fabricating
    data or adding another semantic section.
    """
    text = str(report_text or "")
    # Normalize image-Markdown placeholders at the renderer boundary too. Some
    # provider or section-edit responses emit ![Chart title](CHART_1) instead of
    # [CHART_1]; if this escapes the route's earlier normalizer, Chromium prints
    # the literal Markdown and moves the actual chart away from the intended spot.
    text = re.sub(
        r"!\[[^\]\n]*\]\(\s*<?\s*\[?CHART_(\d+)\]?\s*>?\s*\)",
        lambda match: f"[CHART_{match.group(1)}]",
        text,
        flags=re.IGNORECASE,
    )
    for index, chart in enumerate(charts or [], start=1):
        if not isinstance(chart, dict):
            continue
        if re.search(rf"\[CHART_{index}\]", text, flags=re.IGNORECASE):
            continue
        title = str(chart.get("title") or "").strip()
        if not title or re.fullmatch(r"(?:chart|graph|figure)\s*\d*", title, re.IGNORECASE):
            continue
        line_pattern = re.compile(
            r"(?im)^[ \t]*(?:#{1,6}\s*)?(?:\*\*|__)?\s*"
            + re.escape(title)
            + r"\s*(?:\*\*|__)?\s*:?\s*$"
        )
        text, replaced = line_pattern.subn(f"[CHART_{index}]", text, count=1)
        if replaced:
            log.info("HTML report: restored missing chart placeholder from title — chart=%d title=%r", index, title[:120])
    return text


def _render_sources_appendix(
    sources: object,
    placement: str = "end_of_report",
    include_appendix: bool = False,
    source_registry: list[dict] | None = None,
    report_tier: str = "detailed",
    cited_source_ids: set[str] | None = None,
) -> str:
    """Render canonical sources as a compact, numbered, one-line list.

    Keep stable source IDs in the DOM so inline [S#] citations still jump to the
    correct source. Client-facing appendix rows show only a sequence number,
    source title, and short link label. Detailed provenance stays in internal
    audit metadata instead of adding repeated caution paragraphs to each row.
    """
    manifest = source_registry if source_registry is not None else build_source_registry(sources)
    if not manifest or placement == "none":
        return ""
    display_manifest = list(manifest)
    if str(report_tier).lower() == "brief":
        cited = {str(x).upper() for x in (cited_source_ids or set())}
        if cited:
            display_manifest = [src for src in display_manifest if str(src.get("id") or "").upper() in cited]
    if not display_manifest:
        return ""

    lines: list[str] = []
    for index, source in enumerate(display_manifest, start=1):
        # When a direct publisher fetch established a canonical page, make that
        # provenance page the preferred click target while retaining the search/
        # requested URL in the manifest for audit traceability.
        url = _safe_plain_url(source.get("publisher_page_url") or source.get("url"))
        source_id = str(source.get("id") or f"S{index}").strip().upper()
        title = str(source.get("title") or source.get("publisher") or url or "Untitled source").strip()
        safe_id = html.escape(source_id, quote=True)
        safe_title = html.escape(title, quote=True)
        publication_date = str(source.get("publication_date") or "").strip()
        date_markup = (
            f'<span class="gg-source-date">Published: {html.escape(publication_date, quote=True)}</span>'
            if publication_date else ""
        )
        if url:
            safe_url = html.escape(url, quote=True)
            # Print exports must preserve source provenance in visible/extractable
            # text, not only as an href on the generic word "link". This makes
            # the exact citation destination auditable from a PDF and still keeps
            # the URL clickable in the HTML preview. The URL-only class ensures
            # long URLs can wrap instead of overflowing the source appendix.
            link_markup = (
                f'<a class="gg-source-link gg-source-url-only" href="{safe_url}" '
                f'target="_blank" rel="noopener noreferrer">{safe_url}</a>'
            )
        else:
            link_markup = '<span class="gg-source-unavailable">(No public URL supplied)</span>'
        # Keep client-facing source lists compact. Per-source retrieval failures
        # are retained in backend diagnostics and publication-audit metadata, not
        # repeated verbatim beside every citation in the exported report.
        metadata_parts = [part for part in (date_markup,) if part]
        metadata_markup = f'<span class="gg-source-metadata">{" · ".join(metadata_parts)}</span>' if metadata_parts else ""
        lines.append(
            f'<li id="source-{safe_id}" class="gg-source-line">'
            f'<span class="gg-source-id">[{safe_id}]</span> '
            f'<span class="gg-source-title-text">{safe_title}</span> '
            f'{link_markup}'
            f'{metadata_markup}'
            f'</li>'
        )
    count = len(display_manifest)
    heading = "Appendix — Data Sources" if placement == "appendix" or include_appendix else "Data Sources"
    print_break = '<div class="gg-print-page-break" aria-hidden="true"></div>' if str(report_tier).lower() == "brief" else ""
    return (
        f'{print_break}<section id="data-sources" class="gg-sources gg-reveal" data-reveal data-source-placement="{html.escape(placement, quote=True)}" data-source-count="{count}">'
        f'<h2>{heading}</h2>'
        f'<p class="gg-sources-count">Total sources: {count}</p>'
        f'<ol class="gg-source-list">{"".join(lines)}</ol></section>'
    )

def _render_cover(spec_dict: dict, safe_title: str, safe_summary: str, date_str: str, key_stats: list, source_registry: list[dict] | None = None) -> str:
    cover = spec_dict.get("cover") or {}
    if not cover.get("enabled", True):
        return ""
    treatment = str(cover.get("treatment") or "minimal")
    title = html.escape(str(cover.get("title") or "")) or safe_title
    # A cover is an explicit presentation choice. Never silently copy the
    # report summary onto the cover; that created a duplicate Executive Summary
    # when the same summary was also placed in the body. The planner may choose
    # a subtitle explicitly when it genuinely belongs on the cover.
    subtitle = str(cover.get("subtitle") or "")
    subtitle_html = _inline_md(subtitle, source_registry)
    show_date = bool(cover.get("show_date", True))
    aside = ""
    if treatment == "data_driven" and key_stats:
        first = key_stats[0] if isinstance(key_stats[0], dict) else {}
        aside = (
            '<aside class="gg-cover__aside"><p class="gg-cover__aside-label">Key signal</p>'
            f'<div class="gg-metric-value">{html.escape(str(first.get("value", "")))}</div>'
            f'<div class="gg-metric-label">{html.escape(str(first.get("label", "")))}</div></aside>'
        )
    return (
        f'<header id="report-cover" class="gg-cover gg-cover--{html.escape(treatment, quote=True)}">'
        '<div><p class="gg-eyebrow">Growth Gradual · Research Intelligence</p>'
        f'<h1 class="gg-title">{title}</h1>'
        f'{f"<p class=\"gg-summary\">{subtitle_html}</p>" if subtitle_html else ""}'
        f'{f"<div class=\"gg-date\">Prepared · {date_str}</div>" if show_date else ""}</div>'
        f'{aside}</header>'
    )


_STATUS_DRAFT_RE = re.compile(r"^\W{0,4}(?:draft\b|not\s+approved\s+for|preliminary\s+draft|this\s+(?:report|draft)\s+(?:is|has)\s+not\s+(?:been\s+)?approved)", re.IGNORECASE)
_STATUS_WARNING_RE = re.compile(r"^\W{0,4}(?:warning|caution|data\s+limitation|evidence\s+limitation)\b\s*[:—–-]", re.IGNORECASE)


def _status_banner_label(text: object) -> tuple[str, str] | None:
    """Classify a blockquote line as a publication-status notice, if it is one."""
    value = str(text or "").strip()
    if _STATUS_DRAFT_RE.match(value):
        return ("draft", "Draft · not publication-approved")
    if _STATUS_WARNING_RE.match(value):
        return ("warning", "Notice")
    return None


def _extract_leading_status_banner(report: str) -> tuple[str, tuple[str, str, str] | None]:
    """Split a leading draft/notice blockquote from the report body.

    Returns (report_without_banner, (kind, label, text)) or (report, None).
    Only a blockquote that precedes all other content qualifies.
    """
    lines = str(report or "").split("\n")
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    start = i
    quoted = []
    while i < len(lines) and lines[i].lstrip().startswith(">"):
        quoted.append(lines[i].lstrip()[1:].strip())
        i += 1
    text = " ".join(q for q in quoted if q).strip()
    label = _status_banner_label(text) if text else None
    if not label:
        return report, None
    rest = "\n".join(lines[:start] + lines[i:]).lstrip("\n")
    return rest, (label[0], label[1], text)


_KEEP_BLOCK_START_RE = re.compile(
    r'^\s*<(div|figure)\b[^>]*class="[^"]*\b(gg-chart-wrap|gg-figure|gg-stats-grid|gg-metrics|gg-table-wrap|gg-status-banner)\b[^"]*"[^>]*>',
    re.IGNORECASE,
)
_KEEP_TAG_RE = re.compile(r'<(/?)(div|figure)\b[^>]*?(/?)>', re.IGNORECASE)


def _split_leading_keep_block(body_html: str) -> tuple[str, str]:
    """Return (first_block, rest) when the body opens with a compact block.

    Long tables (more than 6 rows) are not bound: they must be free to paginate.
    Returns ("", body_html) when nothing should be kept with the heading.
    """
    text = str(body_html or "")
    match = _KEEP_BLOCK_START_RE.match(text)
    if not match:
        return "", text
    if match.group(2).lower() == "gg-table-wrap":
        rows = re.search(r'data-row-count="(\d+)"', match.group(0))
        if not rows or int(rows.group(1)) > 6:
            return "", text
    tag = match.group(1).lower()
    depth = 0
    for token in _KEEP_TAG_RE.finditer(text, match.start(0)):
        if token.group(2).lower() != tag or token.group(3):
            continue
        depth += -1 if token.group(1) else 1
        if depth == 0:
            end = token.end()
            return text[:end], text[end:]
    return "", text


_KICKER_GENERIC_TYPES = {"", "narrative", "generic", "section", "content", "text", "overview", "body", "auto"}
# Only section types that tell a reader something are named in the overline;
# composition labels ("narrative", "custom", "metrics dashboard") are internal.
_KICKER_READER_TYPES = {
    "risk_assessment": "Risk assessment", "risks": "Risk assessment", "comparison": "Comparison", "timeline": "Timeline",
    "financials": "Financials", "methodology": "Methodology", "findings": "Key findings", "outlook": "Outlook",
    "valuation": "Valuation", "case_study": "Case study", "recommendations": "Recommendations", "appendix": "Appendix",
}


def _section_kicker_text(number: int, section_type: object) -> str:
    """Reader-facing overline: "Section 02 · Risk assessment"."""
    kind = str(section_type or "").strip().lower().replace("-", "_").replace(" ", "_")
    label = f"Section {int(number):02d}"
    if kind in _KICKER_READER_TYPES:
        label += " · " + _KICKER_READER_TYPES[kind]
    return label


def _report_anchor(title: object, index: int, prefix: str = "section") -> str:
    """Stable, unique in-document anchor derived from generated section titles."""
    slug = re.sub(r"[^a-z0-9]+", "-", str(title or "").casefold()).strip("-")[:54]
    return f"{prefix}-{index}-{slug or 'content'}"


def _render_report_navigation(entries: list[tuple[str, str]], report_tier: str) -> str:
    # Brief is an executive dashboard: keep its opening compact. Navigation
    # earns its space in the longer analytical editions.
    if report_tier == "brief" or len(entries) < 2:
        return ""
    links = "".join(
        f'<li><a href="#{html.escape(anchor, quote=True)}">{html.escape(label)}</a></li>'
        for anchor, label in entries
    )
    return (
        '<nav class="gg-report-nav" aria-label="Report sections"><details open>'
        '<summary>Navigate this report</summary>'
        f'<ul>{links}</ul></details></nav>'
    )


def _render_report_index(entries: list[tuple[str, str]]) -> str:
    if not entries:
        return ""
    rows = "".join(
        f'<li><a href="#{html.escape(anchor, quote=True)}"><span class="gg-report-index-label">{html.escape(label)}</span></a></li>'
        for anchor, label in entries
    )
    return f'<nav id="report-index" class="gg-report-index" aria-label="Contents"><h2>Contents</h2><ol>{rows}</ol></nav>'


def _strip_redundant_model_source_appendix(report: str) -> str:
    """Remove a trailing model-written Sources/References section.

    The renderer owns the canonical source appendix. A second markdown source list
    creates duplicate provenance and can leave a large empty paginated container.

    Models may emit the heading as plain ``Sources:``, ``## Sources:``, or as
    markdown-emphasised ``**Sources:**``/``__Sources:__``. Normalize those small
    presentation differences before deciding whether the marker is a standalone
    trailing source section. Do not remove ``Methodology & Sources`` because that
    is substantive report content rather than the duplicate bibliography.
    """
    text = str(report or "")
    if not text.strip():
        return text

    source_names = {
        "sources", "data sources", "complete data sources", "complete sources",
        "source list", "references", "bibliography",
        "sources & references", "sources and references",
        "source citations", "citations", "source references", "sources cited",
        "works cited", "source index", "source registry", "source list and citations",
    }
    matches: list[tuple[int, int]] = []
    lines = text.splitlines(keepends=True)
    offset = 0
    for raw in lines:
        candidate = raw.strip()
        if candidate:
            # Strip up to H4 markdown heading markers.
            candidate = re.sub(r"^#{1,4}\s*", "", candidate)
            # Strip one pair of markdown emphasis wrappers around the heading.
            candidate = re.sub(r"^(?:\*\*|__)(.*?)(?:\*\*|__)$", r"\1", candidate)
            candidate = candidate.strip()
            # Some writers title the duplicate bibliography as "Appendix — Data
            # Sources" or add a source count ("Complete Data Sources 20").
            # Canonicalize these presentation variants without touching ordinary
            # analytical headings such as "Appendix — Methodology".
            candidate = re.sub(r"^appendix(?:\s+[a-z0-9]+)?\s*[—–‑:\-]\s*", "", candidate, flags=re.IGNORECASE).strip()
            candidate = re.sub(r"\s+(?:\(\d+\)|\d+)\s*$", "", candidate).strip()
            # A colon belongs to the heading, not to its semantic name.
            candidate = re.sub(r"\s*:\s*$", "", candidate).strip()
            if candidate.casefold() in source_names:
                matches.append((offset, offset + len(raw)))
        offset += len(raw)

    if not matches:
        return text
    start, _ = matches[-1]
    # Only remove a late bibliography. A mid-report discussion headed simply
    # "Sources" should remain part of the report.
    if start < len(text) * 0.40:
        return text

    return text[:start].rstrip()


def _strip_duplicate_title_heading(report: str, title: str) -> str:
    """Remove a leading model echo of the document title from report sections."""
    expected = re.sub(r"[^a-z0-9]+", " ", html.unescape(str(title or "")).casefold()).strip()
    if not expected:
        return report
    lines = str(report or "").splitlines()
    first = next((index for index, line in enumerate(lines) if line.strip()), None)
    if first is None:
        return report
    heading = re.match(r"^\s*#{1,3}\s+(.+?)\s*#*\s*$", lines[first])
    if not heading:
        return report
    actual = re.sub(r"[^a-z0-9]+", " ", html.unescape(heading.group(1)).casefold()).strip()
    if actual != expected:
        return report
    del lines[first]
    while first < len(lines) and not lines[first].strip():
        del lines[first]
    return "\n".join(lines)


def _dedupe_repeated_prose(report: str, max_comparisons: int = 180) -> str:
    """Remove accidental repeated narrative blocks while preserving structure.

    This is intentionally conservative: headings, tables, lists, citations-only
    lines and visual placeholders are never removed. Only a later prose paragraph
    that is an exact/near-exact duplicate of an earlier substantive paragraph is
    dropped. This fixes LLM section repetition without imposing a domain template.
    """
    text = str(report or "")
    if not text.strip():
        return text
    parts = re.split(r"(\n\s*\n)", text)
    seen: list[str] = []
    out: list[str] = []
    comparisons = 0

    def normalise(value: str) -> str:
        value = re.sub(r"\[S\d+(?:\s*,\s*S\d+)*\]", "", value, flags=re.I)
        value = re.sub(r"[^a-z0-9%$€£₹]+", " ", value.lower())
        return re.sub(r"\s+", " ", value).strip()

    for idx in range(0, len(parts), 2):
        block = parts[idx]
        sep = parts[idx + 1] if idx + 1 < len(parts) else ""
        stripped = block.strip()
        norm = normalise(stripped)
        protected = (
            not stripped
            or stripped.startswith(("#", "|", "- ", "* ", ">", "[CHART_", "[WEB_IMG_", "[FILE_IMG_", "[PAGE_IMG_"))
            or re.match(r"^(?:source|sources|data source)\s*:", stripped, re.I)
            or len(norm) < 260
        )
        duplicate = False
        if not protected and comparisons < max_comparisons:
            for prior in seen[-80:]:
                comparisons += 1
                if norm == prior:
                    duplicate = True
                    break
                # Strong near-duplicate guard: same opening evidence and >92%
                # similarity. Avoid broad semantic rewriting.
                if len(norm) >= 420 and norm[:90] == prior[:90]:
                    ratio = difflib.SequenceMatcher(None, norm, prior).ratio()
                    if ratio >= 0.92:
                        duplicate = True
                        break
                if comparisons >= max_comparisons:
                    break
        if duplicate:
            # Preserve the paragraph separator so adjacent sections do not
            # accidentally concatenate in markdown.
            out.append("")
            out.append(sep)
            continue
        out.append(block)
        if not protected:
            seen.append(norm)
        out.append(sep)
    return "".join(out)


def build_html_report(report: str, title: str, question: str, summary: str,
                       key_stats: list, charts: list, images: list,
                       theme: dict | None = None, sources: object = None,
                       presentation: object = None, report_tier: str = "detailed",
                       evidence_assets: list | None = None,
                       asset_uses: list | None = None,
                       report_style: str | None = None, kicker: str = "") -> str:
    # Older report writers use [IMAGE_n] while the presentation layer's
    # canonical web-image token is [WEB_IMG_n]. Normalize at the shared HTML
    # boundary so browser and PDF resolve the same selected image item.
    # Defense-in-depth: structured generation markers are control tokens, not
    # report content. Strip them here because this renderer is shared by both
    # the HTML and PDF export paths, including saved/action-edited reports.
    report = strip_excluded_report_sections(strip_generation_markers(normalize_report_markdown(report)))
    report = re.sub(r"\[IMAGE_(\d+)\]", r"[WEB_IMG_\1]", str(report or ""), flags=re.IGNORECASE)
    report = _recover_chart_placeholders_from_titles(report, charts or [])
    report = _strip_redundant_model_source_appendix(report)
    report = _strip_duplicate_title_heading(report, title)
    report = _dedupe_repeated_prose(report)
    # v93: a leading draft/notice blockquote is page-level status, not the body
    # of a section. Lift it above the sections (text kept verbatim) so it does
    # not create an untitled "Report" section.
    report, _leading_status = _extract_leading_status_banner(report)
    report_tier = str(report_tier or "detailed").strip().lower()
    if report_tier not in ("brief", "detailed", "in_depth"):
        report_tier = "detailed"
    if report_tier == "brief":
        # Final renderer boundary: even if a stale frontend or provider bypasses
        # the generation-time Brief contract, never let a Brief render as a
        # Detailed report. The validated presentation plan supplies the dynamic
        # section intent; this does not impose domain-specific section names.
        report = _enforce_brief_section_cap(report, presentation if isinstance(presentation, dict) else None, max_sections=4)
        # Provider/planner wrappers occasionally leak a standalone "Report"
        # label into the markdown body. It is not research content and creates
        # a misleading orphan line beneath a section heading.
        report = re.sub(r"(?im)^\s*(?:#{1,4}\s*)?(?:\*\*|__)?Report(?:\*\*|__)?\s*$", "", report)
    render_theme = dict(theme or {})
    # Keep the browser/PDF family on one restrained institutional palette;
    # report mode changes composition density, not the visual identity.
    render_theme.update({
        "primaryColor": "#17384F",
        "accentColor": "#387C58",
        "surfaceColor": "#FFFFFF",
        "surfaceAltColor": "#F2F6F3",
        "textColor": "#1D2B33",
        "mutedColor": "#34464F",
        "borderColor": "#CCD8D1",
        "fontFamily": "",
        "chartColors": ["#2F7857", "#167C8A", "#315D86", "#916D3E", "#805574", "#A24E43"],
    })
    if report_tier == "in_depth":
        render_theme.update({
            "primaryColor": "#17384F",
            "accentColor": "#387C58",
            "surfaceColor": "#FFFFFF",
            "surfaceAltColor": "#F2F6F3",
            "textColor": "#1D2B33",
            "mutedColor": "#34464F",
            "borderColor": "#CCD8D1",
            "fontFamily": "",
            "chartColors": ["#2F7857", "#167C8A", "#315D86", "#916D3E", "#805574", "#A24E43"],
        })
    # v93 editorial design system: one validated palette and type system for
    # browser HTML, preview and PDF. Report mode still changes composition.
    render_theme.update(_editorial_render_theme())
    # Client house style (Market Currents / Feature Article); None keeps the
    # editorial look for stored reports and older callers.
    house_style = normalize_report_style(report_style)
    render_theme.update(house_theme(house_style))
    if house_style == "feature_article" and images and isinstance(images[0], dict) and images[0].get("url"):
        # The first image is placed prominently as the hero image on Page 1; do not repeat it in the article body.
        report = re.sub(r"\[(?:WEB_IMG|IMAGE)_1\]", "", report, count=1, flags=re.IGNORECASE)
    image_source_records = []
    for image in images or []:
        image_source_url = (image.get("sourceUrl") or image.get("sourceURL")) if isinstance(image, dict) else None
        if isinstance(image, dict) and _safe_plain_url(image_source_url):
            image_title = str(image.get("sourceTitle") or image.get("title") or image.get("caption") or "Web image").strip()
            if len(image_title) > 108:
                image_title = " ".join(image_title.split()[:14]).rstrip(" ,;:") + " …"
            image_source_records.append({
                "title": image_title,
                "url": image_source_url,
                "publisher": image.get("publisher") or "Web image source",
                "kind": image.get("sourceType") or image.get("kind") or "Web image",
                "publication_date": image.get("publicationDate") or image.get("publication_date"),
            })
    chart_source_records = []
    attribution_labels: list[object] = []
    for chart in charts or []:
        if not isinstance(chart, dict):
            continue
        candidates = [chart.get("source"), chart.get("sources"), chart.get("sourceUrl"), chart.get("sourceURL")]
        for candidate in candidates:
            candidate_items = candidate if isinstance(candidate, list) else [candidate]
            for item in candidate_items:
                if isinstance(item, dict):
                    source_url = item.get("url") or item.get("href")
                    if _safe_plain_url(source_url):
                        chart_source_records.append({
                            **item,
                            "title": item.get("title") or chart.get("sourceTitle") or chart.get("title") or "Chart data source",
                            "url": source_url,
                            "kind": item.get("kind") or "Chart data source",
                        })
                    else:
                        attribution_labels.append(item)
                elif isinstance(item, str) and _safe_plain_url(item):
                    chart_source_records.append({
                        "title": chart.get("sourceTitle") or chart.get("title") or "Chart data source",
                        "url": item,
                        "kind": "Chart data source",
                    })
                elif isinstance(item, str) and item.strip() and not _source_ids(item):
                    attribution_labels.append(item)
    attribution_labels.extend(
        match.group(1).strip()
        for match in re.finditer(r"(?im)^\s*(?:source|sources|data source)\s*:\s*(.+)$", report or "")
    )
    attribution_labels.extend(_collect_presentation_source_attributions(presentation))
    # Primary research sources own the S# namespace. Visual/chart URLs that are
    # not part of the research source manifest must never consume a new S# ID,
    # otherwise a model-issued citation can accidentally resolve to an image URL
    # (the former S27 failure). They are still rendered as direct clickable source
    # notes under the visual.
    # Preserve source IDs referenced in the report. If a source record has
    # disappeared, append an explicit unresolved placeholder instead of silently
    # stripping its citation during rendering.
    source_registry = ensure_cited_source_records(report, sources)
    unlinked_sources = _unlinked_attribution_records(attribution_labels, source_registry)
    if unlinked_sources:
        source_registry = build_source_registry(source_registry, additional_sources=unlinked_sources)

    # Preserve backwards compatibility with provenance-gated source visuals.
    # Assets are rendered only when explicitly selected and either licensed for
    # embedding or safely reconstructable from extracted data. Unverified images
    # are not embedded as decoration.
    evidence_asset_html: dict[str, str] = {}
    assets_by_id = {
        str(asset.get("asset_id") or ""): asset
        for asset in (evidence_assets or []) if isinstance(asset, dict) and asset.get("asset_id")
    }
    try:
        from utils.evidence_assets import validate_asset_uses
        validated_uses = validate_asset_uses(asset_uses or [], list(assets_by_id.values()))
    except Exception:
        validated_uses = []
    for use in validated_uses:
        asset_id = str(use.get("asset_id") or "")
        asset = assets_by_id.get(asset_id)
        if not asset:
            continue
        source_url = _safe_plain_url(asset.get("source_url") or asset.get("page_url"))
        asset_url = _safe_plain_url(asset.get("asset_url"))
        caption = str(use.get("caption") or asset.get("caption") or asset.get("title") or "Source visual evidence").strip()
        caption_html = html.escape(caption)
        extracted = asset.get("extracted_data") if isinstance(asset.get("extracted_data"), dict) else {}
        rows = extracted.get("rows") if isinstance(extracted.get("rows"), list) else []
        rendered_asset = ""
        if str(asset.get("asset_type") or "").lower() in {"table", "chart", "graph"} and len(rows) >= 2:
            headers = [str(v or "") for v in (rows[0] or [])]
            values = [[str(v or "") for v in row] for row in rows[1:] if isinstance(row, (list, tuple))]
            rendered_asset = _render_table_markup(headers, values, source_registry, title=caption)
        elif asset.get("can_embed") and asset_url and source_url:
            alt = html.escape(str(asset.get("alt_text") or caption), quote=True)
            rendered_asset = f'<figure class="gg-reveal gg-figure gg-evidence-asset"><img src="{html.escape(asset_url, quote=True)}" alt="{alt}" loading="eager" decoding="async" /><figcaption>{caption_html}</figcaption></figure>'
        if not rendered_asset:
            continue
        # This local source note is intentionally anchored to the verified source
        # webpage, not to a third-party image CDN URL.
        if source_url:
            host = urlparse(source_url).hostname or source_url
            host = host[4:] if host.lower().startswith("www.") else host
            rendered_asset += (
                '<div class="gg-source-note">Source: '
                f'<a href="{html.escape(source_url, quote=True)}">{html.escape(host)}</a>'
                '</div>'
            )
        evidence_asset_html[asset_id] = '<div class="gg-evidence-asset-wrap">' + rendered_asset + '</div>'

    # Final citation guard: only primary/canonical S# citations are valid.
    report = _remove_orphan_citations(report, source_registry)
    force_source_notes = bool(source_registry)
    spec, warnings = _normalise_presentation(presentation, report, title, summary, key_stats, charts, report_tier)
    if warnings:
        log.info("HTML report: presentation normalization: %s", warnings)
    spec_dict = spec.to_dict()
    # Semantic document model: no fixed section template, only the actual
    # report headings/content are analysed so the renderer can adapt layout.
    document = build_report_document(
        report, title, question, summary, tier=report_tier,
        charts=charts, images=images, sources=sources,
    )
    # Presentation is the composition contract: derive only a small set of
    # renderer-controlled classes from the validated schema. No domain template
    # is selected here, and no arbitrary CSS can enter from the LLM.
    selected_layouts = {str(x.get("layout") or "auto").lower() for x in spec_dict.get("sections") or []}
    # ``auto`` means the renderer will choose the concrete composition per
    # section. Keep the page-level class intentionally generic instead of
    # pretending the report has one fixed layout.
    if "auto" in selected_layouts:
        composition_class = "gg-composition--adaptive"
    elif len(selected_layouts) > 1:
        composition_class = "gg-composition--hybrid"
    else:
        composition_class = "gg-composition--" + next(iter(selected_layouts), "single_column")
    title_text = str(title or question or "Research Report")
    safe_title = html.escape(title_text)
    safe_summary = html.escape(summary or "")
    date_str = datetime.now(timezone.utc).strftime("%d %B %Y")
    css = _build_css(render_theme, report_tier)
    # v93: bundled Lora/Inter (embedded, offline) + editorial layer, appended
    # after the legacy primitives so it composes rather than replaces them.
    css = css + "\n" + _editorial_font_faces() + "\n" + _editorial_css() + house_css(house_style)
    # Do not emit arbitrary theme.customCss from the LLM; only validated theme tokens are consumed.
    # Keep PDF rendering deterministic/offline. A model-selected fontFamily can still
    # be used when installed locally, while the dynamic renderer does not require
    # a live Google Fonts fetch during PDF generation.
    font_link = ""
    actual_sections = [{"title": sec.title, "body": sec.body, "_model_index": i} for i, sec in enumerate(document.sections)]
    matches = _match_planned_sections(spec, actual_sections)

    exec_spec = spec_dict.get("executive_summary") or {}
    exec_placement = str(exec_spec.get("placement") or "none")
    exec_body = str(exec_spec.get("body") or summary or "").strip()
    # Do not automatically inject KPIs into every executive summary. Metrics are
    # a presentation choice now; only render them here when the validated plan
    # explicitly places metric blocks in the executive summary.
    exec_metrics = exec_spec.get("key_metrics") or []

    summary_html = ""
    # The presentation contract decides whether an executive-summary panel is
    # rendered. Do not resurrect it merely because the payload has a summary;
    # the generated report may already contain an actual "Executive Summary"
    # section, and rendering both is a duplicate.
    show_executive_summary = bool(exec_body) and exec_placement != "none"
    # If the generated report already contains a substantive Executive Summary
    # section whose opening text substantially duplicates the presentation
    # summary, keep the real section and suppress the extra summary panel.
    def _is_executive_summary_section(section_title: object) -> bool:
        normalized = re.sub(r"\s+", " ", str(section_title or "")).strip().casefold()
        # Models sometimes merge the overview and executive summary into one
        # heading, e.g. "Executive Summary & Macroeconomic Context". Treat that
        # as the real summary section so the compact API summary does not force
        # a redundant near-empty page immediately after the cover.
        return normalized.startswith(("executive summary", "executive takeaway", "executive overview"))

    actual_exec = next((sec for sec in document.sections if _is_executive_summary_section(sec.title)), None)
    # A report can contain an LLM-authored Markdown summary section that is not
    # represented in the optional presentation plan. Check the source report as
    # well, so an API summary card cannot repeat content that the renderer will
    # emit from the Markdown body below.
    raw_summary_heading = re.search(
        r"(?im)^\s{0,3}#{1,6}\s+(?:executive\s+(?:summary|takeaway|overview))\b",
        str(report or ""),
    )
    if show_executive_summary and actual_exec is not None:
        # The generated report already owns the executive-summary section.
        # Never add a second summary card merely because the API also carries
        # a compact summary field. This was the main cause of the repeated
        # Executive Summary seen in PDF exports.
        show_executive_summary = False
    elif show_executive_summary and raw_summary_heading:
        show_executive_summary = False
    if house_style == "feature_article":
        # Feature articles display the narrative standfirst on the cover header and omit
        # the corporate boxed Executive Summary card.
        show_executive_summary = False
    if show_executive_summary:
        summary_cls = "gg-summary-wrap gg-summary-wrap--sidebar" if exec_placement == "sidebar" else "gg-summary-wrap--end" if exec_placement == "end_summary" else "gg-summary-wrap"
        body = _markdown_to_html(exec_body, [], [], render_theme, source_registry, force_source_notes)
        metrics = _render_metrics(exec_metrics, source_registry)
        summary_heading = str(exec_spec.get("heading") or ("Executive Takeaway" if report_tier == "brief" else "Executive Summary"))
        summary_html = f'<section id="report-overview" class="{summary_cls}"><div class="gg-summary-card gg-summary-card--high"><h2 class="gg-summary-heading">{html.escape(summary_heading)}</h2>{body}{metrics}</div></section>'

    planned_metric_content = any(
        str(section.get("section_type") or "").lower() == "metrics_dashboard"
        or any(
            isinstance(block, dict) and str(block.get("kind") or block.get("type") or "").lower()
            in {"metrics", "metrics_dashboard", "kpi_grid", "stats", "metric_grid"}
            for block in (section.get("blocks") or [])
        )
        for section in (spec_dict.get("sections") or [])
    )
    stat_limit = 3 if report_tier == "brief" else 6 if report_tier == "detailed" else 8
    dashboard_stats = _brief_key_stats(key_stats) if report_tier == "brief" else key_stats
    # A Brief report gets one KPI surface, not an executive-metrics row followed
    # by a second dashboard made from the same keyStats payload. If the planner
    # supplied executive metrics, those own the KPI presentation. A dashboard is
    # only rendered when there are no executive metrics to avoid duplicate cards.
    explicit_exec_metrics = bool(exec_metrics)
    should_render_dashboard = bool(dashboard_stats) and planned_metric_content and not explicit_exec_metrics
    dashboard_html = (
        '<section class="gg-dashboard-strip" aria-label="Report key metrics">'
        + _render_key_stats(dashboard_stats, source_registry, stat_limit)
        + '</section>'
        if should_render_dashboard else ""
    )

    if report_tier == "brief" and not charts:
        recovered = _verified_metric_trend(key_stats, report, source_registry) or _verified_report_trend(report, source_registry)
        if recovered:
            chart_html = _render_chart_block(recovered, 991, render_theme, source_registry, force_source_note=True)
            if chart_html:
                dashboard_html += '<section class="gg-brief-trend" aria-label="Sourced report trend">' + chart_html + '</section>'

    # Collapse ultra-thin, text-only headings into the previous section as H3
    # when there is no visual/structured material. This keeps dynamic reports
    # from producing an otherwise empty A4 page for a heading with one sentence.
    compact_matches: list[tuple[dict, dict]] = []
    for _plan, _actual in matches:
        _body = str(_actual.get("body") or "").strip()
        _has_visual = bool(re.search(r"\[CHART_\d+\]|\[(?:WEB_IMG|FILE_IMG|PAGE_IMG)_\d+\]", _body)) or bool(
            re.search(r"(?im)^\s*[-*]\s+", _body) and len(re.findall(r"(?im)^\s*[-*]\s+", _body)) >= 3
        )
        if compact_matches and 0 < len(_body) < 650 and not _has_visual and not (_plan.get("blocks") or []):
            prev_plan, prev_actual = compact_matches[-1]
            prev_body = str(prev_actual.get("body") or "").rstrip()
            merged_body = prev_body + ("\n\n### " + str(_actual.get("title") or _plan.get("title") or "Additional detail").strip() + "\n\n" + _body)
            compact_matches[-1] = (prev_plan, {**prev_actual, "body": merged_body})
        else:
            compact_matches.append((_plan, _actual))
    matches = compact_matches

    body_sections = []
    section_entries: list[tuple[str, str]] = []
    chart_cursor = 0
    legacy_metrics_rendered = False
    rendered_metric_fingerprints: set[str] = set()
    for idx, (plan, actual) in enumerate(matches):
        section_title = html.escape(str(plan.get("title") or actual.get("title") or f"Section {idx + 1}"))
        model_index = actual.get("_model_index")
        model_sec = document.sections[model_index] if isinstance(model_index, int) and 0 <= model_index < len(document.sections) else None
        layout = _adaptive_layout_for_section(plan, actual, model_sec, report_tier)
        if layout not in {"single_column", "two_column", "grid", "full_bleed", "sidebar_main"}:
            layout = "single_column"
        density = _adaptive_section_density(plan, model_sec)
        body_len = len(str(actual.get("body") or ""))
        if body_len > 7000:
            density = "dense"
        elif body_len < 1600 and density == "standard":
            density = "sparse"
        emphasis = str(plan.get("emphasis") or "normal")
        section_type = str(plan.get("section_type") or "narrative")
        body_raw = str(actual.get("body") or "").strip()
        metric_limit = 4 if report_tier == "brief" else 6 if report_tier == "detailed" else 8
        block_html, chart_cursor, blocks_meaningful = _render_structured_blocks(
            plan.get("blocks") or [], charts, key_stats, render_theme, chart_cursor,
            source_registry, force_source_notes=force_source_notes, metric_limit=metric_limit,
            suppress_fallback_metrics=bool(dashboard_html),
            rendered_metric_fingerprints=rendered_metric_fingerprints,
        )
        narrative_html = _markdown_to_html(
            body_raw, charts, images, render_theme, source_registry, force_source_notes,
            evidence_asset_html=evidence_asset_html,
        ) if body_raw else ""
        if not blocks_meaningful:
            body_html = narrative_html
        else:
            # Structured blocks are additive presentation primitives. Never discard the
            # report prose merely because the planner also requested KPI/table/callout blocks.
            body_html = (narrative_html + "\n" + block_html).strip() if narrative_html.strip() and block_html.strip() else (narrative_html or block_html)
        if section_type == "metrics_dashboard" and key_stats and "gg-metric" not in body_html and not legacy_metrics_rendered and not dashboard_html:
            metric_limit = 4 if report_tier == "brief" else 6 if report_tier == "detailed" else 8
            body_html = _render_key_stats(key_stats, source_registry, metric_limit) + body_html
            legacy_metrics_rendered = True
        if section_type == "timeline" and not blocks_meaningful:
            # Markdown remains the content source; presentation controls the arrangement.
            pass
        # A plan can legitimately contain a section the model did not generate.
        # Do not render an orphan heading for it; that creates large blank pages.
        if not body_raw and not blocks_meaningful:
            continue
        # The real generated heading is authoritative; the presentation plan
        # can control layout but never relabel a body with an unrelated title.
        section_title = str(actual.get("title") or plan.get("title") or f"Section {idx + 1}")
        section_anchor = _report_anchor(section_title, idx + 1)
        section_entries.append((section_anchor, section_title))
        # A generic planner label such as "Report" is not useful when the
        # generated body already starts with its own semantic Markdown heading.
        # Suppress only that generic wrapper so the real content heading becomes
        # the visible section title; domain-specific/model-chosen headings are
        # left untouched.
        generic_wrapper = (
            section_title.strip().casefold() in {"report", "research report"}
            and bool(re.match(r"^\s*#{2,4}\s+\S+", body_raw))
        )
        # Number only sections that are actually rendered. A plan can contain
        # a synthetic/empty summary slot that is skipped above; using `idx + 1`
        # left the first visible section numbered 02 or 03 in exported PDFs.
        visible_section_no = sum(1 for rendered in body_sections if 'class="gg-section-heading"' in rendered) + 1
        if house_style == "market_currents" and visible_section_no == 1:
            if section_title.strip().casefold() in {str(title).strip().casefold(), "report", "research report", "overview"}:
                section_title = "Executive Summary"
        section_kicker = f"Section {visible_section_no:02d}" if house_style == "market_currents" else _section_kicker_text(visible_section_no, section_type)
        heading_html = "" if generic_wrapper else (
            f'<div class="gg-section-heading" data-section-number="{visible_section_no:02d}"><span class="gg-section-index" aria-hidden="true">{visible_section_no:02d}</span><div class="gg-section-heading-copy"><div class="gg-section-kicker">{html.escape(section_kicker)}</div><h2>{html.escape(section_title)}</h2></div></div>'
        )
        body_html = transform_section_html(house_style, body_html)
        if (house_style == "market_currents" and key_stats and not body_sections and not dashboard_html
                and "gg-stats-grid" not in body_html and "gg-metric" not in body_html):
            # The client's format puts the headline-figure cards under the
            # executive summary of the first section.
            body_html += '<div class="gg-house-kpis">' + _render_key_stats(key_stats, source_registry, 8) + '</div>'
        # v93 keep-with-next: when a section opens directly with a figure,
        # chart, metric grid or short table, bind the heading to that block so
        # print engines move them together instead of splitting the figure
        # (Chromium resolves heading break-after:avoid vs figure
        # break-inside:avoid by splitting the figure).
        _lead_html, _rest_html = _split_leading_keep_block(body_html) if heading_html else ("", body_html)
        _body_cls = f'gg-section-body gg-section-body--{html.escape(layout, quote=True)}'
        if _lead_html:
            _section_inner = (
                f'<div class="gg-keep-with-next">{heading_html}<div class="{_body_cls} gg-section-body--lead">{_lead_html}</div></div>'
                + (f'<div class="{_body_cls}">{_rest_html}</div>' if _rest_html.strip() else '')
            )
        else:
            _section_inner = f'{heading_html}<div class="{_body_cls}">{body_html}</div>'
        body_sections.append(
            f'<section id="{html.escape(section_anchor, quote=True)}" data-section-order="{idx + 1}" data-layout-resolved="{html.escape(layout, quote=True)}" class="gg-section gg-reveal gg-section--{html.escape(layout, quote=True)} gg-section--{html.escape(emphasis, quote=True)} gg-section-type--{html.escape(section_type, quote=True)}" data-layout="{html.escape(layout, quote=True)}" data-density="{html.escape(density, quote=True)}" data-emphasis="{html.escape(emphasis, quote=True)}" data-section-type="{html.escape(section_type, quote=True)}" data-reveal>'
            f'{_section_inner}</section>'
        )

    # Section-editing can remove a [CHART_n] placeholder while the validated
    # structured chart remains in the chart array. The browser already restores
    # unreferenced charts; do the same in HTML/PDF so accepted chart data is not
    # silently lost and no title is left without a visual.
    referenced_chart_indices = {
        int(match.group(1)) for match in re.finditer(r"\[CHART_(\d+)\]", report, flags=re.IGNORECASE)
    }
    presentation_chart_titles = {
        re.sub(r"[^a-z0-9]+", " ", str(block.get("title") or "").casefold()).strip()
        for section in (spec_dict.get("sections") or []) if isinstance(section, dict)
        for block in (section.get("blocks") or []) if isinstance(block, dict)
        if str(block.get("kind") or block.get("type") or "").casefold() == "chart" and str(block.get("title") or "").strip()
    }
    recovered_chart_html = []
    for chart_index, chart in enumerate(charts or [], start=1):
        if chart_index in referenced_chart_indices or not isinstance(chart, dict):
            continue
        title_key = re.sub(r"[^a-z0-9]+", " ", str(chart.get("title") or "").casefold()).strip()
        if title_key and title_key in presentation_chart_titles:
            continue
        if not _chart_has_renderable_payload(chart):
            log.warning("HTML report: skipped unreferenced chart with no renderable data — index=%d title=%r", chart_index, chart.get("title"))
            continue
        rendered_chart = _render_chart_block(chart, chart_index, render_theme, source_registry, force_source_note=True)
        if rendered_chart:
            recovered_chart_html.append(
                f'<div class="gg-unplaced-chart" data-chart-index="{chart_index}" data-chart-recovered="true">{rendered_chart}</div>'
            )
            log.info("HTML report: restored unreferenced structured chart — index=%d title=%r", chart_index, chart.get("title"))
    if recovered_chart_html:
        # If valid chart objects survived but their inline placeholders were
        # lost during generation/section editing, restore them in a named
        # section rather than leaving a visually detached, untitled chart tail.
        recovered_heading = (
            '<div class="gg-section-heading" data-section-number=""><span class="gg-section-index" aria-hidden="true">↗</span>'
            '<div class="gg-section-heading-copy"><div class="gg-section-kicker">EVIDENCE VISUALS</div>'
            '<h2>Charts &amp; Data Analysis</h2></div></div>'
        )
        body_sections.append(
            '<section id="evidence-charts-data-analysis" class="gg-section gg-reveal gg-section--single_column gg-section-type--findings" '
            'data-layout="single_column" data-density="dense" data-emphasis="normal" data-section-type="findings" data-chart-recovery-section="true">'
            + recovered_heading + '<div class="gg-section-body gg-section-body--single_column">'
            + "".join(recovered_chart_html) + '</div></section>'
        )
        log.info("HTML report: grouped %d recovered chart(s) in Charts & Data Analysis section", len(recovered_chart_html))

    cited_source_ids = set(_source_ids(report))
    for stat in key_stats or []:
        if isinstance(stat, dict):
            cited_source_ids.update(_source_ids(stat.get("source") or stat.get("sources") or stat.get("sourceIds")))
    for chart in charts or []:
        if isinstance(chart, dict):
            cited_source_ids.update(_source_ids(chart.get("source") or chart.get("sources") or chart.get("sourceIds")))
    source_spec = spec_dict.get("source_appendix") or {}
    placement = str(source_spec.get("placement") or "end_of_report")
    if placement == "none" and source_registry:
        placement = "end_of_report"
    sources_html = _render_sources_appendix(
        sources, placement, bool(source_spec.get("include_appendix")), source_registry,
        report_tier=report_tier, cited_source_ids=cited_source_ids,
    )
    inline_sources_html = ""
    if placement == "none":
        sources_html = ""
        inline_sources_html = ""

    if exec_placement in ("after_cover", "top_of_body"):
        opening_html = summary_html
        closing_summary = ""
    elif exec_placement == "end_summary":
        opening_html = ""
        closing_summary = summary_html
    elif exec_placement == "sidebar":
        opening_html = summary_html
        closing_summary = ""
    else:
        opening_html = summary_html if summary_html else ""
        closing_summary = ""

    cover_spec = dict(spec_dict)
    # If the planner explicitly chose a cover subtitle that is effectively the
    # same as the executive summary, keep the cover but remove the duplicate
    # prose. The cover remains model-driven; this is only a collision guard.
    if isinstance(cover_spec.get("cover"), dict) and cover_spec["cover"].get("subtitle"):
        _cover_sub = re.sub(r"\s+", " ", str(cover_spec["cover"].get("subtitle") or "")).strip().casefold()
        _sum_sub = re.sub(r"\s+", " ", str(summary or "")).strip().casefold()
        if _cover_sub and _sum_sub and (_cover_sub == _sum_sub or (_len := min(len(_cover_sub), len(_sum_sub))) > 80 and _cover_sub[:_len] in _sum_sub):
            cover_spec["cover"] = {**cover_spec["cover"], "subtitle": ""}
    cover_html = _render_cover(cover_spec, safe_title, safe_summary, date_str, key_stats, source_registry)
    if house_style:
        cover_html = render_house_cover(
            house_style, title=title_text, summary=summary or "", question=str(question or ""), date_str=date_str,
            key_stats=key_stats, report_text=report, images=images, kicker=kicker,
        )
    # A real cover already owns the report title/date. Repeating a second title
    # band immediately below it creates a duplicate opening and wastes print space.
    title_band_html = ""
    if not cover_html:
        content_for_period = " ".join((title_text, str(question or "")))
        period_match = re.search(
            r"\b(?:FY\s*)?20\d{2}\s*(?:[–—-]|to|through)\s*(?:FY\s*)?20\d{2}\b|\bthrough\s+(?:FY\s*)?20\d{2}\s+and\s+beyond\b",
            content_for_period,
            flags=re.IGNORECASE,
        )
        meta_items = []
        if period_match:
            meta_items.append("Research period · " + html.escape(period_match.group(0)))
        meta_items.append("Prepared · " + html.escape(date_str))
        title_band_html = (
            '<header id="report-title" class="gg-title-band">'
            '<p class="gg-report-brandbar">Growth Gradual · Research Intelligence</p>'
            f'<h1 class="gg-title">{safe_title}</h1>'
            f'<p class="gg-title-meta">{" &nbsp;·&nbsp; ".join(meta_items)}</p>'
            '</header>'
        )
    status_banner_html = ""
    if _leading_status:
        _kind, _label, _text = _leading_status
        status_banner_html = (
            f'<div class="gg-status-banner gg-status-banner--{_kind}" role="note" data-status-label="{html.escape(_label, quote=True)}">'
            f'{_inline_md(_text, source_registry)}</div>'
        )
    navigation_entries: list[tuple[str, str]] = []
    if summary_html:
        navigation_entries.append(("report-overview", summary_heading))
    navigation_entries.extend(section_entries)
    has_source_appendix = bool(sources_html)
    if has_source_appendix:
        navigation_entries.append(("data-sources", "Data Sources"))
    navigation_html = _render_report_navigation(navigation_entries, report_tier)
    index_entries = [*navigation_entries]
    # A contents panel is useful for substantial Deep Research reports, but
    # becomes visual overhead on short reports with only a couple of sections.
    # Keep the navigation adaptive instead of inserting a tiny, mostly empty TOC.
    index_html = _render_report_index(index_entries) if report_tier == "in_depth" and len(index_entries) >= 4 else ""
    tier_class = f"gg-tier--{html.escape(report_tier, quote=True)}"
    visual_spec = spec_dict.get("visual_spec") if isinstance(spec_dict.get("visual_spec"), dict) else {}
    visual_classes = " ".join(
        f"gg-v-{html.escape(str(visual_spec.get(k) or '').strip().lower().replace('_','-'), quote=True)}"
        for k in ("composition_mode", "opening_mode", "visual_rhythm", "whitespace_profile", "emphasis_profile", "density", "title_treatment", "section_treatment", "chart_treatment", "table_treatment", "background_style")
        if str(visual_spec.get(k) or '').strip()
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>{safe_title} — Growth Gradual</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
{font_link}
<style>{css}</style>
</head>
<body data-report-tier="{html.escape(report_tier, quote=True)}"{f' data-report-style="{house_style}" data-running-title="{html.escape(running_title(house_style, title_text, question), quote=True)}"' if house_style else ''} class="{f'gg-house gg-house--{house_style} ' if house_style else ''}{_EDITORIAL_BODY_CLASS} gg-style--{html.escape(str((theme or {}).get("designMode") or "editorial"), quote=True)} gg-cards--{html.escape(str((theme or {}).get("cardMode") or "outlined"), quote=True)} gg-section-treatment--{html.escape(str((theme or {}).get("sectionTreatment") or "rule"), quote=True)} gg-chart-style--{html.escape(str((theme or {}).get("chartStyle") or "annotated"), quote=True)} {visual_classes} {tier_class}{' gg-has-cover' if cover_html else ''}{' gg-cover-bold-banner' if cover_html and str(cover_spec.get("cover", {}).get("treatment") or "minimal").lower() == "bold_banner" else ''}">
{cover_html}
{navigation_html}
<main class="{composition_class}" data-report-tier="{html.escape(report_tier, quote=True)}">
  {title_band_html}
  {status_banner_html}
  {index_html}
  {(opening_html + dashboard_html) if report_tier == "brief" else (dashboard_html + opening_html)}
  {inline_sources_html}
  <div class="gg-report-sections">{"".join(body_sections)}</div>
  {closing_summary}
  {sources_html}
  {house_closing(house_style)}
</main>
<footer class="gg-report-footer"><strong>Growth Gradual · Research Intelligence</strong><span>{safe_title}</span><span>Sources and methodology appear within this report.</span></footer>
<script>{_JS}</script>
</body>
</html>"""

@router.post("")
async def generate_html_report(request: Request):
    t0 = time.perf_counter()
    try:
        body = await request.json()
    except Exception:
        log.warning("HTML report: invalid request body")
        return JSONResponse({"error": "Invalid request body"}, status_code=400)

    report: str = body.get("report", "")
    if not report:
        log.warning("HTML report: no report content in request")
        return JSONResponse({"error": "No report content"}, status_code=400)

    if report.strip() in _KNOWN_FAILURE_MESSAGES or report.strip().startswith("## Report Generation Error"):
        log.warning("HTML report: refusing to render known report-generation-failure sentinel")
        return JSONResponse(
            {"error": "The report failed to generate, so there's nothing to export yet. Please try generating the report again."},
            status_code=422,
        )

    title: str = body.get("title", "")
    question: str = body.get("question", "Research Report")
    summary: str = body.get("summary", "")
    key_stats: list = body.get("keyStats", [])
    charts: list = body.get("charts", [])
    images: list = body.get("images", [])
    theme: dict | None = _sanitize_theme(body.get("theme"))
    sources = normalise_source_manifest(body.get("sources", []))
    presentation = body.get("presentation")
    report_tier = str(body.get("reportTier") or "detailed").strip().lower()
    if report_tier not in ("brief", "detailed", "in_depth"):
        report_tier = "detailed"

    # Same unwrap-double-encoded-JSON safety net as routes/pdf.py.
    stripped = report.strip()
    if stripped.startswith("{") and '"report"' in stripped:
        try:
            inner = json.loads(stripped, strict=False)
            if isinstance(inner.get("report"), str) and len(inner["report"]) > 100:
                report = inner["report"]
                title = title or inner.get("title", "")
                summary = summary or inner.get("summary", "")
                key_stats = key_stats or inner.get("keyStats", [])
                charts = charts or inner.get("charts", [])
                theme = theme or _sanitize_theme(inner.get("theme"))
                presentation = presentation or inner.get("presentation")
        except Exception as e:
            log.debug("HTML report: report field is not double-encoded JSON, using as-is (%s)", e)

    if "\\n" in report:
        report = report.replace("\\n", "\n")
    report = re.sub(r"^```(?:json|markdown)?\s*", "", report.strip())
    report = re.sub(r"```\s*$", "", report).strip()

    report, charts, _chart_heal = sanitize_charts_for_publication(
        report, charts, sources, question=question, require_publisher_verification=True,
    )
    if _chart_heal["removedCharts"] or _chart_heal["prunedPoints"]:
        log.warning("HTML report: export gate removed %d unsupported chart(s) and %d chart point(s) instead of failing; %d chart(s) kept",
                    _chart_heal["removedCharts"], _chart_heal["prunedPoints"], len(charts))
    chart_findings = _chart_heal["remainingFindings"]
    if chart_findings:
        return JSONResponse({
            "error": "This report contains chart values that conflict with verification status or lack supporting source evidence. Resolve the findings before export.",
            "publicationStatus": "FAIL", "findings": chart_findings[:20], "exported": False,
        }, status_code=422)

    try:
        html_doc = build_html_report(
            report, title, question, summary, key_stats, charts, images,
            theme, sources, presentation, report_tier,
            report_style=normalize_report_style(body.get("reportStyle"), DEFAULT_REPORT_STYLE),
            kicker=str(body.get("kicker") or "")[:60],
        )
    except Exception as e:
        log.error("HTML report: build_html_report failed: %s", e)
        return JSONResponse({"error": f"Failed to generate HTML report: {e}"}, status_code=500)

    elapsed = (time.perf_counter() - t0) * 1000
    log.info("HTML report: done — %.1f KB in %.0fms", len(html_doc) / 1024, elapsed)

    date_filename = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return Response(
        content=html_doc,
        media_type="text/html",
        headers={
            "Content-Disposition": f'inline; filename="growth-gradual-report-{date_filename}.html"',
            "Cache-Control": "no-store",
        },
    )
