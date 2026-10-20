"""Deterministic provenance checks for numeric charts and report tables."""
from __future__ import annotations

import os
import re
import logging


log = logging.getLogger(__name__)

_UNVERIFIED = re.compile(
    r"(?i)\b(?:not\s+verified|unverified|unsupported|insufficient\s+evidence|not\s+established)\b"
)
_UNVERIFIED_CELL = re.compile(
    r"(?i)^\s*(?:[—–-]|n/?a|na|not\s+available|unavailable|unknown|not\s+reported|not\s+disclosed|not\s+verified|unverified|unsupported|insufficient\s+evidence|not\s+established)\s*$"
)
_NUMBER = re.compile(
    r"^\s*[₹$€£]?\s*([-+]?(?:(?:\d{1,3}(?:,\d{2,3})+|\d+)(?:\.\d*)?|\.\d+))\s*%?\s*(?:[A-Za-z][A-Za-z0-9/²³%.-]*)?\s*$"
)
_GENERIC = {
    "chart", "figure", "data", "series", "value", "values", "total", "reported",
    "projection", "projected", "forecast", "trend", "comparison", "versus", "vs",
    "period", "year", "growth", "change", "market", "size", "capacity", "installed",
    "power", "metric", "result", "amount", "share", "rate", "index", "source",
    "research", "report", "reports", "analysis", "analytical", "overview", "outlook",
}
_MEASURE_UNITS = {
    "gw": (r"\bgw\b", r"gigawatts?"), "mw": (r"\bmw\b", r"megawatts?"),
    "kw": (r"\bkw\b", r"kilowatts?"), "twh": (r"\btwh\b", r"terawatt[- ]hours?"),
    "gwh": (r"\bgwh\b", r"gigawatt[- ]hours?"), "mwh": (r"\bmwh\b", r"megawatt[- ]hours?"),
    "%": (r"%", r"percent(?:age)?", r"\bper\s?cent\b"), "inr":(r"\binr\b", r"\brs\.?\b", "₹", "rupees?"),
    "rs": (r"\brs\.?\b", r"\binr\b", "₹", "rupees?"), "₹": ("₹", r"\binr\b", r"\brs\.?\b", "rupees?"),
    "usd": (r"\busd\b", r"\bus\s*\$", r"\$", "dollars?"), "$": (r"\busd\b", r"\bus\s*\$", r"\$", "dollars?"),
    "bps": (r"\bbps\b", r"basis points?"), "crore": (r"\bcrores?\b",), "lakh": (r"\blakhs?\b",),
}


def _tables(text: str) -> list[tuple[list[str], list[list[str]]]]:
    lines = str(text or "").splitlines()
    separator = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")
    result = []
    i = 0
    while i + 1 < len(lines):
        if "|" not in lines[i] or not separator.match(lines[i + 1]):
            i += 1
            continue
        cells = lambda line: [x.strip() for x in line.strip().strip("|").split("|")]
        headers = cells(lines[i])
        i += 2
        rows = []
        while i < len(lines) and "|" in lines[i] and lines[i].strip():
            if not separator.match(lines[i]):
                row = cells(lines[i])
                row += [""] * max(0, len(headers) - len(row))
                rows.append(row[:len(headers)])
            i += 1
        if headers and rows:
            result.append((headers, rows))
    return result


def _numeric(value: object) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    return bool(_NUMBER.fullmatch(str(value).strip()))


def _indexed_points(chart: dict) -> list[tuple[int, int, str, str, object]]:
    """Return numeric chart points with their original series/data indices."""
    result = []
    for series_index, series in enumerate(chart.get("series") or []):
        if not isinstance(series, dict):
            continue
        name = str(series.get("name") or series.get("label") or "")
        data = series.get("data") if isinstance(series.get("data"), list) else series.get("values")
        if not isinstance(data, list):
            continue
        for point_index, point in enumerate(data):
            if not isinstance(point, dict):
                continue
            value = next((point[k] for k in ("value", "y", "count", "amount") if k in point), None)
            if not _numeric(value):
                continue
            label = next((str(point[k]).strip() for k in ("label", "name", "category", "period", "date", "x", "key")
                          if point.get(k) is not None and str(point[k]).strip()), "")
            result.append((series_index, point_index, name, label, value))
    return result


def _points(chart: dict) -> list[tuple[str, str, object]]:
    return [(series, label, value) for _, _, series, label, value in _indexed_points(chart)]


