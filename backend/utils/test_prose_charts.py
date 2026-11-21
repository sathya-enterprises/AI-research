"""Charts from figures stated in one sentence of a source (utils.prose_charts)."""
from types import SimpleNamespace

from utils.prose_charts import prose_charts


def _store(text: str):
    return SimpleNamespace(passages=[SimpleNamespace(kind="narrative", reliability=0.8, text=text,
                                                     source_id="S3", passage_id="S3.2")])


def test_time_series_in_one_sentence_becomes_a_line_chart_quoting_that_sentence():
    sentence = ("Indeed, the share of Russian natural gas (both pipeline and LNG) in total EU gas imports, which "
                "increased 35 percent in 2015 to 41 percent in 2020, dropped to 11 percent in 2023.")
    charts = prose_charts(_store(sentence), "How did the share of Russian gas in EU imports change")
    assert len(charts) == 1
    chart = charts[0]
    assert chart.spec["type"] == "line"
    assert chart.spec["title"] == "Share of Russian natural gas in total EU gas imports (%)"
    assert [(label, value) for _s, label, value in chart.points] == [("2015", 35.0), ("2020", 41.0), ("2023", 11.0)]
    point = chart.spec["series"][0]["data"][0]
    assert point["evidenceQuote"] == sentence and point["evidenceValue"] == "35" and chart.spec["fromSourceSentence"]


def test_category_comparison_takes_the_shared_measure_as_its_title():
    sentence = "Primary GER stands at 90.9%, upper primary at 90.3%, secondary at 78.7%, and higher secondary at 58.4%."
    charts = prose_charts(_store(sentence), "How have gross enrolment ratios (GER) in Indian schools changed")
    assert len(charts) == 1
    assert charts[0].spec["title"] == "GER (%)"
    assert [label for _s, label, _v in charts[0].points] == ["Primary GER", "upper primary", "secondary", "higher secondary"]


def test_clauses_and_fragments_are_not_charted():
    question = "How did FPI and DII flows and sector returns move in Indian markets"
    for sentence in (
        # labels are clauses with verbs
        "DII inflows remain strong at USD 7.4 billion while FIIs have pulled out USD 17 billion and DIIs have invested USD 65 billion.",
        # no measure is named: the labels share nothing
        "Financial services attracted ₹43,102 crore, telecom ₹7,600 crore, automobiles ₹7,600 crore and IT ₹5,100 crore.",
        # mixed measures and units
        "Sales rose 12% in 2022 and the company said it had 3 plants and 400 dealers, up 5% from last year.",
    ):
        assert prose_charts(_store(sentence), question) == [], sentence


def test_off_topic_sentences_and_search_snippets_are_ignored():
    sentence = "Primary GER stands at 90.9%, upper primary at 90.3%, secondary at 78.7%, and higher secondary at 58.4%."
    assert prose_charts(_store(sentence), "Brent crude oil prices and refinery margins") == []
    snippet = SimpleNamespace(passages=[SimpleNamespace(kind="search_excerpt", reliability=0.9, text=sentence,
                                                        source_id="S1", passage_id="S1.1")])
    assert prose_charts(snippet, "How have gross enrolment ratios (GER) in Indian schools changed") == []
