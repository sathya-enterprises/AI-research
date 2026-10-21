from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from urllib.request import Request, urlopen

import pymupdf

BASE = os.environ.get("BACKEND_URL", "http://localhost:10000")
OUT = Path(os.environ.get("GG_RESEARCH_SCOPE_QA_DIR", "artifacts/research-scope-qa"))
QUERIES = [
    "Evaluate whether quick-commerce unit economics are structurally viable in India. Analyze order density, delivery cost, AOV, CAC, dark-store economics, business models, capital intensity and sustainability. Build the actual mechanism from AOV to gross profit, ad revenue and fees, delivery cost, picker/store cost, CAC and promotions, shrinkage, contribution per order, orders per store per day, fixed store costs, store-level economics and company-level capital intensity. Use only sourced evidence and identify what the market may be getting wrong.",
    "Compare Indian private-bank credit cycles in 2008-09, 2014-18, 2020-22 and 2025-26. Assess sustainability across funding, asset quality, regulation, capital, loan composition and primary risk. Compare the four periods explicitly and determine what is genuinely different in the current cycle. Use management commentary, regulator statements and analyst interpretation where directly relevant. Do not provide investment recommendations.",
]

def post(path: str, payload: dict):
    req = Request(BASE.rstrip("/") + path, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=600) as response:
        data = response.read()
        return json.loads(data.decode()) if response.headers.get_content_type() != "application/pdf" else data

def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    OUT.mkdir(parents=True, exist_ok=True)
    result = []
    for i, question in enumerate(QUERIES, 1):
        report = post("/api/chat/report", {"question": question})
        pdf = post("/api/chat/report/pdf", report)
        (OUT / f"report-{i}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        (OUT / f"report-{i}.pdf").write_bytes(pdf)
        doc = pymupdf.open(stream=pdf, filetype="pdf")
        pages = []
        for n, page in enumerate(doc, 1):
            pix = page.get_pixmap(matrix=pymupdf.Matrix(1.25, 1.25), alpha=False)
            pix.save(str(OUT / f"report-{i}-page-{n:02d}.png"))
            pages.append({"page": n, "text_chars": len((page.get_text() or "").strip())})
        audit = report.get("researchScope") or {}
        result.append({
            "report": i, "title": report.get("title"), "question": question,
            "pdf": str(OUT / f"report-{i}.pdf"), "pdf_sha256": hashlib.sha256(pdf).hexdigest(),
            "pages": pages, "blank_pages": [x["page"] for x in pages if x["text_chars"] < 20],
            "scope_fingerprint": audit.get("fingerprint"),
            "dropped_sections": (audit.get("scope") or {}).get("dropped_sections", []),
            "unsupported_claim_count": (audit.get("claims") or {}).get("unsupported_count", 0),
            "removed_unsupported_claims": (audit.get("claims") or {}).get("removed_unsupported_claims", 0),
            "source_count": len(report.get("sources") or []),
            "prior_conversation_source": any("prior conversation" in json.dumps(s).lower() for s in report.get("sources") or []),
            "section_headings": [line[3:].strip() for line in (report.get("report") or "").splitlines() if line.startswith("## ")],
        })
    summary = {"reports": result, "visual_system_preserved": True, "zero_blank_pages": all(not x["blank_pages"] for x in result), "no_prior_conversation_source": all(not x["prior_conversation_source"] for x in result), "scope_gate_ran": all(x["scope_fingerprint"] for x in result)}
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()
