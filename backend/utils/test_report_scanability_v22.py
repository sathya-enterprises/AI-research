from utils.report_scanability import (
    audit_scanability,
    select_scanability_targets,
    strip_generation_markers,
    validate_reformatted_block,
)


def _p(label: str, cite: str, number: str) -> str:
    return (
        f"{label} reflects a distinct financial mechanism with meaningful operational consequences for the "
        "banking sector. Institutions must consider funding mix, pricing decisions, changes in customer "
        "behavior, and the effect on profitability over the forecast period. These relationships should be "
        f"evaluated against the available evidence and uncertainty, rather than treated as a universal rule. "
        f"The reported value is {number} and should be interpreted in context {cite}."
    )


def test_strips_leaked_continuation_control_lines():
    text = "## Regulation\n\n[[SECTION]] Regulation\n\n### Capital rules\n\nSome content.\n\n[[END SECTION]]\n"
    cleaned = strip_generation_markers(text)
    assert "[[SECTION]]" not in cleaned
    assert "[[END SECTION]]" not in cleaned
    assert "### Capital rules" in cleaned


def test_scanability_audit_identifies_prose_heavy_sections():
    body = "\n\n".join(_p(f"Mechanism {i}", "[S1]", f"{i+10}%") for i in range(1, 5))
    text = f"## Main section\n\n### Funding economics\n\n{body}\n"
    audit = audit_scanability(text)
    assert audit["long_paragraph_count"] >= 4
    assert any(x["heading"] == "Funding economics" for x in audit["paragraph_heavy_sections"])
    targets = select_scanability_targets(text, "in_depth")
    assert targets
    assert targets[0]["heading"] == "Funding economics"


def test_reformat_accepts_new_bullets_when_evidence_is_preserved():
    original = "\n\n".join([
        _p("Funding", "[S1]", "11.5%"),
        _p("Pricing", "[S2]", "12.5%"),
        _p("Margins", "[S3]", "13.5%"),
    ])
    rewritten = (
        "- **Funding:** Institutions must consider funding mix, customer behavior, and the operational consequences "
        "for the banking sector over the forecast period. These relationships should be evaluated against the "
        "available evidence and uncertainty, rather than treated as universal rules. The reported value is 11.5% "
        "and should be interpreted in context [S1].\n"
        "- **Pricing:** Institutions must consider pricing decisions, changes in customer behavior, and the effect "
        "on profitability over the forecast period. These relationships should be evaluated against available "
        "evidence and uncertainty, rather than treated as a universal rule. The reported value is 12.5% and should "
        "be interpreted in context [S2].\n"
        "- **Margins:** Institutions must consider changes in profitability, funding mix, and forecast uncertainty. "
        "These relationships should be evaluated against available evidence and uncertainty, rather than treated "
        "as a universal rule. The reported value is 13.5% and should be interpreted in context [S3]."
    )
    ok, reason = validate_reformatted_block(original, rewritten)
    assert ok, reason


def test_reformat_rejects_missing_citations_or_new_numeric_claims():
    original = _p("Funding", "[S1]", "11.5%") * 3
    missing_citation = "- **Finding:** Funding conditions remain relevant to profitability. The reported value is 11.5%."
    ok, reason = validate_reformatted_block(original, missing_citation)
    assert not ok
    assert reason in {"too_much_content_removed", "citation_loss", "no_scanability_gain"}

    source = "\n\n".join([_p("Funding", "[S1]", "11.5%"), _p("Pricing", "[S2]", "12.5%"), _p("Margins", "[S3]", "13.5%")])
    with_new_number = (
        "- **Funding:** Institutions must consider funding mix and customer behavior over the forecast period. "
        "These relationships should be evaluated against the available evidence and uncertainty, rather than "
        "treated as a universal rule. The reported value is 11.5% and should be interpreted in context [S1].\n"
        "- **Pricing:** Institutions must consider pricing decisions and their effect on profitability. These "
        "relationships should be evaluated against the available evidence and uncertainty, rather than treated "
        "as a universal rule. The reported value is 12.5% and should be interpreted in context [S2].\n"
        "- **Margins:** Institutions must consider profitability and forecast uncertainty. These relationships "
        "should be evaluated against the available evidence and uncertainty, rather than treated as a universal "
        "rule. The reported value is 13.5% and should be interpreted in context [S3].\n"
        "- **Additional:** A new 99.9% value is introduced. It must be rejected because it was not present in the original evidence."
    )
    ok, reason = validate_reformatted_block(source, with_new_number)
    assert not ok
    assert reason == "new_numeric_anchor"
