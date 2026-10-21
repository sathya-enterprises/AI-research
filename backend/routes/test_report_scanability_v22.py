import re

import pytest
import routes.report as report_module


def _paragraph(label: str, source: int, value: str) -> str:
    return (
        f"{label} represents an important structural mechanism for the banking sector and affects operating "
        "decisions across the forecast horizon. Institutions need to assess customer behavior, funding conditions, "
        "pricing trade-offs, and the interaction between balance-sheet resilience and sustainable profitability. "
        "The evidence should be interpreted in context and not treated as a universal outcome for every lender. "
        f"The reported metric is {value} [S{source}]."
    )


@pytest.mark.asyncio
async def test_scanability_pass_reformats_prose_only_and_preserves_evidence(monkeypatch):
    paragraphs = [
        _paragraph("Funding pressure", 1, "11.5%"),
        _paragraph("Pricing decisions", 2, "12.5%"),
        _paragraph("Margin outlook", 3, "13.5%"),
        _paragraph("Risk controls", 4, "14.5%"),
    ]
    report_text = "## Banking outlook\n\n### Financial dynamics\n\n" + "\n\n".join(paragraphs) + "\n\n## Other section\n\nShort close.\n"

    async def fake_call(_system, prompt):
        body = prompt.split("BODY TO REFORMAT:", 1)[1].strip()
        labels = ["Funding insight", "Pricing insight", "Margin insight", "Risk insight"]
        return "\n".join(f"- **{labels[i] if i < len(labels) else 'Insight'}:** {line.strip()}" for i, line in enumerate(body.split("\n\n")))

    monkeypatch.setattr(report_module, "_call_llm_plain", fake_call)
    updated, result = await report_module._apply_report_scanability_pass(report_text, "banking outlook", "in_depth")

    assert result["targets"] >= 1
    assert result["accepted"] >= 1
    assert "- **Funding insight:**" in updated
    for citation in ("[S1]", "[S2]", "[S3]", "[S4]"):
        assert citation in updated
    for metric in ("11.5%", "12.5%", "13.5%", "14.5%"):
        assert metric in updated
    assert result["after"]["long_paragraph_count"] < result["before"]["long_paragraph_count"]
