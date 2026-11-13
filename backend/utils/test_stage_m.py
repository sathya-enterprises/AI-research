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
    assert queries[0].startswith("share of new car sales that are electric India China annual time series")
    assert len(queries) == 6 and all("official" in q or "table" in q for q in queries)


def test_explicit_measure_units_override_related_but_unrequested_planner_suggestions():
    question = (
        "Compare internet use (% of individuals using the internet) and mobile-cellular subscriptions per 100 people "
        "in India, Kenya, and Brazil from 2015 to 2024. Show annual trends, the latest country gaps, "
        "and documented digital access drivers."
    )
    countries = ["India", "Kenya", "Brazil"]
    gw = _Gateway({"scope": "India, Kenya, Brazil", "measures": [
        {"measure": "percentage of individuals using the internet", "entities": countries, "chart": "trend"},
        {"measure": "cost of 1GB of mobile data as a percentage of monthly GNI per capita", "entities": countries, "chart": "trend"},
        {"measure": "4G or 5G network population coverage", "entities": countries, "chart": "comparison"},
    ]})

    plan = asyncio.run(plan_measures(question, gw, today=TODAY, tier="detailed"))

    assert gw.calls == ["measure_plan"]  # deterministic reconciliation uses no extra LLM call
    assert [m.measure for m in plan.measures] == [
        "internet use (% of individuals using the internet)",
        "mobile-cellular subscriptions per 100 people",
    ]
    assert all(m.entities == countries and m.chart == "trend" for m in plan.measures)
    assert {item["measure"] for item in plan.rejected} == {
        "cost of 1GB of mobile data as a percentage of monthly GNI per capita",
        "4G or 5G network population coverage",
    }
    queries = measure_queries(plan, question, today=TODAY, tier="detailed")
    assert len(queries) == 2
    assert all("2015 to 2024" in query and "annual time series" in query for query in queries)
    assert all(all(query.count(country) == 1 for country in countries) for query in queries)
    assert "mobile-cellular subscriptions per 100 people" in queries[1]
    assert not any("cost of 1GB" in query or "4G or 5G" in query for query in queries)


def test_one_valid_measure_is_kept_for_a_single_metric_question():
    gw = _Gateway({"scope": "India", "measures": [
        {"measure": "life expectancy at birth", "entities": [], "chart": "trend"},
    ]})
    plan = asyncio.run(plan_measures("How has life expectancy in India changed?", gw, today=TODAY, tier="detailed"))
    assert plan.method == "llm"
    assert [measure.measure for measure in plan.measures] == ["life expectancy at birth"]
    query = measure_queries(plan, "How has life expectancy in India changed?", today=TODAY,
                            tier="detailed")[0]
    assert "annual time series" in query and "life expectancy at birth" in query


def test_measure_search_shape_follows_comparison_share_and_ranking_requests():
    expected = {
        "How does renewable energy share break down by source?": ("share", "composition breakdown"),
        "How has electric vehicle share changed over time in India?": ("trend", "annual time series"),
        "Compare unemployment rates between Canada and Brazil": ("comparison", "comparison across entities"),
        "Rank countries by annual patent applications": ("ranking", "ranked values"),
    }
    for question, (kind, phrase) in expected.items():
        plan = fallback_plan(question)
        assert plan.measures and plan.measures[0].chart == kind
        queries = measure_queries(plan, question, today=TODAY, tier="detailed")
        assert queries and phrase in queries[0]


def test_measure_plan_falls_back_without_the_llm():
    plan = asyncio.run(plan_measures("measles vaccination coverage in Africa", None, today=TODAY, tier="detailed"))
    assert plan.method.startswith("keywords") and plan.measures
    assert fallback_plan("inflation").measures[0].measure == "inflation"
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


def test_disagreement_check_keeps_leading_country_entities_distinct():
    from utils.disagreements import find_disagreements, statements

    comparison = (
        "## Internet use\n\n"
        "In Brazil, internet usage was 84.46% in 2024 [S1].\n\n"
        "Kenya's internet usage was 34.98% in 2024 [S2]."
    )
    parsed = statements(comparison)
    assert [item["entities"] for item in parsed] == [{"brazil"}, {"kenya"}]
    assert find_disagreements(comparison) == []

    same_subject = (
        "## Brazil: Internet use\n\n"
        "Brazil's internet usage was 84.46% in 2024 [S1].\n\n"
        "Brazil's internet usage was 65% in 2024 [S2]."
    )
    assert len(find_disagreements(same_subject)) == 1


def test_disagreement_check_does_not_assume_an_unstated_shared_entity():
    report = (
        "## Internet use\n\nInternet usage was 84.46% in 2024 [S1].\n\n"
        "Internet usage was 34.98% in 2024 [S2]."
    )
    assert find_disagreements(report) == []
