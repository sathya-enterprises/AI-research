"""Generate two research artifacts and compare their actual compositions.

This is intentionally endpoint-level: the PDFs are produced by
/api/chat/report/pdf, then every page is rendered to PNG with PyMuPDF.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from urllib.request import Request, urlopen

QUERIES = [
    "Assess whether the current Indian private-bank earnings cycle is sustainable. Examine NIM trends, deposit costs, credit growth, asset quality, unsecured lending, operating leverage, capital requirements, and competitive intensity. Compare management commentary from major private banks with domestic and international analyst perspectives. Determine the key variables that could cause earnings estimates to move up or down over the next 2–3 years.",
    "Evaluate whether generative AI represents a structural threat or an earnings opportunity for Indian IT services companies. Analyze revenue growth, pricing, employee productivity, deal sizes, margins, hiring, AI-related investments, client spending, and the transition from traditional services to AI-led offerings. Compare management commentary from major IT companies with domestic and global analyst views, and identify what the market may be getting wrong.",
]


def post(base: str, path: str, payload: dict):
    req = Request(base.rstrip("/") + path, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=420) as response:
        body = response.read()
        if response.headers.get_content_type() == "application/pdf":
            return body, dict(response.headers)
        return json.loads(body.decode()), dict(response.headers)


def section_titles(markdown: str) -> list[str]:
    return [m.group(1).strip() for m in re.finditer(r"^##\s+(.+?)\s*$", markdown or "", re.M)]


def inspect_pdf(data: bytes, folder: Path, name: str) -> dict:
    import pymupdf as fitz
    doc = fitz.open(stream=data, filetype="pdf")
    page_info = []
    pixels = []
    for idx, page in enumerate(doc, 1):
        pix = page.get_pixmap(matrix=fitz.Matrix(1.25, 1.25), alpha=False)
        path = folder / f"{name}-page-{idx:02d}.png"
        pix.save(str(path))
        text = (page.get_text() or "").strip()
        pixels.append(hashlib.sha256(pix.samples).hexdigest())
        page_info.append({"page": idx, "text_chars": len(text), "width": pix.width, "height": pix.height})
    return {
        "page_count": len(doc),
        "page_info": page_info,
        "blank_pages": [p["page"] for p in page_info if p["text_chars"] < 20],
        "repeated_pages": len(pixels) - len(set(pixels)),
    }


def composition(report: dict, pdf: bytes, headers: dict, folder: Path, name: str) -> dict:
    presentation = report.get("presentation") or {}
    sections = presentation.get("sections") or []
    blocks = [b for s in sections for b in (s.get("blocks") or []) if isinstance(b, dict)]
    charts = report.get("charts") or []
    cited = sorted(set(re.findall(r"\[S\d+\]", report.get("report", ""))))
    out = {
        "section_titles": section_titles(report.get("report", "")),
        "planned_section_titles": [str(s.get("title", "")) for s in sections],
        "section_count": len(sections),
        "block_types": sorted(set(str(b.get("kind", "")) for b in blocks)),
        "block_counts": {k: sum(1 for b in blocks if b.get("kind") == k) for k in sorted(set(str(b.get("kind", "")) for b in blocks))},
        "chart_types": sorted(set(str(c.get("type", "")) for c in charts)),
        "table_types": [str(c.get("type", "")) for c in charts if c.get("type") == "table"],
        "has_cover": bool((presentation.get("cover") or {}).get("enabled")),
        "has_executive_summary": str((presentation.get("executive_summary") or {}).get("placement", "none")) != "none",
        "has_conclusion": any("conclu" in t.lower() for t in section_titles(report.get("report", ""))),
        "source_count": len(report.get("sources") or []),
        "citation_count": len(cited),
        "citation_ids": cited,
        "pdf_sha256": hashlib.sha256(pdf).hexdigest(),
        "pdf_bytes": len(pdf),
        "proxy_sha256": headers.get("X-PDF-SHA256", headers.get("x-pdf-sha256", "")),
    }
    out["pdf_qa"] = inspect_pdf(pdf, folder, name)
    return out


def main() -> int:
    base = os.environ.get("BACKEND_URL", "http://localhost:10000")
    folder = Path(os.environ.get("GG_COMPOSITION_QA_DIR", "artifacts/composition-qa"))
    folder.mkdir(parents=True, exist_ok=True)
    reports = []
    for idx, query in enumerate(QUERIES, 1):
        report, report_headers = post(base, "/api/chat/report", {"question": query})
        if not isinstance(report, dict) or not report.get("report"):
            raise RuntimeError(f"report {idx} did not return report content")
        (folder / f"report-{idx}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        pdf, pdf_headers = post(base, "/api/chat/report/pdf", report)
        if not isinstance(pdf, bytes) or not pdf.startswith(b"%PDF-"):
            raise RuntimeError(f"report {idx} did not return a PDF")
        (folder / f"report-{idx}.pdf").write_bytes(pdf)
        reports.append({"report": idx, "query": query, **composition(report, pdf, pdf_headers, folder, f"report-{idx}")})
    result = {"reports": reports, "structurally_different": reports[0]["section_count"] != reports[1]["section_count"] or reports[0]["block_counts"] != reports[1]["block_counts"] or reports[0]["chart_types"] != reports[1]["chart_types"] or reports[0]["has_cover"] != reports[1]["has_cover"]}
    (folder / "composition-comparison.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
