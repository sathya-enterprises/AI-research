"""Conservative normalization for model-generated report Markdown.

The report writer occasionally returns literal escaped newlines or serializes
multiple Markdown table rows onto one line. Normalize those presentation
artifacts before either the browser or PDF renderer sees the report. This is
formatting-only: it never invents or changes cell values.
"""
from __future__ import annotations

import re
import html as _html

_TABLE_ROW_RE = re.compile(r"^\s*\|(.*)\|\s*$")
import logging

_log = logging.getLogger("markdown_normalizer")


def _is_pipe_row(line: str) -> bool:
    stripped = str(line or "").strip()
    return stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 3


_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")
_INLINE_SOURCE_ID_RE = re.compile(r"\b(?:S|SRC)\s*[-#]?\s*(\d+)\b", re.IGNORECASE)


def sanitize_inline_citations(value: object, sources: object = None) -> str:
    """Keep only canonical source IDs in citation-like brackets.

    Model output can leave empty ``[]`` markers or mix a valid source ID with
    invented aliases (for example ``[S19, Tamb35]``). Preserve ordinary
    bracketed report text, but normalize citation groups against the report's
    canonical source manifest before either preview or PDF rendering.
    """
    text = str(value or "")
    known_ids: set[str] | None = None
    if sources is not None:
        known_ids = set()
        rows = sources if isinstance(sources, (list, tuple, set)) else [sources]
        for source in rows:
            raw_id = source.get("id") if isinstance(source, dict) else source
            match = re.fullmatch(r"\s*S\s*[-#]?\s*(\d+)\s*", str(raw_id or ""), re.IGNORECASE)
            if match:
                known_ids.add(f"S{match.group(1)}")

    def clean_group(inner: str, opening: str = "[", closing: str = "]") -> str:
        content = str(inner or "").strip()
        if not content or re.fullmatch(r"[,;|\s]+", content):
            return ""
        matches = list(_INLINE_SOURCE_ID_RE.finditer(content))
        if matches:
            ids = list(dict.fromkeys(f"S{match.group(1)}" for match in matches))
            if known_ids is not None:
                ids = [source_id for source_id in ids if source_id in known_ids]
            return f"[{', '.join(ids)}]" if ids else ""

        # Providers sometimes emit noncanonical citation aliases such as
        # [Tamb35] or [my27]. Do not mistake ordinary period labels/entities
        # such as [Q3], [FY25], or [Nifty50] for citation IDs.
        tokens = [part.strip() for part in re.split(r"[,;|]", content)]
        def source_like(token: str) -> bool:
            if re.fullmatch(r"(?i)Q[1-4]|(?:FY|CY|H[1-2])\s*['’]?\s*\d{1,4}|(?:NIFTY|SENSEX|NSE|BSE)\s*\d{1,4}", token):
                return False
            return bool(re.fullmatch(r"(?i)(?:SRC|S)\s*[-#]?\s*\d+|[A-Za-z]{2,12}\d{1,4}", token))
        if tokens and all(source_like(token) for token in tokens):
            return ""
        return f"{opening}{inner}{closing}"

    nested = re.compile(r"\[\[([^\[\]\n]{0,160})\]\]")
    text = nested.sub(lambda match: clean_group(match.group(1), "[[", "]]"), text)
    square = re.compile(r"\[([^\[\]\n]{0,160})\]")
    text = square.sub(lambda match: clean_group(match.group(1)), text)
    parens = re.compile(r"(?<![\w])\(([^()\n]{1,160})\)(?![\w])")
    text = parens.sub(
        lambda match: clean_group(match.group(1), "(", ")")
        if _INLINE_SOURCE_ID_RE.search(match.group(1)) else match.group(0),
        text,
    )
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"([,;:])(?:\s*[,;:])+", r"\1", text)
    text = re.sub(r"(?m)^[ \t]*[.,;:!?]+[ \t]*$", "", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _cells(line: str) -> list[str] | None:
    match = _TABLE_ROW_RE.match(line)
    if not match:
        return None
    # Markdown pipes inside cell text should be escaped. Split only on actual
    # delimiters, preserving empty cells until we know a row was flattened.
    return [cell.strip() for cell in match.group(1).split("|")]


def _infer_flattened_width(cells: list[str], header_width: int) -> int:
    """Infer one missing trailing source column from a flattened table row.

    Some model responses flatten multiple four-column records (metric, trend,
    latest value, source) beneath a three-column header, commonly because the
    source header was lost. Only widen when a candidate width is strongly
    supported by repeated citation/dash markers in its last column.
    """
    compact = [cell for cell in cells if cell.strip()]
    def source_like(value: str) -> bool:
        value = value.strip()
        return (
            not value
            or bool(re.fullmatch(r"\[S\d+\]", value, flags=re.IGNORECASE))
            or value.casefold() in {"—", "–", "-", "n/a", "na", "unknown", "source unavailable"}
        )

    if len(compact) == header_width + 1 and source_like(compact[-1]):
        return header_width + 1
    if len(compact) < header_width * 2:
        return header_width

    candidates: list[tuple[float, int]] = []
    for width in range(header_width + 1, min(8, header_width + 3) + 1):
        groups = [compact[i:i + width] for i in range(0, len(compact), width)]
        complete = [group for group in groups if len(group) == width]
        if len(complete) < 3:
            continue
        ratio = sum(source_like(group[-1]) for group in complete) / len(complete)
        if ratio >= 0.7:
            candidates.append((ratio, width))
    if not candidates:
        return header_width
    # Prefer the narrowest width with strong source-column evidence. This avoids
    # misreading arbitrary long rows as increasingly wide records.
    best_ratio = max(score for score, _ in candidates)
    best_widths = [width for score, width in candidates if score == best_ratio]
    return min(best_widths)


# Providers sometimes split this phrase across lines, omit the article, or
# HTML-escape the surrounding callout. Match the evidence marker itself as a
# safety net; final report text must never contain this internal drafting token.
_UNVERIFIED_METRIC_PLACEHOLDER_RE = re.compile(
    r"\b(?:(?:a|an)\s+)?(?:percentage|percent(?:age)?|amount|number|figure|numeric\s+value)\s+"
    r"(?:was\s+)?not\s+verified\s+in\s+retrieved\s+publisher[\s\-‐‑–]*page\s+evidence\b"
    r"|\bnot\s+verified\s+in\s+retrieved\s+publisher[\s\-‐‑–]*page\s+evidence\b"
    r"|\bnot\s+verified(?:\s+(?:y[\s\-]*o[\s\-]*y|year[\s\-]*over[\s\-]*year|by\s+source|in\s+(?:the\s+)?cited\s+sources?))?\b",
    flags=re.IGNORECASE,
)
_NOT_VERIFIED_CELL = "__GG_NOT_VERIFIED_TABLE_CELL__"


def _html_table_to_markdown(match: re.Match) -> str:
    """Convert a simple model-emitted HTML table to Markdown instead of leaking tags."""
    table_body = match.group(1) or ""
    rows = re.findall(r"<tr\b[^>]*>(.*?)</tr\s*>", table_body, flags=re.IGNORECASE | re.DOTALL)
    parsed_rows: list[list[str]] = []
    for row in rows:
        cells = []
        for cell in re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]\s*>", row, flags=re.IGNORECASE | re.DOTALL):
            cell = re.sub(r"<br\s*/?>", "; ", cell, flags=re.IGNORECASE)
            cell = re.sub(r"</?(?:strong|b)\b[^>]*>", "**", cell, flags=re.IGNORECASE)
            cell = re.sub(r"</?(?:em|i)\b[^>]*>", "*", cell, flags=re.IGNORECASE)
            cell = re.sub(r"<[^>]+>", "", cell)
            cell = re.sub(r"\s+", " ", cell).strip().replace("|", r"\|")
            cells.append(cell)
        if cells:
            parsed_rows.append(cells)
    if not parsed_rows:
        return "\n\n"
    width = max(len(row) for row in parsed_rows)
    normalized_rows = [row + [""] * (width - len(row)) for row in parsed_rows]
    output = ["| " + " | ".join(normalized_rows[0]) + " |", "| " + " | ".join(["---"] * width) + " |"]
    output.extend("| " + " | ".join(row) + " |" for row in normalized_rows[1:])
    return "\n\n" + "\n".join(output) + "\n\n"


