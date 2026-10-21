from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from urllib.request import Request, urlopen

import pymupdf

BASE = os.environ.get("BACKEND_URL", "http://localhost:10000")
OUT = Path(os.environ.get("GG_EVIDENCE_ASSETS_QA_DIR", "backend/artifacts/evidence-assets-qa"))
QUERIES = [
    "Evaluate whether quick-commerce unit economics are structurally viable in India. Analyze order density, delivery cost, AOV, CAC, dark-store economics, business models, capital intensity and sustainability.",
    "Compare Indian private-bank credit cycles in 2008-09, 2014-18, 2020-22 and 2025-26 across funding, asset quality, regulation, capital, loan composition and primary risk.",
    "Evaluate whether generative AI is a structural threat or earnings opportunity for Indian IT services companies. Analyze growth, pricing, productivity, deal sizes, margins, hiring, investment and client spending.",
    "Assess Infosys earnings resilience over the next two years, focusing on revenue growth, margins, deal wins, AI-led services, employee productivity and management commentary.",
]
_ONLY = os.environ.get("GG_EVIDENCE_ASSETS_QA_ONLY", "")


def post(path: str, payload: dict):
    req = Request(BASE.rstrip("/") + path, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=900) as response:
        data = response.read()
        return data if response.headers.get_content_type() == "application/pdf" else json.loads(data.decode())


def render_pdf(pdf_path: Path, prefix: str):
    doc = pymupdf.open(pdf_path)
    pages = []
    for i, page in enumerate(doc, 1):
        pix = page.get_pixmap(matrix=pymupdf.Matrix(1.2, 1.2), alpha=False)
        path = OUT / f"{prefix}-page-{i:02d}.png"
        pix.save(path)
        text = page.get_text("text")
        pages.append({"page": i, "text_chars": len(text), "blank": len(text.strip()) == 0})
    return pages


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    reports = []
    selected_queries = [(int(_ONLY), QUERIES[int(_ONLY) - 1])] if _ONLY.isdigit() and 1 <= int(_ONLY) <= len(QUERIES) else list(enumerate(QUERIES, 1))
    for i, question in selected_queries:
        data = post("/api/chat/report", {"question": question, "sources": []})
        report_path = OUT / f"report-{i}.json"
        report_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        pdf_bytes = post("/api/chat/report/pdf", {**data, "question": question})
        pdf_path = OUT / f"report-{i}.pdf"
        pdf_path.write_bytes(pdf_bytes)
        pages = render_pdf(pdf_path, f"report-{i}")
        assets = data.get("evidenceAssets") or []
        used = data.get("assetUses") or []
        reports.append({
            "report": i,
            "question": question,
            "title": data.get("title"),
            "pdf": str(pdf_path),
            "pdf_sha256": hashlib.sha256(pdf_bytes).hexdigest(),
            "pages": pages,
            "blank_pages": [p["page"] for p in pages if p["blank"]],
            "asset_count": len(assets),
            "accepted_assets": [a["asset_id"] for a in assets if a.get("can_use_as_evidence")],
            "rejected_assets": [{"asset_id": a.get("asset_id"), "reason": a.get("relevance_reason"), "role": a.get("evidence_role")} for a in assets if not a.get("can_use_as_evidence")],
            "asset_types": sorted({a.get("asset_type") for a in assets}),
            "asset_source_ids": sorted({a.get("source_id") for a in assets if a.get("source_id")}),
            "used_assets": used,
            "asset_locations": [{"asset_id": u.get("asset_id"), "presentation": u.get("presentation"), "marker_present": f"[ASSET:{u.get('asset_id')}]" in data.get("report", "")} for u in used],
            "claim_citations": data.get("researchScope", {}).get("claims", {}).get("claims_checked", 0),
            "unsupported_claim_count": data.get("researchScope", {}).get("claims", {}).get("unsupported_count", 0),
            "prior_conversation_source": any("prior conversation" in json.dumps(a).lower() for a in assets),
            "visual_system_preserved": bool(data.get("presentation") or data.get("theme")),
        })
    summary = {"reports": reports, "zero_blank_pages": all(not r["blank_pages"] for r in reports), "scope_validation_preserved": all(r["unsupported_claim_count"] == 0 for r in reports)}
    (OUT / "evidence-assets-summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    (OUT / "evidence-assets.json").write_text(json.dumps({"reports": [{"report": r["report"], "assets": json.loads((OUT / f"report-{r['report']}.json").read_text(encoding="utf-8")).get("evidenceAssets", [])} for r in reports]}, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
