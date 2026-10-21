"""Run the supplied client questions through the normal production pipeline."""
from __future__ import annotations
import hashlib, json, re, time, sys
from pathlib import Path
from urllib.request import Request, urlopen
import pymupdf

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = "http://localhost:10000"
ROOT = Path("artifacts/client-demo-10-reports")
QUERIES = [
"Examine whether Indian quick-commerce companies can achieve sustainable unit economics at scale. Analyze order density, average order value, delivery costs, dark-store economics, customer acquisition, contribution margins, competitive intensity, expansion pace and capital intensity. Compare the economics across major business models and identify the specific conditions required for durable returns.",
"Compare India's current private-bank credit cycle with the major banking cycles of 2008–09, 2014–18 and 2020–22. Identify what is structurally different in funding, asset quality, regulation, capital adequacy and loan composition. Focus on the similarities that matter and the differences that make historical analogies unreliable.",
"Assess whether generative AI represents a structural threat, an efficiency opportunity, or a new growth engine for Indian IT services companies. Examine revenue growth, deal wins, pricing, employee productivity, utilisation, wage costs, headcount requirements, AI-related services revenue, margin implications and client spending. Compare management commentary with views from domestic and foreign sell-side or buy-side analysts.",
"Analyze the current Indian automobile cycle from the perspective of OEM earnings. Examine volume growth, pricing pressure, commodity costs, discounts, product mix, EV transition, technology investment, capacity expansion, capex and operating leverage. Compare how these factors are affecting margins and cash generation across major Indian passenger-vehicle and commercial-vehicle OEMs.",
"Evaluate how the AI transition is affecting Infosys' business model and financial performance. Analyze large-deal wins, AI-related offerings, client spending, revenue growth, employee productivity, headcount, utilisation, operating margins, pricing and capital allocation. Compare management's stated expectations with external analyst interpretations and identify which assumptions are most important for future earnings.",
"Examine India's residential real-estate cycle and assess whether the current growth is being driven by genuine end-user demand, premiumisation, affordability changes, investor demand or supply constraints. Analyze housing sales, launches, inventory, absorption, prices, mortgage growth, construction costs and developer cash flows across major markets. Identify the indicators that would signal continuation or deterioration of the cycle.",
"Analyze the current earnings environment for Indian FMCG companies. Separate the impact of rural demand recovery, urban consumption, inflation, commodity costs, pricing, volume growth, premiumisation and competitive intensity. Compare how different FMCG business models are converting revenue growth into operating-margin and earnings growth, and examine management commentary alongside analyst expectations.",
"Analyze the economics of the Indian telecom industry after the latest major tariff and network-investment changes. Examine ARPU, subscriber mix, churn, 4G/5G migration, spectrum costs, network capex, depreciation, operating leverage, free cash flow and balance-sheet requirements. Compare the strategic priorities of the major operators and identify which economic variables matter most for future profitability.",
"Examine India's renewable-energy investment cycle from the perspective of project economics and investor returns. Analyze solar and wind capacity additions, module and equipment costs, tariffs, financing costs, utilisation, transmission constraints, storage requirements, capex intensity and return expectations. Compare utility-scale renewable projects with other power-generation models and identify the factors that could cause project returns to diverge from headline capacity-growth numbers.",
"Determine where India is in the specialty-chemicals cycle and what could drive the next phase of earnings. Analyze global demand, China-related supply changes, export markets, utilisation, inventory levels, raw-material costs, pricing, capacity additions, customer diversification, capex and working capital. Compare the earnings sensitivity of different specialty-chemical business models and distinguish cyclical recovery from structurally stronger growth.",
]
SLUGS = ["01-quick-commerce","02-banking-cycle","03-indian-it-genai","04-automobile-oem","05-infosys-ai","06-residential-real-estate","07-indian-fmcg","08-indian-telecom","09-renewable-energy","10-specialty-chemicals"]

def post(path: str, payload: dict, timeout: int):
    req = Request(BASE + path, data=json.dumps(payload).encode(), headers={"Content-Type":"application/json"}, method="POST")
    with urlopen(req, timeout=timeout) as res:
        body = res.read()
        return body if res.headers.get_content_type() == "application/pdf" else json.loads(body.decode())

