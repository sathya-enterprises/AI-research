"""Charts from figures that sources state in a sentence rather than a table (any topic).

Offline scan of 39 saved runs: every topic's fetched pages held chart-ready figures, and for several
(EU gas, mobile/5G, education) most of them were written as prose:

    "...EU's biggest gas supplier (34 %, 23.3 bcm), followed by Russia (18 %, 12.4 bcm), the US (16 %, ...)"
    "Primary GER stands at 90.9%, upper primary at 90.3%, secondary at 78.7%, and higher secondary at 58.4%"
    "...which increased 35 percent in 2015 to 41 percent in 2020, dropped to ..."

The table chart builder never reads these. This module turns ONE sentence carrying >= 3 figures of one
unit into a candidate chart (category comparison or time series). Precision over recall:

* every point comes from the same sentence of the same evidence passage; that sentence is each point's
  evidence quote, and the exact number token is its evidence value;
* labels must be short names (no digits, not pronouns), values distinct, the unit the same;
* the sentence must share words with the question; the candidate then goes through the same quality,
  window, meaning and source-verification gates as a table chart.
"""
from __future__ import annotations

import re

from utils.evidence_text import SENTENCE_END
from utils.section_charts import PlannedChart

_UNIT = (r"(?:%|per\s?cent|percent|lakh\s+crore|crore|lakh|trillion|billion|million|bn|mn|bcm|tcm|"
         r"gw|mw|twh|gwh|million\s+tonnes|tonnes|mt|bps)")
_CURRENCY = r"(?:₹|rs\.?\s?|inr\s?|us\$\s?|\$|usd\s?|€|eur\s?)"
_VALUE = rf"(?P<cur>{_CURRENCY})?(?P<num>\d[\d,]*(?:\.\d+)?)\s?(?P<unit>{_UNIT})(?![a-z])"
_PERIOD = r"(?:FY\s?'?\d{2,4}(?:-\d{2})?|(?:19|20)\d{2}(?:-\d{2,4})?|Q[1-4]\s?(?:FY)?\s?'?\d{2,4})"
_SERIES_RE = re.compile(rf"{_VALUE}\s+(?:in|during|for|by|as of|at the end of|as on)\s+(?P<period>{_PERIOD})\b", re.I)
_CATEGORY_RE = re.compile(
    rf"(?P<label>[A-Za-z][A-Za-z&.'’\- ]{{0,40}}?)\s*(?:\(\s*|\s(?:stands at|stood at|was at|is at|at|of|with|"
    rf"attracted|received|recorded|reported|drew|got|saw)\s+|:\s*|\s+(?={_CURRENCY}\d))"
    rf"{_VALUE}", re.I)
_CLAUSE_SPLIT = re.compile(r",|;|\band\b|\bwhile\b|\bwhereas\b|\bfollowed by\b|\bversus\b|\bvs\.?", re.I)
_LEAD_WORDS = re.compile(r"(?i)^(?:and|while|whereas|the|with|followed by|by|for|of|in|its|their|a|an)\s+")
_TRAIL_WORDS = re.compile(r"(?i)\s+(?:stands|stood|was|is|were|are|reached|rose|fell|grew|accounted|attracted|"
                          r"received|recorded|reported|at|of|with)$")
_BAD_LABEL = re.compile(r"(?i)^(?:it|this|that|these|those|they|which|who|share|growth|rate|total|about|around|"
                        r"nearly|over|up|down|to|from|by|in|an?|the|its|their|year|years|period)$")
_LEAD_CLAUSE = re.compile(r"(?i)^(?:indeed|in addition|additionally|moreover|however|meanwhile|overall|notably|"
                          r"according to [^,]+|in (?:fy\s?\d{2,4}|\d{4}(?:-\d{2})?))\s*,\s*")
_STOP = set("""a an and are as at be by can for from has have how in into is it its of on or over past the their
this to was were what which with within years year compare compared did do does change changed trend trends
across between analyse analyze explain show grown growth evolve""".split())
MAX_PROSE_CHARTS = 12
# A label is a name ("Europe", "upper primary", "PSU Bank"), never a clause ("DIIs have invested", "foreign
# tourists spent"): review of the first offline run found every junk sentence chart had a verb in a label.
_LABEL_VERB = re.compile(r"(?i)\b(?:has|have|had|is|are|was|were|be|been|led|leads|remain\w*|pull\w*|invest\w*|"
                         r"spen[dt]\w*|result\w*|increas\w*|decreas\w*|declin\w*|ris\w*|rose|fell|grew|grow\w*|"
                         r"reach\w*|account\w*|contribut\w*|record\w*|report\w*|said|says|followed|compared)\b")