def _normalize_html_fragments(text: str) -> str:
    """Convert raw or HTML-escaped presentation fragments into safe Markdown.

    Some model/provider paths return escaped tags such as ``\u003cdiv`` or
    ``&lt;div&gt;``. Decode only when the content visibly contains encoded HTML
    tag names, then convert/remove the wrappers. This also prevents literal tags
    from leaking into the chat preview or PDF text layer.
    """
    value = str(text or "")
    if re.search(r"&lt;\s*/?\s*(?:div|strong|b|em|i|p|section|article|ul|ol|li|table|thead|tbody|tr|td|th|h[1-6])\b", value, re.I):
        value = _html.unescape(value)
    # Preserve visible line breaks encoded by providers before normalizing tags.
    if re.search(r"\\u003c|\\u003e", value, re.I):
        value = re.sub(r"\\u003c", "<", value, flags=re.I)
        value = re.sub(r"\\u003e", ">", value, flags=re.I)
    # Handle tables before generic tag removal so their cell boundaries survive.
    value = re.sub(r"<table\b[^>]*>(.*?)</table\s*>", _html_table_to_markdown, value,
                   flags=re.IGNORECASE | re.DOTALL)
    # Preserve inline emphasis before dropping layout/style wrappers.
    value = re.sub(r"<strong\b[^>]*>", "**", value, flags=re.IGNORECASE)
    value = re.sub(r"</strong\s*>", "**", value, flags=re.IGNORECASE)
    value = re.sub(r"<b\b[^>]*>", "**", value, flags=re.IGNORECASE)
    value = re.sub(r"</b\s*>", "**", value, flags=re.IGNORECASE)
    value = re.sub(r"<em\b[^>]*>", "*", value, flags=re.IGNORECASE)
    value = re.sub(r"</em\s*>", "*", value, flags=re.IGNORECASE)
    value = re.sub(r"<i\b[^>]*>", "*", value, flags=re.IGNORECASE)
    value = re.sub(r"</i\s*>", "*", value, flags=re.IGNORECASE)
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
    value = re.sub(r"<h([1-6])\b[^>]*>(.*?)</h\1\s*>",
                   lambda m: "\n\n" + ("#" * int(m.group(1))) + " " + re.sub(r"<[^>]+>", "", m.group(2)).strip() + "\n\n",
                   value, flags=re.IGNORECASE | re.DOTALL)
    value = re.sub(r"<li\b[^>]*>", "\n- ", value, flags=re.IGNORECASE)
    value = re.sub(r"</li\s*>", "\n", value, flags=re.IGNORECASE)
    value = re.sub(r"</?(?:ul|ol)\b[^>]*>", "\n\n", value, flags=re.IGNORECASE)
    value = re.sub(r"</?(?:div|p|section|article|header|footer|aside|blockquote)\b[^>]*>", "\n\n", value,
                   flags=re.IGNORECASE)
    # Do not expose literal presentation markup in the final report/PDF.
    value = re.sub(r"</?[a-zA-Z][^>]*>", "", value)
    return value


