"""Choosing which verified charts a report shows (utils.chart_select) and the market topic vocabulary."""
from utils.chart_evidence_audit import chart_meaning_reason
from utils.chart_select import index_level_comparison, select_charts, short_window_question
from utils.section_charts import _period_end_year

MONTH_Q = ("Produce a report with graphs on how the Indian share market performed over the last one month, "
           "and what share of the move came from foreign and domestic investor flows.")


def _chart(title, labels, values, source="S1"):
    return {"type": "horizontal_bar", "title": title, "source": [source],
            "series": [{"name": title.split(":")[0], "data": [{"label": l, "value": v} for l, v in zip(labels, values)]}]}


def test_market_questions_accept_index_and_flow_charts_but_not_unrelated_tables():
    for title in ("Day change (%): Nifty 50, Sensex, Nifty Bank", "BUY VALUE (Cr) (crore): 20 Nov, 2026, 19 Nov, 2026",
                  "% Change: HDFC Bank, Axis Bank, ICICI Bank", "PE Ratio: Nifty PSU Bank, Nifty IT"):
        assert chart_meaning_reason(_chart(title, ["a", "b", "c"], [1, 2, 3]), MONTH_Q, []) == "", title
    assert chart_meaning_reason(_chart("Issue Size(Cr) (crore): Crystal Crop Protection Ltd", ["a", "b", "c"], [1, 2, 3]),
                                MONTH_Q, [])


def test_index_levels_are_not_compared_across_indices_but_ratios_and_changes_are():
    labels = ["Sensex", "Nifty Next 50", "Nifty Bank", "Nifty 50"]
    assert index_level_comparison(_chart("Value (points): 4 NSE indices", labels, [82960, 72848, 63022, 25503]))
    assert not index_level_comparison(_chart("PE Ratio: indices", labels, [22, 30, 14, 21]))
    assert not index_level_comparison(_chart("Day change (%): indices", labels, [1.1, 0.9, 2.5, 1.1]))


def test_short_window_questions_drop_old_long_horizon_and_repeated_views():
    assert short_window_question(MONTH_Q) and not short_window_question("sector rotation in the past 8 quarters")
    charts = [
        _chart("Day change (%): Nifty 50, Sensex", ["Nifty 50", "Sensex", "Nifty Bank"], [1.1, 1.0, 2.5]),
        _chart("2Y SIP return (%): Nifty 50", ["Nifty 50", "Nifty 500", "Midcap"], [12, 14, 18]),
        _chart("2024Q4: Capital Goods, FMCG", ["Capital Goods", "FMCG", "Banks"], [3, 4, 5]),
        _chart("17.95 15.12 12.03: Brigade", ["a", "b", "c"], [1, 2, 3]),
        *[_chart(f"Change (%): stocks {i}", [f"s{i}a", f"s{i}b", f"s{i}c"], [1 + i, 2 + i, 3 + i], source=f"S{9 + i}")
          for i in range(4)],
    ]
    domains = {f"S{9 + i}": "moneycontrol.com" for i in range(4)}
    kept, dropped = select_charts(charts, domains, MONTH_Q, today="2026-11-21")
    titles = [c["title"] for c in kept]
    assert titles[0].startswith("Day change") and sum(t.startswith("Change (%)") for t in titles) == 2
    assert not any(t.startswith(("2Y SIP", "2024Q4", "17.95")) for t in titles)
    assert len(dropped) == 5


def test_long_horizon_questions_keep_repeated_measures_for_different_data():
    charts = [_chart(f"Registrations: maker set {i}", [f"m{i}a", f"m{i}b", f"m{i}c"], [10 + i, 20 + i, 30 + i],
                     source=f"S{i}") for i in range(4)]
    kept, dropped = select_charts(charts, {f"S{i}": "vahan.parivahan.gov.in" for i in range(4)},
                                  "EV registrations in India from FY2020 to FY2025", today="2026-11-21")
    assert len(kept) == 4 and not dropped


