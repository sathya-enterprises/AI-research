from routes.report import _action_edit_validation
from routes.html_report import _render_source_note


def test_action_rejects_placeholders_even_when_original_has_other_entities():
    original = "## Competitive Dynamics\n\n**DoorDash** [S4] and **Uber Eats** [S4].\n\n| Platform | Region | Share |\n|---|---|---|\n| DoorDash | US | 36% [S4] |"
    edited = "## Competitive Dynamics\n\n| Platform | Region | Share |\n|---|---|---|\n| Leading Platform 1 | Regional market | >30% [S4] |\n\n| Platform | Region | Share |\n|---|---|---|\n| DoorDash | US | 36% [S4] |"
    ok, problems = _action_edit_validation(original, edited, "Improve the comparison table and keep existing companies.")
    assert not ok
    assert any("placeholder" in p for p in problems)
    assert any("duplicate table" in p for p in problems)


def test_action_accepts_single_existing_table_with_real_entities():
    original = "## Competitive Dynamics\n\n**DoorDash** [S4] and **Uber Eats** [S4].\n\n| Platform | Region | Share |\n|---|---|---|\n| DoorDash | US | 36% [S4] |\n| Uber Eats | Global | Duopoly leader [S4] |"
    edited = "## Competitive Dynamics\n\n| Platform | Core Region | Market Position/Share | Strategic Focus | Primary Monetization Drivers |\n|---|---|---|---|---|\n| DoorDash | US | 36% [S4] | Convenience | Ads [S4] |\n| Uber Eats | Global | Duopoly leader [S4] | Ecosystem | Fees [S4] |"
    ok, problems = _action_edit_validation(original, edited, "Present the existing comparison as one clean table.")
    assert ok, problems


def test_open_source_label_resolves_to_canonical_source():
    html = _render_source_note(
        "Open source ↗",
        [{"id": "S4", "title": "Canonical Market Report", "publisher": "Example Research", "url": "https://example.com/report"}],
        ["S4"],
        force=True,
    )
    assert "Example Research" in html
    assert "Canonical Market Report" not in html or "Example Research" in html
    assert "S4" in html
