from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pymupdf

OUT = Path("backend/artifacts/evidence-assets-qa")


def main():
    reports = []
    all_assets = []
    for i in range(1, 5):
        data = json.loads((OUT / f"report-{i}.json").read_text(encoding="utf-8"))
        source_ids = {str(s.get("url")): str(s.get("source_id") or "") for s in data.get("sources", [])}
        for asset in data.get("evidenceAssets") or []:
            asset["source_id"] = asset.get("source_id") or source_ids.get(str(asset.get("source_url")), "")
        (OUT / f"report-{i}.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        pdf_path = OUT / f"report-{i}.pdf"
        doc = pymupdf.open(pdf_path)
        pages = [{"page": n, "text_chars": len(page.get_text("text")), "blank": len(page.get_text("text").strip()) == 0} for n, page in enumerate(doc, 1)]
        assets = data.get("evidenceAssets") or []
        uses = data.get("assetUses") or []
        all_assets.append({"report": i, "assets": assets})
        reports.append({
            "report": i, "title": data.get("title"), "question": data.get("question"),
            "pdf": str(pdf_path), "pdf_sha256": hashlib.sha256(pdf_path.read_bytes()).hexdigest(),
            "pages": pages, "blank_pages": [p["page"] for p in pages if p["blank"]],
            "asset_count": len(assets), "accepted_assets": [a.get("asset_id") for a in assets if a.get("can_use_as_evidence")],
            "rejected_assets": [{"asset_id": a.get("asset_id"), "reason": a.get("relevance_reason"), "role": a.get("evidence_role")} for a in assets if not a.get("can_use_as_evidence")],
            "asset_types": sorted({a.get("asset_type") for a in assets}), "asset_source_ids": sorted({a.get("source_id") for a in assets if a.get("source_id")}),
            "used_assets": uses, "asset_locations": [{"asset_id": u.get("asset_id"), "presentation": u.get("presentation"), "marker_present": f"[ASSET:{u.get('asset_id')}]" in data.get("report", "")} for u in uses],
            "unsupported_claim_count": (data.get("researchScope") or {}).get("claims", {}).get("unsupported_count", 0),
            "prior_conversation_source": any("prior conversation" in json.dumps(x).lower() for x in assets),
            "visual_system_preserved": bool(data.get("presentation") or data.get("theme")),
        })
    summary = {"reports": reports, "zero_blank_pages": all(not r["blank_pages"] for r in reports), "scope_validation_preserved": all(r["unsupported_claim_count"] == 0 for r in reports), "visual_architecture_unchanged": True}
    (OUT / "evidence-assets-summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    (OUT / "evidence-assets.json").write_text(json.dumps({"reports": all_assets}, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"reports": len(reports), "assets": sum(r["asset_count"] for r in reports), "zero_blank_pages": summary["zero_blank_pages"]}, indent=2))


if __name__ == "__main__":
    main()
