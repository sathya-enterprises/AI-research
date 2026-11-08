"""Per-section layout from each section's evidence (Stage D).

Every block is built from evidence-store data, never from the writer's prose:
values are cells copied from table passages (with the source-level citation of
the passage they came from), so a stat strip, table or scorecard cannot contain
a number the evidence does not hold. A block is added only when the evidence
supports it; thin sections stay short.

Blocks (rendered by existing renderer components, see utils/layout_blocks.py):
  * stat strip      -- 3-4 verified numbers opening a sector deep-dive
  * key takeaways   -- the section's own summary sentences (number-policy checked)
  * compact table   -- a cited table with <= 12 rows, placed after the paragraph
                       that first cites it (larger tables stay in the appendix)
  * comparison      -- several entities x metrics for a compact comparison section
  * what to watch   -- risk / outlook sentences moved out of the prose into a callout
  * sector scorecard and "how to read this report" near the front
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from utils.chart_titles import clean_label, header_is_data, non_comparable_metric, period_of
from utils.entity_tiers import SYNONYMS, canonical

CITE_RE = re.compile(r"\[(S\d+(?:\.\d+)?(?:\s*[,;]\s*S\d+(?:\.\d+)?)*)\]")
_NUM_CELL_RE = re.compile(r"^\(?[+\-−–]?\s*(?:₹|\$|rs\.?\s*)?[\d,]*\.?\d+\s*(?:%|x|pts|bps|cr|crore|bn|mn)?\)?$", re.I)
_PERIOD_HEADER_RE = re.compile(
    r"(?i)^(?:Q[1-4]\s*(?:FY|CY)?\s*'?\d{2,4}|FY\s*'?\d{2,4}|(?:19|20)\d{2}|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s*'?\d{2,4})$")
MAX_INLINE_ROWS = 12
# Counts of constituents are not statistics worth a stat card or a scorecard column.
_COUNT_HEADER_RE = re.compile(r"(?i)^(?:stocks?|constituents?|count|no\.?\s*of\b.*|number of\b.*|#|companies|members|rank)$")
MAX_INLINE_COLS = 6


@dataclass
class Table:
    passage_id: str
    source_id: str
    heading: str
    header: list[str]
    rows: list[list[str]]
    reliability: float = 0.0
    kind: str = "table"

    @property
    def cite(self) -> str:
        return f"[{self.source_id}]"


def parse_table(passage) -> Table | None:
    lines = [ln for ln in str(passage.text or "").splitlines() if " | " in ln]
    if len(lines) < 3:
        return None
    heading_line = next((ln for ln in str(passage.text).splitlines() if ln.strip() and " | " not in ln), "")
    heading = re.sub(r"^TABLE \d+\s*[—-]\s*", "", heading_line.strip()).replace("(continued)", "").strip()
    split = lambda ln: [c.strip() for c in ln.strip().strip("|").split("|")]  # noqa: E731
    header = split(lines[0])
    rows = [r for r in (split(ln) for ln in lines[1:]) if len(r) == len(header) and r[0]]
    if len(header) < 2 or len(rows) < 2 or any(header_is_data(h) for h in header[1:]):
        return None
    return Table(passage.passage_id, passage.source_id, heading, header, rows,
                 float(getattr(passage, "reliability", 0) or 0), passage.kind)


def _numeric(cell: str) -> bool:
    return bool(_NUM_CELL_RE.match(str(cell or "").replace(" ", "").replace("−", "-")))


def _value_columns(table: Table) -> list[int]:
    """Comparable numeric columns, the latest period column first."""
    cols = [c for c in range(1, len(table.header))
            if sum(_numeric(r[c]) for r in table.rows) >= max(2, int(0.7 * len(table.rows)))
            and not non_comparable_metric(table.header[c]) and not _COUNT_HEADER_RE.match(table.header[c].strip())]
    periods = [c for c in cols if _PERIOD_HEADER_RE.match(table.header[c].strip())]
    if not periods:
        return cols
    from utils.chart_evidence_audit import _period_ranges

    def period_end(col: int) -> int:
        ranges = _period_ranges(table.header[col])
        return max((end for _start, end, _label in ranges), default=-1)

    # Latest period first, whatever order the source table uses.
    periods.sort(key=lambda c: (period_end(c), c), reverse=True)
    return periods + [c for c in cols if c not in periods]


def _metric_label(table: Table, col: int) -> str:
    header = clean_label(table.header[col])
    if _PERIOD_HEADER_RE.match(header):
        base = clean_label(table.header[0]) if table.header[0] and not re.fullmatch(
            r"(?i)sectors?|index|indices|name|company|stock|item|industry|particulars", table.header[0]) else ""
        context = base or (table.heading[:40] if table.heading else "Value")
        return f"{context}, {header}"
    # A metric without a period takes the period its own table names ("fortnight ending 15 Oct 2026").
    period = period_of([header], table.heading, [r[0] for r in table.rows])
    return f"{header}, {period}" if period else header


def _entity_of(label: str, wanted: set[str] | None = None) -> str | None:
    """Canonical entity for a row label: a known sector, or one of the section's own entities."""
    canon = canonical(clean_label(label))
    return canon if canon and (canon in SYNONYMS or (wanted and canon in wanted)) else None


