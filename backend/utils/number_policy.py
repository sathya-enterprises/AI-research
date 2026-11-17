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
from utils.evidence_text import SENTENCE_END  # one sentence-boundary rule for every checker

log = logging.getLogger("number_policy")

CITE_RE = re.compile(r"\[(S\d+(?:\.\d+)?(?:\s*[,;]\s*S\d+(?:\.\d+)?)*)\]")
# "Rs.10.38" (no space after "Rs.") is a number too: the "." before it is the abbreviation's, not a decimal
# point (live run, telecom: every "Rs.176.59" in a passage was invisible to the checks).
_NUM_RE = re.compile(r"(?:(?<![\w.,\[])|(?<=\bRs\.)|(?<=\bRe\.))[-+−]?\d[\d,]*(?:\.\d+)?(?!\.\d)")
_FIG_RE = re.compile(r"\b(?:Figure|Fig\.)\s+\d+\b|\[\[\s*FIGURE\s+\d+\s*\]\]", re.I)
_PERIOD_RE = re.compile(
    r"\b(?:Q[1-4]|H[12])\s*(?:FY|CY)?\s*'?\d{2,4}(?:\s*[-/–]\s*\d{2,4})?\b|\bFY\s*'?\d{2,4}(?:\s*[-/–]\s*\d{2,4})?\b|"
    r"\b(?:19|20)\d{2}\s*[-–/]\s*\d{2,4}\b", re.I)
# Durations, ordinals and counts are not statistics to verify.
_NON_STAT_RE = re.compile(
    # (?<![\d,.]) keeps "10,915 of 13,112" or "1,036 m" from being read as a count/duration.
    r"(?i)(?<![\d,.])\b\d{1,3}\s*[- ]?\s*(?:w|wk|wks|weeks?|months?|mths?|m|years?|yrs?|y|days?|d|quarters?|qtrs?|sessions?|fortnights?)\b(?![%])"
    # A rate's denominator ("34 per 100,000 live births", "per 1,000 population") is not a statistic.
    r"|\bper\s+(?:1|10|100|1,000|1000|10,000|10000|100,000|100000|1,00,000)\b(?![\d,.])"
    r"|(?<![\d,.])\b\d{1,3}(?:st|nd|rd|th)\b"
    r"|(?<![\d,.])\b\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\b"
    r"|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2}\b(?![,.]\d)"
    r"|(?<![\d,.])\b\d{1,3}\s+(?:out\s+)?of\s+(?:the\s+)?\d{1,3}\b(?![,.]\d)"
    r"|\b(?:top|bottom|first|last|all|over|across|among)\s+\d{1,2}\b(?![,.]\d)(?!\s*%)(?!\s+(?:out\s+)?of\b)(?=\s+[a-z])"
    # Index names and ranges are names, not statistics: "Nifty 50", "Nifty Midcap 150", "Services 25/50",
    # "12-18 months".
    r"|\b(?:nifty|sensex|bse|s&p\s+bse)(?:\s+(?:next|midcap|smallcap|microcap|largemidcap|total\s+market|india))?\s+\d{2,3}\b(?![,.]\d|\s*%)"
    r"|\b\d{1,3}\s*/\s*\d{1,3}\b(?![,.]\d|\s*%)"
    # A number joined to a word by a hyphen names something ("National Highway-539", "COVID-19", "NH-48"), and
    # so does a list of route numbers after it ("National Highway-40, 716, and 727A"): live EV run removed
    # sentences for "539 is not in the evidence".
    r"|(?<=[A-Za-z]-)\d{1,4}[A-Z]?\b(?![.,]\d|\s*%)"
    r"|\b(?:highways?|NH|SH|routes?|lines?)[-\s]+\d{1,4}[A-Z]?(?:\s*,\s*(?:and\s+)?\d{1,4}[A-Z]?\b)+(?![.,]\d|\s*%)"
    r"|(?<![\d,.])\b\d{1,3}\s*[-–]\s*\d{1,3}\s*(?:weeks?|months?|years?|days?|quarters?)\b")
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


_WELL_FORMED_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d{1,2}(?:,\d{2})*,\d{3}(?:\.\d+)?|\d+(?:\.\d+)?")


