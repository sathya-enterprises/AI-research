"""Generate offline planner-only generalization artifacts."""
from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from utils.query_contract import plan_query
from utils.test_query_contract import GENERALIZATION_QUERIES


def main():
    cases = []
    for query in GENERALIZATION_QUERIES:
        c = plan_query(query).to_dict()
        cases.append({"query": query, "contract": c, "required_dimensions": [d["name"] for d in c["required_dimensions"]], "derived_dimensions": [d["name"] for d in c["derived_dimensions"]], "optional_dimensions": [d["name"] for d in c["optional_dimensions"]], "not_applicable_dimensions": [d["name"] for d in c["not_applicable_dimensions"]], "freshness_requirements": {d["name"]: d["freshness_requirement"] for d in c["required_dimensions"]}, "preferred_source_roles": {d["name"]: d["preferred_source_roles"] for d in c["required_dimensions"]}})
    by_query = {x["query"]: x for x in cases}
    result = {"test": "query -> contract -> dimensions -> policy", "live_research_performed": False, "pdfs_generated": False, "query_count": len(cases), "materially_different_contracts": len({tuple(x["required_dimensions"]) for x in cases}) >= 10, "semantic_checks": {"earnings_has_financial_dimension": "financial performance" in by_query[GENERALIZATION_QUERIES[0]]["required_dimensions"], "cement_has_industry_economics": "industry economics" in by_query[GENERALIZATION_QUERIES[1]]["required_dimensions"], "real_estate_has_vacancy_rents": "vacancy and rents" in by_query[GENERALIZATION_QUERIES[6]]["required_dimensions"], "semiconductor_has_supply_chain_and_china_plus_one": {"supply-chain structure", "China+1 exposure"}.issubset(set(by_query[GENERALIZATION_QUERIES[8]]["required_dimensions"])), "ambiguous_requires_clarification": by_query[GENERALIZATION_QUERIES[-1]]["contract"]["clarification_required"], "no_arpu_in_non_telecom": all("ARPU/tariff economics" not in x["required_dimensions"] for x in cases), "no_universal_cash_flow": all("cash flow" not in x["required_dimensions"] for x in cases), "no_universal_risk_or_competition": all("risk and uncertainty" not in x["required_dimensions"] and "competitive context" not in x["required_dimensions"] for x in cases)}, "cases": cases}
    (ROOT / "architecture-generalization-test.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = ["# Architecture Generalization Test", "", "Offline planner-only fixture: no provider calls, live research, or PDFs were run.", "", f"Cases: {len(cases)}", f"Materially different contracts: {result['materially_different_contracts']}", "", "Semantic checks:"]
    lines.extend(f"- {name}: {value}" for name, value in result["semantic_checks"].items())
    lines.extend(["", "| # | Query | Required | Derived | Optional | Not applicable |", "|---:|---|---|---|---|---|"])
    for i, row in enumerate(cases, 1):
        cell = lambda xs: ", ".join(xs) or "—"
        lines.append(f"| {i} | {row['query'].replace('|', '/') } | {cell(row['required_dimensions'])} | {cell(row['derived_dimensions'])} | {cell(row['optional_dimensions'])} | {cell(row['not_applicable_dimensions'])} |")
    (ROOT / "architecture-generalization-test.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