def _remove_unverified_metric_placeholders(text: str) -> str:
    """Drop whole unsupported quantitative sentences; table cells say Not verified.

    Work at paragraph/unit level, not one physical line at a time. LLMs often wrap
    prose at line boundaries, which used to split ``a percentage`` from
    ``not verified...`` and bypass the line-local guard.
    """
    output: list[str] = []
    blocks = re.split(r"(\n[ \t]*\n)", str(text or ""))
    structural_start = re.compile(r"(?m)(?=^\s*(?:[-*+]\s+|\d+[.)]\s+|#{1,6}\s+|>\s+))")
    prefix_re = re.compile(r"^(\s*(?:(?:[-*+]\s+)|(?:\d+[.)]\s+)|(?:>\s+)|(?:#{1,6}\s+))?)")
    sentence_split_re = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\u201c\"'(\[])")
    for block in blocks:
        if not block or re.fullmatch(r"\n[ \t]*\n", block):
            output.append(block)
            continue
        lines = block.splitlines()
        nonempty = [line for line in lines if line.strip()]
        is_table = bool(nonempty) and any(line.lstrip().startswith("|") for line in nonempty) and all(
            line.lstrip().startswith("|") or _TABLE_SEP_RE.match(line) for line in nonempty
        )
        if is_table:
            safe_lines = []
            for line in lines:
                if not line.lstrip().startswith("|"):
                    safe_lines.append(line)
                    continue
                cells = line.strip().strip("|").split("|")
                cells = [_NOT_VERIFIED_CELL if _UNVERIFIED_METRIC_PLACEHOLDER_RE.search(cell) else cell.strip() for cell in cells]
                safe_lines.append("| " + " | ".join(cells) + " |")
            output.append("\n".join(safe_lines))
            continue
        # Separate headings/list entries before flattening wrapped continuation lines.
        units = structural_start.split(block)
        if not units:
            units = [block]
        for unit in units:
            if not unit:
                continue
            flattened = re.sub(r"\s+", " ", unit).strip()
            if not _UNVERIFIED_METRIC_PLACEHOLDER_RE.search(flattened):
                output.append(unit)
                continue
            prefix_match = prefix_re.match(flattened)
            prefix = prefix_match.group(1) if prefix_match else ""
            body = flattened[len(prefix):] if prefix else flattened
            # Protect common abbreviations before splitting full sentences.
            protected = re.sub(r"\b(U\.S|U\.K|e\.g|i\.e|etc)\.", lambda m: m.group(0).replace(".", "\ue000"), body, flags=re.I)
            sentences = sentence_split_re.split(protected)
            kept = [part.replace("\ue000", ".").strip() for part in sentences
                    if part.strip() and not _UNVERIFIED_METRIC_PLACEHOLDER_RE.search(part)]
            if kept:
                output.append(prefix + " ".join(kept))
            # Omit a whole sentence/unit if its claim depends on the unknown metric.
    value = "".join(output)
    # A final fail-safe: any remaining isolated drafting token in prose is removed
    # sentence-wise above; in residual non-sentence fragments, neutralize the token.
    value = _UNVERIFIED_METRIC_PLACEHOLDER_RE.sub("—", value)
    # Normalize blank lines while preserving ordinary block structure.
    value = re.sub(r"(?m)^\s*(?:[-*+]\s*)?$", "", value)
    value = re.sub(r"\n{3,}", "\n\n", value).strip()
    return value.replace(_NOT_VERIFIED_CELL, "Not verified")