# A measure is a short noun phrase ("GER", "Share of Russian natural gas in total EU gas imports"); a fragment
# with figures, bullets or a dangling connector ("Respectively. • Sector Indices...", "About 57% of total RES
# capacity", "USA led export destinations in 2022 with $") is not a chart title.
_BAD_MEASURE = re.compile(r"[\d•·:$₹€|]|(?i:^(?:respectively|salient|about|among|around|nearly|over|this|these|that|"
                          r"those|it|in|on|at|as|while|with|third|second|first|highest|lowest|biggest|largest|"
                          r"small|number one)\b)|(?i:\b(?:with|by|of|to|from|and|or|the|a|an|followed)\s*$)")
MAX_LABEL_WORDS = 4


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z0-9&-]{2,}", str(text or "").casefold()) if w not in _STOP}


def _unit_key(match: re.Match) -> str:
    unit = re.sub(r"\s+", " ", (match.group("unit") or "")).casefold()
    unit = {"per cent": "%", "percent": "%", "percent": "%", "bn": "billion", "mn": "million"}.get(unit, unit)
    currency = re.sub(r"[\s.]", "", (match.group("cur") or "")).casefold()
    currency = {"₹": "INR", "rs": "INR", "inr": "INR", "$": "USD", "us$": "USD", "usd": "USD",
                "€": "EUR", "eur": "EUR"}.get(currency, "")
    return f"{currency} {unit}".strip()


def _number(raw: str) -> float | None:
    try:
        return float(raw.replace(",", ""))
    except ValueError:
        return None


def _clean_label(label: str) -> str:
    text = re.sub(r"\s+", " ", str(label or "")).strip(" .,:;-–(")
    for _ in range(3):
        text = _LEAD_WORDS.sub("", text).strip()
        text = _TRAIL_WORDS.sub("", text).strip()
    return text


def _measure(sentence: str, first_start: int, first_label: str = "") -> str:
    """The measure the figures are of: the words before the first figure, trimmed to a short phrase."""
    head = sentence[:first_start]
    if first_label and head.rstrip(" (:").endswith(first_label):
        head = head.rstrip(" (:")[: -len(first_label)]
    head = _LEAD_CLAUSE.sub("", head.strip())
    head = re.sub(r"\([^)]*\)", " ", head)
    head = re.split(r"(?i),?\s+(?:which|that|who|where)\b", head)[0]
    head = re.sub(r"(?i)\b(?:stands?|stood|was|were|is|are|increased|rose|fell|grew|declined|reached|"
                  r"remained|accounted for|amounted to)\b.*$", "", head)
    head = re.sub(r"\s+", " ", head).strip(" ,:;-–")
    words = head.split()
    if len(words) > 10:
        words = words[-10:]
    phrase = " ".join(words)
    phrase = re.sub(r"(?i)^(?:the|a|an)\s+", "", phrase)
    return phrase[:1].upper() + phrase[1:] if len(phrase.split()) >= 2 else ""


def _sentences(text: str) -> list[str]:
    parts = re.split(SENTENCE_END + r"\s+(?=[A-Z0-9\"“(])|\n+", str(text or ""))
    return [p.strip() for p in parts if 40 <= len(p.strip()) <= 600 and "|" not in p]


def _series_points(sentence: str) -> tuple[list[tuple[str, float, str]], str, int] | None:
    matches = list(_SERIES_RE.finditer(sentence))
    if len(matches) < 3:
        return None
    units = {_unit_key(m) for m in matches}
    if len(units) != 1:
        return None
    points: dict[str, tuple[float, str]] = {}
    for m in matches:
        value = _number(m.group("num"))
        period = re.sub(r"\s+", " ", m.group("period")).strip()
        if value is None or period in points:
            return None  # the same period twice in one sentence is two different measures
        points[period] = (value, m.group("num"))
    return [(p, v, raw) for p, (v, raw) in points.items()], units.pop(), matches[0].start()


