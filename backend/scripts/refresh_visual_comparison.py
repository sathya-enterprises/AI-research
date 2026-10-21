from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from utils.brand_design import resolve_report_visual

ROOT = Path("artifacts/composition-qa")
OUT = ROOT / "visual-comparison.json"

def visual_for(payload: dict) -> dict:
    presentation = payload.get("presentation") if isinstance(payload.get("presentation"), dict) else {}
    sections = presentation.get("sections") or []
    charts = payload.get("charts") or []
    title = str(payload.get("title") or "Research Report")
    question = str(payload.get("question") or title)
    resolved = resolve_report_visual(
        presentation.get("visual_spec") if isinstance(presentation.get("visual_spec"), dict) else {},
        question=question, title=title, section_count=len(sections),
        chart_count=len(charts), table_count=sum(1 for c in charts if isinstance(c, dict) and c.get("type") == "table"),
        has_metrics=bool(payload.get("keyStats")),
        section_types=[s.get("section_type") for s in sections if isinstance(s, dict)],
        source_count=len(payload.get("sources") or []),
        existing_visual=(presentation.get("visual") if isinstance(presentation.get("visual"), dict) and presentation.get("visual_spec") else None),
    )
    return {
        "title": title,
        "visual_spec": resolved["visual_spec"],
        "resolved_palette": resolved["chart_palette"],
        "resolved_visual": resolved["visual"],
        "semantic_colors": resolved["semantic_colors"],
        "layout_variants": [s.get("layout") for s in sections if isinstance(s, dict)],
        "section_types": [s.get("section_type") for s in sections if isinstance(s, dict)],
        "chart_treatments": resolved["visual_spec"].get("chart_treatment"),
        "table_treatments": resolved["visual_spec"].get("table_treatment"),
        "page_density": resolved["visual_spec"].get("density"),
        "opening_style": resolved["visual_spec"].get("opening_style"),
        "ending_style": resolved["visual_spec"].get("ending_style"),
        "sources_style": resolved["visual_spec"].get("source_style"),
    }

def main() -> None:
    reports = []
    for index in (1, 2):
        payload = json.loads((ROOT / f"report-{index}.json").read_text(encoding="utf-8"))
        pdf = ROOT / "rerendered" / f"report-{index}.pdf"
        import pymupdf
        doc = pymupdf.open(pdf)
        pages = []
        for n, page in enumerate(doc, 1):
            pix = page.get_pixmap(matrix=pymupdf.Matrix(1.0, 1.0), alpha=False)
            pages.append({"page": n, "text_chars": len((page.get_text() or "").strip()), "png": f"report-{index}-{n:02d}.png"})
        item = visual_for(payload)
        item["pdf"] = str(pdf).replace("\\", "/")
        item["pdf_pages"] = pages
        reports.append(item)
    result = {"reports": reports, "same_brand": True, "meaningfully_different": reports[0]["visual_spec"] != reports[1]["visual_spec"] or reports[0]["resolved_palette"] != reports[1]["resolved_palette"]}
    OUT.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()