def normalize_report_markdown(value: object) -> str:
    """Normalize model-generated report Markdown before UI and PDF rendering.

    In addition to escaped line breaks/table recovery, this converts common
    raw HTML callouts/tables to Markdown and removes unsupported-metric
    placeholders rather than publishing them verbatim.
    """
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    # SECTION TO COMPLETE is an internal continuation marker, not report prose.
    # Drop the whole control line because its trailing text is usually an unfinished
    # section title and the report contains the real heading elsewhere.
    text = re.sub(r"(?im)^[ \t]*\[{2,3}\s*SECTION TO COMPLETE\s*\]{2,3}[^\n]*(?:\n|$)", "", text)
    text = re.sub(r"\[{2,3}\s*SECTION TO COMPLETE\s*\]{2,3}", "", text, flags=re.I)
    text = _normalize_html_fragments(text)
    # Some writers wrap an internal chart token in Markdown image syntax, for
    # example ![Market projection](CHART_1). That is not a real image URL: it
    # bypasses the structured-chart renderer and leaks as raw Markdown in PDF.
    # Canonicalize it to the line-oriented chart placeholder used by both
    # HTML and PDF before any subsequent normalization or citation cleanup.
    text = re.sub(
        r"!\[[^\]\n]*\]\(\s*<?\s*\[?CHART_(\d+)\]?\s*>?\s*\)",
        lambda m: f"[CHART_{m.group(1)}]",
        text,
        flags=re.IGNORECASE,
    )
    # A chart JSON block inside a ```json fence is replaced by [CHART_n] (or
    # removed when invalid), which used to leave a fence around the placeholder
    # or an empty "```\n```" pair that renders as a blank code box.
    text = re.sub(r"^[ \t]*```[A-Za-z0-9_-]*[ \t]*\n\s*(\[CHART_\d+\])\s*\n[ \t]*```[ \t]*$", r"\1", text, flags=re.M | re.I)
    text = re.sub(r"^[ \t]*```[A-Za-z0-9_-]*[ \t]*\n(?:[ \t]*\n)*[ \t]*```[ \t]*$\n?", "", text, flags=re.M)
    # Some JSON/provider recovery paths leave the two-character sequences \\n    # and \\r instead of actual newlines in the report body.
    text = re.sub(r"(?<!\\)\\r\\n", "\n", text)
    text = re.sub(r"(?<!\\)\\n", "\n", text)
    text = re.sub(r"(?<!\\)\\r", "\n", text)
    # A source sentence and an ordered-list item occasionally arrive joined
    # without whitespace (for example, "... [S31].3. **The next section...").
    # Restore the structural line break before table/layout cleanup so the list
    # renders correctly and its ordinal cannot be mistaken for a factual value.
    # Never split inside a decimal: "(from 38.69 to 40.90) during" is one number,
    # not a sentence ending in "40." followed by a list item "90)".
    text = re.sub(
        r"(?<=[.!?])(?<!\d\.)(?=(?:\(?\d{1,3}[.)]|\(\d{1,3}\))[ \t]+(?:[*_#>]+[ \t]*)?\S)",
        "\n\n",
        text,
    )

    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        header = _cells(lines[i])
        if header and len(header) >= 2 and i + 1 < len(lines) and _TABLE_SEP_RE.match(lines[i + 1]):
            col_count = len(header)
            # Inspect the first data line before emitting the header. A known
            # malformed pattern loses the trailing "Source" header but leaves
            # repeated source IDs in flattened four-column body rows.
            first_data = _cells(lines[i + 2]) if i + 2 < len(lines) else None
            inferred_width = _infer_flattened_width(first_data or [], col_count) if first_data and len(first_data) > col_count else col_count
            if inferred_width == col_count + 1 and not any(re.search(r"source|citation|reference", cell, re.I) for cell in header):
                header = header + ["Source"]
            col_count = len(header)
            out.append("| " + " | ".join(header) + " |")
            # Canonicalize the separator while preserving left/center/right
            # alignment only insofar as the source expresses it.
            sep_cells = [c.strip() for c in lines[i + 1].strip().strip("|").split("|")]
            if len(sep_cells) != col_count:
                sep_cells = ["---"] * col_count
            out.append("| " + " | ".join(sep_cells) + " |")
            i += 2
            while i < len(lines):
                raw = lines[i]
                row_cells = _cells(raw)
                if row_cells is None:
                    break
                if len(row_cells) <= col_count:
                    # Conventional row: pad short rows, but never merge it with
                    # its neighbour or silently truncate a real extra column.
                    row = row_cells + [""] * (col_count - len(row_cells))
                    out.append("| " + " | ".join(row) + " |")
                else:
                    # A common model failure is multiple rows serialized as
                    # `|a|b|c|d|e|f|...|` after the header. Empty cells often
                    # mark a lost `|\n|` boundary; remove these only in this
                    # flattened-row recovery branch and group by the known width.
                    compact = [cell for cell in row_cells if cell.strip()]
                    if len(compact) < col_count * 2:
                        # This is more likely a single malformed row than a
                        # flattened sequence; retain it as a bounded row.
                        row = row_cells[:col_count] + [""] * max(0, col_count - len(row_cells))
                        out.append("| " + " | ".join(row) + " |")
                    else:
                        for start in range(0, len(compact), col_count):
                            group = compact[start:start + col_count]
                            if len(group) < col_count:
                                group += [""] * (col_count - len(group))
                            out.append("| " + " | ".join(group) + " |")
                i += 1
            # Let the outer loop emit the first non-table line without skipping it.
            continue

        # Remove stray Markdown hard-break backslashes and escaped ordered-list
        # punctuation seen in model output (e.g. `1\. Next item`).
        line = re.sub(r"\\\s*$", "", lines[i])
        line = re.sub(r"^(\s*)(\d+)\\\.", r"\1\2.", line)
        line = re.sub(r"^(\s*)\\>", r"\1>", line)
        out.append(line)
        i += 1

    normalized = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
    # Normalize provider-emitted full-width citation brackets. Leaving these
    # variants intact makes PDF citations visually inconsistent and prevents
    # the publication audit from resolving them against the canonical S# IDs.
    normalized = re.sub(r"[【〔［]\s*S(\d+)\s*[】〕］]", r"[S\1]", normalized, flags=re.I)
    normalized = re.sub(r"\]\s+([.,;:!?])", r"]\1", normalized)
    normalized = _strip_generation_control_markers(normalized)
    normalized = _put_visual_placeholders_on_own_lines(normalized)
    try:  # Stage I: text-art arrows/bars become sentences or are removed (the PDF renders no monospace art)
        from utils.flow_text import convert_text_art
        normalized, _art_actions = convert_text_art(normalized)
        if _art_actions:
            _log.info("Normalizer: text-art clean-up - %d change(s)", len(_art_actions))
    except Exception as exc:  # pragma: no cover - never block normalisation
        _log.warning("Normalizer: text-art clean-up skipped: %s", exc)
    normalized = _clean_ascii_diagram_frames(normalized)
    normalized = _dedupe_repeated_markdown_blocks(normalized)
    normalized = _remove_unverified_metric_placeholders(normalized)
    # A final guard: no common HTML presentation tags or unverified-metric
    # placeholders should leak into the browser preview or the exported PDF.
    normalized = _normalize_html_fragments(normalized)
    normalized = _remove_unverified_metric_placeholders(normalized)
    normalized = _remove_dangling_markdown_lead_ins(normalized)
    normalized = _remove_empty_markdown_headings(normalized)
    normalized = _reorder_executive_summary_first(normalized)
    normalized = _remove_empty_markdown_headings(normalized)
    normalized = _dedupe_repeated_markdown_blocks(normalized)
    return re.sub(r"\n{3,}", "\n\n", normalized).strip()


