"""Deterministic report-integrity repairs applied before publication.

The module is intentionally conservative: unsupported numeric tables/chart-like
objects are disclosed rather than silently prettified or converted to graphics.
Only content that can be traced to directly fetched publisher rows is retained.
"""
from __future__ import annotations

import math
import re
from typing import Any
from urllib.parse import urlparse

_NOTE_TABLE = (
    "> **Table omitted:** its rows could not be reconciled to directly fetched "
    "publisher data with the required labels, periods and values. No values were inferred."
)
_NOTE_CHART = (
    "> **Chart not included:** the proposed table did not provide a source-verified "
    "series for a graphical chart, so no values were plotted."
)
_NUMERIC = re.compile(r"(?<![A-Za-z])[-+]?(?:\d{1,3}(?:,\d{2,3})+|\d+)(?:\.\d+)?%?")


def _plain(value: object) -> str:
    text = re.sub(r"<[^>]*>", " ", str(value or ""))
    text = re.sub(r"\*\*|__|`|\\", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _number_key(value: str) -> str:
    raw = str(value or "").replace(",", "").replace("%", "").strip()
    try:
        num = float(raw)
        return str(int(num)) if num.is_integer() else f"{num:.8f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return ""


def _numbers(text: str) -> list[str]:
    return [key for m in _NUMERIC.finditer(_plain(text)) if (key := _number_key(m.group(0)))]


def _direct_publisher_texts(sources: list[dict] | None) -> list[str]:
    texts: list[str] = []
    for source in sources or []:
        if not isinstance(source, dict):
            continue
        status = str(source.get("pageFetchStatus") or source.get("page_fetch_status") or "").casefold()
        url = str(source.get("url") or source.get("publisher_page_url") or source.get("publisherPageUrl") or "").strip()
        host = (urlparse(url).hostname or "").casefold() if url else ""
        if status != "ok" or not host or not (host.endswith("rbi.org.in") or host.endswith("icici.bank.in") or host.endswith("sbi.co.in") or host.endswith("hdfcbank.com") or host.endswith("axisbank.com") or host.endswith("bankofbaroda.in") or host.endswith("bankofindia.co.in") or host.endswith("npci.org.in") or host.endswith("sebi.gov.in") or host.endswith("bseindia.com") or host.endswith("nseindia.com")):
            continue
        # pageContent is the extracted publisher page/table representation. fullContent
        # is accepted only with a successful HTTP page fetch and a canonical publisher.
        content = "\n".join(str(source.get(key) or "") for key in (
            "publisherEvidenceExcerpt", "publisher_evidence_excerpt", "pageContent", "page_content", "fullContent", "full_content"
        )).strip()
        if len(content) >= 80:
            texts.append(content)
    return texts


def _valid_table_headers(headers: list[str] | tuple[str, ...] | None) -> bool:
    """Reject placeholder headers that make numeric tables uninterpretable."""
    if not headers:
        return False
    invalid = {"", "-", "–", "—", "−", "n/a", "na", "none", "null", "unknown", "unnamed", "unnamed column", "value"}
    for raw in headers:
        value = re.sub(r"\s+", " ", _plain(raw)).strip().casefold()
        if value in invalid or re.fullmatch(r"(?:unnamed(?: column)?|column ?\d+|field ?\d+)", value):
            return False
    return True


def _row_supported_by_publisher(row: list[str], columns: list[str], publisher_texts: list[str]) -> bool:
    if not row or not publisher_texts:
        return False
    cells = [_plain(c) for c in row]
    label = next((c for c in cells if c and not _numbers(c)), "")
    label_tokens = [t for t in re.findall(r"[a-z]{3,}", label.casefold()) if t not in {
        "bank", "banks", "sector", "total", "group", "groups", "ratio", "trend", "current", "source", "reported"
    }]
    if not label_tokens:
        return False
    expected_numbers = []
    for cell in cells:
        expected_numbers.extend(_numbers(cell))
    if not expected_numbers:
        return False
    # A row is accepted only if one direct publisher row contains the row identity
    # and every numeric value. Finding each number somewhere on the same site is
    # insufficient because it can accidentally combine different metrics/periods.
    for source_text in publisher_texts:
        for source_line in source_text.splitlines():
            line = _plain(source_line)
            low = line.casefold()
            matched_tokens = sum(1 for token in label_tokens if token in low)
            if matched_tokens < min(2, len(label_tokens)):
                continue
            line_numbers = set(_numbers(line))
            if all(number in line_numbers for number in expected_numbers):
                return True
    return False


def _parse_markdown_table(lines: list[str], start: int) -> tuple[int, list[str], list[list[str]]] | None:
    if start + 1 >= len(lines) or "|" not in lines[start]:
        return None
    sep = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")
    if not sep.match(lines[start + 1]):
        return None
    cells = lambda line: [_plain(x) for x in line.strip().strip("|").split("|")]
    headers = cells(lines[start])
    rows: list[list[str]] = []
    pos = start + 2
    while pos < len(lines) and "|" in lines[pos] and lines[pos].strip() and not re.match(r"^\s*#{1,6}\s", lines[pos]):
        if not sep.match(lines[pos]):
            row = cells(lines[pos])
            row.extend([""] * max(0, len(headers) - len(row)))
            rows.append(row[:len(headers)])
        pos += 1
    return (pos, headers, rows) if headers and rows else None


def sanitize_unverified_markdown_tables(report: str, sources: list[dict] | None) -> tuple[str, list[dict]]:
    """Replace numeric Markdown tables whose rows do not match fetched publisher rows.

    Non-numeric / purely explanatory tables are left alone. If any numeric data row
    cannot be matched as one row with its subject and all values, the whole table is
    replaced with an explicit disclosure to avoid publishing a mixed-period mashup.
    """
    lines = str(report or "").splitlines()
    publisher_texts = _direct_publisher_texts(sources)
    output: list[str] = []
    findings: list[dict] = []
    i = 0
    while i < len(lines):
        parsed = _parse_markdown_table(lines, i)
        if not parsed:
            output.append(lines[i]); i += 1; continue
        end, headers, rows = parsed
        data_rows = [r for r in rows if any(_numbers(cell) for cell in r)]
        if not data_rows:
            output.extend(lines[i:end]); i = end; continue
        valid_headers = _valid_table_headers(headers)
        failing = [idx for idx, row in enumerate(data_rows, start=1) if not _row_supported_by_publisher(row, headers, publisher_texts)]
        if failing or not valid_headers:
            findings.append({"header": headers[:8], "unsupportedRows": failing[:12],
                             "invalidHeaders": not valid_headers})
            if output and output[-1].strip():
                output.append("")
            output.append(_NOTE_TABLE)
            output.append("")
        else:
            output.extend(lines[i:end])
        i = end
    result = "\n".join(output)
    result = re.sub(r"\n{3,}", "\n\n", result).strip()
    return result, findings


def _chart_title_caption_regex(title: str) -> re.Pattern[str] | None:
    """Match only a heading that is explicitly a chart/figure caption.

    The previous matcher searched broad metric phrases such as "credit and
    deposit growth" anywhere in a heading. That accidentally matched an
    analytical section called "Credit and Deposit Growth Analysis" and deleted
    the whole section (including newly inserted verified chart placeholders)
    while removing a fake chart-table object. Only explicit caption headings
    are eligible for replacement now.
    """
    clean = re.sub(r"^\s*\d+(?:\.\d+)*[.)]?\s*", "", _plain(title))
    if not clean or clean.casefold() in {"supporting data table", "data table"}:
        return None
    caption = re.match(r"(?i)^(?:chart|figure|plot|graph)\b\s*(?:[:\-–—]\s*)?(?P<description>.+)$", clean)
    if not caption:
        # A plain topic/title is not proof that an entire report section is a
        # chart caption. Its [CHART_n] placeholder will be replaced safely by
        # remove_unverified_table_chart_specs without deleting surrounding text.
        return None
    description = caption.group("description").strip()
    terms = [t for t in re.findall(r"[a-z0-9]{3,}", description.casefold())
             if t not in {"the", "and", "for", "with", "from", "versus", "chart", "figure", "plot", "graph"}]
    if not terms:
        return None
    lookaheads = "".join(r"(?=.*\b" + re.escape(term) + r"\b)" for term in terms[:5])
    return re.compile(
        rf"(?im)^(?P<hash>#{{2,6}})\s+(?:\d+(?:\.\d+)*[.)]?\s*)?(?:chart|figure|plot|graph)\b"
        rf"\s*(?:[:\-–—]\s*)?(?={lookaheads})[^\n]*$\n?"
    )


def _replace_caption_block(
    report: str, title: str, preserve_chart_indices: set[int] | None = None,
) -> tuple[str, bool]:
    """Remove an explicit fake-chart caption without deleting nearby real charts.

    Real chart-gallery placeholders can be inserted in the same analytical
    subsection as a model-authored fake chart/table heading. The old cleanup
    replaced the whole subsection through the next peer heading, which erased
    those verified charts too. Preserve their caption + marker pairs while
    replacing only the unverified caption block.
    """
    pattern = _chart_title_caption_regex(title)
    if not pattern:
        return report, False
    match = pattern.search(report)
    if not match:
        return report, False
    heading_level = len(match.group("hash"))
    tail = report[match.end():]
    next_heading = re.search(rf"(?m)^#{{1,{heading_level}}}\s+[^\n]*$", tail)
    end = match.end() + next_heading.start() if next_heading else len(report)
    block = report[match.end():end]
    keep_indices = {int(x) for x in (preserve_chart_indices or set())}
    lines = block.splitlines()
    preserved_parts: list[str] = []
    seen_indices: set[int] = set()
    for index, line in enumerate(lines):
        marker = re.fullmatch(r"\s*\[CHART_(\d+)\]\s*", line, re.I)
        if not marker:
            continue
        chart_index = int(marker.group(1))
        if chart_index not in keep_indices or chart_index in seen_indices:
            continue
        seen_indices.add(chart_index)
        # Keep a figure caption immediately above the marker and an optional
        # family heading immediately above it. Never carry over stale numeric
        # descriptions from the rejected model-authored chart table.
        keep_lines: list[str] = []
        prior = index - 1
        while prior >= 0 and not lines[prior].strip():
            prior -= 1
        if prior >= 0 and re.match(r"^\s*\*\*Figure\s+\d+\s*[—–:-]", lines[prior], re.I):
            keep_lines.append(lines[prior].strip())
            prior -= 1
            while prior >= 0 and not lines[prior].strip():
                prior -= 1
            if prior >= 0 and re.match(r"^\s*#{2,5}\s+", lines[prior]):
                keep_lines.insert(0, lines[prior].strip())
        keep_lines.append(f"[CHART_{chart_index}]")
        preserved_parts.append("\n\n".join(keep_lines))
    if preserved_parts:
        disclosure = (
            "> **Proposed chart table omitted:** its figures did not validate as one consistent, source-backed series. "
            "The verified visualizations below use separate publisher-page observations."
        )
    else:
        disclosure = _NOTE_CHART
    replacement = disclosure
    if preserved_parts:
        replacement += "\n\n" + "\n\n".join(preserved_parts)
    updated = report[:match.start()].rstrip() + "\n\n" + replacement + "\n\n" + report[end:].lstrip("\n")
    return re.sub(r"\n{3,}", "\n\n", updated), True


def _remove_empty_headings(report: str) -> str:
    """Remove headings with no content in their complete section subtree.

    A parent section is not empty merely because its first line is a nested
    heading. The former implementation looked only up to the next heading of
    any level; this incorrectly deleted useful parent headings (and could hide
    inline chart placeholders) whenever a section started with ``###``.
    """
    text = re.sub(r"\n{3,}", "\n\n", str(report or "")).strip()
    while text:
        lines = text.splitlines()
        headings: list[tuple[int, int]] = []
        for i, line in enumerate(lines):
            match = re.match(r"^\s*(#{1,6})\s+.+$", line)
            if match:
                headings.append((i, len(match.group(1))))
        if not headings:
            return text
        remove_indices: set[int] = set()
        for pos, (start, level) in enumerate(headings):
            end = next((next_start for next_start, next_level in headings[pos + 1:]
                        if next_level <= level), len(lines))
            subtree_body = []
            for line in lines[start + 1:end]:
                stripped = line.strip()
                if not stripped or re.match(r"^#{1,6}\s+", stripped):
                    continue
                # Inline chart markers are real content: removing their parent
                # heading can orphan a chart at precisely the place it belongs.
                if re.fullmatch(r"\[CHART_\d+\]", stripped, re.I):
                    subtree_body.append(stripped)
                    continue
                subtree_body.append(stripped)
            if not subtree_body:
                remove_indices.add(start)
        if not remove_indices:
            return text
        updated = "\n".join(line for i, line in enumerate(lines) if i not in remove_indices)
        updated = re.sub(r"\n{3,}", "\n\n", updated).strip()
        if updated == text:
            return text
        text = updated
    return text


def remove_unverified_table_chart_specs(report: str, charts: list | None, sources: list[dict] | None) -> tuple[str, list[dict], dict]:
    """Keep only row-verified table objects; remove chart-labelled tables as fakes.

    A table is not a graphical chart. Tables may remain in the chart payload only
    when each numeric row matches one fetched publisher row exactly enough to show
    it was not assembled from different metrics or periods.
    """
    originals = list(charts or [])
    publisher_texts = _direct_publisher_texts(sources)
    kept: list[dict] = []
    mask: list[bool] = []
    dropped: list[dict] = []
    matched_captions: set[int] = set()
    report_text = str(report or "")
    # Real charts are preserved even when an unsupported table-based chart
    # caption in the same section is removed.
    preserve_chart_indices = {
        idx for idx, item in enumerate(originals, start=1)
        if isinstance(item, dict)
        and str(item.get("type") or item.get("chartType") or item.get("chart_type") or "").strip().casefold().replace("-", "_").replace(" ", "_") != "table"
    }
    for idx, raw in enumerate(originals, start=1):
        if not isinstance(raw, dict):
            mask.append(False); dropped.append({"index": idx, "title": f"Chart {idx}", "reason": "invalid spec"}); continue
        ctype = str(raw.get("type") or raw.get("chartType") or raw.get("chart_type") or "").strip().casefold().replace("-", "_").replace(" ", "_")
        if ctype != "table":
            mask.append(True); kept.append(raw); continue
        title = str(raw.get("title") or raw.get("name") or f"Table {idx}").strip()
        chart_imitator = bool(re.search(r"(?i)\b(chart|plot|figure|graph)\b", title))
        rows = raw.get("rows") if isinstance(raw.get("rows"), list) else []
        columns = raw.get("columns") if isinstance(raw.get("columns"), list) else []
        row_valid = bool(_valid_table_headers(columns) and len(rows) >= 2 and publisher_texts and all(
            _row_supported_by_publisher((
                [str(row.get(str(col), row.get(col, "")) or "") for col in columns] if isinstance(row, dict)
                else [str(x or "") for x in row] if isinstance(row, (list, tuple)) else [str(row)]
            ), [str(c) for c in columns], publisher_texts) for row in rows
        ))
        if row_valid and not chart_imitator:
            mask.append(True); kept.append(dict(raw)); continue
        updated, caption_handled = _replace_caption_block(report_text, title, preserve_chart_indices)
        if caption_handled:
            report_text = updated
            matched_captions.add(idx)
        dropped.append({"index": idx, "title": title[:160], "reason": "chart-like table" if chart_imitator else "unverified table rows"})
        mask.append(False)
    new_index: dict[int, int | None] = {}
    count = 0
    for original_index, keep in enumerate(mask, start=1):
        if keep:
            count += 1; new_index[original_index] = count
        else:
            new_index[original_index] = None
    def placeholder(m: re.Match[str]) -> str:
        old = int(m.group(1))
        new = new_index.get(old)
        if new is not None:
            return f"[CHART_{new}]"
        if old in matched_captions:
            return ""
        return _NOTE_CHART if old in {d["index"] for d in dropped} else ""
    report_text = re.sub(r"\[CHART_(\d+)\]", placeholder, report_text, flags=re.I)
    report_text = re.sub(r"(?im)^\s*\*\*Charts and data\.\*\*.*?(?=^#{1,6}\s|\Z)", "", report_text, flags=re.S)
    report_text = _remove_empty_headings(report_text)
    report_text = re.sub(r"\n{3,}", "\n\n", report_text).strip()
    return report_text, kept, {"removedTables": len(dropped), "removed": dropped, "remainingCharts": len(kept)}


def _parse_row_ending_totals(source: dict, row_label: str) -> list[float] | None:
    status = str(source.get("pageFetchStatus") or source.get("page_fetch_status") or "").casefold()
    url = str(source.get("url") or source.get("publisher_page_url") or "")
    if status != "ok" or "rbi.org.in" not in (urlparse(url).hostname or "").casefold():
        return None
    text = "\n".join(str(source.get(k) or "") for k in ("pageContent", "page_content", "fullContent", "full_content"))
    for line in text.splitlines():
        if not re.search(rf"(?i)^\s*{re.escape(row_label)}\s*\|", line.strip()):
            continue
        values = []
        for cell in line.split("|")[1:]:
            key = _number_key(re.sub(r"[%₹$]", "", cell.strip()))
            if key:
                try: values.append(float(key))
                except ValueError: pass
        if len(values) >= 2:
            return values[-2:]
    return None


def repair_banking_growth_and_ratio_labels(report: str, sources: list[dict] | None, manifest: list[dict] | None = None) -> tuple[str, dict]:
    """Fix known ratio-direction inconsistencies only when RBI FY totals verify them."""
    totals = None
    source_url = ""
    for source in sources or []:
        if not isinstance(source, dict):
            continue
        deposits = _parse_row_ending_totals(source, "3. Deposits")
        loans = _parse_row_ending_totals(source, "4. Loans and advances")
        if deposits and loans:
            totals = (deposits, loans)
            source_url = str(source.get("url") or source.get("publisher_page_url") or "")
            break
    if not totals:
        return str(report or ""), {"changed": False, "reason": "verified RBI aggregate rows unavailable"}
    deps, loans = totals
    dep_growth = (deps[1] / deps[0] - 1.0) * 100.0 if deps[0] else None
    loan_growth = (loans[1] / loans[0] - 1.0) * 100.0 if loans[0] else None
    dep_to_loan = deps[1] / loans[1] if loans[1] else None
    loan_to_dep = loans[1] / deps[1] if deps[1] else None
    if not all(x is not None and math.isfinite(x) for x in (dep_growth, loan_growth, dep_to_loan, loan_to_dep)):
        return str(report or ""), {"changed": False, "reason": "invalid RBI ratios"}
    sid = next((str(s.get("id") or "").upper() for s in (manifest or []) if isinstance(s, dict) and str(s.get("url") or s.get("publisher_page_url") or "") == source_url and re.fullmatch(r"S\d+", str(s.get("id") or ""), re.I)), "")
    cite = f" [{sid}]" if sid else ""
    text = str(report or "")
    original = text
    # Correct the executive claim only if it exactly asserts deposits outpaced loans.
    if loan_growth > dep_growth:
        text = re.sub(
            r"(?im)^\s*[-*]\s*.*Deposit growth outpaced loan growth.*$",
            f"- **Credit grew faster than deposits in FY 2023–24**: RBI-reported year-end totals imply approximately {loan_growth:.1f}% growth in loans and advances versus {dep_growth:.1f}% growth in deposits, increasing the loan-to-deposit ratio from the perspective of funding demand{cite}.",
            text,
        )
    # Do not let a deposit/loan value be mislabeled as loan/deposit. The ratio
    # may occur in a bullet, a normal paragraph, or a KPI line; the old bullet-
    # only 1.27 pattern missed unbulleted narrative such as "Loan-to-Deposit
    # Ratio: 1.27" and allowed contradictory interpretations into the PDF.
    ratio_line = re.compile(
        r"(?im)^(?P<prefix>\s*(?:[-*]\s*)?)(?:\*\*)?Loan[\s\u2010-\u2015‑‑-]*to[\s\u2010-\u2015‑‑-]*Deposit Ratio(?:\*\*)?\s*:\s*(?P<value>[-+]?\d[\d,]*(?:\.\d+)?)(?P<suffix>[^\n]*)$",
    )

    def _repair_inverse_ratio(match: re.Match) -> str:
        try:
            shown = float(match.group("value").replace(",", ""))
        except (TypeError, ValueError):
            return match.group(0)
        tolerance = max(0.025, abs(loan_to_dep) * 0.025)
        # Leave a correctly labeled, source-consistent loan/deposit value alone.
        # Otherwise replace the whole claim with the two ratios computed from
        # the same publisher totals. This also catches a wrong value (not only
        # the common 1.27 inverse-direction mistake) instead of preserving it.
        if abs(shown - loan_to_dep) <= tolerance:
            return match.group(0)
        prefix = match.group("prefix")
        return (f"{prefix}**Deposit-to-loan ratio**: {dep_to_loan:.2f}; the inverse "
                f"loan-to-deposit ratio was {loan_to_dep:.2f} at end-March 2024, "
                f"computed from RBI-reported deposits and loans and advances{cite}.")

    text = ratio_line.sub(_repair_inverse_ratio, text)
    text = re.sub(
        r"(?im)^\s*\*?Implication\*?\s*[–—-]\s*The DLR’s slight contraction in 2024 signals a tighter funding environment but still leaves banks comfortably funded, reducing liquidity risk\.\s*$",
        f"A single year-end ratio does not establish a funding trend on its own; compare like-for-like RBI totals across periods before describing contraction or expansion{cite}.",
        text,
    )
    # Remove unsupported prior-period comparator tied to the same ratio.
    text = re.sub(r"\s*\(up from\s+1\.20\s+in\s+FY\s*2022[‑–-]23\)", "", text, flags=re.I)
    return text, {"changed": text != original, "depositGrowthPct": round(dep_growth, 2), "loanGrowthPct": round(loan_growth, 2),
                  "depositToLoan": round(dep_to_loan, 4), "loanToDeposit": round(loan_to_dep, 4), "sourceId": sid}
