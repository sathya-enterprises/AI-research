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
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")


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
                cells = ["—" if _UNVERIFIED_METRIC_PLACEHOLDER_RE.search(cell) else cell.strip() for cell in cells]
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
    return re.sub(r"\n{3,}", "\n\n", value).strip()


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
    normalized = _dedupe_repeated_markdown_blocks(normalized)
    normalized = _remove_unverified_metric_placeholders(normalized)
    # A final guard: no common HTML presentation tags or unverified-metric
    # placeholders should leak into the browser preview or the exported PDF.
    normalized = _normalize_html_fragments(normalized)
    normalized = _remove_unverified_metric_placeholders(normalized)
    return re.sub(r"\n{3,}", "\n\n", normalized).strip()


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
    seen_subheads: dict[str, set[str]] = {}
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

        kept.append(block)
        if sep:
            kept.append(sep)
        i += 2

    return re.sub(r"\n{3,}", "\n\n", "".join(kept)).strip()