def prune_unsupported_chart_points(charts: list | None, audit: dict | None) -> tuple[list[dict], list[int], int]:
    """Prune unsupported points while keeping charts that retain enough verified data.

    Missing source mappings and conflicts with report tables explicitly marked
    unverified reject a whole chart. Ordinary point-level evidence failures remove
    only those observations. Non-table charts must retain at least two numeric
    observations (one for gauge-like single-value visuals).

    Returns ``(kept_charts, removed_original_indices, removed_point_count)``.
    Chart indices are 1-based and refer to the original input list.
    """
    import copy
    import math

    original = list(charts or [])
    failures = [item for item in ((audit or {}).get("chartEvidenceFailures") or []) if isinstance(item, dict)]
    conflicts = [item for item in ((audit or {}).get("chartTableVerificationConflicts") or []) if isinstance(item, dict)]
    hard_reject = {int(item.get("chartIndex")) for item in conflicts if str(item.get("chartIndex", "")).isdigit()}
    point_failures: dict[int, set[tuple[int, int]]] = {}
    for item in failures:
        raw_index = item.get("chartIndex")
        if not str(raw_index or "").isdigit():
            continue
        chart_index = int(raw_index)
        if str(item.get("seriesIndex", "")).isdigit() and str(item.get("pointIndex", "")).isdigit():
            point_failures.setdefault(chart_index, set()).add((int(item["seriesIndex"]), int(item["pointIndex"])))
        else:
            # Missing/unresolvable chart source IDs are a chart-wide failure.
            hard_reject.add(chart_index)

    kept: list[dict] = []
    removed_indices: list[int] = []
    removed_points = 0
    for chart_index, raw_chart in enumerate(original, start=1):
        if not isinstance(raw_chart, dict):
            removed_indices.append(chart_index)
            continue
        chart_type = str(raw_chart.get("type") or raw_chart.get("chartType") or "").casefold()
        # A table/heatmap/graph with no usable payload can never be rendered or
        # verified; the publication audit flags it ("no data rows"), so drop it.
        if chart_type == "table":
            _cols = raw_chart.get("columns") if isinstance(raw_chart.get("columns"), list) else []
            _rows = raw_chart.get("rows") if isinstance(raw_chart.get("rows"), list) else []
            if len(_cols) < 2 or len(_rows) < 2:
                hard_reject.add(chart_index)
        elif chart_type in {"heatmap", "network", "tree", "flowchart", "gantt", "timeline", "sankey", "chord", "venn"}:
            try:
                from utils.report_quality import _chart_has_nonempty_payload
                if not _chart_has_nonempty_payload(raw_chart):
                    hard_reject.add(chart_index)
            except Exception:
                pass
        if chart_type not in {"table", "heatmap", "network", "tree", "flowchart", "gantt", "timeline", "sankey", "chord", "venn",
                              "boxplot", "population_pyramid", "candlestick", "ohlc", "density", "violin", "ridgeline"}:
            finite_count = 0
            for series in raw_chart.get("series") or []:
                if not isinstance(series, dict):
                    continue
                values = series.get("data") if isinstance(series.get("data"), list) else series.get("values")
                for point in values or []:
                    raw_value = (next((point[k] for k in ("value", "y", "count", "amount") if k in point), None)
                                 if isinstance(point, dict) else point)
                    try:
                        if math.isfinite(float(str(raw_value).replace(",", "").strip())):
                            finite_count += 1
                    except (TypeError, ValueError):
                        continue
            minimum_finite = 1 if chart_type in {"gauge", "bullet", "metric", "indicator", "single_value"} else 1
            if finite_count < minimum_finite:
                hard_reject.add(chart_index)
        if chart_index in hard_reject:
            removed_indices.append(chart_index)
            continue
        if chart_type == "table" or chart_index not in point_failures:
            kept.append(raw_chart)
            continue

        bad_positions = point_failures.get(chart_index, set())
        chart = copy.deepcopy(raw_chart)
        raw_series = chart.get("series")
        if not isinstance(raw_series, list):
            removed_indices.append(chart_index)
            continue
        cleaned_series = []
        for series_index, series in enumerate(raw_series):
            if not isinstance(series, dict):
                continue
            data_key = "data" if isinstance(series.get("data"), list) else "values" if isinstance(series.get("values"), list) else None
            if not data_key:
                cleaned_series.append(series)
                continue
            data = series[data_key]
            filtered = [point for point_index, point in enumerate(data)
                        if (series_index, point_index) not in bad_positions]
            removed_points += len(data) - len(filtered)
            series[data_key] = filtered
            if filtered:
                cleaned_series.append(series)
        chart["series"] = cleaned_series

        numeric_count = 0
        for series in cleaned_series:
            data = series.get("data") if isinstance(series.get("data"), list) else series.get("values")
            for point in data or []:
                if not isinstance(point, dict):
                    continue
                value = next((point[k] for k in ("value", "y", "count", "amount") if k in point), None)
                if not _numeric(value):
                    continue
                try:
                    if math.isfinite(float(str(value).replace(",", "").strip())):
                        numeric_count += 1
                except (TypeError, ValueError):
                    pass
        minimum = 1 if chart_type in {"gauge", "bullet", "metric", "indicator", "single_value"} else 2
        if numeric_count < minimum:
            removed_indices.append(chart_index)
            continue
        kept.append(chart)
    return kept, removed_indices, removed_points


_SOURCE_TEXT_KEYS = (
    "evidenceExcerpt", "chartEvidenceExcerpt", "publisherChartEvidenceExcerpt", "searchChartEvidenceExcerpt",
    "searchEvidenceExcerpt", "publisherEvidenceExcerpt", "pageContent", "page_content", "fullContent", "full_content",
    "evidenceContent", "content", "text", "excerpt", "snippet", "body",
)
# Explicit unit scalings between a quoted figure and a plotted value
# (lakh crore → crore, billion → crore, crore → lakh, US$ bn → US$ mn …).
_QUOTE_SCALES = (1.0, 100000.0, 100.0, 0.01, 0.1, 1000.0, 1_000_000.0, 0.001)


def _norm_quote(text: object) -> str:
    return re.sub(r"[\s|*`_\\]+", "", str(text or "")).casefold()


def _search_extract_evidence_allowed() -> bool:
    """Whether explicitly verified search-index quotes may support chart points."""
    return os.getenv("REPORT_ACCEPT_SEARCH_EXTRACT_EVIDENCE", "1").strip().casefold() not in {"0", "false", "no", "off"}


