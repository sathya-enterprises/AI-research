"""Render and reconcile the archived live telecom research run."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import fitz

from routes.pdf import build_pdf
from utils import research_store


def main() -> None:
    base = Path(__file__).resolve().parents[2] / "artifacts" / "telecom-live"
    payload = json.loads((base / "report-response.json").read_text(encoding="utf-8"))
    report = str(payload.get("report") or "")
    sources = payload.get("sources") or []
    source_ids = {str(s.get("source_id") or s.get("id")) for s in sources}
    citations = {f"S{x}" for x in re.findall(r"\[S(\d+)\]", report)}
    orphan_citations = sorted(citations - source_ids)
    appendix_orphans = sorted(source_ids - citations)
    pdf = build_pdf(
        report, payload.get("title") or "Indian Telecom Economics", payload.get("question") or "", payload.get("summary") or "",
        payload.get("keyStats") or [], payload.get("charts") or [], payload.get("images") or [], payload.get("theme") or {},
        sources, payload.get("presentation"), payload.get("evidenceAssets") or [], payload.get("assetUses") or [],
    )
    pdf_path = base / "telecom_report.pdf"
    pdf_path.write_bytes(pdf)
    (base / "report.pdf").write_bytes(pdf)  # legacy archive compatibility
    doc = fitz.open(pdf_path)
    page_text = [page.get_text() for page in doc]
    for i, page in enumerate(doc, 1):
        pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
        pix.save(str(base / f"page-{i:02d}.png"))
    store_snapshot = research_store.snapshot(str(payload.get("research_run_id")))
    qa = {
        "research_run_id": payload.get("research_run_id"),
        "citation_integrity": {"body_citations": sorted(citations), "source_ids": sorted(source_ids), "orphan_citations": orphan_citations, "appendix_orphans": appendix_orphans, "pass": not orphan_citations},
        "source_count": len(sources),
        "report_characters": len(report),
        "pdf_page_count": len(doc),
        "blank_pages": [i + 1 for i, text in enumerate(page_text) if len(text.strip()) < 80],
        "raw_markdown": bool(re.search(r"```|^#{1,6} ", "\n".join(page_text), re.M)),
        "store_snapshot": store_snapshot,
        "technical_pass": not any(len(text.strip()) < 80 for text in page_text) and not re.search(r"```|^#{1,6} ", "\n".join(page_text), re.M),
        "dimension_coverage": (payload.get("researchCompleteness") or {}).get("dimension_retrieval", {}),
        "chart_provenance": payload.get("chartProvenance") or [],
        "chart_provenance_pass": all(bool(c.get("source_ids") and c.get("evidence_ids") and c.get("visual_id")) for c in (payload.get("chartProvenance") or [])),
        "source_role_classification_pass": all(bool(s.get("source_role")) for s in sources),
    }
    telemetry_path = base / "research-telemetry.json"
    telemetry = json.loads(telemetry_path.read_text(encoding="utf-8"))
    telemetry["report_page_count"] = len(doc)
    telemetry["citation_integrity_final"] = qa["citation_integrity"]
    telemetry["pdf_qa"] = qa
    telemetry_path.write_text(json.dumps(telemetry, ensure_ascii=False, indent=2), encoding="utf-8")
    (base / "telecom_research_telemetry.json").write_text(json.dumps(telemetry, ensure_ascii=False, indent=2), encoding="utf-8")
    (base / "qa.json").write_text(json.dumps(qa, ensure_ascii=False, indent=2), encoding="utf-8")
    dimension_rows = (qa["dimension_coverage"] or {}).get("dimensions", [])
    md = ["# Telecom research QA", "", f"- Research run: `{payload.get('research_run_id')}`", f"- PDF pages: {len(doc)}", f"- Body citation reconciliation: {'PASS' if not orphan_citations else 'FAIL'}", f"- Appendix reconciliation: {'PASS' if not appendix_orphans else 'REVIEW'}", f"- Technical PDF QA: {'PASS' if qa['technical_pass'] else 'FAIL'}", f"- Chart provenance: {'PASS' if qa['chart_provenance_pass'] else 'FAIL'}", "", "## Dimension coverage", "", "| Dimension | Candidates | Retrieved chunks | Unique chunks | Evidence | Primary | Secondary | Claims | Contradictions | Saturation | Coverage |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
    for row in dimension_rows:
        md.append("| " + " | ".join(str(row.get(k, "")) for k in ("dimension", "candidate_sources", "retrieved_chunks", "unique_chunks", "supporting_evidence", "primary_sources", "secondary_sources", "claims", "contradictions", "saturation", "coverage")) + " |")
    (base / "telecom_research_qa.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(json.dumps({"research_run_id": payload.get("research_run_id"), "pages": len(doc), "sources": len(sources), "orphan_citations": orphan_citations, "store": store_snapshot, "technical_pass": qa["technical_pass"], "chart_provenance_pass": qa["chart_provenance_pass"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
