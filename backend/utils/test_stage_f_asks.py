"""Stage F item 3: the question's asks are parsed, mapped to sections, repaired and reported."""
from datetime import date

from utils.question_asks import coverage_note, guard_exhaustive_claims, map_asks, parse_asks, repair_for_asks
from utils.report_outline import Section
from utils.section_layout import timeline_from_body

TODAY = date(2026, 11, 8)
P1 = "How can you explain & analyze the sector rotation in Indian stock markets in the past 8 quarters."
P2 = ("Explain the proportion and reasons for stake sales by Indian business promoters. Support it with substantial data "
      "which is easy to understand by UHNIs. Will appreciate an exhaustive list of 'all' promoter exits in past 8 quarters. "
      "Add detailed reasoning of this approach by Indian business promoters.")
P3 = ("List down the why, what, how of all the FTAs signed by India in the past 1 year. It will add merit to draw a "
      "comparison from the longer past or even internationally how these FTAs have been effective or vice-versa.")


def test_windows_are_generic_8_quarters_plus_qtd_and_12_months_to_today():
    assert "Q3 FY27" in parse_asks(P1, TODAY).window_note and "QTD (partial)" in parse_asks(P1, TODAY).window_note
    note = parse_asks(P3, TODAY).window_note
    assert "12 months to today" in note and "Nov 08, 2025 - Nov 08, 2026" in note


def test_prompt_asks_are_recognised():
    a2 = parse_asks(P2, TODAY)
    kinds = {a.kind for a in a2.asks}
    assert a2.items == "promoter exits" and a2.exhaustive_requested and {"audience", "deliverable", "analysis"} <= kinds
    a3 = parse_asks(P3, TODAY)
    assert a3.items == "FTAs" and a3.per_item == ["why", "what", "how"]
    assert [a.text for a in a3.asks if a.kind == "comparison"] == ["comparison with the longer past", "international comparison"]


def test_unmapped_asks_get_sections_and_item_sections_get_why_what_how():
    asks = parse_asks(P3, TODAY)
    sections = [Section("sec-1", "India-UK CETA", role="deep_dive", entity="India-UK CETA", goal="the FTA with the UK"),
                Section("sec-2", "Effectiveness: trade and utilisation outcomes", goal="trade data and utilisation")]
    map_asks(asks, sections, {"A6": ["Effectiveness: trade and utilisation outcomes"]})
    decisions = []
    n = [len(sections)]

    def factory(title, goal, queries):
        n[0] += 1
        return Section(f"sec-{n[0]}", title, goal=goal, queries=queries)

    repair_for_asks(asks, sections, decisions, factory)
    titles = [s.title for s in sections]
    assert "International comparison" in titles and "Comparison with the longer past" in titles
    assert [s.title for s in sections[0].subsections] == ["Why it was signed", "What it covers", "How it works and is implemented"]
    note = coverage_note(asks)
    assert "Coverage of the question" in note and "international comparison" in note


def test_exhaustive_is_never_claimed():
    asks = parse_asks(P2, TODAY)
    text, n = guard_exhaustive_claims("This is an exhaustive list of promoter exits.", asks)
    assert n == 1 and "exhaustive" not in text and "found in the sources reviewed" in text
    assert "not described as exhaustive" in coverage_note(asks)


def test_timeline_from_dated_cited_sentences():
    body = ("India and the UK signed the CETA in July 2025 [S4.1]. Talks had resumed in February 2025 [S4.2]. "
            "Parliament approved it in March 2026 [S5.1]. Duties fall over ten years.")
    line = timeline_from_body(body)
    assert line.startswith("> [!TIMELINE] Feb 2025") and line.count(" | ") == 2 and "[S5.1]" in line
    assert timeline_from_body("Signed in July 2025 [S4.1]. Approved in March 2026 [S5.1].") == ""
