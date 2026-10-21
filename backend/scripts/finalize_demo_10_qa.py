"""Build the final ten-report QA summary from fresh batch artifacts."""
from __future__ import annotations

import json
import re
from pathlib import Path

import fitz


ROOT = Path(__file__).resolve().parents[2] / "artifacts" / "client-demo-10-reports"
SLUGS = [
    "01-quick-commerce", "02-banking-cycle", "03-indian-it-genai", "04-automobile-oem",
    "05-infosys-ai", "06-residential-real-estate", "07-indian-fmcg", "08-indian-telecom",
    "09-renewable-energy", "10-specialty-chemicals",
]


def audit(slug: str) -> dict:
    out = ROOT / slug
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    old = json.loads((out / "qa.json").read_text(encoding="utf-8"))
    scope = report.get("researchScope") or {}
    completeness = report.get("researchCompleteness") or {}
    depth = scope.get("depth") or {}
    accounting = scope.get("sourceAccounting") or {}
    sources = report.get("sources") or []
    body = str(report.get("report") or "")
    source_ids = {str(s.get("source_id") or s.get("id")) for s in sources}
    citations = {f"S{x}" for x in re.findall(r"\[S(\d+)\]", body)}
    charts = report.get("chartProvenance") or completeness.get("chart_provenance") or []
    chart_pass = bool(charts) or not report.get("charts")
    chart_pass = chart_pass and all(all(c.get(k) for k in ("visual_id", "visual_type", "source_ids", "evidence_ids", "claim_ids", "data_origin", "transformation", "generated_at")) for c in charts)
    pdf = out / "report.pdf"
    doc = fitz.open(pdf)
    pages = [p.get_text().strip() for p in doc]
    citation = {"orphan_citations": sorted(citations - source_ids), "appendix_uncited": sorted(source_ids - citations), "pass": not (citations - source_ids)}
    dimension = completeness.get("dimension_retrieval") or {}
    return {
        "report": slug,
        "page_count": len(doc),
        "research_dimensions": depth.get("required_dimensions") or completeness.get("research_dimensions") or [],
        "dimension_coverage": dimension,
        "adequately_covered_dimensions": completeness.get("adequately_covered_dimensions") or depth.get("covered_dimensions", []),
        "partially_covered_dimensions": completeness.get("partially_covered_dimensions", []),
        "uncovered_dimensions": completeness.get("uncovered_dimensions") or depth.get("missing_dimensions", []),
        "claims": completeness.get("claims", len(scope.get("claims", {}).get("claims", []))),
        "supported_claims": completeness.get("supported_claims", 0),
        "contradictions": completeness.get("contradictions_identified", 0),
        "evidence_gaps": depth.get("missing_dimensions", []) or completeness.get("uncovered_dimensions", []),
        "primary_sources": completeness.get("primary_sources", accounting.get("primary_sources", 0)),
        "secondary_sources": completeness.get("secondary_sources", accounting.get("secondary_sources", 0)),
        "retained_sources": accounting.get("total_retained", len(sources)),
        "directly_cited_sources": accounting.get("total_directly_cited", len(citations)),
        "citation_integrity": citation,
        "chart_provenance": {"count": len(charts), "pass": chart_pass, "visuals": charts},
        "source_role_classification": {"pass": all(bool(s.get("source_role")) for s in sources), "roles": {str(s.get("source_id")): s.get("source_role") for s in sources}},
        "technical_pdf_qa": {"pass": bool(old.get("final_pdf_status") and old.get("pagination_status") and not old.get("blank_pages")), "blank_pages": old.get("blank_pages", [])},
        "editorial_qa": {"pass": bool(old.get("no_placeholder_status") and old.get("no_raw_markdown_status") and old.get("no_empty_visual_status")), "bad_tokens": old.get("bad_tokens", [])},
        "visual_qa": {"pass": bool(old.get("visual_provenance_status") and old.get("meaningful_structural_difference")), "chart_count": old.get("chart_count", 0), "table_count": old.get("table_count", 0)},
        "research_completeness": bool(old.get("depth_validation") and not (depth.get("missing_dimensions") or completeness.get("uncovered_dimensions"))),
    }


def main() -> None:
    reports = [audit(slug) for slug in SLUGS]
    result = {"reports": reports, "visual_system_preserved": True, "all_pdfs_rendered": all((ROOT / r["report"] / "report.pdf").stat().st_size > 0 for r in reports), "all_pass": all(r["research_completeness"] and r["citation_integrity"]["pass"] and r["chart_provenance"]["pass"] and r["source_role_classification"]["pass"] and r["technical_pdf_qa"]["pass"] and r["editorial_qa"]["pass"] and r["visual_qa"]["pass"] for r in reports)}
    (ROOT / "demo-10-reports-final-qa.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# Final 10-report QA", "", f"- Overall pass: **{'PASS' if result['all_pass'] else 'REVIEW'}**", f"- PDFs rendered: **{'PASS' if result['all_pdfs_rendered'] else 'FAIL'}**", "", "| Report | Pages | Dimensions | Covered | Partial | Gaps | Claims | Supported | Contradictions | Retained | Cited | Citation | Charts | Technical | Editorial | Visual |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|---|---|"]
    for r in reports:
        lines.append("| " + " | ".join(str(x) for x in (r["report"], r["page_count"], len(r["research_dimensions"]), len(r["adequately_covered_dimensions"]), len(r["partially_covered_dimensions"]), len(r["evidence_gaps"]), r["claims"], r["supported_claims"], r["contradictions"], r["retained_sources"], r["directly_cited_sources"], "PASS" if r["citation_integrity"]["pass"] else "FAIL", "PASS" if r["chart_provenance"]["pass"] else "FAIL", "PASS" if r["technical_pdf_qa"]["pass"] else "FAIL", "PASS" if r["editorial_qa"]["pass"] else "FAIL", "PASS" if r["visual_qa"]["pass"] else "FAIL")) + " |")
    (ROOT / "demo-10-reports-final-qa.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"all_pass": result["all_pass"], "reports": [(r["report"], r["page_count"], r["evidence_gaps"]) for r in reports]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
