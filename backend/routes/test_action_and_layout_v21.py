import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routes.report import (
    _normalise_action_heading,
    _find_explicit_action_section,
    _is_additive_table_request,
    _action_requests_chart_operation,
    _validate_new_action_table,
)
from routes.html_report import _render_key_stats


def test_explicit_action_target_matches_numbered_heading():
    sections = [{"heading": "Geopolitical & Operational Risks"}, {"heading": "Key Trends"}]
    assert _normalise_action_heading('3. Geopolitical & Operational Risks') == 'geopolitical & operational risks'
    assert _find_explicit_action_section('3. Geopolitical & Operational Risks', sections) == 0


def test_additive_table_request_is_surgical_intent():
    assert _is_additive_table_request('Target section: "Risks"\nInstruction: here add one table also in this section')
    assert not _is_additive_table_request('Replace the existing table with a cleaner comparison table')


def test_preservation_mentions_do_not_trigger_chart_generation():
    assert not _action_requests_chart_operation(
        'Shorten this section. Preserve the existing chart and table datasets.'
    )
    assert not _action_requests_chart_operation(
        'Do not add facts or citations. Keep every chart unchanged.'
    )


def test_explicit_chart_edits_are_recognized():
    assert _action_requests_chart_operation('Update the chart using verified values.')
    assert _action_requests_chart_operation('Add a chart to this section.')
    assert _action_requests_chart_operation('Remove the selected chart.')


def test_additive_table_rejects_new_numbers_and_unknown_sources():
    original = 'Energy procurement concerns are 34% and 58% [S1].'
    table = '| Risk | Evidence |\n|---|---|\n| Energy | 34% [S1] |\n| New | 72% [S9] |'
    ok, problems = _validate_new_action_table(original, table)
    assert not ok
    assert any('unsupported source' in p for p in problems)
    assert any('unsupported numeric' in p for p in problems)


def test_brief_dashboard_uses_three_columns_for_three_metrics_and_source_markers():
    html = _render_key_stats([
        {'label': 'Metric A', 'value': '$1.0T', 'sourceIds': ['S1']},
        {'label': 'Metric B', 'value': '34%', 'sourceIds': ['S1']},
        {'label': 'Metric C', 'value': '58%', 'sourceIds': ['S1']},
    ], [{'id': 'S1', 'title': 'Source One', 'publisher': 'Publisher', 'url': 'https://example.com/1'}], 3)
    assert 'gg-stats-count-3' in html
    assert '[S1]' in html