def _publisher_evidence_verified(source: dict) -> bool:
    """Return true only when chart evidence was fetched from a public publisher page."""
    status = str(source.get("pageFetchStatus") or source.get("page_fetch_status") or "").strip().casefold()
    origin = str(source.get("evidenceExcerptOrigin") or source.get("evidence_excerpt_origin") or
                 source.get("evidenceOrigin") or source.get("evidence_origin") or "").strip().casefold()
    gallery_excerpt = str(source.get("publisherChartEvidenceExcerpt") or "").strip()
    gallery_excerpt_verified = bool(
        source.get("publisher_gallery_chart_evidence_verified") and gallery_excerpt
    )
    # Backward compatibility with the v10 manifest, whose publisher gallery
    # excerpt shared the generic chartEvidenceExcerpt field.
    legacy_gallery_excerpt_verified = bool(
        source.get("publisher_gallery_chart_evidence_verified")
        and str(source.get("chartEvidenceExcerpt") or "").strip()
    )
    table_excerpt_verified = bool(
        source.get("publisher_table_chart_evidence_verified")
        and str(source.get("chartEvidenceExcerpt") or "").strip()
    )
    direct = any(str(source.get(key) or "").strip() for key in (
        "publisherEvidenceExcerpt", "publisher_evidence_excerpt", "pageContent", "page_content",
    )) or table_excerpt_verified or gallery_excerpt_verified or legacy_gallery_excerpt_verified
    manifest_excerpt = bool(str(source.get("evidenceExcerpt") or source.get("evidence_excerpt") or "").strip())
    # A mixed publisher+search source is accepted only when this report's gallery
    # attached a publisher-specific excerpt that was re-matched to fetched page text.
    origin_ok = origin in {"", "publisher_page", "publisher-page", "publisher_page_crawl", "publisher_page_fetch", "publisher"} or gallery_excerpt_verified or legacy_gallery_excerpt_verified or table_excerpt_verified
    return status == "ok" and origin_ok and (direct or manifest_excerpt)


def _search_chart_evidence_verified(source: dict) -> bool:
    """Validate a search-only quote excerpt without relabeling it as publisher text."""
    if not _search_extract_evidence_allowed():
        return False
    if not source.get("search_extract_chart_evidence_verified"):
        return False
    if not str(source.get("searchChartEvidenceExcerpt") or "").strip():
        return False
    url = str(source.get("url") or source.get("matchedEvidenceUrl") or "").strip().casefold()
    if not url.startswith(("https://", "http://")):
        return False
    origin = str(source.get("evidenceOrigin") or source.get("evidence_origin") or "").strip().casefold()
    return origin != "okf_retrieval"


def _chart_source_text(source: dict | None, require_publisher_verification: bool = False) -> str:
    if not isinstance(source, dict):
        return ""
    if not require_publisher_verification:
        return "\n".join(str(source.get(key) or "") for key in _SOURCE_TEXT_KEYS).strip()

    publisher_ok = _publisher_evidence_verified(source)
    search_ok = _search_chart_evidence_verified(source)
    if not publisher_ok and not search_ok:
        return ""

    pieces: list[str] = []
    if publisher_ok:
        pieces.extend(str(source.get(key) or "") for key in (
            "publisherEvidenceExcerpt", "publisher_evidence_excerpt", "pageContent", "page_content",
        ))
        origin = str(source.get("evidenceExcerptOrigin") or source.get("evidence_excerpt_origin") or
                     source.get("evidenceOrigin") or source.get("evidence_origin") or "").strip().casefold()
        # Avoid adding a mixed/general evidence excerpt to publisher evidence.
        # It may contain search-only sentences, so use it only when provenance is
        # explicitly publisher-page-only (or has no provenance metadata at all).
        if origin in {"", "publisher_page", "publisher-page", "publisher_page_crawl", "publisher_page_fetch", "publisher"}:
            pieces.extend(str(source.get(key) or "") for key in ("evidenceExcerpt", "evidence_excerpt"))
        if source.get("publisher_gallery_chart_evidence_verified"):
            pieces.append(str(source.get("publisherChartEvidenceExcerpt") or ""))
            # Previous manifests used chartEvidenceExcerpt for gallery quotes.
            if not str(source.get("publisherChartEvidenceExcerpt") or "").strip():
                pieces.append(str(source.get("chartEvidenceExcerpt") or ""))
        if source.get("publisher_table_chart_evidence_verified"):
            pieces.append(str(source.get("chartEvidenceExcerpt") or ""))
    if search_ok:
        pieces.append(str(source.get("searchChartEvidenceExcerpt") or ""))
    # Drop empty strings and duplicate content while preserving the provenance
    # partition above. _quote_supports still requires the point's exact quote and value.
    return "\n".join(dict.fromkeys(piece.strip() for piece in pieces if piece and piece.strip())).strip()


def _chart_metric_tokens(chart: dict, surrounding: str = "") -> set[str]:
    raw = " ".join(str(chart.get(key) or "") for key in ("title", "xLabel", "yLabel", "unit"))
    raw += " " + " ".join(str(s.get("name") or s.get("label") or "") for s in chart.get("series", []) if isinstance(s, dict))
    raw += " " + str(surrounding or "")
    tokens = {t for t in re.findall(r"[a-z]{3,}", raw.casefold()) if t not in _GENERIC}
    return tokens