def well_formed_number(token: str) -> bool:
    """International (1,500,000) or Indian (15,00,000) digit grouping, or no grouping at all."""
    digits = re.sub(r"^[-+−–]\s*", "", str(token or "").strip()).rstrip(",.%")
    return bool(_WELL_FORMED_RE.fullmatch(digits)) if re.match(r"\d", digits) else True


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


def _is_name_number(sentence: str, start: int, end: int, unit: str, passage_texts: dict, cited_ids: set) -> bool:
    """A unitless whole number that the evidence writes right after the same word, as part of a name.

    Strategy and product names carry numbers ("ALPHABOTS MIDCAP 29", "ALPHA 100", "Nifty 50"); checked as
    statistics they were "not in the evidence" and the sentence naming the strategy was removed. The test
    is verbatim: "<word> <number>" must occur in the evidence text itself.
    """
    token = sentence[start:end]
    if unit or "." in token or "," in token or token[:1] in "+-−":
        return False
    previous = re.search(r"([A-Za-z][A-Za-z&'-]{1,})\s+$", sentence[:start])
    if not previous or re.match(r"\s*%", sentence[end:]):
        return False
    phrase = re.compile(r"(?<![A-Za-z])" + re.escape(previous.group(1)) + r"\s+" + re.escape(token) + r"(?!\d)", re.I)
    texts = [passage_texts[pid] for pid in (cited_ids or passage_texts) if pid in passage_texts]
    return any(phrase.search(text or "") for text in texts)


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
    unit: str = ""
    scale: float = 1.0  # "10 lakh" in the passage is the count 1,000,000 (scale 1e5)
    year_like: bool = False  # 1900-2100 with no grouping: maybe a year, maybe a value ("₹2040")


_UNIT_WORD_RE = re.compile(r"\s*(%|per\s?cent|lakh\s+crores?|lakh\s+crs?\b|crores?|crs?\b|trillion|tn\b|billion|bn\b|million|mn\b|bps\b|x(?![A-Za-z])|times\b)", re.I)


def unit_family(after: str) -> str:
    """The unit written right after a number: '%', 'lakh crore', 'crore', 'trillion', 'billion', 'million' or ''."""
    match = _UNIT_WORD_RE.match(after or "")
    if not match:
        return ""
    word = re.sub(r"\s+", " ", match.group(1).casefold())
    if word in {"%", "percent", "per cent"}:
        return "%"
    if word.startswith("lakh"):
        return "lakh crore"
    if word.startswith("cr"):
        return "crore"
    return {"tn": "trillion", "bn": "billion", "mn": "million"}.get(word, word)


def number_pool(passages: dict[str, str]) -> list[PoolNumber]:
    pool = []
    for pid, text in passages.items():
        text = str(text or "")
        for match in _NUM_RE.finditer(text):
            value = _float(match.group(0))
            if value is None:
                continue
            year_like = bool(re.fullmatch(r"(?:19|20)\d{2}", match.group(0)))
            pct = bool(re.match(r"\s*%", text[match.end():match.end() + 3]))
            scale_word = re.match(r"\s*(lakh\s+crores?|lakhs?|crores?|million|billion|thousand)\b",
                                  text[match.end():match.end() + 16], re.I)
            scale = {"lakh": 1e5, "lakhs": 1e5, "crore": 1e7, "crores": 1e7, "million": 1e6, "billion": 1e9,
                     "thousand": 1e3}.get(re.sub(r"\s+", " ", scale_word.group(1).casefold()), 1.0) if scale_word else 1.0
            pool.append(PoolNumber(value, match.group(0).lstrip("+"), pid, match.start(), text.count("\n", 0, match.start()),
                                   pct, unit_family(text[match.end():match.end() + 16]), scale, year_like))
    return pool


def _fmt(item: PoolNumber) -> str:
    raw = item.raw.lstrip("+")
    raw = re.sub(r"(\.\d*?)0+$", r"\1", raw).rstrip(".") if "." in raw else raw
    return raw + ("%" if item.pct else "")


