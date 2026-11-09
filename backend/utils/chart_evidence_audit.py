"""Deterministic provenance checks for numeric charts and report tables."""
from __future__ import annotations

import contextvars
import os
import re
import logging
import datetime as _dt

from utils.evidence_provenance import is_trusted_calculated_sector_source

log = logging.getLogger(__name__)

# Charts the V2 pipeline validated once, early (evidence, window, placement next to a paragraph
# about them): their observation signatures, per request. The topical-relevance heuristic does not
# remove them; the evidence and duplicate checks still run. Set server-side only (Stage F).
_TRUSTED_CHART_SIGNATURES: contextvars.ContextVar[frozenset] = contextvars.ContextVar(
    "trusted_chart_signatures", default=frozenset())


def trust_validated_charts(charts: list | None) -> int:
    signatures = frozenset(_chart_observation_signature(c) for c in (charts or []) if isinstance(c, dict))
    _TRUSTED_CHART_SIGNATURES.set(signatures)
    return len(signatures)

_UNVERIFIED = re.compile(
    r"(?i)\b(?:not\s+verified|unverified|unsupported|insufficient\s+evidence|not\s+established)\b"
)
_UNVERIFIED_CELL = re.compile(
    r"(?i)^\s*(?:[—–-]|n/?a|na|not\s+available|unavailable|unknown|not\s+reported|not\s+disclosed|not\s+verified|unverified|unsupported|insufficient\s+evidence|not\s+established)\s*$"
)
_MISSING_CELL = re.compile(r"(?i)^\s*[\u2014\u2013\uFFFD]\s*$")
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
    only those observations. Trend/sequence charts must retain the minimum their
    renderer can draw; categorical bars and single-value visuals may contain one
    observation.

    Returns ``(kept_charts, removed_original_indices, removed_point_count)``.
    Chart indices are 1-based and refer to the original input list.
    """
    import copy
    import math

    original = list(charts or [])
    failures = [item for item in ((audit or {}).get("chartEvidenceFailures") or []) if isinstance(item, dict)]
    conflicts = [item for item in ((audit or {}).get("chartTableVerificationConflicts") or []) if isinstance(item, dict)]
    hard_reject = {int(item.get("chartIndex")) for item in conflicts if str(item.get("chartIndex", "")).isdigit()}
    # A chart cannot mix requested-window data with a point from an older
    # source period. Drop the complete visual so its title/caption cannot imply
    # a time span that the verified observations do not share.
    hard_reject.update(
        int(item.get("chartIndex")) for item in failures
        if str(item.get("chartIndex", "")).isdigit()
        and item.get("reason") == "The chart source period falls outside the time window requested by the user."
    )
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
            minimum_finite = (
                4 if chart_type == "sparkline"
                else 3 if chart_type == "waterfall"
                else 2 if chart_type in {"line", "candlestick", "ohlc"}
                else 1
            )
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
_QUOTE_SCALES = (1.0, 100000.0, 10000000.0, 100.0, 0.01, 0.1, 1000.0, 1_000_000.0, 0.001)


def _norm_quote(text: object) -> str:
    return re.sub(r"[\s|*`_\\]+", "", str(text or "")).casefold()


def _search_extract_evidence_allowed() -> bool:
    """Whether explicitly verified search-index quotes may support chart points."""
    return os.getenv("REPORT_ACCEPT_SEARCH_EXTRACT_EVIDENCE", "0").strip().casefold() not in {"0", "false", "no", "off"}


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
    # Stage J: a fetched page that also has a search snippet ("publisher_page_and_search_extract") is
    # publisher evidence for its FETCHED PAGE TEXT; _chart_source_text then uses only that text (never
    # the mixed excerpt), so a search-only sentence still cannot verify a chart point.
    fetched_page = any(str(source.get(key) or "").strip() for key in ("pageContent", "page_content"))
    mixed_with_page = origin == "publisher_page_and_search_extract" and fetched_page
    origin_ok = origin in {"", "publisher_page", "publisher-page", "publisher_page_crawl", "publisher_page_fetch", "publisher"} or gallery_excerpt_verified or legacy_gallery_excerpt_verified or table_excerpt_verified or mixed_with_page
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
    origin = str(source.get("evidenceOrigin") or source.get("evidence_origin") or "").strip().casefold()
    kind = str(source.get("kind") or source.get("source_type") or source.get("sourceType") or "").strip().casefold()
    workbook_excerpt = str(source.get("chartEvidenceExcerpt") or "").strip()
    # A user-uploaded workbook is primary evidence for a file-based report. It is
    # not a web publisher page, but its exact row excerpt may still validate a
    # chart point when evidenceSource/evidenceQuote/evidenceValue match that row.
    if origin == "uploaded_workbook" and "uploaded" in kind and workbook_excerpt:
        return workbook_excerpt
    if is_trusted_calculated_sector_source(source):
        return workbook_excerpt
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


def _matrix_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _matrix_entity_matches(expected: object, observed: object) -> bool:
    """Match a displayed issuer label to the same source label with legal suffixes."""
    expected_key = _matrix_key(expected)
    observed_key = _matrix_key(observed)
    if not expected_key or not observed_key:
        return False
    if expected_key == observed_key:
        return True
    suffixes = ("limited", "ltd", "incorporated", "inc", "corporation", "corp", "company", "co")

    def without_suffix(value: str) -> str:
        for suffix in suffixes:
            if value.endswith(suffix) and len(value) > len(suffix):
                return value[:-len(suffix)]
        return value

    expected_base = without_suffix(expected_key)
    observed_base = without_suffix(observed_key)
    if expected_base == observed_base:
        return True

    def acronym(value: object) -> str:
        words = [word for word in re.findall(r"[a-z0-9]+", str(value or "").casefold())
                 if word not in {"of", "and", "the"}]
        return "".join(word[0] for word in words) if len(words) >= 2 else ""

    return bool(
        (len(expected_base) >= 2 and acronym(observed) == expected_base)
        or (len(observed_base) >= 2 and acronym(expected) == observed_base)
    )


_METRIC_COLUMN_ALIASES = {
    "grossnpa": {"grossnpa", "gnpa", "grossnonperformingassets", "grossnonperformingadvances"},
    "netnpa": {"netnpa", "nnpa", "netnonperformingassets", "netnonperformingadvances"},
    "roe": {"roe", "returnonequity", "returnonnetworth", "ronw"},
    "roa": {"roa", "returnonassets"},
    "nim": {"nim", "netinterestmargin"},
    "car": {"car", "crar", "capitaladequacy", "capitaladequacyratio"},
    "pe": {"pe", "peratio", "pricetoearnings", "pricetoearningsratio"},
    "pb": {"pb", "pbratio", "pricetobook", "pricetobookratio"},
    "cdratio": {"cdratio", "cd", "creditdepositratio"},
}


def _heatmap_column_matches(chart: dict, column_index: int, observed: object) -> bool:
    columns = chart.get("columns") if isinstance(chart.get("columns"), list) else []
    if not 0 <= column_index < len(columns):
        return False
    observed_key = _matrix_key(observed)
    expected_keys = {_matrix_key(columns[column_index])}
    metric_keys = chart.get("columnMetricKeys") or chart.get("column_metrics") or []
    if isinstance(metric_keys, list) and column_index < len(metric_keys):
        metric_key = _matrix_key(metric_keys[column_index])
        if metric_key:
            expected_keys.add(metric_key)
            expected_keys.update(_METRIC_COLUMN_ALIASES.get(metric_key, set()))
    return bool(observed_key and observed_key in expected_keys)


def _pipe_cells(line: str) -> list[str]:
    raw = str(line or "").strip()
    if "|" not in raw:
        return []
    return [cell.strip() for cell in raw.strip("|").split("|")]


def _matrix_value_matches(chart_value: object, source_value: object) -> bool:
    """Compare one heatmap cell with the exact cited source-table cell."""
    source_text = str(source_value or "").strip()
    missing = {"", "-", "—", "–", "n/a", "na", "not available", "unavailable", "unknown"}
    if chart_value is None:
        return source_text.casefold() in missing
    chart_match = _NUMBER.fullmatch(str(chart_value).strip())
    source_match = _NUMBER.fullmatch(source_text)
    if not chart_match or not source_match:
        return False
    try:
        plotted = float(chart_match.group(1).replace(",", ""))
        observed = float(source_match.group(1).replace(",", ""))
    except ValueError:
        return False
    if observed == 0:
        return plotted == 0
    ratio = plotted / observed
    return any(abs(ratio - scale) <= max(1e-6, abs(scale) * 1e-6) for scale in _QUOTE_SCALES)


def _calculated_sector_heatmap_cell_matches(
    chart: dict, source: dict, row_index: int, column_index: int,
) -> bool:
    """Match a calculated heatmap cell to its quarter-aligned source row."""
    if not is_trusted_calculated_sector_source(source):
        return False
    excerpt = str(source.get("chartEvidenceExcerpt") or "").strip()
    columns = chart.get("columns") if isinstance(chart.get("columns"), list) else []
    rows = chart.get("rows") if isinstance(chart.get("rows"), list) else []
    values = chart.get("values") if isinstance(chart.get("values"), list) else []
    if not (0 <= row_index < len(rows) and 0 <= row_index < len(values)
            and isinstance(values[row_index], list) and 0 <= column_index < len(columns)
            and column_index < len(values[row_index])):
        return False
    coverage = re.search(r"(?im)^Quarter coverage:\s*(.+?)\s*$", excerpt)
    if not coverage:
        return False
    source_columns = [value.strip() for value in coverage.group(1).split(",") if value.strip()]
    if [_matrix_key(value) for value in source_columns] != [_matrix_key(value) for value in columns]:
        return False
    target_row = _matrix_key(rows[row_index])
    source_cell = None
    for line in excerpt.splitlines():
        cells = [cell.strip() for cell in line.split("|")]
        if len(cells) != len(source_columns) + 1 or _matrix_key(cells[0]) != target_row:
            continue
        source_cell = cells[column_index + 1]
        break
    return source_cell is not None and _matrix_value_matches(
        values[row_index][column_index], source_cell,
    )


def _heatmap_cell_matches_source(
    chart: dict, source: dict, row_index: int, column_index: int,
    require_publisher_verification: bool,
) -> bool:
    """Require a heatmap value in the exact cited row and column."""
    if _calculated_sector_heatmap_cell_matches(chart, source, row_index, column_index):
        return True
    evidence = _chart_source_text(source, require_publisher_verification)
    if not evidence:
        return False
    rows = chart.get("rows") if isinstance(chart.get("rows"), list) else []
    columns = chart.get("columns") if isinstance(chart.get("columns"), list) else []
    values = chart.get("values") if isinstance(chart.get("values"), list) else []
    if not (0 <= row_index < len(rows) and 0 <= row_index < len(values)
            and isinstance(values[row_index], list) and 0 <= column_index < len(columns)
            and column_index < len(values[row_index])):
        return False
    target_value = values[row_index][column_index]
    # Publisher pages and workbooks commonly expose a machine-readable Markdown
    # table. A value elsewhere on the page is not enough: both its entity row
    # and its exact period column must match this cell.
    for headers, source_rows in _tables(evidence):
        matching_columns = [i for i, header in enumerate(headers)
                            if _heatmap_column_matches(chart, column_index, header)]
        if not matching_columns:
            continue
        for source_row in source_rows:
            if not source_row or not _matrix_entity_matches(rows[row_index], source_row[0]):
                continue
            for matching_column in matching_columns:
                if matching_column < len(source_row) and _matrix_value_matches(
                    target_value, source_row[matching_column],
                ):
                    return True
    # Some publisher pages and the evidence dataset use pipe-delimited tables
    # without a Markdown separator row. Match those with the same exact entity,
    # metric column, and value requirements; never accept a nearby number alone.
    lines = evidence.splitlines()
    for header_index, header_line in enumerate(lines):
        headers = _pipe_cells(header_line)
        matching_columns = [i for i, header in enumerate(headers)
                            if _heatmap_column_matches(chart, column_index, header)]
        if not matching_columns:
            continue
        for source_line in lines[header_index + 1:]:
            if not source_line.strip():
                break
            source_row = _pipe_cells(source_line)
            if not source_row:
                break
            if re.fullmatch(r"\s*:?-{2,}:?\s*", source_line.strip().strip("|")):
                continue
            if not _matrix_entity_matches(rows[row_index], source_row[0]):
                continue
            for matching_column in matching_columns:
                if matching_column < len(source_row) and _matrix_value_matches(
                    target_value, source_row[matching_column],
                ):
                    return True
    return False


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
    chart_subject = " ".join([
        title,
        str(chart.get("xLabel") or ""),
        str(chart.get("yLabel") or ""),
        " ".join(
            str(series.get("name") or series.get("label") or "")
            for series in chart.get("series", []) if isinstance(series, dict)
        ),
    ]).casefold()
    if re.search(r"\bpromoter\b", q) and re.search(
        r"\b(?:stake\s+sales?|exits?|shareholding|bulk\s+deals?|block\s+deals?|pledges?|promoter\s+holdings?)\b", q
    ):
        # A nearby report paragraph can mention promoter exits even when a
        # model-generated chart is about an unrelated portfolio metric. Require
        # the chart's own title/axes/series to identify ownership or a transaction.
        if not re.search(
            r"\b(?:promoter|stake|shareholding|ownership|shares?\s+(?:sold|transacted)|"
            r"sale|seller|exit|pledge|bulk\s+deal|block\s+deal|holding\s+change)\b",
            chart_subject,
        ):
            return False
    if re.search(r"\b(?:fta|free[- ]trade|trade agreement)\b", q):
        if not re.search(
            r"\b(?:fta|free[- ]trade|trade agreement|tariff|export|import|trade|partner|market access|"
            r"rules? of origin|utili[sz]ation|concession)\b",
            chart_subject,
        ):
            return False
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
        # Deterministic workbook charts are already selected from the uploaded
        # workbook's actual metric columns. A generic prompt ("analyze this data")
        # may not repeat terms such as "return" or "PMS", so the generic topical
        # relevance heuristic incorrectly discarded every valid workbook chart.
        # Trust this bypass only for the server-set provenance flag + origin; the
        # normal evidence/value checks and duplicate-signature checks still run.
        server_workbook_chart = (
            isinstance(chart, dict)
            and chart.get("_serverWorkbookEvidence") is True
            and str(chart.get("sourceOrigin") or "").strip().casefold() == "uploaded_workbook"
        )
        v2_validated = _chart_observation_signature(chart) in _TRUSTED_CHART_SIGNATURES.get()
        keep = server_workbook_chart or v2_validated or _chart_is_relevant(chart, report, question, idx)
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
            # Spreadsheet exports may serialize percentage cells as either a
            # decimal fraction (0.248) or a display string (24.8%). Strip the
            # display suffix for numeric comparison; the chart unit and explicit
            # workbook evidence still govern whether the scale is acceptable.
            # Publishers often print a typographic minus (U+2212) or en dash;
            # float() rejects both, which silently failed every negative point.
            plotted_text = str(value).replace(",", "").replace("−", "-").replace("–", "-").strip().rstrip("% ")
            evidence_text = str(evidence_value).replace(",", "").replace("−", "-").replace("–", "-").strip().rstrip("% ")
            plotted = abs(float(plotted_text))
            quoted = abs(float(evidence_text))
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
    # Typographic minus (U+2212) / en dash: treat as "-" on both sides.
    value = str(value or "").replace("−", "-").replace("–", "-")
    evidence = str(evidence or "").replace("−", "-").replace("–", "-")
    parsed = _NUMBER.fullmatch(value.strip())
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


# ---------------------------------------------------------------------------
# Calendar-quarter period alignment
#
# A chart point labelled (or titled) "Q2 2025" must be supported by the Q2 2025
# column of a source table, not by any value in a row that happens to mention
# 2025. Before this, only 4-digit years were compared, so a Q3 value could be
# plotted under a Q2 title as long as the entity and number existed in the row.
# ---------------------------------------------------------------------------
_QUARTER_TOKEN = re.compile(r"\bQ([1-4])\s*[-\u2013']?\s*((?:19|20)\d{2})\b", re.I)
_QUARTER_HEADER_RUN = re.compile(
    r"(?:Q[1-4]\s*[-\u2013']?\s*(?:19|20)\d{2}\s*\|\s*){2,}Q[1-4]\s*[-\u2013']?\s*(?:19|20)\d{2}", re.I,
)
_CELL_NUMBER = re.compile(r"[-+\u2212]?\d[\d,]*(?:\.\d+)?")


def quarter_tokens(*parts: object) -> set[str]:
    """Canonical calendar-quarter tokens ("Q2 2025") found in the given text parts."""
    text = " ".join(str(p or "") for p in parts)
    return {f"Q{m.group(1)} {m.group(2)}" for m in _QUARTER_TOKEN.finditer(text)}


def quarter_column_ok(own: str, context: str, value: object, wanted: set[str]) -> bool | None:
    """Check a table row's value sits under the wanted quarter column.

    Returns True/False when the header quarter row aligns with the row's cells,
    and None when the layout cannot be aligned (callers fall back to prose rules).
    """
    if not wanted or "|" not in str(own or ""):
        return None
    runs = _QUARTER_HEADER_RUN.findall(str(context or ""))
    if not runs:
        return None
    header = [
        next(iter(quarter_tokens(cell)), "")
        for cell in max(runs, key=len).split("|")
    ]
    if len(header) < 3 or not all(header):
        return None
    cells = [c.strip() for c in str(own).strip().strip("|").split("|")]
    offset = len(cells) - len(header)
    if offset < 0:
        return None
    try:
        target = float(str(value).replace(",", "").replace("\u2212", "-").rstrip("%"))
    except (TypeError, ValueError):
        return None
    columns = []
    for index, cell in enumerate(cells):
        if index < offset:
            continue
        match = _CELL_NUMBER.fullmatch(cell.replace("%", "").replace("*", "").strip())
        if not match:
            continue
        try:
            observed = float(match.group(0).replace(",", "").replace("\u2212", "-"))
        except ValueError:
            continue
        if abs(observed - target) <= max(0.001, abs(target) * 0.002):
            columns.append(header[index - offset])
    if not columns:
        return None
    return any(column in wanted for column in columns)


def quarter_window_ok(own: str, context: str, value: object, wanted: set[str]) -> bool:
    """True when a value's local evidence window agrees with the wanted quarter(s)."""
    if not wanted:
        return True
    column = quarter_column_ok(own, context, value, wanted)
    if column is not None:
        return column
    found = quarter_tokens(context)
    # No quarter mentioned nearby: leave it to the existing entity/year checks.
    return not found or bool(found & wanted)


