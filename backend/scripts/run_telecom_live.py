"""Run and archive the required live Indian telecom research regression."""
from __future__ import annotations

import json
import os
from pathlib import Path

import httpx


QUESTION = (
    "Assess Indian telecom economics after tariff and network changes, including ARPU, "
    "subscriber mix, churn, 4G/5G, spectrum, network capex, depreciation, operating leverage, "
    "free cash flow, balance sheet effects, operator strategies and the key variables that determine future economics."
)


def main() -> None:
    base = Path(__file__).resolve().parents[2] / "artifacts" / "telecom-live"
    base.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=httpx.Timeout(connect=30, read=900, write=30, pool=30)) as client:
        response = client.post("http://127.0.0.1:10000/api/chat/report", json={"question": QUESTION})
        response.raise_for_status()
        payload = response.json()
    (base / "report-response.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    telemetry = {
        "question": QUESTION,
        "research_run_id": payload.get("research_run_id"),
        "research_dimensions": (payload.get("researchCompleteness") or {}).get("research_dimensions", []),
        "research_completeness": payload.get("researchCompleteness", {}),
        "search_telemetry": payload.get("searchTelemetry", {}),
        "research_store": payload.get("researchStore", {}),
        "sources": payload.get("sources", []),
        "citation_integrity": (payload.get("researchScope") or {}).get("citations", {}),
        "source_accounting": (payload.get("researchScope") or {}).get("sourceAccounting", {}),
        "depth": (payload.get("researchScope") or {}).get("depth", {}),
        "report_page_count": None,
    }
    # Required handoff names; keep the legacy filename as a compatibility copy
    # for the existing archived QA scripts.
    telemetry_json = json.dumps(telemetry, ensure_ascii=False, indent=2)
    (base / "telecom_research_telemetry.json").write_text(telemetry_json, encoding="utf-8")
    (base / "research-telemetry.json").write_text(telemetry_json, encoding="utf-8")
    print(json.dumps({"status": "ok", "research_run_id": payload.get("research_run_id"), "sources": len(payload.get("sources", [])), "report_chars": len(payload.get("report", "")), "artifact": str(base / "research-telemetry.json")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
