"""Automatic defect scan of a finished report: every defect class found by reading live reports is a rule.

Run over every report of a benchmark run (scripts/defect_scan.py) so a regression shows up as a number,
not as something a person has to notice. Each rule returns the offending snippets.
"""
from __future__ import annotations

import re

# (rule id, severity, description, regex) - severity "critical" blocks a release of the engine.
RULES: list[tuple[str, str, str, re.Pattern]] = [
    ("leaked_marker", "critical", "internal [[...]] marker in the report text",
     re.compile(r"\[\[(?!FIGURE\b)[^\]]{1,60}\]\]")),
    ("leaked_fact_id", "critical", "raw fact id (F61) in the report text",
     re.compile(r"(?<![\w\-/.&])F\d{1,3}\b(?![\-/&])")),
    ("odd_citation", "high", "citation not in [S..] form (【S3】)",
     re.compile(r"【\s*S\d+[^】]*】")),
    ("doubled_value", "high", "the same value printed twice (\"94% (94% TWh)\")",
     re.compile(r"(\d[\d,.]*\s?%?)\s*\(\s*\1\s?[A-Za-z%]")),
    ("broken_unit", "high", "a malformed unit (\"%%\", \"MW%\", \"11,455 ₹ Cr\")",
     re.compile(r"\d\s?%%|\d\s?(?:MW|GW|MT|crore|lakh)%|\d\s[₹$]\s?(?:Cr|crore|Mn|Bn)\b")),
    ("split_rupee", "high", "a sentence broken at \"Rs.\" (\"Rs [S4]. 25 crore\")",
     re.compile(r"\bRs\s*\[S\d")),
    ("ascii_chart", "high", "a chart drawn with characters inside a table",
     # drawing characters beyond the dashes of a Markdown separator row ("| --- | --- |" is not a chart)
     re.compile(r"\|\s*(?=[-*/\\_=.+o]*[*/\\o])[-*/\\_=.+o]{10,}\s*\|")),
    ("footnoted_year", "medium", "a year with a footnote read as a value (\"20231\")",
     re.compile(r"(?<![\d,.\-−])(?:19|20)\d{2}[1-9](?![\d,.%])(?!\s*(?:crore|lakh|million|billion|MW|GW|tonnes|units|km))")),
    ("schema_text", "medium", "dataset schema text (\"Int64\", \"Distinct | Count\") in the report",
     re.compile(r"\bInt64\b|\bFloat64\b|\|\s*Distinct\s*\|")),
    ("raw_date_header", "medium", "a raw date used as a column name (\"30-Jan-26,\")",
     re.compile(r"\|\s*\d{1,2}-[A-Z][a-z]{2}-\d{2},")),
]


def scan_report(report: str) -> dict[str, list[str]]:
    """Rule id -> up to five offending snippets (rules with no hit are left out)."""
    text = str(report or "")
    found: dict[str, list[str]] = {}
    for rule_id, _severity, _description, pattern in RULES:
        hits = []
        for match in pattern.finditer(text):
            hits.append(text[max(0, match.start() - 50):match.end() + 30].replace("\n", " "))
            if len(hits) >= 5:
                break
        if hits:
            found[rule_id] = hits
    return found


def scan_capture(capture: dict, *, chart_minimum: dict[str, int] | None = None) -> dict:
    """Defects of one captured run: text rules, audit findings by severity, chart count against the minimum."""
    def stage(name: str) -> dict:
        value = (capture.get("stages") or {}).get(name) or {}
        return value.get("data", value) if isinstance(value, dict) else {}

    final = stage("final_output")
    v2 = stage("pipeline_v2")
    report = str(final.get("report") or "")
    text_defects = scan_report(report)
    severity = {rule_id: sev for rule_id, sev, _d, _p in RULES}
    findings = (final.get("audit") or {}).get("findings") or []
    audit = {level: sum(1 for f in findings if f.get("severity") == level) for level in ("critical", "high", "medium")}
    tier = str(capture.get("reportTier") or "detailed")
    minimum = (chart_minimum or {"detailed": 8, "in_depth": 18}).get(tier, 8)
    charts = len(final.get("charts") or [])
    removed = len(((v2.get("numberPolicy") or {}).get("sentencesRemoved")) or [])
    return {
        "status": final.get("publicationStatus"),
        "tier": tier,
        "charts": charts,
        "chartMinimum": minimum,
        "chartsBelowMinimum": max(0, minimum - charts),
        "sentencesRemoved": removed,
        "auditFindings": audit,
        "textDefects": {k: len(v) for k, v in text_defects.items()},
        "criticalTextDefects": sum(len(v) for k, v in text_defects.items() if severity.get(k) == "critical"),
        "examples": text_defects,
    }
