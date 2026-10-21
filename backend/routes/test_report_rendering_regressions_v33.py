from routes.html_report import _markdown_to_html, _render_image_block
from routes.pdf import _prepare_pdf_images
from routes.report import (
    _filter_image_candidates,
    _validate_image_selections,
    _force_fallback_images,
    _top_up_images,
    _chart_density_audit,
)


def test_flattened_historical_performance_table_renders_as_native_table():
    source = (
        "| Metric | Trend | Latest figure |\\n"
        "|---|---|---|\\n"
        "| Revenue growth | Double-digit CAGR | — | [S1] | "
        "Export share | 73-75% of trade exports | 73.7% | [S1] | "
        "Headcount | 4M to 5.5M | — | — |"
    )
    rendered = _markdown_to_html(source, [], [], source_registry=[{"id": "S1", "url": "https://example.com/report", "title": "Report"}])
    assert 'class="gg-table"' in rendered
    assert "Revenue growth" in rendered
    assert "Export share" in rendered
    assert "<tr>" in rendered
    assert "| Export share | 73-75%" not in rendered


def test_pdf_image_preparation_never_promotes_asset_url_to_source_page():
    asset = "https://cdn.example.com/chart.png"
    payload = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
    prepared = _prepare_pdf_images([], [{"url": asset, "data": payload, "caption": "Chart"}], [])
    assert len(prepared) == 1
    assert prepared[0]["url"].startswith("data:image/png;base64,")
    assert prepared[0]["sourceUrl"] == ""
    assert prepared[0]["url"] != asset


def test_source_metadata_survives_image_candidate_selection_and_fallbacks():
    raw = [{
        "url": "https://example.com/it-market-chart.png",
        "description": "Indian IT services market forecast chart",
        "sourceUrl": "https://example.com/market-report",
        "sourceTitle": "Example Research",
        "publisher": "Example Research",
    }]
    candidates = _filter_image_candidates(raw, limit=4, subject="Indian IT services market")
    assert candidates and candidates[0]["sourceUrl"] == "https://example.com/market-report"
    selected, mask = _validate_image_selections([{"candidateIndex": 1, "caption": "Market forecast"}], candidates)
    assert mask == [True]
    assert selected[0]["sourceUrl"] == "https://example.com/market-report"
    assert selected[0]["sourceTitle"] == "Example Research"
    fallback, forced = _force_fallback_images([], candidates)
    assert forced and fallback[0]["sourceUrl"] == "https://example.com/market-report"
    topped, added = _top_up_images([], candidates, target=1)
    assert added and topped[0]["sourceTitle"] == "Example Research"


def test_image_render_uses_publisher_page_not_asset_url_as_source_link():
    image = {
        "url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=",
        "sourceUrl": "https://example.com/market-report",
        "sourceTitle": "Example Research",
        "caption": "Market-size chart",
    }
    markup = _render_image_block(image, 1, source_registry=[{"id": "S1", "url": "https://example.com/market-report", "title": "Example Research"}], force_source_note=True)
    assert '<img src="data:image/png;base64,' in markup
    assert 'href="https://example.com/market-report"' in markup
    assert 'cdn.example.com' not in markup


def test_density_audit_reports_observations_without_a_target_or_fabrication():
    audit = _chart_density_audit([{
        "type": "line", "title": "Two year trend",
        "series": [{"name": "GDP", "data": [{"label": "FY26", "value": 7.7}, {"label": "FY27", "value": 6.6}]},
                   {"name": "Credit", "data": [{"label": "FY26", "value": 16.1}, {"label": "FY27", "value": 14.0}]}],
    }], "detailed")
    assert audit["profile"]["minimum"] is None
    assert audit["profile"]["target"] is None
    chart = audit["charts"][0]
    assert chart["status"] == "measured"
    assert chart["count"] == 2
    assert chart["minimum"] is None
    assert chart["within_band"] is None
    assert chart["series_counts"] == [{"name": "GDP", "count": 2}, {"name": "Credit", "count": 2}]


def test_model_written_data_sources_appendix_is_removed_from_body():
    from routes.html_report import _strip_redundant_model_source_appendix

    report = (
        "## Findings\n\n" + ("Substantive analysis of the evidence. " * 20) + "\n\n"
        "## Appendix — Data Sources\n\n"
        "Total sources: 2\n\nhttps://example.com/a\nhttps://example.com/b"
    )
    cleaned = _strip_redundant_model_source_appendix(report)
    assert "Substantive analysis of the evidence." in cleaned
    assert "Appendix — Data Sources" not in cleaned
    assert "https://example.com/a" not in cleaned


