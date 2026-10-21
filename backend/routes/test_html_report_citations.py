from routes.html_report import build_html_report, _remove_orphan_citations

def test_parenthetical_and_bracket_citations_do_not_raise():
    registry = [{"id": "S1", "title": "Example", "url": "https://example.com"}]
    text = "Historical data (S1) and forecast evidence [S1]."
    out = _remove_orphan_citations(text, registry)
    assert out == "Historical data [S1] and forecast evidence [S1]."
    html = build_html_report(
        "## Market\nHistorical data (S1) and forecast evidence [S1].",
        "Test", "Q", "Summary", [], [], [], {}, registry,
        {"domain":"comparison","sections":[],"cover":{"enabled":False},"source_appendix":{"placement":"end_of_report","include_appendix":True}},
        "detailed",
    )
    assert "example.com" in html
