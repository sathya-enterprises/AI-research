"""Number policy for V2 section text (Stage C2).

Every number in the report must be one of:
  * reported   -- it appears in a passage the sentence cites (citations are
                  repaired first, see section_writer.repair_citations);
  * calculated -- it is derived from numbers in the cited passages (sum,
                  difference, share/ratio, percentage change, or a unit
                  conversion such as crore -> trillion). Such a number is
                  labelled in the text: "₹83,816 crore (calculated: 47,955 +
                  21,745 + 14,116)". No silent conversions;
  * not a statistic -- years, quarter labels, durations ("13-week",
                  "15-year average"), ordinals/percentiles ("84th") and
                  counts of items ("13 out of 15 sectors").

A sentence that still holds a number that is neither reported nor calculable
is removed, and the removal is logged and recorded; it is never hidden.
"""
from __future__ import annotations

import itertools
import logging
import re
from dataclasses import dataclass, field

log = logging.getLogger("number_policy")

CITE_RE = re.compile(r"\[(S\d+(?:\.\d+)?(?:\s*[,;]\s*S\d+(?:\.\d+)?)*)\]")
_NUM_RE = re.compile(r"(?<![\w.,\[])[-+−]?\d[\d,]*(?:\.\d+)?(?!\.\d)")
_FIG_RE = re.compile(r"\b(?:Figure|Fig\.)\s+\d+\b|\[\[\s*FIGURE\s+\d+\s*\]\]", re.I)
_PERIOD_RE = re.compile(
    r"\b(?:Q[1-4]|H[12])\s*(?:FY|CY)?\s*'?\d{2,4}(?:\s*[-/–]\s*\d{2,4})?\b|\bFY\s*'?\d{2,4}(?:\s*[-/–]\s*\d{2,4})?\b|"
    r"\b(?:19|20)\d{2}\s*[-–/]\s*\d{2,4}\b", re.I)
# Durations, ordinals and counts are not statistics to verify.
_NON_STAT_RE = re.compile(
    # (?<![\d,.]) keeps "10,915 of 13,112" or "1,036 m" from being read as a count/duration.
    r"(?i)(?<![\d,.])\b\d{1,3}\s*[- ]?\s*(?:w|wk|wks|weeks?|months?|mths?|m|years?|yrs?|y|days?|d|quarters?|qtrs?|sessions?|fortnights?)\b(?![%])"
    r"|(?<![\d,.])\b\d{1,3}(?:st|nd|rd|th)\b"
    r"|(?<![\d,.])\b\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\b"
    r"|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2}\b(?![,.]\d)"
    r"|(?<![\d,.])\b\d{1,3}\s+(?:out\s+)?of\s+(?:the\s+)?\d{1,3}\b(?![,.]\d)"
    r"|\b(?:top|bottom|first|last|all|over|across|among)\s+\d{1,2}\b(?![,.]\d)(?!\s*%)(?!\s+(?:out\s+)?of\b)(?=\s+[a-z])")
SMALL_ORDINALS = {"0", "1", "2", "3", "4", "5"}
_QUALIFIER_RE = re.compile(r"(?i)\b(?:over|more than|above|about|around|nearly|almost|approximately|approx\.?|roughly|close to|under|below|less than|some)\s*(?:₹|rs\.?|inr|usd|\$)?\s*$")
_UNIT_FACTORS = {  # value_in_text * factor == value_in_passage
    "crore->trillion": 1e5, "crore->billion": 1e2, "crore->million": 1e-1, "lakh crore->crore": 1e-5, "million->billion": 1e3,
    "crore->lakh crore": 1e5, "billion->million": 1e-3, "%->bps": 1e-2, "bps->%": 1e2, "ratio->%": 1e-2,
}


def normalize(token: str) -> str:
    raw = token.replace("−", "-").replace(",", "").lstrip("+")
    try:
        value = float(raw)
    except ValueError:
        return raw
    return f"{value:.4f}".rstrip("0").rstrip(".")


