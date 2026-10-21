from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from routes.pdf import build_pdf

OUT = Path("backend/artifacts/evidence-assets-qa")


def main():
    path = OUT / "report-4.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    by_url = {str(s.get("url")): str(s.get("source_id") or "") for s in data.get("sources", [])}
    for asset in data.get("evidenceAssets", []):
        asset["source_id"] = asset.get("source_id") or by_url.get(str(asset.get("source_url")), "")
    asset = next(a for a in data.get("evidenceAssets", []) if a.get("can_recreate") and a.get("can_use_as_evidence"))
    decision = {"asset_id": asset["asset_id"], "use_asset": True, "presentation": "inline_evidence", "caption": asset.get("title") or "Recreated source table"}
    marker = f"[ASSET:{asset['asset_id']}]"
    data["report"] = data["report"].replace(marker, "").strip()
    data["report"] = re.sub(r"(?im)^(#{2,4}\s+(?:sources|references)\b)", marker + r"\n\n\1", data["report"], count=1)
    if marker not in data["report"]:
        data["report"] = data["report"].rstrip() + "\n\n" + marker
    data["assetUses"] = [decision]
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    pdf = build_pdf(data["report"], data.get("title", ""), data.get("question", ""), data.get("summary", ""), data.get("keyStats", []), data.get("charts", []), "", data.get("fileImages", []), data.get("images", []), data.get("theme"), data.get("sources", []), data.get("presentation"), data.get("evidenceAssets", []), data.get("assetUses", []))
    pdf_path = OUT / "report-4.pdf"
    pdf_path.write_bytes(pdf)
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    for i, page in enumerate(doc, 1):
        page.get_pixmap(matrix=pymupdf.Matrix(1.2, 1.2), alpha=False).save(OUT / f"report-4-page-{i:02d}.png")
    print({"asset_id": asset["asset_id"], "source_id": asset.get("source_id"), "pdf": str(pdf_path), "pages": len(doc)})


if __name__ == "__main__":
    main()
