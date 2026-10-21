"""Secondary report-quality review helpers.

The report writer remains responsible for research content. This module is a
second-pass editorial gate: it reviews the generated artifact using the
renderer's page diagnostics plus the report structure, looking for duplication,
empty/orphan pages, weak visual balance, and repeated sections. It never invents
facts; any repair is routed back through the section Action validator.
"""
from __future__ import annotations

import re
from collections import Counter

from utils.chart_evidence_audit import audit_chart_evidence_consistency


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().casefold()


_DANGLING_NUMERIC_SUFFIXES = (
    # Common LLM truncations seen in reports: "—translating to Rs." and
    # "(projected credit expansion of Rs.". These patterns remove only an
    # incomplete trailing clause; they never supply a missing number.
    re.compile(r"\s*[—–-]\s*translating\s+to\s+(?:Rs\.?|₹|INR|USD|US\$|\$)?\s*$", re.I),
    re.compile(r"\s*\(?\s*(?:projected\s+)?(?:absolute\s+)?credit\s+expansion\s+of\s+(?:Rs\.?|₹|INR|USD|US\$|\$)?\s*$", re.I),
    re.compile(r"\s*(?:translating|amounting|equivalent)\s+to\s+(?:Rs\.?|₹|INR|USD|US\$|\$)?\s*$", re.I),
    re.compile(r"\s*(?:is|was|were|reached|totalled|totaled|of|at|to)\s+(?:Rs\.?|₹|INR|USD|US\$|\$)\s*$", re.I),
)


def find_incomplete_numeric_fragments(report: str) -> list[dict]:
    """Find high-confidence dangling currency/quantitative clauses without guessing values."""
    findings: list[dict] = []
    in_fence = False
    for number, line in enumerate(str(report or "").replace("\r\n", "\n").split("\n"), start=1):
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or not line.strip() or "http://" in line or "https://" in line:
            continue
        if line.lstrip().startswith("|") or re.match(r"^\s*[-*+]\s*$", line):
            continue
        for pattern in _DANGLING_NUMERIC_SUFFIXES:
            match = pattern.search(line.rstrip())
            if match:
                prefix = line[:match.start()].rstrip()
                if len(re.sub(r"[^A-Za-z0-9]", "", prefix)) >= 12:
                    findings.append({"line": number, "text": line.strip()[:260], "fragment": match.group(0).strip()})
                break
    return findings


def repair_incomplete_numeric_fragments(report: str) -> tuple[str, int]:
    """Remove incomplete trailing numeric clauses; never invent or infer the missing value."""
    lines = str(report or "").replace("\r\n", "\n").split("\n")
    repaired = 0
    in_fence = False
    for index, line in enumerate(lines):
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or not line.strip() or "http://" in line or "https://" in line or line.lstrip().startswith("|"):
            continue
        updated = line.rstrip()
        for pattern in _DANGLING_NUMERIC_SUFFIXES:
            match = pattern.search(updated)
            if not match:
                continue
            prefix = updated[:match.start()].rstrip(" \t,;:—–-")
            # Avoid destructive edits to very short fragments/headings.
            if len(re.sub(r"[^A-Za-z0-9]", "", prefix)) < 12:
                break
            if prefix and prefix[-1] not in ".!?":
                prefix += "."
            updated = prefix
            repaired += 1
            break
        lines[index] = updated
    result = "\n".join(lines)
    # Normalize only whitespace that the removed suffix could have left behind.
    result = re.sub(r"[ \t]+([.!?])", r"\1", result)
    return result, repaired


def _chart_has_nonempty_payload(chart: object) -> bool:
    """Cheap renderer-independent guard against empty chart/table placeholders."""
    if not isinstance(chart, dict):
        return False
    ctype = str(chart.get("type") or chart.get("chartType") or chart.get("chart_type") or "").casefold()
    if ctype == "table":
        columns = chart.get("columns") if isinstance(chart.get("columns"), list) else []
        rows = chart.get("rows") if isinstance(chart.get("rows"), list) else []
        return len(columns) >= 2 and len(rows) >= 2
    if ctype == "heatmap":
        rows, columns, values = chart.get("rows"), chart.get("columns"), chart.get("values")
        return (isinstance(rows, list) and len(rows) >= 2 and isinstance(columns, list) and len(columns) >= 2
                and isinstance(values, list) and len(values) == len(rows)
                and all(isinstance(row, list) and len(row) == len(columns) and any(v is not None for v in row) for row in values))
    if ctype in {"sankey", "chord", "network", "tree", "flowchart"}:
        return isinstance(chart.get("nodes"), list) and bool(chart.get("nodes")) and isinstance(chart.get("links"), list) and bool(chart.get("links"))
    if ctype in {"gantt", "timeline"}:
        return bool(chart.get("events")) or bool((chart.get("series") or [{}])[0].get("data") if isinstance(chart.get("series"), list) else [])
    if ctype == "venn":
        return isinstance(chart.get("sets"), list) and len(chart.get("sets") or []) >= 2
    series = chart.get("series") if isinstance(chart.get("series"), list) else []
    if not series:
        return False
    any_points = False
    for item in series:
        if not isinstance(item, dict):
            continue
        data = item.get("data")
        if isinstance(data, list) and data:
            any_points = True
            break
    return any_points


