"""Regression tests for the publication-validation / chart-loss fixes (v90).

Covers the failure seen in the 2026-10-20 Brief banking capture: RBI writes
"per cent", reports write "%"; identifier labels became numeric claims; the
extractor missed RBI prose/table data; generated tables failed their own gate;
publication failures discarded the generated report.
"""
from __future__ import annotations

import asyncio
import glob
import json
import os
from pathlib import Path

import pytest

import routes.report as R
from utils.chart_evidence_audit import audit_chart_evidence_consistency
from utils.evidence_dataset import build_evidence_dataset

BANK_TARGETS = ["Scheduled Commercial Banks", "Public Sector Banks", "Private Sector Banks"]


def _audit(report: str, page: str, url: str = "https://rbi.org.in/a", sid: str = "S1") -> dict:
    manifest = [{"id": sid, "url": url, "title": "RBI release"}]
    enriched = [{"url": url, "title": "RBI release", "pageContent": page, "pageFetchStatus": "ok"}]
    rows = R._attach_audit_source_evidence(manifest, enriched, report)
    return R._source_evidence_quality_audit(rows)


# ── 1. numeric claim matching ───────────────────────────────────────────────

def test_percent_sign_claim_matches_rbi_per_cent_source():
    audit = _audit("The GNPA ratio of SCBs fell to 2.7% at end-March 2024 [S1].",
                   "The gross non-performing assets (GNPA) ratio of SCBs fell to 2.7 per cent at end-March 2024.")
    assert audit["blocking"] is False
    assert audit["mismatchedCitationGroups"] == []


def test_per_cent_claim_matches_flattened_rbi_table_row_with_per_cent_label():
    page = "4.5 Provision Coverage Ratio (Per cent) * | 76.2 | 76.3\n4.6 Slippage ratio (Per cent) * | 1.5 | 1.4"
    audit = _audit("| **Provision Coverage Ratio (PCR)** | 76.2% [S1] | 76.3% [S1] |", page)
    assert audit["blocking"] is False


def test_percentage_variation_header_makes_bare_growth_cells_percent():
    page = ("Sr. No | Items | Amount Outstanding (At end-March) | Percentage Variation\n"
            "2024 | 2025 P | 2024 | 2025 P\n"
            "6.1 Total number of credit cards (in lakhs) | 1,018 | 1,099 | 19.3 | 7.9")
    audit = _audit("Credit cards in circulation rose by 19.3 per cent to 1,018 lakhs [S1].", page)
    assert audit["blocking"] is False


def test_identifier_and_horizon_labels_are_not_numeric_claims():
    audit = _audit(
        "Banks must keep complaint addressal strong in tier-2 and tier-3 cities over the 12-month outlook "
        "during 2023-24 and 2024-25 [S1].",
        "Complaint addressal remained a priority for banks in semi-urban areas.",
    )
    assert audit["mismatchedCitationGroups"] == []


def test_lower_precision_claim_matches_rounded_source_but_single_digit_does_not():
    ok = _audit("PCR levels stayed above 76 per cent [S1].", "The provision coverage ratio was 76.2 per cent.")
    assert ok["blocking"] is False
    bad = _audit("The GNPA ratio was 2 per cent [S1].", "The GNPA ratio was 2.4 per cent.")
    assert bad["blocking"] is True


def test_value_present_only_for_a_different_metric_is_rejected_with_reason():
    page = "2.3 Return on Equity (RoE) (Per cent) * | 13.6 | 13.5\n4.3 Gross NPA ratio (Per cent) | 2.7 | 2.2"
    audit = _audit("The gross NPA ratio was 13.5 per cent [S1].", page)
    assert audit["blocking"] is True
    group = audit["mismatchedCitationGroups"][0]
    assert group["rejectionReason"] == "value_found_but_source_sentence_describes_a_different_metric_or_entity"


def test_generated_chart_captions_are_not_claims():
    audit = _audit("*Advances growth (YoY) over 2 reported periods (FY24 to FY25) for 1 entity. [S1]",
                   "Loans and advances grew 11.5 per cent in 2024-25.")
    assert audit["mismatchedCitationGroups"] == []


# ── 2. chart point audit ────────────────────────────────────────────────────

