import sys

sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1]))

from routes.report import _action_edit_validation, _action_protected_evidence
from routes.html_report import _render_source_note


def test_action_rejects_placeholder_replacement_and_new_numbers():
    original = '''## Competitive Dynamics & Delivery Economics

**DoorDash (36%)** and **Uber Eats** control the US duopoly [S4]. **Just Eat Takeaway (31%)** leads Western Europe, while **Meituan / Ele.me** dominates China [S4], [S23].

Source: S4, S23'''
    edited = '''## Competitive Dynamics & Delivery Economics

| Platform | Core Region | Market Position/Share |
|---|---|---|
| Leading Platform 1 | Regional market | >30% |
| Leading Platform 2 | Regional market | >30% |

[S4]'''
    ok, problems = _action_edit_validation(original, edited, 'Keep the existing companies and evidence.')
    assert not ok
    assert any('preserved entities disappeared' in p for p in problems)
    assert any('placeholder entity' in p for p in problems)
    assert any('new unsupported numeric values' in p for p in problems)


def test_action_accepts_evidence_preserving_competitor_table():
    original = '''## Competitive Dynamics & Delivery Economics

**DoorDash (36%)** and **Uber Eats** control the US duopoly [S4]. **Just Eat Takeaway (31%)** leads Western Europe, while **Meituan / Ele.me** dominates China [S4], [S23].'''
    edited = '''## Competitive Dynamics & Delivery Economics

| Platform | Core Region | Market Position/Share | Strategic Focus | Primary Monetization Drivers |
|---|---|---|---|---|
| DoorDash | US | 36% [S4] | Market-share defense | Advertising |
| Uber Eats | US | Existing evidence [S4] | Ecosystem | Advertising |
| Just Eat Takeaway | Western Europe | 31% [S4] | Profitability | Commissions |
| Meituan / Ele.me | China | Dominant [S23] | Super-app | Ads + logistics |

[S4], [S23]'''
    ok, problems = _action_edit_validation(original, edited, 'Keep the existing companies, evidence, and source citations.')
    assert ok, problems


def test_action_protected_manifest_captures_entities_citations_and_numbers():
    original = '**DoorDash (36%)** and **Uber Eats** [S4], market grew to $436.5 billion.'
    manifest = _action_protected_evidence(original)
    assert 'DoorDash' in manifest['entities']
    assert 'Uber Eats' in manifest['entities']
    assert manifest['citations'] == ['S4']
    assert '$436.5billion' in manifest['numbers']


def test_source_note_uses_canonical_source_title_for_direct_url():
    html = _render_source_note(
        'https://example.com/report',
        [{'id': 'S4', 'title': 'Example Market Report', 'publisher': 'Example Research', 'url': 'https://example.com/report'}],
        force=True,
    )
    assert 'Example Research' in html
    assert 'Open original source' in html
    assert 'https://example.com/report' in html