_MONTH_NUMBER = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MONTH_NAME_RE = r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
_QUESTION_MONTH_YEAR_RE = re.compile(rf"\b({_MONTH_NAME_RE})\.?\s*,?\s*((?:19|20)\d{{2}})\b", re.I)
_QUESTION_MONTH_RANGE_RE = re.compile(
    rf"\b({_MONTH_NAME_RE})\.?\s*,?\s*((?:19|20)\d{{2}})\s*(?:[-\u2013\u2014]|to|through|until)\s*"
    rf"({_MONTH_NAME_RE})\.?\s*,?\s*((?:19|20)\d{{2}})\b", re.I,
)
_QUESTION_QUARTER_RE = re.compile(r"\bQ([1-4])\s*(?:(FY|CY)\s*)?['\u2019]?(\d{2,4})\b", re.I)
_QUESTION_FY_RE = re.compile(r"\bFY\s*['\u2019]?(\d{2,4})\b", re.I)
_QUESTION_ISO_DATE_RE = re.compile(r"\b((?:19|20)\d{2})-(\d{1,2})-(\d{1,2})\b")
_QUESTION_YEAR_RANGE_RE = re.compile(r"\b((?:19|20)\d{2})\s*(?:[-\u2013\u2014]|to|through|until)\s*((?:19|20)\d{2})\b", re.I)
_QUESTION_YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")
_QUESTION_PERIOD_ENDPOINT = (
    rf"Q[1-4]\s*(?:(?:FY|CY)\s*)?['\u2019]?\d{{2,4}}|(?:FY|CY)\s*['\u2019]?\d{{2,4}}|"
    rf"{_MONTH_NAME_RE}\.?\s*,?\s*(?:19|20)\d{{2}}|(?:19|20)\d{{2}}"
)
_QUESTION_EXPLICIT_RANGE_RE = re.compile(
    rf"(?i)\b(?:(?:from|between)\s+)?(?P<start>{_QUESTION_PERIOD_ENDPOINT})\s*"
    rf"(?:to|through|until|[-\u2013\u2014])\s*(?P<end>{_QUESTION_PERIOD_ENDPOINT})\b"
)
_QUESTION_BETWEEN_AND_RANGE_RE = re.compile(
    rf"(?i)\bbetween\s+(?P<start>{_QUESTION_PERIOD_ENDPOINT})\s+and\s+"
    rf"(?P<end>{_QUESTION_PERIOD_ENDPOINT})\b"
)


