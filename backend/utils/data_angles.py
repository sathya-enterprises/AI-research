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


def window_years(question: str, today: date) -> list[int]:
    since = re.search(r"(?i)\b(?:since|from)\s+((?:19|20)\d{2})\b", question or "")
    try:
        from utils.source_quality import window_start
        start = int(since.group(1)) if since else window_start(question, today).year
    except Exception:
        start = today.year - 2
    return list(range(max(start, today.year - 6), today.year + 1))


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
