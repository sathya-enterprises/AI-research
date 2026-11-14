"""Regressions for two content-loss bugs.

1. ``_report_body_from_value`` / ``_recover_report_body`` contained string literals where an
   intended ``"\\n"`` had become a real line break (``"\\<newline>"`` is an empty string), so
   ``value.replace("", "\\n")`` put every character on its own line. A provider-outage report
   was published that way.
2. ``evidence_check._fact_supported`` reported a sentence as unsupported when its only numbers
   were labelled calculations whose inputs and arithmetic had been verified, so the evidence
   check deleted results the number policy had accepted.
"""
from __future__ import annotations

import io
import json
import pathlib
import tokenize

import routes.report as report
from routes.source_manifest import build_source_registry
from utils.evidence_check import _fact_supported
from utils.evidence_store import build_evidence_store
from utils.fact_sheet import FactSheet

BACKEND = pathlib.Path(__file__).resolve().parents[1]
BS = "\\"


def _has_line_continuation(literal: str) -> bool:
    index = literal.find("\n")
    while index != -1:
        run, cursor = 0, index - 1
        while cursor >= 0 and literal[cursor] == BS:
            run += 1
            cursor -= 1
        if run % 2 == 1:
            return True
        index = literal.find("\n", index + 1)
    return False


def test_no_single_line_string_literal_contains_a_backslash_newline():
    offenders = []
    for path in sorted(BACKEND.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        source = path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type != tokenize.STRING:
                continue
            if token.string.lstrip("rbuRBUfF").startswith(('"""', "'''")):
                continue
            if _has_line_continuation(token.string):
                offenders.append(f"{path.relative_to(BACKEND)}:{token.start[0]}")
    assert offenders == [], f"string literals that collapse to '' via backslash-newline: {offenders}"


def test_deterministic_fallback_report_survives_body_recovery():
    sources = [{
        "id": f"S{i}", "title": f"Source {i}",
        "evidence": ("The Nifty IT index fell 12.5% in Q3 2025 while banks rose 8.1% as foreign investors "
                     "rotated into financials. Analysts said conventional methods tend to underperform during "
                     "periods of high volatility."),
    } for i in range(1, 6)]
    plan = {"sections": [{"heading": "Findings", "purpose": "x"}, {"heading": "Rotation", "purpose": "y"}]}
    raw, model = report._deterministic_report_json("x", "in_depth", report_plan=plan, sources=sources)
    expected = json.loads(raw)["report"].strip()

    recovered = report._recover_report_body(raw)

    assert model == "local:evidence-fallback"
    assert recovered == expected
    assert max(len(line) for line in recovered.splitlines()) > 40


def test_recovery_decodes_escaped_newlines_in_a_malformed_envelope():
    body = "Line one has enough words.\\nLine two follows here. " * 40
    raw = '{"report": "' + body + '", "charts": ['  # truncated JSON: json.loads fails

    recovered = report._recover_report_body(raw)

    assert recovered.startswith("Line one has enough words.\nLine two follows here.")
    assert "\\n" not in recovered


def test_short_body_is_not_replaced_by_a_longer_corrupted_recovery():
    body = "## Findings\n\n" + "Banks rose 8.1% in Q3 2025 as investors rotated into financials [S1]. " * 8
    raw = json.dumps({"title": "t", "report": body, "charts": []})

    recovered = report._report_body_from_value(json.loads(raw))

    assert recovered in ("", body.strip())


def _official_store():
    table = (
        "OFFICIAL DATASET — World Bank, World Development Indicators: Individuals using the Internet "
        "(% of population) [IT.NET.USER.ZS]\n"
        "TABLE 1 — Individuals using the Internet (% of population), 2015–2024\n"
        "Year | Brazil | India | Kenya\n"
        "2015 | 58.33 | 14.90 | 16.59\n"
        "2020 | 81.34 | 43.41 | 24.72\n"
        "2024 | 84.46 | 64.94 | 34.98"
    )
    rows = [{
        "title": "World Bank: Individuals using the Internet (% of population), 2015-2024",
        "url": "https://data.worldbank.org/indicator/IT.NET.USER.ZS", "publisher": "World Bank",
        "fullContent": table, "pageContent": table, "pageFetchStatus": "ok",
    }]
    question = "Compare internet use in India, Kenya and Brazil from 2015 to 2024"
    manifest = build_source_registry(rows, [])
    store = build_evidence_store(rows, question=question, manifest=manifest)
    return FactSheet(store.passages), store


def test_sentence_whose_only_number_is_a_verified_calculation_is_supported():
    sheet, store = _official_store()
    claim = "India's internet use grew by 50.04 (calculated: 64.94 - 14.90) percentage points over the decade [S1]."

    assert _fact_supported(sheet, store, claim, {"S1"}) is True


def test_wrong_arithmetic_or_unsourced_inputs_are_still_unsupported():
    sheet, store = _official_store()

    assert _fact_supported(sheet, store, "India grew by 50.05 (calculated: 64.94 - 14.90) points [S1].", {"S1"}) is False
    assert _fact_supported(sheet, store, "India grew by 50.04 (calculated: 64.94 - 99.99) points [S1].", {"S1"}) is False
    assert _fact_supported(sheet, store, "India's internet use reached 71.20% in 2024 [S1].", {"S1"}) is False
