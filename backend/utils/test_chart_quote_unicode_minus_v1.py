"""Regression: chart points quoted with a typographic minus (U+2212) must verify."""
from utils.chart_evidence_audit import _number_is_in, _quote_supports

ROW = "Nifty Auto | \u22120.7% | \u22129.9%"


def test_unicode_minus_values_are_found_in_their_quote():
    assert _number_is_in("\u22120.7%", ROW)
    assert _number_is_in("-0.7", ROW)
    assert not _number_is_in("0.8", ROW)


def test_unicode_minus_quote_supports_plotted_negative_value():
    source = "TABLE 1\nIndex | Q3 2026 | 2026 so far\n" + ROW
    assert _quote_supports(-0.7, "\u22120.7%", ROW, source)
    assert not _quote_supports(-0.8, "\u22120.7%", ROW, source)
