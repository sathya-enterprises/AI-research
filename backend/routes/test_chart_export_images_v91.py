"""v91: charts that pass at report time must survive PDF/HTML export; writer charts are
grounded in full source text; gallery charts are on-topic; Brief reports get a credited image.

Reproduces the 20 Oct 16:29 run: report had 3 charts, the PDF export gate removed all 3
because the source manifest round-trip dropped the chart-verification evidence.
"""
from __future__ import annotations

import contextvars
import glob
import json
from pathlib import Path

import pytest

import routes.report as R
from routes.source_manifest import normalise_source_manifest
from utils.chart_evidence_audit import sanitize_charts_for_publication

RBI_TABLE = (
    "[STRUCTURED PUBLISHER TABLE DATA — original rows preserved]\n"
    "TABLE 1 — Publications\n"
    "Sr. No | Items | Amount Outstanding (At end-March) | Percentage Variation\n"
    "2024 | 2025 P | 2024 | 2025 P\n"
    "4 | Asset Quality #\n"
    "4.3 Gross NPA ratio (Gross NPAs as percentage of gross advances)** | 2.7 | 2.2\n"
    "4.5 Provision Coverage Ratio (Per cent) * | 76.2 | 76.3\n"
)
URL = "https://www.rbi.org.in/scripts/PublicationsView.aspx?id=23642"


def _fixture():
    manifest = [{"id": "S4", "title": "Report on Trend and Progress", "url": URL, "_ggSourceRegistryVersion": 1}]
    enriched = [{
        "title": "Report on Trend and Progress", "url": URL, "pageContent": RBI_TABLE, "pageFetchStatus": "ok",
        "pageFetchUrl": URL, "pageFetchRelevanceMatched": True, "evidenceOrigin": "publisher_page",
        "fullContent": "Indicators of scheduled commercial banks (SCBs). SCBs remained resilient.",
    }]
    return manifest, enriched


def _gnpa_chart(points):
    return {"type": "line", "title": "Gross NPA ratio — Scheduled Commercial Banks", "unit": "%", "source": ["S4"],
            "series": [{"name": "Scheduled Commercial Banks", "data": [{"label": l, "value": v} for l, v in points]}]}


def _in_context(fn):
    return contextvars.copy_context().run(fn)


def test_writer_chart_points_are_grounded_by_value_entity_and_column_period():
    manifest, enriched = _fixture()

    def run():
        charts, meta = R._ground_writer_chart_points(
            [_gnpa_chart([("FY24", 2.7), ("FY25", 2.2), ("FY25", 2.7), ("FY23", 9.9)])], manifest, enriched)
        return charts[0]["series"][0]["data"], meta
    points, meta = _in_context(run)
    grounded = [(p["label"], p["value"]) for p in points if p.get("evidenceQuote")]
    assert grounded == [("FY24", 2.7), ("FY25", 2.2)]  # wrong column (FY25=2.7) and invented FY23 rejected
    assert all("Gross NPA ratio" in p["evidenceQuote"] for p in points if p.get("evidenceQuote"))
    assert meta["pointsGrounded"] == 2


def test_table_column_period_alignment():
    ctx = "Sr. No | Items | Amount Outstanding (At end-March) | Percentage Variation 2024 | 2025 P | 2024 | 2025 P"
    row = "1.2 Deposits | 2,17,41,578 | 2,41,47,183 | 14.0 | 11.1"
    assert R._table_column_period_ok(row, ctx, "14.0", R._chart_period_forms("FY24")) is True
    assert R._table_column_period_ok(row, ctx, "11.1", R._chart_period_forms("FY24")) is False
    assert R._table_column_period_ok("GNPA ratio | 2.7", "no header", "2.7", R._chart_period_forms("FY24")) is None


def test_grounded_chart_survives_report_gate_and_pdf_export_after_browser_roundtrip():
    manifest, enriched = _fixture()
    report = "## Asset quality\nGNPA fell [S4].\n\n[CHART_1]\n"

    def run():
        man = R.ensure_cited_source_records(report, manifest)
        charts, _ = R._ground_writer_chart_points([_gnpa_chart([("FY24", 2.7), ("FY25", 2.2)])], man, enriched)
        audited = R._attach_audit_source_evidence(man, enriched, report)
        public = R._merge_source_evidence_statuses(man, audited)
        _t, kept, _m = sanitize_charts_for_publication(report, charts, public, question="banking", require_publisher_verification=True)
        roundtrip = normalise_source_manifest(json.loads(json.dumps(public)))  # what the frontend sends back
        _t2, exported, meta = sanitize_charts_for_publication(report, kept, roundtrip, question="banking", require_publisher_verification=True)
        return kept, exported, meta
    kept, exported, meta = _in_context(run)
    assert len(kept) == 1 and len(exported) == 1 and meta["removedCharts"] == 0


