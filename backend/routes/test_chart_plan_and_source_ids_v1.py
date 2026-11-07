"""Chart planning and source-ID consistency across the report stages."""
from __future__ import annotations

import asyncio

import routes.report as report
from routes.report import _final_chart_evidence_gate, _normalise_inline_chart_spec, _validate_chart_spec
from routes.source_manifest import build_source_registry, normalise_source_manifest
from utils.chart_gallery import gallery_types_for
from utils.chart_planner import assess_chart_coverage, bind_charts_to_plan, plan_chart_needs


def _manifest_and_enriched():
    quote1 = "Revenue was 100 crore in 2024."
    quote2 = "Revenue was 120 crore in 2025."
    source = {
        "id": "S2", "title": "Verified revenue data", "url": "https://example.com/revenue",
        "publisher": "example.com", "kind": "Web source", "source_status": "resolved",
        "page_fetch_status": "ok", "evidenceExcerpt": f"{quote1} {quote2}",
        "chartEvidenceExcerpt": f"{quote1}\n{quote2}",
    }
    enriched = [{
        "id": "S2", "url": source["url"], "matchedEvidenceUrl": source["url"], "pageFetchStatus": "ok",
        "evidenceOrigin": "publisher_page", "evidenceExcerpt": source["evidenceExcerpt"],
        "publisherEvidenceExcerpt": source["evidenceExcerpt"], "chartEvidenceExcerpt": source["chartEvidenceExcerpt"],
        "pageContent": f"{quote1}\n{quote2}", "evidenceAvailable": True,
    }]
    return [source], enriched


def _chart_citing(source_id: str) -> dict:
    return {
        "type": "bar", "title": "Revenue 2024-2025", "unit": "crore", "xLabel": "Year", "yLabel": "Revenue (crore)",
        "sourceIds": [source_id], "source": [source_id],
        "series": [{"name": "Revenue", "data": [
            {"label": "Revenue 2024", "value": 100, "evidenceSource": source_id, "evidenceValue": 100,
             "evidenceQuote": "Revenue was 100 crore in 2024."},
            {"label": "Revenue 2025", "value": 120, "evidenceSource": source_id, "evidenceValue": 120,
             "evidenceQuote": "Revenue was 120 crore in 2025."},
        ]}],
    }


def _gate(charts, manifest, enriched):
    return _final_chart_evidence_gate(
        "Revenue\n[CHART_1]", charts, manifest, enriched, normalizer=lambda c: c,
        validator=_validate_chart_spec, adapter=_normalise_inline_chart_spec, stage="t",
    )


def test_gate_repoints_chart_that_cites_the_wrong_id_to_the_source_holding_the_quote():
    manifest, enriched = _manifest_and_enriched()
    _, charts, final_manifest, _, meta = _gate([_chart_citing("S9")], manifest, enriched)
    assert len(charts) == 1, meta
    assert charts[0]["sourceIds"] == ["S2"]
    assert {p["evidenceSource"] for p in charts[0]["series"][0]["data"]} == {"S2"}
    assert meta["residualFindings"] == 0


def test_without_reconciliation_the_same_chart_is_dropped(monkeypatch):
    """Documents the defect: the audit alone cannot recover a mis-numbered chart."""
    manifest, enriched = _manifest_and_enriched()
    monkeypatch.setattr(report, "_reconcile_charts_to_manifest", lambda charts, *a, **k: (charts, {}))
    _, charts, *_ = _gate([_chart_citing("S9")], manifest, enriched)
    assert charts == []


def test_correctly_numbered_chart_is_untouched_by_reconciliation():
    manifest, enriched = _manifest_and_enriched()
    original = _chart_citing("S2")
    _, charts, *_ = _gate([original], manifest, enriched)
    assert len(charts) == 1 and charts[0]["sourceIds"] == ["S2"]


def test_action_web_research_extends_a_gapped_registry_without_renumbering(monkeypatch):
    prior = normalise_source_manifest(build_source_registry(
        [{"title": f"Src {n}", "url": f"https://x.example/{n}"} for n in range(1, 6)]))
    cited_only = [prior[0], prior[2], prior[4]]          # S1, S3, S5 - a Brief appendix

    async def fake_search(*_a, **_k):
        return [{"title": "Fresh page", "url": "https://fresh.example/a", "snippet": "Fresh figure 42%."}]

    import routes.chat as chat
    monkeypatch.setattr(chat, "exa_search_multi", fake_search)
    evidence, combined = asyncio.run(report._action_web_research("add detail", "Findings", "Title", cited_only))
    assert [s["id"] for s in combined] == ["S1", "S3", "S5", "S6"]
    assert "[S6]" in evidence and "[S1]" not in evidence


def test_gallery_types_follow_the_plan_for_domain_questions_only():
    question = "Sector rotation in Indian markets over the past 8 quarters"
    default = gallery_types_for(question)
    narrowed = gallery_types_for(question, planned_types=["heatmap", "line", "bar"])
    assert set(narrowed) <= set(default) and "scatter" not in narrowed and "heatmap" in narrowed
    # a plan that leaves <2 candidates must not collapse the domain set
    assert gallery_types_for(question, planned_types=["heatmap"]) == default
    # generic questions keep the existing catalogue behaviour
    assert gallery_types_for("Summarise the EV market", planned_types=["bar", "line"]) == gallery_types_for("Summarise the EV market")


def test_source_less_writer_chart_is_bound_to_planned_evidence():
    text = ("| Sector | Q1 2025 | Q2 2025 | Q3 2025 | Q4 2025 |\n"
            + "\n".join(f"| Nifty {name} return | 1.1% | 2.2% | 3.3% | 4.4% |"
                        for name in ("Bank", "IT", "Pharma", "FMCG", "Auto", "Metal")))
    coverages = assess_chart_coverage(plan_chart_needs("sector rotation past 8 quarters")[:1], [{"id": "S4", "text": text}])
    chart = {"type": "heatmap", "title": "Nifty sector returns by quarter", "rows": ["a"], "columns": ["b"], "values": [[1.0]]}
    other = {"type": "bar", "title": "Unrelated chart", "series": [{"name": "x", "data": [{"label": "a", "value": 1}]}]}
    bound, count = bind_charts_to_plan([chart, other], coverages)
    assert count == 1 and bound[0]["sourceIds"] == ["S4"] and "sourceIds" not in bound[1]
