"""v92: unit equivalences, presentation-quality gate, clean chart rendering, image hygiene.

From the 20 Oct telecom / EV / renewables runs: drafts caused by "1.33 billion" vs
"1,330.58 million" and lakh/million counts; junk gallery charts built from raw table
headers; clipped chart axes and unlabelled doughnuts in the PDF; QA notes printed in
the report; a 3-minute PDF caused by one slow image; promotional/sitemap images.
"""
from __future__ import annotations

import io
import re

import routes.report as R
from routes.report_design import SVG_AXIS_INK as _AXIS


def _audit(report: str, page: str) -> dict:
    manifest = [{"id": "S1", "url": "https://trai.gov.in/a", "title": "TRAI"}]
    enriched = [{"url": "https://trai.gov.in/a", "title": "TRAI", "pageFetchStatus": "ok", "pageContent": page}]
    return R._source_evidence_quality_audit(R._attach_audit_source_evidence(manifest, enriched, report))


# ── counts and scaled units ────────────────────────────────────────────────

def test_billion_and_million_counts_match_within_written_precision():
    page = "The total telephone subscriber base of India reached 1,330.58 million at the end of March 2026."
    assert _audit("Telecom subscribers crossed 1.33 billion by March 2026 [S1].", page)["blocking"] is False
    assert _audit("Telecom subscribers crossed 1.4 billion by March 2026 [S1].", page)["blocking"] is True


def test_lakh_counts_match_million_counts_and_lakh_tables():
    assert _audit("Electric two-wheeler sales were 1.15 million units in FY25 [S1].",
                  "Electric two-wheeler sales were 11.5 lakh units in 2024-25 according to VAHAN.")["blocking"] is False
    assert _audit("Credit cards in circulation rose by 19.3 per cent to 1,018 lakhs [S1].",
                  "Sr. No | Items | Amount Outstanding (At end-March) | Percentage Variation\n2024 | 2025 P | 2024 | 2025 P\n"
                  "6.1 Total number of credit cards (in lakhs) | 1,018 | 1,099 | 19.3 | 7.9")["blocking"] is False


def test_rupee_lakh_crore_rounding_is_bounded():
    page = "Aggregate deposits of the banks rose to ₹2,47,500 crore at the end of the year."
    assert _audit("Deposits rose to Rs 2.5 lakh crore [S1].", page)["blocking"] is False
    assert _audit("Deposits rose to Rs 2.6 lakh crore [S1].", page)["blocking"] is True


# ── presentation quality ───────────────────────────────────────────────────

def test_gallery_quality_gate_rejects_raw_headers_and_composites_but_repairs_unit_brackets():
    assert R._gallery_chart_quality_issue({"title": "Achievements (1st April -30th September (FY27) by entity"})
    assert R._gallery_chart_quality_issue({"title": "Entity × metric heatmap (scaled 0–100)"}, chart_type="heatmap")
    assert R._gallery_chart_quality_issue({"title": "8 w return vs 8w return"}, chart_type="scatter")
    spec = {"title": "Capacity (MW by entity", "series": [{"name": "Capacity (MW", "data": []}]}
    assert R._gallery_chart_quality_issue(spec) == ""
    assert spec["title"] == "Capacity (MW) by entity" and spec["series"][0]["name"] == "Capacity (MW)"
    assert R._gallery_chart_quality_issue({"title": "Entity × metric heatmap (scaled 0–100)"}, named_types=["heatmap"], chart_type="heatmap") == ""


def test_mostly_empty_ranked_bar_is_rejected():
    spec = {"title": "Capacity ranked", "series": [{"name": "x", "data": [{"label": str(i), "value": v} for i, v in enumerate([1000, 3, 2, 1, 0.5])]}]}
    assert R._gallery_chart_quality_issue(spec, chart_type="horizontal_bar")


def test_internal_qa_notes_and_orphan_table_intros_are_removed():
    text = ("## Key Metrics\nText.\n\n### Source-Extracted Quantitative Evidence\n\n"
            "The table below surfaces 3 distinct numeric observations extracted from retrieved source text.\n\n"
            "> **Table omitted:** its rows could not be reconciled to directly fetched publisher data.\n\n## Next\nBody.")
    out, meta = R._polish_report_presentation(text)
    assert out == "## Key Metrics\nText.\n\n## Next\nBody."
    assert meta == {"qaNotesRemoved": 1, "orphanTableIntrosRemoved": 1}


