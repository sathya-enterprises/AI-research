"""Measure slots: which (measure x entity) pairs of the measure plan already have data, and searches for the rest.

A slot is covered when a fetched page or data file holds a data block (a ``TABLE`` block, or at least three
year-number pairs) whose text carries the measure's words and, for an entity slot, the entity's name. Empty
slots get one more round of data-seeking searches before writing: the measure with the scope and entity,
phrased for statistics tables and report files, and once with the name of an official publisher this run has
already reached (one that published a covered measure, or any official host). Nothing here knows a topic.

    slots = slot_coverage(plan, rows)
    queries = gap_queries(slots, plan, years=..., official_hosts=..., cap=6)
"""
from __future__ import annotations

import re

from utils.primary_sources import host_of, is_primary

_STOP = {"the", "and", "for", "from", "with", "into", "over", "under", "per", "all", "total", "annual", "rate",
         "rates", "number", "share", "level", "levels", "size", "value", "values"}
_NUMBER = re.compile(r"(?<![A-Za-z])[-+]?\d[\d,]*(?:\.\d+)?%?")
_PERIOD_VALUE = re.compile(
    r"(?<!\d)((?:19|20)\d{2}(?:[-/](?:19|20)?\d{2})?)(?!\d)"
    r"(?:\s*(?:[,=:]|was|is|at|stood\s+at|reached|rose\s+to|fell\s+to)\s*|\s+)"
    r"([-+]?\d[\d,]*(?:\.\d+)?%?)(?!\d)", re.I)
_PERIOD = re.compile(r"(?i)^(?:(?:19|20)\d{2}(?:[-\u2013/]\d{2,4})?|FY\s*'?\d{2,4}(?:[-\u2013]\d{2,4})?|"
                     r"CY\s*'?\d{4}|Q[1-4](?:\s*FY)?\s*'?\d{2,4}|H[12]\s*FY\s*'?\d{2,4})$")
_TIME_HEADER = re.compile(r"(?i)\b(?:year|date|period|quarter|month|fiscal(?:\s+year)?)\b")
_METADATA_LABEL = re.compile(r"(?i)\b(?:identifier|unique id|\bid\b|\bcodes?\b|unit(?: of measure)?|"
                             r"description|definition|metadata|geographic entities|time intervals|method(?:ology)?|"
                             r"estimation|validation|citation|disclaimer|frequency|periodicity|denominator|numerator|"
                             r"data update|data release|value display label|short name|abbreviation)\b")
_HEADER_LABEL = re.compile(r"(?i)^(?:year|date|period|quarter|month|fiscal year|country|country or area|entity|"
                           r"region|state|area|rank|indicator|measure|metric|name|category|cause of death|"
                           r"operational definition of health indicator|field)$")


