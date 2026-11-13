from __future__ import annotations

from copy import deepcopy

from utils.chart_evidence_audit import _unit_is_in, audit_chart_evidence_consistency
from utils.data_links import parent_data_context, pick_links
from utils.evidence_store import build_evidence_store
from utils.section_charts import _header_units, _parse_number, charts_from_passage


def _parent_page() -> str:
    return (
        "Solar capacity by country\n"
        "Period | China (GW) | World total (GW) | China share (%)\n"
        "2023 | 86.4 | 120.0 | 72.0\n"
        "2024 | 91.2 | 130.0 | 70.2"
    )


def test_linked_data_context_keeps_parent_headers_without_parent_observations():
    page = _parent_page()
    context = parent_data_context(page)

    assert "Solar capacity by country" in context
    assert "China (GW)" in context
    assert "China share (%)" in context
    assert "86.4" not in context
    assert "91.2" not in context

    measure, question = ({"solar", "capacity"}, {"china", "solar", "capacity"})
    picked = pick_links([{
        "url": "https://www.eia.gov/solar/capacity",
        "title": "China solar capacity",
        "pageFetchStatus": "ok",
        "pageFetchRelevanceMatched": True,
        "pageContent": page,
        "pageDataLinks": [{"url": "https://www.eia.gov/files/china-solar-capacity.csv", "text": "China solar capacity CSV"}],
    }], measure_terms=measure, question_terms=question, limit=1, existing_urls=set())

    assert len(picked) == 1
    assert picked[0]["parentDataContext"] == context


def test_linked_csv_chart_inherits_only_its_exact_parent_column_unit_and_audits():
    parent_url = "https://publisher.example/solar-capacity"
    child_url = "https://publisher.example/files/china-solar.csv"
    context = parent_data_context(_parent_page())
    manifest = [
        {
            "id": "S1", "title": "China solar capacity CSV", "url": child_url,
            "data_parent_source_id": "S2", "data_parent_url": parent_url,
            "data_parent_title": "Solar capacity by country", "data_parent_context": context,
            "page_fetch_status": "ok",
        },
        {"id": "S2", "title": "Solar capacity by country", "url": parent_url, "page_fetch_status": "ok"},
    ]
    rows = [
        {
            "title": "China solar capacity CSV", "url": child_url,
            "pageFetchStatus": "ok", "pageContent": (
                "TABLE 1 - Solar capacity, China\nYear | China\n"
                "2020 | 60.0\n2021 | 68.0\n2022 | 75.0\n2023 | 86.4\n2024 | 91.2"
            ),
        },
        {"title": "Solar capacity by country", "url": parent_url, "pageFetchStatus": "ok", "pageContent": _parent_page()},
    ]
    store = build_evidence_store(rows, question="China solar capacity", manifest=manifest)
    child_passage = next(passage for passage in store.passages if passage.source_id == "S1")

    assert child_passage.parent_source_id == "S2"
    assert "China (GW)" in child_passage.parent_data_context
    chart = charts_from_passage(child_passage, 1)[0].spec
    assert chart["unit"] == "GW"
    assert chart["unitEvidenceSource"] == "S2"
    assert chart["sourceIds"] == ["S1", "S2"]
    assert all(point["evidenceSource"] == "S1" for series in chart["series"] for point in series["data"])

    audit_sources = [
        {
            "id": "S1", "url": child_url, "page_fetch_status": "ok", "pageContent": rows[0]["pageContent"],
            "data_parent_source_id": "S2", "data_parent_url": parent_url,
            "data_parent_context": context,
        },
        {"id": "S2", "url": parent_url, "page_fetch_status": "ok", "pageContent": context},
    ]
    result = audit_chart_evidence_consistency("", [chart], audit_sources)
    assert result["blocking"] is False, result

    mismatched = deepcopy(audit_sources)
    mismatched[0]["data_parent_context"] = "Period | China share (%)"
    rejected = audit_chart_evidence_consistency("", [chart], mismatched)
    assert rejected["blocking"] is True
    assert any("inherited chart unit" in item["reason"] for item in rejected["chartEvidenceFailures"])


def test_parent_unit_mapping_fails_closed_for_ambiguous_or_wrong_units():
    context = "Period | China (GW) | China share (%)"
    assert _header_units("Revenue (EUR million)") == {"EUR million"}
    assert _unit_is_in({"unit": "EUR million"}, "Revenue was € 5 million")
    assert not _unit_is_in({"unit": "EUR million"}, "Revenue was 5 million")
    assert _unit_is_in({"unit": "GBP billion"}, "Revenue was £ 3 billion")
    assert _unit_is_in({"unit": "JPY"}, "Amount in yen")

    from utils.section_charts import _parent_header_unit

    assert _parent_header_unit(context, "China") == "GW"
    assert _parent_header_unit(context, "China share") == "%"


def test_numeric_claim_units_cover_common_measure_families_and_currency_aliases():
    values = [
        ("67.7 years", "years"), ("1,234 tonnes", "tonnes"), ("5 kg", "kg"),
        ("12 km", "km"), ("900 ha", "ha"),
        (f"{chr(0x20AC)} 40 million", "EUR million"),
        (f"{chr(0x00A3)} 2.5 billion", "GBP billion"),
        (f"{chr(0x00A5)} 9000", "JPY"), (f"{chr(0x20B9)} 15 crore", "INR crore"),
    ]
    assert [(float(_parse_number(raw)[0]), _parse_number(raw)[1]) for raw, _ in values] == [
        (float(_parse_number(raw)[0]), unit) for raw, unit in values
    ]
