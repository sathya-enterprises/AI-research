from routes.html_report import build_html_report, _adaptive_layout_for_section
from utils.report_document import build_report_document


def test_semantic_document_is_dynamic():
    report = """## Unusual Section
Some analytical text.

[CHART_1]

[CHART_2]

| A | B |
|---|---|
| 1 | 2 |
"""
    doc = build_report_document(report, "T", "Q", "", tier="brief", charts=[{}, {}], images=[], sources=[])
    assert [s.title for s in doc.sections] == ["Unusual Section"]
    assert doc.sections[0].chart_count == 2
    assert doc.sections[0].table_count == 1


def test_auto_layout_is_evidence_driven():
    doc = build_report_document("## One\n[CHART_1]\n[CHART_2]\nShort text.", "T", "Q", "", charts=[{}, {}])
    sec = doc.sections[0]
    plan = {"layout": "auto", "title": "One"}
    assert _adaptive_layout_for_section(plan, {"body": sec.body, "title": sec.title}, sec, "brief") == "grid"


def test_render_has_tier_and_adaptive_hooks():
    report = "## Market Signals\nShort analytical paragraph.\n\n[CHART_1]"
    presentation = {
        "domain": "generic",
        "cover": {"enabled": True, "treatment": "minimal", "title": "Test", "subtitle": ""},
        "executive_summary": {"placement": "none", "heading": "Executive Summary", "key_metrics": []},
        "sections": [{"id":"s1","title":"Market Signals","section_type":"narrative","layout":"auto","density":"auto","emphasis":"normal","order":0,"blocks":[]}],
        "source_appendix": {"placement":"end_of_report","include_appendix":False},
        "default_layout":"auto", "default_density":"standard"
    }
    html = build_html_report(report, "Test", "Q", "", [], [{"title":"C","series":[{"data":[{"label":"A","value":1}]}]}], [], sources=[], presentation=presentation, report_tier="in_depth")
    assert 'data-report-tier="in_depth"' in html
    assert 'gg-tier--in_depth' in html
    assert 'gg-composition--adaptive' in html


def test_fixed_section_template_is_not_injected():
    report = "## Transaction Lens\nRelevant analysis only."
    html = build_html_report(report, "Test", "Q", "", [], [], [], sources=[], report_tier="brief")
    assert "Transaction Lens" in html
    assert "Market Overview" not in html
