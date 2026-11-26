"""Chart titles keep their subject and measure; metadata tables and contained duplicates are dropped."""
from utils.chart_lint import caption_subject, contained_charts, fix_chart, quality_reason, run_chart_lint, table_caption


def _line(name, points, **extra):
    return {"type": "line", "title": "x", "xLabel": "Period", "yLabel": f"{name} (%)", "unit": "%", "metric": name,
            "series": [{"name": name, "data": [{"label": l, "value": v} for l, v in points]}], **extra}


XIAOMI = "TABLE 93 — Xiaomi’s Market Share in India by Year (2014-2024)\nYear | Market Share\n2024 | 20.70%"


def test_caption_and_subject():
    assert table_caption(XIAOMI) == "Xiaomi’s Market Share in India"
    assert caption_subject(table_caption(XIAOMI), "Market Share") == "Xiaomi in India"
    assert table_caption("TABLE 4 — What is the gold import duty in India right now?\n...") == ""
    assert table_caption("TABLE 9 — Witnessed the bulk of Aug’26 inflows\n...") == ""
    assert table_caption("TABLE 2 — Table 3.2\nA | B") == ""
    assert table_caption("Year | Share\n2021 | 4") == ""


def test_a_single_series_chart_gets_its_subject_from_the_source_caption():
    spec = _line("Market Share", [("2021", 27.31), ("2022", 25.98), ("2023", 22.81)])
    fixed, _ = fix_chart(spec, passage_text=XIAOMI)
    assert fixed["title"].startswith("Market Share (%): Xiaomi in India, 2021")
    plain, _ = fix_chart(spec, passage_text="Year | Market Share\n2021 | 27.31%")
    assert "Xiaomi" not in plain["title"]                       # nothing invented without a caption


def test_a_date_headed_column_is_named_by_the_row_header_or_dropped():
    spec = {"type": "horizontal_bar", "title": "x", "xLabel": "Sectoral flows", "yLabel": "Jan-26",
            "series": [{"name": "Jan-26", "data": [{"label": n, "value": v} for n, v in
                                                   [("IT", 5.0), ("Banks", 9.0), ("Auto", 2.0)]]}]}
    assert quality_reason(spec, "") == ""
    fixed, _ = fix_chart(spec)
    assert fixed["title"].startswith("Sectoral flows") and fixed["title"].endswith("Jan-26")
    spec["xLabel"] = "Item 3"
    assert "headed only by a date" in quality_reason(spec, "")


def test_document_and_regression_metadata_is_not_charted():
    for name in ("Page no.", "Rows", "Coefficient", "Obs", "Std. error", "p-value"):
        spec = {"type": "bar", "title": "x", "xLabel": "Variable", "yLabel": name,
                "series": [{"name": name, "data": [{"label": f"v{i}", "value": float(i * 3 + 1)} for i in range(4)]}]}
        assert "metadata" in quality_reason(spec, ""), name


def test_a_chart_whose_points_are_all_in_another_chart_is_dropped_whichever_comes_first():
    alone = _line("Market Share", [("2021", 27.31), ("2022", 25.98), ("2023", 22.81)])
    shorter = _line("Market Share", [("2021", 27.31), ("2022", 25.98), ("2023", 22.81)])
    brands = {"type": "line", "title": "Brands", "xLabel": "Period", "yLabel": "Value",
              "series": [{"name": "Xiaomi", "data": alone["series"][0]["data"]},
                         {"name": "Vivo", "data": [{"label": "2021", "value": 14.33}, {"label": "2022", "value": 15.73},
                                                   {"label": "2023", "value": 17.15}]}]}
    assert set(contained_charts([alone, brands, shorter])) == {1, 3}
    kept, dropped, _ = run_chart_lint([alone, brands, shorter])
    assert len(kept) == 1 and {d["index"] for d in dropped} == {1, 3}
    other_values = _line("Market Share", [("2021", 1.0), ("2022", 2.0), ("2023", 3.0)])
    assert contained_charts([alone, other_values]) == {}


def _bar(name, x_label):
    return {"type": "bar", "title": "x", "xLabel": x_label, "yLabel": name,
            "series": [{"name": name, "data": [{"label": f"Item {c}", "value": v} for c, v in zip("ABCD", (4.0, 9.0, 2.0, 7.0))]}]}


def test_page_text_headers_are_not_measures_but_long_measure_names_are():
    junk = [("-24 along with the Not nfra.com. Total No. of flights considered", "Airline"),
            ("12.57 12.01 11.22 10.20 8.68", "Bank"),
            ("NA", "e relatively lower isolation due to higher rainfall dity. In the south")]
    for name, x in junk:
        assert "page text" in quality_reason(_bar(name, x), ""), name
    fine = [("Solvency Losses as per cent of Tier 1 Capital of the Banking System", "HFC Name"),
            ("Share", "Share within agriculture and allied, current prices, 2025-26")]
    for name, x in fine:
        assert "page text" not in quality_reason(_bar(name, x), ""), name
