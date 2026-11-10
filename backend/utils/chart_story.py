"""Stage L: a deterministic reading of any verified chart, built only from its plotted points.

A verified chart that the section writer did not mention used to be dropped ("not referenced or
interpreted by the section text"), so reports stayed text-heavy even when the evidence held good
tables. Every verified chart now carries an interpretation: when the writer skips it, the figure is
placed with this sentence (``section_writer`` already does this for fetched-dataset charts).

The sentence quotes each value as the source printed it (``evidenceValue``; accounting negatives
"(1,234)" shown as "-1,234") and cites the evidence passages the chart was copied from, so the
number checks find every value in the cited passage.
"""
from __future__ import annotations

import re

_NUM_RE = re.compile(r"[-+−]?\d[\d,]*(?:\.\d+)?")


def _shown(point: dict, unit: str) -> str:
    raw = str(point.get("evidenceValue") or "").strip()
    raw = re.sub(r"^\(\s*([\d,.]+)\s*\)$", r"-\1", raw)
    if not raw or not _NUM_RE.search(raw):
        value = float(point.get("value") or 0)
        raw = f"{value:,.2f}".rstrip("0").rstrip(".")
    if unit and unit not in raw and not re.search(r"[%a-zA-Z₹$]", raw):
        money = re.match(r"(?i)^\s*(₹|rs\.?|inr|us\$|\$|€|£)\s*(.*)$", unit)
        if unit == "%":
            raw = f"{raw}%"
        elif money:
            sign = "-" if raw.startswith(("-", "−")) else ("+" if raw.startswith("+") else "")
            symbol = "₹" if money.group(1).lower().startswith(("rs", "inr", "₹")) else money.group(1)
            raw = f"{sign}{symbol}{raw.lstrip('-−+')}" + (f" {money.group(2).strip().lower()}" if money.group(2).strip() else "")
        else:
            raw = f"{raw} {unit}"
    return raw


def _cite(passage_ids: list[str]) -> str:
    ids = [p for p in passage_ids if p][:3]
    return f" [{', '.join(ids)}]" if ids else ""


def _when(label: str) -> str:
    """'on 10 Nov 2026' for a date, 'in Nov 2026' / 'in 2024' / 'in February' otherwise."""
    return f"on {label}" if re.match(r"^\d{1,2}\s+[A-Za-z]{3}", label) else f"in {label}"


def _name(label: object) -> str:
    text = str(label or "").strip()
    if text.isupper() and len(text) > 5:  # "MAX HEALTHCARE INSTITUTE LTD" -> "Max Healthcare Institute Ltd"
        return " ".join(w if len(w) <= 3 and w not in {"LTD", "THE", "AND"} else w.capitalize() for w in text.split())
    return text


def _unit(spec: dict) -> str:
    unit = str(spec.get("unit") or "").strip()
    if unit:
        return unit
    for field in ("yLabel", "title"):
        m = re.search(r"\(([^()]*(?:crore|cr|lakh|bn|billion|million|mn|%|x)[^()]*)\)", str(spec.get(field) or ""), re.I)
        if m:
            return m.group(1).strip()
    return ""


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:] if text[:2] != text[:2].upper() else text


def interpret_chart(spec: dict, passage_ids: list[str]) -> str:
    """One or two sentences reading the chart; '' when the chart has no readable series."""
    series = [s for s in (spec.get("series") or []) if isinstance(s, dict) and s.get("data")]
    if not series:
        return ""
    unit = _unit(spec)
    kind = str(spec.get("type") or "")
    cite = _cite(list(passage_ids))
    from utils.chart_lint import is_time_axis
    labels = [str(p.get("label") or "") for p in series[0]["data"]]
    timed = kind in {"line", "area", "spline"} or is_time_axis(labels)
    if timed:
        parts = []
        from utils.chart_lint import time_key
        for s in series[:4]:
            pts = list(s["data"])
            keys = [time_key(str(p.get("label") or "")) for p in pts]
            if None not in keys:
                pts = [p for _, p in sorted(zip(keys, pts), key=lambda kv: kv[0])]
            if len(pts) < 2:
                continue
            first, last = pts[0], pts[-1]
            text = (f"{_name(s.get('name')) or 'the series'} went from {_shown(first, unit)} {_when(str(first.get('label')))} "
                    f"to {_shown(last, unit)} {_when(str(last.get('label')))}")
            if len(pts) >= 4:
                hi = max(pts, key=lambda p: float(p.get("value") or 0))
                lo = min(pts, key=lambda p: float(p.get("value") or 0))
                if hi is not first and hi is not last:
                    text += f", peaking at {_shown(hi, unit)} {_when(str(hi.get('label')))}"
                if lo is not first and lo is not last and lo is not hi:
                    text += f" with a low of {_shown(lo, unit)} {_when(str(lo.get('label')))}"
            parts.append(text)
        if not parts:
            return ""
        return "; ".join(parts[:3]) + cite + "."
    pts = series[0]["data"]
    if len(pts) < 3:
        return ""
    hi = max(pts, key=lambda p: float(p.get("value") or 0))
    lo = min(pts, key=lambda p: float(p.get("value") or 0))
    positives = sum(1 for p in pts if float(p.get("value") or 0) > 0)
    if 0 < positives < len(pts):
        verb = "was" if positives == 1 else "were"
        return (f"{positives} of the {len(pts)} values shown {verb} positive: {_name(hi.get('label'))} was highest at "
                f"{_shown(hi, unit)} and {_name(lo.get('label'))} lowest at {_shown(lo, unit)}{cite}.")
    return (f"{_name(hi.get('label'))} was highest at {_shown(hi, unit)} and {_name(lo.get('label'))} lowest at "
            f"{_shown(lo, unit)} among the {len(pts)} shown{cite}.")
