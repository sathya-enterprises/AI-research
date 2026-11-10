"""Stage M: shape checks on charted data - for any topic, no domain knowledge.

Verification proves a value is printed on the cited page. These checks ask whether the series makes sense
as a series, using only its shape:

  * place  - a single-place chart about a country / region the question does not ask about (a US sector
             P/E chart in a report about India) is dropped; multi-place comparisons are kept (peers);
  * unit   - magnitudes impossible for the stated unit ("%" values above 1,000; "billion" values above
             100,000; "million" values above 100,000,000) mean the column's unit was mislabelled: dropped;
  * jump   - one step of a positive level series that moves by more than 1.8x (or below 0.55x) while
             every other step stays within +/-15%: kept, with a warning recorded and shown in the
             chart's reading ("check against the source"), because real breaks exist.
"""
from __future__ import annotations

import math
import re


def _series(spec: dict) -> list[dict]:
    return [s for s in (spec.get("series") or []) if isinstance(s, dict)]


def _values(spec: dict) -> list[float]:
    out = []
    for s in _series(spec):
        for p in s.get("data") or []:
            try:
                out.append(float(p.get("value")))
            except (TypeError, ValueError):
                continue
    if spec.get("type") == "heatmap":
        out += [float(v) for row in spec.get("values") or [] for v in row if isinstance(v, (int, float))]
    return out


def _chart_text(spec: dict) -> str:
    parts = [str(spec.get("title") or ""), str(spec.get("yLabel") or "")]
    for s in _series(spec):
        parts.append(str(s.get("name") or ""))
        parts += [str(p.get("label") or "") for p in s.get("data") or []]
    return " | ".join(parts)


def place_problem(spec: dict, question: str) -> str:
    from utils.official_data import COUNTRIES, mentioned_entities
    asked = set(mentioned_entities(question)) - {"WLD"}
    if not asked:
        return ""
    shown = set(mentioned_entities(_chart_text(spec))) - {"WLD"}
    if len(shown) == 1 and not (shown & asked):
        other = COUNTRIES.get(next(iter(shown)), next(iter(shown)))
        return f"the chart is about {other}, not the place the question asks about"
    return ""


def unit_problem(spec: dict) -> str:
    vals = [abs(v) for v in _values(spec)]
    if not vals:
        return ""
    unit = f"{spec.get('unit') or ''} {spec.get('yLabel') or ''} {spec.get('title') or ''}".casefold()
    top = max(vals)
    if re.search(r"%|per ?cent|percentage", unit) and not re.search(r"(?i)crore|billion|million|bn|mn|₹|\$", unit) and top > 1000:
        return f"values up to {top:,.0f} cannot be percentages (the column's unit is mislabelled)"
    if re.search(r"\bbillion|\bbn\b", unit) and top > 100_000:
        return f"values up to {top:,.0f} are implausible in billions (the column's unit is mislabelled)"
    if re.search(r"\bmillion|\bmn\b", unit) and not re.search(r"\bbillion|\bbn\b", unit) and top > 100_000_000:
        return f"values up to {top:,.0f} are implausible in millions (the column's unit is mislabelled)"
    return ""


def jump_warnings(spec: dict) -> list[str]:
    """One sudden step in an otherwise stable positive level series (per series, time order as plotted)."""
    from utils.chart_lint import is_time_axis, time_key
    out = []
    for s in _series(spec):
        pts = [p for p in s.get("data") or [] if isinstance(p.get("value"), (int, float))]
        labels = [str(p.get("label") or "") for p in pts]
        if len(pts) < 3 or not is_time_axis(labels):
            continue
        keys = [time_key(l) for l in labels]
        if None not in keys:
            pts = [p for _, p in sorted(zip(keys, pts), key=lambda kv: kv[0])]
        vals = [float(p["value"]) for p in pts]
        if min(vals) <= 0:
            continue  # flows / changes legitimately swing; only level series are checked
        steps = [b / a for a, b in zip(vals, vals[1:])]
        big = [i for i, r in enumerate(steps) if r > 1.8 or r < 0.55]
        calm = [r for i, r in enumerate(steps) if i not in big]
        if len(big) == 1 and calm and all(0.87 <= r <= 1.15 for r in calm):
            i = big[0]
            out.append(f"{s.get('name') or 'the series'} moves from {vals[i]:g} in {pts[i].get('label')} to "
                       f"{vals[i + 1]:g} in {pts[i + 1].get('label')} ({steps[i]:.1f}x) while other years change "
                       f"by less than 15% - check against the source")
    return out


def chart_data_problem(spec: dict, question: str) -> str:
    """'' when the chart's data passes the shape checks; otherwise why it is dropped."""
    return place_problem(spec, question) or unit_problem(spec)


def finite(spec: dict) -> bool:
    return all(math.isfinite(v) for v in _values(spec))
