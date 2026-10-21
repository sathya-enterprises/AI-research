import asyncio
from types import SimpleNamespace

from utils.orchestrator import _requires_mandatory_web_research
from utils.research_context import clear_research_context, load_research_context, save_research_context
from routes.html_report import _strip_redundant_model_source_appendix


def test_explicit_research_brief_forces_web_research():
    q = (
        "Assess the growth outlook for India's electric two-wheeler market over the next five years. "
        "Examine demand trends, competition, margins, capex, management commentary and analyst views."
    )
    assert _requires_mandatory_web_research(q) is True


def test_simple_conceptual_question_does_not_force_web_research():
    assert _requires_mandatory_web_research("What is linear regression and how does it work?") is False


def test_short_lived_research_context_round_trip():
    sid = "test-research-context"
    clear_research_context(sid)
    save_research_context(sid, topic="India EV market", sources=[{"title": "EV source", "url": "https://example.com"}], context="evidence")
    ctx = load_research_context(sid)
    assert ctx and ctx["topic"] == "India EV market"
    assert len(ctx["sources"]) == 1
    clear_research_context(sid)
    assert load_research_context(sid) is None


def test_trailing_model_sources_section_is_removed():
    report = "## Market Dynamics\n\nUseful analysis.\n\n## Sources\n\n- Source A\n- Source B\n"
    cleaned = _strip_redundant_model_source_appendix(report)
    assert "## Sources" not in cleaned
    assert "Useful analysis." in cleaned


def test_mid_report_sources_heading_is_preserved():
    report = "## Sources\n\nEvidence quality discussion.\n\n## Outlook\n\nForward view."
    assert _strip_redundant_model_source_appendix(report) == report


if __name__ == "__main__":
    test_explicit_research_brief_forces_web_research()
    test_simple_conceptual_question_does_not_force_web_research()
    test_short_lived_research_context_round_trip()
    test_trailing_model_sources_section_is_removed()
    test_mid_report_sources_heading_is_preserved()
    print("PASS: research pipeline fixes")
