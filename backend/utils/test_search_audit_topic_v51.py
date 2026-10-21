from utils.intent import _extract_research_topic, is_search_audit_request


def test_search_layer_prompt_extracts_actual_topic_not_heading_or_instructions():
    prompt = """**SEARCH-LAYER TEST — EXA**

Search the web for current, credible information about software companies, startups, and investors connected to Electronic City, Bengaluru.

Run 3 targeted searches. Do not generate a research report yet. Return a source audit table."""
    assert is_search_audit_request(prompt)
    assert _extract_research_topic(prompt) == "software companies, startups, and investors connected to Electronic City, Bengaluru"


def test_normal_report_prompt_is_not_marked_search_audit():
    prompt = "Generate a detailed research report on the outlook for India's renewable power sector."
    assert not is_search_audit_request(prompt)
    assert "renewable power sector" in _extract_research_topic(prompt).lower()


def test_explicit_mode_supports_search_audit_without_prompt_marker():
    assert is_search_audit_request("Electronic City startup sources", "search_audit")
