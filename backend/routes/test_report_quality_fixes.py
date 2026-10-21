"""Focused regression checks for report latency/relevance fixes."""
from routes.report import (
    _extract_company_candidates,
    _filter_image_candidates,
    _image_prompt_is_specific,
    _force_fallback_images,
    _top_up_images,
    _recover_report_body,
)
from utils.presentation_schema import ReportPresentationSpec


def main():
    q = "Create a detailed comparison report of Tata Motors vs Mahindra & Mahindra covering business mix, financial performance"
    companies = _extract_company_candidates(q)
    assert companies[:2] == ["Tata Motors", "Mahindra & Mahindra"], companies
    assert all(c.lower() not in {"create", "use", "suv", "financial"} for c in companies)

    imgs = [
        {"url": "https://example.com/tata-suv.jpg", "description": "Tata Motors electric SUV"},
        {"url": "https://example.com/random-office.jpg", "description": "Generic modern office"},
        {"url": "https://example.com/tcs-logo.svg", "description": "TCS logo"},
    ]
    filtered = _filter_image_candidates(imgs, subject=q)
    assert filtered and "tata" in filtered[0]["description"].lower(), filtered
    assert len(filtered) == 1, filtered

    assert _image_prompt_is_specific(
        "Documentary photograph of an H5N1 virology laboratory with researchers examining sample tubes",
        "H5N1 avian influenza human transmission",
    )
    assert not _image_prompt_is_specific("A modern office with a laptop and coffee cup", "Tata Motors")

    assert _force_fallback_images([], filtered)[0] == []
    assert _top_up_images([], filtered)[0] == []

    spec, warnings = ReportPresentationSpec.from_llm_output({
        "domain": "regulatory",
        "sections": [{
            "id": "rules", "title": "Rules", "section_type": "compliance",
            "blocks": [{"kind": "bullets", "items": ["Rule A", "Rule B"]}],
        }],
    })
    assert spec.to_dict()["sections"][0]["blocks"][0]["kind"] == "bullets"
    assert not warnings
    print("PASS: report quality regression checks")


def test_recover_report_body_from_dynamic_section_array():
    """Valid provider sections must survive when no top-level `report` string exists."""
    import json

    first = "A source-backed finding explains the first economic mechanism. " * 12
    second = "A separate section describes the market evidence and investor impact. " * 12
    raw = json.dumps({
        "title": "Research output",
        "sections": [
            {"title": "Generated coverage: market structure", "body": first},
            {"heading": "Generated coverage: financing", "content": second},
        ],
        "charts": [],
    })
    recovered = _recover_report_body(raw)
    assert len(recovered) > 800
    assert "## Generated coverage: market structure" in recovered
    assert "## Generated coverage: financing" in recovered
    assert first.strip() in recovered and second.strip() in recovered

    nested_raw = json.dumps({"payload": {"sections": [
        {"title": "Provider envelope: first", "body": first},
        {"title": "Provider envelope: second", "content": second},
    ]}})
    nested_recovered = _recover_report_body(nested_raw)
    assert "## Provider envelope: first" in nested_recovered
    assert "## Provider envelope: second" in nested_recovered


def test_shared_html_suppresses_unattributed_images_and_keeps_brief_compact():
    from routes.html_report import build_html_report

    common = {
        "title": "Institutional Investment Outlook",
        "question": "Assess the investment outlook",
        "summary": "A source-backed executive summary.",
        "key_stats": [],
        "charts": [],
        "images": [{"url": "https://example.com/research-image.jpg", "caption": "Deployment"}],
        "sources": [],
    }
    brief = build_html_report(
        "## Evidence\n\n[IMAGE_1]", theme=None, presentation=None,
        report_tier="brief", **common,
    )
    detailed = build_html_report(
        "## Evidence\n\n[IMAGE_1]", theme=None, presentation=None,
        report_tier="detailed", **common,
    )
    assert "[IMAGE_1]" not in brief
    assert "research-image.jpg" not in brief
    assert '<nav class="gg-report-nav"' not in brief
    assert '<nav class="gg-report-nav"' in detailed
    assert "Attribution was not supplied with this item." not in brief
    assert '<figure class="gg-figure' not in brief

    pdf_image = build_html_report(
        "## Evidence\n\n[WEB_IMG_1]", theme=None, presentation=None,
        report_tier="detailed", **{
            **common,
            "images": [{
                "url": "data:image/png;base64,AA==",
                "sourceUrl": "https://example.com/research/deployment",
                "caption": "Deployment",
            }],
        },
    )
    assert "Attribution was not supplied with this item." not in pdf_image
    assert 'href="https://example.com/research/deployment"' in pdf_image