def find_conversion(token: str, pool: list[PoolNumber], *, qualifier: str = "", unit: str = "") -> str:
    """A labelled unit conversion of one pool number; the text's unit word must be the target unit."""
    pool = [item for item in pool if not item.year_like]  # possible years are never converted
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
    pool = [item for item in pool if not item.year_like]  # possible years never take part in a guess
    sentence_pool = [p for p in sentence_pool if normalize(p.raw) != normalize(token) and not p.year_like]
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


_CALC_LABEL_RE = re.compile(r"\(calculated(?: from| using)?\s*:?\s*((?:[^()]|\([^()]*\))*)\)", re.I)


def _signed_sum(body: str) -> float | None:
    """'29.90 - (-0.73)' -> 30.63; None unless the label is only additions / subtractions of numbers."""
    text = re.sub(r"\(\s*([-−+]?\s*\d[\d,]*(?:\.\d+)?)\s*\)", r"\1", body.strip())
    text = re.sub(r"(?<=\d)\s*%", "", text)
    parts = re.split(r"\s+([+\-−])\s+", text)
    if len(parts) < 3:
        return None
    try:
        values = [float(p.replace(",", "").replace("−", "-").replace(" ", "")) for p in parts[0::2]]
    except ValueError:
        return None
    total = values[0]
    for op, value in zip(parts[1::2], values[1:]):
        total = total + value if op == "+" else total - value
    return total


_SCALE_AFTER_RE = re.compile(r"[-+−]?\d[\d,]*(?:\.\d+)?\s*(trillion|tn|t|billion|bn|b|million|mn|m|lakh\s+crores?|"
                             r"crores?|cr|lakhs?|thousand|k)?\b", re.I)
_SCALES = {"trillion": 1e12, "tn": 1e12, "t": 1e12, "billion": 1e9, "bn": 1e9, "b": 1e9, "million": 1e6,
           "mn": 1e6, "m": 1e6, "crore": 1e7, "crores": 1e7, "cr": 1e7, "lakh": 1e5, "lakhs": 1e5,
           "thousand": 1e3, "k": 1e3}


def _scaled_inputs(body: str) -> list[float]:
    """The inputs of a calculation with their scale words applied ("12.12 trillion" -> 1.212e13)."""
    out = []
    for m in _SCALE_AFTER_RE.finditer(body):
        number = re.match(r"[-+−]?\d[\d,]*(?:\.\d+)?", m.group(0)).group(0)
        word = re.sub(r"\s+", " ", (m.group(1) or "").casefold())
        scale = 1e12 if word.startswith("lakh crore") else _SCALES.get(word, 1.0)
        out.append(abs(_float(number) or 0.0) * scale)
    return out


# "62.0% (calculated: 62 / 100)", "2.7 (calculated: 2.7 * 1)": a quoted value labelled as a calculation.
# The label is dropped and the value is checked like any other number.
_RESTATING_CALC_RE = re.compile(
    r"(?P<value>\d[\d,]*(?:\.\d+)?)(?P<unit>\s*(?:%|per\s?cent|times|x)?)\s*"
    r"\(calculated:\s*(?P<input>\d[\d,]*(?:\.\d+)?)\s*%?\s*(?:/\s*100|[*×x]\s*1)\s*\)", re.I)


def drop_restating_calculations(text: str) -> str:
    def sub(m: re.Match) -> str:
        if normalize(m.group("value")) == normalize(m.group("input")):
            return m.group("value") + m.group("unit")
        return m.group(0)
    return _RESTATING_CALC_RE.sub(sub, text)


