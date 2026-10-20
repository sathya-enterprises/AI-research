"""v93: editorial design system for HTML report, PDF and static charts.

Covers the presentation layer only — report content, chart data, citations and
publication-audit behaviour must be unchanged by the redesign.
"""
from __future__ import annotations

import io
import re

import pytest

from routes import html_report as H
from routes import pdf as P
from routes import report_design as D

SOURCES = [
    {"id": "S1", "title": "Report on Trend and Progress of Banking in India 2024-25",
     "url": "https://www.rbi.org.in/scripts/PublicationsView.aspx?id=23642", "publisher": "Reserve Bank of India", "_ggSourceRegistryVersion": 1},
]
CHARTS = [
    {"type": "line", "title": "Gross NPA ratio", "unit": "%", "source": ["S1"],
     "series": [{"name": "SCBs", "data": [{"label": "FY23", "value": 3.9}, {"label": "FY24", "value": 2.7}, {"label": "FY25", "value": 2.2}]}]},
    {"type": "doughnut", "title": "Share of advances by bank group", "unit": "%", "source": ["S1"],
     "series": [{"name": "Share", "data": [{"label": "Public sector banks", "value": 51.8}, {"label": "Private sector banks", "value": 39.6}, {"label": "Others", "value": 8.6}]}]},
]
REPORT = (
    "> **DRAFT — NOT APPROVED FOR EXTERNAL PUBLICATION.** Review the flagged findings.\n\n"
    "## Executive Summary\n\nGross NPAs fell to 2.2 per cent [S1].\n\n"
    "## Market Structure\n\n[CHART_2]\n\nPublic sector banks hold the largest share [S1].\n\n"
    "## Risks and Outlook\n\nAsset quality is near cyclical lows [S1].\n\n[CHART_1]\n"
)
PRESENTATION = {"sections": [
    {"id": "a", "title": "Executive Summary", "section_type": "metrics_dashboard", "blocks": [{"kind": "metrics"}]},
    {"id": "b", "title": "Market Structure", "section_type": "comparison", "blocks": []},
    {"id": "c", "title": "Risks and Outlook", "section_type": "risk_assessment", "blocks": []},
]}
KEY_STATS = [{"label": "Gross NPA ratio", "value": "2.2%", "change": "-0.5 pp", "source": "S1"},
             {"label": "Aggregate deposits", "value": "₹2,41,47,183 crore", "change": "+11.1%", "source": "S1"}]


@pytest.fixture(scope="module")
def html_doc() -> str:
    return H.build_html_report(REPORT, "Indian Banking FY25", "q", "", KEY_STATS, CHARTS, [], None, SOURCES, PRESENTATION, "detailed")


def test_editorial_body_class_and_embedded_offline_fonts(html_doc):
    assert re.search(r'<body[^>]*class="gg-editorial\b', html_doc)
    assert "font-family: 'Lora'" in html_doc and "font-family: 'Inter'" in html_doc
    assert "data:font/woff2;base64," in html_doc
    assert "fonts.googleapis.com" not in html_doc  # PDF generation never waits on a font request
    assert D.fonts_available()


def test_kpi_value_isolates_number_from_prefix_and_unit_without_changing_precision(html_doc):
    assert '<span class="gg-stat-prefix">₹</span><span class="gg-stat-number"><span class="gg-count"' in html_doc
    assert '<span class="gg-stat-unit">crore</span>' in html_doc
    # "2.2%" keeps one decimal (was forced to 2 → "2.20%" in the browser counter)
    assert re.search(r'data-count-target="2\.2" data-count-decimals="1">2\.2</span>%</span>', html_doc)


def test_section_overline_is_reader_facing(html_doc):
    assert ">Section 01<" in html_doc
    assert ">Section 02 · Comparison<" in html_doc
    assert ">Section 03 · Risk assessment<" in html_doc
    assert ">NARRATIVE<" not in html_doc and ">METRICS DASHBOARD<" not in html_doc


def test_leading_draft_notice_becomes_page_banner_not_a_section(html_doc):
    assert 'class="gg-status-banner gg-status-banner--draft"' in html_doc
    assert "DRAFT — NOT APPROVED FOR EXTERNAL PUBLICATION." in html_doc  # text preserved verbatim
    assert not re.search(r"<h2>Report</h2>", html_doc)
    banner_at = html_doc.index("gg-status-banner--draft")
    assert banner_at < html_doc.index('class="gg-report-sections"')


def test_section_opening_with_a_figure_is_kept_with_its_heading(html_doc):
    m = re.search(r'<div class="gg-keep-with-next"><div class="gg-section-heading"[^>]*>.*?<h2>Market Structure</h2>.*?gg-chart-wrap', html_doc, re.S)
    assert m, "heading and its leading chart must share a keep-together wrapper"


