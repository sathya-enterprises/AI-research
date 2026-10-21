from routes import pdf as pdf_mod


def _numeric_chart(title="Credit growth"):
    return {
        "type": "bar", "title": title, "sourceIds": ["S1"],
        "series": [{"name": "Credit growth", "data": [{"label": "2025", "value": 12}, {"label": "2026", "value": 10}]}],
    }


def test_rendered_pdf_chart_audit_repair_removes_only_implicated_chart_and_remaps_markers():
    report = "Credit evidence [CHART_1]. Deposit evidence [CHART_2]."
    charts = [_numeric_chart("Credit growth"), _numeric_chart("Deposit growth")]
    findings = [{
        "severity": "critical",
        "issue": "Final PDF contains a chart value without verified publisher-page evidence.",
        "chartIndex": 1,
        "chartTitle": "Credit growth",
        "sourceIds": [],
        "reason": "No verified source evidence attached to chart.",
    }]
    updated_report, updated_charts, meta = pdf_mod._repair_rendered_chart_audit(report, charts, findings)
    assert meta["changed"] is True
    assert meta["removedCharts"] == 1
    assert len(updated_charts) == 1
    assert updated_report.count("[CHART_1]") == 1  # the former second chart was renumbered to one
    assert "[CHART_2]" not in updated_report
    assert "Credit evidence" in updated_report and "Deposit evidence" in updated_report


def test_duplicate_numeric_line_repair_only_removes_an_exact_repeated_observation():
    line = "Total liabilities/assets for Public Sector Banks were 15,493,234 in 2025 [S1]."
    report = line + "\n" + line.replace("[S1]", "[S2]") + "\nA distinct credit growth observation was 10% in 2025 [S3]."
    findings = [{
        "severity": "critical",
        "issue": "The final rendered PDF repeats the same numeric observation under different or repeated source attributions.",
        "text": line,
    }]
    repaired, removed = pdf_mod._remove_exact_duplicate_observation_lines(report, findings)
    assert removed == 1
    assert repaired.count("15,493,234") == 1
    assert "10% in 2025" in repaired
