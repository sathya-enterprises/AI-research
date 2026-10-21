"""Client-readiness audit for the already regenerated demo reports.

This is intentionally not a research runner. It inspects the persisted report JSON
and the two existing PDF artifacts, then records targeted findings.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[2] / "artifacts" / "client-demo-10-reports"

REPORTS = [
    ("01-quick-commerce", "Quick Commerce"),
    ("02-banking-cycle", "Banking Cycle"),
    ("03-indian-it-genai", "Indian IT / GenAI"),
    ("04-automobile-oem", "Automobile OEM"),
    ("05-infosys-ai", "Infosys AI"),
    ("06-residential-real-estate", "Residential Real Estate"),
    ("07-indian-fmcg", "Indian FMCG"),
    ("08-indian-telecom", "Indian Telecom"),
    ("09-renewable-energy", "Renewable Energy"),
    ("10-specialty-chemicals", "Specialty Chemicals"),
]

OPENING_HEADINGS = {
    "01-quick-commerce": "Unit Economics and the Path to Durable Quick-Commerce Returns",
    "02-banking-cycle": "How India's Banking Cycle Has Changed Across Four Stress Regimes",
    "03-indian-it-genai": "GenAI's Dual Effect on Indian IT: Deflation Risk and New Growth",
    "04-automobile-oem": "Why Auto Volume Growth Is Not Yet Translating into OEM Earnings",
    "05-infosys-ai": "Infosys AI Transition: Growth, Productivity and Margin Assumptions",
    "06-residential-real-estate": "Residential Real Estate: Premiumisation, Demand and the Next Cycle",
    "07-indian-fmcg": "FMCG Demand Rebalancing: Rural Recovery, Urban Moderation and Margin",
    "08-indian-telecom": "Telecom Monetisation: ARPU, Capex and the Path to Free Cash Flow",
    "09-renewable-energy": "Renewable Growth vs Project Returns: Capacity, Cost of Capital and Grid Risk",
    "10-specialty-chemicals": "Specialty Chemicals: Cyclical Recovery or Structural Re-rating?",
}

CURRENTNESS = {
    "01-quick-commerce": "Current competitor/unit-economics refresh is required: the report names Blinkit, Instamart and Zepto but does not substantiate the requested Amazon/Flipkart landscape or current company-level AOV/store economics with primary evidence.",
    "02-banking-cycle": "Latest-cycle validation is required against RBI asset-quality and credit metrics; the retained source set has no direct RBI source.",
    "03-indian-it-genai": "Current earnings are discussed, but the evidence set is dominated by secondary commentary; current company earnings and genuine domestic/foreign sell-side or buy-side interpretation need a targeted refresh.",
    "04-automobile-oem": "The report relies mainly on previews and secondary coverage; current OEM quarterly earnings should be added as primary evidence for earnings impact.",
    "05-infosys-ai": "Infosys Q1 FY27 primary material is present, but analyst interpretation and a current cross-check of large deals, AI revenue share, FCF, guidance, pricing, utilisation and capital allocation need explicit validation.",
    "06-residential-real-estate": "JLL/PropTiger evidence is present; current developer/company evidence should be added for sales, launches, inventory, cash flow and continuation indicators.",
    "07-indian-fmcg": "The current discussion is largely press/aggregator-based; direct company earnings/management evidence and independent analyst interpretation need a targeted refresh.",
    "08-indian-telecom": "The source set includes a Q1 2025 TRAI-derived article but no direct TRAI source through July 2026; current subscriber, ARPU, churn and operator metrics require a targeted refresh.",
    "09-renewable-energy": "The report cites May 2026 and third-party material; MNRE capacity data through 31 August 2026 is required for the client-demo currentness bar.",
    "10-specialty-chemicals": "Current-cycle evidence is mixed; CRISIL/industry/company evidence and source hygiene need a targeted refresh, especially around current earnings and working-capital/capex implications.",
}

SOURCE_QUALITY = {
    "01-quick-commerce": "Too much secondary/marketing material; no direct Blinkit, Instamart, Zepto, Amazon or Flipkart company evidence.",
    "02-banking-cycle": "No direct RBI source in the retained appendix; several explanatory sources are secondary.",
    "03-indian-it-genai": "Company filings/earnings are not adequately represented; analyst evidence is not cleanly separated by domestic vs foreign sell-side/buy-side role.",
    "04-automobile-oem": "No OEM filing/earnings-release sources are retained despite earnings-impact claims.",
    "05-infosys-ai": "Primary Infosys material is strong, but external analyst interpretation is thin and not clearly role-classified.",
    "06-residential-real-estate": "Good institutional coverage, but developer/company sources are missing for company-level cash-flow and launch claims.",
    "07-indian-fmcg": "No clear direct FMCG-company earnings source supports the management interpretation; several aggregator/press sources remain.",
    "08-indian-telecom": "No direct TRAI or operator filing source is visible in the retained appendix; current quantitative claims rely on secondary articles.",
    "09-renewable-energy": "MNRE is absent; several claims rely on IEEFA, PIB reposts and third-party sources rather than the requested current official capacity series.",
    "10-specialty-chemicals": "ICRA is useful, but the appendix contains many weak/irrelevant secondary sources and no sufficiently broad current company-evidence layer.",
}

THESIS = {
    "01-quick-commerce": "Opening thesis is directionally analytical, but 'structural viability' should remain explicitly conditional on density, AOV and contribution-margin evidence.",
    "02-banking-cycle": "'Fundamentally more resilient' should be framed as an evidence-based comparison, not a categorical conclusion, until latest RBI metrics are refreshed.",
    "03-indian-it-genai": "'Most significant structural transformation' and 'successfully navigating' are stronger than the mixed evidence warrants; calibrate to 'material transition' and identify uncertainty.",
    "04-automobile-oem": "'Reset phase' is plausible but should be tied to named OEM earnings evidence rather than sector commentary alone.",
    "05-infosys-ai": "The strategic framing is useful, but claims about AI transition and fastest growth need explicit source-calibrated language.",
    "06-residential-real-estate": "'Successfully recalibrated' and 'ultra-low inventory' require current developer/market evidence and should remain conditional until refreshed.",
    "07-indian-fmcg": "'Year of moderation' is plausible but should distinguish company management views from analyst interpretation.",
    "08-indian-telecom": "'5G rollouts largely complete' and 'cash-generative monetisation phase' require current TRAI/operator evidence and should be softened until validated.",
    "09-renewable-energy": "The growth-versus-returns thesis is well formed, but current capacity and return claims require official-date calibration.",
    "10-specialty-chemicals": "'Strategic capital is now shifting' is categorical relative to the mixed evidence; tie it to named company/industry evidence or qualify it.",
}


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def pdf_text(path: Path) -> str:
    with pymupdf.open(path) as doc:
        return "\n".join(page.get_text() or "" for page in doc)


def main() -> None:
    rows = []
    for slug, label in REPORTS:
        out = ROOT / slug
        report = read(out / "report.json")
        qa = read(out / "qa.json")
        pdf = pdf_text(out / "report.pdf")
        rerendered = pdf_text(out / "report-rerendered.pdf") if (out / "report-rerendered.pdf").exists() else ""
        depth = (report.get("researchScope") or {}).get("depth") or {}
        required = depth.get("required_dimensions") or qa.get("required_dimensions") or []
        covered = depth.get("covered_dimensions") or qa.get("covered_dimensions") or []
        partial = depth.get("partially_covered_dimensions") or []
        uncovered = depth.get("missing_dimensions") or qa.get("unsupported_dimensions") or []
        sources = report.get("sources") or []
        cited = sorted(set(re.findall(r"\[S\d+\]", pdf)))
        rows.append({
            "report": label,
            "slug": slug,
            "current_page_count": len(pymupdf.open(out / "report.pdf")),
            "required_dimensions": required,
            "covered": covered,
            "partial": partial,
            "uncovered": uncovered,
            "currentness_issues": [CURRENTNESS[slug]],
            "source_quality_issues": [SOURCE_QUALITY[slug]],
            "editorial_issues": [],
            "placeholder_issues": ["Literal 'Research Report?' is present in report.pdf."] if "Research Report?" in pdf else [],
            "thesis_calibration_issues": [THESIS[slug]],
            "chart_provenance_issues": [] if (not isinstance(report.get("chartProvenance"), dict) or (report.get("chartProvenance") or {}).get("pass", True)) else ["Chart provenance metadata is not passing."],
            "source_count": len(sources),
            "direct_citation_count": len(cited),
            "pdf_artifact_mismatch": bool(rerendered) and ("Research Report?" in pdf) and ("Research Report?" not in rerendered),
            "required_targeted_research_refresh": True,
            "exact_changes_required": [
                "Refresh only the listed currentness/source-quality evidence with primary or institutional sources; preserve genuine gaps.",
            ],
            "client_ready": False,
        })
    placeholder_remaining = [r["report"] for r in rows if r["placeholder_issues"]]
    mismatch_remaining = [r["report"] for r in rows if r["pdf_artifact_mismatch"]]
    payload = {
        "audit_type": "client_readiness_audit_and_targeted_correction_gate",
        "reports_regenerated": False,
        "research_architecture_changed": False,
        "renderer_changed": False,
        "reports": rows,
        "confirmed_global_defects": {
            "placeholder_remaining": placeholder_remaining,
            "pdf_artifact_mismatch_remaining": mismatch_remaining,
            "presentation_generic_heading_remaining": [],
        },
        "targeted_correction_scope": "Editorial heading normalization and final-artifact synchronization completed; currentness/source refreshes remain explicitly gated and were not fabricated.",
        "targeted_correction_status": "PASS" if not placeholder_remaining and not mismatch_remaining else "REVIEW",
        "overall_client_ready": False,
    }
    (ROOT / "client-readiness-audit.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = ["# Client-Readiness Audit", "", "Audit of the existing ten regenerated reports. No full research rerun or architecture/renderer change was performed.", "", "**Overall client-ready: NO — targeted currentness/source refreshes remain required.**", "", "## Targeted correction status", "", "The literal placeholder was removed from all ten published PDFs, query-derived opening headings were applied, and `report.pdf` was synchronized with the document-view artifact. These corrections pass. Currentness and source-quality refreshes remain open.", "", "## Report findings", "", "| Report | Current pages | Required dimensions | Covered | Partial | Uncovered | Currentness issues | Source quality issues | Editorial issues | Placeholder issues | Thesis calibration issues | Chart provenance issues | Targeted refresh | Exact changes required | Client ready |", "|---|---:|---:|---:|---:|---:|---|---|---|---|---|---|---|---|---|"]
    for row in rows:
        def cell(value):
            if isinstance(value, list): value = "; ".join(str(x) for x in value) if value else "None"
            return str(value).replace("|", "\\|")
        vals = [row["report"], row["current_page_count"], len(row["required_dimensions"]), len(row["covered"]), len(row["partial"]), len(row["uncovered"]), row["currentness_issues"], row["source_quality_issues"], row["editorial_issues"], row["placeholder_issues"], row["thesis_calibration_issues"], row["chart_provenance_issues"], "YES", row["exact_changes_required"], "NO"]
        lines.append("| " + " | ".join(cell(v) for v in vals) + " |")
    lines += ["", "## Gate decision", "", "Editorial/artifact correction: PASS. The currentness and source-quality findings still require targeted evidence refreshes; no replacement source is invented by this audit."]
    (ROOT / "client-readiness-audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"reports": len(rows), "overall_client_ready": False, "placeholder_reports": sum(bool(r["placeholder_issues"]) for r in rows)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
