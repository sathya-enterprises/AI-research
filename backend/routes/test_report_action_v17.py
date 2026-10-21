from routes.report import _action_edit_validation, _explicit_action_target
from routes.html_report import _fallback_presentation


def test_explicit_action_target_is_authoritative():
    instruction = 'Target section: "Vendor & Regional Landscape"\nInstruction: improve the table.'
    assert _explicit_action_target(instruction) == 'Vendor & Regional Landscape'


def test_table_request_must_actually_produce_one_table():
    original = '## Vendor & Regional Landscape\n\nDoorDash [S4] and Uber Eats [S4].'
    edited = '## Vendor & Regional Landscape\n\nDoorDash [S4] and Uber Eats [S4].'
    ok, problems = _action_edit_validation(original, edited, 'Present these competitors in one clean comparison table.')
    assert not ok
    assert any('table' in p for p in problems)


def test_table_request_accepts_one_real_entity_table():
    original = '## Vendor & Regional Landscape\n\n**DoorDash** [S4] and **Uber Eats** [S4].'
    edited = '''## Vendor & Regional Landscape\n\n| Vendor | Core Area | Evidence |\n|---|---|---|\n| DoorDash | Delivery | [S4] |\n| Uber Eats | Delivery | [S4] |'''
    ok, problems = _action_edit_validation(original, edited, 'Present these competitors in one clean comparison table.')
    assert ok, problems


def test_fallback_presentation_does_not_invent_a_cover():
    spec = _fallback_presentation('## Executive Summary\n\nText', 'Title', 'Summary', [{'label':'KPI','value':'10'}], [], 'detailed').to_dict()
    assert spec['cover']['enabled'] is False


def test_action_target_normalizes_section_number():
    from routes.report import _normalize_action_heading
    assert _normalize_action_heading('3. Geopolitical & Operational Risks') == _normalize_action_heading('Geopolitical & Operational Risks')


def test_action_add_table_marker_is_detected():
    from routes.report import _action_requests_table
    assert _action_requests_table('add one table also in this section')
