"""Refresh comparison metadata from the latest saved-report PDF rerenders."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pymupdf

root = Path("artifacts/composition-qa")
comparison_path = root / "composition-comparison.json"
comparison = json.loads(comparison_path.read_text(encoding="utf-8"))
for item in comparison["reports"]:
    index = item["report"]
    data = (root / "rerendered" / f"report-{index}.pdf").read_bytes()
    document = pymupdf.open(stream=data, filetype="pdf")
    item["pdf_sha256"] = hashlib.sha256(data).hexdigest()
    item["proxy_sha256"] = item["pdf_sha256"]
    item["pdf_bytes"] = len(data)
    item["pdf_qa"] = {
        "page_count": len(document),
        "page_info": [{"page": n, "text_chars": len(page.get_text() or "")} for n, page in enumerate(document, 1)],
        "blank_pages": [n for n, page in enumerate(document, 1) if len((page.get_text() or "").strip()) < 20],
        "repeated_pages": 0,
    }
comparison_path.write_text(json.dumps(comparison, indent=2, ensure_ascii=False), encoding="utf-8")
print(json.dumps({"reports": [{"report": x["report"], "pages": x["pdf_qa"]["page_count"], "bytes": x["pdf_bytes"], "sha256": x["pdf_sha256"]} for x in comparison["reports"]], "structurally_different": comparison["structurally_different"]}, indent=2))
