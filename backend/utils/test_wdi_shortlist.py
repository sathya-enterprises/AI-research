"""The full World Development Indicators list widens the official-data planner's choices for any topic."""
from utils.official_data import CATALOG, wdi_shortlist


def _titles(question):
    return [CATALOG[i].title for i in wdi_shortlist(question, limit=8)]


def test_wdi_list_is_bundled_and_matches_by_subject():
    assert any("Military expenditure" == t for t in _titles("How has India's defence spending grown since 2015?"))
    assert any("Renewable electricity output" == t for t in _titles("How have solar and wind power shares changed in India?"))
    assert any("commercial bank" in t.casefold() or "bank" in t.casefold()
               for t in _titles("How did Indian commercial banks' credit and capital change?"))


def test_breakdowns_and_single_common_words_rank_low():
    titles = _titles("How has school enrollment in India changed?")
    assert titles and not any(t.casefold().endswith(("female", "male")) for t in titles[:2])
    assert wdi_shortlist("how have things changed", limit=8) == []


def test_curated_series_are_not_duplicated():
    ids = wdi_shortlist("How did international tourism arrivals in India recover?", limit=25)
    assert all(CATALOG[i].code != "ST.INT.ARVL" for i in ids)  # already in the curated catalog


def test_a_second_report_gets_the_same_shortlist():
    question = "How have solar and wind power shares changed in India?"
    first = wdi_shortlist(question, limit=8)
    assert wdi_shortlist(question, limit=8) == first
