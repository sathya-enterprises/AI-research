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
    r"|\b(?:top|bottom|first|last|all|over|across|among)\s+\d{1,2}\b(?![,.]\d)(?!\s*%)(?!\s+(?:out\s+)?of\b)(?=\s+[a-z])"
    # Index names and ranges are names, not statistics: "Nifty 50", "Nifty Midcap 150", "Services 25/50",
    # "12-18 months".
    r"|\b(?:nifty|sensex|bse|s&p\s+bse)(?:\s+(?:next|midcap|smallcap|microcap|largemidcap|total\s+market|india))?\s+\d{2,3}\b(?![,.]\d|\s*%)"
    r"|\b\d{1,3}\s*/\s*\d{1,3}\b(?![,.]\d|\s*%)"
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
    unit: str = ""


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
            if value is None or re.fullmatch(r"(?:19|20)\d{2}", match.group(0)):
                continue
            pct = bool(re.match(r"\s*%", text[match.end():match.end() + 3]))
            pool.append(PoolNumber(value, match.group(0).lstrip("+"), pid, match.start(), text.count("\n", 0, match.start()),
                                   pct, unit_family(text[match.end():match.end() + 16])))
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


_CALC_LABEL_RE = re.compile(r"\(calculated(?: from| using)?\s*:?\s*([^)]*)\)", re.I)


def verify_calculation_labels(sentence: str, pool: list[PoolNumber]) -> str:
    """'' when every "(calculated: ...)" label in the sentence is verified, else the reason.

    Each input must appear in a cited passage, and the stated operation on those
    inputs must reproduce the number the label follows. A label whose inputs are only
    in the writer's own sentence, or whose arithmetic does not hold, fails.
    """
    pool_values = {normalize(n.raw).lstrip("-") for n in pool}
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
        inputs = re.findall(r"[-+−]?\d[\d,]*(?:\.\d+)?", body)
        if len(inputs) < 2:
            return "calculated label without its inputs"
        missing = [x for x in inputs if normalize(x).lstrip("-") not in pool_values]
        if missing:
            return f"calculation input(s) {', '.join(missing)} not in the cited passages"
        values = [abs(_float(x) or 0.0) for x in inputs]
        if "÷" in body or "/" in body:
            derived = 100 * values[0] / values[1] if values[1] else float("nan")
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
    return re.split(r"(?<=[.!?])(\s+)(?=[A-Z*\[(\"'₹$])", paragraph)


def apply_number_policy(body: str, *, passage_texts: dict[str, str], allowed_numbers: set[str],
                        section_id: str = "", context_entity: str = "") -> PolicyResult:
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
    result = PolicyResult(body=body)
    result.rewritten = []
    body, calcs, failed_calcs = apply_calculations(body, sheet, None)
    result.calculated += [{"section": section_id, "number": c["value"], "inputs": c["inputs"]} for c in calcs]
    out_lines = []
    for line in body.split("\n"):
        if not line.strip() or _TABLE_LINE_RE.match(line) or _HEADING_RE.match(line) or _FIG_RE.fullmatch(line.strip()):
            # A table cell can only be a reported value: a row with a value the evidence does
            # not contain is removed (logged and recorded), like an unsupported sentence.
            if _TABLE_LINE_RE.match(line) and not re.fullmatch(r"\s*\|[\s:|-]+\|?\s*", line):
                # A labelled calculation in a cell is checked through its inputs and arithmetic.
                calc_reason = verify_calculation_labels(line, number_pool(passage_texts)) if "(calculated" in line else ""
                plain_cells = re.sub(r"[-+−]?\d[\d,]*(?:\.\d+)?[^()\d\n|]{0,30}\(calculated[^)]*\)", " ", line)
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
        for piece in _sentences(line):
            if not piece.strip():
                kept.append(piece)
                continue
            sentence = piece
            reason = ""
            if "[[UNSUPPORTED_CALC]]" in sentence:
                reason = "requested calculation uses numbers that are not facts in the evidence"
            cited = [t.strip().upper() for g in CITE_RE.findall(sentence) for t in re.split(r"\s*[,;]\s*", g)]
            cited_ids = {pid for pid in passage_texts if pid in cited or any(pid.split(".")[0] == c for c in cited)}
            tokens = statistic_tokens(re.sub(r"\(calculated[^)]*\)", " ", sentence))
            named_entity = False
            for token in ([] if reason else tokens):
                start = sentence.find(token)
                if start < 0:
                    continue
                end = start + len(token)
                explicit = "-" if token[:1] in "-−" else ("+" if token[:1] == "+" else "")
                unit = unit_family(sentence[end:end + 16])
                if re.match(r"[^()\d\n]{0,30}\(calculated", sentence[end:]):
                    continue  # a labelled calculation: checked below through its inputs and arithmetic
                found = lookup(sheet, token, sign=explicit, unit=unit, sentence=CITE_RE.sub(" ", sentence),
                               passage_ids=cited_ids or None, context_entity=context_entity)
                if found.status == "ok":
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