def _content_words(text: str) -> set[str]:
    normalized = str(text or "").casefold()
    normalized = re.sub(r"\bunder[\s-]+(?:five|5)\b", "under-five", normalized)
    normalized = re.sub(r"\bage[\s-]+standard(?:iz|is)ed\b", "age-standardized", normalized)
    tokens = re.findall(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", normalized)
    words = {w for w in tokens if len(w) >= 3 and w not in _STOP}
    # Keep the two medically meaningful compounds atomic, but accept ordinary hyphenation
    # for terms such as life-expectancy / year-over-year.
    for token in tokens:
        if token not in {"under-five", "age-standardized"} and "-" in token:
            words.update(part for part in token.split("-") if len(part) >= 3 and part not in _STOP)
    return words


def _period_years(period: str) -> set[int]:
    """Return the calendar years represented by a period label, including FY 2020-21."""
    tokens = re.findall(r"(?:19|20)\d{2}|(?<!\d)\d{2}(?!\d)", str(period or ""))
    out: set[int] = set()
    previous: int | None = None
    for token in tokens:
        year = int(token)
        if len(token) == 2:
            year = ((previous // 100) * 100 + year) if previous is not None else 2000 + year
        out.add(year)
        previous = year
    return out


def _period_in_window(period: str, years: list[int] | None) -> bool:
    if years is None:
        return True
    if not years:
        return False
    represented = _period_years(period)
    if not represented:
        return False
    start, end = min(years), max(years)
    return any(start <= year <= end for year in represented)


def _data_blocks(text: str) -> list[str]:
    """Numerically populated tables or local numeric statements, never a whole page's unrelated text.

    Catalog exports often render metadata as pipe tables too. Those blocks can contain the requested
    measure, every country name and a list of years without containing a single observation.
    """
    text = str(text or "")
    blocks = re.split(r"(?m)^(?=TABLE \d+)", text)
    tables = [b for b in blocks if b.startswith("TABLE ") and " | " in b]
    if tables:
        prefix = next((b.strip() for b in blocks if b.strip() and not b.startswith("TABLE ")), "")[:300]
        return [f"TABLE {prefix}\n{b}" for b in tables if _table_has_observations(b)]

    # Some HTML/PDF extractors flatten a complete pipe table onto one line and omit a TABLE caption.
    pipe_lines = [line for line in text.splitlines() if "|" in line]
    if pipe_lines:
        prefix_lines = []
        for line in text.splitlines():
            if "|" in line:
                break
            if line.strip():
                prefix_lines.append(line.strip())
        prefix = "\n".join(prefix_lines)[:300]
        table = f"TABLE {prefix} extracted pipe table\n" + "\n".join(pipe_lines)
        if _table_has_observations(table):
            return [table]

    # Keep numeric prose local: matching a topic/entity somewhere on a whole page is not evidence that
    # every statistic on that page belongs to that topic/entity.
    pieces = re.split(r"(?<=[.!?])\s+(?=[A-Z])|(?:\r?\n){2,}", text)
    return [piece.strip() for piece in pieces if _NUMBER.search(piece)]


def _table_rows(block: str) -> list[list[str]]:
    rows = []
    for line in str(block or "").splitlines():
        if "|" not in line:
            continue
        # Search-result excerpts sometimes flatten Markdown rows as "... | value | | next row | value".
        for fragment in re.split(r"\s*\|\s*\|\s*", line.strip()):
            if "|" not in fragment:
                continue
            cells = [cell.strip() for cell in fragment.strip().strip("|").split("|")]
            if len(cells) >= 2 and not all(re.fullmatch(r"[-: ]+", c or "-") for c in cells):
                rows.append(cells)
    return rows


def _is_header(row: list[str]) -> bool:
    if not row:
        return False
    first = row[0].strip()
    after = [cell.strip() for cell in row[1:]]
    if _HEADER_LABEL.fullmatch(first) or not first:
        return True
    if after and not any(_NUMBER.search(cell) for cell in after):
        return True
    return bool(after and all(_PERIOD.fullmatch(cell) for cell in after))


def _measure_hit(text: str, measure_words: set[str]) -> bool:
    if not measure_words:
        return False
    words = _content_words(text)
    # Short compound measures need every distinguishing word (e.g. prevalence AND incidence).
    need = len(measure_words) if len(measure_words) <= 3 else -(-2 * len(measure_words) // 3)
    return len(measure_words & words) >= need


def _entity_hit(text: str, entity: str) -> bool:
    return not entity or bool(re.search(rf"(?<![a-z]){re.escape(entity.casefold())}(?![a-z])", str(text or "").casefold()))


def _table_has_data(block: str, *, trend: bool = False, years: list[int] | None = None) -> bool:
    """Recognize observation tables; a trend needs 3 distinct periods with numeric values."""
    rows = _table_rows(block)
    if not rows:
        return False
    header = rows[0] if _is_header(rows[0]) else []
    observations = rows[1:] if header else rows

    # Metadata rows such as "Time intervals | 1990, 1991, ..." have many numbers but no observations.
    observed_rows: list[list[str]] = []
    for row in observations:
        if len(row) < 2 or not row[0] or _METADATA_LABEL.search(row[0]):
            continue
        if any(_NUMBER.search(cell) for cell in row[1:]):
            observed_rows.append(row)

    labels = {re.sub(r"\s+", " ", row[0]).casefold() for row in observed_rows}
    has_cross_section = len(observed_rows) >= 3 and len(labels) >= 3
    if not trend:
        return has_cross_section or any(sum(bool(_NUMBER.search(cell)) for cell in row[1:]) >= 3
                                        for row in observed_rows)

    time_columns = [i for i, cell in enumerate(header)
                    if _TIME_HEADER.search(re.sub(r"[_-]+", " ", cell))]
    for column in time_columns:
        periods = {row[column].strip().casefold() for row in observed_rows
                   if column < len(row) and _PERIOD.fullmatch(row[column].strip())
                   and _period_in_window(row[column].strip(), years)
                   and any(_NUMBER.search(cell) for i, cell in enumerate(row) if i != column)}
        if len(periods) >= 3:
            return True

    # Wide tables sometimes put years across the header and measures on the rows.
    period_columns = [i for i, cell in enumerate(header) if _PERIOD.fullmatch(cell.strip())
                      and _period_in_window(cell.strip(), years)]
    if len(period_columns) >= 3 and any(
            sum(i < len(row) and bool(_NUMBER.search(row[i])) for i in period_columns) >= 3
            for row in observed_rows):
        return True
    return False


def _table_has_observations(block: str) -> bool:
    """Keep either cross-sectional tables or real multi-period observations."""
    return _table_has_data(block) or _table_has_data(block, trend=True)


def _covers(block: str, measure_words: set[str], entity: str, *, chart: str = "trend",
            years: list[int] | None = None) -> bool:
    """Measure/entity match must land on the same data row or a single-measure table."""
    if block.lstrip().startswith("TABLE "):
        rows = _table_rows(block)
        if not rows:
            return False
        header = rows[0] if _is_header(rows[0]) else []
        observations = rows[1:] if header else rows
        if not _table_has_data(block, trend=chart == "trend", years=years):
            return False
        header_text = " ".join(header)
        table_lines = block.splitlines()
        first_row = next((i for i, line in enumerate(table_lines) if "|" in line), len(table_lines))
        caption_text = " ".join(line.strip() for line in table_lines[:first_row] if line.strip())
        table_measure = _measure_hit(f"{caption_text} {header_text}", measure_words)
        entity_in_header = _entity_hit(header_text, entity)
        for row in observations:
            if len(row) < 2 or not row[0] or _METADATA_LABEL.search(row[0]):
                continue
            if not any(_NUMBER.search(cell) for cell in row[1:]):
                continue
            row_text = " ".join(row)
            if not (table_measure or _measure_hit(row_text, measure_words)):
                continue
            if not (entity_in_header or _entity_hit(row_text, entity)):
                continue
            if chart != "trend":
                return True
            if _table_has_data(block, trend=True, years=years):
                return True
        return False

    if not _measure_hit(block, measure_words) or not _entity_hit(block, entity) or not _NUMBER.search(block):
        return False
    if chart == "trend":
        observations = {period.casefold() for period, _value in _PERIOD_VALUE.findall(block)
                        if _period_in_window(period, years)}
        return len(observations) >= 3
    return True


def slot_coverage(plan, rows: list[dict], *, max_entities: int | None = None,
                  years: list[int] | None = None) -> list[dict]:
    """One dict per slot: measure, entity ('' for the scope as a whole), covered, sources (urls)."""
    pages = []
    for row in rows or []:
        if not isinstance(row, dict) or row.get("pageFetchStatus") != "ok":
            continue
        context = ""
        try:
            from utils.primary_sources import is_data_file
            from utils.data_files import has_observation_table
            body = str(row.get("pageContent") or "")
            if (row.get("searchQuery") and has_observation_table(body)
                    and is_data_file(str(row.get("url") or ""), str(row.get("pageFetchContentType") or ""))):
                context = f"Measure search query: {row['searchQuery']}"
        except Exception:
            context = ""
        page_text = "\n\n".join(filter(None, (str(row.get("title") or ""), str(row.get("publisherPageTitle") or ""),
                                                  context, str(row.get("pageContent") or ""))))
        blocks = _data_blocks(page_text)
        if blocks:
            pages.append((str(row.get("url") or ""), blocks))
    slots = []
    for m in getattr(plan, "measures", None) or []:
        words = _content_words(m.measure)
        chart = str(getattr(m, "chart", "trend") or "trend").casefold()
        entities = list(m.entities or [""])
        if max_entities is not None:
            entities = entities[:max(0, max_entities)]
        for entity in entities:
            sources = [url for url, blocks in pages
                       if any(_covers(b, words, entity, chart=chart, years=years) for b in blocks)]
            slots.append({"measure": m.measure, "entity": entity, "covered": bool(sources), "sources": sources[:5]})
    return slots


def official_hosts(slots: list[dict]) -> list[str]:
    """Official hosts that already published data for one of the measures (most slots first)."""
    counts: dict[str, int] = {}
    for s in slots:
        for url in s["sources"]:
            if is_primary(url):
                counts[host_of(url)] = counts.get(host_of(url), 0) + 1
    return [h for h, _ in sorted(counts.items(), key=lambda kv: -kv[1]) if h]


def gap_queries(slots: list[dict], plan, *, years: list[int] | None = None, hosts: list[str] | None = None,
                question: str = "", cap: int = 6) -> list[str]:
    """Searches for empty slots, round-robin over measures (most important first) so one measure with many
    entities cannot take every search: a statistics-table query per empty slot (the measure for the whole
    scope first when none of its slots has data), then a report-file query, then one naming an official
    publisher this run already reached."""
    scope = str(getattr(plan, "scope", "") or "")
    span = f"{years[0]} to {years[-1]}" if years and len(years) >= 2 else ""
    explicit_window = bool(re.search(
        r"(?i)\b(?:19|20)\d{2}\b|\b(?:past|last|since|from|between|during|annual|yearly|year-wise|quarterly|monthly|FY|Q[1-4])\b|over\s+time",
        question or "",
    ))
    measures = {str(getattr(m, "measure", "")): m for m in getattr(plan, "measures", None) or []}
    by_measure: dict[str, list[dict]] = {}
    for s in slots:
        by_measure.setdefault(s["measure"], []).append(s)

    def subject(measure: str, entity: str) -> str:
        # A list of countries is already the scope for each of its member slots. Repeating the complete list
        # beside one country creates noisy queries such as "... India, China ... Nigeria statistics Nigeria".
        entity_in_scope = bool(entity and re.search(rf"(?<![A-Za-z]){re.escape(entity)}(?![A-Za-z])", scope, re.I))
        where = scope if scope and not entity_in_scope else ""
        return " ".join(x for x in (measure, where, entity) if x)

    def evidence_shape(measure: str, target: str) -> str:
        chart = str(getattr(measures.get(measure), "chart", "trend") or "trend").casefold()
        time_scope = span if chart == "trend" or explicit_window or not question else ""
        period = time_scope or "latest available year"
        if chart == "share":
            return f"{target} percentage share composition breakdown data table {period}".strip()
        if chart == "ranking":
            return f"{target} ranked values ranking statistics table {period}".strip()
        if chart == "comparison":
            return f"{target} comparison across entities {period} statistics table".strip()
        return f"{target} annual time series statistics by year {time_scope or span} table".strip()

    waves: list[list[str]] = []
    for measure, group in by_measure.items():
        empty = [s for s in group if not s["covered"]]
        if not empty:
            continue
        subjects = ([subject(measure, "")] if len(empty) == len(group) and empty[0]["entity"] else []) + \
                   [subject(measure, s["entity"]) for s in empty]
        queries = [evidence_shape(measure, x) for x in subjects]
        file_period = span if not question or explicit_window else ""
        file_query = f"{subjects[0]} downloadable dataset data table CSV XLSX PDF {file_period}".strip()
        # Search the data-file path early too; the live fetch pipeline opens linked CSV/XLSX/PDF files and
        # retries JS-rendered pages, so a broad report page is not the only recovery route.
        wave = queries[:1] + [file_query]
        # A publisher already reached for a different measure can be a poor
        # match here (for example, an energy source used for a health metric).
        # Reuse a host only when a covered sibling slot for this same measure
        # came from it; the general and downloadable-data searches still run.
        group_hosts = {
            host_of(url)
            for slot in group if slot.get("covered")
            for url in slot.get("sources") or []
            if is_primary(url) and host_of(url)
        }
        host = next((str(value).strip() for value in (hosts or [])
                     if str(value).strip() in group_hosts), "")
        if host:
            host_query = f"site:{host} {evidence_shape(measure, subjects[0])}".strip()
            wave.append(host_query)
        waves.append(wave + queries[1:])
    out: list[str] = []
    for i in range(max((len(w) for w in waves), default=0)):
        out += [w[i] for w in waves if i < len(w)]
    out = list(dict.fromkeys(re.sub(r"\s+", " ", q) for q in out))
    return out[: max(0, cap)]


def slot_limit(report_tier: str) -> int:
    return {"detailed": 6, "in_depth": 12}.get(str(report_tier or "detailed"), 3)


def requested_years(question: str, today) -> list[int]:
    """Requested calendar years shared with data-seeking and official-dataset fetches."""
    from utils.data_angles import window_years
    return window_years(question, today)
