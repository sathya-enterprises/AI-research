"""Narrow a large evidence table to what the question asks about, instead of dropping it.

Open-data downloads (World Bank / WHO / OWID CSVs, statistical annexes) arrive as wide tables: every country
of the world, every year since 1960. A live health report fetched exactly the right table ("Year | World |
United States | China | India | ... | Brazil | Nigeria", 1990-2024) and lost it, because the whole table was
judged outside the 2010-2024 window and off-topic. Here the chart builder is told which rows and columns to
plot: the entities the question names (columns or rows) and the periods inside the window. Rows are never
rewritten, so every plotted value still quotes its own source row exactly.
"""
from __future__ import annotations

import re


def focus_entities(question: str) -> list[str]:
    """Countries / regions the question names (with their common aliases), in question order."""
    try:
        from utils.official_data import COUNTRIES, mentioned_entities
    except Exception:  # pragma: no cover
        return []
    names = [COUNTRIES[c] for c in mentioned_entities(question) if c in COUNTRIES and c != "WLD"]
    return list(dict.fromkeys(names))


_ALIASES = {"united states": {"us", "u.s.", "usa", "united states of america"}, "united kingdom": {"uk", "u.k."},
            "south korea": {"korea, rep.", "korea", "republic of korea"}, "russia": {"russian federation"},
            "china": {"china (mainland)", "people's republic of china"}}


def _key(text: str) -> str:
    """Comparable cell text: flags / footnote marks / emphasis dropped ("🇨🇳 China" -> "china")."""
    text = re.sub(r"[^\w\s.,'()-]", " ", str(text or ""))
    return re.sub(r"\s+", " ", text).strip(" *").casefold()


def matches(label: str, entity: str) -> bool:
    key, ent = _key(label), _key(entity)
    return key == ent or key in _ALIASES.get(ent, set())


def _period_end(label: str) -> int | None:
    from utils.section_charts import _period_end_year
    return _period_end_year(label)


def plan(header: list[str], rows: list[list[str]], *, entities: list[str], min_year: int | None) -> dict:
    """{'cols': [...] | None, 'rows': {row labels} | None, 'reason': str} — None means "no restriction".

    * columns: when >= 2 of the question's entities are column headers, only those columns (the first column,
      the row label, is always kept);
    * rows: when >= 2 of the question's entities are row labels of a table with >= 5 rows, only those rows;
    * periods: when the row labels are dated periods and some fall before ``min_year``, only the in-window rows.
    """
    out: dict = {"cols": None, "rows": None, "reason": ""}
    if not header or not rows:
        return out
    reasons = []
    if entities:
        cols = [i for i, h in enumerate(header) if i > 0 and any(matches(h, e) for e in entities)]
        if len(cols) >= 2:  # (also when every column is an entity: they are compared as entities)
            out["cols"] = cols
            reasons.append("columns: " + ", ".join(header[i] for i in cols))
        labels = [r[0] for r in rows if r]
        keep = {lab for lab in labels if any(matches(lab, e) for e in entities)}
        if len(keep) >= 2 and len(labels) >= 5 and len(keep) < len(labels):
            out["rows"] = keep
            reasons.append("rows: " + ", ".join(sorted(keep)))
    if min_year:  # period COLUMNS ("Country | 1990 | ... | 2024"): only the in-window ones
        ends = {i: _period_end(h) for i, h in enumerate(header) if i > 0}
        dated = {i: y for i, y in ends.items() if y is not None}
        if len(dated) >= 3 and len(dated) >= 0.8 * len(ends):
            inside = [i for i, y in dated.items() if y >= min_year]
            if len(inside) >= 3 and len(inside) < len(dated):
                base = out["cols"] if out["cols"] is not None else [i for i in ends]
                out["cols"] = [i for i in base if i in inside or i not in dated]
                reasons.append(f"period columns from {min_year}")
        labels = [r[0] for r in rows if r]
        ends = {lab: _period_end(lab) for lab in labels}
        if labels and all(v is not None for v in ends.values()):
            inside = {lab for lab, y in ends.items() if y >= min_year}
            if len(inside) >= 3 and len(inside) < len(labels):
                out["rows"] = inside if out["rows"] is None else (out["rows"] & inside)
                reasons.append(f"periods from {min_year}")
    out["reason"] = "; ".join(reasons)
    return out