def test_chart_percent_unit_is_supported_by_per_cent_evidence():
    chart = {"type": "bar", "title": "Gross NPA ratio", "unit": "%", "source": ["S1"],
             "series": [{"name": "Gross NPA ratio", "data": [{"label": "FY25", "value": 2.2}]}]}
    sources = [{"id": "S1", "pageFetchStatus": "ok",
                "evidenceExcerpt": "The gross NPA ratio declined to 2.2 per cent in FY25."}]
    audit = audit_chart_evidence_consistency("[CHART_1]", [chart], sources)
    assert audit["chartEvidenceFailures"] == []


def test_neighbouring_cell_value_is_not_plotted_under_the_wrong_entity():
    chart = {"type": "bar", "title": "Gross NPA ratio by bank group", "unit": "%", "source": ["S1"],
             "series": [{"name": "Gross NPA ratio", "data": [
                 {"label": "Public Sector Banks", "value": 3.1},
                 {"label": "Private Sector Banks", "value": 1.9},
             ]}]}
    evidence = ("The gross NPA ratio of public sector banks was 3.1 per cent. "
                "Private sector banks expanded branches strongly over the year. "
                "Foreign banks reported a gross NPA ratio of 1.9 per cent.")
    audit = audit_chart_evidence_consistency("[CHART_1]", [chart], [{"id": "S1", "pageFetchStatus": "ok", "evidenceExcerpt": evidence}])
    failed = {(f.get("label"), f.get("value")) for f in audit["chartEvidenceFailures"]}
    assert ("Private Sector Banks", "1.9") in failed
    assert ("Public Sector Banks", "3.1") not in failed
    assert any("same sentence or table row" in f["reason"] for f in audit["chartEvidenceFailures"])


# ── 3. evidence extraction ──────────────────────────────────────────────────

def _observations(text: str, question: str = "Indian banking sector gross npa credit growth"):
    ds = build_evidence_dataset([{"id": "S1", "title": "RBI", "url": "https://rbi.org.in/x", "text": text,
                                  "origin": "publisher_page"}], question, targets=BANK_TARGETS)
    return sorted((o.metric, o.entity_key, o.period, o.value) for o in ds.observations)


def test_rbi_end_month_periods_and_follow_on_values_are_extracted():
    obs = _observations("SCBs SCBs SCBs.\n- Asset quality strengthened, with the gross non-performing assets (GNPA) ratio "
                        "declining to 2.2 per cent at end-March 2025 and 2.1 per cent at end-September 2025.")
    assert ("gross_npa_ratio", "scheduled commercial banks", "FY25", 2.2) in obs
    assert ("gross_npa_ratio", "scheduled commercial banks", "Sep 2025", 2.1) in obs


def test_crar_from_to_further_chain_uses_each_values_own_period():
    obs = _observations("Capital to risk weighted assets (CRAR) ratio of SCBs strengthened from 14.3 per cent at "
                        "end-March 2019 to 14.7 per cent at end-March 2020 and further to 15.8 per cent at end-September 2020.")
    assert [o[2:] for o in obs if o[0] == "car"] == [("FY19", 14.3), ("FY20", 14.7), ("Sep 2020", 15.8)]


def test_fiscal_range_suffix_is_not_a_value_and_on_top_of_comparison_is_kept():
    obs = _observations("SCBs SCBs SCBs.\nBuoyant bank credit, which increased by 16.0 per cent in 2023-24 "
                        "(excluding the impact of the merger) on top of 17.0 per cent in 2022-23.")
    assert ("credit_growth", "scheduled commercial banks", "FY24", 16.0) in obs
    assert ("credit_growth", "scheduled commercial banks", "FY23", 17.0) in obs


def test_metric_acronym_does_not_split_window_and_governed_entity_wins():
    obs = _observations("The provisioning coverage ratio (PCR) of SCBs improved further to 77.0 per cent in "
                        "September 2024, largely due to provisioning by PSBs.")
    assert obs == [("provision_coverage", "scheduled commercial banks", "Sep 2024", 77.0)]


def test_nbfc_and_segment_sentences_do_not_inherit_bank_group():
    text = ("The balance sheet of SCBs expanded. SCBs remained well capitalised. SCBs reported profits.\n"
            "The consolidated balance sheet of NBFCs expanded, with GNPA ratio at 3.8 per cent at end-March 2023.\n"
            "The asset quality of large borrower portfolios improved, with the GNPA ratio falling from 4.5 per cent "
            "in March 2023 to 2.4 per cent in September 2024.")
    assert [o for o in _observations(text) if o[0] == "gross_npa_ratio"] == []