def test_month_and_quarter_labels_with_two_digit_or_compact_years_are_dated():
    assert _period_end_year("Aug'25") == 2025 and _period_end_year("Aug’25") == 2025 and _period_end_year("Sep-25") == 2025
    assert _period_end_year("2024Q4") == 2024 and _period_end_year("Mar 25") is None


def test_a_sector_title_names_its_subject_before_the_colon():
    from types import SimpleNamespace
    from utils.report_outline import Section, _headline_entities
    cement = SimpleNamespace(name="Cement", aliases=[])
    energy = SimpleNamespace(name="Energy", aliases=["power", "oil and gas"])
    section = Section(section_id="sec-6", title="Cement: Pricing Power and Volume Trends", role="deep_dive", entity="Cement")
    assert [e.name for e in _headline_entities(section, [cement, energy])] == ["Cement"]
    merged = Section(section_id="sec-7", title="Cement and Power: Capacity", role="deep_dive", entity="")
    assert {e.name for e in _headline_entities(merged, [cement, energy])} == {"Cement", "Energy"}


def test_misread_tables_and_point_changes_across_indices_are_dropped_but_year_series_are_kept():
    from utils.chart_select import index_point_change_comparison, numeric_labels
    assert numeric_labels(_chart("Jan-26: 13 indices", ["-2.36", "-3.34", "-1.07", "-4.13"], [1.38, 0.46, -0.33, -0.66]))
    assert numeric_labels(_chart("Feb’26 Mar’26", ["1336 343 455 292", "529 -1311 -39", "-459 -225 -827"], [1, 2, 3]))
    assert not numeric_labels(_chart("Users (%)", ["2015", "2016", "2017", "2018"], [10, 12, 15, 19]))
    labels = ["Nifty Bank", "Nifty Financial Services", "Nifty IT"]
    assert index_point_change_comparison(_chart("Chg: 3 NSE sector indices", labels, [1572.2, 571.55, 403.2]))
    assert not index_point_change_comparison(_chart("Day change (%): indices", labels, [2.49, 2.02, 1.3]))


def test_short_window_movers_cap_counts_the_view_across_sites():
    charts = [_chart(f"Change (%): stocks {i}", [f"s{i}a", f"s{i}b", f"s{i}c"], [1 + i, 2 + i, 3 + i], source=f"S{i}")
              for i in range(4)]
    domains = {"S0": "moneycontrol.com", "S1": "icicidirect.com", "S2": "moneycontrol.com", "S3": "livemint.com"}
    kept, _dropped = select_charts(charts, domains, MONTH_Q, today="2026-11-21")
    assert len(kept) == 2


def test_a_buy_sell_net_flow_table_over_dates_is_charted_as_net_in_date_order():
    from types import SimpleNamespace
    from utils.section_charts import _focused_charts
    text = ("TABLE 2 — FII/FPI trading activity\nDATE | BUY VALUE (Cr) | SELL VALUE (Cr) | NET VALUE (Cr)\n"
            "20 Nov, 2026 | 23,596.40 | 13,287.98 | 10,308.42\n19 Nov, 2026 | 20,706.95 | 13,295.54 | 7,411.41\n"
            "18 Nov, 2026 | 17,250.10 | 12,100.20 | 5,149.90\n17 Nov, 2026 | 19,930.98 | 15,735.60 | 4,195.38")
    passage = SimpleNamespace(text=text, passage_id="S10.1", source_id="S10", kind="table", reliability=0.9,
                              parent_source_id="", parent_data_context="", title="", url="")
    chart = _focused_charts(passage, 1, None, None, None, None)[0]
    assert chart.spec["type"] == "line" and [s["name"] for s in chart.spec["series"]] == ["NET VALUE"]
    assert [label for _s, label, _v in chart.points][0] == "17 Nov, 2026"
    assert [value for _s, _l, value in chart.points] == [4195.38, 5149.9, 7411.41, 10308.42]
