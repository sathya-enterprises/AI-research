"""Regression: the joined-list-item repair must not split decimals like 40.90)."""
from utils.markdown_normalizer import normalize_report_markdown


def test_decimal_followed_by_parenthesis_is_not_split():
    text = "Pharma P/E rose 5.7% (from 38.69 to 40.90) during the quarter [S19].\n\nNext paragraph."
    assert "40.90) during" in normalize_report_markdown(text)


def test_joined_list_item_after_a_citation_is_still_split():
    text = "The first point is supported [S31].3. **The next point** follows here."
    assert "[S31].\n\n3. **The next point**" in normalize_report_markdown(text)