def number_set(texts) -> set[str]:
    out = set()
    for text in texts:
        for token in _NUM_RE.findall(str(text or "")):
            out.add(normalize(token))
            out.add(normalize(token.lstrip("-−")))
    return out


def statistic_tokens(sentence: str) -> list[str]:
    """Numbers in a sentence that are statistics (excludes citations, figure refs, periods, durations, counts)."""
    text = CITE_RE.sub(" ", sentence)
    text = _FIG_RE.sub(" ", text)
    text = _PERIOD_RE.sub(" ", text)
    text = _NON_STAT_RE.sub(" ", text)
    out = []
    for token in _NUM_RE.findall(text):
        plain = normalize(token).lstrip("-")
        if re.fullmatch(r"(?:19|20)\d{2}", plain) or plain in SMALL_ORDINALS:
            continue
        out.append(token)
    return out


def _decimals(token: str) -> int:
    raw = token.replace(",", "")
    return len(raw.split(".")[1]) if "." in raw else 0


def _float(token: str) -> float | None:
    try:
        return float(token.replace("−", "-").replace(",", "").lstrip("+"))
    except ValueError:
        return None


def _close(derived: float, token: str, qualifier: str = "", *, last_digit: bool = False) -> bool:
    """Does ``derived`` reproduce ``token`` at the precision it is written with?

    ``qualifier`` is the word before the number: "over 83,000" accepts 83,816 (at or
    above, within the rounding step), "about 83,000" accepts +/- half a step.
    ``last_digit`` also accepts truncation in the last decimal (65,25,808 crore ->
    "65.25 trillion"), used for labelled unit conversions only.
    """
    value = _float(token)
    if value is None:
        return False
    value, derived = abs(value), abs(derived)
    decimals = _decimals(token)
    unit = 10 ** (-decimals)
    if abs(derived - value) <= 0.5 * unit + 1e-9 or (last_digit and 0 <= derived - value < unit):
        return True
    if qualifier and decimals == 0:
        digits = token.replace(",", "").lstrip("+-−")
        zeros = len(digits) - len(digits.rstrip("0"))
        if zeros >= 2:
            step = 10 ** zeros
            if qualifier == "above":
                return 0 <= derived - value < step
            if qualifier == "below":
                return 0 <= value - derived < step
            return abs(derived - value) <= step / 2
    return False


def qualifier_before(text: str) -> str:
    match = _QUALIFIER_RE.search(text[-30:])
    if not match:
        return ""
    word = match.group(0).strip().casefold()
    if re.match(r"(?:over|more than|above)", word):
        return "above"
    if re.match(r"(?:under|below|less than|almost)", word):
        return "below"
    return "about"


@dataclass
class PoolNumber:
    value: float
    raw: str
    passage_id: str
    position: int
    line: int = 0
    pct: bool = False


def number_pool(passages: dict[str, str]) -> list[PoolNumber]:
    pool = []
    for pid, text in passages.items():
        text = str(text or "")
        for match in _NUM_RE.finditer(text):
            value = _float(match.group(0))
            if value is None or re.fullmatch(r"(?:19|20)\d{2}", match.group(0)):
                continue
            pct = bool(re.match(r"\s*%", text[match.end():match.end() + 3]))
            pool.append(PoolNumber(value, match.group(0).lstrip("+"), pid, match.start(), text.count("\n", 0, match.start()), pct))
    return pool


def _fmt(item: PoolNumber) -> str:
    raw = item.raw.lstrip("+")
    raw = re.sub(r"(\.\d*?)0+$", r"\1", raw).rstrip(".") if "." in raw else raw
    return raw + ("%" if item.pct else "")


def find_conversion(token: str, pool: list[PoolNumber], *, qualifier: str = "", unit: str = "") -> str:
    """A labelled unit conversion of one pool number; the text's unit word must be the target unit."""
    target = _float(token)
    if target is None or not unit:
        return ""
    unit = unit.casefold().strip()
    unit = {"cr": "crore", "bn": "billion", "mn": "million", "tn": "trillion"}.get(unit, unit)
    for item in pool:
        if abs(item.value) < 1 or item.pct:
            continue
        for name, factor in _UNIT_FACTORS.items():
            src, dst = name.split("->")
            if src in {"%", "bps", "ratio"} or dst != unit:
                continue
            if _close(item.value / factor, token, qualifier, last_digit=True):
                return f"converted from {_fmt(item)} {src}"
    return ""


