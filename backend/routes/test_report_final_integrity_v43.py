from __future__ import annotations

from routes.report import (
    _attach_audit_source_evidence,
    _chart_title_is_specific,
    _expand_source_supported_market_cagr_chart,
    _filter_final_chart_specs,
    _strip_orphaned_chart_title_lines,
)
from utils.chart_catalog import chart_density_assessment
from utils.report_quality import audit_report_publication


def _market_chart() -> dict:
    return {
        "type": "bar",
        "title": "India Banking Market Size Projection (2025 to 2031)",
        "xLabel": "Year",
        "yLabel": "Market value (USD billion)",
        "series": [{
            "name": "Market value",
            "data": [
                {"label": "2025", "value": 162.4},
                {"label": "2031", "value": 283.026},
            ],
        }],
        "source": ["S1"],
    }


def test_market_size_cagr_can_fill_calculated_intermediate_estimates_only_when_math_matches():
    text = (
        "Ken Research values the market at USD 162.4 billion in 2025 and USD 283.026 billion in 2031, "
        "reflecting a compound annual growth rate (CAGR) of 9.7%."
    )
    expanded, changed = _expand_source_supported_market_cagr_chart(_market_chart(), text)
    assert changed is True
    points = expanded["series"][0]["data"]
    assert len(points) == 7
    assert points[0]["label"] == "2025"
    assert points[1]["label"] == "2026*"
    assert points[-1]["label"] == "2031"
    assert points[0]["value"] == 162.4
    assert points[-1]["value"] == 283.026
    assert "calculated estimates" in expanded["method"]
    assert expanded["type"] == "line"


def test_market_size_cagr_does_not_derive_points_if_cagr_does_not_match_endpoints():
    text = "The market grows at a compound annual growth rate (CAGR) of 14.0%."
    expanded, changed = _expand_source_supported_market_cagr_chart(_market_chart(), text)
    assert changed is False
    assert len(expanded["series"][0]["data"]) == 2


def test_final_chart_gate_drops_empty_untitled_chart_and_cleans_stranded_title():
    good = {
        "type": "line",
        "title": "India Banking Market Size Projection (2025 to 2031)",
        "xLabel": "Year", "yLabel": "USD billion",
        "series": [{"name": "Market size", "data": [
            {"label": "2025", "value": 162.4},
            {"label": "2031", "value": 283.026},
        ]}],
    }
    empty = {"type": "bar", "title": "", "series": [{"name": "Credit growth", "data": []}]}
    report = (
        "## Growth Outlook\n\n"
        "![India Banking Market Size Projection (2025 to 2031)](CHART_1)\n\n"
        "Credit Growth Moderation (FY26 Actual vs FY27 Outlook)\n[CHART_2]"
    )

    def adapter(chart):
        return dict(chart)

    def normalizer(chart):
        return dict(chart)

    def validator(chart):
        series = chart.get("series") or []
        return bool(
            chart.get("title") and chart.get("xLabel") and chart.get("yLabel")
            and series and any((s.get("data") or []) for s in series if isinstance(s, dict))
        )

    normalized, kept, dropped = _filter_final_chart_specs(
        report, [good, empty], normalizer, validator, auto_type=lambda c: c, adapter=adapter,
    )
    normalized, removed_titles = _strip_orphaned_chart_title_lines(normalized, kept, len(dropped))
    assert len(kept) == 1
    assert dropped
    assert "![" not in normalized
    assert "[CHART_1]" in normalized
    assert "[CHART_2]" not in normalized
    assert "Credit Growth Moderation (FY26 Actual vs FY27 Outlook)" not in normalized
    assert "Chart omitted" in normalized
    assert removed_titles == 1


def test_generic_chart_titles_are_not_accepted_as_specific_titles():
    assert not _chart_title_is_specific("")
    assert not _chart_title_is_specific("Chart 2")
    assert not _chart_title_is_specific("Bar chart")
    assert _chart_title_is_specific("India Banking Market Size Projection (2025 to 2031)")


def test_table_rows_are_not_miscounted_as_chart_density_misses():
    result = chart_density_assessment({
        "type": "table",
        "columns": ["Metric", "Value"],
        "rows": [["Credit growth", "11.4%"], ["Deposit growth", "12.1%"]],
    }, "brief")
    assert result["applicable"] is False
    assert result["within_band"] is None
    assert result["minimum"] is None
    assert result["maximum"] is None


