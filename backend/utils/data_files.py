"""Turn a downloaded spreadsheet (XLSX/XLSM/CSV) into the same text table blocks the HTML extractor writes.

Official publishers often put the numbers in a linked workbook rather than on the page. The fetcher used to
decode such a file as text, which produced binary noise. Each sheet becomes a ``TABLE n — <sheet>`` block with
``a | b | c`` rows, so the dataset extractor and table charts read it exactly like an HTML table. Formulas are
read as their cached values; empty rows and columns are skipped; output is bounded by ``max_chars``.
"""
from __future__ import annotations

import csv
import io
import logging
import re
from datetime import date, datetime

log = logging.getLogger("data_files")

MAX_ROWS_PER_SHEET = 400
MAX_SHEETS = 12


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d") if (value.hour, value.minute) == (0, 0) else value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float):
        if value.is_integer() and abs(value) < 1e15:
            return str(int(value))
        return f"{value:.6g}" if abs(value) < 1e-3 or abs(value) >= 1e9 else str(round(value, 6))
    return re.sub(r"\s+", " ", str(value)).strip()


def _rows_to_block(title: str, rows: list[list[str]], index: int) -> list[str]:
    rows = [[c for c in row] for row in rows if sum(1 for c in row if c) >= 2]
    if len(rows) < 2:
        return []
    width = max(len(r) for r in rows)
    used = [j for j in range(width) if any(j < len(r) and r[j] for r in rows)]
    out = [f"TABLE {index}" + (f" — {title[:180]}" if title else "")]
    for row in rows[:MAX_ROWS_PER_SHEET]:
        out.append(" | ".join(row[j] if j < len(row) else "" for j in used))
    return out


def _xlsx_blocks(payload: bytes) -> list[list[str]]:
    from openpyxl import load_workbook

    book = load_workbook(io.BytesIO(payload), read_only=True, data_only=True)
    blocks: list[list[str]] = []
    try:
        for sheet in book.worksheets[:MAX_SHEETS]:
            rows = []
            for raw in sheet.iter_rows(values_only=True):
                cells = [_cell(v) for v in raw]
                if any(cells):
                    rows.append(cells)
                if len(rows) >= MAX_ROWS_PER_SHEET:
                    break
            block = _rows_to_block(str(sheet.title or ""), rows, len(blocks) + 1)
            if block:
                blocks.append(block)
    finally:
        book.close()
    return blocks


def _csv_blocks(payload: bytes, title: str) -> list[list[str]]:
    text = payload.decode("utf-8-sig", errors="replace")
    try:
        dialect = csv.Sniffer().sniff(text[:4000], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = [[_cell(c) for c in row] for row in csv.reader(io.StringIO(text), dialect)]
    block = _rows_to_block(title, [r for r in rows if any(r)], 1)
    return [block] if block else []


def spreadsheet_text(payload: bytes, extension: str, *, max_chars: int = 20000, title: str = "") -> str:
    """Text table blocks for a spreadsheet payload; '' when it cannot be read (never raises)."""
    ext = str(extension or "").casefold()
    try:
        if ext in {".xlsx", ".xlsm"} or payload[:2] == b"PK":
            blocks = _xlsx_blocks(payload)
        elif ext == ".csv":
            blocks = _csv_blocks(payload, title)
        else:  # legacy .xls / .ods need extra libraries; report them as unreadable rather than as garbage
            return ""
    except Exception as exc:
        log.debug("Spreadsheet extraction failed (%s): %s", ext, type(exc).__name__)
        return ""
    lines: list[str] = []
    size = 0
    for block in blocks:
        for line in block:
            if size + len(line) + 1 > max_chars:
                return "\n".join(lines)
            lines.append(line)
            size += len(line) + 1
    return "\n".join(lines)
