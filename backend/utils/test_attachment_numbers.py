"""Attachment-only reports: prose numbers must come from the attached files or the deterministic analytics."""
from utils.attachment_numbers import allowed_numbers, check_attachment_numbers

SHEET = """[File: PMS.xlsx]
[Sheet: Table 2]
AMC,INVESTMENT APPROACH,BENCHMARK (TRI),AUM (IN Crs),CATEGORY,1 Mth,1 Yr
AEQUITAS,INDIA OPPORTUNITIES,NIFTY 50,4862.15,SMALL CAP,15.60%,62.34%
2POINT2 CAPITAL,LONG TERM VALUE FUND,S&P BSE 500,1835.56,MULTI CAP,0.90%,24.80%"""

ANALYTICS = "| 1 Yr | [S1] | 450 | 88.9% | 3.61% | 3.67% | -21.35% to 62.34% |"


def _allowed():
    return allowed_numbers(SHEET, ANALYTICS)


def test_numbers_in_the_file_or_analytics_are_kept_with_rounding():
    for sentence in ("Aequitas returned 62.34% over one year [S1].", "Aequitas returned 62.3% over one year [S1].",
                     "Its AUM was 4,862.15 crore [S1].", "2POINT2 manages about 1,836 crore [S1].",
                     "The median one-year return was 3.67% across 450 strategies [S1]."):
        text, removed = check_attachment_numbers(sentence, _allowed())
        assert not removed and text == sentence


def test_invented_numbers_are_removed():
    for sentence in ("Aequitas returned 73.91% over one year [S1].", "The workbook covers 1,234 strategies [S1].",
                     "2POINT2 manages 2,835.56 crore [S1].", "A spread of 58.67 percentage points separated them [S1]."):
        text, removed = check_attachment_numbers(sentence, _allowed())
        assert removed and text == ""


def test_csv_cells_are_separate_values_and_percent_cells_are_not_rescaled():
    allowed = allowed_numbers("2POINT2,FUND,S&P BSE 500,1835.56,MULTI CAP,0.90%,2.70%")
    assert "1835.56" in allowed and "0.90" in allowed
    assert "90" not in allowed  # 0.90% is not 90
    _, removed = check_attachment_numbers("One fund returned 90% [S1].", allowed)
    assert removed


def test_only_the_bad_sentence_goes_and_tables_headings_and_small_numbers_stay():
    report = ("## Leaders\n\nAequitas returned 62.34% over 1 year [S1]. It then returned 99.99% in 2025 [S1].\n\n"
              "| Rank | Strategy | Return |\n|---:|---|---:|\n| 1 | X | 77.77% |\n\nThe top 5 strategies are listed in 3 tables [S1].")
    text, removed = check_attachment_numbers(report, _allowed())
    assert "62.34%" in text and "99.99%" not in text and "| 1 | X | 77.77% |" in text
    assert "## Leaders" in text and "The top 5 strategies are listed in 3 tables [S1]." in text
    assert len(removed) == 1 and removed[0]["numbers"] == ["99.99"]


def test_small_pms_categories_are_listed_but_not_ranked():
    from utils.uploaded_workbook_analytics import attachment_analytics_section
    analytics = {
        "kind": "pms_strategy_performance", "files": [{"filename": "PMS.xlsx"}], "datasets": [],
        "periodSummaries": [{"period": "1 Yr", "available": 3, "coverage": 1.0, "mean": 1, "median": 1, "min": 0, "max": 2}],
        "categorySummaries": [
            {"category": "SECTOR FUND", "available": 1, "mean1Year": 22.15, "median1Year": 22.15},
            {"category": "LARGE CAP", "available": 28, "mean1Year": 6.89, "median1Year": 7.22},
            {"category": "MULTI CAP", "available": 249, "mean1Year": 3.65, "median1Year": 3.70},
        ],
        "charts": [],
    }
    section = attachment_analytics_section(analytics, "S1", {"pms.xlsx": "S1"})
    rows = [line for line in section.splitlines() if line.startswith("| ") and "%" in line and "Yr" not in line]
    assert rows[0].startswith("| LARGE CAP") and rows[1].startswith("| MULTI CAP")
    assert rows[2].startswith("| SECTOR FUND (too few to rank)")
    assert "fewer than 3 strategies" in section


def test_differences_and_shares_of_numbers_in_the_same_sentence_are_kept():
    allowed = allowed_numbers("LARGE CAP,6.89%\nSMALL CAP,-3.57%\nstrategies 506 undisclosed 287")
    kept = ["Large Cap beat Small Cap by 10.46 percentage points (+6.89% vs -3.57%) [S1].",
            "Of 506 strategies, 287 (56.7%) do not disclose AUM [S1]."]
    for sentence in kept:
        assert check_attachment_numbers(sentence, allowed)[1] == []
    wrong = ["Large Cap beat Small Cap by 12.00 points (+6.89% vs -3.57%) [S1].",
             "Of 506 strategies, 237 (46.8%) do not disclose AUM [S1]."]
    for sentence in wrong:
        assert check_attachment_numbers(sentence, allowed)[1]


def test_numbers_inside_text_cells_are_allowed():
    allowed = allowed_numbers("AMC,APPROACH,S&P BSE 500,1835.56,MULTI CAP,0.90%,2.70%,5.90%")
    assert check_attachment_numbers("The S&P BSE 500 benchmark [S1].", allowed)[1] == []


def test_pms_aum_disclosure_is_counted():
    from utils.uploaded_workbook_analytics import attachment_analytics_section
    analytics = {
        "kind": "pms_strategy_performance", "files": [{"filename": "PMS.xlsx"}], "datasets": [], "charts": [],
        "periodSummaries": [{"period": "1 Yr", "available": 3, "coverage": 1.0, "mean": 1, "median": 1, "min": 0, "max": 2}],
        "strategies": [{"aum": 100.0}, {"aum": 300.0}, {"aum": None}, {"aum": None}],
    }
    section = attachment_analytics_section(analytics, "S1", {"pms.xlsx": "S1"})
    assert "| Strategies with AUM not disclosed | [S1] | 2 (50.0%) |" in section
    assert "| Total reported AUM (₹ crore) | [S1] | 400.00 |" in section and "| Median reported AUM (₹ crore) | [S1] | 200.00 |" in section