def _clean_ascii_diagram_frames(text: str) -> str:
    """Remove broken ASCII box lines while retaining their readable labels."""
    source_lines = str(text or "").splitlines()
    markdown_table_lines: set[int] = set()
    cursor = 0
    while cursor + 1 < len(source_lines):
        if "|" not in source_lines[cursor] or not _TABLE_SEP_RE.match(source_lines[cursor + 1]):
            cursor += 1
            continue
        table_end = cursor + 1
        while table_end + 1 < len(source_lines) and "|" in source_lines[table_end + 1] and source_lines[table_end + 1].strip():
            table_end += 1
        markdown_table_lines.update(range(cursor, table_end + 1))
        cursor = table_end + 1
    # Stage I (FIX 3): a block of two or more pipe rows that is not a valid table (its header row was
    # lost) is dropped with its "**Table: ...**" caption and "Source:" line, never flattened into
    # "a — b — c" text. A single stray pipe row is still cleaned as before.
    drop: set[int] = set()
    index = 0
    while index < len(source_lines):
        if index in markdown_table_lines or not _is_pipe_row(source_lines[index]):
            index += 1
            continue
        end = index
        while end + 1 < len(source_lines) and end + 1 not in markdown_table_lines and _is_pipe_row(source_lines[end + 1]):
            end += 1
        if end > index:
            drop.update(range(index, end + 1))
            before = index - 1
            while before >= 0 and not source_lines[before].strip():
                before -= 1
            if before >= 0 and re.match(r"^\s*\*\*Table\b[^\n]*\*\*\s*$", source_lines[before]):
                drop.add(before)
            after = end + 1
            while after < len(source_lines) and not source_lines[after].strip():
                after += 1
            if after < len(source_lines) and re.match(r"^\s*\*?Source:", source_lines[after]):
                drop.add(after)
            _log.info("Normalizer: dropped a %d-row table without a header row", end - index + 1)
        index = end + 1
    lines = []
    for index, line in enumerate(source_lines):
        if index in drop:
            continue
        if index in markdown_table_lines:
            lines.append(line)
            continue
        cleaned = re.sub(r"(?<!\w)[=|+_-]{4,}(?!\w)", " ", line)
        if cleaned.strip().startswith("|") and cleaned.strip().endswith("|") and cleaned.count("|") >= 2:
            cleaned = re.sub(r"^\s*\|\s*|\s*\|\s*$", "", cleaned)
            cleaned = re.sub(r"\s*\|\s*", " — ", cleaned).strip(" —")
        cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).rstrip()
        lines.append(cleaned)
    value = "\n".join(lines)
    value = re.sub(
        r"(?im)^([^\n]*\bForeign\s+Portfolio\s+Investors?)\s*\n\s*"
        r"\((FIIs?)\)\s+(Domestic\s+Institutional\s+Investors?\s*\(DIIs?\))[^\n]*$",
        r"\1 (\2) and \3",
        value,
    )
    value = re.sub(
        r"(?i)\b(Foreign\s+Portfolio\s+Investors?\s*(?:\(FIIs?\))?)\s*/\s*"
        r"(Domestic\s+Institutional\s+Investors?\s*(?:\(DIIs?\))?)\b",
        r"\1 and \2",
        value,
    )
    return re.sub(r"\n{3,}", "\n\n", value)


def _strip_generation_control_markers(text: str) -> str:
    """Remove internal continuation wrappers even when a provider leaks them.

    These tokens are generation protocol, never report content. The renderer
    also calls this normalizer as a last-mile safety net for stored/legacy
    reports that bypass the newest generation path.
    """
    value = str(text or "")
    value = re.sub(
        r"(?im)^\s*\[\[(?:SECTION:\s*.*?|END SECTION|SECTION TO EXPAND|END_SCAN_SECTION)\]\]\s*$",
        "",
        value,
    )
    # Also remove a marker if it was glued to other text by malformed output.
    value = re.sub(r"\[\[(?:SECTION:\s*.*?|END SECTION|SECTION TO EXPAND|END_SCAN_SECTION)\]\]", "", value, flags=re.I)
    return re.sub(r"\n{3,}", "\n\n", value).strip()