def test_long_tables_are_not_bound_to_the_heading():
    rows = "".join(f"<tr><td>{i}</td></tr>" for i in range(9))
    body = f'<div class="gg-table-wrap gg-block" data-row-count="9"><table>{rows}</table></div><p>x</p>'
    assert H._split_leading_keep_block(body) == ("", body)
    short = '<div class="gg-reveal gg-chart-wrap" data-reveal><div class="a"><div>x</div></div></div><p>rest</p>'
    lead, rest = H._split_leading_keep_block(short)
    assert lead.endswith("</div></div></div>") and rest == "<p>rest</p>"


def test_composition_charts_colour_each_slice_in_fixed_order(html_doc):
    m = re.search(r'"type": "doughnut".*?"backgroundColor": (\[[^\]]+\])', html_doc, re.S)
    assert m and m.group(1).count("#") == 3
    assert m.group(1).startswith('["' + D.CHART_PALETTE[0])


def test_id_only_source_note_names_the_publication(html_doc):
    assert re.search(r'Source:</span> (?:Reserve Bank of India|rbi\.org\.in|Report on Trend)', html_doc)
    assert "Source:</span> S1 <a" not in html_doc


def test_editorial_layer_is_appended_after_legacy_css_and_scoped():
    css = H._build_css({}, "detailed")
    assert ".gg-table" in css
    layer = D.editorial_css()
    assert layer.count("html body.gg-editorial") > 50
    assert "@page" in D.editorial_print_css() and "{short_title}" in D.editorial_print_css()


def test_svg_horizontal_bar_labels_are_not_clipped_and_generic_axis_titles_dropped():
    long = "Small finance banks (aggregate of all reporting institutions)"
    svg = P._svg_chart_from_config({"type": "bar", "data": {"labels": [long, "Foreign banks"], "datasets": [{"label": "CRAR", "data": [21.4, 19.1]}]},
                                    "options": {"indexAxis": "y", "scales": {"y": {"title": {"text": "Category"}}, "x": {"title": {"text": "CRAR (%)"}}}}})
    labels = re.findall(r'text-anchor="end" font-size="10" fill="[^"]+">([^<]+)</text>', svg)
    assert labels and all(len(l) <= P._SVG_HBAR_LABEL_MAX + 2 for l in labels)
    assert ">Category<" not in svg and ">CRAR (%)<" in svg
    xs = [float(x) for x in re.findall(r'<text x="([\d.]+)" y="[\d.]+" text-anchor="end" font-size="10"', svg)]
    assert all(x > 120 for x in xs)  # label column sized to the text, not the old fixed 68px
    assert f'font-family="{D.SVG_FONT.replace(chr(39), "&#x27;")}"' in svg


def test_svg_axis_wraps_long_vertical_bar_labels_instead_of_overlapping():
    svg = P._svg_chart_from_config({"type": "bar", "data": {"labels": ["Public sector banks", "Private sector banks", "Small finance banks", "Foreign banks", "Payments banks", "Regional rural banks"],
                                                            "datasets": [{"label": "x", "data": [1, 2, 3, 4, 5, 6]}]}, "options": {}})
    assert "Public sector" in svg and "banks" in svg
    assert P._svg_wrap_label("Public sector banks", 12) == ["Public", "sector banks"]
    assert P._svg_wrap_label("Public sector banks", 10) == ["Public", "sector ba…"]


def test_pdf_uses_bundled_fonts_and_keeps_citations_machine_readable():
    pypdf = pytest.importorskip("pypdf")
    pdf = P.build_pdf("## Findings\nCredit grew while deposits lagged [S1].\n\n## Risks\nFunding costs remain a key risk [S1].",
                      "Banking Sector Outlook", "q", "", [], [], sources=[{"id": "S1", "title": "RBI publication", "url": "https://example.com/rbi-report"}],
                      report_tier="detailed")
    text = "\n".join(p.extract_text() or "" for p in pypdf.PdfReader(io.BytesIO(pdf)).pages)
    # pypdf is the export-audit fallback: citations must not come out as "[ S 1 ]"
    assert "[S1]" in text and "https://example.com/rbi-report" in text
    fonts = set()
    for page in pypdf.PdfReader(io.BytesIO(pdf)).pages:
        for f in (page.get("/Resources", {}).get("/Font") or {}).values():
            fonts.add(str(f.get_object().get("/BaseFont")))
    assert any("Inter" in f for f in fonts) and any("Lora" in f for f in fonts)


def test_every_chart_reaches_the_pdf_html_as_static_svg():
    html_doc = H.build_html_report(REPORT, "Indian Banking FY25", "q", "", KEY_STATS, CHARTS, [], None, SOURCES, PRESENTATION, "detailed")
    static = P._replace_chart_runtime_with_svg(html_doc, theme=D.editorial_render_theme())
    assert len(re.findall(r'class="gg-chart-canvas-box gg-chart-canvas-box--static"', static)) == len(CHARTS)
    assert all(c in static for c in D.CHART_PALETTE[:3])  # doughnut slices in palette order
