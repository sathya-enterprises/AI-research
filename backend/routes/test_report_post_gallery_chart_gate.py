"""Regression tests for the final, export-equivalent chart evidence gate."""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from routes.report import _final_chart_evidence_gate, _extract_company_candidates, _validate_chart_spec, _normalise_inline_chart_spec, _research_domain_from_intent
from utils.intent import RequestIntent


def _evidence_sources():
    quote1 = "Revenue was 100 crore in 2024."
    quote2 = "Revenue was 120 crore in 2025."
    source = {
        "id": "S1", "title": "Verified revenue data", "url": "https://example.com/revenue",
        "publisher": "example.com", "kind": "Web source", "source_status": "resolved",
        "page_fetch_status": "ok", "evidenceExcerpt": f"{quote1} {quote2}",
        "chartEvidenceExcerpt": f"{quote1}\n{quote2}",
    }
    enriched = [{
        "id": "S1", "url": source["url"], "matchedEvidenceUrl": source["url"],
        "pageFetchStatus": "ok", "evidenceOrigin": "publisher_page",
        "evidenceExcerpt": source["evidenceExcerpt"],
        "publisherEvidenceExcerpt": source["evidenceExcerpt"],
        "chartEvidenceExcerpt": source["chartEvidenceExcerpt"],
        "evidenceAvailable": True,
    }]
    return [source], enriched


def _chart(title: str, bad_derived_input: bool = False):
    return {
        "type": "bar", "title": title, "unit": "crore", "xLabel": "Year", "yLabel": "Revenue (crore)", "source": ["S1"],
        "series": [{"name": "Revenue", "data": [
            {"label": "Revenue 2024", "value": 100, "evidenceSource": "S1",
             "evidenceValue": 100, "evidenceQuote": "Revenue was 100 crore in 2024."},
            {"label": "Revenue 2025", "value": 120, "evidenceSource": "S1",
             "evidenceValue": 120, "evidenceQuote": "Revenue was 120 crore in 2025."},
        ]}],
        "derivedFrom": ([{"sourceId": "S1", "label": "bad input", "value": 999,
                          "evidenceValue": 999, "quote": "Revenue was 999 crore in 2025."}]
                        if bad_derived_input else []),
    }


def test_post_gallery_gate_drops_bad_derived_chart_but_keeps_verified_chart():
    manifest, enriched = _evidence_sources()
    body = "Verified revenue\n[CHART_1]\nUnsupported derived chart\n[CHART_2]"
    text, charts, final_manifest, audited, meta = _final_chart_evidence_gate(
        body, [_chart("Verified revenue 2024–2025"), _chart("Unsupported derived chart", True)],
        manifest, enriched, normalizer=lambda chart: chart, validator=_validate_chart_spec,
        adapter=_normalise_inline_chart_spec, stage="test",
    )
    assert len(charts) == 1
    assert charts[0]["title"] == "Verified revenue 2024–2025"
    assert "[CHART_1]" in text
    assert "[CHART_2]" not in text
    assert meta["residualFindings"] == 0


def test_empty_or_sparse_chart_tables_are_rejected():
    manifest, enriched = _evidence_sources()
    sparse = {"type": "table", "title": "Sparse table", "columns": ["Metric", "Value"],
              "rows": [["Revenue", ""], ["", ""]]}
    text, charts, *_ = _final_chart_evidence_gate(
        "Sparse table\n[CHART_1]", [sparse], manifest, enriched, normalizer=lambda chart: chart,
        validator=_validate_chart_spec, adapter=_normalise_inline_chart_spec, stage="test-sparse-table",
    )
    assert charts == []
    assert "[CHART_1]" not in text


def test_question_opener_is_not_resolved_as_a_company():
    intent = RequestIntent(
        resolved_topic="Indian stock markets sector rotation", evidence_needed=["historical"],
        intent_label="research", wants_report=True, source="test",
    )
    question = "How can you explain and analyze the sector rotation in Indian stock markets over the past 8 quarters?"
    assert _extract_company_candidates(question, intent=intent) == []


def test_broad_economy_stays_general_while_fta_uses_regulatory_research():
    broad = RequestIntent(
        resolved_topic="Indian economy", evidence_needed=["news", "historical", "regulatory"],
        intent_label="research", wants_report=True, source="test",
    )
    fta = RequestIntent(
        resolved_topic="India free trade agreements", evidence_needed=["regulatory", "historical", "comparison"],
        intent_label="research", wants_report=True, source="test",
    )
    assert _research_domain_from_intent("Tell me what's happening with Indian economy and economic progress", broad) == "generic"
    assert _research_domain_from_intent("Compare India's free trade agreements signed in the past year", fta) == "regulatory"


def test_gallery_is_included_in_page_budget_and_search_snippets_are_not_proof_by_default():
    source = __import__("pathlib").Path(__file__).with_name("report.py").read_text(encoding="utf-8")
    gallery_pos = source.index("_append_evidence_chart_gallery(", source.index("async def "))
    page_budget_pos = source.index("_ensure_report_page_budget(", gallery_pos)
    assert gallery_pos < page_budget_pos
    assert 'os.getenv("REPORT_ACCEPT_SEARCH_EXTRACT_EVIDENCE", "0")' in source
