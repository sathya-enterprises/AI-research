"""Numbers in an attachment-only report must come from the attachment.

Web reports check every number against the cited publisher passage. Attachment-only reports skip that check
(there is no publisher page), so a number the writer invented or mis-copied would reach the PDF unchecked.
This module closes that gap with the same rule the web path uses:

  * allowed numbers = every number in the attached files' extracted text + every number in the deterministic
    attachment analytics (coverage, distributions, rankings, key stats); percentages are also accepted in
    fraction form (0.6234 <-> 62.34%) and every allowed value may be shown rounded (62.34 -> 62.3 / 62);
  * a prose sentence holding a number that is in neither is removed (and logged); tables, headings, chart
    blocks and figure captions are left alone (deterministic tables are built from the file);
  * years, small counts (<= 12) and durations ("3-year", "6 months") are not statistics.
"""
from __future__ import annotations

import re

# A comma groups digits only as thousands (1,234,567) or Indian lakhs (12,34,567); in CSV text "50,4862.15"
# is two cells, not one number.
_NUM_CORE = r"(?:\d{1,3}(?:,\d{3})+|\d{1,2}(?:,\d{2})+,\d{3}|\d+)(?:\.\d+)?(?![\d,]*\d)"
_NUM_RE = re.compile(rf"(?<![\w.,])[-+−]?{_NUM_CORE}")
_DURATION_RE = re.compile(r"(?i)\b\d{1,2}\s*[- ]?\s*(?:months?|mths?|years?|yrs?|days?|weeks?|quarters?)\b")
_SKIP_LINE_RE = re.compile(r"^\s*(?:#|\||>|!\[|\[CHART|\[\[|```|Figure \d|\*\*Figure|Table \d|\*\*Table|Source:)", re.I)
_CITE_RE = re.compile(r"\[S\d+(?:\.\d+)?(?:\s*[,;]\s*S\d+(?:\.\d+)?)*\]")


def _variants(value: float, *, percent: bool, fraction: bool) -> set[str]:
    """The value as shown, rounded to 0-2 decimals; a percentage also as its fraction and a fraction
    (|v| <= 1.5 with decimals) also as a percentage - never other rescalings (12.34 must not cover 1,234)."""
    scaled = [value]
    if percent:
        scaled.append(value / 100)
    if fraction:
        scaled.append(value * 100)
    out = set()
    for v in scaled:
        if abs(v) > 1e13:
            continue
        for digits in (0, 1, 2):
            out.add(f"{round(abs(v), digits):.{digits}f}")
    return out


_ALLOWED_RE = re.compile(rf"(?<![\w.])([-+−]?{_NUM_CORE})(\s?%)?")
_CELL_RE = re.compile(r"[(₹$€£]?\s?([-+−]?\d[\d,]*(?:\.\d+)?)\s?(%)?\)?\s*(?:[A-Za-z]{1,6})?")


def allowed_numbers(*texts: str) -> set[str]:
    """Every number in the given texts, with fraction/percent and rounding variants."""
    out: set[str] = set()

    def add(raw: str, percent: bool) -> None:
        try:
            value = float(raw.replace(",", "").replace("−", "-"))
        except ValueError:
            return
        nonlocal out
        out |= _variants(value, percent=percent, fraction="." in raw and abs(value) <= 1.5 and not percent)

    for text in texts:
        for line in str(text or "").splitlines():
            if line.count(",") >= 3 and " | " not in line:
                # a CSV row (sheet text): every cell is one value ("50,4862.15" is two cells)
                import csv
                try:
                    cells = next(csv.reader([line]))
                except Exception:
                    cells = line.split(",")
                for cell in cells:
                    m = _CELL_RE.fullmatch(cell.strip())
                    if m:
                        add(m.group(1), bool(m.group(2)))
                    else:  # a text cell may still name a number ("S&P BSE 500")
                        for inner in _ALLOWED_RE.finditer(cell):
                            add(inner.group(1), bool(inner.group(2)))
                continue
            for m in _ALLOWED_RE.finditer(line):
                add(m.group(1), bool(m.group(2)))
    return out


def _claimed(sentence: str) -> list[str]:
    plain = _DURATION_RE.sub(" ", _CITE_RE.sub(" ", sentence))
    found = []
    for raw in _NUM_RE.findall(plain):
        try:
            value = abs(float(raw.replace(",", "").replace("−", "-")))
        except ValueError:
            continue
        if "." not in raw and value.is_integer() and (value <= 12 or 1900 <= value <= 2100):
            continue  # ranks, small counts, months, years ("12.00" is a statistic, not a count)
        found.append(raw)
    return found


def _supported(raw: str, allowed: set[str]) -> bool:
    value = abs(float(raw.replace(",", "").replace("−", "-")))
    decimals = len(raw.split(".")[1]) if "." in raw else 0
    return f"{value:.{min(decimals, 2)}f}" in allowed


def _value(raw: str) -> float:
    return float(raw.replace(",", "").replace("−", "-"))


def _signed_numbers(sentence: str) -> list[float]:
    out = []
    plain = _DURATION_RE.sub(" ", _CITE_RE.sub(" ", sentence))
    for raw in _NUM_RE.findall(plain):
        try:
            out.append(_value(raw))
        except ValueError:
            continue
    return out


def _derived(raw: str, sentence: str, allowed: set[str]) -> bool:
    """A difference, sum or percentage share of two numbers in the SAME sentence that are themselves in the
    file ("Large Cap beat Small Cap by 10.46 pp (+6.89% vs -3.57%)", "237 strategies (46.8%) of 506")."""
    target = abs(_value(raw))
    decimals = len(raw.split(".")[1]) if "." in raw else 0
    tol = 0.5 * 10 ** -decimals + 1e-9
    inputs = [v for v in _signed_numbers(sentence)
              if abs(v) != target and f"{abs(v):.{min(len(str(v).split('.')[1]) if '.' in str(v) else 0, 2)}f}" in allowed]
    for i, a in enumerate(inputs):
        for b in inputs[i + 1:]:
            candidates = [abs(a - b), abs(a + b)]
            if a:
                candidates.append(abs(b / a * 100))
            if b:
                candidates.append(abs(a / b * 100))
            if any(abs(c - target) <= tol for c in candidates):
                return True
    return False


def check_attachment_numbers(report: str, allowed: set[str]) -> tuple[str, list[dict]]:
    """(report without unsupported prose sentences, removed [{sentence, numbers}])."""
    removed: list[dict] = []
    out_lines = []
    for line in str(report or "").split("\n"):
        if not line.strip() or _SKIP_LINE_RE.match(line):
            out_lines.append(line)
            continue
        kept = []
        for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z\[(\"'])", line):
            bad = [raw for raw in _claimed(sentence)
                   if not _supported(raw, allowed) and not _derived(raw, sentence, allowed)]
            if bad:
                removed.append({"sentence": sentence.strip()[:240], "numbers": bad[:6]})
                continue
            kept.append(sentence)
        joined = " ".join(kept).rstrip()
        bullet_only = re.fullmatch(r"\s*(?:[-*•]|\d+[.)])\s*", joined)
        if joined and not bullet_only:
            out_lines.append(joined)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out_lines)), removed
