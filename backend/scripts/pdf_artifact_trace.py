"""Trace backend and Next.js PDF bytes for the required report questions."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from urllib.request import Request, urlopen

import pymupdf

QUERIES = {
    "A": "Assess whether the current Indian private-bank earnings cycle is sustainable. Examine NIM trends, deposit costs, credit growth, asset quality, unsecured lending, operating leverage, capital requirements, and competitive intensity. Compare management commentary from major private banks with domestic and international analyst perspectives. Determine the key variables that could cause earnings estimates to move up or down over the next 2–3 years.",
    "B": "Evaluate whether generative AI represents a structural threat or an earnings opportunity for Indian IT services companies. Analyze revenue growth, pricing, employee productivity, deal sizes, margins, hiring, AI-related investments, client spending, and the transition from traditional services to AI-led offerings. Compare management commentary from major IT companies with domestic and global analyst views, and identify what the market may be getting wrong.",
    "C": "Investigate whether India's current private-sector capex cycle is broadening beyond a small number of industries. Examine announced projects, capacity utilization, corporate balance sheets, financing conditions, order books, and sector-level investment intentions. Identify the industries where capex is most likely to translate into sustainable earnings growth and distinguish genuine investment plans from management guidance that has yet to translate into execution.",
}


def post(url: str, payload: object) -> tuple[bytes, str, int]:
    req = Request(url, data=json.dumps(payload, ensure_ascii=False).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=360) as res:
        return res.read(), res.headers.get("content-type", ""), res.status


def pdf_stats(data: bytes) -> dict:
    document = pymupdf.open(stream=data, filetype="pdf")
    text = "\n".join(page.get_text() for page in document)
    return {
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "pages": len(document),
        "text_chars": len(text),
        "headings": [line.strip() for line in text.splitlines() if line.strip() and len(line.strip()) < 140 and any(line.strip().startswith(prefix) for prefix in ("Executive", "Market", "Financial", "AI", "Productivity", "Management", "Comparative", "Risks", "Risk", "Conclusion", "Private", "Order", "Investment", "Execution", "Implications", "Sources", "Appendix"))][:30],
    }


def main() -> None:
    backend = os.environ.get("BACKEND_URL", "http://localhost:10000").rstrip("/")
    next_proxy = os.environ.get("NEXT_PROXY_URL", "http://localhost:3000").rstrip("/")
    out = Path(os.environ.get("GG_PDF_TRACE_DIR", "artifacts/pdf-trace"))
    out.mkdir(parents=True, exist_ok=True)
    results = []
    for name, question in QUERIES.items():
        report_raw, report_type, report_status = post(f"{backend}/api/chat/report", {"question": question})
        report = json.loads(report_raw)
        (out / f"report-{name}.json").write_bytes(report_raw)
        report_text = str(report.get("report") or "")
        backend_raw, backend_type, backend_status = post(f"{backend}/api/chat/report/pdf", report)
        (out / f"backend-{name}.pdf").write_bytes(backend_raw)
        proxy_raw, proxy_type, proxy_status = post(f"{next_proxy}/api/chat/report/pdf", report)
        (out / f"proxy-{name}.pdf").write_bytes(proxy_raw)
        results.append({
            "report": name,
            "report_generation": {"status": report_status, "content_type": report_type, "report_chars": len(report_text), "charts": len(report.get("charts") or []), "sections_hint": report_text.count("\n## ")},
            "backend_pdf": {"status": backend_status, "content_type": backend_type, **pdf_stats(backend_raw)},
            "next_proxy_pdf": {"status": proxy_status, "content_type": proxy_type, **pdf_stats(proxy_raw)},
            "byte_identity": backend_raw == proxy_raw,
        })
    (out / "summary.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
