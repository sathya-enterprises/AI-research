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