def _value_with_unit(table: Table, col: int, cell: str) -> str:
    cell = cell.strip()
    if "%" in table.header[col] and "%" not in cell and _numeric(cell):
        return f"{cell}%"
    return cell


def tables_in(store) -> list[Table]:
    out = []
    for passage in store.passages:
        if passage.kind in {"table", "calculated"}:
            table = parse_table(passage)
            if table:
                out.append(table)
    return out


# ── stat strip ───────────────────────────────────────────────────────────────
def stat_strip(tables: list[Table], entity: str, preferred: set[str], *, qtd=None, max_items: int = 4) -> str:
    """'> [!STATS] ...' with 3-4 verified numbers for ``entity`` ('' when fewer than 3 exist)."""
    ranked = sorted(tables, key=lambda t: (t.passage_id not in preferred, t.kind != "calculated", -t.reliability))
    items, labels = [], set()
    for table in ranked:
        row = next((r for r in table.rows if _entity_of(r[0], {entity}) == entity), None)
        if row is None:
            continue
        for col in _value_columns(table)[:1]:
            label = _metric_label(table, col)
            if qtd and (qtd(table.header[col]) or qtd(label)):  # the period may come from the table heading
                label += " (QTD, partial)"
            key = re.sub(r"[^a-z0-9]+", " ", label.casefold()).strip()
            if not _numeric(row[col]) or key in labels:
                continue
            labels.add(key)
            items.append(f"{label}: {_value_with_unit(table, col, row[col])} {table.cite}")
        if len(items) >= max_items:
            break
    return f"> [!STATS] {' | '.join(items)}" if len(items) >= 3 else ""


# ── key takeaways / what to watch ────────────────────────────────────────────
_FIGURE_MENTION_RE = re.compile(r"\b(?:Figure|Fig\.)\s*\d+", re.I)


def takeaways(summary: str, max_sentences: int = 3) -> str:
    # A takeaway box stands apart from the figures: a sentence that points at "Figure N" (whose
    # number may belong to another section) is left out.
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z])", str(summary or ""))
                 if len(s.strip()) > 30 and not _FIGURE_MENTION_RE.search(s)]
    if len(sentences) < 2:
        return ""
    return "> [!TAKEAWAYS] " + " ".join(sentences[:max_sentences])


_WATCH_HEADING_RE = re.compile(r"(?i)\b(?:risks?|outlook|watch|catalysts?|scenarios?|what could change)\b")
_WATCH_SENTENCE_RE = re.compile(
    r"(?i)\b(?:risks?|could|may|might|would|watch|monitor|if |unless|downside|upside|catalysts?|key to|depends?|"
    r"headwinds?|pressures?|uncertain\w*|threats?|vulnerab\w*|caution\w*|slowdown|weigh\w*)\b")