def audit_report_structure(report: str) -> dict:
    text = str(report or "").replace("\r\n", "\n")
    headings = re.findall(r"(?m)^##\s+(.+?)\s*$", text)
    bodies = re.split(r"(?m)^##\s+.+?\s*$", text)[1:]
    norm_heads = [_norm(h) for h in headings]
    duplicate_headings = [h for h, n in Counter(norm_heads).items() if n > 1]

    paragraphs = [
        _norm(p) for p in re.split(r"\n\s*\n", text)
        if len(_norm(p)) >= 180 and not _norm(p).startswith("[")
    ]
    counts = Counter(paragraphs)
    duplicate_paragraphs = [p for p, n in counts.items() if n > 1]

    short_sections = []
    section_overlaps = []
    section_tokens = []
    for h, b in zip(headings, bodies):
        chars = len(_norm(b))
        if 0 < chars < 260:
            short_sections.append({"heading": h.strip(), "chars": chars})
        tokens = set(re.findall(r"[a-z0-9]{4,}", _norm(b)))
        section_tokens.append(tokens)
    # Conservative conceptual-overlap signal for the secondary reviewer. It is
    # intentionally diagnostic rather than an automatic deletion rule.
    for i in range(len(headings)):
        if len(section_tokens[i]) < 18:
            continue
        for j in range(i + 1, len(headings)):
            if len(section_tokens[j]) < 18:
                continue
            overlap = len(section_tokens[i] & section_tokens[j]) / max(1, len(section_tokens[i] | section_tokens[j]))
            if overlap >= 0.32:
                section_overlaps.append({"earlier": headings[i].strip(), "later": headings[j].strip(), "token_overlap": round(overlap, 2)})
                if len(section_overlaps) >= 12:
                    break
        if len(section_overlaps) >= 12:
            break

    # Structured-content diagnostics are included in the reviewer context so
    # duplicate tables and suspicious repeated peer values are not invisible to
    # a prose-only repetition check.
    try:
        _structured_audit = audit_report_publication(text)
        duplicate_table_count = _structured_audit.get("duplicateTableCount", 0)
        suspicious_entity_values = _structured_audit.get("suspiciousEntityTableValues", [])
    except Exception:
        duplicate_table_count = 0
        suspicious_entity_values = []

    return {
        "h2_count": len(headings),
        "headings": headings,
        "duplicate_headings": duplicate_headings,
        "duplicate_paragraph_count": len(duplicate_paragraphs),
        "short_sections": short_sections,
        "conceptual_overlaps": section_overlaps,
        "duplicate_table_count": duplicate_table_count,
        "suspicious_entity_table_values": suspicious_entity_values,
        "control_marker_count": len(re.findall(r"(?m)^\s*\[\[(?:SECTION:|END SECTION|SECTION TO EXPAND|END_SCAN_SECTION)", text, re.I)),
    }


def build_secondary_review_prompt(report: str, tier: str, question: str, layout: dict) -> str:
    structure = audit_report_structure(report)
    section_lines = "\n".join(
        f"- {x['heading']} ({x['chars']} chars)" for x in structure["short_sections"]
    ) or "- none"
    return f"""You are the SECONDARY EDITORIAL QA MODEL for Growth Gradual Research Intelligence.
You are reviewing a report after a primary research model generated it. You are NOT the research writer.
Do not invent facts and do not suggest new facts.

Your job is to detect high-confidence publication defects in four dimensions:
1) STRUCTURE: duplicate top-level sections, an executive summary duplicated by another executive-summary block,
   orphan/near-empty sections, inconsistent hierarchy.
2) REPETITION: repeated substantive paragraphs/claims or the same metric being repeated without a new analytical lens.
3) VISUAL/PAGINATION: blank or near-empty pages, very short pages, text-heavy pages without a visual focal point,
   a chart with too many labels for the available layout, or an isolated section heading likely to create a bad page break.
4) EVIDENCE/PRESENTATION: unsupported placeholder entities, generic source labels, duplicate tables/charts, dangling
   [CHART_n] or image markers, images whose caption/publisher is unrelated to the report subject, or a requested
   comparison that is represented twice.
5) NUMERIC CONSISTENCY: detect incompatible values for the same metric/time period/market definition; changes in
   units or definitions that are not explained; figures that disagree between summary, narrative, tables and charts;
   and suspiciously identical numeric values repeated across different companies/banks/regions. Do not invent a
   corrected figure. Where available source evidence cannot verify a suspicious value, recommend marking it unavailable
   or explicitly qualified rather than guessing.
6) CONCEPTUAL DEDUPLICATION: detect sections that repeat the same analytical theme, mechanism, metric, or
   conclusion under different wording. Repetition is a defect when the later treatment adds no materially new
   evidence, comparison, implication, or decision-useful lens. Do not flag a legitimate cross-reference when it
   introduces a genuinely different lens.

Selected tier: {tier}
User request: {question}

STRUCTURE SNAPSHOT:
{structure}

RENDERED PAGE SNAPSHOT:
{layout}

REPORT TEXT:
{report[:65000]}

Return ONLY valid JSON with this shape:
{{
  "pass": true|false,
  "severity": "none"|"low"|"medium"|"high",
  "issues": ["short concrete issue", ...],
  "repairs": [
    {{"section": "exact H2 heading", "instruction": "a narrowly scoped repair instruction that changes only this section"}}
  ]
}}

Rules for repairs:
- Only propose a repair when the defect is high-confidence.
- Repairs must be section-local and preserve verified entities, numbers, citations, URLs, charts and source evidence.
- Never propose a new number or unsupported company. If a repeated or conflicting numeric claim cannot be verified
  from the available evidence, the safe repair is to qualify it or mark it unavailable, not to invent a replacement.
- Treat structured-content diagnostics (duplicate tables, repeated peer values, dangling visual markers and asset
  attribution) as publication defects, not as stylistic preferences.
- If the issue is only cosmetic and the renderer can handle it, do not propose a content rewrite.
- Maximum 2 repairs.
"""


