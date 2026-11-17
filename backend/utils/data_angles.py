"""Data-seeking search angles (Stage J).

Topic angles retrieve analysis and news; they rarely retrieve the year-by-year numbers a report needs for
charts. For every web-researched report this adds 1 (detailed) or 2 (in-depth) angles that ask for the
question's core metric as statistics inside the question's time window, from official / primary sources.
Deterministic: no LLM call; at most 2 extra searches.
"""
from __future__ import annotations

import re
from datetime import date

_STOP = {
    "how", "has", "have", "had", "what", "which", "why", "when", "where", "who", "are", "is", "was", "were", "the", "a",
    "an", "and", "or", "of", "in", "on", "for", "to", "over", "past", "last", "next", "years", "year", "quarters",
    "quarter", "months", "month", "key", "main", "major", "evolved", "evolve", "changed", "change", "explain",
    "analyze", "analyse", "describe", "discuss", "can", "you", "your", "its", "their", "this", "that", "these",
    "those", "do", "does", "did", "be", "been", "being", "with", "from", "by", "about", "into", "between", "among",
    "growth", "drivers", "driver", "policy", "policies", "changes", "leading", "companies", "outlook", "trends",
    "trend", "impact", "impacts", "role", "future", "current", "recent", "recently", "today", "&", "s", "it",
    "give", "provide", "detailed", "analysis", "report", "overview", "state", "status", "performance",
    "causes", "cause", "effects", "effect", "economic", "since", "main", "shifts", "shift",
}
_DATA_WORDS = re.compile(r"(?i)\b(?:data|statistics|year-wise|yearly|annual|figures|numbers|sales volume|registrations)\b")


def subject_terms(question: str, limit: int = 6) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9&\-]*'?s?", question or "")
    out = []
    for w in words:
        core = re.sub(r"'s$", "", w)
        if core.casefold() in _STOP or len(core) < 2 or re.fullmatch(r"\d+", core):
            continue
        out.append(core)
    return list(dict.fromkeys(out))[:limit]


# "FY2021", "FY 21", "FY2020-21", "FY20–21": group 1 = first year, group 2 = optional second (end) year
_FY_TOKEN = r"FY\s*'?((?:19|20)?\d{2})(?:\s*[-–/]\s*((?:19|20)?\d{2}))?(?!\d)"


def _fy_end_year(first: str | None, second: str | None) -> int | None:
    """The calendar year a fiscal year ends in: FY2021 / FY21 / FY2020-21 -> 2021."""
    raw = second or first
    if not raw:
        return None
    year = int(raw)
    if year < 100:
        year += 2000
    return year if 1900 <= year <= 2200 else None


def requested_year_bounds(question: str, today: date) -> tuple[int, int]:
    """Inclusive calendar-year bounds, preserving explicit historical endpoints."""
    text = str(question or "")
    # Fiscal-year ranges ("FY2021 to FY2025", "FY21-FY25", "FY2020-21 to FY2024-25"). The plain-year pattern
    # below needs a word boundary before the year, so "FY2021" never matched and every FY question fell back to
    # the default recent window (2024-2026): charts and official datasets for FY2021-FY2023 were dropped.
    # An Indian FY ends in March of its year, so FY2021 starts in calendar 2020.
    fy_range = re.search(
        rf"(?i)\b{_FY_TOKEN}\s*(?:to|until|till|through|and|-|–|—)\s*{_FY_TOKEN}", text)
    if fy_range:
        first, last = _fy_end_year(fy_range.group(1), fy_range.group(2)), _fy_end_year(fy_range.group(3), fy_range.group(4))
        if first and last:
            first, last = sorted((first, last))
            return first - 1, min(last, today.year)
    # Split-year ranges ("from 2018-19 to 2024-25"): calendar 2018 to 2025. Only "from 2018" was read, so the
    # window ran to the current year (agri_msp: 2018-2026).
    split_range = re.search(
        r"(?i)\b((?:19|20)\d{2})\s*[-–/]\s*(\d{2})\s*(?:to|until|till|through|and|-|–|—)\s*((?:19|20)\d{2})\s*[-–/]\s*(\d{2})\b",
        text)
    if split_range:
        start = int(split_range.group(1))
        end_start = int(split_range.group(3))
        end = int(str(end_start)[:2] + split_range.group(4))
        end = end + 100 if end < end_start else end
        return min(start, end), min(max(start, end), today.year)
    # Month names may stand before either year ("from January 2022 to December 2024"): read as 2024-2026,
    # which kept every 2022-2023 inflation chart out of the report.
    _month = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?,?\s+"
    explicit_range = re.search(
        rf"(?i)\b(?:(?:from|between)\s+)?(?:{_month})?((?:19|20)\d{{2}})\s*(?:to|until|till|through|and|-|–|—)\s*"
        rf"(?:{_month})?((?:19|20)\d{{2}})\b",
        text)
    if explicit_range:
        start, end = sorted((int(explicit_range.group(1)), int(explicit_range.group(2))))
        return start, min(end, today.year)

    single_split = re.search(r"\b((?:19|20)\d{2})\s*[-–/]\s*(\d{2})\b(?!\s*[-/]\s*\d)", text)
    if single_split and not re.search(r"(?i)\b(?:since|after)\s+(?:19|20)\d{2}", text):
        start = int(single_split.group(1))
        end = int(str(start)[:2] + single_split.group(2))
        end = end + 100 if end < start else end
        if end == start + 1:  # one fiscal year ("in 2023-24"): its two calendar years
            return start, min(end, today.year)
    since = re.search(r"(?i)\b(?:since|from|after)\s+((?:19|20)\d{2})\b", text)
    relative_window = re.search(
        r"(?i)\b(?:past|last|over\s+the\s+past|during\s+the\s+past|in\s+the\s+last)\s+"
        r"(?:\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+(?:years?|decades?|months?|quarters?)\b",
        text)
    try:
        from utils.source_quality import window_start
        start = int(since.group(1)) if since else window_start(text, today).year
    except Exception:
        start = today.year - 2
    if since or relative_window:
        return start, today.year
    # Bound an unspecified/default search window, but never truncate a window the question asked for.
    return max(start, today.year - 6), today.year


def window_years(question: str, today: date) -> list[int]:
    start, end = requested_year_bounds(question, today)
    return list(range(start, end + 1))


def data_angles(question: str, existing: list[str], *, tier: str, today: date | None = None) -> list[str]:
    """The extra queries to add (possibly none)."""
    if any(_DATA_WORDS.search(q or "") for q in existing):
        return []
    today = today or date.today()
    terms = subject_terms(question)
    if len(terms) < 2:
        return []
    years = " ".join(str(y) for y in window_years(question, today))
    subject = " ".join(terms)
    angles = [f"{subject} year-wise statistics {years} official data"]
    if tier == "in_depth":
        angles.append(f"{subject} annual figures table {years} report pdf")
    return [a for a in angles if a.casefold() not in {q.casefold() for q in existing}]