def move_watch_items(body: str, max_items: int = 3) -> tuple[str, str]:
    """Move up to 3 risk/outlook sentences from a risks/outlook subsection into a 'what to watch' callout.

    The sentences move (they are not repeated); a subsection left empty loses its heading.
    """
    parts = re.split(r"(?m)^(###\s+.*)$", body)
    moved: list[str] = []
    for i in range(1, len(parts) - 1, 2):
        if not _WATCH_HEADING_RE.search(parts[i]):
            continue
        # Paragraph by paragraph: tables, lists, figures and sub-headings stay untouched,
        # and paragraph breaks are preserved.
        paragraphs_out = []
        for para in re.split(r"\n\s*\n", parts[i + 1].strip()):
            lead = ""
            if para.lstrip().startswith("#") and "\n" in para.strip():
                # "#### Sub-heading\nParagraph": keep the sub-heading, work on its paragraph.
                lead, para = para.strip().split("\n", 1)
                paragraphs_out.append(lead)
            if (not para.strip() or "\n" in para.strip()
                    or para.lstrip().startswith(("|", ">", "[CHART", "[[FIGURE", "-", "*", "#", "**Table"))):
                if para.strip():
                    paragraphs_out.append(para)
                continue
            keep = []
            for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z])", para.strip()):
                if len(moved) < max_items and _WATCH_SENTENCE_RE.search(sentence) and CITE_RE.search(sentence):
                    moved.append(sentence.strip())
                else:
                    keep.append(sentence)
            if keep:
                paragraphs_out.append(" ".join(keep))
        parts[i + 1] = ("\n\n" + "\n\n".join(paragraphs_out) + "\n\n") if paragraphs_out else "\n\n"
        if not paragraphs_out:
            parts[i] = ""
    if not moved:
        return body, ""
    new_body = re.sub(r"\n{3,}", "\n\n", "".join(parts)).strip()
    return new_body, "> [!WATCH] " + " ".join(moved)


# ── inline compact tables ────────────────────────────────────────────────────
def table_markdown(table: Table, caption: str) -> str:
    cols = list(range(min(len(table.header), MAX_INLINE_COLS)))
    head = "| " + " | ".join(clean_label(table.header[c]) or " " for c in cols) + " |"
    sep = "| " + " | ".join("---" for _ in cols) + " |"
    rows = ["| " + " | ".join(clean_label(r[c]) if c == 0 else r[c] for c in cols) + " |" for r in table.rows[:MAX_INLINE_ROWS]]
    return "\n".join([f"**Table: {caption}**", head, sep, *rows, "", f"Source: {table.cite}"])


def place_inline_tables(body: str, by_id: dict[str, Table], placed: set[str], *, max_tables: int = 2) -> str:
    """Insert each small cited table after the paragraph that first cites its passage (once per report)."""
    added = 0
    paragraphs = re.split(r"\n\s*\n", body)
    out = []
    for para in paragraphs:
        out.append(para)
        if added >= max_tables or para.lstrip().startswith(("|", "#", ">", "[CHART")):
            continue
        for group in CITE_RE.findall(para):
            for pid in (t.strip().upper() for t in re.split(r"\s*[,;]\s*", group)):
                table = by_id.get(pid)
                if (table is None or pid in placed or len(table.rows) > MAX_INLINE_ROWS
                        or len(table.header) > MAX_INLINE_COLS + 2):
                    continue
                caption = table.heading if 6 <= len(table.heading) <= 90 and not table.heading.endswith("?") else \
                    f"{clean_label(table.header[0]) or 'Item'} by {clean_label(table.header[1])}"
                out.append(table_markdown(table, caption))
                placed.add(pid)
                added += 1
                break
            if added >= max_tables or (out and out[-1].startswith("**Table:")):
                break
    return "\n\n".join(out)


# ── comparison and scorecard ────────────────────────────────────────────────
@dataclass
class Column:
    table: Table
    col: int
    label: str
    values: dict[str, str] = field(default_factory=dict)


def entities_mentioned(text: str) -> list[str]:
    """Known sectors a section's text discusses, in order of first mention."""
    from utils.chart_titles import _SECTOR_ALIAS_RE

    found = []
    for canon, pattern in _SECTOR_ALIAS_RE.items():
        match = pattern.search(CITE_RE.sub(" ", text or ""))
        if match:
            found.append((match.start(), canon))
    return [name for _pos, name in sorted(found)]


def insert_after_first_paragraph(body: str, block: str) -> str:
    """Put a block after the section's opening paragraph (or at the top when it opens with a heading)."""
    parts = re.split(r"\n\s*\n", body, maxsplit=1)
    if len(parts) == 2 and not parts[0].lstrip().startswith(("#", "|", ">", "[CHART")):
        return f"{parts[0]}\n\n{block}\n\n{parts[1]}"
    return f"{block}\n\n{body}"