_BANKING_METRIC_FAMILIES = {
    "credit": ("credit", "advances", "loan", "loans", "lending"),
    "deposits": ("deposit", "deposits", "casa", "savings", "term"),
    "asset_quality": ("npa", "gnpa", "nnpa", "nonperforming", "non", "performing", "asset", "assets", "quality", "ratio", "ratios", "provision", "slippage"),
    "profitability": ("profit", "profitability", "margin", "nim", "nii", "roa", "roe", "earnings", "income"),
    "rates": ("repo", "interest", "policy", "rate", "rates", "mclr", "yield"),
    "digital_payments": ("upi", "digital", "payments", "transaction", "transactions", "volume", "value"),
    "capital": ("capital", "adequacy", "tier", "car", "riskweighted"),
    "balance_sheet": ("assets", "liabilities", "balance", "sheet", "growth"),
}


def _chart_is_relevant(chart: dict, report: str, question: str, index: int) -> bool:
    """Block out-of-topic charts before evidence verification/publication."""
    title = str(chart.get("title") or "").strip()
    type_name = str(chart.get("type") or chart.get("chartType") or "").casefold()
    if not title or re.search(r"(?i)\b(?:file size|document size|source count|spread of reported observations|reported observations by metric)\b", title):
        return False
    start = max(0, report.upper().find(f"[CHART_{index}]") - 700)
    marker_pos = report.upper().find(f"[CHART_{index}]")
    surrounding = report[start:marker_pos] if marker_pos >= 0 else ""
    title_and_context = (title + " " + str(chart.get("xLabel") or "") + " " +
                         str(chart.get("yLabel") or "") + " " + surrounding + " " +
                         " ".join(str(s.get("name") or s.get("label") or "") for s in chart.get("series", []) if isinstance(s, dict))).casefold()
    q = str(question or "").casefold()
    is_banking_question = bool(re.search(r"\b(?:banking sector|scheduled commercial banks?|bank credit|deposit growth|gross npa|net npa|upa|upi|indian banks)\b", q))
    if is_banking_question:
        # Net-owned-fund comparisons usually refer to NBFC regulatory capital and
        # were a recurring false-positive for broad scheduled-bank outlooks.
        if re.search(r"\b(?:nbfc|current net owned fund|net owned fund)\b", title_and_context):
            return False
        all_terms = set(re.findall(r"[a-z]{3,}", title_and_context))
        allowed = {term for family in _BANKING_METRIC_FAMILIES.values() for term in family}
        if not all_terms.intersection(allowed):
            return False
    else:
        question_terms = {t for t in re.findall(r"[a-z]{4,}", q) if t not in _GENERIC}
        chart_terms = _chart_metric_tokens(chart, surrounding)
        # General questions must have at least one substantive topical anchor
        # unless the question is too short to provide a useful anchor.
        if len(question_terms) >= 2 and chart_terms and not question_terms.intersection(chart_terms):
            if type_name not in {"table", "heatmap", "network", "tree", "flowchart", "gantt", "timeline", "sankey"}:
                return False
    return True


def _chart_observation_signature(chart: dict) -> tuple:
    points = []
    for series in chart.get("series", []) if isinstance(chart.get("series"), list) else []:
        if not isinstance(series, dict):
            continue
        series_name = re.sub(r"[^a-z0-9]+", " ", str(series.get("name") or series.get("label") or "").casefold()).strip()
        values = series.get("data") if isinstance(series.get("data"), list) else series.get("values")
        for point in values or []:
            if isinstance(point, dict):
                label = next((str(point.get(k)).strip() for k in ("label", "name", "category", "period", "date", "x", "key") if point.get(k) is not None and str(point.get(k)).strip()), "")
                value = next((point.get(k) for k in ("value", "y", "count", "amount") if k in point), None)
            else:
                label, value = "", point
            try:
                numeric = round(float(str(value).replace(",", "").strip()), 8)
            except (TypeError, ValueError):
                continue
            label = re.sub(r"[^a-z0-9]+", " ", label.casefold()).strip()
            points.append((series_name, label, numeric))
    return (str(chart.get("unit") or chart.get("yLabel") or "").casefold().strip(), tuple(sorted(points)))


def _dedupe_chart_specs(report: str, charts: list[dict], question: str) -> tuple[str, list[dict], int]:
    """Remove irrelevant and duplicate chart specs, preserving unique comparisons."""
    kept: list[dict] = []
    signatures: dict[tuple, int] = {}
    mask: list[bool] = []
    removed = 0
    for idx, chart in enumerate(charts, start=1):
        keep = _chart_is_relevant(chart, report, question, idx)
        if keep:
            signature = _chart_observation_signature(chart)
            title_tokens = _chart_metric_tokens(chart)
            if signature[1] and signature in signatures:
                prior_idx = signatures[signature]
                prior = kept[prior_idx]
                prior_tokens = _chart_metric_tokens(prior)
                # Identical labels, units and values under a different chart type
                # are one evidence visualization, not extra analytical coverage.
                if not title_tokens or not prior_tokens or title_tokens.intersection(prior_tokens):
                    keep = False
            if keep:
                signatures[signature] = len(kept)
                kept.append(chart)
        mask.append(keep)
        if not keep:
            removed += 1
    if removed:
        report = remap_chart_placeholders(report, mask)
    return report, kept, removed


