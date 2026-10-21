"""Run one normal production research request and render its PDF for QA."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from urllib.request import Request, urlopen

import pymupdf

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def post(base: str, path: str, payload: dict, timeout: int):
    req = Request(base.rstrip("/") + path, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=timeout) as response:
        body = response.read()
        return body if response.headers.get_content_type() == "application/pdf" else json.loads(body.decode())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--question", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--base", default="http://localhost:10000")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    report = post(args.base, "/api/chat/report", {"question": args.question}, 900)
    (out / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    pdf = post(args.base, "/api/chat/report/pdf", report, 300)
    (out / "report.pdf").write_bytes(pdf)
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    pages = []
    for number, page in enumerate(doc, 1):
        pix = page.get_pixmap(matrix=pymupdf.Matrix(1.25, 1.25), alpha=False)
        path = out / f"page-{number:02d}.png"
        pix.save(str(path))
        pages.append({"page": number, "text_chars": len((page.get_text() or "").strip()), "png": str(path)})
    result = {
        "question": args.question,
        "title": report.get("title"),
        "pages": pages,
        "pdf_sha256": hashlib.sha256(pdf).hexdigest(),
        "research_scope": report.get("researchScope") or report.get("research_scope") or {},
        "source_count": len(report.get("sources") or []),
        "report_chars": len(report.get("report") or ""),
    }
    (out / "qa.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