def _table_rows(report: str) -> list[tuple[list[str], list[list[str]]]]:
    """Extract pipe-delimited Markdown tables for deterministic QA only."""
    lines = str(report or "").splitlines()
    found: list[tuple[list[str], list[list[str]]]] = []
    i = 0
    separator = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")

    def cells(line: str) -> list[str]:
        return [x.strip() for x in line.strip().strip("|").split("|")]

    while i + 1 < len(lines):
        if "|" not in lines[i] or not separator.match(lines[i + 1]):
            i += 1
            continue
        headers = cells(lines[i])
        i += 2
        rows: list[list[str]] = []
        while i < len(lines) and "|" in lines[i] and lines[i].strip():
            row = cells(lines[i])
            if not separator.match(lines[i]):
                row += [""] * max(0, len(headers) - len(row))
                rows.append(row[:len(headers)])
            i += 1
        if headers and rows:
            found.append((headers, rows))
    return found



def _table_period_mismatches(table_defs: list[tuple[list[str], list[list[str]], str]]) -> list[dict]:
    """Find forecast table cells whose horizon conflicts with their column heading.

    This intentionally checks only explicit fiscal/year projection headers and
    explicit terminal/forecast years in a cell. It does not infer missing values
    or treat a general baseline note as a conflicting forecast.
    """
    issues: list[dict] = []
    fy_re = re.compile(r"\bFY\s*(20\d{2}|\d{2})\b", re.I)
    year_re = re.compile(r"\b(20\d{2})\b")
    forecast_re = re.compile(r"\b(?:projection|projected|forecast|target|terminal)\b", re.I)
    for headers, rows, origin in table_defs:
        if not headers or not rows:
            continue
        for col_index, header in enumerate(headers):
            header_text = str(header or "")
            if not forecast_re.search(header_text):
                continue
            header_years = [int(m.group(1)) for m in year_re.finditer(header_text)]
            for m in fy_re.finditer(header_text):
                raw = m.group(1)
                value = int(raw)
                header_years.append(value if value >= 2000 else 2000 + value)
            if not header_years:
                continue
            header_year = max(header_years)
            for row_index, row in enumerate(rows, start=1):
                if col_index >= len(row):
                    continue
                cell = str(row[col_index] or "")
                if not cell.strip():
                    continue
                cell_years = [int(m.group(1)) for m in year_re.finditer(cell)]
                # A terminal value years beyond a fiscal-year projection header
                # is almost always a column-label mismatch, not an FY27 estimate.
                conflicting = [y for y in cell_years if y > header_year + 1]
                if conflicting and (re.search(r"\bterminal\b", cell, re.I) or forecast_re.search(cell)):
                    issues.append({
                        "origin": origin, "column": header_text[:140], "rowIndex": row_index,
                        "cell": cell[:220], "headerYear": header_year,
                        "cellYears": conflicting,
                        "reason": "Forecast/terminal year in a table cell conflicts with the forecast horizon named by its column header.",
                    })
    return issues

