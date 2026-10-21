"""V5: evidence-safe visuals and conservative PDF pagination regressions."""
from routes.html_report import _verified_metric_trend, build_html_report
from routes.pdf import _trim_furniture_only_middle_pages

SOURCES = [{"id":"S1", "title":"Market data", "url":"https://example.org/source"}]


def test_sourced_comparable_metrics_become_visual():
    stats = [{"label":"2024 market size", "value":"$10.77B", "source":"S1"},
             {"label":"2035 projected market size", "value":"$42.0B", "source":"S1"}]
    chart = _verified_metric_trend(stats, "", SOURCES)
    assert chart and chart["source"] == ["S1"]
    assert [v["value"] for v in chart["series"][0]["data"]] == [10.77, 42.0]
    doc = build_html_report("## Outlook\nEvidence [S1]", "Market outlook", "Market", "", stats, [], [], sources=SOURCES, report_tier="brief")
    assert "sourced trend" in doc.lower()


def test_unsourced_or_incompatible_metrics_never_make_chart():
    assert _verified_metric_trend([{"label":"2024 market size", "value":"$10B"},
                                  {"label":"2035 market size", "value":"$42B"}], "", SOURCES) is None
    assert _verified_metric_trend([{"label":"2024 market size", "value":"$10B", "source":"S1"},
                                  {"label":"2035 revenue", "value":"$42B", "source":"S1"}], "", SOURCES) is None


def test_blank_middle_page_removed_but_real_content_preserved():
    import fitz
    pdf = fitz.open()
    for content in ("First", "", "Last"):
        page = pdf.new_page()
        if content:
            page.insert_text((80, 120), content)
    cleaned = fitz.open(stream=_trim_furniture_only_middle_pages(pdf.tobytes()), filetype="pdf")
    assert len(cleaned) == 2
    assert "First" in cleaned[0].get_text() and "Last" in cleaned[1].get_text()
