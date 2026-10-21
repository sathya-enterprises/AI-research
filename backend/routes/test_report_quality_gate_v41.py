from __future__ import annotations

from routes.report import (
    _evaluate_auto_repair_candidate,
    _filter_final_chart_specs,
)
from routes.html_report import _render_sources_appendix
from utils.report_quality import (
    audit_report_publication,
    find_incomplete_numeric_fragments,
    repair_incomplete_numeric_fragments,
)


def _simple_normalizer(chart):
    return dict(chart) if isinstance(chart, dict) else None


def _simple_validator(chart):
    kind = chart.get("type")
    if kind == "table":
        return len(chart.get("columns") or []) >= 2 and len(chart.get("rows") or []) >= 2
    series = chart.get("series") or []
    return bool(chart.get("title")) and bool(series) and any(
        isinstance(item, dict) and len(item.get("data") or []) >= 2 for item in series
    )


def test_incomplete_currency_suffix_is_removed_without_inventing_value():
    text = (
        "- Growth Moderation: Credit growth is projected to moderate to 11.0–11.7% in FY2027—translating to Rs.\n"
        "Credit growth is expected to moderate to 11.0–11.7% in FY2027 (projected credit expansion of Rs.\n"
    )
    repaired, count = repair_incomplete_numeric_fragments(text)
    assert count == 2
    assert "translating to Rs." not in repaired
    assert "credit expansion of Rs." not in repaired
    assert repaired.count(".") >= 2
    assert find_incomplete_numeric_fragments(repaired) == []


def test_publication_audit_flags_truncated_numeric_clause_and_empty_chart():
    audit = audit_report_publication(
        "## Outlook\nCredit growth is expected to moderate in FY2027—translating to Rs.",
        charts=[{"type": "bar", "title": "Chart 2", "series": [{"name": "Value", "data": []}]}],
        sources=[],
    )
    assert audit["incompleteNumericFragments"]
    assert audit["emptyChartIndices"] == [1]
    assert any("incomplete numeric" in item.lower() for item in audit["flags"])
    assert any("no usable data" in item.lower() for item in audit["flags"])


def test_final_chart_filter_removes_empty_charts_and_remaps_placeholders():
    text = "## Trends\n\n[CHART_1]\n\n[CHART_2]\n\n[CHART_3]\n"
    charts = [
        {"type": "line", "title": "Credit growth", "series": [{"name": "Growth", "data": [
            {"label": "FY24", "value": 10.9}, {"label": "FY25", "value": 15.9},
        ]}]},
        {"type": "bar", "title": "Chart 2", "series": [{"name": "Value", "data": []}]},
        {"type": "bar", "title": "Market size", "series": [{"name": "USD billion", "data": [
            {"label": "2025", "value": 162.4}, {"label": "2031", "value": 283.0},
        ]}]},
    ]
    result_text, result_charts, dropped = _filter_final_chart_specs(
        text, charts, _simple_normalizer, _simple_validator
    )
    assert len(result_charts) == 2
    assert dropped == ["Chart 2"]
    assert result_text.count("[CHART_1]") == 1
    assert result_text.count("[CHART_2]") == 1
    assert "[CHART_3]" not in result_text


def test_auto_repair_can_be_accepted_when_it_reduces_pdf_pages_and_passes_quality_gates():
    accepted, reason, full = _evaluate_auto_repair_candidate(
        original_text="x" * 5000,
        repaired_text="y" * 4000,
        original_pages=6,
        repaired_pages=4,
        page_minimum=7,
        page_shortfall=True,
        repair_audit={"status": "WARNING", "findings": []},
        repair_deterministic={"publicationAudit": {"flags": []}},
    )
    assert accepted
    assert reason == "validated"
    assert full


def test_auto_repair_does_not_require_a_page_quota():
    accepted, reason, full = _evaluate_auto_repair_candidate(
        original_text="x" * 5000,
        repaired_text="y" * 5000 + " useful extra words",
        original_pages=6,
        repaired_pages=6,
        page_minimum=7,
        page_shortfall=True,
        repair_audit={"status": "WARNING", "findings": [{"severity": "medium", "issue": "Evidence-limited chart density"}]},
        repair_deterministic={"publicationAudit": {"flags": []}},
    )
    assert accepted
    assert reason == "validated"
    assert full


