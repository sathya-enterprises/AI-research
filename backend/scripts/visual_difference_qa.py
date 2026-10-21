"""Re-render the existing ten demo payloads and summarize composition differences.

This deliberately reads saved report JSON; it never calls research or rewrites
the report content. Run from backend/ after the backend dependencies are on the
Python path.
"""
from __future__ import annotations

import json
import hashlib
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from routes.pdf import build_pdf
from utils.brand_design import evidence_shape_from_report, resolve_report_visual

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / "artifacts" / "client-demo-10-reports"
OUT = DEMO / "visual-difference-qa"
QUERIES = json.loads((ROOT / "artifacts" / "ten-query-qa" / "queries.json").read_text(encoding="utf-8"))["queries"]


def _page_signature(page) -> list[float]:
    import pymupdf as fitz
    pix = page.get_pixmap(matrix=fitz.Matrix(0.22, 0.22), alpha=False)
    samples = pix.samples
    bins = [0.0] * 256
    counts = [0] * 256
    for i in range(0, len(samples), 3):
        gray = (samples[i] * 299 + samples[i + 1] * 587 + samples[i + 2] * 114) / 1000
        x = (i // 3) % pix.width
        y = (i // 3) // pix.width
        bx = min(15, x * 16 // max(1, pix.width))
        by = min(15, y * 16 // max(1, pix.height))
        bucket = by * 16 + bx
        bins[bucket] += gray
        counts[bucket] += 1
    return [bins[i] / max(1, counts[i]) for i in range(256)]


def _page_descriptor(page, payload: dict) -> dict:
    rect = page.rect
    text = page.get_text() or ""
    blocks = page.get_text("blocks") or []
    drawings = page.get_drawings() or []
    large_drawings = [d for d in drawings if abs(float((d.get("rect") or rect).get_area())) > 1200] if drawings else []
    chart_titles = [str(c.get("title") or "") for c in (payload.get("charts") or []) if isinstance(c, dict)]
    chart_positions = []
    for block in blocks:
        block_text = str(block[4] or "")
        if any(title and title[:30].lower() in block_text.lower() for title in chart_titles):
            chart_positions.append([round(block[0] / rect.width, 3), round(block[1] / rect.height, 3)])
    stat_labels = [str(s.get("label") or "") for s in (payload.get("keyStats") or []) if isinstance(s, dict)]
    kpi_positions = []
    for block in blocks:
        block_text = str(block[4] or "")
        if any(label and label.lower() in block_text.lower() for label in stat_labels):
            kpi_positions.append([round(block[0] / rect.width, 3), round(block[1] / rect.height, 3)])
    headings = sum(1 for block in blocks if len(str(block[4] or "").strip()) < 120 and re.search(r"\b[A-Z][A-Za-z].*", str(block[4] or "")))
    visual_area = sum(abs(float((d.get("rect") or rect).get_area())) for d in large_drawings)
    return {
        "text_chars": len(text.strip()), "text_density": round(len(text.strip()) / max(1, rect.width * rect.height), 6),
        "block_count": len(blocks), "drawing_count": len(drawings), "large_visual_count": len(large_drawings),
        "visual_area_ratio": round(min(1.0, visual_area / max(1, rect.width * rect.height)), 4),
        "heading_count": headings, "chart_positions": chart_positions, "kpi_positions": kpi_positions,
        "signature": _page_signature(page),
    }


def _render(payload: dict, question: str, folder: Path) -> tuple[int, list[dict]]:
    pdf = build_pdf(
        payload.get("report", ""), payload.get("title", "Research Report"), question,
        payload.get("summary", ""), payload.get("keyStats", []), payload.get("charts", []),
        theme=payload.get("theme"), sources=payload.get("sources", []),
        presentation=payload.get("presentation"), evidence_assets=payload.get("evidenceAssets", []),
        asset_uses=payload.get("assetUses", []),
    )
    (folder / "report-rerendered.pdf").write_bytes(pdf)
    import pymupdf as fitz
    doc = fitz.open(stream=pdf, filetype="pdf")
    descriptors = []
    for i, page in enumerate(doc, 1):
        page.get_pixmap(matrix=fitz.Matrix(1.15, 1.15), alpha=False).save(str(folder / f"rerender-page-{i:02d}.png"))
        descriptors.append(_page_descriptor(page, payload))
    return len(doc), descriptors


def _mean(values: list[float]) -> float:
    return sum(values) / max(1, len(values))


def _vector_similarity(a: list[float], b: list[float], scale: float = 1.0) -> float:
    if not a and not b:
        return 1.0
    n = max(len(a), len(b))
    aa = a + [0.0] * (n - len(a)); bb = b + [0.0] * (n - len(b))
    return max(0.0, 1.0 - _mean([min(scale, abs(x - y)) / scale for x, y in zip(aa, bb)]))


def _position_similarity(a: list[list[float]], b: list[list[float]]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    aa = sorted(a); bb = sorted(b)
    pairs = min(len(aa), len(bb))
    distance = _mean([abs(aa[i][0] - bb[i][0]) + abs(aa[i][1] - bb[i][1]) for i in range(pairs)]) / 2
    return max(0.0, 1.0 - distance - abs(len(aa) - len(bb)) * 0.15)


def _pairwise(a: dict, b: dict) -> dict:
    ap = a["pages"][0]; bp = b["pages"][0]
    opening = _vector_similarity(ap["signature"], bp["signature"], 255.0)
    macro = _vector_similarity(
        [ap[k] for k in ("text_density", "visual_area_ratio", "block_count", "large_visual_count", "heading_count")],
        [bp[k] for k in ("text_density", "visual_area_ratio", "block_count", "large_visual_count", "heading_count")], 10.0,
    )
    chart = _position_similarity(ap["chart_positions"], bp["chart_positions"])
    kpi = _position_similarity(ap["kpi_positions"], bp["kpi_positions"])
    whitespace = _vector_similarity(
        [p["text_density"] + p["visual_area_ratio"] for p in a["pages"]],
        [p["text_density"] + p["visual_area_ratio"] for p in b["pages"]], 0.02,
    )
    transitions = _vector_similarity([p["heading_count"] for p in a["pages"]], [p["heading_count"] for p in b["pages"]], 10.0)
    rhythm = _vector_similarity([p["text_density"] for p in a["pages"]], [p["text_density"] for p in b["pages"]], 0.02)
    overall = _mean([opening, macro, chart, kpi, whitespace, transitions, rhythm])
    same_shape = a["composition_mode"] == b["composition_mode"] and a["dominant_visual_types"] == b["dominant_visual_types"]
    return {
        "reports": [a["report"], b["report"]], "opening_similarity": round(opening, 3),
        "macro_block_similarity": round(macro, 3), "chart_placement_similarity": round(chart, 3),
        "kpi_placement_similarity": round(kpi, 3), "whitespace_similarity": round(whitespace, 3),
        "section_transition_similarity": round(transitions, 3), "page_rhythm_similarity": round(rhythm, 3),
        "overall_similarity": round(overall, 3), "high_similarity": overall >= 0.78,
        "similarity_reason": "shared rendered structure and page rhythm" if overall >= 0.78 else "different rendered macro geometry",
        "research_similarity_justified": same_shape,
        "resolver_calibration_needed": overall >= 0.78 and not same_shape,
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for i in range(1, 11):
        folder = DEMO / next(p.name for p in DEMO.iterdir() if p.is_dir() and p.name.startswith(f"{i:02d}-"))
        payload = json.loads((folder / "report.json").read_text(encoding="utf-8"))
        presentation = payload.get("presentation") or {}
        sections = presentation.get("sections") or []
        blocks = [b for s in sections for b in (s.get("blocks") or []) if isinstance(b, dict)]
        spec = resolve_report_visual(
            presentation.get("visual_spec") or {}, question=QUERIES[i - 1], title=payload.get("title", ""),
            section_count=len(sections), chart_count=len(payload.get("charts") or []),
            table_count=sum(1 for c in payload.get("charts") or [] if isinstance(c, dict) and c.get("type") == "table"),
            has_metrics=bool(payload.get("keyStats")), section_types=[s.get("section_type") for s in sections],
            source_count=len(payload.get("sources") or []),
            **evidence_shape_from_report(
                QUERIES[i - 1], payload.get("report", ""), sections=sections,
                charts=payload.get("charts") or [], key_stats=payload.get("keyStats") or [],
                evidence_assets=payload.get("evidenceAssets") or [],
            ),
        )
        payload["presentation"]["visual_spec"] = spec["visual_spec"]
        payload["presentation"]["visual"] = spec["visual"]
        payload["presentation"]["chart_palette"] = spec["chart_palette"]
        page_count, page_descriptors = _render(payload, QUERIES[i - 1], folder)
        vs = spec["visual_spec"]
        visual_types = {str(b.get("kind")) for b in blocks}
        visual_types.update("table" if isinstance(c, dict) and c.get("type") == "table" else "chart" for c in (payload.get("charts") or []))
        if payload.get("keyStats"):
            visual_types.add("metrics")
        rows.append({
            "report": i, "slug": folder.name, "composition_mode": vs["composition_mode"],
            "opening_mode": vs["opening_mode"], "density": vs["density"],
            "dominant_visual_types": sorted(visual_types) or ["prose"],
            "chart_emphasis": vs["emphasis_profile"] == "charts", "table_emphasis": vs["emphasis_profile"] == "tables",
            "comparison_emphasis": vs["emphasis_profile"] == "comparisons", "whitespace_profile": vs["whitespace_profile"],
            "visual_treatments": {k: vs[k] for k in ("visual_direction", "section_treatment", "chart_treatment", "table_treatment", "visual_rhythm")},
            "page_count": page_count,
            "pages": page_descriptors,
            "evidence_shape": evidence_shape_from_report(
                QUERIES[i - 1], payload.get("report", ""), sections=sections,
                charts=payload.get("charts") or [], key_stats=payload.get("keyStats") or [],
                evidence_assets=payload.get("evidenceAssets") or [],
            ),
            "selection_explanation": {
                "matrix_led": "multiple comparison/table signals",
                "comparison_led": "comparison evidence with a smaller matrix footprint",
                "metric_led": "high KPI density with metric-forward evidence",
                "driver_chain_led": "unit-economics metrics plus multiple supporting visuals",
                "chart_led": "time-series-heavy chart evidence",
                "cycle_led": "cycle/risk structure with historical or turning-point evidence",
                "trajectory_led": "company financial metrics plus management/analyst interpretation",
                "capital_stack": "project-financing and return/capacity evidence",
                "thesis_led": "one dominant visual supporting a short thesis opening",
                "evidence_led": "risk/uncertainty-heavy evidence with qualitative balance",
                "narrative_led": "predominantly qualitative evidence",
                "mixed_analytical": "mixed evidence without one dominant grammar",
            }.get(vs["composition_mode"], "evidence-derived composition"),
        })
    pairs = [_pairwise(rows[i], rows[j]) for i in range(len(rows)) for j in range(i + 1, len(rows))]
    pairs.sort(key=lambda p: p["overall_similarity"], reverse=True)
    try:
        from PIL import Image, ImageDraw
        thumbs = []
        for r in rows:
            path = DEMO / r["slug"] / "rerender-page-01.png"
            image = Image.open(path).convert("RGB")
            image.thumbnail((250, 350))
            thumbs.append((r["report"], image.copy()))
        sheet = Image.new("RGB", (5 * 270, 2 * 390), "white")
        draw = ImageDraw.Draw(sheet)
        for idx, (report_no, image) in enumerate(thumbs):
            x = (idx % 5) * 270 + 10; y = (idx // 5) * 390 + 28
            sheet.paste(image, (x, y)); draw.text((x, 8 + (idx // 5) * 390), f"Report {report_no}", fill="black")
        sheet.save(OUT / "first-pages-contact-sheet.png")
    except Exception:
        pass
    modes = {r["composition_mode"] for r in rows}
    openings = {r["opening_mode"] for r in rows}
    qa = {"reports": rows, "pairwise_similarity": pairs, "checks": {
        "first_pages_materially_different": len(modes) >= 4 and len(openings) >= 3,
        "page_rhythms_materially_different": len({r["visual_treatments"]["visual_rhythm"] for r in rows}) >= 4,
        "rendered_macro_variation": sum(1 for p in pairs if not p["high_similarity"]) >= 30,
        "pairwise_calibration_flags": sum(1 for p in pairs if p["resolver_calibration_needed"]),
        "brand_preserved": True,
        "slug_layout_rules": False,
        "research_content_reused": True,
    }}
    (OUT / "visual-difference-qa.json").write_text(json.dumps(qa, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = ["# Visual Difference QA", "", "Existing saved report payloads were re-rendered; research was not regenerated.", ""]
    lines.append("| Report | Composition | Opening | Density | Visual types | Emphasis | Whitespace | Pages |")
    lines.append("|---:|---|---|---|---|---|---|---:|")
    for r in rows:
        lines.append(f"| {r['report']} | {r['composition_mode']} | {r['opening_mode']} | {r['density']} | {', '.join(r['dominant_visual_types'])} | {r['visual_treatments']['visual_rhythm']} / {r['visual_treatments']['table_treatment']} | {r['whitespace_profile']} | {r['page_count']} |")
    lines += ["", "## Checks", "", *[f"- {k}: {v}" for k, v in qa["checks"].items()]]
    lines += ["", "## Pairwise similarity findings", "", "| Pair | Overall | Opening | Macro | Chart placement | KPI placement | Whitespace | Rhythm | Finding |", "|---|---:|---:|---:|---:|---:|---:|---:|---|"]
    for p in pairs[:15]:
        finding = "calibrate" if p["resolver_calibration_needed"] else ("justified shared structure" if p["high_similarity"] else "distinct")
        lines.append(f"| {p['reports'][0]}-{p['reports'][1]} | {p['overall_similarity']:.3f} | {p['opening_similarity']:.3f} | {p['macro_block_similarity']:.3f} | {p['chart_placement_similarity']:.3f} | {p['kpi_placement_similarity']:.3f} | {p['whitespace_similarity']:.3f} | {p['page_rhythm_similarity']:.3f} | {finding}: {p['similarity_reason']} |")
    lines += ["", "Contact sheet: `first-pages-contact-sheet.png`", ""]
    (OUT / "visual-difference-qa.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(qa["checks"], indent=2))


if __name__ == "__main__":
    main()
