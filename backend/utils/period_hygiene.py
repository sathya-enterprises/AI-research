"""Period hygiene for tables, columns and stat strips (Stage I).

A Stage H scorecard showed April 2024 "1-month returns" in a November 2026 report whose window
starts in October 2024, under an unlabeled column. Every column the layout builds now carries an
explicit period label or an as-of date, and data from before the window (or with no date at all) is
kept out of scorecards, stat strips, comparison and inline tables.

  * ``table_period(heading, header, published)`` -> (label, end_date): the latest dated period named
    by the table heading or its column headers (month + year, FY / calendar quarter, year); else the
    source's publication date as an "as of" date; else (None, None).
  * calendar quarters of the fetched dataset ("Q3 2026") are shown as fiscal quarters ("Q2 FY27").
"""
from __future__ import annotations

import calendar
import re
from datetime import date

_MONTHS = "jan feb mar apr may jun jul aug sep oct nov dec".split()
_MONTH_YEAR_RE = re.compile(r"(?i)\b(?:(\d{1,2})\s+)?(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?,?\s+(?:(\d{1,2}),?\s+)?((?:19|20)\d{2})\b")
_FY_QUARTER_RE = re.compile(r"(?i)\bQ([1-4])\s*FY\s*'?(\d{2,4})\b")
_CAL_QUARTER_RE = re.compile(r"\bQ([1-4])\s+((?:19|20)\d{2})\b")
_FY_RE = re.compile(r"(?i)\bFY\s*'?(\d{2})(?:\s*[-/]\s*\d{2})?\b")
_YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")


def fy_label(calendar_quarter: str) -> str:
    match = _CAL_QUARTER_RE.fullmatch(str(calendar_quarter or "").strip())
    if not match:
        return calendar_quarter
    q, year = int(match.group(1)), int(match.group(2))
    fy_q, fy = (4, year) if q == 1 else (q - 1, year + 1)
    return f"Q{fy_q} FY{fy % 100:02d}"


def fy_labels_in(text: str) -> str:
    """Every calendar quarter inside a label shown as a fiscal quarter ('Q4 2024 to Q3 2026' -> 'Q3 FY25 to Q2 FY27')."""
    return _CAL_QUARTER_RE.sub(lambda m: fy_label(m.group(0)), str(text or ''))


def qtd_tag_applies(label: str) -> bool:
    """'(QTD, partial)' marks a period aggregate of the running quarter; not a one-day/as-of value, and not twice."""
    return not re.search(r"(?i)\bas of\b|\bQTD\b|^\s*(?:1D|1 ?day|daily|change %?)\b", label or '')


def _month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def _ends(text: str) -> list[tuple[date, str]]:
    out = []
    for m in _MONTH_YEAR_RE.finditer(text or ""):
        month = _MONTHS.index(m.group(2)[:3].casefold()) + 1
        out.append((_month_end(int(m.group(4)), month), m.group(0).strip()))
    for m in _FY_QUARTER_RE.finditer(text or ""):
        q, fy = int(m.group(1)), int(m.group(2)) % 100 + 2000
        month = {1: 6, 2: 9, 3: 12, 4: 3}[q]
        year = fy if q == 4 else fy - 1
        out.append((_month_end(year, month), m.group(0).strip()))
    for m in _CAL_QUARTER_RE.finditer(text or ""):
        q, year = int(m.group(1)), int(m.group(2))
        out.append((_month_end(year, q * 3), fy_label(m.group(0))))
    if not out:
        for m in _FY_RE.finditer(text or ""):
            fy = int(m.group(1)) + 2000
            out.append((date(fy, 3, 31), m.group(0).strip()))
    if not out:
        for m in _YEAR_RE.finditer(text or ""):
            out.append((date(int(m.group(1)), 12, 31), m.group(1)))
    return out


def _parse_published(published: str) -> date | None:
    text = str(published or "").strip()
    m = re.match(r"((?:19|20)\d{2})-(\d{2})-(\d{2})", text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    found = _ends(text)
    return found[0][0] if found else None


def table_period(heading: str, header: list[str], published: str = "") -> tuple[str | None, date | None]:
    """(label, end date) of the data a table holds; (None, None) when nothing dates it."""
    found = _ends(" ".join([heading or "", *[h or "" for h in header or []]]))
    if found:
        end, label = max(found, key=lambda x: x[0])
        return label, end
    as_of = _parse_published(published)
    if as_of:
        return f"as of {as_of:%d %b %Y}", as_of
    return None, None


def in_window(end: date | None, window_start: date | None) -> bool:
    """Usable in scorecards / stat strips / layout tables: dated, and not ending before the window."""
    if window_start is None:
        return True
    return end is not None and end >= window_start
