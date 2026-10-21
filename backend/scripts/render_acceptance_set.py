"""Render the three representative report contracts for visual acceptance QA."""
from pathlib import Path
import json
import pymupdf

from routes.pdf import build_pdf


CASES = [
    ("01-quick-commerce", Path("artifacts/representative-validation/01-quick-commerce/report.json")),
    ("02-banking-cycle", Path("artifacts/representative-validation/02-banking-cycle/report.json")),
    ("09-renewable-energy", Path("artifacts/client-demo-10-reports/09-renewable-energy/report.json")),
]


def main() -> int:
    root = Path("artifacts/acceptance-qa")
    root.mkdir(parents=True, exist_ok=True)
    for slug, source in CASES:
        payload = json.loads(source.read_text(encoding="utf-8"))
        question = payload.get("question") or payload.get("researchQuestion") or payload.get("title") or "Research report"
        pdf = build_pdf(
            payload.get("report", ""), payload.get("title", ""), question,
            payload.get("summary", ""), payload.get("keyStats", []) or [],
            payload.get("charts", []) or [], theme=payload.get("theme"),
            sources=payload.get("sources", []), presentation=payload.get("presentation"),
            evidence_assets=payload.get("evidenceAssets", []) or [],
            asset_uses=payload.get("assetUses", []) or [],
        )
        folder = root / slug
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "report.pdf").write_bytes(pdf)
        document = pymupdf.open(stream=pdf, filetype="pdf")
        for page_number, page in enumerate(document, 1):
            pixmap = page.get_pixmap(matrix=pymupdf.Matrix(1.75, 1.75), alpha=False)
            pixmap.save(str(folder / f"page-{page_number:02d}.png"))
        print(json.dumps({"slug": slug, "pages": len(document), "bytes": len(pdf)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
