"""Generate and inspect the three required research PDFs.

This is intentionally an artifact-level check: it renders PDF pages to PNGs
with PyMuPDF and reports blank/near-blank pages, page counts, and repeated
page fingerprints. It never prints request headers or environment values.

Usage (backend running on the configured local port):
    python scripts/pdf_visual_qa.py
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

QUERIES = [
    "Assess whether the current Indian private-sector banking earnings cycle is sustainable. Examine credit growth, deposit growth, funding costs, NIMs, asset quality, provisions, ROA/ROE, competition and management commentary. Compare management expectations with professional domestic and international analyst views and identify the factors that could cause earnings estimates to rise or fall.",
    "Assess whether generative AI is likely to structurally change the growth and margin outlook for India's major IT services companies. Examine client technology spending, deal activity, pricing, utilisation, headcount, employee costs, AI-related revenue, productivity gains and management commentary. Compare company commentary with professional analyst expectations.",
    "Investigate whether India's private-sector capital expenditure cycle is becoming broad-based enough to support a multi-year earnings cycle. Examine corporate capex announcements, order inflows, capacity utilisation, government spending, private investment intentions, financing conditions, execution trends and working-capital requirements.",
]


def post_json(base: str, path: str, payload: dict) -> dict | bytes:
    request = Request(
        f"{base.rstrip('/')}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=360) as response:
            body = response.read()
            if response.headers.get_content_type() == "application/pdf":
                return body
            return json.loads(body.decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeError(f"{path} returned HTTP {exc.code}; check the local backend logs and configured external services") from None


def inspect_pdf(pdf_bytes: bytes, output_dir: Path, name: str) -> dict:
    import pymupdf as fitz

    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    fingerprints: list[str] = []
    blank_pages: list[int] = []
    for index, page in enumerate(document, start=1):
        text = (page.get_text() or "").strip()
        pix = page.get_pixmap(matrix=fitz.Matrix(1.25, 1.25), alpha=False)
        png_path = output_dir / f"{name}-page-{index:02d}.png"
        pix.save(str(png_path))
        fingerprints.append(hashlib.sha256(pix.samples).hexdigest()[:16])
        if len(text) < 20 and pix.width * pix.height > 0:
            blank_pages.append(index)
    return {
        "pages": len(document),
        "blank_or_near_blank_pages": blank_pages,
        "repeated_page_fingerprints": len(fingerprints) - len(set(fingerprints)),
        "images": len(list(output_dir.glob(f"{name}-page-*.png"))),
    }


def main() -> int:
    base = os.environ.get("BACKEND_URL", "http://localhost:10000")
    output_dir = Path(os.environ.get("GG_PDF_QA_DIR", "artifacts/pdf-qa"))
    output_dir.mkdir(parents=True, exist_ok=True)
    summaries = []
    for index, question in enumerate(QUERIES, start=1):
        report = post_json(base, "/api/chat/report", {"question": question})
        if not isinstance(report, dict) or not report.get("report"):
            raise RuntimeError(f"Report {index} did not return report content")
        pdf = post_json(base, "/api/chat/report/pdf", report)
        if not isinstance(pdf, bytes) or not pdf.startswith(b"%PDF-"):
            raise RuntimeError(f"Report {index} did not return a PDF")
        pdf_path = output_dir / f"report-{index}.pdf"
        pdf_path.write_bytes(pdf)
        summaries.append({"report": index, **inspect_pdf(pdf, output_dir, f"report-{index}")})
    (output_dir / "summary.json").write_text(json.dumps(summaries, indent=2), encoding="utf-8")
    print(json.dumps(summaries, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
