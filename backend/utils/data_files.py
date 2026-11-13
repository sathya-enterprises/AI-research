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
import unicodedata
import zipfile
from functools import lru_cache
from datetime import date, datetime

log = logging.getLogger("data_files")

MAX_ROWS_PER_SHEET = 400
MAX_SHEETS = 12
MAX_HEADER_ROWS = 20

_STRICT_OOXML_NAMESPACES = {
    b"http://purl.oclc.org/ooxml/spreadsheetml/main":
        b"http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    b"http://purl.oclc.org/ooxml/officeDocument/relationships":
        b"http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    b"http://purl.oclc.org/ooxml/drawingml/main":
        b"http://schemas.openxmlformats.org/drawingml/2006/main",
    b"http://purl.oclc.org/ooxml/drawingml/spreadsheetDrawing":
        b"http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing",
    b"http://purl.oclc.org/ooxml/drawingml/chart":
        b"http://schemas.openxmlformats.org/drawingml/2006/chart",
}

_ENTITY_HEADERS = {
    "area", "area name", "country", "country name", "country or area", "country code",
    "countrycode", "entity", "entity name", "geography", "geographic area", "location",
    "location name", "ref area", "refarea", "region", "state", "province", "territory",
    "iso2", "iso3", "iso alpha2", "iso alpha3", "iso 3166 1 alpha 2", "iso 3166 1 alpha 3",
}
_TIME_HEADERS = {"year", "date", "period", "time period", "timeperiod", "fiscal year", "time"}
_ENTITY_HEADER_LABELS = {_normalized for _normalized in (re.sub(r"[^a-z0-9]+", "", h.casefold()) for h in _ENTITY_HEADERS)}
_TIME_HEADER_LABELS = {_normalized for _normalized in (re.sub(r"[^a-z0-9]+", "", h.casefold()) for h in _TIME_HEADERS)}
_LABEL_HEADER = re.compile(r"(?i)\b(?:cause|indicator|measure|metric|category|item|name|disease|condition|sector|product|commodity|technology|series)\b")
_LABEL_HEADER_EXACT = {
    "cause", "ghe cause", "cause of death", "indicator", "indicator name", "measure", "measure name",
    "metric", "metric name", "category", "category name", "item", "item name", "name", "disease",
    "condition", "sector", "product", "product name", "commodity", "technology", "series", "series name",
}
_TOTAL_DIMENSIONS = {"total", "persons", "both sexes", "all sexes", "both", "all"}
_GEOGRAPHY_HEADER = re.compile(r"(?i)\b(?:countries?|member\s+states?|states?|provinces?|regions?|territories|locations?|geographies|areas?)\b")


def _normalized_label(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "", text.casefold())


def _label_header_strength(value: object) -> int:
    """Score concise table dimensions above nearby title or caption text."""
    text = str(value or "").strip()
    if not text or len(re.findall(r"[a-z0-9]+", text.casefold())) > 4 or not _LABEL_HEADER.search(text):
        return 0
    return 2 if _normalized_label(text) in {_normalized_label(x) for x in _LABEL_HEADER_EXACT} else 1


def _is_short_code_label(value: object) -> bool:
    return bool(re.fullmatch(r"(?:[A-Za-z]{1,3}|\d{1,4})\.?", str(value or "").strip()))


@lru_cache(maxsize=1)
def _country_header_labels() -> frozenset[str]:
    labels = {_normalized_label(value) for value in _country_names_by_code().values()}
    labels.update(_country_names_by_code().keys())
    try:
        import pycountry
        for country in pycountry.countries:
            for attribute in ("name", "official_name", "common_name", "alpha_2", "alpha_3", "numeric"):
                value = getattr(country, attribute, None)
                if value:
                    labels.add(_normalized_label(value))
    except Exception:
        pass
    return frozenset(labels)


