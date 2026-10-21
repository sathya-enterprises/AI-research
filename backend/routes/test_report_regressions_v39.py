"""Regression tests for v39 continuation, table, and publication QA fixes."""
from __future__ import annotations

import asyncio
import re

import routes.report as report_mod
from utils.report_quality import audit_report_publication


def test_semantically_duplicate_structured_tables_are_removed_and_refs_remapped():
    table_a = {
        "type": "table", "title": "Credit and Asset Quality",
        "columns": ["Metric", "FY26 actual", "FY27 projection"],
        "rows": [
            ["Credit Growth", "15.9%", "11.4%"],
            ["Gross NPAs", "2.0–2.5%", "2.0–2.5%"],
            ["Market Expansion CAGR", "9.7%", "9.7%"],
            ["Real GDP Growth", "7.7%", "6.4–7.0%"],
        ],
    }
    table_b = {
        "type": "table", "title": "Macroeconomic Projections",
        "columns": ["Indicator", "Observed Range", "Expected Range"],
        "rows": [
            ["Bank Credit Growth", "15.9%", "11.4%"],
            ["System-wide Gross NPA", "2.0–2.5%", "2.0–2.5%"],
            ["Banking Market CAGR", "9.7%", "9.7%"],
            ["GDP Growth", "7.7%", "6.4–7.0%"],
        ],
    }
    text = "## Outlook\n\n[CHART_1]\n\n[CHART_2]\n"
    result_text, result_charts = report_mod._dedupe_semantically_duplicate_table_charts(text, [table_a, table_b])
    assert len(result_charts) == 1
    assert result_text.count("[CHART_1]") == 1
    assert "[CHART_2]" not in result_text


def test_structured_table_audit_flags_terminal_2031_in_fy27_projection_column():
    chart = {
        "type": "table", "title": "Growth Outlook",
        "columns": ["Metric", "FY25 / FY26 actuals", "FY27 projection / target"],
        "rows": [
            ["Scheduled bank deposits", "USD 2.725 trillion (May 2026)", "USD 5.11 trillion (Terminal 2031)"],
            ["Gross advances", "INR 213.6 trillion (FY26)", "USD 4.27 trillion (Terminal 2031)"],
            ["Credit growth", "15.9% (FY26)", "11.0%–11.7% (FY27)"],
        ],
    }
    audit = audit_report_publication("## Outlook\n\n[CHART_1]", charts=[chart], sources=[])
    issues = audit["timeframeLabelMismatches"]
    assert len(issues) == 2
    assert any("forecast horizon" in flag for flag in audit["flags"])
    gated = report_mod._apply_deterministic_audit_gate(
        {"status": "PASS", "blocking": False, "findings": []},
        {"publicationAudit": audit, "chartDensity": {"exceptionsOrMisses": []}},
    )
    assert gated["status"] == "FAIL"
    assert gated["blocking"] is True

    # Deterministic publication blockers must override a Gemini WARNING too,
    # not only PASS; preserve the independent reviewer result for diagnostics.
    gated_warning = report_mod._apply_deterministic_audit_gate(
        {"status": "WARNING", "blocking": False, "findings": []},
        {"publicationAudit": audit, "chartDensity": {"exceptionsOrMisses": []}},
    )
    assert gated_warning["status"] == "FAIL"
    assert gated_warning["modelReviewStatus"] == "WARNING"
    assert gated_warning["blocking"] is True