def _quote_supports(value: object, evidence_value: object, quote: object, source_text: str) -> bool:
    """A point is supported when its exact quoted source line is in the source text and holds the value."""
    quote = str(quote or "").strip()
    if len(quote) < 6 or not source_text:
        return False
    if _norm_quote(quote) not in _norm_quote(source_text):
        return False
    shown = str(evidence_value or "").strip() or str(value or "").strip()
    if not _number_is_in(shown, quote):
        return False
    if str(evidence_value or "").strip() and value is not None:
        try:
            plotted = abs(float(str(value).replace(",", "")))
            quoted = abs(float(str(evidence_value).replace(",", "")))
        except ValueError:
            return False
        if quoted == 0:
            return plotted == 0
        ratio = plotted / quoted
        return any(abs(ratio - scale) <= scale * 1e-6 for scale in _QUOTE_SCALES)
    return True


def _source_ids(chart: dict) -> list[str]:
    raw = chart.get("sourceIds") or chart.get("source_ids") or chart.get("sources") or chart.get("source") or []
    values = raw if isinstance(raw, (list, tuple, set)) else [raw]
    return sorted({sid.upper() for value in values for sid in re.findall(r"\bS\d+\b", str(value), re.I)})


def _number_is_in(value: object, evidence: str) -> bool:
    parsed = _NUMBER.fullmatch(str(value or "").strip())
    if not parsed:
        return False
    try:
        target = float(parsed.group(1).replace(",", ""))
    except ValueError:
        return False
    values = re.findall(r"(?<![A-Za-z0-9])[-+]?(?:(?:\d{1,3}(?:,\d{2,3})+|\d+)(?:\.\d*)?|\.\d+)(?![A-Za-z0-9])", evidence)
    for raw in values:
        try:
            observed = float(raw.replace(",", ""))
        except ValueError:
            continue
        if abs(observed - target) <= max(0.001, abs(target) * 0.002):
            return True
    return False


_VALUE_TOKEN = re.compile(r"(?<![A-Za-z0-9])[-+]?(?:(?:\d{1,3}(?:,\d{2,3})+|\d+)(?:\.\d*)?|\.\d+)(?![A-Za-z0-9])")


def _value_windows(value: object, evidence: str) -> list[tuple[str, str]]:
    """Return the local evidence context around each occurrence of a chart value.

    A chart point is only supported when its subject and period appear *near*
    the value: the same prose sentence (plus the one before it), or the same
    table row together with that table's header/caption lines. Checking terms
    anywhere on a long page let a neighbouring cell's number be plotted under
    the wrong bank, metric or period.
    """
    parsed = _NUMBER.fullmatch(str(value or "").strip())
    if not parsed or not evidence:
        return []
    try:
        target = float(parsed.group(1).replace(",", ""))
    except ValueError:
        return []
    lines = str(evidence).splitlines() or [str(evidence)]
    windows: list[str] = []
    for index, line in enumerate(lines):
        hits = []
        for match in _VALUE_TOKEN.finditer(line):
            try:
                observed = float(match.group(0).replace(",", ""))
            except ValueError:
                continue
            if abs(observed - target) <= max(0.001, abs(target) * 0.002):
                hits.append(match)
        if not hits:
            continue
        if "|" in line and line.count("|") >= 2:
            start = index
            while start > 0 and "|" in lines[start - 1]:
                start -= 1
            header = lines[max(0, start - 2):min(start + 3, index)]
            for match in hits:
                # Flattened tables put several rows on one line; keep only the
                # row label just before the value (not every row on the line).
                row_text = line if len(line) <= 260 else line[max(0, match.start() - 160):match.end() + 40]
                windows.append((row_text, " ".join(header + [row_text])))
            continue
        cuts = [0] + [m.end() for m in re.finditer(r"(?<=[.!?;])\s+(?=[A-Z(₹$\-•*])", line)] + [len(line)]
        spans = [(cuts[i], cuts[i + 1]) for i in range(len(cuts) - 1) if cuts[i + 1] > cuts[i]] or [(0, len(line))]
        for match in hits:
            current = next((i for i, (a, b) in enumerate(spans) if a <= match.start() < b), len(spans) - 1)
            a = spans[max(0, current - 1)][0]
            b = spans[current][1]
            prefix = lines[index - 1][-240:] if current == 0 and index > 0 else ""
            own = line[spans[current][0]:b]
            windows.append((own, (prefix + " " + line[a:b]).strip()))
    return windows


_ENTITY_FILLER = {"bank", "banks", "sector", "group", "groups", "ltd", "limited", "the", "and", "all"}


def _label_terms_present(label: str, text: str) -> bool:
    """True when a point's entity label (or its acronym, e.g. PSBs) is in the text."""
    words = [w for w in re.findall(r"[A-Za-z]{3,}", str(label or "")) if w.casefold() not in _GENERIC]
    terms = [w for w in words if w.casefold() not in _ENTITY_FILLER]
    if not terms:
        return True
    if any(re.search(rf"\b{re.escape(t)}\b", text, re.I) for t in terms):
        return True
    initials = "".join(w[0] for w in re.findall(r"[A-Za-z]+", str(label or "")) if w.casefold() not in {"the", "and", "of"})
    return len(initials) >= 2 and bool(re.search(rf"\b{re.escape(initials)}s?\b", text, re.I))


def _unit_is_in(chart: dict, evidence: str) -> bool:
    label = str(chart.get("unit") or chart.get("yLabel") or "").casefold()
    for unit, patterns in _MEASURE_UNITS.items():
        if unit in label:
            return any(re.search(pattern, evidence, re.I) for pattern in patterns)
    return True


