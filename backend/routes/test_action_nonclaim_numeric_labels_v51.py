from routes.report import (
    _action_extract_numbers,
    _attach_audit_source_evidence,
    _source_evidence_quality_audit,
)


def test_fiscal_year_labels_are_not_quantitative_claims():
    text = "FY2023-24; FY 2024–25; FY2023 24; FY25; financial year 2025/26"

    assert _action_extract_numbers(text) == set()


def test_dates_and_reference_numbering_are_not_quantitative_claims():
    text = "Published September 30, 2026; see Table 3, Figure 2, Chart_1, and [S3]."

    assert _action_extract_numbers(text) == set()


def test_real_quantities_and_units_remain_protected():
    text = "Capacity reached 81,813.60 MW in FY2023-24, up 15.2%."

    assert _action_extract_numbers(text) == {"81,813.60mw", "15.2%"}


def test_list_ordinals_versions_and_index_names_are_not_quantitative_claims():
    text = (
        "1. **Digitalization** improved filing speed.\n"
        "2) Accuracy doubts remain.\n"
        "(3) Random verification is still required.\n"
        "India's e-CoO 2.0 portal supports origin certificates.\n"
        "Nifty Smallcap 250, Nifty 500, Microcap 250 and Smallcap 250 are indices."
    )

    assert _action_extract_numbers(text) == set()


def test_clock_and_reporting_period_labels_are_ignored_but_real_values_remain():
    text = (
        "Block deals ran in a 35-minute morning window (9:55 AM - 10:30 AM). "
        "October 2026 and Sep '26 are month labels. Compare 1-Year Basis, "
        "1-year trailing basis, "
        "3/5/10-Year Basis and the 20-year horizon; the verified return is 1.25% "
        "over 10 consecutive quarters."
    )

    assert _action_extract_numbers(text) == {"35-minute", "1.25%", "10"}


def test_source_audit_does_not_block_a_cited_limitation_for_fiscal_years():
    report = (
        "The retrieved sources contain no usable evidence for UPI transaction "
        "volume or value for FY2023-24 and FY2024-25, so a verified quantitative "
        "comparison cannot be made [S1], [S2]."
    )
    manifest = [
        {"id": "S1", "title": "First source", "url": "https://example.com/1"},
        {"id": "S2", "title": "Second source", "url": "https://example.com/2"},
    ]

    audited_sources = _attach_audit_source_evidence(manifest, [], report)
    audit = _source_evidence_quality_audit(audited_sources)

    assert all(not group.get("claimTokens") for source in audited_sources for group in source.get("_citedClaimGroups", []))
    assert audit["blocking"] is False
    assert audit["mismatchedCitationGroups"] == []