def test_rbi_key_indicator_table_is_decoded_with_units_signs_and_submeasure_guards():
    page = ("Sr. No | Items | Amount Outstanding (At end-March) | Percentage Variation\n"
            "2024 | 2025 P | 2024 | 2025 P\n"
            "1 | Balance Sheet Operations#\n"
            "1.2 Deposits | 2,17,41,578 | 2,41,47,183 | -3.5 | 11.1\n"
            "2 | Profitability#\n"
            "2.2 Return on Asset (RoA) (Per cent) * | 1.3 | 1.4\n"
            "3.1 Capital to risk weighted assets ratio (CRAR) @ | 16.9 | 17.4\n"
            "3.3 CRAR (tier I) (Per cent) @ | 14.8 | 15.5\n"
            "4.1 Gross NPAs | 4,80,818 | 4,31,634 | -15.9 | -10.2\n"
            "4.3 Gross NPA ratio (Gross NPAs as percentage of gross advances)** | 2.7 | 2.2\n"
            "5.5 Personal loans | 53,46,691 | 59,71,696 | 27.8 | 11.7")
    ds = build_evidence_dataset([
        {"id": "S4", "title": "RBI", "url": "u", "text": page, "origin": "publisher_page"},
        {"id": "S4", "title": "RBI", "url": "u", "text": "Indicators of SCBs. SCBs data.", "origin": "search_extract"},
    ], "Indian banking sector", targets=BANK_TARGETS)
    got = sorted({(o.metric, o.period, o.value) for o in ds.observations if o.origin == "table"})
    assert ("deposits_growth", "FY24", -3.5) in got
    assert ("roa", "FY25", 1.4) in got
    assert ("car", "FY25", 17.4) in got and ("car", "FY25", 15.5) not in got
    assert ("gross_npa_ratio", "FY24", 2.7) in got
    assert not any(m == "gross_npa_ratio" and abs(v) > 10 for m, _p, v in got)
    assert not any(v in {27.8, 11.7} for _m, _p, v in got)


def test_indicator_table_without_a_stated_entity_is_not_guessed():
    page = ("Sr. No | Items | Amount Outstanding (At end-March) | Percentage Variation\n"
            "2024 | 2025 P | 2024 | 2025 P\n2.2 Return on Asset (RoA) (Per cent) * | 1.3 | 1.4")
    ds = build_evidence_dataset([{"id": "S4", "title": "Publications", "url": "u", "text": page,
                                  "origin": "publisher_page"}], "Indian banking sector", targets=BANK_TARGETS)
    assert not [o for o in ds.observations if o.metric == "roa"]


def test_gallery_requested_metric_keys_do_not_double_the_growth_suffix():
    from utils.chart_gallery import _Builder
    ds = build_evidence_dataset([], "x", targets=BANK_TARGETS)
    keys = _Builder(ds, "Banking-sector credit growth and deposit growth. Gross and net NPA trends.")._requested_metric_keys()
    assert "credit_growth" in keys and "advances_growth" in keys and "deposits_growth" in keys
    assert "gross_npa_ratio" in keys and not any(k.endswith("_growth_growth") for k in keys)


# ── 4. self-checked evidence table, chart notes, repair safety ─────────────

def test_inserted_evidence_rows_that_fail_the_audit_are_dropped_individually():
    before = "## Data and Evidence\n\nText [S1].\n"
    after = (before + "\n## Source-Extracted Quantitative Evidence\n\nThe table below surfaces 3 distinct numeric observations extracted.\n\n"
             "| Entity / subject | Measure | Reported value | Period | Evidence type | Source |\n|---|---|---:|---|---|---|\n"
             "| Scheduled Commercial Banks | Gross NPA ratio | 2.7 % | FY24 | Publisher page | [S1] |\n"
             "| Scheduled Commercial Banks | Return on assets | 1.4 % | FY25 | Publisher page | [S1] |\n"
             "| Solar Power | Capacity | 17,779.35 GW | FY27 | Publisher page | [S1] |\n")
    manifest = [{"id": "S1", "url": "https://rbi.org.in/a", "title": "RBI"}]
    enriched = [{"url": "https://rbi.org.in/a", "title": "RBI", "pageFetchStatus": "ok",
                 "pageContent": "The GNPA ratio of SCBs was 2.7 per cent in 2023-24. The return on assets (RoA) of SCBs was 1.4 per cent in 2024-25."}]
    text, kept, rejected = R._verify_inserted_observation_rows(before, after, manifest, enriched)
    assert kept == 2 and len(rejected) == 1
    assert "17,779.35" not in text and "surfaces 2 distinct" in text


