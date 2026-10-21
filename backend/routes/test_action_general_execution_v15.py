from routes.report import (
    _action_edit_validation,
    _action_protected_evidence,
    _action_relevant_cross_sections,
    _action_requires_external_research,
)


def test_conceptual_bold_labels_are_not_false_entities():
    original = '''## Key Growth Drivers & Investments

**Capital Injections and Subsidies:** government support can accelerate capacity build-out [S1].

**Automotive and Mobile Demand Pull:** downstream demand supports scale [S2].

**Global Equipment and Foundry Partnerships:** partnerships can improve access to technology [S3].

**Supply Chain Diversification:** sourcing resilience can reduce concentration risk [S4].'''
    edited = '''## Key Growth Drivers & Investments

- **Capital support:** public incentives can accelerate capacity build-out [S1].
- **Demand pull:** downstream demand supports scale [S2].
- **Foundry partnerships:** external partnerships can improve technology access [S3].
- **Supply resilience:** diversified sourcing can reduce concentration risk [S4].'''
    ok, problems = _action_edit_validation(
        original,
        edited,
        'Reorganize this section into concise points while preserving the evidence and citations.',
    )
    assert ok, problems
    assert not any('preserved entities disappeared' in p for p in problems)


def test_alphanumeric_tokens_are_not_false_numeric_claims():
    original = '## Product Strategy\n\nThe 24design framework is already used in this section [S1].'
    edited = '## Product Strategy\n\nThe 24design framework is retained and clarified [S1].'
    ok, problems = _action_edit_validation(original, edited, 'Improve clarity while preserving existing evidence.')
    assert ok, problems
    assert not any('new unsupported numeric values' in p for p in problems)


def test_action_detects_research_bearing_update():
    assert _action_requires_external_research(
        'Update this section with the latest evidence, add new supporting data and citations.',
        'Existing analysis [S1].',
    ) is True


def test_action_does_not_search_for_pure_formatting_change():
    assert _action_requires_external_research(
        'Convert this section into concise bullet points and improve readability without adding facts.',
        'Existing analysis [S1].',
    ) is False


def test_cross_section_context_is_loaded_when_heading_is_named():
    report = '''## Market Outlook & Key Metrics\n\nCredit growth is the key demand driver.\n\n## Key Growth Drivers & Investments\n\nInvestment is accelerating.\n\n## Risk Assessment\n\nFunding risk remains material.'''
    context = _action_relevant_cross_sections(
        report,
        'Key Growth Drivers & Investments',
        'Align this section with the Market Outlook & Key Metrics discussion and preserve consistency.',
        max_chars=5000,
    )
    assert 'CROSS-SECTION CONTEXT' in context
    assert 'Market Outlook & Key Metrics' in context
    assert 'Credit growth is the key demand driver.' in context


def test_action_protected_evidence_still_captures_real_named_entities():
    manifest = _action_protected_evidence('**Reliance Retail** [S1] and **Tata Motors** [S2] expanded capacity.')
    assert 'Reliance Retail' in manifest['entities']
    assert 'Tata Motors' in manifest['entities']