def find_derivation(token: str, pool: list[PoolNumber], *, qualifier: str = "", sentence_pool: list[PoolNumber] = ()) -> str:
    """A short derivation ("47,955 + 21,745 + 14,116") of ``token``, or ''.

    Inputs and operations are restricted so chance matches do not label an
    invented number as "calculated":
      * a unit conversion of one number in a cited passage (crore -> trillion ...);
      * any of sum / difference / share / percentage change of two numbers the
        writer's own sentence states;
      * a difference or sum of two numbers on the same line of one cited passage,
        both percentages or both amounts;
      * a sum of three same-sign amounts from one cited passage (values with at
        least four digits only).
    """
    target = _float(token)
    if target is None:
        return ""
    sentence_pool = [p for p in sentence_pool if normalize(p.raw) != normalize(token)]
    for a, b in itertools.permutations(sentence_pool, 2):
        if _close(a.value + b.value, token, qualifier) and a.position < b.position:
            return f"{_fmt(a)} + {_fmt(b)}"
        if _close(a.value - b.value, token, qualifier):
            return f"{_fmt(a)} − {_fmt(b)}"
        if b.value and abs(a.value) < abs(b.value) and a.value * b.value > 0 and _close(100 * a.value / b.value, token, qualifier):
            return f"{_fmt(a)} ÷ {_fmt(b)}"
        if b.value and _close(100 * (a.value - b.value) / abs(b.value), token, qualifier):
            return f"change from {_fmt(b)} to {_fmt(a)}"
    for a, b in itertools.permutations(pool[:600], 2):
        if a.passage_id != b.passage_id or a.line != b.line or a.pct != b.pct or a.raw == b.raw:
            continue
        if _close(a.value - b.value, token, qualifier):
            return f"{_fmt(a)} − {_fmt(b)}"
        if a.position < b.position and _close(a.value + b.value, token, qualifier):
            return f"{_fmt(a)} + {_fmt(b)}"
    digits = len(re.sub(r"\D", "", token.split(".")[0]))
    if digits >= 4:
        by_passage: dict[str, list[PoolNumber]] = {}
        for item in pool:
            if abs(item.value) >= 1 and not item.pct:
                by_passage.setdefault(item.passage_id, []).append(item)
        for items in by_passage.values():
            for a, b, c in itertools.combinations(items[:60], 3):
                if not (a.value > 0) == (b.value > 0) == (c.value > 0):
                    continue
                if min(abs(a.value), abs(b.value), abs(c.value)) < 0.05 * abs(target):
                    continue
                if _close(a.value + b.value + c.value, token, qualifier):
                    return f"{_fmt(a).lstrip('-−')} + {_fmt(b).lstrip('-−')} + {_fmt(c).lstrip('-−')}"
    return ""


@dataclass
class PolicyResult:
    body: str
    calculated: list[dict] = field(default_factory=list)
    removed: list[dict] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)


_TABLE_LINE_RE = re.compile(r"^\s*\|")
_HEADING_RE = re.compile(r"^\s*#{1,6}\s")


def _sentences(paragraph: str) -> list[str]:
    return re.split(r"(?<=[.!?])(\s+)(?=[A-Z*\[(\"'₹$])", paragraph)