def _potential_market_forecast_conflicts(report: str) -> list[dict]:
    """Flag obvious cross-year scale reversals in broad market-size claims.

    This is intentionally a warning, not a correction: different definitions
    (e.g. IT services vs. digital transformation) can produce different values.
    Only claims explicitly framed as overall/total/industry-wide market values,
    expressed in USD billion/million/trillion, and sharing a domain token are
    compared. A source-backed report should reconcile the scopes before publish.
    """
    text = str(report or "")
    segments = re.split(r"(?<=[.!?])\s+(?=[A-Z₹$0-9(])|\n+", text)
    money_re = re.compile(
        r"(?:\bUSD\s*|\bUS\s*\$\s*|\$\s*)([\d,]+(?:\.\d+)?)\s*"
        r"(trillion|tn|billion|bn|million|mn)\b", re.I
    )
    year_re = re.compile(r"\b(?:FY\s*)?(20\d{2})\b", re.I)
    broad_scope_re = re.compile(r"\b(?:overall|total|aggregate|entire|industry[- ]wide|sector[- ]wide)\b", re.I)
    market_re = re.compile(r"\b(?:market|revenue|market size|market value)\b", re.I)
    domain_aliases = {
        "banking": "bank", "banks": "bank", "services": "service",
        "industries": "industry", "insurers": "insurance", "pharmaceuticals": "pharma",
    }
    domain_terms = {"it", "bank", "service", "insurance", "pharma", "energy", "telecom", "automotive", "semiconductor", "solar", "wind", "healthcare", "hospital", "oil", "gas"}
    claims: list[dict] = []
    for sentence in segments:
        sentence = re.sub(r"\s+", " ", sentence).strip()
        if len(sentence) < 30 or not broad_scope_re.search(sentence) or not market_re.search(sentence):
            continue
        lowered_terms = set(re.findall(r"[a-z]+", sentence.casefold()))
        domains = {domain_aliases.get(term, term) for term in lowered_terms} & domain_terms
        if not domains:
            continue
        amounts = list(money_re.finditer(sentence))
        years = list(year_re.finditer(sentence))
        if not amounts or not years:
            continue
        for amount in amounts:
            value = float(amount.group(1).replace(",", ""))
            unit = amount.group(2).casefold()
            if unit in {"trillion", "tn"}:
                value *= 1000.0
            elif unit in {"million", "mn"}:
                value /= 1000.0
            nearest_year = min(years, key=lambda y: abs(y.start() - amount.start()))
            claims.append({
                "year": int(nearest_year.group(1)), "usdBillion": round(value, 6),
                "domains": domains, "sentence": sentence[:360],
            })
    conflicts: list[dict] = []
    seen_pairs: set[tuple[int, float, int, float]] = set()
    for i, left in enumerate(claims):
        for right in claims[i + 1:]:
            if left["year"] == right["year"] or not (left["domains"] & right["domains"]):
                continue
            earlier, later = (left, right) if left["year"] < right["year"] else (right, left)
            # Flag only a material downward reversal (>25%), not small rounding
            # differences or benign forecast revisions.
            if earlier["usdBillion"] > 0 and later["usdBillion"] < earlier["usdBillion"] * 0.75:
                signature = (earlier["year"], earlier["usdBillion"], later["year"], later["usdBillion"])
                if signature in seen_pairs:
                    continue
                seen_pairs.add(signature)
                conflicts.append({
                    "earlierYear": earlier["year"], "earlierUsdBillion": earlier["usdBillion"],
                    "laterYear": later["year"], "laterUsdBillion": later["usdBillion"],
                    "earlierClaim": earlier["sentence"], "laterClaim": later["sentence"],
                    "reason": "Broad market-size values materially decrease over time; verify scope, unit, source and forecast year before publication.",
                })
    return conflicts


def reconcile_orphaned_chart_references(report: str, charts: list | None = None) -> str:
    """Replace explicit chart promises that have no matching structured chart.

    This prevents prose such as "Chart: Foreign Inflows ... see below" from
    surviving after the chart validator rejected the proposed chart. It never
    creates chart data; it states the artifact was omitted because no validated
    structured chart matches the reference.
    """
    text = str(report or "")
    chart_titles: list[tuple[int, str]] = []
    for index, chart in enumerate(charts or [], start=1):
        if isinstance(chart, dict):
            title = str(chart.get("title") or "").strip()
            if title:
                chart_titles.append((index, title))
    # Only references backed by a slot in the supplied structured chart list
    # count as existing. Dangling placeholders are rewritten below instead of
    # being allowed to survive publication.
    chart_count = len(charts or [])
    existing_refs = {
        int(x) for x in re.findall(r"\[CHART_(\d+)\]", text, re.I)
        if 1 <= int(x) <= chart_count
    }

    stop = {
        "chart", "figure", "visualization", "the", "a", "an", "for", "of", "and", "or", "to", "in", "on",
        "with", "as", "by", "from", "see", "below", "above", "following", "shown", "illustrates", "compares",
    }

    def tokens(value: str) -> set[str]:
        return {x for x in re.findall(r"[a-z0-9]{3,}", value.casefold()) if x not in stop}

    chart_line = re.compile(
        r"^(?P<indent>\s*(?:[-*]\s*)?)(?P<label>(?:chart|figure|visualization)(?:\s+\d+)?\s*[:\-–]\s*)(?P<desc>.+?)\s*$",
        re.I,
    )
    new_lines: list[str] = []
    for line in text.splitlines():
        # An orphan marker has no renderer payload. Replace it with an explicit,
        # reader-facing omission note so it cannot leak into the final report.
        def _replace_dangling(match_ref):
            index = int(match_ref.group(1))
            if 1 <= index <= chart_count:
                return match_ref.group(0)
            return "> **Chart not included:** no validated structured chart is available for this reference."

        line = re.sub(r"\[CHART_(\d+)\]", _replace_dangling, line, flags=re.I)
        match = chart_line.match(line)
        if not match:
            new_lines.append(line)
            continue
        desc = re.sub(r"\s*(?:[-–—:]\s*)?(?:see\s+(?:below|above|following)|shown\s+(?:below|above)|refer\s+to\s+(?:the\s+)?charts?\s+section)\.?\s*$", "", match.group("desc"), flags=re.I).strip()
        desc_tokens = tokens(desc)
        matched = False
        matched_index = None
        for chart_index, title in chart_titles:
            title_tokens = tokens(title)
            if not desc_tokens or not title_tokens:
                continue
            overlap = len(desc_tokens & title_tokens) / max(1, min(len(desc_tokens), len(title_tokens)))
            if overlap >= 0.60:
                matched = True
                matched_index = chart_index
                break
        if matched:
            # If the prose promises a chart but the writer omitted its inline
            # placeholder, put the matching structured chart at this location.
            if matched_index is not None and matched_index not in existing_refs and not re.search(r"\[CHART_\d+\]", line, re.I):
                new_lines.append(line + f"\n\n[CHART_{matched_index}]")
                existing_refs.add(matched_index)
            else:
                new_lines.append(line)
        else:
            subject = desc[:180] or "the requested visualization"
            new_lines.append(
                f"{match.group('indent')}> **Chart not included:** no validated structured chart matched “{subject}”; no chart is shown for this reference."
            )
    return "\n".join(new_lines)