def _wide_entity_start(rows: list[list[str]], header_indices: set[int], label_column: int,
                       first_selected_entity: int) -> int:
    """Find the first column of a wide entity block, including unrequested columns."""
    starts = []
    country_labels = _country_header_labels()
    for row_index in header_indices:
        if row_index >= len(rows):
            continue
        for column, cell in enumerate(rows[row_index][:first_selected_entity]):
            if column <= label_column:
                continue
            value = str(cell or "").strip()
            if not value:
                continue
            if _normalized_label(value) in country_labels:
                starts.append(column)
            elif _GEOGRAPHY_HEADER.search(value):
                starts.append(column + 1)
    return min(starts) if starts else first_selected_entity


@lru_cache(maxsize=1)
def _country_names_by_code() -> dict[str, str]:
    names: dict[str, str] = {}
    # Reuse the names already shared with the official-dataset connector.
    try:
        from utils.official_data import COUNTRIES
        for code, name in COUNTRIES.items():
            names[str(code).casefold()] = str(name)
    except Exception:
        pass
    # The connector's curated set covers common datasets. pycountry expands it
    # to the full ISO list when installed, which is important for arbitrary
    # country scopes in SDMX / statistical-office exports.
    try:
        import pycountry
        for country in pycountry.countries:
            name = str(getattr(country, "common_name", "") or getattr(country, "name", ""))
            if not name:
                continue
            for key in ("alpha_2", "alpha_3", "numeric"):
                code = str(getattr(country, key, "") or "").casefold()
                if code:
                    names[code] = name
    except Exception:
        pass
    # Keep the live examples and common codes available in minimal installs.
    names.update({"ind": "India", "chn": "China", "bra": "Brazil", "nga": "Nigeria"})
    return names


@lru_cache(maxsize=128)
def _aliases_for_entity(entity: str) -> frozenset[str]:
    raw = str(entity or "").strip()
    aliases = {_normalized_label(raw)} - {""}
    try:
        import pycountry
        country = pycountry.countries.lookup(raw)
        for attr in ("name", "official_name", "common_name", "alpha_2", "alpha_3", "numeric"):
            value = getattr(country, attr, None)
            if value:
                aliases.add(_normalized_label(value))
    except Exception:
        # Match the installed connector's known country names/codes even when
        # the optional full ISO catalog is unavailable.
        for code, name in _country_names_by_code().items():
            if _normalized_label(name) == _normalized_label(raw) or _normalized_label(code) == _normalized_label(raw):
                aliases.update((_normalized_label(code), _normalized_label(name)))
    return frozenset(aliases)


def _entity_matches(value: object, entities: list[str]) -> str:
    normalized = _normalized_label(value)
    if not normalized:
        return ""
    for entity in entities:
        if normalized in _aliases_for_entity(str(entity)):
            return str(entity)
    return ""


def _filter_year_rows(rows: list[list[str]], years: list[int] | None) -> list[list[str]]:
    years = [int(year) for year in years or [] if str(year).isdigit()]
    if not rows or not years:
        return rows
    header_idx = next((i for i, row in enumerate(rows[:MAX_HEADER_ROWS]) if any(
        re.sub(r"[^a-z0-9]+", "", cell.casefold()) in _TIME_HEADER_LABELS for cell in row
    )), None)
    if header_idx is None:
        return rows
    time_column = next((j for j, cell in enumerate(rows[header_idx])
                        if re.sub(r"[^a-z0-9]+", "", cell.casefold()) in _TIME_HEADER_LABELS), None)
    if time_column is None:
        return rows
    start, end = min(years), max(years)
    selected = []
    for row in rows[header_idx + 1:]:
        value = row[time_column] if time_column < len(row) else ""
        observed_years = [int(year) for year in re.findall(r"(?:19|20)\d{2}", value)]
        if not observed_years or any(start <= year <= end for year in observed_years):
            selected.append(row)
    return rows[:header_idx + 1] + selected