def test_counted_complete_sources_heading_is_removed_but_methodology_is_preserved():
    from routes.html_report import _strip_redundant_model_source_appendix

    report = (
        "## Methodology & Sources\n\n" + ("This is substantive methodology and analysis. " * 20) + "\n\n"
        "## Complete Data Sources 20\n\nhttps://example.com/a"
    )
    cleaned = _strip_redundant_model_source_appendix(report)
    assert "Methodology & Sources" in cleaned
    assert "This is substantive methodology and analysis." in cleaned
    assert "Complete Data Sources 20" not in cleaned
    assert "https://example.com/a" not in cleaned


def test_inline_chart_placeholder_is_normalized_for_renderer():
    from utils.markdown_normalizer import normalize_report_markdown

    report = "Forecasted market value is 235 bn at the end of the period [CHART_1]."
    normalized = normalize_report_markdown(report)
    assert "Forecasted market value is 235 bn at the end of the period." in normalized
    assert "\n[CHART_1]\n" in f"\n{normalized}\n"
    assert "[CHART_1]." not in normalized


def test_leaked_continuation_markers_are_removed_at_markdown_boundary():
    from utils.markdown_normalizer import normalize_report_markdown

    report = (
        "## Competitive Landscape\n\n"
        "Substantive original content about the market.\n\n"
        "[[SECTION: 2. Market Size, Structure & Growth]]\n\n"
        "Additional source-backed findings.\n\n[[END SECTION]]"
    )
    normalized = normalize_report_markdown(report)
    assert "Competitive Landscape" in normalized
    assert "Substantive original content" in normalized
    assert "Additional source-backed findings" in normalized
    assert "[[SECTION:" not in normalized
    assert "[[END SECTION]]" not in normalized


def test_exact_repeated_tables_and_numbered_subheads_are_removed():
    from utils.markdown_normalizer import normalize_report_markdown

    table = (
        "| Fiscal year | T&M % | Fixed-price % | Outcome-based % |\n"
        "|---|---:|---:|---:|\n"
        "| 2018 | 62 | 30 | 8 |\n"
        "| 2020 | 58 | 28 | 14 |\n"
        "| 2022 | 53 | 25 | 22 |\n"
        "| 2024 | 48 | 22 | 30 |\n"
        "| 2025 | 45 | 20 | 35 |"
    )
    report = (
        "## Financial, Pricing & Unit Economics\n\n"
        "### 1. Pricing Mix Evolution (FY2018-FY2025)\n\n" + table + "\n\n"
        "A distinct explanation of how the pricing mix changed over the review period.\n\n"
        "### 1. Pricing Mix Evolution (FY2018-FY2025)\n\n" + table
    )
    normalized = normalize_report_markdown(report)
    assert normalized.count("| Fiscal year | T&M %") == 1
    assert normalized.count("### 1. Pricing Mix Evolution") == 1
    assert "A distinct explanation" in normalized


def test_image_filter_drops_insurance_visual_for_banking_report():
    from routes.report import _filter_image_candidates

    raw = [{
        "url": "https://insurance150.com/visuals/private-equity-insurance-chart.png",
        "description": "Private Equity Activity in the Insurance Sector",
        "sourceUrl": "https://insurance150.com/report/private-equity",
        "publisher": "Insurance150",
    }, {
        "url": "https://rbi.org.in/images/bad-loans-bank-chart.png",
        "description": "India bank non-performing assets ratio declines; Gross NPA ratio",
        "sourceUrl": "https://rbi.org.in/financial-stability-report",
        "publisher": "Reserve Bank of India",
    }]
    selected = _filter_image_candidates(raw, limit=4, subject="India banking sector growth outlook, credit and non-performing assets")
    assert all("insurance150.com" not in item["url"] for item in selected)
    assert any("rbi.org.in" in item["url"] for item in selected)


def test_publication_audit_flags_suspicious_same_values_across_distinct_banks():
    from utils.report_quality import audit_report_publication

    report = (
        "## Peer comparison\n\n"
        "| Bank | Market cap (₹ cr) | FY2025 credit growth |\n"
        "|---|---:|---:|\n"
        "| SBI | 11,20,543 | 12.5% |\n"
        "| HDFC Bank | 11,20,543 | 12.5% |\n"
        "| ICICI Bank | 11,20,543 | 12.5% |\n"
        "| Axis Bank | 11,20,543 | 12.5% |"
    )
    audit = audit_report_publication(report, charts=[], images=[], sources=[{"id": "S1", "url": "https://example.com"}])
    assert audit["status"] == "review_required"
    assert any("identical" in flag or "repeated" in flag for flag in audit["flags"])
    assert len(audit["suspiciousEntityTableValues"]) == 2