def test_audit_flags_implausible_inr_usd_conversion_but_not_plausible_pair():
    bad = audit_report_publication(
        "Public sector bank net profit was Rs. 1.98 lakh crore (US$ 224.04 billion).",
        charts=[], images=[], sources=[],
    )
    assert bad["currencyConversionAnomalies"]
    assert any("currency conversion" in flag.lower() for flag in bad["flags"])

    plausible = audit_report_publication(
        "Total business was Rs. 283.3 lakh crore (US$ 3,205.57 billion).",
        charts=[], images=[], sources=[],
    )
    assert plausible["currencyConversionAnomalies"] == []


def test_source_evidence_matching_ignores_www_and_tracking_parameters():
    manifest = [{
        "id": "S1", "title": "ICRA banking outlook",
        "url": "https://www.icra.in/research/banking-outlook?utm_source=report",
    }]
    enriched = [{
        "url": "https://icra.in/research/banking-outlook",
        "text": "ICRA expects banking-sector credit growth to moderate in FY2027 as deposits and funding costs remain key constraints. " * 3,
    }]
    attached = _attach_audit_source_evidence(manifest, enriched)
    assert attached[0]["evidenceAvailable"] is True
    assert "credit growth to moderate" in attached[0]["evidenceExcerpt"]


def test_currency_conversion_anomaly_blocks_a_model_pass():
    from routes.report import _apply_deterministic_audit_gate

    publication = audit_report_publication(
        "Public sector bank net profit was Rs. 1.98 lakh crore (US$ 224.04 billion).",
        charts=[], images=[], sources=[],
    )
    result = _apply_deterministic_audit_gate(
        {"status": "PASS", "findings": []},
        {"publicationAudit": publication, "chartDensity": {"exceptionsOrMisses": []}},
    )
    assert result["status"] == "FAIL"
    assert result["blocking"] is True


def test_html_render_after_final_chart_gate_has_chart_and_readable_source_appendix():
    from routes.html_report import build_html_report

    evidence = "The market was USD 162.4 billion in 2025 and is forecast at USD 283.026 billion in 2031, a 9.7% CAGR."
    chart, changed = _expand_source_supported_market_cagr_chart(_market_chart(), evidence)
    assert changed
    body = (
        "## Growth Outlook\n\nMarket value is expected to rise over the projection period.\n\n"
        "![India Banking Market Size Projection (2025 to 2031)](CHART_1)\n"
    )
    normalized, charts, dropped = _filter_final_chart_specs(
        body, [chart], lambda c: dict(c), lambda c: bool(c.get("title") and c.get("series") and c["series"][0].get("data")),
        auto_type=lambda c: c, adapter=lambda c: dict(c),
    )
    assert not dropped
    html = build_html_report(
        report=normalized, title="India Banking Outlook", question="India banking sector", summary="",
        key_stats=[], charts=charts,
        images=[],
        sources=[{
            "id": "S1", "title": "India Banking Market Outlook", "url": "https://example.com/banking-outlook",
        }],
        report_tier="brief",
    )
    assert "![" not in html
    assert "[CHART_1]" not in html
    assert "India Banking Market Size Projection" in html
    assert "2026*" in html
    assert "calculated estimates" in html.lower() or "calculated estimate" in html.lower()
    assert "India Banking Market Outlook" in html
    assert "https://example.com/banking-outlook" in html


def test_duplicate_numeric_observations_with_different_source_ids_block_publication():
    from routes.report import _publication_audit_is_blocking

    report = ("| Metric | Entity | Period | Value | Source |\n"
              "| --- | --- | --- | ---: | --- |\n"
              "| Gross NPA ratio | Public Sector Banks | 2025 | 3.2% | [S1] |\n"
              "| Gross NPA ratio | Public Sector Banks | 2025 | 3.2% | [S2] |")
    audit = audit_report_publication(report, [], [], [
        {"id": "S1", "title": "Official banking data"},
        {"id": "S2", "title": "Same official banking data"},
    ])
    assert audit["duplicateObservations"]
    assert _publication_audit_is_blocking(audit)