def test_chart_rationale_that_describes_the_chart_itself_is_not_rendered():
    from routes import html_report as H
    chart = {"type": "bar", "title": "Capacity by state", "unit": "GW", "source": ["S1"],
             "rationale": "Horizontal bar chart provides a clean visual ranking of top-performing states with readable labels.",
             "series": [{"name": "Capacity", "data": [{"label": "Rajasthan", "value": 33.2}, {"label": "Gujarat", "value": 32.9}]}]}
    html = H._render_chart_block_inner(chart, 1)
    assert "clean visual ranking" not in html


def test_pdf_axis_ticks_are_nice_and_bars_start_at_zero():
    from routes.pdf import _svg_chart_from_config
    svg = _svg_chart_from_config({"type": "bar", "data": {"labels": ["A", "B"], "datasets": [{"label": "x", "data": [20790.8, 4158]}]}, "options": {}})
    ticks = re.findall(r'fill="' + _AXIS + r'">([^<]+)</text>', svg)
    assert ticks[:6] == ["25k", "20k", "15k", "10k", "5k", "0"]
    svg2 = _svg_chart_from_config({"type": "bar", "data": {"labels": ["2025", "2026"], "datasets": [{"label": "s", "data": [50, 51.5]}]}, "options": {}})
    assert re.findall(r'fill="' + _AXIS + r'">([^<]+)</text>', svg2)[-1] == "0"


def test_pdf_doughnut_has_a_visible_legend_with_shares():
    from routes.pdf import _svg_chart_from_config
    svg = _svg_chart_from_config({"type": "doughnut", "data": {"labels": ["Public Sector Banks", "Private Sector Banks"],
                                                               "datasets": [{"label": "Adv", "data": [60, 40]}]}, "options": {}})
    legend = re.findall(r'<text[^>]*>([^<]+)</text>', svg)
    assert legend == ["Public Sector Banks 60.0%", "Private Sector Banks 40.0%"]
    ys = [float(y) for y in re.findall(r'<text[^>]*y="([\d.]+)"', svg)]
    assert all(y < 300 for y in ys)  # inside the box, not at the clipped bottom edge


def test_static_chart_boxes_are_marked_so_print_css_does_not_clip_them():
    from routes.pdf import _replace_chart_runtime_with_svg
    html_doc = ('<div class="gg-chart-canvas-box"><canvas id="c1"></canvas></div>'
                '<script>window.__ggCharts = window.__ggCharts || []; window.__ggCharts.push({id: "c1", config: '
                '{"type": "bar", "data": {"labels": ["A"], "datasets": [{"label": "x", "data": [1]}]}, "options": {}}});</script>')
    out = _replace_chart_runtime_with_svg(html_doc)
    assert 'gg-chart-canvas-box gg-chart-canvas-box--static' in out and "<svg" in out


# ── images ─────────────────────────────────────────────────────────────────

def test_icons_and_banners_are_rejected_as_report_images():
    from PIL import Image
    from routes.pdf import _image_bytes_look_editorial

    def png(w, h):
        buf = io.BytesIO(); Image.new("RGB", (w, h), "white").save(buf, "PNG"); return buf.getvalue()
    assert _image_bytes_look_editorial(png(1200, 675)) is True
    assert _image_bytes_look_editorial(png(200, 200)) is False      # icon
    assert _image_bytes_look_editorial(png(1600, 300)) is False     # banner strip


def test_image_candidates_skip_site_furniture_and_promotions_and_prefer_editorial():
    raw = [
        {"url": "https://www.trai.gov.in/sites/default/files/img/sitemap.png", "description": "Telecom sector quality of service",
         "sourceUrl": "https://www.trai.gov.in/"},
        {"url": "https://isol.energy/banner.jpg", "description": "India renewable energy capacity | The Intelligence Layer of Energy Systems platform",
         "sourceUrl": "https://isol.energy/"},
        {"url": "https://img.example.net/solar.jpg", "description": "India renewable energy solar capacity additions",
         "sourceUrl": "https://example-blog.com/solar"},
        {"url": "https://img.etimg.com/solar.jpg", "description": "India renewable energy solar capacity hits record",
         "sourceUrl": "https://economictimes.indiatimes.com/solar-record"},
    ]
    out = R._filter_image_candidates(raw, subject="India renewable energy solar capacity")
    assert [c["sourceUrl"] for c in out] == ["https://economictimes.indiatimes.com/solar-record", "https://example-blog.com/solar"]


def test_preflight_image_records_keep_their_credit():
    from routes.pdf import _prepare_pdf_images
    out = _prepare_pdf_images([], [{"url": "https://img.etimg.com/x.jpg", "caption": "Illustrative image — EV",
                                     "sourceUrl": "https://economictimes.indiatimes.com/ev"}], [])
    assert out[0]["sourceUrl"] == "https://economictimes.indiatimes.com/ev" and out[0]["caption"].startswith("Illustrative")