def _put_visual_placeholders_on_own_lines(text: str) -> str:
    """Make inline visual tokens parseable by the line-oriented renderer.

    Keep terminal sentence punctuation attached to the preceding prose and move
    the token itself onto its own line so the chart/image parser can resolve it.
    """
    token_re = re.compile(r"\[(?:CHART|WEB_IMG|FILE_IMG|PAGE_IMG|IMAGE)_\d+\]", re.I)
    output: list[str] = []
    for line in str(text or "").splitlines():
        matches = list(token_re.finditer(line))
        if not matches or (len(matches) == 1 and line.strip() == matches[0].group(0)):
            output.append(line)
            continue
        pieces: list[str] = []
        cursor = 0
        for match in matches:
            before = line[cursor:match.start()].rstrip()
            if before:
                pieces.append(before)
            pieces.append(match.group(0))
            cursor = match.end()
        tail = line[cursor:]
        # A punctuation mark immediately after the final token belongs to the
        # sentence that introduced it, not to the marker line itself.
        if len(matches) and re.fullmatch(r"\s*[.,;:!?]\s*", tail):
            punct = tail.strip()
            if len(pieces) >= 2:
                pieces[-2] = pieces[-2].rstrip() + punct
            tail = ""
        if tail.strip():
            pieces.append(tail.strip())
        output.append("\n\n".join(piece for piece in pieces if piece))
    return "\n".join(output)


def _dedupe_repeated_markdown_tables(text: str) -> str:
    """Remove duplicate table runs and repeated subsection labels.

    Model continuation output sometimes concatenates a table directly with a
    heading or repeats the same table in the middle of a larger paragraph
    block. Paragraph-only deduplication misses those cases. This pass consumes
    table rows independently of surrounding prose and also removes exact
    repeated H3/H4 or short numbered subsection labels within the same H2.
    Different table values and substantive claims are preserved.
    """
    lines = str(text or "").splitlines()
    out: list[str] = []
    seen: set[str] = set()
    seen_subheads: dict[str, set[str]] = {"__preamble__": set()}
    current_h2 = "__preamble__"
    i = 0

    def canonical_table_line(line: str) -> str:
        value = re.sub(r"\[S\d+(?:\s*,\s*S\d+)*\]", "", line, flags=re.I)
        value = re.sub(r"[*_`]+", "", value)
        value = re.sub(r"\s+", " ", value.casefold()).strip()
        return value

    while i < len(lines):
        current_line = lines[i]
        h2_match = re.match(r"^\s*##(?!#)\s+(.+?)\s*$", current_line)
        if h2_match:
            current_h2 = canonical_table_line(h2_match.group(1)) or "__unnamed_h2__"
            seen_subheads.setdefault(current_h2, set())
            out.append(current_line)
            i += 1
            continue

        # Continuation content occasionally repeats loose numbered labels
        # rather than Markdown headings. De-duplicate only exact short labels
        # in the same parent section; numbered narrative/list items with
        # sentence punctuation or long text are not considered headings.
        stripped_line = current_line.strip()
        heading_text = stripped_line
        is_heading = bool(re.match(r"^#{3,6}\s+.+$", stripped_line))
        if is_heading:
            heading_text = re.sub(r"^#{3,6}\s+", "", stripped_line)
        elif stripped_line.startswith("**") and stripped_line.endswith("**") and len(stripped_line) <= 132:
            heading_text = stripped_line.strip("*").strip()
            is_heading = True
        elif re.match(r"^\d+(?:\.\d+)*[.)]?\s+[A-Z].{4,100}$", stripped_line) and not re.search(r"[.!?]$", stripped_line):
            heading_text = stripped_line
            is_heading = True
        if is_heading and len(heading_text) <= 120:
            heading_signature = canonical_table_line(heading_text)
            current_seen = seen_subheads.setdefault(current_h2, set())
            if heading_signature in current_seen:
                i += 1
                continue
            current_seen.add(heading_signature)

        if i + 1 < len(lines) and "|" in lines[i] and _TABLE_SEP_RE.match(lines[i + 1]):
            table_lines = [lines[i], lines[i + 1]]
            j = i + 2
            while j < len(lines) and lines[j].strip() and _TABLE_ROW_RE.match(lines[j]):
                # Do not consume a second table header that starts a separate
                # table without a blank line; it will be considered next.
                if j + 1 < len(lines) and _TABLE_SEP_RE.match(lines[j + 1]):
                    break
                table_lines.append(lines[j])
                j += 1
            if len(table_lines) >= 3:
                signature = "\n".join(canonical_table_line(line) for line in table_lines)
                # Markdown parsers commonly require blank lines around tables.
                # Continuation responses sometimes omit them, causing a valid
                # table to be misread as prose or an unrelated diagram.
                if out and out[-1].strip():
                    out.append("")
                if signature not in seen:
                    seen.add(signature)
                    out.extend(table_lines)
                # Skip the table either way, but don't swallow the blank line,
                # heading, prose, or the next table header.
                i = j
                if i < len(lines) and lines[i].strip():
                    out.append("")
                continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