def test_manifest_normalisation_keeps_chart_verification_evidence():
    entry = {"id": "S1", "title": "RBI", "url": URL, "page_fetch_status": "ok", "_ggSourceRegistryVersion": 1,
             "publisherChartEvidenceExcerpt": "| 4.3 Gross NPA ratio | 2.7 | 2.2 |", "publisher_gallery_chart_evidence_verified": True,
             "searchChartEvidenceExcerpt": "GNPA 2.7 per cent", "search_extract_chart_evidence_verified": "True"}
    out = normalise_source_manifest([entry])[0]
    assert out["publisherChartEvidenceExcerpt"].startswith("| 4.3")
    assert out["publisher_gallery_chart_evidence_verified"] is True and out["search_extract_chart_evidence_verified"] is True


def test_prompt_fragments_and_data_source_names_are_not_chart_entities():
    names = ["RBI NPCI investor-relations reports and", "private-sector 4. Net interest margins", "Reserve Bank",
             "Focus", "NPCI", "HDFC Bank", "State Bank of India"]
    assert R._clean_gallery_entities(names) == ["HDFC Bank", "State Bank of India"]


def test_exa_lead_images_become_credited_candidates():
    from routes.chat import _emit_exa_image_candidates
    out: list = []
    _emit_exa_image_candidates([{"url": "https://news.example.com/banks", "title": "Indian banks credit growth slows",
                                 "image": "https://cdn.example.com/banks.jpg"},
                                {"url": "https://x.example.com/a", "title": "No image", "image": ""}], out)
    assert out == [{"url": "https://cdn.example.com/banks.jpg", "description": "Indian banks credit growth slows",
                    "sourceUrl": "https://news.example.com/banks", "sourceTitle": "Indian banks credit growth slows",
                    "publisher": "news.example.com", "publicationDate": "", "sourceType": "Publisher page image"}]
    selected, forced = R._force_fallback_images([], R._filter_image_candidates(out, subject="Indian banking sector credit growth"))
    assert forced and selected[0]["sourceUrl"] == "https://news.example.com/banks"
    assert selected[0]["caption"].startswith("Illustrative image")


def test_image_search_query_is_short_and_topical():
    q = ("Analyze the Indian banking sector using the latest available data from the Reserve Bank of India (RBI), "
         "NPCI, bank investor-relations reports, and other credible financial sources.\n\nFocus on: ...")
    assert R._image_search_query(q) == "Indian banking sector"


def _capture(name: str) -> str | None:
    here = Path(__file__).resolve().parent
    for root in (here, here.parent):
        hits = glob.glob(str(root / "**" / name), recursive=True)
        if hits:
            return hits[0]
    return None


@pytest.mark.skipif(_capture("research_capture_20261020_162905_*.json") is None, reason="16:29 capture not present")
def test_replay_1629_brief_capture_requested_chart_types_on_topic_and_exportable():
    cap = json.load(open(_capture("research_capture_20261020_162905_*.json")))
    st = cap["stages"]
    enriched = st["sources_after_article_fetch"]["data"]["sources"]
    manifest = st["canonical_source_manifest"]["data"]["sources"]
    report = st["final_output"]["data"]["report"]
    garbage_entities = ["RBI NPCI investor-relations reports and", "private-sector 4. Net interest margins", "Reserve Bank", "Focus"]

    def run():
        text, charts, meta = R._append_evidence_chart_gallery(
            report_text=report, charts=[], question=cap["question"], enriched=enriched, source_manifest=manifest,
            report_tier="brief", entities=garbage_entities)
        man = R.ensure_cited_source_records(text, manifest)
        public = R._merge_source_evidence_statuses(man, R._attach_audit_source_evidence(man, enriched, text))
        roundtrip = normalise_source_manifest(json.loads(json.dumps(public)))
        _t, exported, gate = sanitize_charts_for_publication(text, charts, roundtrip, question=cap["question"], require_publisher_verification=True)
        return charts, exported, meta
    charts, exported, meta = _in_context(run)
    types = {c.get("type") for c in charts}
    assert {"line", "bar"} <= types and types & {"doughnut", "pie"}
    assert "Reserve Bank" not in json.dumps(charts)
    assert "Scheduled Commercial Banks" in meta["targets"]
    assert not any("Demand deposits" in str(c.get("title")) for c in charts)
    assert len(exported) == len(charts)
