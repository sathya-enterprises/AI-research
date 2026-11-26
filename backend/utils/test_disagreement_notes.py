"""Source disagreements: both figures kept, a note placed in the section where the reader meets them."""
from types import SimpleNamespace

from utils.disagreements import find_disagreements, place_notes, section_note

REPORT = """## Capacity
India's installed solar capacity reached 116 GW in 2025, according to the ministry [S3].

Demand kept rising across states.

## Outlook
India's installed solar capacity reached 118 GW in 2025, the industry body reported [S9].
"""


def test_a_small_real_disagreement_is_found_and_both_figures_are_kept():
    items = find_disagreements(REPORT, strict=True)
    assert len(items) == 1 and {items[0]["a"]["shown"], items[0]["b"]["shown"]} == {"116 GW", "118 GW"}
    note = section_note(items[0])
    assert "116 GW [S3]" in note and "118 GW [S9]" in note and note.startswith("> [!NOTE:Sources differ]")
    from utils.layout_blocks import render_admonition
    html = render_admonition(note, [])
    assert html and "Sources differ" in html and "pullquote" not in html
    sections = [SimpleNamespace(title=b.split("\n", 1)[0], body=b.split("\n", 1)[1]) for b in REPORT.split("## ")[1:]]
    placed = place_notes(sections, items)
    assert placed == [{"section": "Outlook", "period": "2025"}]
    assert "Sources differ" in sections[1].body and "Sources differ" not in sections[0].body
    assert "118 GW in 2025, the industry body reported [S9]." in sections[1].body   # the sentence is untouched
    assert "\n\n\n" not in sections[1].body
    assert place_notes(sections, items) == []                       # placed once


def test_different_subjects_or_a_slice_of_a_total_are_not_shown_as_disagreements():
    report = """## Flows
In Q3FY26 the Nifty 50 aggregate profit after tax rose to Rs 2.33 lakh crore [S2].

## Earnings
In Q3FY26 the Nifty 500 aggregate profit after tax rose to Rs 2.6 lakh crore [S57].

## Trade
India's merchandise exports to the UK reached US$ 13.4 billion in 2025 [S4].
India's merchandise exports to all FTA partners reached US$ 179.1 billion in 2025 [S44].
"""
    assert find_disagreements(report, strict=True) == []


def test_a_gap_within_rounding_or_the_same_source_is_not_a_disagreement():
    same = REPORT.replace("118 GW", "116.5 GW")
    assert find_disagreements(same, strict=True) == []
    one_source = REPORT.replace("[S9]", "[S3]")
    assert find_disagreements(one_source, strict=True) == []
