from utils.markdown_normalizer import normalize_report_markdown


def _table_rows(text: str) -> list[list[str]]:
    rows = []
    for line in text.splitlines():
        if line.strip().startswith("|") and line.strip().endswith("|"):
            cells = [part.strip() for part in line.strip()[1:-1].split("|")]
            if cells and not all(set(cell) <= set(":- ") for cell in cells):
                rows.append(cells)
    return rows


def test_literal_newlines_and_flattened_source_table_are_repaired():
    source = (
        "| Metric | 2014-2024 trend | Latest figure |\\n"
        "|---|---|---|\\n"
        "| Revenue growth (aggregate) | Consistent double-digit CAGR; TCS 20.43% avg. over 10 yr | — | [S1] | "
        "Export earnings share | ~73-75% of total trade exports | 73.7% | [S1] | "
        "Employee headcount | Growth from ~4 M (2014) to >5.5 M (2023) | — | — | "
        "Utilization rates | 78-80% for major firms | — | — | "
        "Operating margin | 20-25% for top-tier firms | — | — |\\n"
        "\\nNote: annual figures are limited.\\n"
    )
    normalized = normalize_report_markdown(source)
    rows = _table_rows(normalized)
    assert rows[0] == ["Metric", "2014-2024 trend", "Latest figure", "Source"]
    assert len(rows) == 6  # header + five data rows
    assert rows[1] == ["Revenue growth (aggregate)", "Consistent double-digit CAGR; TCS 20.43% avg. over 10 yr", "—", "[S1]"]
    assert rows[2] == ["Export earnings share", "~73-75% of total trade exports", "73.7%", "[S1]"]
    assert rows[3][0] == "Employee headcount"
    assert "Note: annual figures are limited." in normalized


def test_single_flattened_row_with_source_keeps_all_four_columns():
    normalized = normalize_report_markdown(
        "| Metric | Trend | Latest |\\n|---|---|---|\\n| Revenue | Double digit | 73.7% | [S1] |"
    )
    rows = _table_rows(normalized)
    assert rows[0] == ["Metric", "Trend", "Latest", "Source"]
    assert rows[1] == ["Revenue", "Double digit", "73.7%", "[S1]"]


def test_regular_markdown_table_remains_intact():
    source = "| Metric | Value |\\n|---|---:|\\n| Revenue | 100 |"
    normalized = normalize_report_markdown(source)
    assert "| Metric | Value |" in normalized
    assert "| Revenue | 100 |" in normalized
    assert len(_table_rows(normalized)) == 2


def test_formatting_repairs_do_not_leak_hard_breaks_or_ordered_list_escape():
    source = "First line" + "\\" + "\n" + "1" + "\\" + ". Second item\n" + "\\" + "> Quoted item"
    normalized = normalize_report_markdown(source)
    assert "\\" not in normalized
    assert "1. Second item" in normalized
    assert "> Quoted item" in normalized


def test_unverified_quantitative_table_values_become_neutral_dashes():
    normalized = normalize_report_markdown(
        "| Metric | Value |\n|---|---|\n| Credit growth | Not verified – 15% |\n| Deposit growth | a percentage not verified in retrieved publisher-page evidence |"
    )
    assert "Not verified" not in normalized
    assert "publisher-page evidence" not in normalized
    assert "| — |" in normalized or "| Credit growth | — |" in normalized


def test_unverified_prose_sentence_is_removed_not_published_as_placeholder():
    normalized = normalize_report_markdown(
        "The outlook is stable. Credit growth is projected at a percentage not verified in retrieved publisher-page evidence. Asset quality remains sound."
    )
    assert "not verified" not in normalized.casefold()
    assert "The outlook is stable." in normalized
    assert "Asset quality remains sound." in normalized


def test_html_callouts_and_lists_are_normalized_without_literal_tags():
    normalized = normalize_report_markdown(
        '<div style="border-left: 4px solid #1f77b4"><strong>Strategic implication:</strong> ' 
        'Banks should balance funding and asset quality.</div><br><ul><li>First point</li><li>Second point</li></ul>'
    )
    assert "<div" not in normalized and "<br" not in normalized and "<li" not in normalized
    assert "**Strategic implication:**" in normalized
    assert "First point" in normalized and "Second point" in normalized