def audit_chart_evidence_consistency(report: str, charts: list | None, sources: list | None,
                                     require_publisher_verification: bool = False) -> dict:
    """Require source-backed numeric series and reject contradictory table states.

    This is deliberately fail-closed: an unavailable excerpt is not evidence,
    and a numeric chart is not allowed to promote a related explicitly
    unverified observation into a fact. No values are inferred or repaired.
    """
    chart_rows = [x for x in (charts or []) if isinstance(x, dict)]
    source_by_id = {
        str(x.get("id") or "").strip().upper(): x
        for x in (sources or []) if isinstance(x, dict) and str(x.get("id") or "").strip()
    }
    table_defs = [(headers, rows) for headers, rows in _tables(report)]
    for chart in chart_rows:
        if str(chart.get("type") or chart.get("chartType") or "").casefold() != "table":
            continue
        columns = chart.get("columns") if isinstance(chart.get("columns"), list) else []
        rows = chart.get("rows") if isinstance(chart.get("rows"), list) else []
        normalized_rows = []
        for row in rows:
            if isinstance(row, dict):
                normalized_rows.append([str(row.get(str(col), row.get(col, "")) or "") for col in columns])
            elif isinstance(row, (list, tuple)):
                normalized_rows.append([str(value or "") for value in row])
        if columns and normalized_rows:
            table_defs.append((list(map(str, columns)), normalized_rows))
    conflicts = []
    for chart_index, chart in enumerate(chart_rows, 1):
        if str(chart.get("type") or chart.get("chartType") or "").casefold() == "table":
            continue
        title = str(chart.get("title") or f"Chart {chart_index}")
        for headers, rows in table_defs:
            for row_index, row in enumerate(rows, 1):
                entity = str(row[0] or "").strip() if row else ""
                entity_terms = {t for t in re.findall(r"[a-z]{3,}", entity.casefold())
                                if t not in {"power", "sector", "segment", "total", "value", "capacity", "market"}}
                if not entity_terms:
                    continue
                for col_index, cell in enumerate(row[1:], 1):
                    if not (_UNVERIFIED.search(str(cell or "")) or _UNVERIFIED_CELL.fullmatch(str(cell or ""))):
                        continue
                    period = str(headers[col_index] if col_index < len(headers) else "")
                    years = set(re.findall(r"20\d{2}", period))
                    for series, label, value in _points(chart):
                        identity = f"{series} {label} {title}".casefold()
                        if not entity_terms.intersection(re.findall(r"[a-z]{3,}", identity)):
                            continue
                        chart_years = set(re.findall(r"20\d{2}", f"{series} {label}"))
                        if years and chart_years and not years.intersection(chart_years):
                            continue
                        conflicts.append({
                            "chartIndex": chart_index, "chartTitle": title[:180],
                            "tableRow": row_index, "entity": entity[:100], "period": period[:100],
                            "chartValue": str(value)[:40],
                            "reason": "A numeric chart point is presented for an entity/period that the related report table explicitly marks unverified.",
                        })

    failures = []
    for chart_index, chart in enumerate(chart_rows, 1):
        if str(chart.get("type") or chart.get("chartType") or "").casefold() == "table":
            continue
        title = str(chart.get("title") or f"Chart {chart_index}")
        # Derived charts (shares, quartiles, histogram counts, scaled scores)
        # are verified through the reported inputs they were computed from.
        derived_inputs = chart.get("derivedFrom") if isinstance(chart.get("derivedFrom"), list) else []
        for record in derived_inputs:
            if not isinstance(record, dict):
                continue
            sid = str(record.get("sourceId") or "").strip().upper()
            source = source_by_id.get(sid)
            text = _chart_source_text(source, require_publisher_verification)
            if not _quote_supports(record.get("value"), record.get("evidenceValue"), record.get("quote"), text):
                failures.append({
                    "chartIndex": chart_index, "chartTitle": title[:180], "sourceIds": [sid] if sid else [],
                    "label": str(record.get("label") or "")[:100], "value": str(record.get("value"))[:40],
                    "reason": "A reported input of this derived chart is not supported by its quoted source line.",
                })
                break
        indexed_points = []
        for series_index, series in enumerate(chart.get("series") or []):
            if not isinstance(series, dict):
                continue
            name = str(series.get("name") or series.get("label") or "")
            data = series.get("data") if isinstance(series.get("data"), list) else series.get("values")
            for point_index, point in enumerate(data if isinstance(data, list) else []):
                if not isinstance(point, dict) or point.get("derived"):
                    continue
                value = next((point[k] for k in ("value", "y", "count", "amount") if k in point), None)
                if not _numeric(value):
                    continue
                label = next((str(point[k]).strip() for k in ("label", "name", "category", "period", "date", "x", "key")
                              if point.get(k) is not None and str(point[k]).strip()), "")
                indexed_points.append((series_index, point_index, name, label, value, point))
        if not indexed_points:
            continue
        ids = _source_ids(chart)
        quoted_ids = {str(p.get("evidenceSource") or "").strip().upper() for *_rest, p in indexed_points if p.get("evidenceSource")}
        if not ids and not quoted_ids:
            failures.append({"chartIndex": chart_index, "chartTitle": title[:180], "sourceIds": [],
                             "reason": "Numeric chart observations have no source IDs attached to the chart."})
            continue
        missing = sorted((set(ids) | quoted_ids) - set(source_by_id))
        if missing:
            failures.append({"chartIndex": chart_index, "chartTitle": title[:180], "sourceIds": missing,
                             "reason": "Chart source IDs do not resolve to the report source manifest."})
        source_text = {}
        for sid in set(ids) | quoted_ids:
            source = source_by_id.get(sid)
            if not source:
                continue
            source_text[sid] = _chart_source_text(source, require_publisher_verification)
        for series_index, point_index, series, label, value, point in indexed_points:
            quote_sid = str(point.get("evidenceSource") or "").strip().upper()
            if quote_sid and point.get("evidenceQuote"):
                if _quote_supports(value, point.get("evidenceValue"), point.get("evidenceQuote"), source_text.get(quote_sid, "")):
                    continue
                failures.append({
                    "chartIndex": chart_index, "seriesIndex": series_index, "pointIndex": point_index,
                    "chartTitle": title[:180], "series": series[:100], "label": label[:100], "value": str(value)[:40],
                    "unit": str(chart.get("unit") or chart.get("yLabel") or "")[:60], "sourceIds": [quote_sid],
                    "sourceFetchStatus": str((source_by_id.get(quote_sid) or {}).get("page_fetch_status") or "unknown"),
                    "reason": "The quoted source line for this chart point is not present in the source evidence or does not contain the value.",
                })
                continue
            context = " ".join((title, str(chart.get("xLabel") or ""), str(chart.get("yLabel") or ""),
                                str(chart.get("unit") or ""), series, label)).casefold()
            terms = {t for t in re.findall(r"[a-z]{3,}", context) if t not in _GENERIC}
            point_terms = {t for t in re.findall(r"[a-z]{3,}", f"{series} {label}".casefold()) if t not in _GENERIC}
            years = set(re.findall(r"20\d{2}", f"{series} {label}"))
            supported = False
            local_context_miss = False
            for sid, evidence in source_text.items():
                if sid not in ids:
                    continue
                if not evidence or not _number_is_in(value, evidence):
                    continue
                required_terms = point_terms or terms
                if required_terms and not any(re.search(rf"\b{re.escape(term)}\b", evidence, re.I) for term in required_terms):
                    continue
                if not _unit_is_in(chart, evidence):
                    continue
                if years and not any(year in evidence for year in years):
                    continue
                # Same subject/period checks, but local to the value: the
                # entity or metric term and the period must sit in the same
                # sentence/table row (with its header) as the plotted number.
                windows = _value_windows(value, evidence)
                if windows:
                    local_ok = any(
                        # The point's entity must be in the value's own sentence/row;
                        # the metric may come from the row header or the sentence before.
                        _label_terms_present(label, own)
                        and (not required_terms or any(
                            re.search(rf"\b{re.escape(term)}\b", context, re.I) for term in required_terms))
                        and (not years or any(year in context for year in years))
                        for own, context in windows
                    )
                    if not local_ok:
                        local_context_miss = True
                        continue
                supported = True
                break
            if supported:
                continue
            status = ", ".join(sorted({str(source_by_id[sid].get("pageFetchStatus") or source_by_id[sid].get("page_fetch_status") or "unknown")
                                       for sid in ids if sid in source_by_id})) or "unavailable"
            reason = ("The cited chart source has no usable extracted evidence text." if not any(source_text.values())
                      else "The value appears in the cited source, but not in the same sentence or table row as this chart point's subject and period (likely a neighbouring cell or a different metric)."
                      if local_context_miss
                      else "No cited source excerpt supports this chart value together with its subject and period.")
            failures.append({
                "chartIndex": chart_index, "seriesIndex": series_index, "pointIndex": point_index,
                "chartTitle": title[:180], "series": series[:100],
                "label": label[:100], "value": str(value)[:40],
                "unit": str(chart.get("unit") or chart.get("yLabel") or "")[:60],
                "sourceIds": ids, "sourceFetchStatus": status, "reason": reason,
            })
    return {"chartTableVerificationConflicts": conflicts[:50], "chartEvidenceFailures": failures[:100],
            "blocking": bool(conflicts or failures)}


