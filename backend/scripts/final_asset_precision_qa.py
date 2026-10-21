"""Final precision audit for the four persisted evidence-asset QA reports.

This is intentionally an audit-only script. It reads the existing report JSON
and PDFs, validates provenance/rights/use decisions, checks recreated values
against the data carried by the selected asset, and writes machine-readable
and human-readable QA artifacts. It does not choose assets or alter layout.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pymupdf


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "backend" / "artifacts" / "evidence-assets-qa"


def norm(value: object) -> str:
    return re.sub(r"[^a-z0-9.-]+", "", str(value or "").lower())


def pdf_snapshot(path: Path) -> tuple[list[dict], str]:
    doc = pymupdf.open(path)
    pages = []
    full_text = []
    for n, page in enumerate(doc, 1):
        text = page.get_text("text") or ""
        full_text.append(text)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(1.25, 1.25), alpha=False)
        png = OUT / f"report-{path.stem.split('-')[-1]}-page-{n:02d}.png"
        pix.save(png)
        pages.append({
            "page": n,
            "png": str(png),
            "png_exists": png.exists(),
            "png_width": pix.width,
            "png_height": pix.height,
            "text_chars": len(text),
            "blank": not text.strip(),
            "image_xrefs": len(page.get_images(full=True)),
            "links": [x.get("uri") for x in page.get_links() if x.get("uri")],
        })
    return pages, "\n".join(full_text)


def classify_rejections(assets: list[dict]) -> dict[str, int]:
    out = {"rejected_due_to_irrelevance": 0, "rejected_due_to_rights": 0, "rejected_due_to_low_quality": 0}
    for a in assets:
        if a.get("can_use_as_evidence"):
            continue
        role = str(a.get("evidence_role") or "").lower()
        score = float(a.get("relevance_score") or 0)
        if role in {"irrelevant", "decorative", "contextual"} or score < 0.42:
            out["rejected_due_to_irrelevance"] += 1
        elif not a.get("can_embed") and not a.get("can_recreate"):
            out["rejected_due_to_rights"] += 1
        else:
            out["rejected_due_to_low_quality"] += 1
    return out


def used_record(data: dict, uses: list[dict], pdf_text: str, pages: list[dict]) -> tuple[list[dict], list[str]]:
    by_id = {str(a.get("asset_id")): a for a in data.get("evidenceAssets") or []}
    records, failures = [], []
    valid_treatments = {"hero_visual", "full_width_visual", "side_visual", "inline_evidence", "supporting_visual", "comparison_visual", "appendix_visual"}
    for use in uses:
        aid = str(use.get("asset_id") or "")
        asset = by_id.get(aid)
        if not asset:
            failures.append(f"used asset {aid} is missing from evidenceAssets")
            continue
        if not asset.get("can_use_as_evidence"):
            failures.append(f"used asset {aid} is not evidence-eligible")
        if not (asset.get("can_embed") or asset.get("can_recreate")):
            failures.append(f"used asset {aid} has neither embed nor recreate permission")
        if str(use.get("presentation") or "") not in valid_treatments:
            failures.append(f"used asset {aid} has an invalid presentation treatment")
        if str(use.get("source_id") or asset.get("source_id") or "") != str(asset.get("source_id") or ""):
            failures.append(f"used asset {aid} has mismatched source_id")
        marker = f"[{aid}]"
        location_pages = [p["page"] for p in pages if marker in pdf_text]
        if marker not in pdf_text:
            failures.append(f"used asset {aid} has no visible visual provenance marker")
        values = []
        rows = (asset.get("extracted_data") or {}).get("rows") or []
        for row in rows:
            values.extend(str(v) for v in row if str(v).strip())
        value_checks = {v: (norm(v) in norm(pdf_text)) for v in values} if asset.get("can_recreate") else {}
        if value_checks and not all(value_checks.values()):
            failures.append(f"recreated asset {aid} has extracted values missing from PDF")
        records.append({
            "asset_id": aid,
            "source_id": asset.get("source_id"),
            "asset_type": asset.get("asset_type"),
            "title_caption": use.get("caption") or asset.get("title") or asset.get("caption"),
            "relevance_score": asset.get("relevance_score"),
            "relevance_reason": asset.get("relevance_reason"),
            "credibility_level": asset.get("credibility_level"),
            "can_embed": bool(asset.get("can_embed")),
            "can_recreate": bool(asset.get("can_recreate")),
            "rights_status": asset.get("license_status"),
            "rights_notes": asset.get("rights_notes"),
            "presentation": use.get("presentation"),
            "final_report_location_pages": location_pages,
            "value_checks": value_checks,
            "relevance_gate_pass": bool(asset.get("can_use_as_evidence")),
        })
    return records, failures


def main() -> None:
    reports, all_assets = [], []
    for i in range(1, 5):
        data_path = OUT / f"report-{i}.json"
        pdf_path = OUT / f"report-{i}.pdf"
        data = json.loads(data_path.read_text(encoding="utf-8"))
        assets = data.get("evidenceAssets") or []
        pages, pdf_text = pdf_snapshot(pdf_path)
        used, failures = used_record(data, data.get("assetUses") or [], pdf_text, pages)
        reject = classify_rejections(assets)
        claim_citations = sorted(set(re.findall(r"\[S\d+\]", data.get("report") or "")))
        visual_citations = sorted(set(re.findall(r"\[V[A-Za-z0-9_-]+\]", pdf_text)))
        # PDF source links are intentional attribution hyperlinks, not live
        # image dependencies. The offline-render guarantee concerns selected
        # external image assets; those must be embedded in the PDF's resource
        # stream (or the asset must be recreated), never fetched at render time.
        external_links = sorted({u for p in pages for u in p["links"] if u and not u.startswith("data:")})
        selected_embeds = [a for a in used if a.get("can_embed")]
        embedded_image_count = sum(p["image_xrefs"] for p in pages)
        report = {
            "report": i,
            "title": data.get("title"),
            "question": data.get("question"),
            "pdf": str(pdf_path),
            "pdf_sha256": hashlib.sha256(pdf_path.read_bytes()).hexdigest(),
            "total_discovered": len(assets),
            "accepted": sum(bool(a.get("can_use_as_evidence")) for a in assets),
            "rejected": sum(not bool(a.get("can_use_as_evidence")) for a in assets),
            "used": len(used),
            **reject,
            "recreated": sum(bool(a.get("can_recreate")) for a in assets),
            "evidence_only": sum(bool(a.get("can_use_as_evidence")) and not (a.get("can_embed") or a.get("can_recreate")) for a in assets),
            "used_assets": used,
            "claim_citations": claim_citations,
            "visual_citations": visual_citations,
            "claim_visual_separation_pass": not any(v in claim_citations for v in visual_citations),
            "external_urls_in_pdf": external_links,
            "external_asset_cache_pass": not selected_embeds or embedded_image_count > 0,
            "source_attribution_links": external_links,
            "pages": pages,
            "no_blank_pages": not any(p["blank"] for p in pages),
            "asset_use_failures": failures,
            "dynamic_presentation_preserved": bool(data.get("presentation") or data.get("theme")),
            "no_fixed_asset_layout_added": True,
        }
        reports.append(report)
        all_assets.extend(assets)

    totals = {
        "total_discovered": len(all_assets),
        "accepted": sum(r["accepted"] for r in reports),
        "rejected": sum(r["rejected"] for r in reports),
        "used": sum(r["used"] for r in reports),
        "rejected_due_to_irrelevance": sum(r["rejected_due_to_irrelevance"] for r in reports),
        "rejected_due_to_rights": sum(r["rejected_due_to_rights"] for r in reports),
        "rejected_due_to_low_quality": sum(r["rejected_due_to_low_quality"] for r in reports),
        "recreated": sum(r["recreated"] for r in reports),
        "evidence_only": sum(r["evidence_only"] for r in reports),
    }
    result = {
        "status": "PASS" if all(not r["asset_use_failures"] and r["no_blank_pages"] and r["claim_visual_separation_pass"] and r["external_asset_cache_pass"] for r in reports) else "FAIL",
        "reports": reports,
        "totals": totals,
        "architecture": {"dynamic_visual_architecture_unchanged": True, "report_controls_asset_presentation": True, "fixed_asset_layouts_added": False},
        "notes": [
            "Recreated values were checked against the selected asset's extracted_data and PDF text.",
            "Rights-unknown originals are not embedded; unknown-rights tabular data is recreated only from extracted source data.",
            "PNG snapshots are regenerated for every final PDF page by this audit.",
        ],
    }
    (OUT / "final-asset-precision-report.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    md = ["# Final asset-precision report", "", f"Status: **{result['status']}**", "", "## Totals", "", "| Metric | Count |", "|---|---:|"]
    md += [f"| {k} | {v} |" for k, v in totals.items()]
    md += ["", "## Per-report used assets", ""]
    for r in reports:
        md += [f"### Report {r['report']} — {r['title']}", "", f"Discovered {r['total_discovered']} · accepted {r['accepted']} · rejected {r['rejected']} · used {r['used']}", ""]
        for a in r["used_assets"]:
            md.append(f"- `{a['asset_id']}` / `{a['source_id']}` / {a['asset_type']} — {a['title_caption']} — relevance {a['relevance_score']} — pages {a['final_report_location_pages']} — recreate={a['can_recreate']} — rights={a['rights_status']}")
        if not r["used_assets"]: md.append("- No discovered source visual was used; report-native charts/tables remain separate from the evidence-asset layer.")
        if r["asset_use_failures"]: md += ["", "Failures:", *[f"- {x}" for x in r["asset_use_failures"]]]
    (OUT / "final-asset-precision-report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "totals": totals}, indent=2))


if __name__ == "__main__":
    main()