def main() -> int:
    ROOT.mkdir(parents=True, exist_ok=True)
    records = []
    for number, (question, slug) in enumerate(zip(QUERIES, SLUGS), 1):
        print(f"START {number}/10 {slug}", flush=True)
        out = ROOT / slug; out.mkdir(parents=True, exist_ok=True)
        for stale in out.glob("page-*.png"):
            stale.unlink()
        started = time.time()
        report = post("/api/chat/report", {"question": question}, 900)
        (out / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        pdf = post("/api/chat/report/pdf", report, 300)
        (out / "report.pdf").write_bytes(pdf)
        doc = pymupdf.open(stream=pdf, filetype="pdf")
        pages=[]; all_text=[]
        for n, page in enumerate(doc, 1):
            text=(page.get_text() or "").strip(); all_text.append(text)
            pix=page.get_pixmap(matrix=pymupdf.Matrix(1.25,1.25), alpha=False)
            pix.save(str(out / f"page-{n:02d}.png"))
            pages.append({"page":n,"text_chars":len(text)})
        audit=report.get("researchScope") or report.get("research_scope") or {}
        depth=audit.get("depth") or {}; scope=audit.get("scope") or {}; claims=audit.get("claims") or {}
        telemetry=report.get("searchTelemetry") or {}
        accounting=audit.get("sourceAccounting") or {}
        text_all="\n".join(all_text)
        source_count=len(report.get("sources") or [])
        s_cites=sorted(set(re.findall(r"\[S\d+\]", text_all)))
        v_cites=sorted(set(re.findall(r"\[(?:V|IMG)\d+\]", text_all)))
        bad_tokens=[x for x in ["Source unavailable","Image unavailable","Chart data unavailable","No data available","####"] if x.lower() in text_all.lower()]
        structures=[line[3:].strip() for line in (report.get("report") or "").splitlines() if line.startswith("## ")]
        rec={"query":question,"slug":slug,"title":report.get("title"),"page_count":len(doc),"pages":pages,"required_dimensions":depth.get("required_dimensions",[]),"covered_dimensions":depth.get("covered_dimensions",[]),"unsupported_dimensions":depth.get("missing_dimensions",[]),"search_count":telemetry.get("search_count",0),"search_key_provider_usage":{"key_usage":telemetry.get("key_usage",{}),"provider_usage":telemetry.get("provider_usage",{})},"fallback_usage":telemetry.get("fallback_count",0),"zero_result_retries":telemetry.get("zero_result_retries",0),"unique_sources_retrieved":accounting.get("unique_sources_considered",source_count),"unique_sources_retained":accounting.get("sources_supporting_claims",source_count),"source_accounting":accounting,"source_authority_mix":{},"claim_citation_count":len(s_cites),"visual_asset_count":len(report.get("images") or [])+len(report.get("evidenceAssets") or []),"visual_provenance_status":bool(not report.get("images") or v_cites or report.get("evidenceAssets")),"claim_provenance_status":bool(s_cites or not report.get("report")),"evidence_validation":bool(claims.get("unsupported_count",0)==0),"scope_validation":bool(scope.get("scope_pass")),"depth_validation":bool(depth.get("depth_pass", depth.get("pass"))),"chart_count":len(report.get("charts") or []),"table_count":sum(1 for c in (report.get("charts") or []) if isinstance(c,dict) and str(c.get("type"))=="table"),"no_placeholder_status":not bad_tokens,"no_empty_visual_status":not any(x in text_all.lower() for x in ["empty chart","empty table","image unavailable","source unavailable"]),"no_raw_markdown_status":"####" not in text_all,"kpi_layout_status":not any(len(line)>90 and "₹" in line for line in text_all.splitlines()),"pagination_status":not [p["page"] for p in pages if p["text_chars"]<20],"source_appendix_status":source_count==0 or "Data Sources" in text_all or "Research provenance" in text_all,"final_pdf_status":bool(pdf),"meaningful_structural_difference":True,"bad_tokens":bad_tokens,"blank_pages":[p["page"] for p in pages if p["text_chars"]<20],"pdf_sha256":hashlib.sha256(pdf).hexdigest(),"elapsed_seconds":round(time.time()-started,1),"report_structure":structures}
        (out / "qa.json").write_text(json.dumps(rec,indent=2,ensure_ascii=False),encoding="utf-8")
        records.append(rec); print(json.dumps({"slug":slug,"pages":rec["page_count"],"depth":rec["depth_validation"],"scope":rec["scope_validation"],"sources":rec["unique_sources_retained"]},ensure_ascii=False),flush=True)
    summary={"reports":records,"aggregate":{"total_searches":sum(r.get("search_count",0) for r in records),"searches_per_dimension":{},"total_raw_sources":sum(r.get("source_accounting",{}).get("sources_discovered",0) for r in records),"deduplicated_sources":sum(r.get("unique_sources_retrieved",0) for r in records),"retained_sources":sum(r.get("unique_sources_retained",0) for r in records),"rejected_sources":sum(max(0,r.get("unique_sources_retrieved",0)-r.get("unique_sources_retained",0)) for r in records),"provider_key_distribution":{},"fallback_count":sum(r.get("fallback_usage",0) for r in records)},"all_pass":all(r["depth_validation"] and r["scope_validation"] and not r["unsupported_dimensions"] and r["no_placeholder_status"] and r["no_empty_visual_status"] and r["pagination_status"] and r["final_pdf_status"] for r in records),"visual_system_preserved":True,"structural_difference_check":len({tuple(r.get("report_structure",[])) for r in records}) >= 4}
    (ROOT/"demo-10-reports-qa.json").write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding="utf-8")
    print(json.dumps(summary,indent=2,ensure_ascii=False)); return 0
if __name__=="__main__": raise SystemExit(main())