def ensure_requested_chart_disclosure(report: str, question: str, charts: list | None = None) -> str:
    """Disclose a chart requirement that could not be satisfied with validated data."""
    request = str(question or "")
    if not re.search(r"(?is)\b(?:include|add|create|generate|show|provide|produce)\b.{0,100}\b(?:chart|graph|plot|visuali[sz]ation)s?\b|\bat least one\b.{0,50}\b(?:chart|graph|plot|visuali[sz]ation)s?\b", request):
        return str(report or "")
    def _looks_numeric(value: object) -> bool:
        if isinstance(value, bool) or value is None:
            return False
        if isinstance(value, (int, float)):
            return True
        text = str(value).strip().replace(",", "")
        text = re.sub(r"^[₹$€£]\s*", "", text)
        text = re.sub(r"\s*%$", "", text)
        try:
            float(text)
            return bool(text)
        except (TypeError, ValueError):
            return False

    found_valid_data_chart = False
    for chart in charts or []:
        if not isinstance(chart, dict):
            continue
        kind = str(chart.get("type") or chart.get("chartType") or "").lower()
        if kind == "table":
            continue
        if kind == "heatmap":
            values = chart.get("values") or []
            if any(isinstance(row, list) and any(_looks_numeric(value) for value in row) for row in values):
                found_valid_data_chart = True
                break
        for series in chart.get("series") or []:
            if not isinstance(series, dict):
                continue
            data = series.get("data") or []
            if any(isinstance(point, dict) and _looks_numeric(point.get("value")) for point in data):
                found_valid_data_chart = True
                break
        if found_valid_data_chart:
            break
    text = str(report or "")
    if found_valid_data_chart or re.search(r"(?i)\*\*Chart not included:\*\*", text):
        return text
    disclosure = (
        "> **Chart not included:** the request called for a chart, but no validated structured chart "
        "with source-supported numeric observations was available. No chart values have been invented."
    )
    return text.rstrip() + "\n\n" + disclosure if text.strip() else disclosure


def _potential_cross_metric_projection_basis_issues(report: str) -> list[dict]:
    """Flag one CAGR footnote apparently being applied to unrelated metrics.

    This conservative detector is meant for review, not correction. A CAGR for
    market valuation does not automatically justify projections for deposits,
    advances, profit, or other balance-sheet metrics. The source must support
    each projected metric or the report must label that metric unavailable.
    """
    text = str(report or "")
    if not re.search(r"(?i)\bprojections?\s+(?:are\s+)?based on\s+\d+(?:\.\d+)?\s*%\s+CAGR\b", text):
        return []
    issues: list[dict] = []
    for table_index, (headers, rows) in enumerate(_table_rows(text), start=1):
        header_text = " ".join(headers).casefold()
        if not ("metric" in header_text or "indicator" in header_text):
            continue
        if not re.search(r"(?i)\b(?:projected|forecast|outlook)\b", header_text):
            continue
        row_labels = [str(row[0] if row else "").casefold() for row in rows]
        families = {
            "deposits": any(re.search(r"\bdeposit(?:s)?\b", label) for label in row_labels),
            "advances/credit": any(re.search(r"\b(?:gross\s+)?advances?\b|\bcredit\b|\bloans?\b", label) for label in row_labels),
            "market value/size": any(re.search(r"\bmarket\s+(?:value|size)\b", label) for label in row_labels),
            "profitability": any(re.search(r"\b(?:profit|profitability|roa|roe|margin)\b", label) for label in row_labels),
            "asset quality": any(re.search(r"\b(?:npa|non[- ]performing|asset quality)\b", label) for label in row_labels),
        }
        present = [name for name, found in families.items() if found]
        if len(present) >= 2:
            issues.append({
                "tableIndex": table_index,
                "metricFamilies": present,
                "rowLabels": [label[:100] for label in row_labels[:12]],
                "reason": (
                    "A general CAGR footnote appears alongside forecast values for multiple distinct metrics. "
                    "A CAGR is metric-specific; verify a separate source-supported basis for each projected row."
                ),
            })
    return issues