def _month_ordinal(year: int, month: int) -> int:
    return year * 12 + month - 1


def _year_number(raw: str) -> int:
    value = int(raw)
    return 2000 + value if value < 100 else value


def _fiscal_quarter_months(quarter: int, fiscal_end_year: int) -> tuple[int, int]:
    if quarter == 4:
        return _month_ordinal(fiscal_end_year, 1), _month_ordinal(fiscal_end_year, 3)
    start_month = 4 + (quarter - 1) * 3
    start_year = fiscal_end_year - 1
    return _month_ordinal(start_year, start_month), _month_ordinal(start_year, start_month + 2)


def _calendar_quarter_months(quarter: int, year: int) -> tuple[int, int]:
    start_month = (quarter - 1) * 3 + 1
    return _month_ordinal(year, start_month), _month_ordinal(year, start_month + 2)


def _period_ranges(text: object) -> list[tuple[int, int, str]]:
    """Parse explicit calendar/fiscal periods into inclusive month ranges."""
    remaining = str(text or "")
    ranges: list[tuple[int, int, str]] = []

    def add(start: int, end: int, label: str) -> None:
        if end < start:
            start, end = end, start
        ranges.append((start, end, label))

    def mask(match: re.Match) -> None:
        nonlocal remaining
        remaining = remaining[:match.start()] + (" " * (match.end() - match.start())) + remaining[match.end():]

    for match in list(_QUESTION_QUARTER_RE.finditer(remaining)):
        quarter, basis, raw_year = int(match.group(1)), (match.group(2) or "").upper(), match.group(3)
        year = _year_number(raw_year)
        if basis == "FY":
            start, end = _fiscal_quarter_months(quarter, year)
        else:
            start, end = _calendar_quarter_months(quarter, year)
        add(start, end, match.group(0))
        mask(match)

    for match in list(_QUESTION_ISO_DATE_RE.finditer(remaining)):
        year, month = int(match.group(1)), int(match.group(2))
        if 1 <= month <= 12:
            ordinal = _month_ordinal(year, month)
            add(ordinal, ordinal, match.group(0))
        mask(match)

    for match in list(_QUESTION_MONTH_RANGE_RE.finditer(remaining)):
        first_month = _MONTH_NUMBER.get(match.group(1).casefold()[:3])
        last_month = _MONTH_NUMBER.get(match.group(3).casefold()[:3])
        if first_month and last_month:
            add(_month_ordinal(int(match.group(2)), first_month),
                _month_ordinal(int(match.group(4)), last_month), match.group(0))
        mask(match)

    for match in list(_QUESTION_MONTH_YEAR_RE.finditer(remaining)):
        month_name = match.group(1).casefold()[:3]
        month = _MONTH_NUMBER.get(month_name)
        if month:
            ordinal = _month_ordinal(int(match.group(2)), month)
            add(ordinal, ordinal, match.group(0))
        mask(match)

    for match in list(_QUESTION_YEAR_RANGE_RE.finditer(remaining)):
        first, last = int(match.group(1)), int(match.group(2))
        add(_month_ordinal(first, 1), _month_ordinal(last, 12), match.group(0))
        mask(match)

    for match in list(_QUESTION_FY_RE.finditer(remaining)):
        fiscal_end_year = _year_number(match.group(1))
        add(_month_ordinal(fiscal_end_year - 1, 4), _month_ordinal(fiscal_end_year, 3), match.group(0))
        mask(match)

    for match in list(_QUESTION_YEAR_RE.finditer(remaining)):
        year = int(match.group(1))
        add(_month_ordinal(year, 1), _month_ordinal(year, 12), match.group(0))
        mask(match)
    return ranges