def test_heading_attached_body_is_deduped_across_paraphrased_numeric_claims():
    first = (
        "- Foreign Capital Influx: International investor confidence remains robust, evidenced by "
        "$14–15 billion in foreign inflows directed toward Indian banking, credit expansion, and "
        "digital lending platforms in 2025 [S8]."
    )
    first_module = (
        "### Foreign Capital Inflows and Digital Lending Catalysts\n"
        "Beyond traditional household intermediation, the structural expansion of India's banking "
        "sector is heavily catalyzed by robust international capital commitments and technology-driven "
        "ecosystems. Investor confidence in the country's financial architecture reached a historic "
        "milestone in 2025, with all-time high foreign inflows of $14–15 billion pouring directly into "
        "the banking sector. [S8]"
    )
    duplicate_module = (
        "### Foreign Capital Inflows and Long-Term Market Scalability\n"
        "Beyond traditional intermediation, the banking landscape is reinforced by substantial "
        "international capital commitments and structural formalization. Global investor confidence "
        "is reflected in all-time-high foreign inflows of $14–15 billion recorded into India's banking "
        "sector, propelled directly by accelerated credit deployment and digital lending infrastructure."
    )
    # Force the long-report hygiene path while using a valid table as inert
    # padding. The important case is a Markdown heading followed by its body
    # on a single newline, which used to cause the whole block to be skipped.
    padding = "| Row | Note |\n|---|---|\n" + "\n".join(
        f"| {i} | Distinct neutral validation row {i} |" for i in range(1, 150)
    )
    report = (
        padding + "\n\n## Primary Growth Drivers\n\n" + first + "\n\n" + first_module
        + "\n\n" + duplicate_module
    )
    cleaned = report_mod._dedupe_redundant_report_content(report)
    assert cleaned.count("$14–15 billion") == 1  # the canonical source claim remains; repeated continuations are removed
    assert "Foreign Capital Inflows and Long-Term Market Scalability" not in cleaned
    assert "all-time-high foreign inflows" not in cleaned


def test_dedupe_does_not_merge_same_value_claims_from_different_years():
    first = "Credit growth was 11.4% in FY2027, based on the verified annual series [S3]."
    second = "Credit growth was 11.4% in FY2028 under a separate forecast [S5]."
    padding = "| Row | Note |\n|---|---|\n" + "\n".join(
        f"| {i} | Distinct neutral validation row {i} |" for i in range(1, 150)
    )
    report = "## Credit Outlook\n\n" + first + "\n\n" + second + "\n\n" + padding
    cleaned = report_mod._dedupe_redundant_report_content(report)
    assert "FY2027" in cleaned and "FY2028" in cleaned
    assert "separate forecast" in cleaned


async def _fake_continuation(prompt: str, tier: str):
    selected = re.findall(r"\[\[SECTION TO EXPAND\]\]\s+([^\n]+)", prompt)
    # Approximate the model response with substantive, structured but distinct
    # modules. Keep each reply proportional to the word budget the prompt gave.
    m = re.search(r"about (\d+) words only", prompt)
    words = max(100, min(650, int(m.group(1)) if m else 250))
    body = "Evidence-backed distinct analysis describes a separate causal mechanism, the affected stakeholder, and its decision implication. "
    body = (body * ((words * 7 // len(body)) + 1))[: words * 7]
    blocks = []
    for i, heading in enumerate(selected):
        blocks.append(f"[[SECTION: {heading}]]\n### Focused module {i + 1}\n{body}\n[[END SECTION]]")
    return "\n\n".join(blocks), "test-model"


def test_page_budget_continuation_scales_module_count_to_remaining_gap(monkeypatch):
    prompts = []

    async def capture(prompt, tier):
        prompts.append(prompt)
        return await _fake_continuation(prompt, tier)

    monkeypatch.setattr(report_mod, "_call_gemini_plain_continuation", capture)
    report = "\n\n".join([f"## Section {i}\nExisting concise content for section {i}." for i in range(1, 4)])
    expanded = asyncio.run(report_mod._expand_report_to_tier(
        report_text=report,
        question="Test report depth behaviour",
        report_tier="brief",
        report_plan={"sections": [{"heading": f"Section {i}"} for i in range(1, 4)]},
        evidence_text="Verified source evidence and distinct contextual findings.",
        target_override=len(report) + 9000,
        max_passes_override=4,
    ))
    counts = [len(re.findall(r"\[\[SECTION TO EXPAND\]\]", prompt)) for prompt in prompts]
    assert prompts
    assert counts[0] == 3
    assert counts[-1] <= counts[0]
    assert len(expanded) <= len(report) + 9_000  # accepted continuation must stay within the target override