def test_chart_omission_notes_are_removed_moved_or_made_specific():
    charts = [{"type": "line", "title": "Gross NPA ratio trend", "series": [{"name": "SCBs", "data": [{"label": "FY24", "value": 2.7}]}]},
              {"type": "bar", "title": "Deposit levels", "series": [{"name": "Deposits", "data": [{"label": "PSBs", "value": 1}]}]}]
    text = ("## Gross and Net NPA Trends\nText.\n\n> **Chart not included:** no validated structured chart matched “NPA”; no chart is shown.\n\n"
            "## Liquidity\nText.\n\n> **Chart not included:** no validated structured chart matched “LCR”; no chart is shown.\n\n"
            "## Data and Charts\n[CHART_1]\n\n[CHART_2]\n\n"
            "> **Chart not included:** the request called for a chart, but no validated structured chart was available.\n")
    out, meta = R._resolve_chart_omission_notes(text, charts)
    assert "Chart not included" not in out
    npa_section = out.split("## Liquidity")[0]
    assert "[CHART_1]" in npa_section
    assert "**Data limitation:**" in out and meta["moved"] == 1


def test_repair_that_drops_a_chart_placeholder_reattaches_it_to_its_section():
    chart = {"type": "line", "title": "Credit growth", "series": [{"name": "SCBs", "data": [{"label": "FY24", "value": 16}]}]}
    pre = "## Credit Growth\nCredit rose [S1].\n\n[CHART_1]\n\n## Outlook\nStable."
    post = "## Credit Growth\nCredit rose sharply [S1].\n\n## Outlook\nStable."
    text, charts, reattached, lost = R._reattach_charts_lost_in_repair(pre, [chart], post, [])
    assert charts == [chart] and len(reattached) == 1 and not lost
    assert text.index("[CHART_1]") < text.index("## Outlook")
    _t, _c, _r, lost2 = R._reattach_charts_lost_in_repair(pre, [chart], "## Outlook\nStable.", [])
    assert lost2 and "removed the section" in lost2[0]["reason"]


def test_chart_pipeline_log_records_counts_and_reasons():
    log = R._ChartPipelineLog()
    a = {"type": "bar", "title": "A", "series": [{"name": "x", "data": [{"label": "p", "value": 1}]}]}
    b = {"type": "bar", "title": "B", "series": [{"name": "y", "data": [{"label": "q", "value": 2}]}]}
    log.record("writer_candidates", [a, b])
    log.record("evidence_validated", [a], removed=R._removed_chart_reasons([a, b], [a], [{"chartIndex": 2, "reason": "no source"}]))
    out = log.as_dict()
    assert out["finalCount"] == 1 and out["stages"][1]["removed"][0] == {"title": "B", "index": 2, "reason": "no source", "allReasons": ["no source"]}


# ── 5. publication outcomes, jobs, draft export ────────────────────────────

def test_publication_outcome_classification():
    body = "## Report\n" + "Evidence-backed analysis. " * 40
    failing = {"publicationAudit": {"flags": []},
               "sourceEvidenceAudit": {"blocking": True, "mismatchedCitationGroups": [
                   {"claim": "GNPA 9.9%", "sourceIds": ["S1"], "unmatchedNumericClaims": ["percent:9.9"]}]}}
    draft = R._classify_publication_outcome(failing, body)
    assert draft["outcome"] == "draft" and draft["failedStage"] == "source_evidence"
    assert draft["failureSummary"][0]["sources"] == ["S1"]
    assert R._classify_publication_outcome(failing, "")["outcome"] == "structural_failure"
    assert R._classify_publication_outcome({"publicationAudit": {}, "sourceEvidenceAudit": {}}, body)["outcome"] == "published"