def _question_time_windows(question: object) -> list[tuple[int, int, str]]:
    """Resolve explicit or relative time limits from the user's request."""
    text = str(question or "")
    # Stage J: "since 2022" / "from 2022" (open-ended) = January 2022 to the end of the current year,
    # not "only 2022"
    since = re.search(r"(?i)\b(?:since|from|after)\s+(?:the\s+(?:start|beginning)\s+of\s+)?((?:19|20)\d{2})\b(?!\s*(?:to|until|till|through|and|-|–|—)\s*(?:19|20)?\d{2})", text)
    if since:
        import datetime as _since_dt
        _today = _since_dt.date.today()
        return [(_month_ordinal(int(since.group(1)), 1), _month_ordinal(_today.year, 12), since.group(0))]
    relative = re.search(
        r"(?i)\b(last|past|previous|prior|preceding)\s+(\d+)\s+(days?|weeks?|months?|quarters?|years?)\b", text,
    )
    if relative:
        count = max(1, min(120, int(relative.group(2))))
        unit = relative.group(3).casefold()
        today = _dt.date.today()
        end_month = _month_ordinal(today.year, today.month)
        if unit.startswith("quarter"):
            # The window is the N completed calendar quarters plus the current quarter
            # to date (Stage D decision): current-quarter data is valid evidence; charts
            # label it "QTD, partial" and keep it apart from the completed quarters.
            current_q_start = _calendar_quarter_months((today.month - 1) // 3 + 1, today.year)[0]
            start_month = current_q_start - count * 3
            end_month = current_q_start + 2  # a "Q4 2026" / "Q3 FY27" label spans the whole current quarter
        elif unit.startswith("year"):
            # Stage J: whole calendar years, like quarters: the last N completed years plus the current
            # year to date ("past 3 years" in Nov 2026 = 2023-2026). A month-exact window (Dec 2023 -
            # Nov 2026) rejected every whole-year label but two, so no yearly series could pass.
            start_month = _month_ordinal(today.year - count, 1)
            end_month = _month_ordinal(today.year, 12)
        elif unit.startswith("week"):
            start_month = _month_ordinal((today - _dt.timedelta(days=count * 7)).year,
                                         (today - _dt.timedelta(days=count * 7)).month)
        elif unit.startswith("day"):
            start_month = _month_ordinal((today - _dt.timedelta(days=count)).year,
                                         (today - _dt.timedelta(days=count)).month)
        else:
            start_month = end_month - count + 1
        return [(start_month, end_month, relative.group(0))]

    explicit_range = _QUESTION_EXPLICIT_RANGE_RE.search(text) or _QUESTION_BETWEEN_AND_RANGE_RE.search(text)
    if explicit_range:
        start_periods = _period_ranges(explicit_range.group("start"))
        end_periods = _period_ranges(explicit_range.group("end"))
        if start_periods and end_periods:
            start_month = min(period[0] for period in start_periods)
            end_month = max(period[1] for period in end_periods)
            return [(start_month, end_month, explicit_range.group(0))]

    text_low = text.casefold()
    today = _dt.date.today()
    current_month = _month_ordinal(today.year, today.month)
    if re.search(r"\b(?:year to date|ytd|this year)\b", text_low):
        return [(_month_ordinal(today.year, 1), current_month, "year to date")]
    if re.search(r"\b(?:last|previous|prior)\s+quarter\b", text_low):
        current_q_start = _calendar_quarter_months((today.month - 1) // 3 + 1, today.year)[0]
        end_month = current_q_start - 1
        return [(end_month - 2, end_month, "last completed quarter")]
    if re.search(r"\bcurrent\s+quarter\b", text_low):
        start_month, _ = _calendar_quarter_months((today.month - 1) // 3 + 1, today.year)
        return [(start_month, current_month, "current quarter")]
    if re.search(r"\b(?:latest|current|recent|most recent)\b", text_low):
        return [(current_month - 11, current_month, "recent 12-month window")]
    return _period_ranges(text)


def _periods_outside_question_window(
    question: object,
    observed_texts: list[object],
    fallback_texts: list[object] | None = None,
) -> list[str]:
    """Return explicitly observed periods outside the requested time window.

    Point labels, chart titles, and the source quote are primary evidence for
    the plotted period. Publisher metadata is only a fallback for charts whose
    point labels and quotes omit their reporting period (for example, a chart
    titled only "1-month returns" from an April 2024 article).
    """
    wanted = _question_time_windows(question)
    if not wanted:
        return []

    def periods(parts: list[object] | None) -> list[tuple[int, int, str]]:
        found: list[tuple[int, int, str]] = []
        seen: set[tuple[int, int, str]] = set()
        for part in parts or []:
            for item in _period_ranges(part):
                if item not in seen:
                    seen.add(item)
                    found.append(item)
        return found

    observed = periods(observed_texts)
    candidates = observed or periods(fallback_texts)
    outside: list[str] = []
    for start, end, label in candidates:
        if not any(start >= wanted_start and end <= wanted_end for wanted_start, wanted_end, _ in wanted):
            if label not in outside:
                outside.append(label)
    return outside[:3]


def _source_period_metadata(source: dict | None) -> list[str]:
    if not isinstance(source, dict):
        return []
    return [str(source.get(key) or "") for key in (
        "title", "publisher_page_title", "publisherPageTitle", "period", "dataPeriod", "data_period",
        "publication_date", "publicationDate", "published_at", "publishedAt",
        "publisherPagePublishedAt", "publisher_page_published_at",
        "url", "publisher_page_url", "publisherPageUrl",
    ) if str(source.get(key) or "").strip()]


def drop_charts_outside_requested_window(
    report_text: str,
    charts: list | None,
    sources: list | None,
    question: object,
) -> tuple[str, list[dict], dict]:
    """Drop whole charts when their plotted data or source period is out of scope.

    Some non-calendar charts have no period in their title or point labels. In
    that case, use the cited publisher title, URL, and publication metadata as
    the period signal. Explicit periods on chart points take precedence, so a
    current series in an older publication can still pass.
    """
    original = list(charts or [])
    if not original or not _question_time_windows(question):
        return str(report_text or ""), original, {
            "removedCharts": 0, "removedIndices": [], "findings": [], "narrationLinesRemoved": 0,
        }

    source_by_id: dict[str, dict] = {}
    for source in sources or []:
        if not isinstance(source, dict):
            continue
        source_id = str(source.get("id") or "").strip().upper()
        if source_id:
            source_by_id[source_id] = source

    def out_of_window(observed: list[object], source_ids: list[str]) -> list[str]:
        explicit = _period_ranges(" ".join(str(part or "") for part in observed))
        if explicit:
            return _periods_outside_question_window(question, observed)
        outside: list[str] = []
        for source_id in source_ids:
            outside.extend(_periods_outside_question_window(
                question, observed, _source_period_metadata(source_by_id.get(source_id)),
            ))
        return list(dict.fromkeys(outside))[:3]

    stale_indices: set[int] = set()
    findings: list[dict] = []
    for chart_index, chart in enumerate(original, start=1):
        if not isinstance(chart, dict):
            continue
        title = str(chart.get("title") or f"Chart {chart_index}")
        chart_source_ids = _source_ids(chart)
        chart_context = [
            title, str(chart.get("xLabel") or ""), str(chart.get("yLabel") or ""),
            str(chart.get("unit") or ""),
        ]
        stale: tuple[list[str], list[str]] | None = None

        for series_index, point_index, series_name, label, _value in _indexed_points(chart):
            series_rows = chart.get("series") or []
            series_row = series_rows[series_index] if series_index < len(series_rows) else {}
            point_rows = (series_row.get("data") or series_row.get("values") or []) if isinstance(series_row, dict) else []
            point = point_rows[point_index] if point_index < len(point_rows) else {}
            point_id = str(
                point.get("evidenceSource") or point.get("evidence_source") or ""
            ).strip().upper() if isinstance(point, dict) else ""
            source_ids = [point_id] if point_id else chart_source_ids
            observed = [
                label, series_name, *chart_context,
                str(point.get("period") or "") if isinstance(point, dict) else "",
                str(point.get("date") or "") if isinstance(point, dict) else "",
                str(point.get("evidenceQuote") or point.get("evidence_quote") or "") if isinstance(point, dict) else "",
            ]
            outside = out_of_window(observed, source_ids)
            if outside:
                stale = (source_ids, outside)
                break

        if stale is None and str(chart.get("type") or chart.get("chartType") or "").casefold() == "heatmap":
            rows = chart.get("rows") if isinstance(chart.get("rows"), list) else []
            columns = chart.get("columns") if isinstance(chart.get("columns"), list) else []
            values = chart.get("values") if isinstance(chart.get("values"), list) else []
            for row_index, row in enumerate(values):
                if not isinstance(row, list):
                    continue
                for column_index, value in enumerate(row):
                    if not _numeric(value):
                        continue
                    row_label = str(rows[row_index]) if row_index < len(rows) else ""
                    column_label = str(columns[column_index]) if column_index < len(columns) else ""
                    observed = [column_label, row_label, *chart_context]
                    outside = out_of_window(observed, chart_source_ids)
                    if outside:
                        stale = (chart_source_ids, outside)
                        break
                if stale is not None:
                    break

        if stale is None and not _indexed_points(chart) and chart_source_ids:
            outside = out_of_window(chart_context, chart_source_ids)
            if outside:
                stale = (chart_source_ids, outside)

        if stale is not None:
            source_ids, periods = stale
            stale_indices.add(chart_index)
            findings.append({
                "chartIndex": chart_index,
                "chartTitle": title[:180],
                "sourceIds": source_ids,
                "sourcePeriod": ", ".join(periods),
                "reason": "The chart source period falls outside the time window requested by the user.",
            })

    if not stale_indices:
        return str(report_text or ""), original, {
            "removedCharts": 0, "removedIndices": [], "findings": [], "narrationLinesRemoved": 0,
        }

    text, removed_lines = remove_stale_chart_narration(
        str(report_text or ""), original, stale_indices, findings,
    )
    keep_mask = [index not in stale_indices for index in range(1, len(original) + 1)]
    text = remap_chart_placeholders(text, keep_mask)
    kept = [chart for chart, keep in zip(original, keep_mask) if keep]
    return text, kept, {
        "removedCharts": len(stale_indices),
        "removedIndices": sorted(stale_indices),
        "findings": findings,
        "narrationLinesRemoved": removed_lines,
    }



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
                                     require_publisher_verification: bool = False,
                                     question: str = "") -> dict:
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
                    cell_text = str(cell or "").strip()
                    # Canonical Unicode em/en dashes are explicit normalized
                    # missing-value markers; legacy/plain dash spellings below
                    # still represent an absent row.
                    if _MISSING_CELL.fullmatch(cell_text):
                        cell_text = "not verified"
                    # A dash in an extracted comparison table means no row was
                    # observed for that entity/period. It is not an explicit
                    # claim that a chart's separately sourced observation is
                    # unverified.
                    if cell_text in {"-", "—", "–", "â€”", "â€“"}:
                        continue
                    if not (
                        _UNVERIFIED.search(cell_text)
                        or _UNVERIFIED_CELL.fullmatch(cell_text)
                        or _MISSING_CELL.fullmatch(cell_text)
                    ):
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
            record_quote = str(record.get("quote") or "")
            record_windows = _value_windows(
                record.get("evidenceValue") or record.get("value"), record_quote,
            )
            outside_periods = _periods_outside_question_window(
                question,
                [
                    " ".join(str(record.get(key) or "") for key in ("label", "period", "date", "quarter")),
                    " ".join((title, str(chart.get("xLabel") or ""), str(chart.get("yLabel") or ""))),
                    *[own for own, _context in record_windows],
                    *[context for _own, context in record_windows],
                ],
                _source_period_metadata(source),
            )
            if outside_periods:
                failures.append({
                    "chartIndex": chart_index, "chartTitle": title[:180], "sourceIds": [sid] if sid else [],
                    "label": str(record.get("label") or "")[:100], "value": str(record.get("value"))[:40],
                    "sourcePeriod": ", ".join(outside_periods),
                    "reason": "The chart source period falls outside the time window requested by the user.",
                })
                break
        if str(chart.get("type") or chart.get("chartType") or "").casefold() == "heatmap":
            rows = chart.get("rows") if isinstance(chart.get("rows"), list) else []
            columns = chart.get("columns") if isinstance(chart.get("columns"), list) else []
            values = chart.get("values") if isinstance(chart.get("values"), list) else []
            numeric_cells = [
                (row_index, column_index, cell)
                for row_index, row in enumerate(values)
                if isinstance(row, list)
                for column_index, cell in enumerate(row)
                if _numeric(cell)
            ]
            if numeric_cells:
                ids = _source_ids(chart)
                missing = sorted(set(ids) - set(source_by_id))
                failure = None
                if not ids:
                    failure = {
                        "chartIndex": chart_index, "chartTitle": title[:180], "sourceIds": [],
                        "reason": "Numeric heatmap cells have no source IDs attached to the chart.",
                    }
                elif missing:
                    failure = {
                        "chartIndex": chart_index, "chartTitle": title[:180], "sourceIds": missing,
                        "reason": "Heatmap source IDs do not resolve to the report source manifest.",
                    }
                else:
                    for row_index, column_index, value in numeric_cells:
                        row_label = str(rows[row_index])[:100] if row_index < len(rows) else ""
                        column_label = str(columns[column_index])[:100] if column_index < len(columns) else ""
                        supported = False
                        period_failure = None
                        for sid in ids:
                            source = source_by_id[sid]
                            if not _heatmap_cell_matches_source(
                                chart, source, row_index, column_index,
                                require_publisher_verification,
                            ):
                                continue
                            outside_periods = _periods_outside_question_window(
                                question,
                                [
                                    column_label,
                                    " ".join((title, str(chart.get("xLabel") or ""), str(chart.get("yLabel") or ""))),
                                ],
                                _source_period_metadata(source),
                            )
                            if outside_periods:
                                period_failure = {
                                    "chartIndex": chart_index, "chartTitle": title[:180],
                                    "row": row_label, "column": column_label, "value": str(value)[:40],
                                    "sourceIds": [sid], "sourcePeriod": ", ".join(outside_periods),
                                    "reason": "The chart source period falls outside the time window requested by the user.",
                                }
                                continue
                            supported = True
                            break
                        if not supported and period_failure:
                            failure = period_failure
                            break
                        if not supported:
                            failure = {
                                "chartIndex": chart_index, "chartTitle": title[:180],
                                "row": row_label, "column": column_label, "value": str(value)[:40],
                                "sourceIds": ids,
                                "reason": "The heatmap cell is not supported by the cited source row and period column.",
                            }
                            break
                if failure:
                    failures.append(failure)
            continue
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
        # Uploaded workbooks are primary evidence, but they are not publisher
        # webpages. For charts created by the deterministic server-side workbook
        # analytics path, validate each exact row quote and numeric scaling
        # directly. Requiring publisher-page verification here rejects valid
        # workbook points even though the row-level quote came from the workbook.
        server_workbook_chart = chart.get("_serverWorkbookEvidence") is True and str(
            chart.get("sourceOrigin") or ""
        ).strip().casefold() == "uploaded_workbook"
        for series_index, point_index, series, label, value, point in indexed_points:
            quote_sid = str(point.get("evidenceSource") or "").strip().upper()
            if server_workbook_chart and quote_sid in ids and quote_sid in source_by_id and point.get("evidenceQuote"):
                source_text[quote_sid] = str(point.get("evidenceQuote") or "")
            if quote_sid and point.get("evidenceQuote"):
                if _quote_supports(value, point.get("evidenceValue"), point.get("evidenceQuote"), source_text.get(quote_sid, "")):
                    quote_source = source_by_id.get(quote_sid)
                    quote_windows = _value_windows(
                        point.get("evidenceValue") or value, str(point.get("evidenceQuote") or ""),
                    )
                    outside_periods = _periods_outside_question_window(
                        question,
                        [
                            " ".join((label, series)),
                            " ".join((title, str(chart.get("xLabel") or ""), str(chart.get("yLabel") or ""))),
                            *[own for own, _context in quote_windows],
                            *[context for _own, context in quote_windows],
                        ],
                        _source_period_metadata(quote_source),
                    )
                    if outside_periods:
                        failures.append({
                            "chartIndex": chart_index, "seriesIndex": series_index, "pointIndex": point_index,
                            "chartTitle": title[:180], "series": series[:100], "label": label[:100],
                            "value": str(value)[:40], "sourceIds": [quote_sid],
                            "sourcePeriod": ", ".join(outside_periods),
                            "reason": "The chart source period falls outside the time window requested by the user.",
                        })
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
            # Point-level quarter wins; otherwise the chart's own title/axis names it
            # (a bar chart titled "... (Q2 2025)" with sector labels).
            wanted_quarters = (
                quarter_tokens(label) or quarter_tokens(series)
                or quarter_tokens(title, chart.get("xLabel"))
            )
            supported = False
            local_context_miss = False
            stale_periods: list[str] = []
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
                        and quarter_window_ok(own, context, value, wanted_quarters)
                        for own, context in windows
                    )
                    if not local_ok:
                        local_context_miss = True
                        continue
                outside_periods = _periods_outside_question_window(
                    question,
                    [
                        " ".join((label, series)),
                        " ".join((title, str(chart.get("xLabel") or ""), str(chart.get("yLabel") or ""))),
                        *[own for own, _context in windows],
                        *[local for _own, local in windows],
                    ],
                    _source_period_metadata(source_by_id.get(sid)),
                )
                if outside_periods:
                    stale_periods = outside_periods
                    continue
                supported = True
                break
            if supported:
                continue
            status = ", ".join(sorted({str(source_by_id[sid].get("pageFetchStatus") or source_by_id[sid].get("page_fetch_status") or "unknown")
                                       for sid in ids if sid in source_by_id})) or "unavailable"
            reason = ("The cited chart source has no usable extracted evidence text." if not any(source_text.values())
                      else "The chart source period falls outside the time window requested by the user."
                      if stale_periods
                      else "The value appears in the cited source, but not in the same sentence or table row as this chart point's subject and period (likely a neighbouring cell or a different metric)."
                      if local_context_miss
                      else "No cited source excerpt supports this chart value together with its subject and period.")
            failures.append({
                "chartIndex": chart_index, "seriesIndex": series_index, "pointIndex": point_index,
                "chartTitle": title[:180], "series": series[:100],
                "label": label[:100], "value": str(value)[:40],
                "unit": str(chart.get("unit") or chart.get("yLabel") or "")[:60],
                "sourceIds": ids, "sourceFetchStatus": status, "reason": reason,
                **({"sourcePeriod": ", ".join(stale_periods)} if stale_periods else {}),
            })
    return {"chartTableVerificationConflicts": conflicts[:50], "chartEvidenceFailures": failures[:100],
            "blocking": bool(conflicts or failures)}


_CHART_PLACEHOLDER = re.compile(r"\[CHART_(\d+)\]")


def remove_stale_chart_narration(
    report_text: str,
    charts: list | None,
    stale_chart_indices: set[int] | list[int] | tuple[int, ...],
    findings: list[dict] | None = None,
) -> tuple[str, int]:
    """Remove only adjacent prose/captions that describe rejected stale charts."""
    lines = str(report_text or "").splitlines()
    if not lines or not stale_chart_indices:
        return str(report_text or ""), 0

    failure_sources: dict[int, set[str]] = {}
    for finding in findings or []:
        if not isinstance(finding, dict) or not str(finding.get("chartIndex", "")).isdigit():
            continue
        if finding.get("reason") != "The chart source period falls outside the time window requested by the user.":
            continue
        index = int(finding["chartIndex"])
        failure_sources.setdefault(index, set()).update(
            str(source_id).strip().upper() for source_id in (finding.get("sourceIds") or []) if str(source_id).strip()
        )

    citation_re = re.compile(r"(?i)\bS\d+\b")
    duration_re = re.compile(r"(?i)\b\d+\s*[- ]?\s*(?:months?|quarters?|weeks?|days?|years?)\b")
    caption_re = re.compile(
        r"(?i)\b(?:each\s+point|plotted|reported\s+figures|returns?\s+across|"
        r"observations?\s+across|points?\s+(?:show|represent|position|plot)|"
        r"over\s+\d+\s+reported\s+periods?|positioned\s+by\s+\w+|"
        r"windows?\s+(?:ending|through|as\s+of))\b"
    )
    metric_re = re.compile(r"(?i)\b(?:returns?|performance|sector|indices?|index|periods?|window|trailing)\b")
    structural_re = re.compile(r"(?i)^\s*(?:#{1,6}\s|[-*+]\s|\d{1,3}[.)]\s|\||>\s*\[?CHART_|\[CHART_\d+\])")
    remove_lines: set[int] = set()

    def _nearby_paragraph(marker_line: int, direction: int) -> tuple[list[int], str]:
        cursor = marker_line + direction
        skipped_blank = 0
        while 0 <= cursor < len(lines) and not lines[cursor].strip():
            skipped_blank += 1
            if skipped_blank > 2:
                return [], ""
            cursor += direction
        if not 0 <= cursor < len(lines) or structural_re.search(lines[cursor].strip()):
            return [], ""
        indexes: list[int] = []
        while 0 <= cursor < len(lines) and lines[cursor].strip() and not structural_re.search(lines[cursor].strip()):
            indexes.append(cursor)
            if len(indexes) >= 4:
                break
            cursor += direction
        indexes.sort()
        return indexes, " ".join(lines[index].strip() for index in indexes)

    for raw_index in stale_chart_indices:
        try:
            chart_index = int(raw_index)
        except (TypeError, ValueError):
            continue
        if chart_index < 1 or chart_index > len(charts or []):
            continue
        chart = (charts or [])[chart_index - 1]
        if not isinstance(chart, dict):
            continue
        marker_re = re.compile(rf"\[CHART_{chart_index}\]", re.I)
        marker_line = next((i for i, line in enumerate(lines) if marker_re.search(line)), None)
        if marker_line is None:
            continue

        title = str(chart.get("title") or "")
        title_terms = {
            token for token in re.findall(r"[a-z0-9]+", title.casefold())
            if token not in {"the", "and", "for", "from", "with", "by", "vs", "versus", "of", "in", "across", "ranked", "chart"}
        }
        source_ids = set(_source_ids(chart)) | failure_sources.get(chart_index, set())
        title_durations = {
            re.sub(r"[^a-z0-9]", "", match.group(0).casefold()) for match in duration_re.finditer(title)
        }

        for direction in (-1, 1):
            candidate_indexes, candidate = _nearby_paragraph(marker_line, direction)
            if not candidate_indexes:
                continue
            clean = re.sub(r"\[[Ss]\d+(?:\s*[,;]\s*[Ss]\d+)*\]", " ", candidate)
            clean = re.sub(r"[*_`]+", " ", clean)
            candidate_terms = set(re.findall(r"[a-z0-9]+", clean.casefold()))
            overlap = len(title_terms & candidate_terms) / max(1, min(len(title_terms), len(candidate_terms)))
            cited_sources = {item.upper() for item in citation_re.findall(candidate)}
            same_source = bool(source_ids & cited_sources)
            candidate_durations = {
                re.sub(r"[^a-z0-9]", "", match.group(0).casefold()) for match in duration_re.finditer(candidate)
            }
            same_horizon = bool(title_durations & candidate_durations)
            is_chart_caption = bool(caption_re.search(candidate))
            describes_stale_series = bool(metric_re.search(candidate))
            matches_stale_chart = (
                same_source and (is_chart_caption or overlap >= 0.25 or same_horizon)
            ) or (
                is_chart_caption and overlap >= 0.5
            ) or (
                same_horizon and describes_stale_series and (same_source or is_chart_caption)
            )
            if matches_stale_chart:
                remove_lines.update(candidate_indexes)

    if not remove_lines:
        return str(report_text or ""), 0
    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(line for i, line in enumerate(lines) if i not in remove_lines)).strip()
    return cleaned, len(remove_lines)


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
    # Capture blocking conflicts from the submitted payload before relevance or
    # duplicate pruning can remove the chart that demonstrates the mismatch.
    initial_audit = audit_chart_evidence_consistency(
        text, current, sources, require_publisher_verification=require_publisher_verification,
        question=question,
    )
    critical_conflicts = list(initial_audit.get("chartTableVerificationConflicts") or [])
    text, current, irrelevant_or_duplicate = _dedupe_chart_specs(text, current, question)
    text, caption_recovered_indices = _attach_chart_placeholders_to_matching_captions(text, current)
    if caption_recovered_indices:
        log.info("Chart publication recovered %d caption-matched inline placeholder(s): %s", len(caption_recovered_indices), caption_recovered_indices)
    removed_total = irrelevant_or_duplicate
    points_total = 0
    stale_narration_removed = 0
    # Preserve table/chart conflicts as blocking findings even if pruning removes
    # the contradictory chart. Silently deleting it would make export appear to
    # pass instead of surfacing a critical claim/evidence mismatch for repair.
    for _ in range(max_passes):
        audit = audit_chart_evidence_consistency(
            text, current, sources, require_publisher_verification=require_publisher_verification,
            question=question,
        )
        kept, removed_idx, removed_pts = prune_unsupported_chart_points(current, audit)
        if not removed_idx and not removed_pts:
            break
        if removed_idx:
            stale_indices = {
                int(item.get("chartIndex")) for item in (audit.get("chartEvidenceFailures") or [])
                if isinstance(item, dict) and str(item.get("chartIndex", "")).isdigit()
                and item.get("reason") == "The chart source period falls outside the time window requested by the user."
            }
            text, removed_narration = remove_stale_chart_narration(
                text, current, stale_indices & set(removed_idx), audit.get("chartEvidenceFailures") or [],
            )
            stale_narration_removed += removed_narration
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
        text, current, sources, require_publisher_verification=require_publisher_verification,
        question=question,
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
        "staleChartNarrationLinesRemoved": stale_narration_removed,
    }
