"""Stage L: a table is shown to readers only when it is a readable table.

Junk that reached the appendix of a live report:
  * a "header" that is a data row ("REALTY | 21.77", "TATASTEEL | TATA STEEL LTD. | METALS & MINING | 17.04",
    "DIVISLAB ... | ₹11.57K | 16.3% + ₹430.1B") - a page whose table had no header row;
  * separator rows printed as cells ("--- | ---");
  * a header cell that is only a unit ("(US$ billion and Per cent of GDP)") - the metric name was lost;
  * a stray row from another block of the page (one cell repeating the first: "DLF | DLF Ltd.").
"""
from __future__ import annotations

import re

_SEP_CELL_RE = re.compile(r"^\s*:?-{3,}:?\s*$")
_UNIT_ONLY_RE = re.compile(r"^\s*\((?:[^()]*)\)\s*$")


def _cells(row: str) -> list[str]:
    return [c.strip() for c in row.strip().strip("|").split("|")]


def table_problem(header: str, body_rows: list[str]) -> str:
    """'' for a readable table, else the reason it is not shown."""
    from utils.chart_titles import header_is_data
    head = _cells(header)
    if len(head) < 2 or not body_rows:
        return "fewer than two columns or no rows"
    def _is_year(cell: str) -> bool:
        return bool(re.fullmatch(r"\s*(?:19|20)\d{2}(?:\s*[-–/]\s*\d{2,4})?\s*", cell))

    if any(header_is_data(c) and not _is_year(c) for c in head[1:]) or re.search(r"[₹$]\s?\d|\d\s?%|\d+(?:\.\d+)?[xX]\b", " ".join(head[1:])):
        return "the first row is data, not a header"
    if any(_UNIT_ONLY_RE.match(c) for c in head[1:]):
        return "a column header is only a unit (its metric name is missing)"
    rows = [_cells(r) for r in body_rows]
    if any(all(_SEP_CELL_RE.match(c) or not c for c in r) for r in rows):
        return "separator rows printed as data"
    if not any(re.search(r"\d", " ".join(r[1:])) for r in rows) and len(head) <= 2:
        return ""  # a two-column text table (e.g. trigger / why) is fine
    return ""


def clean_rows(header: str, body_rows: list[str]) -> list[str]:
    """Drop stray rows: a row whose second cell only restates the first ("DLF | DLF Ltd.") while the
    other rows hold different content."""
    width = len(_cells(header))
    out = []
    for r in body_rows:
        cells = _cells(r)
        if width >= 2 and len(cells) >= 2:
            a, b = cells[0].casefold(), re.sub(r"(?i)\s+(?:ltd|limited|inc|plc)\.?$", "", cells[1]).casefold()
            if a and b and (a == b or (a in b and len(b) - len(a) <= 6)) and all(not c for c in cells[2:]):
                continue
        out.append(r)
    return out
