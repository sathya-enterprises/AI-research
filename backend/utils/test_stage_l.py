"""Stage L: charts survive and read correctly (time order, axis titles, finished periods)."""
from utils.chart_lint import chronological, fix_chart, short_axis_label, time_key
from utils.section_charts import _is_qtd, qtd_start_month


def _line(labels_values):
    return {"type": "line", "title": "Figure 1: Net investment (crore)",
            "series": [{"name": "Net investment", "data": [{"label": l, "value": v} for l, v in labels_values]}]}


def test_time_axis_reversed_to_oldest_first():
    spec = _line([("30 Sept 2026", -21745), ("15 Sept 2026", -14116), ("31 Aug 2026", 13010), ("15 Jul 2026", 15559)])
    out, fixes = fix_chart(spec)
    assert [p["label"] for p in out["series"][0]["data"]] == ["15 Jul 2026", "31 Aug 2026", "15 Sept 2026", "30 Sept 2026"]
    assert "time axis ordered oldest to newest" in fixes


def test_time_keys_sort_mixed_period_styles():
    assert time_key("2023") < time_key("2024") < time_key("2025")
    assert time_key("Q1 FY26") < time_key("Q2 FY26") < time_key("FY26")
    assert time_key("Jul 2026") < time_key("15 Aug 2026") < time_key("2026-09-30")
    assert time_key("Banks") is None


def test_ascending_series_untouched():
    spec = _line([("2023", 1.0), ("2024", 2.0), ("2025", 3.0)])
    assert chronological(spec) is False


def test_long_axis_title_shortened_to_unit():
    assert short_axis_label("Market capitalization of listed domestic companies", "% of GDP") == "% of GDP"
    assert short_axis_label("Net investment", "₹ crore") == "Net investment (₹ crore)"
    assert len(short_axis_label("Foreign direct investment net inflows to the reporting economy", "")) <= 40


def test_finished_fortnight_is_not_qtd():
    start = qtd_start_month("2026-11-10")
    assert not _is_qtd("16 Oct 2026 to 31 Oct 2026", start)
    assert not _is_qtd("Oct 2026", start)
    assert _is_qtd("Nov 2026", start) and _is_qtd("Q3 FY27", start)
    assert _is_qtd("Oct 2026", int(start))  # a bare quarter start keeps the old rule


def test_calculation_labels_hidden_from_readers():
    from utils.reader_text import reader_text
    assert reader_text("a 32.9% (calculated: change from 2230 to 2964)% increase.") == "a 32.9% increase."
    assert reader_text("moved 14.16 (calculated: 17.06 - 2.90)% faster") == "moved 14.16% faster"
    assert reader_text("an investment of 500 (calculated: 5500 - 5000) to 5,500 crore.") == "an investment of 5,000 to 5,500 crore."
    assert reader_text("gap of -15.3 (calculated: 49.0 - 64.3) points") == "gap of -15.3 points"


def test_entity_rewrite_only_touches_prose():
    from utils.evidence_check import _is_prose
    assert not _is_prose("$0 $400M $800M $1,200M")
    assert not _is_prose("Healthcare [$$$$$$$$$$$$$$] $809 million")
    assert not _is_prose("0% 15% 30%")
    assert _is_prose("Healthcare received $809 million of net inflows in July 2026 from foreign investors.")


_BARS = """Healthcare took $809 million, behind Consumer Services ($1,063 million) and ahead of Consumer Durables ($768 million), Metals & Mining ($512 million) and IT ($352 million) [S36.2].

```
                        NET FPI INFLOWS BY SECTOR (JULY 2026)
  Consumer Services     [$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$] $1,063 million
  Healthcare            [$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$] $809 million
  Consumer Durables     [$$$$$$$$$$$$$$$$$$$$$$$$$$$$$] $768 million
  Metals & Mining       [$$$$$$$$$$$$$$$$$$$] $512 million
  Information Tech.     [$$$$$$$$$$$$] $352 million
                        +--------------------+--------------------+
                        $0                   $400M                $800M                $1,200M
```

Next paragraph."""

_BOX = """Sector revenue nearly tripled over the decade [S72.8].

```
                               AGGREGATE SECTOR FINANCIALS
┌──────────────────────────┬───────────────┬───────────────┐
│ Metric                   │ FY16          │ FY26          │
├──────────────────────────┼───────────────┼───────────────┤
│ Aggregate Sector Revenue │ ~₹2.25 lakh crore │ ~₹6.28 lakh crore │
│ Aggregate Sector PAT     │ ₹27,420 crore │ ₹82,904 crore │
└──────────────────────────┴───────────────┴───────────────┘
```
"""


def test_text_art_bars_dropped_when_text_gives_the_numbers():
    from utils.text_art_fences import resolve_fences
    out, actions = resolve_fences(_BARS)
    assert "$$$" not in out and "$400M" not in out and "```" not in out and "Next paragraph." in out
    assert actions == ["dropped text art whose numbers the text already gives"]


