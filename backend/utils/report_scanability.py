"""Evidence-preserving scanability helpers for analytical research reports.

These helpers support the report writer with measurable diagnostics and a
conservative reformatting pass. They do not create facts or invent chart data.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

_HEADING_RE = re.compile(r"(?m)^(#{2,4})[ \t]+(.+?)\s*$")
_CITATION_RE = re.compile(r"\[S\d+\]", re.I)
_PLACEHOLDER_RE = re.compile(r"\[(?:CHART|WEB_IMG|FILE_IMG|PAGE_IMG)_\d+\]", re.I)
_URL_RE = re.compile(r"https?://[^\s<>\])}]+", re.I)
_NUMBER_RE = re.compile(r"(?<![A-Za-z])\d[\d,]*(?:\.\d+)?%?(?![A-Za-z])")


def strip_generation_markers(report_text: str) -> str:
    """Remove leaked continuation-control markers before layout and publication."""
    out: list[str] = []
    for line in str(report_text or "").replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        if not stripped:
            # Keep paragraph boundaries so downstream scanability and report
            # section parsers can distinguish separate prose blocks. Collapsing
            # blank lines here made several long paragraphs look like one block.
            out.append("")
            continue
        if re.match(r"^\[\[SECTION\]\]", stripped, re.I):
            # This is a continuation-control wrapper, not reader-facing content.
            continue
        if re.match(r"^\[\[(?:END SECTION|SECTION TO EXPAND|END_SCAN_SECTION)\]\]", stripped, re.I):
            continue
        # Structured layout tokens are placement hints, never reader-facing content.
        # Remove both standalone and inline forms: provider outputs occasionally
        # append the marker to a sentence or citation instead of giving it a line.
        line = re.sub(r"\s*\[\s*RISK_MATRIX\s*\]\s*", " ", line, flags=re.I).rstrip()
        # A parser should consume these wrappers. If any leak into report text,
        # retain the named heading but never the control-token syntax.
        if re.match(r"^\[\[SECTION:\s*.*?\]\]\s*$", stripped, re.I):
            continue
        out.append(line)
    text = "\n".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + ("\n" if text.strip() else "")


def _normal_numbers(text: str) -> set[str]:
    return {m.group(0).replace(",", "").rstrip("%") for m in _NUMBER_RE.finditer(text or "")}


def _blocks(text: str) -> list[dict[str, Any]]:
    """Return heading-owned Markdown blocks without altering source text."""
    text = str(text or "")
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        return []
    result = []
    for i, match in enumerate(matches):
        body_start = match.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[body_start:body_end]
        result.append({
            "start": body_start,
            "end": body_end,
            "heading": match.group(2).strip(),
            "level": len(match.group(1)),
            "body": body,
            "index": i,
        })
    return result


def _body_metrics(body: str) -> dict[str, int | float]:
    chunks = [x.strip() for x in re.split(r"\n\s*\n", body or "") if x.strip()]
    long_para = 0
    max_run = 0
    run = 0
    for chunk in chunks:
        if chunk.startswith("#") or chunk.startswith(">") or "|" in chunk or re.match(r"(?:[-*+]\s|\d+[.)]\s)", chunk):
            run = 0
            continue
        if _PLACEHOLDER_RE.search(chunk) or re.match(r"^(?:Source|Sources|Data Sources):", chunk, re.I):
            run = 0
            continue
        # Count only prose-sized paragraph blocks, not short labels or KPI lines.
        if len(chunk) >= 280:
            long_para += 1
            run += 1
            max_run = max(max_run, run)
        else:
            run = 0
    bullets = len(re.findall(r"(?m)^\s*[-*+]\s+", body or ""))
    numbered = len(re.findall(r"(?m)^\s*\d+[.)]\s+", body or ""))
    table_rows = len(re.findall(r"(?m)^\s*\|.*\|\s*$", body or ""))
    blockquotes = len(re.findall(r"(?m)^\s*>\s*", body or ""))
    visuals = len(_PLACEHOLDER_RE.findall(body or ""))
    return {
        "long_paragraphs": long_para,
        "max_consecutive_long_paragraphs": max_run,
        "bullets": bullets,
        "numbered_items": numbered,
        "table_rows": table_rows,
        "blockquote_lines": blockquotes,
        "visual_placeholders": visuals,
        "structures": int(bullets > 0) + int(numbered > 0) + int(table_rows >= 2) + int(blockquotes > 0) + int(visuals > 0),
    }


def audit_scanability(report_text: str) -> dict[str, Any]:
    """Provide deterministic, tier-independent scanability statistics."""
    blocks = _blocks(report_text)
    details = []
    for block in blocks:
        m = _body_metrics(block["body"])
        details.append({"heading": block["heading"], "level": block["level"], **m,
                        "body_chars": len(block["body"])})
    return {
        "heading_block_count": len(details),
        "long_paragraph_count": sum(int(x["long_paragraphs"]) for x in details),
        "structured_block_count": sum(int(x["structures"]) for x in details),
        "marker_leak_count": len(re.findall(r"(?m)^\s*\[\[(?:SECTION|END SECTION|SECTION TO EXPAND)", report_text or "", re.I)),
        "paragraph_heavy_sections": [x for x in details if x["body_chars"] >= 900 and x["long_paragraphs"] >= 3 and x["structures"] < 2],
        "sections": details,
    }


def select_scanability_targets(report_text: str, tier: str, max_targets: int | None = None) -> list[dict[str, Any]]:
    """Select prose-heavy heading blocks that can benefit from a faithful reformat."""
    normalized_tier = str(tier or "detailed").lower()
    limit = max_targets if max_targets is not None else (4 if normalized_tier in {"in_depth", "deep", "deep_research"} else 3)
    blocks = _blocks(report_text)
    candidates = []
    for block in blocks:
        heading = block["heading"]
        body = block["body"]
        heading_key = heading.casefold()
        if any(x in heading_key for x in ("data source", "reference", "appendix", "methodology", "executive summary", "contents")):
            continue
        if len(body) < 900 or len(body) > 10500:
            continue
        metrics = _body_metrics(body)
        if metrics["long_paragraphs"] < 3:
            continue
        # Already well-structured blocks are not rewritten merely for variety.
        score = (int(metrics["long_paragraphs"]) * 4
                 + int(metrics["max_consecutive_long_paragraphs"]) * 3
                 - int(metrics["structures"]) * 4
                 - int(metrics["bullets"]) // 3)
        if score < 12:
            continue
        candidates.append({**block, "metrics": metrics, "score": score})
    # Pick highest-defect sections while retaining report order for predictable processing.
    selected = sorted(candidates, key=lambda x: (-x["score"], x["index"]))[:max(0, int(limit))]
    return sorted(selected, key=lambda x: x["start"])


def validate_reformatted_block(original: str, rewritten: str) -> tuple[bool, str]:
    """Reject rewrites that lose references, numbers, URLs, or fail to improve scanability."""
    old = str(original or "").strip()
    new = str(rewritten or "").strip()
    if len(new) < max(300, int(len(old) * 0.55)):
        return False, "too_much_content_removed"
    if len(new) > int(len(old) * 1.35) + 600:
        return False, "unbounded_expansion"
    old_cites = {x.upper() for x in _CITATION_RE.findall(old)}
    new_cites = {x.upper() for x in _CITATION_RE.findall(new)}
    if not old_cites.issubset(new_cites):
        return False, "citation_loss"
    old_urls = {x.rstrip(".,;:") for x in _URL_RE.findall(old)}
    new_urls = {x.rstrip(".,;:") for x in _URL_RE.findall(new)}
    if not old_urls.issubset(new_urls):
        return False, "url_loss"
    old_placeholders = Counter(_PLACEHOLDER_RE.findall(old))
    new_placeholders = Counter(_PLACEHOLDER_RE.findall(new))
    if any(new_placeholders[k] < count for k, count in old_placeholders.items()):
        return False, "visual_placeholder_loss"
    old_nums = _normal_numbers(old)
    new_nums = _normal_numbers(new)
    if not old_nums.issubset(new_nums):
        return False, "numeric_anchor_loss"
    # A formatting pass must not introduce new numeric claims.
    if not new_nums.issubset(old_nums):
        return False, "new_numeric_anchor"
    before = _body_metrics(old)
    after = _body_metrics(new)
    structured_gain = int(after["structures"]) > int(before["structures"])
    paragraph_gain = int(after["long_paragraphs"]) <= max(1, int(before["long_paragraphs"]) - 2)
    if not structured_gain and not paragraph_gain:
        return False, "no_scanability_gain"
    if int(after["max_consecutive_long_paragraphs"]) > 2 and not structured_gain:
        return False, "still_paragraph_heavy"
    return True, "accepted"


def replace_block_bodies(report_text: str, replacements: list[tuple[int, int, str]]) -> str:
    """Apply body replacements from back to front so earlier offsets remain stable."""
    result = str(report_text or "")
    for start, end, body in sorted(replacements, key=lambda x: x[0], reverse=True):
        result = result[:start] + "\n" + body.strip() + "\n\n" + result[end:]
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip() + ("\n" if result.strip() else "")
