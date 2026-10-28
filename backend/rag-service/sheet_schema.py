"""Helpers for preserving complete spreadsheet sheet schemas in RAG chunks."""

import csv
import re
from typing import Any

_SHEET_MARKER_RE = re.compile(r"(?m)^\s*\[Sheet:\s*(?P<name>[^\]\r\n]+)\]\s*$")
_SCHEMA_QUERY_RE = re.compile(r"\b(columns?|headers?|field names?|schema)\b", re.IGNORECASE)


def extract_sheet_schema_chunks(text: str) -> list[dict[str, Any]]:
    """Create a small, self-contained schema chunk for every CSV-rendered workbook sheet.

    The ordinary recursive splitter can cut a workbook at a sheet/header boundary.
    Keeping an explicit schema chunk prevents a query about columns from receiving
    a fragment such as ``Date,Me`` instead of the full header row.
    """
    markers = list(_SHEET_MARKER_RE.finditer(text or ""))
    schemas: list[dict[str, Any]] = []
    for position, match in enumerate(markers):
        sheet_name = match.group("name").strip()
        end = markers[position + 1].start() if position + 1 < len(markers) else len(text)
        block = text[match.end():end]
        first_row = next((line.strip() for line in block.splitlines() if line.strip()), "")
        if not first_row:
            continue
        try:
            columns = [str(cell).strip() for cell in next(csv.reader([first_row]))]
        except (csv.Error, StopIteration):
            continue
        # A sheet with only one column is valid, but require at least one named field.
        columns = [column for column in columns if column]
        if not columns:
            continue
        schema_text = (
            "[Workbook sheet schema]\n"
            f"Sheet: {sheet_name}\n"
            f"Column count: {len(columns)}\n"
            "Columns: " + " | ".join(columns) + "\n"
            "Exact header row (CSV): " + first_row
        )
        schemas.append({"sheet_name": sheet_name, "columns": columns, "text": schema_text})
    return schemas


def matching_schema_chunk_indices(question: str, chunks: list[dict[str, Any]]) -> list[int]:
    """Find exact schema chunks for a named-sheet column/header question."""
    if not _SCHEMA_QUERY_RE.search(question or ""):
        return []
    query_normalized = re.sub(r"[^a-z0-9]", "", (question or "").lower())
    matches: list[int] = []
    for index, chunk in enumerate(chunks):
        metadata = chunk.get("source_metadata") if isinstance(chunk.get("source_metadata"), dict) else {}
        if metadata.get("rag_chunk_kind") != "sheet_schema":
            continue
        sheet_name = str(metadata.get("sheet_name") or "")
        sheet_normalized = re.sub(r"[^a-z0-9]", "", sheet_name.lower())
        if sheet_normalized and sheet_normalized in query_normalized:
            matches.append(index)
    return matches

