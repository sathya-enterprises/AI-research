import json

from routes.report import _recover_report_body


def test_recovers_structured_sections_when_report_field_is_empty():
    sections = [
        {"title": "Executive Context", "content": "".join(["Evidence-backed market context. "] * 80)},
        {"title": "Growth Drivers", "content": "".join(["Policy and investment are important drivers. "] * 80)},
        {"title": "Risks", "content": "".join(["Execution and demand risks should be monitored. "] * 80)},
    ]
    payload = {
        "cover": {},
        "domain": "technology",
        "executive_summary": "".join(["The outlook remains evidence-backed. "] * 20),
        "report": "",
        "sections": sections,
        "source_appendix": [],
        "visual": {},
    }
    raw = json.dumps(payload)
    recovered = _recover_report_body(raw, payload)
    assert len(recovered) >= 800
    assert "## Executive Summary" in recovered
    assert "## Executive Context" in recovered
    assert "## Growth Drivers" in recovered
    assert "## Risks" in recovered