def verify_calculation_labels(sentence: str, pool: list[PoolNumber]) -> str:
    """'' when every "(calculated: ...)" label in the sentence is verified, else the reason.

    Each input must appear in a cited passage, and the stated operation on those
    inputs must reproduce the number the label follows. A label whose inputs are only
    in the writer's own sentence, or whose arithmetic does not hold, fails.
    """
    pool_values = {normalize(n.raw).lstrip("-") for n in pool if not n.year_like}
    # A count written with a scale word ("10 lakh") is also the plain input (1000000).
    pool_values |= {normalize(f"{abs(n.value) * n.scale:.4f}") for n in pool if n.scale != 1.0 and not n.year_like}
    # A value that looks like a year ("MSP of ₹2040 per quintal") may be a named input; it is never used
    # to guess a derivation, but an explicit calculation may cite it.
    year_like_values = {normalize(n.raw).lstrip("-") for n in pool if n.year_like}
    for match in _CALC_LABEL_RE.finditer(sentence):
        body = match.group(1)
        before = re.findall(r"[-+−]?\d[\d,]*(?:\.\d+)?", CITE_RE.sub(" ", sentence[:match.start()]))
        if not before:
            return "calculated label without a number"
        target = before[-1]
        conversion = re.match(r"\s*converted from\s+([-+−]?\d[\d,]*(?:\.\d+)?)\s*(.*)$", body, re.I)
        if conversion:
            if normalize(conversion.group(1)).lstrip("-") not in pool_values:
                return f"conversion input {conversion.group(1)} is not in the cited passages"
            continue
        # "× 100" turns a ratio into a percent: a constant, not a fact to find in a passage (removed
        # sentences in the healthcare, insurance and inflation benchmark prompts).
        body = re.sub(r"\s*[*×x]\s*100\s*%?\s*$", "", body.strip())
        # "(560.4 - 268.8) / 268.8" is the percent change from 268.8 to 560.4.
        growth = re.fullmatch(r"\(\s*([-+−]?\d[\d,]*(?:\.\d+)?)\s*[-−]\s*([-+−]?\d[\d,]*(?:\.\d+)?)\s*\)\s*[/÷]\s*"
                              r"([-+−]?\d[\d,]*(?:\.\d+)?)", body)
        if growth and normalize(growth.group(2)) == normalize(growth.group(3)):
            body = f"change from {growth.group(2)} to {growth.group(1)}"
        inputs = re.findall(r"[-+−]?\d[\d,]*(?:\.\d+)?", body)
        if len(inputs) < 2:
            return "calculated label without its inputs"
        # "100% - 76%" (the rest of a whole) and "0% - 1.8%": 100 and 0 are constants, not facts.
        constants = {"100", "0"} if "%" in body else set()
        missing = [x for x in inputs
                   if normalize(x).lstrip("-") not in pool_values | year_like_values | constants]
        if missing:
            return f"calculation input(s) {', '.join(missing)} not in the cited passages"
        values = [abs(_float(x) or 0.0) for x in inputs]
        signed_sum = _signed_sum(body)
        if signed_sum is not None:
            # "29.90 - (-0.73)" is 30.63: sums and differences keep their signs (the calculation engine
            # computes them that way; this check used absolute values and rejected its correct result).
            qualifier = qualifier_before(sentence[:sentence.find(target)]) if target in sentence else ""
            if not _close(signed_sum, target, qualifier):
                return f"calculation does not reproduce {target}"
            continue
        if "÷" in body or "/" in body:
            # Scale words count: "₹12.12 trillion / 7.31 billion" is ₹1,658 a transaction and
            # "$217B / $1.20T" is 18.1% (both were rejected when the scales were dropped).
            scaled = _scaled_inputs(body)
            if len(scaled) == len(values):
                values = scaled
            qualifier = qualifier_before(sentence[:sentence.find(target)]) if target in sentence else ""
            ratio = values[0] / values[1] if values[1] else float("nan")
            if _close(100 * ratio, target, qualifier) or _close(ratio, target, qualifier):
                continue
            return f"calculation does not reproduce {target}"
        elif "change from" in body.casefold():
            derived = 100 * (values[1] - values[0]) / values[0] if values[0] else float("nan")
        elif "−" in body or " - " in body:
            derived = values[0] - values[1]
        elif "+" in body:
            derived = sum(values)
        else:
            return "calculated label without a recognisable operation"
        qualifier = qualifier_before(sentence[:sentence.find(target)]) if target in sentence else ""
        if not _close(derived, target, qualifier):
            return f"calculation does not reproduce {target}"
    return ""


@dataclass
class PolicyResult:
    body: str
    calculated: list[dict] = field(default_factory=list)
    removed: list[dict] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    rewritten: list[dict] = field(default_factory=list)