def _run_job_with(monkeypatch, status_code: int, payload: dict) -> dict:
    from fastapi.responses import JSONResponse

    async def fake_generate(_request):
        return JSONResponse(payload, status_code=status_code)

    monkeypatch.setattr(R, "generate_report", fake_generate)
    job_id = "job-" + str(status_code) + "-" + str(len(R._REPORT_JOBS))
    R._REPORT_JOBS[job_id] = {"status": "queued", "createdAt": 0, "reportTier": "brief"}
    asyncio.run(R._run_report_job_async(job_id, {}))
    return R._REPORT_JOBS[job_id]


def test_report_job_keeps_a_422_draft_instead_of_discarding_it(monkeypatch):
    job = _run_job_with(monkeypatch, 422, {"report": "## Draft\nBody", "publicationOutcome": "draft",
                                           "failedStage": "source_evidence", "error": "draft"})
    assert job["status"] == "done" and job["outcome"] == "draft"
    assert job["result"]["report"].startswith("## Draft")


def test_report_job_structural_failure_reports_failed_stage(monkeypatch):
    job = _run_job_with(monkeypatch, 422, {"report": "", "publicationOutcome": "structural_failure",
                                           "failedStage": "report_structure", "error": "unusable"})
    assert job["status"] == "failed" and job["failedStage"] == "report_structure"


def _post(body):
    from starlette.requests import Request
    raw = json.dumps(body).encode()

    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}
    return Request({"type": "http", "method": "POST", "path": "/", "headers": []}, receive)


def test_duplicate_report_start_reuses_the_running_job(monkeypatch):
    submitted = []
    monkeypatch.setattr(R, "_submit_report_job", lambda job_id, body: submitted.append(job_id))
    monkeypatch.setattr(R, "_REPORT_JOBS", {})
    body = {"reportTier": "brief", "question": "q", "clientRequestId": "msg-1:brief:1:7"}
    first = json.loads(asyncio.run(R.start_report_job(_post(body))).body)
    second_response = asyncio.run(R.start_report_job(_post(body)))
    second = json.loads(second_response.body)
    assert second["jobId"] == first["jobId"] and second.get("reused") is True
    assert len(submitted) == 1


def test_draft_banner_appears_once():
    from routes.pdf import _apply_single_draft_banner
    text = _apply_single_draft_banner("> **Draft — not approved for publication.**\n\n## A\nBody\n\n> **DRAFT — NOT APPROVED FOR EXTERNAL PUBLICATION.** old")
    assert text.count("NOT APPROVED FOR EXTERNAL PUBLICATION") == 1


# ── 6. replay of the real failing capture (skipped if fixture absent) ──────

def _capture_path() -> str | None:
    here = Path(__file__).resolve().parent
    for pattern in ("research_capture_20261020_150215_*.json", "**/research_capture_20261020_150215_*.json"):
        for root in (here, here.parent):
            hits = glob.glob(str(root / pattern), recursive=True)
            if hits:
                return hits[0]
    return None


@pytest.mark.skipif(_capture_path() is None, reason="2026-10-20 15:02 research capture not present")
def test_replay_2026_10_20_brief_banking_capture_now_passes_source_evidence_with_charts():
    cap = json.load(open(_capture_path()))
    st = cap["stages"]
    enriched = st["sources_after_article_fetch"]["data"]["sources"]
    manifest = st["canonical_source_manifest"]["data"]["sources"]
    report = st["final_output"]["data"]["report"]
    import contextvars

    def run():
        # Each production report job runs in its own context; isolate the
        # gallery's context variables so they cannot leak into other tests.
        text, charts, _meta = R._append_evidence_chart_gallery(
            report_text=report, charts=[], question=cap["question"], enriched=enriched,
            source_manifest=manifest, report_tier=cap.get("reportTier", "brief"),
        )
        manifest2 = R.ensure_cited_source_records(text, manifest)
        return charts, R._source_evidence_quality_audit(R._attach_audit_source_evidence(manifest2, enriched, text))

    charts, evidence = contextvars.copy_context().run(run)
    assert evidence["blocking"] is False
    assert len(charts) >= 3
    assert any(str(c.get("type")) == "line" for c in charts)


def test_counts_in_lakhs_are_not_treated_as_rupee_amounts():
    page = "6.1 Total number of credit cards (in lakhs) | 1,018 | 1,099 | 19.3 | 7.9"
    assert _audit("Credit cards reached 1,099 lakh units at end-March 2025 [S1].", page)["blocking"] is False
    assert _audit("Deposits rose to Rs 1,099 lakh crore [S1].", page)["blocking"] is True