def _filter_entity_rows(rows: list[list[str]], entities: list[str] | None,
                        years: list[int] | None = None) -> list[list[str]]:
    """Keep requested entities from long or wide tables before row/char caps.

    Long SDMX files commonly use ISO codes in REF_AREA, while workbook exports
    often put every country in a separate column. If the table exposes neither
    form, leave it intact rather than guessing at its schema.
    """
    rows = _filter_year_rows(rows, years)
    entities = [str(e).strip() for e in entities or [] if str(e).strip()]
    if not rows or not entities:
        return rows

    header_idx = next((i for i, row in enumerate(rows[:MAX_HEADER_ROWS]) if any(
        _normalized_label(cell) in _ENTITY_HEADER_LABELS for cell in row
    )), None)
    entity_column = None
    if header_idx is not None:
        entity_column = next((j for j, cell in enumerate(rows[header_idx])
                              if _normalized_label(cell) in _ENTITY_HEADER_LABELS), None)

    if entity_column is not None:
        prefix = rows[:header_idx + 1]
        groups: dict[str, list[list[str]]] = {entity: [] for entity in entities}
        for row in rows[header_idx + 1:]:
            if entity_column >= len(row):
                continue
            matched = _entity_matches(row[entity_column], entities)
            if not matched:
                continue
            values = list(row)
            code = _normalized_label(values[entity_column])
            display = _country_names_by_code().get(code)
            if display and _normalized_label(display) == _normalized_label(matched):
                # Keep the source's original code in view alongside its label.
                values[entity_column] = f"{matched} ({values[entity_column]})"
            groups[matched].append(values)
        matched_groups = [entity for entity in entities if groups.get(entity)]
        if not matched_groups:
            return prefix
        selected: list[list[str]] = []
        for offset in range(max(len(groups[e]) for e in matched_groups)):
            for entity in matched_groups:
                if offset < len(groups[entity]):
                    selected.append(groups[entity][offset])
        return prefix + selected[:MAX_ROWS_PER_SHEET]

    # Wide tables put entity names/codes in the first few header rows. Retain
    # identifier columns and only the requested entity columns, which prevents
    # a global spreadsheet from consuming the extraction budget before later
    # rows/sheets are reached.
    entity_candidates = []
    for header_index, header_row in enumerate(rows[:MAX_HEADER_ROWS]):
        matched_columns = {column: matched for column, cell in enumerate(header_row[1:], start=1)
                           if (matched := _entity_matches(cell, entities))}
        if not matched_columns:
            continue
        has_header_anchor = any(
            _label_header_strength(cell)
            or _normalized_label(cell) in _ENTITY_HEADER_LABELS
            or _normalized_label(cell) in _TIME_HEADER_LABELS
            for cell in header_row
        )
        named_entities = sum(len(str(header_row[column] or "").strip()) > 3 for column in matched_columns)
        entity_candidates.append((len(matched_columns), has_header_anchor, named_entities, -header_index,
                                  matched_columns, header_index))
    entity_columns: dict[int, str] = {}
    entity_header_rows: dict[int, int] = {}
    if entity_candidates:
        _, _, _, _, entity_columns, selected_header_index = max(entity_candidates, key=lambda candidate: candidate[:4])
        entity_header_rows = {column: selected_header_index for column in entity_columns}
    if not entity_columns:
        return rows
    first_entity_column = min(entity_columns)
    label_candidates = []
    entity_header_indices = set(entity_header_rows.values())
    for row_index, header_row in enumerate(rows[:MAX_HEADER_ROWS]):
        for column, cell in enumerate(header_row[:first_entity_column]):
            strength = _label_header_strength(cell)
            if not strength:
                continue
            distance = min((abs(row_index - index) for index in entity_header_indices), default=MAX_HEADER_ROWS)
            if distance <= 2:
                label_candidates.append((row_index in entity_header_indices, strength, -distance, -row_index,
                                         -column, column, row_index))
    label_column = None
    label_header_row = None
    if label_candidates:
        _, _, _, _, _, label_column, label_header_row = max(label_candidates)
    has_time_header = any(
        re.sub(r"[^a-z0-9]+", "", cell.casefold()) in _TIME_HEADER_LABELS
        for header_row in rows[:MAX_HEADER_ROWS] for cell in header_row[:first_entity_column]
    )

    if label_column is not None and label_header_row is not None and not has_time_header:
        header_row = rows[label_header_row]
        context_column = next((column for column, cell in enumerate(header_row[:first_entity_column])
                               if re.search(r"(?i)\b(?:sex|gender)\b", str(cell or ""))), None)
        code_columns = [column for column, cell in enumerate(header_row[:first_entity_column])
                        if column != context_column and re.search(r"(?i)\bcode\b", str(cell or ""))]
        entity_start = _wide_entity_start(rows, entity_header_indices, label_column, first_entity_column)
        label_columns = [column for column in range(label_column, entity_start)
                         if column != context_column and column not in code_columns]
        data_start = max(label_header_row, *entity_header_rows.values()) + 1
        candidate_rows = [row for row in rows[data_start:]
                          if any(column < len(row) and row[column] for column in label_columns)
                          and any(column < len(row) and re.search(r"\d", str(row[column] or ""))
                                  for column in entity_columns)]
        # When a cause/series code is present, skip nearby population or metadata
        # rows that share the merged label area but have no code of their own.
        if code_columns:
            coded_rows = [row for row in candidate_rows
                          if any(column < len(row) and str(row[column] or "").strip() for column in code_columns)]
            if coded_rows and len(coded_rows) >= max(2, (len(candidate_rows) + 1) // 2):
                candidate_rows = coded_rows
        use_total = bool(context_column is not None and any(
            _normalized_label(row[context_column]) in {_normalized_label(x) for x in _TOTAL_DIMENSIONS}
            for row in candidate_rows if context_column < len(row)))
        normalized = [str(header_row[label_column] or "Category").strip()]
        normalized.extend(entity_columns[column] for column in sorted(entity_columns))
        out = [normalized]
        for row in candidate_rows:
            context = str(row[context_column] or "").strip() if context_column is not None and context_column < len(row) else ""
            if use_total and _normalized_label(context) not in {_normalized_label(x) for x in _TOTAL_DIMENSIONS}:
                continue
            label_parts = [str(row[column]).strip() for column in label_columns
                           if column < len(row) and str(row[column] or "").strip()]
            natural_labels = [part for part in label_parts if not _is_short_code_label(part)]
            label = (natural_labels or label_parts or [""])[-1]
            if context and not use_total and _normalized_label(context) not in {_normalized_label(x) for x in _TOTAL_DIMENSIONS}:
                label = f"{label} ({context})"
            out.append([label] + [str(row[column] or "") if column < len(row) else ""
                                  for column in sorted(entity_columns)])
        if len(out) > 1:
            return out[:MAX_ROWS_PER_SHEET + 1]

    keep = list(range(first_entity_column)) + sorted(entity_columns)
    return [[row[column] if column < len(row) else "" for column in keep] for row in rows]


def _sheet_caption(rows: list[list[str]], entities: list[str] | None) -> str:
    """Retain a workbook table's short caption and units beside its extracted rows."""
    if not rows or not entities:
        return ""
    header_matches = [
        (sum(bool(_entity_matches(cell, entities)) for cell in row), index)
        for index, row in enumerate(rows[:MAX_HEADER_ROWS])
    ]
    match_count, header_index = max(header_matches, default=(0, 0), key=lambda item: (item[0], -item[1]))
    if match_count < 2:
        return ""
    candidates = []
    for row in rows[max(0, header_index - 3):header_index]:
        cells = [str(cell).strip() for cell in row if str(cell or "").strip()]
        if not cells or len(cells) > 2:
            continue
        line = re.sub(r"\s+", " ", " ".join(cells)).strip()
        if len(line) < 12 or len(line) > 240:
            continue
        if re.fullmatch(r"(?i)(?:[a-z]{3,9}\s+)?(?:19|20)\d{2}", line):
            continue
        candidates.append(line)
    return " ".join(candidates[-2:])[:280]


def _normalize_strict_ooxml(payload: bytes) -> bytes:
    """Make ISO strict OOXML workbooks readable by openpyxl.

    Excel and some official statistical publishers emit an otherwise valid XLSX
    package using the ISO/IEC 29500 strict namespace URIs. openpyxl currently
    expects the equivalent ECMA-376 transitional URIs; it can silently expose
    zero worksheets for these files. Translate only package XML namespaces in
    memory, leaving the downloaded source and cell values untouched.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(payload), "r") as source:
            workbook_name = "xl/workbook.xml"
            if workbook_name not in source.namelist():
                return payload
            workbook_xml = source.read(workbook_name)
            if not any(strict in workbook_xml for strict in _STRICT_OOXML_NAMESPACES):
                return payload
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as normalized:
                for info in source.infolist():
                    content = source.read(info.filename)
                    if info.filename.endswith((".xml", ".rels")):
                        for strict, transitional in _STRICT_OOXML_NAMESPACES.items():
                            content = content.replace(strict, transitional)
                    normalized.writestr(info, content)
            return output.getvalue()
    except (OSError, zipfile.BadZipFile, KeyError):
        return payload


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
        cells = [row[j] if j < len(row) else "" for j in used]
        while cells and not cells[-1]:
            cells.pop()
        out.append(" | ".join(cells))
    return out


def _xlsx_blocks(payload: bytes, *, entities: list[str] | None = None,
                 years: list[int] | None = None) -> list[list[str]]:
    from openpyxl import load_workbook

    payload = _normalize_strict_ooxml(payload)
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
            caption = _sheet_caption(rows, entities)
            rows = _filter_entity_rows(rows, entities, years)
            sheet_title = str(sheet.title or "")
            title = f"{sheet_title} - {caption}" if caption else sheet_title
            block = _rows_to_block(title, rows, len(blocks) + 1)
            if block:
                blocks.append(block)
    finally:
        book.close()
    return blocks


def _csv_blocks(payload: bytes, title: str, *, entities: list[str] | None = None,
                years: list[int] | None = None) -> list[list[str]]:
    text = payload.decode("utf-8-sig", errors="replace")
    try:
        dialect = csv.Sniffer().sniff(text[:4000], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = [[_cell(c) for c in row] for row in csv.reader(io.StringIO(text), dialect)]
    rows = _filter_entity_rows(rows, entities, years)
    block = _rows_to_block(title, [r for r in rows if any(r)], 1)
    return [block] if block else []


def spreadsheet_text(payload: bytes, extension: str, *, max_chars: int = 20000, title: str = "",
                     entities: list[str] | None = None, years: list[int] | None = None) -> str:
    """Text table blocks for a spreadsheet payload; '' when it cannot be read (never raises)."""
    ext = str(extension or "").casefold()
    try:
        if ext in {".xlsx", ".xlsm"} or payload[:2] == b"PK":
            blocks = _xlsx_blocks(payload, entities=entities, years=years)
        elif ext == ".csv":
            blocks = _csv_blocks(payload, title, entities=entities, years=years)
        else:  # legacy .xls / .ods need extra libraries; report them as unreadable rather than as garbage
            return ""
    except Exception as exc:
        log.debug("Spreadsheet extraction failed (%s): %s", ext, type(exc).__name__)
        return ""
    lines: list[str] = []
    size = 0
    remaining_blocks = len(blocks)
    for block in blocks:
        block_budget = (max_chars - size) // max(1, remaining_blocks)
        block_size = 0
        for line in block:
            addition = len(line) + 1
            if size + addition > max_chars or block_size + addition > block_budget:
                break
            lines.append(line)
            size += addition
            block_size += addition
        remaining_blocks -= 1
    return "\n".join(lines)


def has_observation_table(text: str) -> bool:
    """Whether extracted file text includes a populated table, not just metadata."""
    numeric_rows = 0
    labels: set[str] = set()
    for line in str(text or "").splitlines():
        if "|" not in line:
            continue
        cells = [cell.strip() for cell in line.split("|")]
        if len(cells) < 2 or not re.search(r"\d", " ".join(cells[1:])):
            continue
        numeric_rows += 1
        if cells[0]:
            labels.add(_normalized_label(cells[0]))
    return numeric_rows >= 3 and len(labels) >= 2
