from utils.report_tiers import get_report_tier_spec, report_tier_contract


def test_report_tiers_define_distinct_rendered_page_bands():
    expected = {
        "brief": (6, 8, 10),
        "detailed": (25, 27, 30),
        "in_depth": (70, 85, 100),
    }
    for tier, (minimum, target, maximum) in expected.items():
        page_target = get_report_tier_spec(tier).page_target
        assert (page_target.minimum, page_target.target, page_target.maximum) == (minimum, target, maximum)


def test_report_tier_contract_communicates_page_and_source_breadth():
    expected = {
        "brief": ("6–10 pages", "8–12 distinct relevant sources"),
        "detailed": ("25–30 pages", "20–30 distinct relevant sources"),
        "in_depth": ("70–100 pages", "35–50 distinct relevant sources"),
    }
    for tier, (page_band, source_band) in expected.items():
        contract = report_tier_contract(tier)
        assert page_band in contract
        assert source_band in contract
        assert "page band" in contract.lower()


def test_report_tier_contract_is_authoritative():
    contract = report_tier_contract("in_depth")
    assert "SELECTED REPORT TIER: Deep Research Report" in contract
    assert "Predictive Scenario Modeling" in contract
    assert "Data Dictionary & Methodology" in contract
    assert "canonical Data Sources appendix" in contract


def test_unknown_tier_defaults_to_detailed():
    assert get_report_tier_spec("not-a-tier").tier.value == "detailed"


def test_tier_contracts_keep_dynamic_structure_and_evidence_driven_visuals():
    for tier in ("brief", "detailed", "in_depth"):
        contract = report_tier_contract(tier)
        assert "RENDERED PAGE BAND:" in contract
        assert "Structure: choose the actual headings" in contract
        assert "Visualizations: include when they communicate evidence more clearly" in contract


def test_chart_density_profiles_match_each_report_tier():
    for tier, label in (("brief", "Brief"), ("detailed", "In-Detail"), ("in_depth", "Deep Research")):
        contract = report_tier_contract(tier)
        assert f"CHART GUIDANCE: {label}" in contract
        assert "there is no minimum point count or visual quota" in contract
