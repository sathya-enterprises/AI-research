"""Table shapes from real publishers that used to give no chart (any topic)."""
from types import SimpleNamespace

from utils.evidence_store import _split_tables_and_text
from utils.section_charts import _focused_charts, _parse_number


def _passage(text: str):
    return SimpleNamespace(text=text, passage_id="S1.1", source_id="S1", kind="table", reliability=0.9,
                           parent_source_id="", parent_data_context="", title="", url="")


def test_unit_row_under_a_year_header_becomes_a_time_series():
    text = ("TABLE 1 — Emissions by country\n"
            "Country | 1990 | 2000 | 2015 | 2020 | 2023\n"
            "unit | Mton CO 2 | Mton CO 2 | Mton CO 2 | Mton CO 2 | Mton CO 2\n"
            "EU27 | 3809.70 | 3563.26 | 3086.21 | 2641.19 | 2500.10\n"
            "China | 2400.10 | 3500.20 | 10500.30 | 11600.40 | 12600.50\n"
            "United States | 5100.10 | 6000.20 | 5300.30 | 4700.40 | 4900.50\n"
            "India | 600.10 | 1000.20 | 2300.30 | 2400.40 | 2900.50")
    charts = _focused_charts(_passage(text), 1, None, None, 2015, 2023)
    assert charts and charts[0].spec["type"] == "line"
    assert "Mton CO 2" in charts[0].spec["title"]
    assert {s["name"] for s in charts[0].spec["series"]} == {"EU27", "China", "United States", "India"}


def test_grouped_two_row_header_with_serial_column_is_charted_by_crop():
    text = ("TABLE 19\n"
            "Sl. No. | Rabi Crops | Cost | MSP\n"
            "2021-22 | 2025-26 | Difference | 2021-22 | 2025-26 | Difference\n"
            "1 | Wheat | 960 | 1182 | 222 | 1975 | 2425 | 450\n"
            "2 | Barley | 971 | 1239 | 268 | 1600 | 1980 | 380\n"
            "3 | Gram | 2866 | 3527 | 661 | 5100 | 5650 | 550\n"
            "4 | Masur | 2866 | 3537 | 671 | 5500 | 6700 | 1200")
    charts = _focused_charts(_passage(text), 1, None, None, None, None)
    assert charts
    labels = {label for _series, label, _value in charts[0].points}
    assert {"Wheat", "Barley", "Gram"} <= labels
    # every data row is still a substring of the source row, so evidence quotes keep matching
    assert "1 | Wheat | 960 | 1182 | 222 | 1975 | 2425 | 450" in text


def test_capital_magnitude_letters_are_scales_but_lower_case_units_are_not():
    assert _parse_number("$462.6B") == (462.6, "USD billion")
    assert _parse_number("12M") == (12.0, "million")
    assert _parse_number("5m") is None


def test_flattened_pdf_table_under_a_period_header_is_rebuilt_as_a_table():
    page = ("Subscriber base (in million)\n"
            "Mar-24 Jun-24 Sep-24\n"
            "Wireless 1170.53 1153.72 1150.66\n"
            "Wireline 35.11 36.93 39.27\n"
            "Total 1205.64 1190.66 1189.92\n"
            "The subscriber base was broadly stable over the period.")
    tables, narrative = _split_tables_and_text(page)
    assert tables, narrative
    heading, rows = tables[0]
    assert heading == "Subscriber base (in million)"
    assert rows[0].startswith("| ") and "Mar-24" in rows[0] and "Sep-24" in rows[0]
    assert "| Wireless | 1170.53 | 1153.72 | 1150.66 |" in rows
    assert "broadly stable" in narrative


def test_flattened_rows_without_a_period_header_stay_text():
    # a column whose period is unknown must not be charted against a guessed date
    page = "Change over months\nWireless 1170.53 1153.72 1150.66\nWireline 35.11 36.93 39.27\nTotal 1205.64 1190.66 1189.92"
    tables, _narrative = _split_tables_and_text(page)
    assert not tables