def test_box_drawn_table_becomes_a_sourced_markdown_table():
    from utils.text_art_fences import resolve_fences
    out, _ = resolve_fences(_BOX)
    assert "| Metric | FY16 | FY26 |" in out and "| Aggregate Sector PAT | ₹27,420 crore | ₹82,904 crore |" in out
    assert "**Table: Aggregate sector financials**" in out and "Source: [S72.8]" in out and "┌" not in out


def test_broken_heading_table_row_and_data_source_line_repaired():
    from utils.text_art_fences import repair_broken_lines
    text = "#### FII Positioning and Under\nweight Allocations\n\n| **\nIndian Pension Funds** | Restricted |\n\n*Data Source: [S83.13]*"
    out, _ = repair_broken_lines(text)
    assert "#### FII Positioning and Underweight Allocations" in out
    assert "| **Indian Pension Funds** | Restricted |" in out and "Source: [S83.13]" in out and "Data Source" not in out


def test_time_rows_become_series_per_column_and_split_by_granularity():
    from types import SimpleNamespace
    from utils.section_charts import charts_from_passage
    text = ("TABLE 1 — Flows\nCalendar Month | FPI Flows Secondary | FPI Flows Primary | FPI Flows Equity\n"
            "Calendar 2022 (₹ Crore) | (146,048.38) | 24,608.94 | (121,439.44)\n"
            "Calendar 2023 (₹ Crore) | 1,27,759.75 | 43,347.14 | 1,71,106.89\n"
            "Calendar 2024 (₹ Crore) | (1,21,210.21) | 1,21,637.15 | 426.94\n"
            "Jan-2025 (₹ Crore) | (81,903.72) | 3,876.78 | (78,026.94)\n"
            "Feb-2025 (₹ Crore) | (41,748.97) | 7,174.62 | (34,574.35)\n"
            "Mar-2025 (₹ Crore) | (6,027.77) | 2,055.16 | (3,972.61)\n"
            "Total for 2025 (₹ Crore) | (2,31,990.59) | 73,583.09 | (1,58,407.50)")
    passage = SimpleNamespace(passage_id="S15.1", source_id="S15", text=text, kind="table", merged_ids=None)
    charts = charts_from_passage(passage, 1, None)
    assert len(charts) == 2  # yearly and monthly rows apart; the Total row is not plotted
    yearly = charts[0].spec
    assert [s["name"] for s in yearly["series"]] == ["Secondary", "Primary"]  # Equity = their sum, dropped
    assert yearly["metric"] == "FPI Flows"
    assert [p["label"] for p in yearly["series"][0]["data"]] == ["2022", "2023", "2024"]
    assert yearly["series"][0]["data"][0]["value"] == -146048.38


def test_accounting_negative_and_multiple_suffix_verify():
    from utils.chart_evidence_audit import _quote_supports
    q = "Calendar 2022 (₹ Crore) | (146,048.38) | 24,608.94"
    assert _quote_supports(-146048.38, "(146,048.38)", q, q)
    q2 = "Tue, 10 Nov 2026 | ₹10.1t | 20.2x | 35.6x"
    assert _quote_supports(20.2, "20.2x", q2, q2)
    q3 = "MAX HEALTHCARE | ₹89058 cr | 12.4%"
    assert _quote_supports(89058.0, "₹89058 cr", q3, q3)


def test_every_chart_gets_a_data_reading():
    from utils.chart_story import interpret_chart
    spec = {"type": "horizontal_bar", "yLabel": "Net FII Flow (Rs crore)", "series": [{"name": "Net FII Flow", "data": [
        {"label": "January", "value": -35962, "evidenceValue": "-35,962"},
        {"label": "February", "value": 22615, "evidenceValue": "+22,615"},
        {"label": "March", "value": -117775, "evidenceValue": "-1,17,775"}]}]}
    text = interpret_chart(spec, ["S35.1"])
    assert text == ("1 of the 3 values shown was positive: February was highest at +₹22,615 crore and March lowest at "
                    "-₹1,17,775 crore [S35.1].")
    line = {"type": "line", "unit": "x", "series": [{"name": "PE", "data": [
        {"label": "10 Nov 2026", "value": 20.2, "evidenceValue": "20.2x"},
        {"label": "07 Sep 2025", "value": 38.8, "evidenceValue": "38.8x"},
        {"label": "26 Mar 2025", "value": 34, "evidenceValue": "34x"}]}]}
    assert interpret_chart(line, ["S77.1"]).startswith("PE went from 34x on 26 Mar 2025 to 20.2x on 10 Nov 2026")


def test_one_window_for_past_n_years():
    import datetime
    from utils.source_quality import window_start
    today = datetime.date(2026, 11, 10)
    assert window_start("flows in the past 10 years", today) == datetime.date(2016, 11, 10)
    assert window_start("since 2022", today) == datetime.date(2022, 1, 1)


def test_near_duplicate_chart_values_flagged():
    from utils.chart_lint import quality_reason
    spec = {"type": "bar", "series": [{"name": "x", "data": [{"label": f"Sector {c}", "value": v} for c, v in zip("ABCDEF", [5, 7, 9, 11, 13, 15])]}]}
    earlier = [sorted([5.0, 7.0, 9.0, 11.0, 13.0, 21.0])]
    assert "nearly the same values" in quality_reason(spec, "", earlier)
