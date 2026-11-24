"""Charts from series stated in the report's own paragraphs, every point found in the evidence."""
import types

from utils.report_prose_charts import _paragraph_figures, growth_reading, period_key, report_prose_charts


def _store(*passages):
    return types.SimpleNamespace(passages=[types.SimpleNamespace(passage_id=pid, source_id=sid, text=text, kind=kind,
                                                                 parent_source_id="")
                                           for pid, sid, kind, text in passages])


SHAREHOLDING = ("Public shareholding in Energy equities altered from 17.9% in June 2023 to 17.7% in March 2026, after "
                "reaching a high of 19.2% in June 2024 [S3]. In Industrials, public ownership contracted from 17.1% "
                "in June 2023 down to 15.4% by March 2026 [S3].")


def test_entities_measures_and_periods_are_read_from_two_sentences():
    figures = _paragraph_figures(SHAREHOLDING)
    assert [(f["entity"], f["value"], f["period"]) for f in figures] == [
        ("Energy", 17.9, "June 2023"), ("Energy", 17.7, "March 2026"), ("Energy", 19.2, "June 2024"),
        ("Industrials", 17.1, "June 2023"), ("Industrials", 15.4, "March 2026")]


def test_a_paragraph_series_becomes_a_chart_only_with_points_found_in_the_evidence():
    store = _store(("S3.1", "S3", "narrative",
                    "Public shareholding: Energy 17.9% in June 2023, 19.2% in June 2024 and 17.7% in March 2026. "
                    "Industrials stood at 17.1% in June 2023 and 15.4% in March 2026."))
    charts = report_prose_charts([types.SimpleNamespace(section_id="sec-1", body=SHAREHOLDING)], store)
    assert len(charts) == 1
    spec = charts[0].spec
    assert spec["title"] == "Public shareholding (%)" and spec["fromSourceSentence"]
    assert {s["name"]: [p["label"] for p in s["data"]] for s in spec["series"]} == {
        "Energy": ["Jun 2023", "Jun 2024", "Mar 2026"], "Industrials": ["Jun 2023", "Mar 2026"]}
    assert all(p["evidenceQuote"] in store.passages[0].text for s in spec["series"] for p in s["data"])
    assert "-1.7 percentage points" in charts[0].reading
    # nothing in the evidence: no chart
    assert report_prose_charts([types.SimpleNamespace(section_id="sec-1", body=SHAREHOLDING)], _store()) == []


def test_table_rows_with_indian_digit_grouping_and_period_columns_are_evidence():
    body = ("Hydro generation measured 1,51,627.33 MU in 2021-22, rose to 1,62,098.77 MU in 2022-23, and fell to "
            "1,34,053.92 MU in 2023-24 [S10].")
    store = _store(("S10.1", "S10", "table", "Generation by source (Million Units)\n| Source | 2021-22 | 2022-23 | 2023-24 |\n"
                    "| --- | --- | --- | --- |\n| Hydro | 151627.33 | 162098.77 | 134053.92 |"))
    charts = report_prose_charts([types.SimpleNamespace(section_id="s", body=body)], store)
    assert len(charts) == 1
    point = charts[0].spec["series"][0]["data"][0]
    assert point["evidenceRowLabel"] == "Hydro" and point["evidenceColumn"] == "2021-22"
    assert "a year compounded" in charts[0].reading


def test_approximate_figures_projections_and_signed_numbers_are_never_charted():
    text = ("Total capacity grew from approximately 382 GW on April 1, 2021, to over 520 GW by January 31, 2026, "
            "and is planned at 900 GW in 2035.")
    assert _paragraph_figures(text) == []
    store = _store(("S1.1", "S1", "narrative", "The change was -6.42% in May 2024."))
    from utils.report_prose_charts import _evidence_sentence
    assert _evidence_sentence(store, "6.42", "May 2024", []) is None


def test_periods_sort_and_growth_statistics():
    assert period_key("June 2023") < period_key("June 2024") < period_key("March 2026")
    assert period_key("Sep-23") == (2023, 9, 1) and period_key("FY 2024-25")[0] == 2024
    reading = growth_reading([("April 2021", 234.72), ("January 2026", 248.54)], "GW")
    assert "+13.82 GW" in reading and "+5.9%" in reading and "1.2% a year compounded" in reading
