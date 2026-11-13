from utils.evidence_dataset import _unit_family, _unit_hint


def test_unqualified_crores_keep_the_currency_unstated():
    token, currency = _unit_hint("(In Crores)")

    assert token.casefold() == "crores"
    assert currency == ""
    assert _unit_family(token, currency) == ("crore (as reported)", 1.0)


def test_explicit_rupee_crores_keep_the_currency_and_scale():
    token, currency = _unit_hint("Market Cap (Rs Cr)")

    assert token.casefold() == "cr"
    assert currency.casefold().startswith("rs")
    assert _unit_family(token, currency) == ("₹ crore", 1.0)


def test_unqualified_lakhs_are_not_relabelled_as_rupees():
    token, currency = _unit_hint("Amount in lakh")

    assert token.casefold() == "lakh"
    assert currency == ""
    assert _unit_family(token, currency) == ("lakh (as reported)", 1.0)
