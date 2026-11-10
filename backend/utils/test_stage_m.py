"""Stage M: measure plan, shape checks on charted data, and where sources disagree - any topic."""
import asyncio
import datetime

from utils.disagreements import disagreement_note, find_disagreements
from utils.measure_plan import fallback_plan, measure_queries, plan_measures, validate
from utils.series_checks import chart_data_problem, jump_warnings

TODAY = datetime.date(2026, 11, 10)


class _Result:
    def __init__(self, data):
        self.ok, self._data = True, data

    def json(self):
        return self._data


class _Gateway:
    def __init__(self, data):
        self.data, self.calls = data, []

    async def generate(self, **kw):
        self.calls.append(kw["purpose"])
        return _Result(self.data)


def test_measure_plan_is_validated_and_becomes_data_searches():
    data = {"measures": [
        {"measure": "share of new car sales that are electric", "entities": ["India", "China"], "chart": "trend"},
        {"measure": "public EV charging stations", "entities": ["India"], "chart": "trend"},
        {"measure": "", "entities": []},
        {"measure": "EV sales 2024 were 1.9 million", "entities": ["India"]},
        {"measure": "electric two-wheeler sales by maker", "entities": [], "chart": "ranking"},
    ]}
    gw = _Gateway(data)
    plan = asyncio.run(plan_measures("How fast is EV adoption growing in India?", gw, today=TODAY, tier="in_depth"))
    assert gw.calls == ["measure_plan"] and plan.method == "llm"
    assert [m.measure for m in plan.measures] == ["share of new car sales that are electric", "public EV charging stations",
                                                  "electric two-wheeler sales by maker"]
    assert len(plan.rejected) == 2
    queries = measure_queries(plan, "EV adoption in India past 5 years", today=TODAY, tier="in_depth")
    assert queries[0].startswith("share of new car sales that are electric India China statistics by year")
    assert len(queries) == 6 and all("official" in q or "table" in q for q in queries)


def test_measure_plan_falls_back_without_the_llm():
    plan = asyncio.run(plan_measures("measles vaccination coverage in Africa", None, today=TODAY, tier="detailed"))
    assert plan.method.startswith("keywords") and plan.measures
    assert fallback_plan("x").measures == []
    assert validate({"measures": "nope"}, tier="detailed").measures == []


def _chart(title, labels_values, unit="", series="Value"):
    return {"type": "line", "title": title, "unit": unit, "yLabel": unit,
            "series": [{"name": series, "data": [{"label": l, "value": v} for l, v in labels_values]}]}


def test_single_place_chart_about_another_country_is_dropped():
    us = _chart("P/E ratio, United States (x): 11 sectors", [("Energy", 17.8), ("IT", 38.5), ("Utilities", 17.7)])
    assert "United States" in chart_data_problem(us, "Have FIIs fled India?")
    peers = _chart("EV share: India, China and United States", [("2023", 2.0), ("2024", 2.5), ("2025", 3.0)])
    assert chart_data_problem(peers, "EV adoption in India") == ""
    assert chart_data_problem(us, "Which sectors are expensive?") == ""  # no place asked: nothing to compare


def test_magnitudes_impossible_for_the_unit_are_dropped():
    rbi = _chart("Value (US$ billion): 11 economies", [("USA", 584978), ("UK", 517369), ("Mauritius", 509914)], "US$ billion")
    assert "billions" in chart_data_problem(rbi, "")
    pct = _chart("Share (%)", [("A", 1500), ("B", 20), ("C", 30)], "%")
    assert "percentages" in chart_data_problem(pct, "")
    ok = _chart("Net flow (₹ crore)", [("A", 150000), ("B", -20000), ("C", 3000)], "₹ crore")
    assert chart_data_problem(ok, "") == ""


def test_one_sudden_step_in_a_stable_level_series_is_flagged_not_dropped():
    mcap = _chart("Market cap (% of GDP): India", [("2022", 120.0), ("2023", 124.0), ("2024", 136.4), ("2025", 266.9)],
                  "% of GDP", "India")
    warnings = jump_warnings(mcap)
    assert warnings and "2.0x" in warnings[0] and chart_data_problem(mcap, "India") == ""
    ev = _chart("EV sales", [("2021", 100.0), ("2022", 210.0), ("2023", 400.0), ("2024", 820.0)])
    assert jump_warnings(ev) == []  # fast but steady growth is not a break
    flows = _chart("Net flows", [("2022", -146048.0), ("2023", 127759.0), ("2024", -121210.0)])
    assert jump_warnings(flows) == []  # flows swing sign: not a level series


def test_where_sources_disagree():
    report = ("## A: Flows\n\nNet FPI outflows from Indian equities reached ₹1,52,692 crore in FY26 [S3].\n\n"
              "## B: Flows\n\nNet FPI outflows from Indian equities were ₹1.8 trillion in FY26 [S85].\n\n"
              "## Realty: Volatility\n\nBy late October 2026, the net outflow was ₹438 crore [S76].\n\n"
              "## Telecom: Growth\n\nBy late October 2026, the net outflow was ₹1,559 crore [S73].")
    found = find_disagreements(report)
    assert len(found) == 1 and found[0]["period"] == "fy26"
    note = disagreement_note(found)
    assert "₹1,52,692 crore [S3] vs ₹1.8 trillion [S85]" in note and note.startswith("**Where sources disagree.**")
    same_source = "## A: X\n\nIndian sales of cars rose to ₹100 crore in FY26 [S1].\n\n## B: X\n\nIndian sales of cars were ₹300 crore in FY26 [S1]."
    assert find_disagreements(same_source) == []