_CHART_PLACEHOLDER = re.compile(r"\[CHART_(\d+)\]")


def remap_chart_placeholders(report_text: str, valid_mask: list[bool]) -> str:
    """Renumber [CHART_n] after charts were dropped; remove placeholders of dropped charts."""
    remap: dict[int, int | None] = {}
    new_pos = 0
    for old_idx, keep in enumerate(valid_mask, start=1):
        if keep:
            new_pos += 1
            remap[old_idx] = new_pos
        else:
            remap[old_idx] = None

    def _sub(m: re.Match) -> str:
        new_n = remap.get(int(m.group(1)))
        return f"[CHART_{new_n}]" if new_n is not None else ""

    out_lines = []
    text = re.sub(r"!\[[^\]\n]*\]\(\s*<?\s*\[?CHART_(\d+)\]?\s*>?\s*\)",
                  lambda m: f"[CHART_{m.group(1)}]", str(report_text or ""), flags=re.I)
    for line in text.split("\n"):
        replaced = _CHART_PLACEHOLDER.sub(_sub, line)
        if line.strip().startswith("[CHART_") and not replaced.strip():
            continue
        out_lines.append(replaced)
    return "\n".join(out_lines)


def _attach_chart_placeholders_to_matching_captions(report: str, charts: list[dict]) -> tuple[str, list[int]]:
    """Recover omitted inline markers only when a chart title matches a caption.

    Some report drafts contain a valid structured chart and an explicit caption,
    but omit the renderer control token. Without this handoff, publication drops
    the chart as unreferenced and exports only the prose/table. This recovery is
    intentionally conservative: it never fabricates observations and never
    appends charts without a strong, local title match.
    """
    text = str(report or "")
    lines = text.splitlines()
    referenced = {int(value) for value in re.findall(r"\[CHART_(\d+)\]", text, re.I)}
    stop = {"the", "and", "for", "with", "from", "this", "that", "chart", "figure", "visualization", "visualisation", "year", "yoy", "vs", "over", "by", "of", "in", "on", "to", "a", "an"}

    def tokens(value: object) -> set[str]:
        return {x for x in re.findall(r"[a-z0-9]+", str(value or "").casefold()) if len(x) >= 3 and x not in stop}

    attached: list[int] = []
    # Iterate original indices so existing references remain stable.
    for index, chart in enumerate(charts or [], start=1):
        if index in referenced or not isinstance(chart, dict):
            continue
        kind = str(chart.get("type") or chart.get("chartType") or chart.get("chart_type") or "").casefold()
        if kind in {"table", "data_table", "supporting_table"}:
            continue
        title_tokens = tokens(chart.get("title") or chart.get("name") or "")
        if len(title_tokens) < 2:
            continue
        matches: list[tuple[float, int]] = []
        for line_index, line in enumerate(lines):
            if re.search(r"\[CHART_\d+\]", line, re.I):
                continue
            line_tokens = tokens(re.sub(r"^\s*#+\s*", "", line))
            if len(line_tokens) < 2:
                continue
            overlap = len(title_tokens & line_tokens) / max(1, min(len(title_tokens), len(line_tokens)))
            explicit = bool(re.search(r"\b(?:chart|figure|visuali[sz]ation)\b", line, re.I))
            if overlap >= 0.55 and (explicit or overlap >= 0.8):
                matches.append((overlap + (0.2 if explicit else 0), line_index))
        if not matches:
            continue
        _, line_index = max(matches, key=lambda item: item[0])
        insert_at = line_index + 1
        cursor = insert_at
        saw_table = False
        while cursor < len(lines):
            current = lines[cursor].strip()
            if not current:
                if saw_table:
                    break
                cursor += 1
                continue
            if current.startswith("|") and current.endswith("|"):
                saw_table = True
                cursor += 1
                continue
            if saw_table:
                break
            break
        if saw_table:
            insert_at = cursor
        lines.insert(insert_at, f"[CHART_{index}]")
        attached.append(index)
    return ("\n".join(lines) if attached else text), attached


