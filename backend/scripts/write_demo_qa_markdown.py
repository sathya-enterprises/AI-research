"""Write the human-readable QA companion from the batch JSON artifact."""
from pathlib import Path
import json

root = Path("artifacts/client-demo-10-reports")
data = json.loads((root / "demo-10-reports-qa.json").read_text(encoding="utf-8"))
lines = ["# Growth Gradual - Client Demo QA", "", f"Overall automated acceptance: **{data.get('all_pass')}**", ""]
for i, r in enumerate(data.get("reports", []), 1):
    lines += [
        f"## {i:02d} - {r.get('slug')}", "",
        f"- Title: {r.get('title')}", f"- Pages: {r.get('page_count')}",
        f"- Required dimensions: {', '.join(r.get('required_dimensions', [])) or 'none recorded'}",
        f"- Covered dimensions: {', '.join(r.get('covered_dimensions', [])) or 'none recorded'}",
        f"- Unsupported dimensions: {', '.join(r.get('unsupported_dimensions', [])) or 'none'}",
        f"- Searches / fallback / zero-result retries: {r.get('search_count')} / {r.get('fallback_usage')} / {r.get('zero_result_retries')}",
        f"- Search provider usage: {r.get('search_key_provider_usage')}",
        f"- Sources retrieved / retained: {r.get('unique_sources_retrieved')} / {r.get('unique_sources_retained')}",
        f"- Pipeline discovered / unique / considered / retained / cited: {r.get('source_accounting', {}).get('total_discovered', 0)} / {r.get('source_accounting', {}).get('total_unique_discovered', 0)} / {r.get('source_accounting', {}).get('total_considered', 0)} / {r.get('source_accounting', {}).get('total_retained', 0)} / {r.get('source_accounting', {}).get('total_directly_cited', 0)}",
        f"- Claims [S#] / visual assets / [V#]: {r.get('claim_citation_count')} / {r.get('visual_asset_count')} / {r.get('visual_provenance_status')}",
        f"- Evidence / scope / depth: {r.get('evidence_validation')} / {r.get('scope_validation')} / {r.get('depth_validation')}",
        f"- Charts / tables: {r.get('chart_count')} / {r.get('table_count')}",
        f"- Search depth: {r.get('queries_executed')} queries / {r.get('pages_retrieved')} pages / {r.get('unique_candidates')} unique candidates",
        f"- Evidence saturation: {r.get('evidence_saturation')}",
        f"- Source depth / quality / missing dimensions: {r.get('source_depth_pass')} / {r.get('source_quality_pass')} / {', '.join(r.get('missing_evidence_dimensions') or []) or 'none'}",
        f"- Placeholder / empty visual / raw Markdown: {r.get('no_placeholder_status')} / {r.get('no_empty_visual_status')} / {r.get('no_raw_markdown_status')}",
        f"- KPI / pagination / source appendix / final PDF: {r.get('kpi_layout_status')} / {r.get('pagination_status')} / {r.get('source_appendix_status')} / {r.get('final_pdf_status')}",
        f"- Visual composition: {r.get('visual_art_direction')}",
        f"- Text defects: {r.get('text_defects') or 'none'}; bad whitespace pages: {r.get('bad_whitespace_pages') or 'none'}",
        f"- Telecom validation: {r.get('telecom_validation') or 'not applicable'}",
        f"- Structural difference: {r.get('meaningful_structural_difference')}", "",
    ]
(root / "demo-10-reports-qa.md").write_text("\n".join(lines), encoding="utf-8")
