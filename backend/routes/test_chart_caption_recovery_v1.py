from routes.report import _canonicalize_chart_registry


def _chart(title="Deposit and Credit Year-on-Year Growth"):
    return {
        "type": "bar",
        "title": title,
        "unit": "%",
        "sourceIds": ["S1"],
        "series": [
            {"name": "Deposits", "data": [{"label": "FY 2023-24", "value": 26.7}, {"label": "Dec 2024", "value": 11.4}]},
            {"name": "Credit", "data": [{"label": "FY 2023-24", "value": 20.3}, {"label": "Dec 2024", "value": 11.3}]},
        ],
    }


def test_chart_caption_without_placeholder_recovers_valid_chart_inline():
    report = (
        "## Credit and Deposit Growth\n\n"
        "4. Chart – Deposit vs. Credit Growth\n\n"
        "| Period | Deposits (%) | Credit (%) |\n"
        "| --- | ---: | ---: |\n"
        "| FY 2023-24 | 26.7 | 20.3 |\n"
        "| Dec 2024 | 11.4 | 11.3 |\n\n"
        "The series shows moderation."
    )
    updated, charts, meta = _canonicalize_chart_registry(report, [_chart()])
    assert updated.count("[CHART_1]") == 1
    assert len(charts) == 1
    assert updated.index("[CHART_1]") > updated.index("| Dec 2024")
    assert meta["captionMatchedChartsAttached"] == [1]


def test_chart_recovery_does_not_append_unrelated_or_table_specs():
    report = "## Risk Profile\n\nProvision coverage is not disclosed."
    table = {"type": "table", "title": "Supporting Data Table", "columns": ["A"], "rows": [["1"]]}
    updated, charts, meta = _canonicalize_chart_registry(report, [_chart("Credit Growth Trend"), table])
    assert "[CHART_1]" not in updated
    assert charts == []
    assert meta["captionMatchedChartsAttached"] == []


def test_publication_sanitizer_recovers_caption_before_unreferenced_chart_pruning():
    from utils.chart_evidence_audit import _attach_chart_placeholders_to_matching_captions
    report = "## Credit and Deposit Growth\n\nChart: Deposit vs. Credit Growth\n\nEvidence follows."
    updated, attached = _attach_chart_placeholders_to_matching_captions(report, [_chart()])
    assert attached == [1]
    assert updated.count("[CHART_1]") == 1
    assert updated.index("Chart: Deposit vs. Credit Growth") < updated.index("[CHART_1]")


def test_ratio_integrity_repairs_unbulleted_wrong_direction_claim():
    from utils.report_integrity import repair_banking_growth_and_ratio_labels
    source = {
        "id": "S1", "url": "https://www.rbi.org.in/example", "pageFetchStatus": "ok",
        "pageContent": "3. Deposits | 1,000 | 1,140\n4. Loans and advances | 700 | 840\n",
    }
    report = "## Risk profile\nLoan-to-Deposit Ratio: 1.27 (up from 1.20 in FY 2022-23)."
    repaired, meta = repair_banking_growth_and_ratio_labels(report, [source], [{"id": "S1", "url": source["url"]}])
    assert meta["changed"] is True
    assert "Deposit-to-loan ratio**: 1.36" in repaired
    assert "loan-to-deposit ratio was 0.74" in repaired
    assert "up from 1.20" not in repaired