def test_publication_audit_flags_dangling_inline_chart_and_unresolved_source_id():
    from utils.report_quality import audit_report_publication
    from utils.markdown_normalizer import normalize_report_markdown

    report = normalize_report_markdown("The projected value is shown here [CHART_3]. It is based on [S9].")
    audit = audit_report_publication(report, charts=[{}], images=[], sources=[{"id": "S1", "url": "https://example.com"}])
    assert audit["danglingChartRefs"] == [3]
    assert audit["unresolvedCitationIds"] == ["S9"]
    assert audit["status"] == "review_required"


def test_duplicate_markdown_tables_are_removed_even_without_blank_line_boundaries():
    from utils.markdown_normalizer import normalize_report_markdown

    table = (
        "| Fiscal year | T&M % | Fixed-price % | Outcome-based % |\n"
        "|---|---:|---:|---:|\n"
        "| 2018 | 62 | 30 | 8 |\n"
        "| 2020 | 58 | 28 | 14 |\n"
        "| 2022 | 53 | 25 | 22 |"
    )
    source = "## Unit Economics\n### Pricing Mix\n" + table + "\n### Pricing Mix (continued)\n" + table + "\n## Risks\nUnique risk analysis."
    normalized = normalize_report_markdown(source)
    assert normalized.count("| Fiscal year | T&M %") == 1
    assert "## Risks" in normalized
    assert "Unique risk analysis." in normalized


def test_audit_reports_citations_as_unresolved_when_manifest_is_empty():
    from utils.report_quality import audit_report_publication

    audit = audit_report_publication("Evidence-backed claim [S4].", sources=[])
    assert audit["unresolvedCitationIds"] == ["S4"]
    assert audit["status"] == "review_required"


def test_publication_audit_flags_materially_reversing_broad_market_forecasts():
    from utils.report_quality import audit_report_publication

    report = (
        "The overall Indian IT services market is estimated at USD 45.70 billion in 2026, "
        "with projections to reach USD 78.14 billion by 2034 [S1].\n\n"
        "Total Indian IT services revenue is projected to reach US $340 bn by FY2028, "
        "representing a CAGR of approximately 9% from FY2023 [S2]."
    )
    audit = audit_report_publication(report, sources=[
        {"id": "S1", "url": "https://example.com/a"},
        {"id": "S2", "url": "https://example.com/b"},
    ])
    assert audit["potentialForecastConflicts"]
    assert any("forecasts" in flag.lower() for flag in audit["flags"])


def test_full_width_citations_normalize_before_pdf_and_audit():
    from utils.markdown_normalizer import normalize_report_markdown

    normalized = normalize_report_markdown("Credit growth was 12.5 % 【S2】 .")
    assert "[S2]." in normalized
    assert "【S2】" not in normalized


def test_image_filter_drops_external_assets_without_publisher_page():
    from routes.report import _filter_image_candidates

    selected = _filter_image_candidates([{
        "url": "https://cdn.example.com/banking-chart.png",
        "description": "Bank credit growth and deposit trends chart",
        # An asset CDN URL alone is not a verifiable publisher-page citation.
    }], limit=4, subject="India banking credit and deposit growth")
    assert selected == []


def test_markdown_pipe_table_is_not_misclassified_as_ascii_diagram():
    from routes.html_report import _markdown_to_html
    from utils.markdown_normalizer import normalize_report_markdown

    source = (
        "## Pricing Analysis\n### Pricing Mix\n"
        "| Fiscal year | T&M % | Fixed-price % | Outcome-based % |\n"
        "|---|---:|---:|---:|\n"
        "| 2018 | 62 | 30 | 8 |\n"
        "| 2020 | 58 | 28 | 14 |\n"
        "| 2022 | 53 | 25 | 22 |"
    )
    rendered = _markdown_to_html(normalize_report_markdown(source), [], [])
    assert '<table' in rendered
    assert 'gg-table' in rendered
    assert 'gg-ascii-diagram' not in rendered
    assert '2018' in rendered and '2022' in rendered