def _find_implausible_inr_usd_equivalents(text: str) -> list[dict]:
    """Find obvious order-of-magnitude errors in paired INR lakh-crore/USD equivalents.

    This guard is intentionally narrow: it only checks values explicitly presented
    as an INR lakh-crore amount followed immediately by a parenthetical USD amount.
    It flags impossible implied exchange rates; it never rewrites a financial value.
    """
    pattern = re.compile(
        r"(?P<inr>(?:₹|Rs\.?\s*)\s*(?P<inr_value>[\d,]+(?:\.\d+)?)\s*lakh\s*crore)"
        r"\s*\(\s*(?P<usd>(?:US\$|USD|\$)\s*(?P<usd_value>[\d,]+(?:\.\d+)?)\s*"
        r"(?P<usd_unit>billion|bn|million|mn|trillion|tn))\s*\)",
        re.I,
    )
    factors = {"billion": 1e9, "bn": 1e9, "million": 1e6, "mn": 1e6, "trillion": 1e12, "tn": 1e12}
    findings = []
    for match in pattern.finditer(str(text or "")):
        try:
            inr_value = float(match.group("inr_value").replace(",", "")) * 1e12
            usd_value = float(match.group("usd_value").replace(",", "")) * factors[match.group("usd_unit").casefold()]
            implied_rate = inr_value / usd_value if usd_value else float("inf")
        except (ValueError, KeyError, ZeroDivisionError):
            continue
        # A very broad band intentionally avoids depending on a precise current FX
        # quote; an implied <40 or >160 INR/USD is an order-of-magnitude warning.
        if not (40 <= implied_rate <= 160):
            findings.append({
                "rupeeAmount": match.group("inr"),
                "dollarEquivalent": match.group("usd"),
                "impliedINRPerUSD": round(implied_rate, 3),
                "reason": "The paired amounts imply an implausible INR/USD exchange rate; verify the unit conversion against the source.",
            })
    return findings[:20]