def entity_columns(tables: list[Table], entities: list[str], *, qtd=None, preferred: set[str] | None = None) -> list[Column]:
    wanted = set(entities)
    columns = []
    for table in tables:
        mapped = {}
        for row in table.rows:
            entity = _entity_of(row[0], wanted)
            if entity in wanted and entity not in mapped:
                mapped[entity] = row
        if len(mapped) < 3:
            continue
        for col in _value_columns(table)[:1]:
            label = _metric_label(table, col)
            if qtd and (qtd(table.header[col]) or qtd(label)):  # the period may come from the table heading
                label += " (QTD, partial)"
            values = {e: _value_with_unit(table, col, r[col]) for e, r in mapped.items() if _numeric(r[col])}
            if len(values) >= 3:
                columns.append(Column(table, col, label, values))
    columns.sort(key=lambda c: (c.table.passage_id not in (preferred or set()), -len(c.values),
                                c.table.kind != "calculated", -c.table.reliability))
    chosen, seen = [], set()

    def number(cell: str) -> float | None:
        try:
            return round(float(re.sub(r"[^\d.\-]", "", cell.replace("−", "-").replace(",", ""))), 2)
        except ValueError:
            return None

    def same_data(a: Column, b: Column) -> bool:
        shared = [e for e in a.values if e in b.values]
        if len(shared) < 3:
            return False
        equal = sum(1 for e in shared if number(a.values[e]) is not None and number(a.values[e]) == number(b.values[e]))
        return equal >= 0.8 * len(shared)

    for column in columns:
        key = re.sub(r"[^a-z0-9]+", " ", column.label.casefold()).strip()
        if key in seen or any(same_data(column, other) for other in chosen):
            continue  # the same figures from a second source are one column, not two
        seen.add(key)
        chosen.append(column)
    return chosen


def entity_table(columns: list[Column], entities: list[str], *, caption: str, max_cols: int = 4,
                 min_values: int = 2) -> str:
    columns = columns[:max_cols]
    if len(columns) < 2:
        return ""
    rows = []
    for entity in entities:
        # A sector without a value in a column gets an EMPTY cell: the chart checks read an em dash
        # as "this value is not verified", which would contradict a chart of the same metric.
        cells = [c.values.get(entity, "") for c in columns]
        if sum(cell != "" for cell in cells) >= min_values:
            rows.append("| " + " | ".join([entity, *cells]) + " |")
    if len(rows) < 3:
        return ""
    head = "| " + " | ".join(["Sector", *[f"{c.label} {c.table.cite}" for c in columns]]) + " |"
    sep = "| " + " | ".join("---" for _ in range(len(columns) + 1)) + " |"
    sources = ", ".join(dict.fromkeys(c.table.cite for c in columns))
    return "\n".join([f"**Table: {caption}**", head, sep, *rows[:20], "", f"Source: {sources}"])


def fix_heading_runs(markdown: str) -> tuple[str, int]:
    """No heading directly followed by another heading.

    An H2 followed straight by an H3 loses the H3 (its text becomes the section's
    opening); an H3/H4 with no text before the next heading (an empty subsection)
    is removed. Returns (markdown, headings removed).
    """
    lines = markdown.split("\n")
    heading = lambda ln: (len(m.group(1)) if (m := re.match(r"^(#{2,6})\s+\S", ln)) else 0)  # noqa: E731
    drop: set[int] = set()
    for i, line in enumerate(lines):
        level = heading(line)
        if not level:
            continue
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        if j >= len(lines):
            if level > 2:
                drop.add(i)
            continue
        next_level = heading(lines[j])
        if not next_level:
            continue
        if level >= 3:
            drop.add(i)
        elif next_level >= 3:
            drop.add(j)
    out = [ln for k, ln in enumerate(lines) if k not in drop]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)), len(drop)


def how_to_read(*, deep_dives: int, compact: int, has_qtd: bool, has_calculated: bool) -> str:
    parts = [
        "The executive summary and this scorecard give the answer;",
        "the cross-cutting sections explain the market regime, flows and valuations;",
        f"{deep_dives} sector deep-dives follow, each opening with its key figures and takeaways;" if deep_dives else "",
        f"{compact} further sectors are compared in compact sections;" if compact else "",
        "the outlook closes the analysis, and the appendices hold the data tables and the source list.",
        "Every figure carries a source citation [S#] that resolves to the Data Sources appendix.",
        "Numbers marked 'calculated' were derived from the cited inputs shown beside them." if has_calculated else "",
        "Current-quarter data is labelled 'QTD, partial' and kept apart from completed quarters." if has_qtd else "",
    ]
    return "> [!NOTE:How to read this report] " + " ".join(p for p in parts if p)