def apply_number_policy(body: str, *, passage_texts: dict[str, str], allowed_numbers: set[str],
                        section_id: str = "") -> PolicyResult:
    """Label calculated numbers, remove sentences whose numbers the evidence does not support."""
    result = PolicyResult(body=body)
    out_lines = []
    for line in body.split("\n"):
        if not line.strip() or _TABLE_LINE_RE.match(line) or _HEADING_RE.match(line) or _FIG_RE.fullmatch(line.strip()):
            # A table cell can only be a reported value: a row with a value the evidence does
            # not contain is removed (logged and recorded), like an unsupported sentence.
            if _TABLE_LINE_RE.match(line) and not re.fullmatch(r"\s*\|[\s:|-]+\|?\s*", line):
                bad = [t for t in statistic_tokens(line)
                       if normalize(t) not in allowed_numbers and normalize(t).lstrip("-") not in allowed_numbers]
                if bad:
                    result.removed.append({"section": section_id, "number": bad[0], "sentence": "table row: " + line.strip()[:220]})
                    log.info("V2 number policy removed a table row in %s: value(s) %s not found in the evidence",
                             section_id or "?", ", ".join(bad)[:120])
                    continue
            out_lines.append(line)
            continue
        pieces = _sentences(line)
        kept: list[str] = []
        for piece in pieces:
            if not piece.strip():
                kept.append(piece)
                continue
            sentence = piece
            cited = [t.strip().upper() for g in CITE_RE.findall(sentence) for t in re.split(r"\s*[,;]\s*", g)]
            cited_texts = {pid: passage_texts[pid] for pid in cited if pid in passage_texts}
            if not cited_texts:
                cited_texts = {pid: text for pid, text in passage_texts.items()
                               if any(pid.split(".")[0] == c.split(".")[0] for c in cited)}
            pool = number_pool(cited_texts) if cited_texts else []
            # An uncited sentence may still state a labelled unit conversion of a number the
            # section was given (2,645 crore -> "₹26,450 million"); only conversions are searched.
            conversion_pool = pool or number_pool(passage_texts)
            tokens = statistic_tokens(sentence)
            sentence_pool = [n for n in number_pool({"sentence": CITE_RE.sub(" ", sentence)})
                             if normalize(n.raw).lstrip("-") in {normalize(t).lstrip("-") for t in tokens}]
            unsupported = [t for t in tokens if normalize(t) not in allowed_numbers and normalize(t).lstrip("-") not in allowed_numbers]
            drop = False
            for token in unsupported:
                start = sentence.find(token)
                qualifier = qualifier_before(sentence[:max(0, start)]) if start >= 0 else ""
                unit_word = re.match(r"\s*(lakh crore|crore|cr|trillion|billion|million|bn|mn|tn)\b",
                                     sentence[start + len(token):] if start >= 0 else "", re.I)
                unit_word = unit_word.group(1) if unit_word else ""
                derivation = (find_conversion(token, pool or conversion_pool, qualifier=qualifier, unit=unit_word)
                              or find_derivation(token, pool, qualifier=qualifier, sentence_pool=sentence_pool))
                if derivation:
                    label = f" (calculated: {derivation})"
                    end = start + len(token)
                    # Keep the unit with the number: "₹83,816 crore (calculated: ...)".
                    unit = re.match(r"\s*(?:%|x|bps|pp|percentage points?|(?:lakh\s+)?crore|cr|trillion|billion|million|bn|mn|tn)\b|\s*%", sentence[end:], re.I)
                    if unit:
                        end += unit.end()
                    if "calculated" not in sentence[end:end + 15]:
                        sentence = sentence[:end] + label + sentence[end:]
                    result.calculated.append({"section": section_id, "number": token, "inputs": derivation})
                else:
                    drop = True
                    result.removed.append({"section": section_id, "number": token,
                                           "sentence": re.sub(r"\s+", " ", CITE_RE.sub("", piece)).strip()[:240]})
            if drop:
                log.info("V2 number policy removed a sentence in %s: number(s) %s not found in the cited evidence",
                         section_id or "?", ", ".join(t for t in unsupported)[:120])
                continue
            kept.append(sentence)
        new_line = "".join(kept).strip()
        if re.fullmatch(r"\s*(?:[-*+]|\d+\.)\s*(?:\*\*[^*]*\*\*:?)?\s*", new_line or ""):
            new_line = ""  # a bullet whose only sentence was removed
        if new_line or not line.strip():
            out_lines.append(new_line if line.strip() else line)
    result.body = re.sub(r"\n{3,}", "\n\n", "\n".join(out_lines)).strip()
    return result
