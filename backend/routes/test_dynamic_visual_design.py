"""Focused regression checks for model-selected visual directions.
Run from backend/: python3 -m routes.test_dynamic_visual_design
"""
from routes.html_report import build_html_report
from utils.presentation_schema import ReportPresentationSpec
from utils.brand_design import resolve_report_visual


def spec_with_visual(**visual):
    data = {
        "domain": "generic",
        "visual": {
            "mode": "light",
            "primary_color": "#17324D",
            "secondary_color": "#3E6B8A",
            "accent_color": "#C98A2B",
            "surface_color": "#F7F9FB",
            "surface_alt_color": "#EEF3F7",
            "text_color": "#17324D",
            "muted_color": "#64748B",
            "border_color": "#D7E0E8",
            "typography_scale": "balanced",
            "shape_style": "soft",
            "accent_strategy": "duotone",
            "chart_style": "editorial",
        },
        "sections": [
            {"id":"overview","title":"Overview","section_type":"overview","layout":"single_column","density":"standard","emphasis":"normal","order":0,"blocks":[{"kind":"prose"}]}
        ],
    }
    data["visual"].update(visual)
    return ReportPresentationSpec.from_llm_output(data)[0]


def main():
    # The resolver must respond to evidence character, not a report slug or
    # an accent-color permutation.
    comparison = resolve_report_visual(
        {}, question="Compare historical funding, asset quality and loan composition across cycles",
        title="Private-bank cycle comparison", section_count=6, chart_count=2, table_count=3,
        has_metrics=True, section_types=["comparison", "market_context", "risk_assessment"],
    )
    economics = resolve_report_visual(
        {}, question="Evaluate unit economics, AOV, CAC and contribution margin",
        title="Operating model", section_count=6, chart_count=2, table_count=0,
        has_metrics=True, section_types=["metrics_dashboard", "financials", "findings"],
    )
    assert comparison["visual_spec"]["composition_mode"] == "comparison_led"
    assert economics["visual_spec"]["composition_mode"] == "metric_led"
    assert comparison["visual_spec"]["opening_mode"] != economics["visual_spec"]["opening_mode"]
    assert comparison["visual_spec"]["emphasis_profile"] != economics["visual_spec"]["emphasis_profile"]

    matrix = resolve_report_visual({}, section_count=8, chart_count=3, table_count=4, has_metrics=True,
        comparison_count=3, financial_metric_density=12, qualitative_ratio=0.42)
    project = resolve_report_visual({}, section_count=8, chart_count=3, table_count=1, has_metrics=True,
        project_economics=True, financial_metric_density=12, qualitative_ratio=0.24)
    narrative = resolve_report_visual({}, section_count=5, chart_count=0, table_count=0, has_metrics=False,
        qualitative_ratio=0.86)
    assert matrix["visual_spec"]["composition_mode"] == "matrix_led"
    assert project["visual_spec"]["composition_mode"] == "capital_stack"
    assert narrative["visual_spec"]["composition_mode"] == "narrative_led"
    assert len({matrix["visual_spec"]["visual_rhythm"], project["visual_spec"]["visual_rhythm"], narrative["visual_spec"]["visual_rhythm"]}) == 3
    assert matrix["visual_spec"]["table_treatment"] == "comparison_matrix"
    assert project["visual_spec"]["chart_treatment"] == "hero"

    a = spec_with_visual(
        mode="dark", primary_color="#102A43", secondary_color="#2C7A7B", accent_color="#F4B942",
        surface_color="#0B1526", surface_alt_color="#13243A", text_color="#F8FAFC", muted_color="#B8C5D6",
        border_color="#28415D", typography_scale="dramatic", shape_style="sharp", accent_strategy="contrast", chart_style="technical",
    )
    b = spec_with_visual(
        mode="editorial", primary_color="#442C55", secondary_color="#7B4E6A", accent_color="#D95D39",
        surface_color="#FFF8F2", surface_alt_color="#F3E7DD", text_color="#2B1F25", muted_color="#6E5B63",
        border_color="#DFCBC0", typography_scale="compact", shape_style="rounded", accent_strategy="single", chart_style="minimal",
    )
    a.validate_strict(); b.validate_strict()
    html_a = build_html_report("## Findings\n\nAlpha evidence.", "Report A", "A", "Summary", [], [], [], None, [], a)
    html_b = build_html_report("## Findings\n\nBeta evidence.", "Report B", "B", "Summary", [], [], [], None, [], b)
    assert "#10243A" not in html_a  # sanity guard against accidental stale fixtures
    assert "#2C7A7B" in html_a and "#F4B942" in html_a and "gg-visual--dark" in html_a
    assert "#442C55" in html_b and "#D95D39" in html_b and "gg-visual--editorial" in html_b
    assert html_a != html_b, "different validated visual specs must produce different rendered HTML"
    assert "<script>" not in html_a.split("<body",1)[0], "no script should be injected into head from visual spec"
    malformed = spec_with_visual(primary_color="not-a-color", accent_color="<script>alert(1)</script>")
    malformed.validate_strict()
    assert malformed.visual.primary_color == "#17324D"
    assert "<script>" not in malformed.visual.accent_color
    print("dynamic visual design checks: PASS")


if __name__ == "__main__":
    main()