def test_pdf_print_adapter_hides_navigation_and_uses_report_title_footer():
    from routes.html_report import build_html_report
    from routes.pdf import _strip_non_printing_runtime

    html_doc = build_html_report(
        "## Evidence\n\nSubstantive analysis.",
        "India Renewable Investment Outlook — Capital and Grid Analysis",
        "Assess investment outlook", "A source-backed summary.", [], [], [],
        report_tier="detailed",
    )
    print_doc = _strip_non_printing_runtime(html_doc)
    assert ".gg-report-nav { display: none !important; }" in print_doc
    assert "Growth Gradual | Research Intelligence | India Renewable Investment Outlook" in print_doc


def test_dynamic_document_splits_h1_and_h2_but_keeps_h3_nested():
    from utils.report_document import build_report_document

    document = build_report_document(
        "# Executive Summary\n\nSummary evidence.\n\n"
        "# Capital Economics\n\nInvestment evidence.\n\n"
        "### Financing sensitivity\n\nRate and leverage analysis.\n\n"
        "## Grid Constraints\n\nTransmission evidence.",
        "Investment Outlook", "Research question", "Summary", tier="detailed",
    )
    assert [section.title for section in document.sections] == [
        "Executive Summary", "Capital Economics", "Grid Constraints",
    ]
    assert "### Financing sensitivity" in document.sections[1].body


def test_arrow_chart_legend_uses_separate_rows_for_long_series_names():
    from routes.html_report import _render_chart_block_inner

    chart = _render_chart_block_inner({
        "type": "arrow", "title": "Annual Capital Requirement",
        "series": [
            {"name": "Current Annual Investment (FY22 Baseline)", "data": [{"label": "Annual Investment", "value": 14.5}]},
            {"name": "Required Annual Run-Rate to 2030", "data": [{"label": "Annual Investment", "value": 30.0}]},
        ],
    }, idx=1)
    assert chart
    assert 'cx="72" cy="18"' in chart
    assert 'cx="72" cy="35"' in chart


def test_fenced_ascii_box_table_renders_as_a_source_linked_html_table():
    from routes.html_report import _markdown_to_html

    sources = [{
        "id": "S1", "title": "Renewable Capacity Statistics",
        "publisher": "MNRE", "url": "https://mnre.gov.in/reports/capacity",
        "domain": "mnre.gov.in", "source_type": "web",
    }]
    markdown = """Installed capacity reached 230 GW [S1].

```
+-----------------------------------+
| INDIA RENEWABLE ENERGY MATRIX     |
+------------------+----------------+
| Metric           | FY26           |
+------------------+----------------+
| Installed RE     | 230.78 GW      |
| Annual generation| 310.59 BU      |
+------------------+----------------+
```
"""
    rendered = _markdown_to_html(markdown, [], [], source_registry=sources, force_source_notes=True)
    assert '<table class="gg-table">' in rendered
    assert "INDIA RENEWABLE ENERGY MATRIX" in rendered
    assert "+------------------+" not in rendered
    table_end = rendered.index("</table>")
    source_note = rendered.find("gg-source-note", table_end)
    assert source_note > table_end
    assert "MNRE" in rendered[source_note:]
    assert 'href="#source-S1"' in rendered[source_note:]