def test_brief_source_appendix_displays_canonical_citation_id_and_title():
    registry = [{
        "id": "S8", "title": "ICRA Banking Credit Outlook", "publisher": "ICRA",
        "domain": "icra.in", "url": "https://example.org/icra-outlook",
    }]
    html = _render_sources_appendix(
        registry, placement="appendix", include_appendix=True,
        source_registry=registry, report_tier="brief", cited_source_ids={"S8"},
    )
    assert "[S8]" in html
    assert "ICRA Banking Credit Outlook" in html
    assert "https://example.org/icra-outlook" in html


def test_final_chart_filter_adapts_x_labels_and_numeric_arrays():
    from routes.report import _normalise_inline_chart_spec

    text = "## Market trajectory\n\n[CHART_1]\n"
    raw = [{
        "chart_type": "line", "title": "Market size milestones",
        "x_labels": ["2025", "2026", "2027", "2028", "2029", "2030", "2031"],
        "data": [162.4, 178.1, 195.4, 214.4, 235.2, 258.0, 283.0],
    }]
    result_text, charts, dropped = _filter_final_chart_specs(
        text, raw, _simple_normalizer, _simple_validator,
        adapter=_normalise_inline_chart_spec,
    )
    assert not dropped
    assert len(charts) == 1
    assert len(charts[0]["series"][0]["data"]) == 7
    assert "[CHART_1]" in result_text


def test_auto_repair_can_reduce_pages_without_any_tier_minimum():
    accepted, reason, full = _evaluate_auto_repair_candidate(
        original_text="x" * 5000,
        repaired_text="y" * 4500,
        original_pages=8,
        repaired_pages=7,
        page_minimum=7,
        page_shortfall=False,
        repair_audit={"status": "WARNING", "findings": [{"severity": "medium", "issue": "Evidence-limited chart density"}]},
        repair_deterministic={"publicationAudit": {"flags": []}},
    )
    assert accepted
    assert reason == "validated"
    assert full


def test_markdown_image_wrapper_around_chart_token_normalizes_to_structured_placeholder():
    from utils.markdown_normalizer import normalize_report_markdown

    source = "## Market Size\n\n![India Banking Market Size Projection (2025–2031)](CHART_1)\n"
    normalized = normalize_report_markdown(source)
    assert "![India Banking Market Size Projection" not in normalized
    assert "[CHART_1]" in normalized


def test_source_appendix_uses_compact_single_line_numbered_entries():
    registry = [{
        "id": "S8", "title": "ICRA Banking Credit Outlook", "publisher": "ICRA",
        "domain": "icra.in", "url": "https://example.org/icra-outlook",
    }]
    html = _render_sources_appendix(
        registry, placement="appendix", include_appendix=True,
        source_registry=registry, report_tier="brief", cited_source_ids={"S8"},
    )
    # The source appendix is deliberately compact: one numbered list entry
    # with the title and a short visible link label; no large source cards.
    assert '<span class="gg-source-id">[S8]</span>' in html
    assert '<ol' in html and '<li' in html
    assert 'href="https://example.org/icra-outlook"' in html
    assert '>link</a>)</li>' in html
    assert 'ICRA Banking Credit Outlook' in html
    assert '<div class="gg-source-content">' not in html


def test_remap_canonicalizes_wrapped_chart_image_syntax_before_dropping_invalid_chart():
    from routes.report import _remap_chart_placeholders

    source = "![Market size](CHART_1)\n\n![Credit growth](CHART_2)"
    result = _remap_chart_placeholders(source, [{"title": "bad"}, {"title": "good"}], [False, True])
    assert "![Market size]" not in result
    assert "![Credit growth]" not in result
    assert "[CHART_2]" not in result
    assert "[CHART_1]" in result


def test_two_point_chronological_line_chart_is_preserved_but_categorical_comparison_is_bar():
    from routes.report import _recover_pseudo_trend_line_as_bar

    temporal = {"type": "line", "title": "Market milestones", "series": [{"name": "Value", "data": [
        {"label": "2025", "value": 162.4}, {"label": "2031", "value": 283.0},
    ]}]}
    categorical = {"type": "line", "title": "Bank comparison", "series": [{"name": "Value", "data": [
        {"label": "Public banks", "value": 55}, {"label": "Private banks", "value": 45},
    ]}]}
    assert _recover_pseudo_trend_line_as_bar(temporal)["type"] == "line"
    assert _recover_pseudo_trend_line_as_bar(categorical)["type"] == "bar"
