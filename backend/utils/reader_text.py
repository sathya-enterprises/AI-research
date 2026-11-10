"""Stage L: reader-facing clean-up applied only when the report is rendered (HTML / PDF).

The "(calculated: ...)" label is an internal contract: the number policy verified the arithmetic and
the audits skip such numbers because of the label. Readers should not see it ("32.9% (calculated:
change from 2230 to 2964)%"). It is removed at render time only, after every gate has run.

One writer mistake is repaired here as well: a range whose lower bound was "calculated" as the
difference of the two bounds ("500 (calculated: 5500 - 5000) to 5,500 crore") - the source said
"5,000 to 5,500"; the lower bound is restored from the calculation's own second input.
"""
from __future__ import annotations

import re

_NUM = r"[-+−]?\d[\d,]*(?:\.\d+)?"
_LABEL_RE = re.compile(r"\s*\(calculated:\s*(?:[^()]|\([^()]*\))*\)", re.I)
# "500 (calculated: 5500 - 5000) to 5,500"
_RANGE_DIFF_RE = re.compile(
    rf"(?P<val>{_NUM})\s*\(calculated:\s*(?P<a>{_NUM})\s*[-−]\s*(?P<b>{_NUM})\s*\)\s*(?P<to>to|-|–)\s*(?P<hi>{_NUM})",
    re.I)


def _num(text: str) -> float | None:
    try:
        return float(text.replace(",", "").replace("−", "-"))
    except ValueError:
        return None


def _fix_range(match: re.Match) -> str:
    a, b, hi = _num(match["a"]), _num(match["b"]), _num(match["hi"])
    if a is not None and b is not None and hi is not None and abs(a - hi) < 1e-9 and b < a:
        lo = match["b"]
        if "," in match["hi"] and "," not in lo and len(lo.split(".")[0]) > 3:
            whole = lo.split(".")[0]
            lo = f"{int(whole):,}" + (("." + lo.split(".")[1]) if "." in lo else "")
        return f"{lo} {match['to']} {match['hi']}"
    return match.group(0)


def strip_calculation_labels(text: str) -> str:
    """Remove "(calculated: ...)" labels (and a unit the label pushed after itself)."""
    text = _RANGE_DIFF_RE.sub(_fix_range, str(text or ""))

    def drop(match: re.Match) -> str:
        return ""

    out = []
    pos = 0
    for m in _LABEL_RE.finditer(text):
        before = text[pos:m.start()]
        after_start = m.end()
        # "32.9% (calculated: ...)%" -> "32.9%": a unit repeated right after the label is dropped
        prev_unit = re.search(r"(%|x|crore|billion|million|bps)\s*$", before)
        nxt = re.match(r"\s*(%|x\b|crore\b|billion\b|million\b|bps\b)", text[after_start:])
        if prev_unit and nxt and prev_unit.group(1) == nxt.group(1):
            after_start += nxt.end()
        out.append(before + drop(m))
        pos = after_start
    out.append(text[pos:])
    return re.sub(r"[ \t]+([,.;:)])", r"\1", "".join(out))


def reader_text(markdown: str) -> str:
    """All render-time clean-ups (kept separate from the gates, which need the internal labels)."""
    return strip_calculation_labels(markdown)