def _category_points(sentence: str) -> tuple[list[tuple[str, float, str]], str, int, str] | None:
    found: list[tuple[str, float, str, str, int]] = []
    offset = 0
    for clause in _CLAUSE_SPLIT.split(sentence):
        start = sentence.find(clause, offset)
        offset = max(offset, start + len(clause)) if start >= 0 else offset
        for match in _CATEGORY_RE.finditer(clause):  # "penetration stood at 3.7% with life insurance at 2.7%"
            label = _clean_label(match.group("label"))
            value = _number(match.group("num"))
            if (not label or value is None or re.search(r"\d", label) or len(label.split()) > MAX_LABEL_WORDS
                    or _BAD_LABEL.match(label) or _LABEL_VERB.search(label)):
                continue
            found.append((label, value, match.group("num"), _unit_key(match),
                          (start if start >= 0 else 0) + match.start("num")))
    if len(found) < 3:
        return None
    unit = max({f[3] for f in found}, key=lambda u: sum(1 for f in found if f[3] == u))
    same = [f for f in found if f[3] == unit]
    labels = [f[0].casefold() for f in same]
    if len(same) < 3 or len(set(labels)) != len(labels):
        return None
    return [(f[0], f[1], f[2]) for f in same], unit, same[0][4], same[0][0]


def prose_charts(store, question: str, *, start_index: int = 500, limit: int = MAX_PROSE_CHARTS,
                 min_reliability: float = 0.5) -> list[PlannedChart]:
    """Candidate charts from single sentences of narrative evidence passages."""
    topic = _words(question)
    charts: list[PlannedChart] = []
    seen: set[tuple] = set()
    for passage in getattr(store, "passages", []) or []:
        if passage.kind != "narrative" or float(getattr(passage, "reliability", 0) or 0) < min_reliability:
            continue
        for sentence in _sentences(passage.text):
            if not (_words(sentence) & topic):  # the meaning gate checks topic again on the chart
                continue
            kind = "line"
            parsed = _series_points(sentence)
            first_label = ""
            if parsed:
                points, unit, first_start = parsed
                points.sort(key=lambda p: p[0])
            else:
                parsed_cat = _category_points(sentence)
                if not parsed_cat:
                    continue
                points, unit, first_start, first_label = parsed_cat
                kind = "bar"
            values = [v for _label, v, _raw in points]
            if len(set(values)) < 3:
                continue
            signature = tuple(sorted((label.casefold(), round(v, 4)) for label, v, _raw in points))
            if signature in seen:
                continue
            seen.add(signature)
            measure = _measure(sentence, first_start, first_label)
            if first_label and measure.casefold().endswith(first_label.casefold()) and \
                    measure.casefold() != first_label.casefold():
                measure = ""  # "Metals and mining sector": the phrase is a list of labels, not a measure
            elif first_label and measure.casefold() == first_label.casefold():
                # "Primary GER stands at 90.9%, upper primary at 90.3%...": the words before the first figure
                # are that point's label; the measure is what the labels share ("GER"). Labels that share
                # nothing ("Financial services attracted Rs 43,102 crore, telecom...") name no measure: skip.
                others = {w.casefold() for label, _v, _raw in points[1:] for w in label.split()}
                residual = [w for w in first_label.split() if w.casefold() not in others]
                measure = " ".join(residual) if 0 < len(residual) < len(first_label.split()) else ""
                if measure and kind == "bar":
                    shared = set(w.casefold() for w in first_label.split()) - {w.casefold() for w in residual}
                    if not shared:
                        measure = ""
            if not measure or _BAD_MEASURE.search(measure) or len(measure.split()) > 12:
                continue
            measure = measure[:1].upper() + measure[1:]
            unit_text = unit if unit else ""
            title = measure if (not unit_text or unit_text.casefold() in measure.casefold()) else f"{measure} ({unit_text})"
            data = [{"label": label, "value": value, "evidenceSource": passage.source_id, "evidenceQuote": sentence,
                     "evidenceValue": raw, "evidenceRowLabel": label, "evidenceMetric": measure,
                     **({"evidencePeriod": label} if kind == "line" else {"evidenceEntity": label})}
                    for label, value, raw in points]
            spec = {"type": kind, "title": title, "xLabel": "Period" if kind == "line" else "Item",
                    "yLabel": unit_text or measure, "unit": unit_text,
                    "series": [{"name": measure, "data": data}],
                    "evidenceBasis": "reported", "fromSourceSentence": True,
                    "basisNote": f"Values copied from one sentence in evidence passage {passage.passage_id}."}
            charts.append(PlannedChart(
                chart_id=f"p{start_index + len(charts)}", spec=spec, passage_ids=[passage.passage_id],
                source_ids=[passage.source_id], points=[(measure, label, value) for label, value, _raw in points],
                match_text=" ".join([title, *[label for label, _v, _raw in points]]), heading=sentence))
            if len(charts) >= limit:
                return charts
    return charts