def _dedupe_repeated_markdown_blocks(text: str) -> str:
    """Remove exact repeated tables, lists, and substantive prose blocks.

    Continuation models can reproduce an earlier table with slightly different
    citation labels or emit a numbered subsection repeatedly. This cleanup is
    deliberately exact after conservative normalization: it does not merge
    different values, estimate missing observations, or rewrite prose.
    """
    text = _dedupe_repeated_markdown_tables(str(text or ""))
    parts = re.split(r"(\n\s*\n)", text)
    seen_tables: set[str] = set()
    seen_long_blocks: set[str] = set()
    seen_lists: set[tuple[str, str]] = set()
    seen_callouts: set[str] = set()
    seen_subheads: dict[str, set[str]] = {}
    seen_sentences: dict[str, set[str]] = {}
    current_h2 = "__preamble__"
    kept: list[str] = []

    def canon(value: str) -> str:
        value = re.sub(r"\[S\d+(?:\s*,\s*S\d+)*\]", "", value, flags=re.I)
        value = re.sub(r"`{1,3}|[*_]", "", value)
        value = re.sub(r"\s+", " ", value.casefold()).strip()
        return value

    i = 0
    while i < len(parts):
        block = parts[i]
        sep = parts[i + 1] if i + 1 < len(parts) else ""
        stripped = block.strip()
        if not stripped:
            kept.append(block)
            if sep:
                kept.append(sep)
            i += 2
            continue

        h2 = re.match(r"^##(?!#)\s+(.+?)\s*$", stripped)
        if h2:
            current_h2 = canon(h2.group(1))
            seen_subheads.setdefault(current_h2, set())
            seen_sentences.setdefault(current_h2, set())

        lines = stripped.splitlines()
        table_like = (
            len(lines) >= 3
            and any(_TABLE_SEP_RE.match(line) for line in lines)
            and all((not line.strip()) or _cells(line) is not None for line in lines)
        )
        if table_like:
            signature = "\n".join(canon(line) for line in lines if line.strip())
            if signature in seen_tables:
                i += 2
                continue
            seen_tables.add(signature)
            kept.append(block)
            if sep:
                kept.append(sep)
            i += 2
            continue

        is_list = bool(re.match(r"^(?:[-*+]\s|\d+[.)]\s)", stripped, re.M)) and len(lines) >= 2
        if is_list:
            signature = canon(stripped)
            list_key = (current_h2, signature)
            if list_key in seen_lists:
                i += 2
                continue
            seen_lists.add(list_key)
            kept.append(block)
            if sep:
                kept.append(sep)
            i += 2
            continue

        if stripped.startswith(">"):
            signature = canon(stripped)
            if signature in seen_callouts:
                i += 2
                continue
            seen_callouts.add(signature)

        # Drop exact repeated subsection labels only inside the same H2.
        # H1/H2 architecture is retained; this primarily catches repeated
        # continuation labels such as "1. Pricing Mix Evolution".
        headingish = bool(
            re.match(r"^(?:#{3,6}\s+.+|\d+(?:\.\d+)*[.)]?\s+[A-Za-z].{4,})$", stripped)
            or re.match(r"^\*\*[^*]{5,}\*\*$", stripped)
        )
        if headingish:
            signature = canon(re.sub(r"^#{3,6}\s+", "", stripped))
            scope = seen_subheads.setdefault(current_h2, set())
            if signature in scope:
                i += 2
                continue
            scope.add(signature)

        # Only long standalone prose blocks are eligible; citations are ignored
        # for duplicate detection, but numeric values and entity names remain.
        if len(stripped) >= 180 and not stripped.startswith(("#", ">", "[CHART_", "[WEB_IMG_", "[FILE_IMG_", "[PAGE_IMG_", "http://", "https://")) and "|" not in stripped:
            signature = canon(stripped)
            if signature in seen_long_blocks:
                i += 2
                continue
            seen_long_blocks.add(signature)

        if not table_like and not is_list and not headingish and not stripped.startswith(("#", ">", "[CHART_", "[WEB_IMG_", "[FILE_IMG_", "[PAGE_IMG_", "http://", "https://", "```")) and "|" not in stripped:
            sentence_rows = re.split(r"(?<=[.!?])\s+", stripped)
            kept_sentences = []
            sentence_scope = seen_sentences.setdefault(current_h2, set())
            for sentence in sentence_rows:
                signature = canon(sentence)
                if signature and signature in sentence_scope:
                    continue
                if signature:
                    sentence_scope.add(signature)
                kept_sentences.append(sentence)
            if not kept_sentences:
                i += 2
                continue
            if len(kept_sentences) != len(sentence_rows):
                stripped = " ".join(kept_sentences)
                block = stripped

        kept.append(block)
        if sep:
            kept.append(sep)
        i += 2

    return re.sub(r"\n{3,}", "\n\n", "".join(kept)).strip()


