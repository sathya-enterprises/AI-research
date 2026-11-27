"""Explaining an existing chart from its underlying data, not from a picture of it.

``chart_facts`` reads the chart spec (series, labels, values, unit) and computes the observations an explanation
may use: per series the highest and lowest point, first and last values, the change between them (absolute,
percent, and the compound annual rate when labels are years), and at the last shared label which series is
largest and the gap to the smallest.

``check_explanation`` accepts an explanation only if
  * every number it states is a chart value, a computed observation, or a number in the supplied evidence
    (rounding to the chart's precision is allowed);
  * every causal claim ("because", "driven by", "due to", "led to", ...) sits in a sentence that cites a source
    from the supplied evidence - an observed pattern may be described, a cause must be sourced;
  * every [S#] it cites is one of the supplied sources.
"""
from __future__ import annotations

import math
import re

_NUM_RE = re.compile(r"(?<![\w.])[-+−]?\d[\d,]*(?:\.\d+)?")
_CITE_RE = re.compile(r"\[(S\d+(?:\.\d+)?(?:\s*[,;]\s*S\d+(?:\.\d+)?)*)\]")
_CAUSAL_RE = re.compile(r"\b(?:because|due\s+to|driven\s+by|caused\s+by|causing|as\s+a\s+result\s+of|led\s+to|leads\s+to|"
                        r"owing\s+to|thanks\s+to|resulted\s+in|results\s+in|attributable\s+to|on\s+account\s+of)\b", re.I)
_YEAR_RE = re.compile(r"^(?:FY\s*)?((?:19|20)\d{2})")


def _num(x) -> float | None:
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def _year(label) -> int | None:
    """2021, FY2021, FY21, 2021-22 -> 2021."""
    text = str(label or "").strip()
    m = _YEAR_RE.match(text)
    if m:
        return int(m.group(1))
    m = re.match(r"^FY\s*'?(\d{2})\b", text, re.I)
    return 2000 + int(m.group(1)) if m else None


def chart_facts(spec: dict) -> dict:
    unit = str(spec.get("unit") or "")
    series_out = []
    for series in spec.get("series") or []:
        pts = [(str(p.get("label")), _num(p.get("value"))) for p in series.get("data") or []]
        pts = [(lab, v) for lab, v in pts if v is not None]
        if not pts:
            continue
        hi = max(pts, key=lambda t: t[1])
        lo = min(pts, key=lambda t: t[1])
        first, last = pts[0], pts[-1]
        item = {"name": str(series.get("name") or ""), "points": len(pts), "highest": hi, "lowest": lo,
                "first": first, "last": last, "change": round(last[1] - first[1], 6)}
        if first[1]:
            item["changePct"] = round(100 * (last[1] - first[1]) / abs(first[1]), 2)
        y0, y1 = _year(first[0]), _year(last[0])
        if y0 and y1 and y1 > y0 and first[1] > 0 and last[1] > 0:
            item["cagrPct"] = round(100 * ((last[1] / first[1]) ** (1 / (y1 - y0)) - 1), 2)
        series_out.append(item)
    facts = {"title": str(spec.get("title") or ""), "unit": unit, "type": str(spec.get("type") or ""),
             "series": series_out}
    if len(series_out) > 1:
        last_label = series_out[0]["last"][0]
        values = [(s["name"], s["last"][1]) for s in series_out if s["last"][0] == last_label]
        if len(values) > 1:
            top = max(values, key=lambda t: t[1])
            bottom = min(values, key=lambda t: t[1])
            facts["atLastLabel"] = {"label": last_label, "largest": top, "smallest": bottom,
                                    "gap": round(top[1] - bottom[1], 6)}
    return facts


def facts_text(facts: dict) -> str:
    unit = facts.get("unit") or ""
    lines = [f"Chart: {facts.get('title')}", f"Unit: {unit or 'as labelled'}"]
    for s in facts.get("series") or []:
        line = (f"- {s['name'] or 'series'}: {s['points']} points; highest {s['highest'][1]:g} ({s['highest'][0]}); "
                f"lowest {s['lowest'][1]:g} ({s['lowest'][0]}); first {s['first'][1]:g} ({s['first'][0]}); "
                f"last {s['last'][1]:g} ({s['last'][0]}); change {s['change']:g}")
        if "changePct" in s:
            line += f" ({s['changePct']:g}%)"
        if "cagrPct" in s:
            line += f"; compound annual rate {s['cagrPct']:g}%"
        lines.append(line)
    if facts.get("atLastLabel"):
        a = facts["atLastLabel"]
        lines.append(f"- At {a['label']}: largest {a['largest'][0]} {a['largest'][1]:g}, smallest {a['smallest'][0]} "
                     f"{a['smallest'][1]:g}, gap {a['gap']:g}")
    return "\n".join(lines)


def _allowed_numbers(spec: dict, facts: dict, evidence_texts: list[str]) -> set[float]:
    vals: set[float] = set()

    def add(x):
        v = _num(x)
        if v is not None:
            vals.add(round(v, 6))

    for series in spec.get("series") or []:
        for p in series.get("data") or []:
            add(p.get("value"))
            for y in re.findall(r"\d+(?:\.\d+)?", str(p.get("label") or "")):
                add(y)
    for s in facts.get("series") or []:
        for key in ("change", "changePct", "cagrPct"):
            if key in s:
                add(s[key])
                add(abs(s[key]))
    if facts.get("atLastLabel"):
        add(facts["atLastLabel"]["gap"])
    for text in evidence_texts:
        for raw in _NUM_RE.findall(text or ""):
            add(raw.replace(",", "").replace("−", "-"))
    for y in re.findall(r"\d+(?:\.\d+)?", str(spec.get("title") or "")):
        add(y)
    return vals


def _number_ok(raw: str, allowed: set[float]) -> bool:
    v = _num(raw.replace(",", "").replace("−", "-"))
    if v is None:
        return True
    if abs(v) < 10 and float(v).is_integer():
        return True            # small counts ("two series", "3 years") are wording, not data
    for a in allowed:
        tol = max(abs(a) * 0.005, 0.051 if "." in raw else 0.51)  # rounding to the stated precision
        if abs(abs(v) - abs(a)) <= tol:
            return True
    return False


def check_explanation(text: str, spec: dict, facts: dict, evidence: dict[str, str]) -> list[str]:
    """Problems that block an explanation (empty list = acceptable). ``evidence``: source id -> passage text."""
    problems = []
    allowed = _allowed_numbers(spec, facts, list(evidence.values()))
    body = _CITE_RE.sub(" ", str(text or ""))
    for raw in _NUM_RE.findall(body):
        if not _number_ok(raw, allowed):
            problems.append(f"unsupported number {raw}")
    known = {k.upper().split(".")[0] for k in evidence}
    for group in _CITE_RE.findall(text or ""):
        for cid in re.split(r"\s*[,;]\s*", group):
            if cid.strip().upper().split(".")[0] not in known:
                problems.append(f"citation {cid.strip()} is not in the supplied evidence")
    for sentence in re.split(r"(?<=[.!?])\s+", str(text or "")):
        if _CAUSAL_RE.search(sentence) and not _CITE_RE.search(sentence):
            problems.append("unsourced causal claim: " + sentence.strip()[:120])
    return sorted(set(problems))
