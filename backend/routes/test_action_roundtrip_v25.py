from pathlib import Path

from routes.report import (
    _find_explicit_action_section,
    _is_additive_table_request,
    _normalise_action_heading,
    _validate_new_action_table,
    _action_edit_validation,
    _action_requires_external_research,
    _action_requests_image_removal,
    _action_requests_chart_removal,
    _remove_action_media_markers,
)


def test_legacy_heading_normalizer_and_selected_target_are_available():
    sections = [
        {"heading": "Geopolitical & Operational Risks"},
        {"heading": "Key Trends"},
    ]
    assert _normalise_action_heading("3. Geopolitical & Operational Risks") == "geopolitical & operational risks"
    assert _find_explicit_action_section("3. Geopolitical & Operational Risks", sections) == 0


def test_additive_table_intent_does_not_override_table_replacement():
    assert _is_additive_table_request('Target section: "Risks"\nInstruction: here add one table also in this section')
    assert not _is_additive_table_request('Replace the existing table with a cleaner comparison table')
    assert not _is_additive_table_request('Update the table and improve the section prose')


def test_additive_table_rejects_unsupported_metrics_and_sources():
    original = 'Energy procurement concerns are 34% and 58% [S1].'
    table = '| Risk | Evidence |\n|---|---|\n| Energy | 34% [S1] |\n| New | 72% [S9] |'
    ok, problems = _validate_new_action_table(original, table)
    assert not ok
    assert any('unsupported source' in p for p in problems)
    assert any('unsupported numeric' in p for p in problems)


def test_additive_table_can_reuse_report_wide_evidence_without_rewriting_original():
    original = '## Risks\n\nFunding stress remains elevated [S1].'
    table = '| Risk | Current evidence |\n|---|---|\n| Funding | 34% [S2] |\n| Credit | 58% [S2] |'
    ok, problems = _validate_new_action_table(original, table, evidence_source='Report evidence: 34% 58% [S2]')
    assert ok, problems


def test_action_rejects_citations_not_in_report_or_evidence_dossier():
    original = '## Risk\n\nFunding risk is material [S1].'
    edited = '## Risk\n\nFunding risk is material [S1]. New claim [S99].'
    ok, problems = _action_edit_validation(original, edited, 'Update the section.', evidence_source=original)
    assert not ok
    assert any('unsupported source citations' in p for p in problems)


def test_action_accepts_new_citation_when_fresh_dossier_provides_it():
    original = '## Risk\n\nFunding risk is material [S1].'
    edited = '## Risk\n\nFunding risk is material [S1]. Fresh verified claim [S2].'
    ok, problems = _action_edit_validation(original, edited, 'Update the section with fresh evidence.', evidence_source=original + '\n[S2] Fresh source evidence')
    assert ok, problems


def test_frontend_roundtrip_includes_and_merges_images_sources_and_presentation():
    source = Path(__file__).parents[1] / 'frontend' / 'src' / 'components' / 'GrowthGradualChat.tsx'
    text = source.read_text(encoding='utf-8')
    assert 'images: reportData.images || []' in text
    assert 'sources: reportData.sources || []' in text
    assert 'images: updatedImages || reportData.images' in text
    assert 'sources: updatedSources || reportData.sources' in text
    assert 'Only the matched section is changed' in text
    assert 'Other sections, charts, images and sources are preserved.' in text


def test_generic_update_for_formatting_does_not_trigger_web_research():
    assert not _action_requires_external_research(
        "Update this section into concise bullets without adding facts.",
        "Existing evidence [S1].",
    )


def test_explicit_media_removal_is_supported_without_searching_for_replacement():
    assert _action_requests_image_removal("Remove the image from this section")
    assert not _action_requests_image_removal("Replace the image with a new one")
    assert _action_requests_chart_removal("Delete the chart in this section")
    cleaned, changed = _remove_action_media_markers(
        "## Risks\n\nRisk explanation.\n\n[CHART_1]\n\nMore explanation.",
        remove_charts=True,
    )
    assert changed
    assert "[CHART_1]" not in cleaned
    assert "Risk explanation." in cleaned and "More explanation." in cleaned


def test_action_allows_explicit_table_and_citation_removal():
    original = "## Risks\n\nClaim [S1].\n\n| Risk | Level |\n|---|---|\n| Funding | High |"
    edited = "## Risks\n\nClaim without source annotation."
    ok, problems = _action_edit_validation(original, edited, "Remove the table and citations from this section.", evidence_source=original)
    assert ok, problems
