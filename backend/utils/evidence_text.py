"""One reading of evidence text, shared by every checker.

The fact sheet, the chart builder, the number policy, the chart gate and the publication audit each had their
own sentence splitter and table-header rules; a parsing fix (the "Rs." sentence split, year headers, stacked
tables, split decimals) had to be repeated in each, and the copies drifted apart. These are the single
implementations; the modules above import them.
"""
from __future__ import annotations

import re

# A sentence ends at ".", "!" or "?" - but not after an abbreviation that is followed by more of the same
# sentence ("Rs. 4.67 lakh crore", "No. 5", "vs. 2023", "Mr. Rao", "e.g. the"). Each guard is a fixed-width
# lookbehind, so the pattern can be used inside re.split / re.compile anywhere.
_ABBREVIATIONS = ("Rs", "Re", "No", "Nos", "vs", "Mr", "Mrs", "Ms", "Dr", "St", "Co", "Ltd", "Inc", "Fig", "Figs",
                  "approx", "Govt", "Dept", "Vol", "Jr", "Sr")
SENTENCE_END = r"(?<=[.!?])" + "".join(rf"(?<!\b{a}\.)" for a in _ABBREVIATIONS) + r"(?<!\be\.g\.)(?<!\bi\.e\.)"


def split_sentences(text: str, *, next_char: str = "") -> list[str]:
    """Sentences of ``text`` (separators dropped). ``next_char``: an optional character class the next
    sentence must start with (e.g. ``"[A-Z0-9₹$(]"``)."""
    pattern = SENTENCE_END + r"\s+" + (f"(?={next_char})" if next_char else "")
    return [part for part in re.split(pattern, str(text or "")) if part]


# Page markup splits a decimal across inline tags ("6 .09 percent", "4911 .81"): one number, not two.
_SPLIT_DECIMAL_RE = re.compile(r"(?<=\d) \.(?=\d)")


# Scanned filings (OCR) print an Indian digit-group comma as a dot: "2,04.11,079" and "1,94,98.166" for
# 2,04,11,079 and 1,94,98,166 shares. A dot after a two-digit group is grouping, not a decimal point, when
# another group follows ("04.11,079") or when three digits end the number ("98.166": a 2-digit group cannot
# be the last group before a decimal). Live run: the writer wrote the right value, the check found only the
# misprint and removed the table rows (promoter holdings).
_OCR_GROUP_DOT_RE = re.compile(r"(?<=\d,\d{2})\.(?=\d{2},\d{2,3}(?!\d))|(?<=\d,\d{2})\.(?=\d{3}(?![\d.,]))")


def normalize_numbers(text: str) -> str:
    return _OCR_GROUP_DOT_RE.sub(",", _SPLIT_DECIMAL_RE.sub(".", str(text or "")))


def cells_of(line: str) -> list[str]:
    return [c.strip() for c in str(line or "").strip().strip("|").split("|")]


_VALUE_CELL_RE = re.compile(
    r"[\s+\-−–(]*(?:₹|\$|€|£|Rs\.?|INR|USD)?\s*[\d,]*\.?\d+\s*"
    r"(?:%|x|pts|bps|cr|crore|lakh|bn|mn|million|billion|trillion|thousands?|gw|mw|kw|gwh|mwh|twh|kwh|mt|kt|kg|km|"
    r"ha|tonnes?|years?|yrs?)?\s*\)?", re.I)
_PERIOD_CELL_RE = re.compile(
    r"(?:FY\s*)?'?(?:19|20)?\d{2}(?:[-/]\d{2,4})?|Q[1-4]\s*(?:FY|CY)?\s*'?\d{2,4}|H[12]\s*FY\s*'?\d{2,4}|"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?[\s'-]*\d{2,4}", re.I)


def is_header_row(line_or_cells) -> bool:
    """A table header row: distinct text cells, none of them a value, a date or a bare period
    ("Sector | Total No. of SME IPOs | Issue size (₹ cr)"); digits inside words ("5G", "FY2024 target") are fine."""
    cells = cells_of(line_or_cells) if isinstance(line_or_cells, str) else [str(c).strip() for c in line_or_cells]
    return (len(cells) >= 2 and all(cells) and len(set(cells)) > 1
            and all(re.search(r"[A-Za-z]{2,}", c) for c in cells)
            # Period names are column names ("Particulars | Mar-20 | Mar-21"); a data row has value cells.
            and not any(_VALUE_CELL_RE.fullmatch(c) or re.fullmatch(r"[\d./-]+", c) for c in cells))


def is_column_numbering(cells: list[str]) -> bool:
    """A row that only numbers the table's columns: "1 | 2 | 3 | 4" or "(1) | (2) | (3)"."""
    numbers = [re.fullmatch(r"\(?\s*(\d{1,2})\s*\)?", c.strip()) for c in cells]
    if len(cells) < 3 or not all(numbers):
        return False
    values = [int(m.group(1)) for m in numbers]
    return values == list(range(values[0], values[0] + len(values))) and values[0] in (0, 1, 2)


_SCHEMA_FIRST_RE = re.compile(r"(?i)^(?:column|field|variable|attribute)(?:\s+name)?$")
_SCHEMA_OTHER_RE = re.compile(r"(?i)^(?:type|data\s*type|dtype|distinct|unique|count|non[- ]?null|nulls?|missing|description)$")


def is_schema_table(header: list[str]) -> bool:
    """A dataset's column listing ("Column | Type | Distinct | Count | Description"): it describes the data."""
    cells = [c.strip() for c in header]
    return (len(cells) >= 3 and bool(_SCHEMA_FIRST_RE.match(cells[0]))
            and sum(bool(_SCHEMA_OTHER_RE.match(c)) for c in cells[1:]) >= 2)


def stacked_table_starts(lines: list[str]) -> list[int]:
    """Indexes of header rows that start a new table after data rows (two tables printed one under the
    other without a caption between them)."""
    starts, seen_data = [], False
    for i, line in enumerate(lines):
        if " | " not in line:
            continue
        if is_header_row(line):
            if seen_data:
                starts.append(i)
                seen_data = False
        else:
            seen_data = True
    return starts
