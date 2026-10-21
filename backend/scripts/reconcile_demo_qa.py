"""Reconcile a rerun report into the batch QA record without changing its PDF."""
from pathlib import Path
import hashlib, json, re
import pymupdf

root = Path("artifacts/client-demo-10-reports")
summary_path = root / "demo-10-reports-qa.json"
summary = json.loads(summary_path.read_text(encoding="utf-8"))
slug = "06-residential-real-estate"
out = root / slug
report = json.loads((out / "report.json").read_text(encoding="utf-8"))
doc = pymupdf.open(out / "report.pdf")
texts = [(p.get_text() or "").strip() for p in doc]
scope = report.get("researchScope", {})
depth = scope.get("depth", {})
claims = scope.get("claims", {})
rec = next(r for r in summary["reports"] if r.get("slug") == slug)
rec.update({
    "title": report.get("title"),
    "page_count": len(doc),
    "pages": [{"page": i + 1, "text_chars": len(t)} for i, t in enumerate(texts)],
    "source_count": len(report.get("sources") or []),
    "required_dimensions": depth.get("required_dimensions", []),
    "covered_dimensions": depth.get("covered_dimensions", []),
    "uncovered_dimensions": depth.get("missing_dimensions", []),
    "depth_pass": bool(depth.get("depth_pass", depth.get("pass"))),
    "scope_pass": bool((scope.get("scope") or {}).get("scope_pass")),
    "unsupported_claim_count": (claims or {}).get("unsupported_count", 0),
    "charts_used": len(report.get("charts") or []),
    "tables_used": sum(1 for c in (report.get("charts") or []) if isinstance(c, dict) and c.get("type") == "table"),
    "external_visual_assets_used": len(report.get("images") or []),
    "S_citation_count": len(set(re.findall(r"\[S\d+\]", "\n".join(texts)))),
    "V_provenance_count": len(set(re.findall(r"\[(?:V|IMG)\d+\]", "\n".join(texts)))),
    "bad_tokens": [x for x in ["Source unavailable", "Image unavailable", "Chart data unavailable", "No data available", "####"] if x.lower() in "\n".join(texts).lower()],
    "blank_pages": [i + 1 for i, t in enumerate(texts) if len(t) < 20],
    "pdf_sha256": hashlib.sha256((out / "report.pdf").read_bytes()).hexdigest(),
})
summary["all_pass"] = all(r.get("depth_pass") and r.get("scope_pass") and not r.get("uncovered_dimensions") and not r.get("bad_tokens") and not r.get("blank_pages") for r in summary["reports"])
summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