def test_bullet_chart_print_svg_preserves_horizontal_value_scale_and_target_ticks():
    from routes.html_report import _render_chart_block_inner
    from routes.html_report import build_html_report
    from routes.pdf import _svg_chart_from_config

    chart = {
        "type": "bullet", "title": "Capacity Trajectory", "unit": "GW",
        "xLabel": "Metric", "yLabel": "Capacity (GW)",
        "series": [{"name": "Installed Capacity", "data": [
            {"label": "May 2026 Base", "value": 230.78, "target": 500},
            {"label": "2030 Baseline Model", "value": 410, "target": 500},
            {"label": "2030 Accelerated Target", "value": 500, "target": 500},
        ]}],
    }
    html_chart = _render_chart_block_inner(chart, idx=1)
    assert '"indexAxis": "y"' in html_chart
    assert '"text": "Capacity (GW)"' in html_chart
    assert '"backgroundColor": "#e3e9e5"' in html_chart
    assert '"backgroundColor": "rgba(0,0,0,0)"' in html_chart
    report_html = build_html_report(
        "## Capacity\n\n[CHART_1]", "Capacity outlook", "Assess capacity", "",
        [], [chart], [], report_tier="in_depth",
    )
    assert "ggBulletTargets" in report_html
    assert "getPixelForValue(target)" in report_html

    bullet_svg = _svg_chart_from_config({
        "type": "bar",
        "unit": "GW",
        "data": {
            "labels": ["May 2026 Base", "2030 Baseline Model", "2030 Accelerated Target"],
            "datasets": [
                {"label": "Scale", "data": [[0, 540], [0, 540], [0, 540]]},
                {"label": "Actual (GW)", "data": [[0, 230.78], [0, 410], [0, 500]],
                 "backgroundColor": ["#c0392b", "#c0392b", "#17384f"]},
                {"label": "Target", "data": [[497, 503], [497, 503], [497, 503]]},
            ],
        },
        "options": {
            "indexAxis": "y",
            "plugins": {"legend": {"display": False}},
            "scales": {"x": {"title": {"text": "Metric"}}, "y": {"title": {"text": "Capacity (GW)"}}},
        },
    })
    assert "Capacity (GW)" in bullet_svg
    assert "May 2026 Base" in bullet_svg and "2030 Accelerated Target" in bullet_svg
    assert "230.8 / 500 GW" in bullet_svg and "410 / 500 GW" in bullet_svg
    assert "540</text>" in bullet_svg
    assert "Scale</text>" not in bullet_svg  # hidden legend stays hidden in print
    assert bullet_svg.count('stroke="#243746"') == 3


if __name__ == "__main__":
    main()


def test_report_normalizer_removes_unverified_metric_placeholders_and_raw_html():
    from utils.markdown_normalizer import normalize_report_markdown

    source = (
        "The sector has strong capital buffers [S1]. "
        "Sector credit growth was projected at a percentage not verified in retrieved publisher-page evidence CAGR in FY28. "
        "Deposit competition remains important [S2].\n\n"
        "| Segment | Growth | Source |\n|---|---:|---|\n"
        "| Services | a percentage not verified in retrieved publisher-page evidence | [S8] |\n\n"
        '<div style="border-left: 4px solid #1f77b4;">'
        "<strong>Strategic Implication:</strong> Banks should balance lending with funding resilience."
        "</div>"
    )
    normalized = normalize_report_markdown(source)
    assert "a percentage not verified in retrieved publisher-page evidence" not in normalized
    assert "Sector credit growth was projected" not in normalized
    assert "Deposit competition remains important [S2]." in normalized
    assert "| Services | Not verified | [S8] |" in normalized
    assert "<div" not in normalized and "</div>" not in normalized
    assert "<strong>" not in normalized and "</strong>" not in normalized
    assert "**Strategic Implication:** Banks should balance lending with funding resilience." in normalized


def test_brief_page_budget_stops_after_the_minimum_valid_page_count(monkeypatch):
    import asyncio
    import routes.report as report

    observed = {"expansion_calls": 0}
    monkeypatch.setattr(report, "_rendered_page_count_preflight", lambda *args, **kwargs: 6)

    async def unexpected_expand(**kwargs):
        observed["expansion_calls"] += 1
        return kwargs["report_text"] + "\n\nExtra padding text. " * 200

    monkeypatch.setattr(report, "_expand_report_to_tier", unexpected_expand)

    async def run():
        text, pages = await report._ensure_report_page_budget(
            report_text="# Brief\n\nEvidence-backed report body.", question="test topic",
            report_tier="brief", report_plan={}, evidence_text="evidence " * 200,
            title="Test brief", summary="", key_stats=[], charts=[], sources=[],
            presentation={}, web_images=[], file_images=[], theme={},
        )
        assert pages == 6
        assert text.startswith("# Brief")
        assert observed["expansion_calls"] == 0

    asyncio.run(run())


def test_v14_report_normalizer_cleans_wrapped_placeholders_and_html_after_decode():
    from utils.markdown_normalizer import normalize_report_markdown

    source = (
        "## Credit Outlook\n\n"
        "A separate, supported statement remains [S1]. Credit growth was projected at a\n"
        "percentage not verified in retrieved publisher-page evidence CAGR during FY28.\n\n"
        "&lt;div style=&quot;border-left: 4px solid #1f77b4;&quot;&gt;"
        "&lt;strong&gt;Strategic Implication:&lt;/strong&gt; Banks should balance lending and funding."
        "&lt;/div&gt;"
    )
    normalized = normalize_report_markdown(source)
    assert "A separate, supported statement remains [S1]." in normalized
    assert "not verified in retrieved publisher-page evidence" not in normalized.lower()
    assert "Credit growth was projected" not in normalized
    assert "<div" not in normalized and "<strong" not in normalized
    assert "**Strategic Implication:** Banks should balance lending and funding." in normalized