def _remove_empty_markdown_headings(text: str) -> str:
    """Remove headings whose section contains no visible prose or visual block."""
    lines = str(text or "").splitlines()
    heading_rows = []
    for index, line in enumerate(lines):
        match = _HEADING_LINE_RE.match(line.strip())
        if match:
            heading_rows.append((index, len(match.group(1))))
    remove: set[int] = set()
    for heading_index, level in heading_rows:
        end = next((index for index, next_level in heading_rows
                    if index > heading_index and next_level <= level), len(lines))
        body = lines[heading_index + 1:end]
        payload = False
        for line in body:
            candidate = line.strip()
            if not candidate or _HEADING_LINE_RE.match(candidate):
                continue
            if re.fullmatch(r"(?:-{3,}|\*{3,}|_{3,}|[*_`~]+)", candidate):
                continue
            if re.fullmatch(r"(?:\[\[SECTION TO COMPLETE\]\]|\[S\d+(?:\s*[,;]\s*S\d+)*\])", candidate, re.I):
                continue
            payload = True
            break
        if not payload:
            remove.add(heading_index)
    if not remove:
        return str(text or "")
    return re.sub(r"\n{3,}", "\n\n", "\n".join(line for i, line in enumerate(lines) if i not in remove)).strip()


def _remove_dangling_markdown_lead_ins(text: str) -> str:
    """Remove redaction orphans such as a colon lead-in followed by no list."""
    lines = str(text or "").splitlines()
    remove: set[int] = set()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or _HEADING_LINE_RE.match(stripped) or stripped.startswith(("|", ">", "[CHART_", "[WEB_IMG_", "[FILE_IMG_", "[PAGE_IMG_")):
            continue
        plain = re.sub(r"[\s*_`~]+$", "", stripped)
        if not plain.endswith(":"):
            continue
        next_index = next((i for i in range(index + 1, len(lines)) if lines[i].strip()), None)
        if next_index is None:
            remove.add(index)
            continue
        following = lines[next_index].strip()
        if re.match(r"^(?:[-*+]|\d+[.)])\s+", following) or following.startswith(("|", "[CHART_", "[WEB_IMG_", "[FILE_IMG_", "[PAGE_IMG_")):
            continue
        remove.add(index)
    if not remove:
        return str(text or "")
    return re.sub(r"\n{3,}", "\n\n", "\n".join(line for i, line in enumerate(lines) if i not in remove)).strip()


def _reorder_executive_summary_first(text: str) -> str:
    """Move the Executive Summary section ahead of the report's other H2s."""
    lines = str(text or "").splitlines()
    starts = [index for index, line in enumerate(lines) if re.match(r"^\s*##(?!#)\s+", line)]
    if len(starts) < 2:
        return str(text or "")

    def title_at(index: int) -> str:
        return re.sub(r"^\s*##\s+", "", lines[index]).strip().rstrip("# ")

    def semantic_title(value: str) -> str:
        cleaned = re.sub(r"(?i)^\s*(?:section\s+)?\d{1,2}\s*(?:[.)|:\-\u2013\u2014]+\s*|\s+)", "", value)
        return re.sub(r"[*_`]", "", cleaned).strip().casefold()

    summary_index = next((i for i, start in enumerate(starts)
                          if re.match(r"(?i)^executive\s+summary\b", semantic_title(title_at(start)))), None)
    if summary_index is None or summary_index == 0:
        return str(text or "")

    section_blocks = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        section_blocks.append(lines[start:end])
    preamble = lines[:starts[0]]
    section_blocks.insert(0, section_blocks.pop(summary_index))

    number_re = re.compile(r"^(##\s+)(?P<section>Section\s+)?(?P<number>\d{1,2})(?P<separator>\s*(?:[.)|:\-\u2013\u2014]\s*|\s+))(?P<title>.+?)\s*$", re.I)
    numbered = [number_re.match(block[0].strip()) for block in section_blocks]
    if any(numbered):
        next_number = 0
        for block, match in zip(section_blocks, numbered):
            if match is None:
                continue
            next_number += 1
            separator = match.group("separator")
            prefix = "Section " if match.group("section") else ""
            block[0] = f"## {prefix}{next_number:02d}{separator}{match.group('title')}"

    return re.sub(r"\n{3,}", "\n\n", "\n".join(preamble + [line for block in section_blocks for line in block])).strip()


# Sections the client does not want in any report or export. Older reports and
# the provider-outage fallback wrote them into the stored Markdown, so they are
# removed at render time as well as no longer generated.
_EXCLUDED_SECTION_HEADING_RE = re.compile(
    r"(?i)^\s*(?:source[-\s]+derived\s+evidence|source[-\s]+extracted\b.*|evidence\s+gaps\s+and\s+interpretation"
    r"|evidence[-\s]+based\s+research\s+record|research\s+provenance)\s*$"
)
_HEADING_LINE_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")


def strip_excluded_report_sections(value: object) -> str:
    """Drop excluded headed sections (heading plus body up to the next heading
    of the same or a higher level) and fallback-only status lines."""
    lines = str(value or "").split("\n")
    kept: list[str] = []
    skip_level = 0
    for line in lines:
        heading = _HEADING_LINE_RE.match(line.strip())
        if heading:
            level = len(heading.group(1))
            if skip_level and level > skip_level:
                continue
            skip_level = 0
            title = re.sub(r"[*_`]", "", heading.group(2))
            if _EXCLUDED_SECTION_HEADING_RE.match(title):
                skip_level = level
                continue
        elif skip_level:
            continue
        if re.match(r"(?i)^\s*\*\*selected report depth:\*\*", line):
            continue
        kept.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