_TABLE_LINE_RE = re.compile(r"^\s*\|")
_HEADING_RE = re.compile(r"^\s*#{1,6}\s")


def _sentences(paragraph: str) -> list[str]:
    return re.split(SENTENCE_END + r"(\s+)(?=[A-Z*\[(\"'₹$])", paragraph)


def _paragraph_names(paragraph: str, entity: str) -> bool:
    """The paragraph names ``entity`` (or one of its sector aliases)."""
    if not entity:
        return False
    try:
        from utils.chart_titles import _SECTOR_ALIAS_RE
        pattern = _SECTOR_ALIAS_RE.get(entity)
        if pattern is not None and pattern.search(paragraph or ""):
            return True
    except Exception:
        pass
    return bool(re.search(rf"(?i)(?<![A-Za-z]){re.escape(entity)}(?![A-Za-z])", paragraph or ""))


def apply_number_policy(body: str, *, passage_texts: dict[str, str], allowed_numbers: set[str],
                        section_id: str = "", context_entity: str = "", fallback_sheet=None) -> PolicyResult:
    """Number check = lookup in the section's fact sheet (value + unit + explicit sign + metric/entity).

    Per number in a sentence:
      * supported fact -> kept;
      * right value whose sentence does not name its entity -> rewritten ("-8.5% for Realty");
      * the passage's value written in another unit -> rewritten back to the passage's form;
      * "[[calc: ...]]" requests -> computed in code from facts (utils.fact_sheet.apply_calculations);
      * anything else (absent, other metric/entity, contradicted sign, unverifiable calculation)
        -> the sentence is removed, with a log line.
    Derived numbers are never guessed from coincidental pairs of passage numbers.
    """
    from types import SimpleNamespace

    from utils.fact_sheet import FactSheet, apply_calculations, lookup

    sheet = FactSheet([SimpleNamespace(passage_id=pid, text=text) for pid, text in passage_texts.items()])
    passage_texts = dict(passage_texts)  # the store fallback may add a cited passage; never mutate the caller's
    result = PolicyResult(body=body)
    result.rewritten = []
    body = drop_restating_calculations(body)
    body, calcs, failed_calcs = apply_calculations(body, sheet, None)
    result.calculated += [{"section": section_id, "number": c["value"], "inputs": c["inputs"]} for c in calcs]
    out_lines = []
    # The line that introduces a bullet list ("Goods Vehicles registrations show:") names
    # the subject of bullets such as "- 2018: 8,86,047 total (658 EVs)".
    lead_in = ""
    for line in body.split("\n"):
        is_bullet = bool(re.match(r"\s*(?:[-*+]|\d+\.)\s", line))
        if line.strip() and not is_bullet and not _TABLE_LINE_RE.match(line) and not _FIG_RE.fullmatch(line.strip()):
            current_lead_in = lead_in
            lead_in = line
        else:
            current_lead_in = lead_in
        if not line.strip() or _TABLE_LINE_RE.match(line) or _HEADING_RE.match(line) or _FIG_RE.fullmatch(line.strip()):
            # A table cell can only be a reported value: a row with a value the evidence does
            # not contain is removed (logged and recorded), like an unsupported sentence.
            if _TABLE_LINE_RE.match(line) and not re.fullmatch(r"\s*\|[\s:|-]+\|?\s*", line):
                # A labelled calculation in a cell is checked through its inputs and arithmetic.
                calc_reason = verify_calculation_labels(line, number_pool(passage_texts)) if "(calculated" in line else ""
                plain_cells = re.sub(r"[-+−]?\d[\d,]*(?:\.\d+)?[^()\d\n|]{0,30}\(calculated(?:[^()]|\([^()]*\))*\)", " ", line)
                bad = [t for t in statistic_tokens(plain_cells) if not sheet.candidates(t.rstrip(",."))]
                bad += [calc_reason.split(" ")[-1]] if calc_reason else []
                if bad:
                    result.removed.append({"section": section_id, "number": bad[0],
                                           "reason": f"table row: {', '.join(bad)[:80]} not in the evidence",
                                           "sentence": "table row: " + line.strip()[:220]})
                    log.info("V2 number policy removed a table row in %s: value(s) %s not found in the evidence",
                             section_id or "?", ", ".join(bad)[:120])
                    continue
            out_lines.append(line)
            continue
        kept: list[str] = []
        previous_sentence = ""
        for piece in _sentences(line):
            if not piece.strip():
                kept.append(piece)
                continue
            sentence = piece
            # A sentence that refers back ("This metric rose from 0.82% ...") takes its subject
            # from the sentence before it; a bullet's first sentence from the list's lead-in.
            # A sentence with its own subject ("Cars sold reached 77 lakh") does not borrow one.
            refers_back = bool(re.match(
                r"(?i)\s*(?:[-*+]\s*)?(?:this|these|that|those|it|its|they|their|"
                r"the\s+(?:metric|figure|share|number|ratio|rate|total|count|value|segment))\b", piece))
            subject_context = (previous_sentence if refers_back else "") or (
                current_lead_in if is_bullet and not previous_sentence else "")
            previous_sentence = piece
            reason = ""
            if "[[UNSUPPORTED_CALC]]" in sentence:
                reason = "requested calculation uses numbers that are not facts in the evidence"
            if "[[UNKNOWN_FACT]]" in sentence:
                reason = "refers to a fact id that is not on the section's fact sheet"
            if "[[PERIOD_FLIP]]" in sentence:
                reason = "dates a value to another year than its article (a month named without a year)"
            if "[[BOUND_FLIP]]" in sentence:
                reason = "bounds a value the opposite way from its passage (e.g. 'exceeding' for 'not exceeding')"
            cited = [t.strip().upper() for g in CITE_RE.findall(sentence) for t in re.split(r"\s*[,;]\s*", g)]
            cited_ids = {pid for pid in passage_texts if pid in cited or any(pid.split(".")[0] == c for c in cited)}
            tokens = statistic_tokens(re.sub(r"\(calculated(?:[^()]|\([^()]*\))*\)", " ", sentence))
            named_entity = False
            for token in ([] if reason else tokens):
                start = sentence.find(token)
                if start < 0:
                    continue
                end = start + len(token)
                if sentence[end:end + 1] == "⁠":
                    continue  # inserted by the code from a fact id ([[F3]]): the exact cell, already verified
                explicit = "-" if token[:1] in "-−" else ("+" if token[:1] == "+" else "")
                unit = unit_family(sentence[end:end + 16])
                if re.match(r"[^()\d\n]{0,30}\(calculated", sentence[end:]):
                    continue  # a labelled calculation: checked below through its inputs and arithmetic
                if _is_name_number(sentence, start, end, unit, passage_texts, cited_ids):
                    continue  # part of a name ("ALPHABOTS MIDCAP 29", "ALPHA 100"), not a statistic
                if not well_formed_number(token):
                    # "INR 5,00,00" (a live writer typo for 5,00,000) is neither international nor Indian
                    # digit grouping: no reader can tell which value was meant.
                    reason = f"{token.rstrip(',.')} is not a well-formed number"
                    break
                found = lookup(sheet, token, sign=explicit, unit=unit, sentence=CITE_RE.sub(" ", sentence),
                               passage_ids=cited_ids or None, context_entity=context_entity)
                if found.status == "ok":
                    continue
                if found.status == "metric_mismatch" and subject_context:
                    nearby = lookup(sheet, token, sign=explicit, unit=unit,
                                    sentence=CITE_RE.sub(" ", f"{subject_context} {sentence}"),
                                    passage_ids=cited_ids or None, context_entity=context_entity)
                    if nearby.status == "ok":
                        continue
                if cited_ids and found.status in {"absent", "metric_mismatch"}:
                    # The fact is in another passage of this section's evidence: correct the citation.
                    other = lookup(sheet, token, sign=explicit, unit=unit, sentence=CITE_RE.sub(" ", sentence),
                                   passage_ids=None, context_entity=context_entity)
                    if other.status == "ok":
                        right = other.fact.passage_id
                        sentence = CITE_RE.sub(lambda m: f"[{m.group(1)}, {right}]", sentence, count=1)
                        cited_ids = cited_ids | {right}
                        result.rewritten.append({"section": section_id, "number": token,
                                                 "change": f"corrected citation to {right} (the passage holding the value)"})
                        continue
                    found = other if other.status != "absent" else found
                if fallback_sheet is not None and found.status in {"absent", "metric_mismatch", "unit_mismatch"}:
                    # The writer can quote a value it saw in a figure brief or another section's summary. The
                    # whole evidence store is verified evidence: keep the sentence when the value is a fact there
                    # with this sentence's entity and metric, and cite the passage that holds it.
                    wide = lookup(fallback_sheet, token, sign=explicit, unit=unit, sentence=CITE_RE.sub(" ", sentence),
                                  passage_ids=None, context_entity=context_entity)
                    if wide.status == "ok":
                        right = wide.fact.passage_id
                        passage_texts[right] = getattr(fallback_sheet, "texts", {}).get(right, "")
                        if CITE_RE.search(sentence):
                            sentence = CITE_RE.sub(lambda m: f"[{m.group(1)}, {right}]", sentence, count=1)
                        else:
                            sentence = re.sub(r"\s*([.!?])?\s*$", lambda m: f" [{right}]{m.group(1) or ''}", sentence, count=1)
                        cited_ids = cited_ids | {right}
                        result.rewritten.append({"section": section_id, "number": token,
                                                 "change": f"cited {right}, the evidence passage holding the value"})
                        continue
                if found.status == "entity_missing" and _paragraph_names(line, found.fact.entity):
                    # The paragraph already names the entity ("The Nifty Metal index ... -7.34% in Q2"):
                    # supported, no "for Metal" insertion (live run H read awkwardly).
                    continue
                if found.status == "entity_missing" and not named_entity:
                    unit_match = _UNIT_WORD_RE.match(sentence[end:])
                    end += unit_match.end() if unit_match else 0
                    sentence = f"{sentence[:end]} for {found.fact.entity}{sentence[end:]}"
                    named_entity = True
                    result.rewritten.append({"section": section_id, "number": token, "change": f"named entity {found.fact.entity}"})
                    continue
                if found.status in {"absent", "unit_mismatch"} and unit in {"trillion", "billion", "lakh crore", "million"}:
                    conversion = find_conversion(token, number_pool({pid: passage_texts[pid] for pid in (cited_ids or passage_texts)}),
                                                 unit=unit)
                    source = re.match(r"converted from (\S+) (.+)$", conversion or "")
                    if source:
                        unit_match = _UNIT_WORD_RE.match(sentence[end:])
                        stop = end + (unit_match.end() if unit_match else 0)
                        sentence = f"{sentence[:start]}{source.group(1)} {source.group(2)}{sentence[stop:]}"
                        result.rewritten.append({"section": section_id, "number": token,
                                                 "change": f"restored the passage's unit: {source.group(1)} {source.group(2)}"})
                        continue
                reason = {"absent": f"{token} is not in the evidence", "unit_mismatch": f"{token} has a different unit in the evidence",
                          "sign_mismatch": f"{token} contradicts the sign in the evidence",
                          "metric_mismatch": f"{token} is in the evidence for another metric or entity"}.get(found.status, found.status)
                break
            if not reason:
                reason = verify_calculation_labels(sentence, number_pool({pid: passage_texts[pid] for pid in (cited_ids or passage_texts)}))
            if reason:
                result.removed.append({"section": section_id, "number": reason.split(" ")[0], "reason": reason,
                                       "sentence": re.sub(r"\s+", " ", CITE_RE.sub("", piece)).strip()[:240]})
                log.info("V2 number policy removed a sentence in %s: %s", section_id or "?", reason[:160])
                continue
            kept.append(sentence)
        new_line = "".join(kept).strip()
        if re.fullmatch(r"\s*(?:[-*+]|\d+\.)\s*(?:\*\*[^*]*\*\*:?)?\s*", new_line or ""):
            new_line = ""  # a bullet whose only sentence was removed
        if new_line or not line.strip():
            out_lines.append(new_line if line.strip() else line)
    result.body = re.sub(r"\n{3,}", "\n\n", "\n".join(out_lines)).strip()
    return result
