from utils.research_scope import build_scope_fingerprint, enforce_scope_and_evidence


def test_banking_fingerprint_preserves_periods_and_dimensions():
    scope = build_scope_fingerprint(
        "Compare Indian private-bank credit cycles in 2008-09, 2014-18, 2020-22 and 2025-26 across funding, asset quality, regulation, capital, loan composition and primary risk."
    )
    assert scope.research_type == "historical_comparison"
    assert set(scope.comparison_periods) == {"2008", "2009", "2014", "2018", "2020", "2022", "2025", "2026"}
    assert {"funding", "asset_quality", "regulation", "capital", "loan_composition", "primary_risk"} <= set(scope.required_dimensions)


def test_off_topic_section_is_removed():
    question = "Assess the sustainability of Indian private-bank credit cycles across funding, asset quality, capital and regulation."
    report = "## Credit cycle comparison\n\nIndian bank funding and asset quality changed across the cycle [S1].\n\n## G7 and dollar dominance\n\nG7 currency policy and meme stocks matter to portfolio strategy."
    result, audit = enforce_scope_and_evidence(report, question, None, [{"title": "Indian bank cycle", "fullContent": "Indian bank funding and asset quality changed across the cycle."}])
    assert "G7 and dollar dominance" not in result
    assert audit["scope"]["dropped_sections"]


def test_prior_conversation_is_not_evidence():
    result, audit = enforce_scope_and_evidence(
        "## Findings\n\nPrior Conversation says revenue was 40% higher [S1].",
        "Evaluate quick-commerce unit economics and contribution per order.",
        None,
        [{"title": "Quick commerce economics", "fullContent": "Quick commerce contribution per order depends on delivery cost."}],
    )
    assert "Prior Conversation" not in result
    assert audit["fingerprint"]["research_type"] == "unit_economics"


def test_references_are_terminal_and_inline_topic_leakage_is_removed():
    result, audit = enforce_scope_and_evidence(
        "## Credit cycle comparison\n\nIndian bank funding and capital changed across periods [S1].\nClimate and ESG risk pricing became central to alternative investment funds [S2].\n\n### References\n\n- RBI cycle study [S1]\n\n### Portfolio recommendations\n\nBuy sector leaders.",
        "Compare Indian private-bank credit cycles across funding and capital.",
        None,
        [
            {"title": "Indian bank cycle", "fullContent": "Indian bank funding and capital changed across periods."},
            {"title": "Climate fund note", "fullContent": "Climate and ESG risk pricing became central to alternative investment funds."},
        ],
    )
    assert "Climate and ESG" not in result
    assert "Portfolio recommendations" not in result
    assert any(item["reason"] == "after_terminal_references" for item in audit["scope"]["dropped_sections"])


def test_observational_validation_does_not_mutate_report():
    report = "## In scope\n\nIndian bank funding changed [S1].\n\n## Off topic\n\nUnrelated portfolio advice."
    result, audit = enforce_scope_and_evidence(report, "Assess Indian bank funding", None, [{"source_id": "S1", "title": "Bank filing", "fullContent": "Indian bank funding changed."}], repair=False)
    assert result == report
    assert audit["scope"]["dropped_sections"]


def test_claim_audit_uses_canonical_source_ids_not_positions():
    question = "Analyze Infosys AI revenue and operating margins."
    report = "Infosys reported higher AI revenue and operating margins [S7]."
    sources = [
        {"source_id": "S7", "title": "Infosys annual results", "fullContent": "Infosys AI revenue and operating margins increased."},
        {"source_id": "S8", "title": "Unrelated source", "fullContent": "Unrelated market commentary."},
    ]
    _, audit = enforce_scope_and_evidence(report, question, None, sources)
    assert audit["claims"]["claims"][0]["direct_support"] == ["S7"]