def sanitize_charts_for_publication(report: str, charts: list | None, sources: list | None,
                                    max_passes: int = 4, *, question: str = "",
                                    require_publisher_verification: bool = False) -> tuple[str, list[dict], dict]:
    """Drop every chart/point the export gate would reject, until the audit is clean.

    Fail-closed on *content* (unverified values never ship) but not on
    *availability*: instead of letting the PDF/HTML/DOCX export answer HTTP 422
    for the whole report, unsupported points/charts are removed and the
    placeholders are renumbered. No values are invented or repaired.
    """
    text = str(report or "")
    current = [c for c in (charts or []) if isinstance(c, dict)]
    text, current, irrelevant_or_duplicate = _dedupe_chart_specs(text, current, question)
    text, caption_recovered_indices = _attach_chart_placeholders_to_matching_captions(text, current)
    if caption_recovered_indices:
        log.info("Chart publication recovered %d caption-matched inline placeholder(s): %s", len(caption_recovered_indices), caption_recovered_indices)
    removed_total = irrelevant_or_duplicate
    points_total = 0
    # Preserve table/chart conflicts as blocking findings even if pruning removes
    # the contradictory chart. Silently deleting it would make export appear to
    # pass instead of surfacing a critical claim/evidence mismatch for repair.
    initial_audit = audit_chart_evidence_consistency(
        text, current, sources, require_publisher_verification=require_publisher_verification
    )
    critical_conflicts = list(initial_audit.get("chartTableVerificationConflicts") or [])
    for _ in range(max_passes):
        audit = audit_chart_evidence_consistency(
            text, current, sources, require_publisher_verification=require_publisher_verification
        )
        kept, removed_idx, removed_pts = prune_unsupported_chart_points(current, audit)
        if not removed_idx and not removed_pts:
            break
        if removed_idx:
            removed_set = set(removed_idx)
            mask = [i not in removed_set for i in range(1, len(current) + 1)]
            text = remap_chart_placeholders(text, mask)
        removed_total += len(removed_idx)
        points_total += removed_pts
        current = kept
    if removed_total or points_total:
        try:
            from utils.report_quality import reconcile_orphaned_chart_references
            text = reconcile_orphaned_chart_references(text, current)
        except Exception:
            pass
    final = audit_chart_evidence_consistency(
        text, current, sources, require_publisher_verification=require_publisher_verification
    )
    remaining = list(final.get("chartTableVerificationConflicts") or []) + list(final.get("chartEvidenceFailures") or [])
    # Preserve critical conflicts found before pruning; the visual no longer
    # ships, but the conflicting written evidence still needs human/repair review.
    seen_conflicts = {repr(item) for item in remaining}
    remaining.extend(item for item in critical_conflicts if repr(item) not in seen_conflicts)
    return text, current, {
        "removedCharts": removed_total, "prunedPoints": points_total,
        "remainingFindings": remaining,
        "captionMatchedChartsAttached": caption_recovered_indices,
    }
