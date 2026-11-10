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
