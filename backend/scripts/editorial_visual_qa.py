"""Two-layer PDF QA for the representative editorial refinement pass.

Layer A is mechanical artifact QA. Layer B is deliberately conservative:
it catches common publication defects (generic openings, missing chart labels,
card-heavy pages, repeated page rhythm) without pretending to automate taste.
"""
from __future__ import annotations

import argparse, json, re
from pathlib import Path
import pymupdf

GENERIC_OPENINGS = {"executive summary", "sector overview", "industry overview", "risks", "conclusion", "key findings"}

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()

def _technical(doc, payload):
    pages, blank, clipped = [], [], []
    for i, page in enumerate(doc, 1):
        text = (page.get_text() or "").strip()
        if len(text) < 30:
            blank.append(i)
        rect = page.rect
        for block in page.get_text("blocks") or []:
            if block[2] > rect.width + 1 or block[3] > rect.height + 1:
                clipped.append(i)
        pages.append({"page": i, "text_chars": len(text), "images": len(page.get_images(full=True))})
    report = str(payload.get("report") or "")
    return {
        "pass": not blank and not clipped and len(doc) > 0 and "[S" in report,
        "pages": pages, "blank_pages": blank, "clipped_blocks": sorted(set(clipped)),
        "citations_present": "[S" in report,
        "source_appendix_present": bool(re.search(r"\bSources?\b|\bReferences\b|\bData Sources\b", "\n".join(p.get_text() or "" for p in doc), re.I)),
    }

def _visual(payload, doc):
    charts = [c for c in (payload.get("charts") or []) if isinstance(c, dict) and c.get("type") != "table"]
    opening = ""
    first_text = doc[0].get_text() if len(doc) else ""
    first_text = "\n".join(line for line in first_text.splitlines() if not line.lower().startswith("growth gradual"))
    headings = re.findall(r"(?m)^(?:[A-Z][^\n]{8,}|\d+\.\s+[^\n]+)$", first_text or "")
    if headings:
        opening = _norm(headings[0])
    chart_checks = []
    full_text = "\n".join(p.get_text() or "" for p in doc)
    for chart in charts:
        labels = [str(d.get("label", "")) for s in chart.get("series") or [] for d in (s.get("data") or [])]
        x_label, y_label = str(chart.get("xLabel") or "").strip(), str(chart.get("yLabel") or chart.get("unit") or "").strip()
        compact_text = _norm(full_text).replace(" ", "")
        missing = [x for x in labels if x and _norm(x).replace(" ", "") not in compact_text]
        chart_checks.append({"title": chart.get("title", ""), "labels": len(labels), "x_axis": bool(x_label), "y_axis": bool(y_label), "labels_visible": not missing, "missing_labels": missing[:8], "pass": bool(labels) and bool(y_label) and not missing})
    cardish = sum(len(p.get_drawings() or []) for p in doc)
    page_diagnostics = []
    for i, page in enumerate(doc, 1):
        text = page.get_text() or ""
        blocks = page.get_text("blocks") or []
        chart_present = "Source:" in text and any(term in text.lower() for term in ("chart", "market", "ratio", "cost", "capacity", "share"))
        page_diagnostics.append({"page": i, "text_blocks": len(blocks), "text_chars": len(text), "visual_focal_point": chart_present or bool(page.get_images(full=True)), "text_heavy_without_focal": len(text) > 1800 and not chart_present})
    repeated = 0
    signatures = []
    for page in doc:
        blocks = page.get_text("blocks") or []
        signatures.append(tuple("chart" if "Source:" in (b[4] or "") else "text" for b in blocks[:8]))
    repeated = len(signatures) - len(set(signatures))
    findings = []
    opening_generic = opening in GENERIC_OPENINGS or any(x in opening for x in GENERIC_OPENINGS)
    question_exposed = bool(re.search(r"research question|\?", first_text or "", re.I))
    if opening_generic: findings.append("opening remains generic")
    if not question_exposed: findings.append("research question is not exposed on page one")
    if any(not c["pass"] for c in chart_checks): findings.append("chart readability failure: missing axis meaning or visible category labels")
    if cardish > max(40, len(doc) * 18): findings.append("page geometry is drawing-heavy; inspect for card density")
    if repeated >= max(2, len(doc) // 2): findings.append("repeated page rhythm detected")
    text_heavy_pages = [d["page"] for d in page_diagnostics if d["text_heavy_without_focal"]]
    if text_heavy_pages: findings.append(f"text-heavy page(s) without a clear focal point: {text_heavy_pages}")
    if cardish > max(28, len(doc) * 12): findings.append("container density may flatten component hierarchy; inspect page balance")
    return {"pass": not any("failure" in f or "generic" in f for f in findings), "opening": opening, "opening_generic": opening_generic, "question_exposed": question_exposed, "chart_checks": chart_checks, "cardish_drawing_count": cardish, "repeated_page_signatures": repeated, "page_diagnostics": page_diagnostics, "findings": findings}

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--json", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    payload = json.loads(Path(args.json).read_text(encoding="utf-8"))
    doc = pymupdf.open(args.pdf)
    result = {"layer_a_technical": _technical(doc, payload), "layer_b_visual_editorial": _visual(payload, doc)}
    result["ready_for_propagation"] = bool(result["layer_a_technical"]["pass"] and result["layer_b_visual_editorial"]["pass"])
    Path(args.out).write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["layer_a_technical"]["pass"] else 1

if __name__ == "__main__":
    raise SystemExit(main())