def audit_report_publication(
    report: str,
    charts: list | None = None,
    images: list | None = None,
    sources: list | None = None,
) -> dict:
    """Audit publication hazards without changing or inventing research facts.

    This is a deterministic guardrail: it identifies leaked protocol tokens,
    dangling visual references, unresolvable citations, duplicated table blocks,
    and suspiciously identical numeric values across entity-specific rows.
    The last category is a warning requiring source verification, not an
    automatic edit to any figure.
    """
    text = str(report or "")
    charts = charts or []
    images = images or []
    sources = sources or []
    marker_matches = re.findall(
        r"\[\[(?:SECTION:|END SECTION|SECTION TO EXPAND|END_SCAN_SECTION)", text, re.I
    )
    # Layout instructions must be consumed by the structured renderer and must
    # never leak into the final reader-facing report.
    # Catch standalone and inline layout-control tokens: a model may leak the
    # marker after a sentence or citation instead of on its own line.
    unresolved_layout_markers = re.findall(r"\[\s*RISK_MATRIX\s*\]", text, re.I)
    # Parse citation syntax, not every bare S<number> token in prose. The former
    # global regex created false source IDs from model names and other labels,
    # while also making grouped citations easy to mishandle in the evidence path.
    citation_groups = re.compile(r"(?<![A-Za-z0-9])[\[(]\s*(S\d+(?:\s*[,;]\s*S\d+)*)\s*[\])]", re.I)
    citations: set[str] = set()
    for match in citation_groups.finditer(text):
        citations.update(item.upper() for item in re.findall(r"\bS\d+\b", match.group(1), re.I))
    available_ids = {
        str(item.get("id") or "").strip().upper()
        for item in sources if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    # Missing source records may be represented explicitly in the appendix so
    # a broken citation is diagnosable, but those placeholders are still a hard
    # provenance failure rather than a way to make the citation appear resolved.
    missing_source_records = sorted(
        str(item.get("id") or "").strip().upper()
        for item in sources if isinstance(item, dict)
        and str(item.get("source_status") or item.get("sourceStatus") or "").casefold() == "unresolved"
        and str(item.get("id") or "").strip()
    )
    unresolved_citations = sorted(citations - available_ids)
    chart_refs = sorted({int(x) for x in re.findall(r"\[CHART_(\d+)\]", text, re.I)})
    image_refs = sorted({int(x) for x in re.findall(r"\[(?:WEB_IMG|IMAGE)_(\d+)\]", text, re.I)})
    dangling_charts = [n for n in chart_refs if n < 1 or n > len(charts)]
    dangling_images = [n for n in image_refs if n < 1 or n > len(images)]
    # A prose-only chart promise is also a dangling reference even when the model
    # never emitted a [CHART_n] token (for example, "Chart: ... see below").
    orphaned_chart_mentions = re.findall(
        r"(?im)^\s*(?:[-*]\s*)?(?:chart|figure|visualization)(?:\s+\d+)?\s*[:\-–]\s*.*(?:see\s+(?:below|above|following)|shown\s+(?:below|above)|refer\s+to\s+(?:the\s+)?charts?\s+section).*?$",
        text,
    )
    # Explicit omission disclosures are a safe publication state, not dangling
    # chart promises. Do not count them as orphaned chart mentions.

    tables = _table_rows(text)
    table_defs_for_period_check: list[tuple[list[str], list[list[str]], str]] = [
        (headers, rows, "report_markdown") for headers, rows in tables
    ]
    # Markdown tables are converted to structured table-chart objects before
    # final QA; audit those objects as well or period mismatches become invisible
    # once the raw Markdown is replaced by [CHART_n].
    for chart_index, chart in enumerate(charts, start=1):
        if not isinstance(chart, dict):
            continue
        chart_type = str(chart.get("type") or chart.get("chartType") or chart.get("chart_type") or "").casefold()
        if chart_type != "table":
            continue
        cols = chart.get("columns") if isinstance(chart.get("columns"), list) else []
        raw_rows = chart.get("rows") if isinstance(chart.get("rows"), list) else []
        normalized_rows = []
        for row in raw_rows:
            if isinstance(row, dict):
                normalized_rows.append([str(row.get(str(c), row.get(c, "")) or "") for c in cols])
            elif isinstance(row, (list, tuple)):
                normalized_rows.append([str(v or "") for v in row])
        if cols and normalized_rows:
            table_defs_for_period_check.append((list(map(str, cols)), normalized_rows, f"chart_{chart_index}"))
    table_signatures: set[str] = set()
    duplicate_table_count = 0
    duplicate_observations: list[dict] = []
    for table_index, (headers, rows) in enumerate(tables, start=1):
        signature_text = "\n".join(["|".join(headers), *["|".join(row) for row in rows]])
        signature_text = re.sub(r"\[S\d+\]", "", signature_text, flags=re.I)
        signature = re.sub(r"\s+", " ", signature_text.casefold()).strip()
        if signature in table_signatures:
            duplicate_table_count += 1
        table_signatures.add(signature)

        # A source ID is provenance, not a unique observation. Repeating the
        # same metric/entity/period/value row under [S2] instead of [S3] must
        # not make the observation appear new. Detect duplicate numeric rows
        # within evidence tables after removing source attribution.
        seen_rows: dict[str, int] = {}
        for row_number, row in enumerate(rows, start=1):
            if len(row) < 2:
                continue
            raw_row = " | ".join(str(cell or "") for cell in row)
            without_citations = re.sub(r"(?:\[S\d+(?:\s*[,;]\s*S\d+)*\]|\(S\d+\))", "", raw_row, flags=re.I)
            normalized_row = re.sub(r"[^a-z0-9%₹$€£]+", " ", without_citations.casefold()).strip()
            numeric_values = re.findall(r"(?<![a-z])\d[\d,.]*(?:\s*%|\s*(?:gw|mw|crore|lakh|million|billion|bps))?", normalized_row, re.I)
            if not numeric_values:
                continue
            # Don't confuse year-only rows or simple numbering with observations.
            if all(re.fullmatch(r"(?:19|20)\d{2}", value.strip()) for value in numeric_values):
                continue
            if normalized_row in seen_rows:
                duplicate_observations.append({
                    "tableIndex": table_index,
                    "firstRow": seen_rows[normalized_row],
                    "duplicateRow": row_number,
                    "row": raw_row[:260],
                    "issue": "Duplicate numeric observation row after source-citation normalization.",
                })
            else:
                seen_rows[normalized_row] = row_number

    suspicious_repeats: list[dict] = []
    entity_headers = ("bank", "company", "entity", "institution", "provider", "vendor", "peer", "region", "country", "competitor")
    metric_headers = ("market cap", "credit growth", "revenue", "margin", "cagr", "growth", "ratio", "share", "value", "r&d", "spend", "portfolio")
    for table_index, (headers, rows) in enumerate(tables, start=1):
        if len(rows) < 3 or not headers:
            continue
        first_header = headers[0].casefold()
        if not any(term in first_header for term in entity_headers):
            continue
        for col_idx, header in enumerate(headers[1:], start=1):
            header_low = header.casefold()
            if not any(term in header_low for term in metric_headers):
                continue
            values = [re.sub(r"\s+", " ", row[col_idx]).strip().casefold() for row in rows if col_idx < len(row)]
            values = [v for v in values if v and v not in {"—", "-", "n/a", "na", "unavailable", "unknown"}]
            if len(values) >= 3:
                from collections import Counter
                value, count = Counter(values).most_common(1)[0]
                if count >= 3 and count / len(values) >= 0.75:
                    suspicious_repeats.append({
                        "tableIndex": table_index,
                        "column": header,
                        "repeatedValue": value,
                        "rowsWithValue": count,
                        "rowsWithAvailableValue": len(values),
                        "reason": "Same metric value is repeated across most distinct entities; verify each row against its cited source.",
                    })

    forecast_conflicts = _potential_market_forecast_conflicts(text)
    cross_metric_projection_issues = _potential_cross_metric_projection_basis_issues(text)
    timeframe_label_mismatches = _table_period_mismatches(table_defs_for_period_check)
    incomplete_numeric_fragments = find_incomplete_numeric_fragments(text)
    currency_conversion_anomalies = _find_implausible_inr_usd_equivalents(text)
    empty_chart_indices = [idx for idx, chart in enumerate(charts, start=1) if not _chart_has_nonempty_payload(chart)]
    untitled_chart_indices = [idx for idx, chart in enumerate(charts, start=1)
                              if isinstance(chart, dict) and not str(chart.get("title") or "").strip()]
    chart_evidence = audit_chart_evidence_consistency(text, charts, sources)

    missing_image_source = []
    for index, image in enumerate(images, start=1):
        if not isinstance(image, dict):
            continue
        source_page = str(image.get("sourceUrl") or image.get("sourceURL") or image.get("source_url") or image.get("pageUrl") or image.get("page_url") or "").strip()
        if not source_page:
            missing_image_source.append(index)

    flags: list[str] = []
    if marker_matches:
        flags.append("Internal continuation markers are present in report text.")
    if unresolved_layout_markers:
        flags.append("One or more structured layout markers (for example [RISK_MATRIX]) remain unresolved in report text.")
    if dangling_charts:
        flags.append("One or more chart placeholders do not resolve to structured chart data.")
    if orphaned_chart_mentions:
        flags.append("One or more chart references have no matching validated structured chart.")
    if dangling_images:
        flags.append("One or more image placeholders do not resolve to available image assets.")
    if unresolved_citations:
        flags.append("Some in-text source identifiers do not resolve to the source manifest.")
    if missing_source_records:
        flags.append("The report contains citation IDs whose source records are missing; those claims are not independently verifiable.")
    if duplicate_table_count:
        flags.append("Exact duplicate tables remain in the report.")
    if duplicate_observations:
        flags.append("One or more numeric observations are duplicated within an evidence table despite different or repeated source attributions.")
    if suspicious_repeats:
        flags.append("One or more entity-comparison tables contain suspiciously repeated metric values.")
    if forecast_conflicts:
        flags.append("Broad market-size forecasts may be inconsistent across years or scopes; reconcile units, definitions, periods and source evidence.")
    if cross_metric_projection_issues:
        flags.append("A general CAGR footnote may be applied to forecasts for multiple distinct metrics; verify a metric-specific basis for every projected row.")
    if timeframe_label_mismatches:
        flags.append("One or more table cells use a forecast/terminal year that conflicts with the column's stated forecast horizon.")
    if incomplete_numeric_fragments:
        flags.append("One or more sentences end in an incomplete numeric/currency clause; remove the fragment or verify the missing value from source evidence.")
    if currency_conversion_anomalies:
        flags.append("One or more paired INR/USD equivalents imply an implausible exchange rate; verify the currency conversion against the cited source.")
    if empty_chart_indices:
        flags.append("One or more structured charts/tables have no usable data and must be removed or repaired before rendering.")
    if untitled_chart_indices:
        flags.append("One or more structured charts have no title.")
    if missing_image_source:
        flags.append("Some external images lack a verifiable publisher-page URL.")
    if chart_evidence["chartTableVerificationConflicts"]:
        flags.append("A numeric chart conflicts with a related table that marks the observation unverified.")
    if chart_evidence["chartEvidenceFailures"]:
        flags.append("One or more numeric chart observations lack source excerpts that verify the value, subject and period.")

    return {
        "status": "review_required" if flags else "clean",
        "flags": flags,
        "controlMarkerCount": len(marker_matches) + len(unresolved_layout_markers),
        "unresolvedLayoutMarkers": unresolved_layout_markers,
        "danglingChartRefs": dangling_charts,
        "orphanedChartMentions": [str(x).strip()[:300] for x in orphaned_chart_mentions[:20]],
        "danglingImageRefs": dangling_images,
        "unresolvedCitationIds": unresolved_citations,
        "missingSourceRecords": missing_source_records,
        "duplicateTableCount": duplicate_table_count,
        "duplicateObservations": duplicate_observations[:100],
        "suspiciousEntityTableValues": suspicious_repeats,
        "potentialForecastConflicts": forecast_conflicts,
        "crossMetricProjectionBasisIssues": cross_metric_projection_issues,
        "timeframeLabelMismatches": timeframe_label_mismatches,
        "incompleteNumericFragments": incomplete_numeric_fragments,
        "currencyConversionAnomalies": currency_conversion_anomalies,
        "emptyChartIndices": empty_chart_indices,
        "untitledChartIndices": untitled_chart_indices,
        "imagesMissingPublisherPage": missing_image_source,
        "chartTableVerificationConflicts": chart_evidence["chartTableVerificationConflicts"],
        "chartEvidenceFailures": chart_evidence["chartEvidenceFailures"],
        "note": "Suspicious repeated values are flagged, never silently changed. Source verification is required before publication.",
    }
