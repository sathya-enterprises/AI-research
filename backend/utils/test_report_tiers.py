from utils.report_tiers import get_report_tier_spec, report_tier_contract


def test_report_tiers_have_an_evidence_depth_floor_and_a_cap():
    # Stage D page policy: minimum = evidence-depth floor, maximum = cap; the target is
    # computed per run from evidence volume (None here) unless the user asks for pages.
    expected = {"detailed": (20, 32), "in_depth": (40, 140)}
    for tier, (floor, cap) in expected.items():
        page_target = get_report_tier_spec(tier).page_target
        assert (page_target.minimum, page_target.target, page_target.maximum) == (floor, None, cap)


def test_report_tier_contract_communicates_pagination_policy_and_source_breadth():
    expected = {
        "detailed": ("at least 20 pages", "up to 32 pages", "20–30 distinct relevant sources"),
        "in_depth": ("at least 40 pages", "up to 140 pages", "35–50 distinct relevant sources"),
    }
    for tier, (floor_text, cap_text, source_band) in expected.items():
        contract = report_tier_contract(tier)
        assert floor_text in contract and cap_text in contract
        assert source_band in contract
        assert "when the evidence supports it" in contract
        assert "Do not pad to reach a page, word, or character count" in contract


def test_report_tier_contract_is_authoritative():
    contract = report_tier_contract("in_depth")
    assert "SELECTED REPORT TIER: Deep Research Report" in contract
    assert "Predictive Scenario Modeling" in contract
    assert "Data Dictionary & Methodology" in contract
    assert "canonical Data Sources appendix" in contract


def test_unknown_tier_defaults_to_detailed():
    assert get_report_tier_spec("not-a-tier").tier.value == "detailed"


def test_tier_contracts_keep_dynamic_structure_and_evidence_driven_visuals():
    for tier in ("detailed", "in_depth"):
        contract = report_tier_contract(tier)
        assert "RENDERED PAGE BAND:" not in contract
        assert "Structure: choose the actual headings" in contract
        assert "Visualizations: include when they communicate evidence more clearly" in contract


def test_chart_density_profiles_match_each_report_tier():
    for tier, label in (("detailed", "In-Detail"), ("in_depth", "Deep Research")):
        contract = report_tier_contract(tier)
        assert f"CHART GUIDANCE: {label}" in contract
        assert "there is no minimum point count or visual quota" in contract


def test_requested_report_page_count_detects_explicit_target_only():
    from utils.report_tiers import requested_report_page_count

    assert requested_report_page_count("Please prepare a 100 page report on FPI flows") == 100
    assert requested_report_page_count("Make a report of approximately 70 pages") == 70
    assert requested_report_page_count("Review the last 10 years of FPI flows") is None
    assert requested_report_page_count("Write a report", 100) == 100
    assert requested_report_page_count("Write a report", 101) is None
