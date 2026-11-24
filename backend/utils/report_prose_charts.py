"""Charts from the figures a written paragraph states (any topic), each point checked against the evidence.

Writers state series in prose that no table chart covers:

    "Public shareholding in Energy equities altered from 17.9% in June 2023 to 17.7% in March 2026, after
     reaching a high of 19.2% in June 2024. In Industrials, public ownership contracted from 17.1% in June 2023
     down to 15.4% by March 2026."
    "Hydro generation measured 1,51,627.33 MU in 2021-22, 1,62,098.77 MU in 2022-23 and 1,34,053.92 MU in 2023-24."

Each is a chart. This module reads each paragraph (line) of the written sections, finds "value + unit + date"
figures and whose they are, and groups them into one measure per chart: one series over several dates, or
several named entities sharing a unit. A point is kept only when the same number (any digit grouping) is found
next to the same year in a fetched evidence passage; that passage's sentence is the point's evidence quote, so
the chart is registered and verified like a sentence chart (utils.prose_charts). Approximate figures
("approximately 382 GW", "over 520 GW") and target / projection years are never charted. The chart is placed
after the paragraph it comes from, with a reading that states the change, the percentage change and, over two
or more years, the compound annual growth rate.
"""
from __future__ import annotations

import math
import re
from datetime import date

from utils.section_charts import PlannedChart

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_MONTH = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|"
          r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
_PERIOD = (rf"(?:{_MONTH}\.?\s+\d{{1,2}},?\s+(?:19|20)\d{{2}}|\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTH},?\s+(?:19|20)\d{{2}}|"
           rf"{_MONTH}\.?['’\s-]*(?:19|20)\d{{2}}|{_MONTH}\.?['’-]\d{{2}}(?!\d)|FY\s?'?\d{{2,4}}(?:\s?[-–]\s?\d{{2,4}})?|"
           rf"Q[1-4]\s?(?:FY)?\s?'?\d{{2,4}}|(?:19|20)\d{{2}}(?:\s?[-–‑]\s?\d{{2,4}})?)")
_UNIT = (r"(?:%|per\s?cent|percent|lakh\s+crore|crore|lakh|trillion|billion\s+units|million\s+units|billion|"
         r"million|bn|mn|bcm|tcm|gw|mw|twh|gwh|kwh|mu|bu|million\s+tonnes|tonnes|mt|bps)")
_CURRENCY = r"(?:₹|rs\.?\s?|inr\s?|us\$\s?|\$|usd\s?|€|eur\s?)"
_APPROX = (r"(?:approximately|approx\.?|about|around|nearly|almost|over|more than|less than|under|roughly|some|"
           r"close to|up to)")
_FIGURE_RE = re.compile(
    rf"(?P<approx>\b{_APPROX}\s+)?(?P<cur>{_CURRENCY})?(?P<num>\d[\d,]*(?:\.\d+)?)\s?(?P<unit>{_UNIT})(?![a-z])"
    rf"(?:\s*\([^)]{{0,40}}\))?\s+(?:in|by|as of|as on|at the end of|on|during|for|till|until|at)\s+(?:the\s+)?"
    rf"(?:end of\s+)?(?:the\s+)?(?:fiscal\s+year\s+|financial\s+year\s+)?(?P<period>{_PERIOD})(?![\d])", re.I)
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"“(])")
_CITE_RE = re.compile(r"\[(S\d+(?:\s*,\s*S\d+)*)\]")
_VERB_RE = re.compile(
    r"(?i)\b(?:altered|changed|increased|decreased|rose|fell|grew|declined|contracted|expanded|moved|climbed|"
    r"dropped|reached|stood|was|were|is|are|went|surged|slipped|improved|edged|recovered|doubled|halved|jumped|"
    r"eased|remained|added|measured|saw|witnessed|posted|generated|registered|recorded|reported|totall?ed|"
    r"amounted|accounted|hit|touched|crossed|from)\b")
_LEAD_RE = re.compile(r"(?i)^(?:in|for|among|within|across|meanwhile|similarly|by contrast|in contrast|however|"
                      r"while|whereas|and|but|the|a|an|correspondingly|consequently|overall|notably)\b[\s,]*")
_STOP_CAPS = {"The", "In", "For", "By", "From", "Meanwhile", "Similarly", "However", "While", "And", "But", "This",
              "These", "Its", "Their", "Public", "Private", "Total", "Overall", "Gross", "Net", "Share"}
_NAME_BAD = re.compile(r"[\d₹$€%|•:;,()\[\]]|(?i:\b(?:recorded|suffered|falling|rising|losses|gains|which|that|"
                       r"when|where|while|after|before|during|late|early|result|trend|years?|this|these|it|they|"
                       r"instance|example|fluctuated|concurrently|terms|whole)\b)")
MAX_REPORT_PROSE_CHARTS = 12


def _unit_key(match: re.Match) -> str:
    unit = re.sub(r"\s+", " ", match.group("unit") or "").casefold()
    unit = {"per cent": "%", "percent": "%", "bn": "billion", "mn": "million", "gw": "GW", "mw": "MW",
            "twh": "TWh", "gwh": "GWh", "kwh": "kWh", "bu": "BU", "mt": "MT", "mu": "MU", "million units": "MU",
            "billion units": "BU"}.get(unit, unit)
    currency = re.sub(r"[\s.]", "", match.group("cur") or "").casefold()
    currency = {"₹": "INR", "rs": "INR", "inr": "INR", "$": "USD", "us$": "USD", "usd": "USD",
                "€": "EUR", "eur": "EUR"}.get(currency, "")
    return f"{currency} {unit}".strip()


def period_key(period: str) -> tuple[int, int, int] | None:
    """(year, month, day) for sorting; a fiscal year sorts at its start year's April, a quarter at its months."""
    text = re.sub(r"\s+", " ", str(period or "")).strip()
    m = re.search(rf"(?i)({_MONTH})\.?\s+(\d{{1,2}}),?\s+((?:19|20)\d{{2}})", text)
    if m:
        return int(m.group(3)), _MONTHS[m.group(1)[:3].casefold()], int(m.group(2))
    m = re.search(rf"(?i)(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH}),?\s+((?:19|20)\d{{2}})", text)
    if m:
        return int(m.group(3)), _MONTHS[m.group(2)[:3].casefold()], int(m.group(1))
    m = re.search(rf"(?i)({_MONTH})\.?['’\s-]*((?:19|20)\d{{2}})", text)
    if m:
        return int(m.group(2)), _MONTHS[m.group(1)[:3].casefold()], 1
    m = re.search(rf"(?i)({_MONTH})\.?['’-](\d{{2}})(?!\d)", text)  # "Sep-23", "Mar'26"
    if m:
        return 2000 + int(m.group(2)), _MONTHS[m.group(1)[:3].casefold()], 1
    m = re.search(r"(?i)q([1-4])\s?(?:fy)?\s?'?(\d{2,4})", text)
    if m:
        year = int(m.group(2)) + (2000 if len(m.group(2)) == 2 else 0)
        return year, int(m.group(1)) * 3, 1
    m = re.search(r"(?i)fy\s?'?(\d{2,4})", text)
    if m:
        year = int(m.group(1)) + (2000 if len(m.group(1)) == 2 else 0)
        return year, 4, 1
    m = re.search(r"((?:19|20)\d{2})", text)
    return (int(m.group(1)), 12, 31) if m else None


def period_label(period: str) -> str:
    key = period_key(period)
    if key and re.search(rf"(?i){_MONTH}", period):
        return f"{['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][key[1]-1]} {key[0]}"
    return re.sub(r"\s*[–‑]\s*", "-", re.sub(r"\s+", " ", period)).strip()


def _valid_name(name: str, max_words: int) -> bool:
    """A series name or measure is a short noun phrase: no figures, symbols, clauses or time words."""
    words = name.split()
    return bool(words) and len(words) <= max_words and not _NAME_BAD.search(name) and not _VERB_RE.search(name)


def _entity_and_measure(subject: str) -> tuple[str, str]:
    """("Energy", "Public shareholding") from "Public shareholding in Energy equities"; ("Industrials", "public
    ownership") from "In Industrials, public ownership". The entity is a capitalised name; the measure the rest."""
    text = re.sub(r"\s+", " ", subject).strip(" ,;:")
    lead = re.match(r"^(?:[Ii]n|[Ff]or|[Aa]mong|[Ww]ithin|[Aa]cross)\s+([A-Z][\w&.'’-]*(?:\s+[A-Z][\w&.'’-]*){0,3})"
                    r"\s*,\s*(.*)$", text)
    if lead:
        return lead.group(1).strip(), lead.group(2).strip()
    for _ in range(2):
        text = _LEAD_RE.sub("", text).strip(" ,;:")
    names = [m for m in re.finditer(r"\b[A-Z][\w&.'’-]*(?:\s+(?:and\s+)?[A-Z][\w&.'’-]*){0,3}", text)
             if m.start() > 0 and m.group(0).split()[0] not in _STOP_CAPS]
    if names:
        name = names[0]
        measure = text[:name.start()] + text[name.end():]
        measure = re.sub(r"(?i)\b(?:in|of|for|at)\s*(?:equities|stocks|sector|segment|industry)?\s*$", "", measure)
        measure = re.sub(r"(?i)\s+(?:in|of|for|at)\s+(?=equities|stocks|sector|segment)", " ", measure)
        measure = re.sub(r"(?i)\b(?:equities|stocks)\b", "", measure)
        return name.group(0).strip(), re.sub(r"\s+", " ", measure).strip(" ,'’s")
    return "", text


def _subject(sentence: str, first: int) -> str:
    head = sentence[:first]
    verb = next(iter(_VERB_RE.finditer(head)), None)
    if verb is not None:
        head = head[:verb.start()]
    head = re.sub(r"\[[^\]]*\]|\([^)]*\)|\*+", " ", head)
    head = re.sub(r"\s+", " ", head).strip(" ,;:")
    return head if 1 <= len(head.split()) <= 12 else ""


def _paragraph_figures(paragraph: str) -> list[dict]:
    """Figures with their entity, measure, unit, period and sentence."""
    out = []
    carry_entity, carry_measure = "", ""
    this_year = date.today().year
    for sentence in _SENTENCE_RE.split(paragraph):
        matches = list(_FIGURE_RE.finditer(sentence))
        if not matches:
            continue
        subject = _subject(sentence, matches[0].start())
        entity, measure = _entity_and_measure(subject) if subject else (carry_entity, carry_measure)
        if not measure and carry_measure:
            measure = carry_measure
        if entity and not _valid_name(entity, 4):
            entity = ""
        if measure and not _valid_name(measure, 8):
            measure = ""
        if not entity and not measure:
            carry_entity, carry_measure = "", ""
            continue  # whose figures these are cannot be told: not charted
        carry_entity, carry_measure = entity, measure
        cites = [c.strip() for group in _CITE_RE.findall(sentence) for c in group.split(",")]
        for m in matches:
            if m.group("approx"):
                continue  # "approximately 382 GW", "over 520 GW": not a value to plot
            try:
                value = float(m.group("num").replace(",", ""))
            except ValueError:
                continue
            key = period_key(m.group("period"))
            if key is None or key[0] > this_year:
                continue  # a target or projection year is not an observation
            out.append({"entity": entity, "measure": measure, "unit": _unit_key(m), "value": value,
                        "raw": m.group("num"), "period": m.group("period"), "sentence": sentence.strip(),
                        "cites": cites})
    return out


def _evidence_sentence(store, raw: str, period: str, cites: list[str]):
    """(passage, sentence) of a fetched evidence passage that states this number next to this period's year.

    Digit grouping is ignored: "1,51,627.33" (Indian grouping), "151,627.33" and "151627.33" are one number."""
    key = period_key(period)
    if key is None:
        return None
    digits = raw.replace(",", "")
    whole, _dot, frac = digits.partition(".")
    pattern = ",?".join(re.escape(d) for d in whole) + (r"\." + re.escape(frac) if frac else "")
    # never the same digits with a sign ("-6.42" is not 6.42) or inside a longer number
    number_re = re.compile(r"(?<![\d.,\-−–])" + pattern + r"(?![\d])")
    year = key[0]
    short = f"{year % 100:02d}"
    # the period's year as sources write it: 2024, FY24, FY 2023-24, 2023-24, Mar'24, Sep-24 (never "-24.5")
    year_re = re.compile(rf"(?<!\d)(?:{year}|(?:fy\s?)?{year - 1}\s?[-–/]\s?(?:{short}|{year})(?!\d)|fy\s?'?{short}(?!\d)|"
                         rf"[a-z]{{3}}\.?\s?['’-]{short}(?![\d.]))", re.I)
    passages = list(getattr(store, "passages", []) or [])
    if cites:
        cited = [p for p in passages if p.source_id in cites or getattr(p, "parent_source_id", "") in cites]
        passages = cited + [p for p in passages if p not in cited]
    for passage in passages:
        if getattr(passage, "kind", "") not in {"table", "narrative"}:
            continue  # search snippets are never chart evidence
        text = str(passage.text or "")
        if whole[:3] not in text.replace(",", ""):
            continue
        found = _table_row_with(text, number_re, year_re) if passage.kind == "table" else None
        if found:
            return {"passage": passage, **found}
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
            if number_re.search(sentence) and year_re.search(sentence):
                return {"passage": passage, "quote": sentence.strip()[:500], "context": sentence}
    return None


def _table_row_with(text: str, number_re: re.Pattern, year_re: re.Pattern) -> dict | None:
    """The table row holding the number in a column whose header (or a row whose label) names the period, with
    the row label, the column and the words around the table (caption, header) that say what it measures."""
    header: list[str] | None = None
    caption = ""
    for line in text.splitlines():
        if "|" not in line:
            header = None
            if line.strip():
                caption = line.strip()
            continue
        if re.fullmatch(r"[\s|:\-]+", line):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if header is None:
            header = cells
            continue
        for index, cell in enumerate(cells):
            if not number_re.search(cell):
                continue
            column = header[index] if index < len(header) else ""
            if year_re.search(column) or year_re.search(cells[0]):
                return {"quote": line.strip()[:500], "row": cells[0], "column": column,
                        "context": " ".join([caption, " ".join(header), line])}
    return None


def _words_in(phrase: str, text: str) -> str:
    """The words of ``phrase`` that ``text`` states, in order (what the source itself says the number is)."""
    low = str(text or "").casefold()
    kept = [w for w in re.findall(r"[A-Za-z][\w'’&-]*", phrase)
            if len(w) >= 3 and w.casefold() not in {"the", "and", "for", "from", "with", "total"}
            and re.search(rf"(?<![a-z]){re.escape(w.casefold().rstrip('s'))}", low)]
    return " ".join(kept)


def growth_reading(points: list[tuple[str, float]], unit: str) -> str:
    """'from 234.72 GW in Apr 2021 to 248.54 GW in Jan 2026 (+13.82 GW; +5.9%; 1.2% a year compounded)'."""
    if len(points) < 2:
        return ""
    (p0, v0), (p1, v1) = points[0], points[-1]
    k0, k1 = period_key(p0), period_key(p1)

    def num(v: float) -> str:
        return f"{v:,.2f}".rstrip("0").rstrip(".")

    def shown(v: float) -> str:
        return f"{num(v)}%" if unit == "%" else (f"{num(v)} {unit}" if unit else num(v))
    change = v1 - v0
    sign = "+" if change >= 0 else ""
    parts = [f"{sign}{num(change)} percentage points" if unit == "%" else f"{sign}{shown(change)}"]
    if v0 > 0 and unit != "%":
        parts.append(f"{'+' if v1 >= v0 else ''}{(v1 / v0 - 1) * 100:.1f}%")
    years = ((k1[0] - k0[0]) + (k1[1] - k0[1]) / 12) if k0 and k1 else 0
    if v0 > 0 and v1 > 0 and years >= 2 and unit != "%":
        parts.append(f"{(math.pow(v1 / v0, 1 / years) - 1) * 100:.1f}% a year compounded")
    return f"from {shown(v0)} in {period_label(p0)} to {shown(v1)} in {period_label(p1)} ({'; '.join(parts)})"


def _measure_key(measure: str) -> str:
    return " ".join(w for w in re.findall(r"[a-z]{3,}", measure.casefold()) if w not in {"the", "and", "for", "total"})


def _chart_groups(figures: list[dict], store) -> list[dict[str, dict[str, dict]]]:
    """Series groups of one measure and unit, every point found in the evidence: several named entities sharing
    a unit ("Energy", "Industrials"), or one series over several dates ("Hydro generation")."""
    proven = []
    for f in figures:
        proof = _evidence_sentence(store, f["raw"], f["period"], f["cites"]) if store is not None else None
        if proof is not None:  # a figure not found next to its date in the evidence is not charted
            proven.append({**f, **proof, "label": period_label(f["period"])})
    groups: list[dict[str, dict[str, dict]]] = []
    used: set[int] = set()
    by_unit: dict[str, list[dict]] = {}
    for f in proven:
        if f["entity"]:
            by_unit.setdefault(f["unit"], []).append(f)
    for items in by_unit.values():
        series: dict[str, dict[str, dict]] = {}
        for f in items:
            series.setdefault(f["entity"], {}).setdefault(f["label"], f)
        series = {n: pts for n, pts in series.items() if len(pts) >= 2}
        if len(series) >= 2:
            groups.append(series)
            used |= {id(f) for pts in series.values() for f in pts.values()}
    by_measure: dict[tuple, dict[str, dict]] = {}
    for f in proven:
        if id(f) in used or not f["measure"]:
            continue
        by_measure.setdefault((f["entity"], _measure_key(f["measure"]), f["unit"]), {}).setdefault(f["label"], f)
    for (entity, _key, _unit), pts in by_measure.items():
        if len(pts) >= 3:
            first = next(iter(pts.values()))
            groups.append({(f"{entity} {first['measure']}".strip() if entity else first["measure"]): pts})
    return groups


def report_prose_charts(drafts: list, store, *, start_index: int = 970, limit: int = MAX_REPORT_PROSE_CHARTS,
                        existing_values: list[set] | None = None) -> list[PlannedChart]:
    """Charts of series written in the report's own paragraphs, every point found in the evidence."""
    out: list[PlannedChart] = []
    seen = [set(v) for v in existing_values or []]
    for draft in drafts:
        body = str(getattr(draft, "body", "") or "")
        for paragraph in re.split(r"\n+", body):  # the writer's paragraphs are lines
            if paragraph.lstrip().startswith(("|", "#", "[[FIGURE", "[CHART", ">", "*Source", "Source:")):
                continue
            figures = _paragraph_figures(paragraph)
            if len(figures) < 3:
                continue
            for series in _chart_groups(figures, store):
                chart = _chart(series, paragraph, draft, start_index + len(out), seen)
                if chart is None:
                    continue
                out.append(chart)
                if len(out) >= limit:
                    return out
    return out


def _chart(series: dict[str, dict[str, dict]], paragraph: str, draft, index: int, seen: list[set]):
    values = {round(p["value"], 4) for pts in series.values() for p in pts.values()}
    if len(values) < 3 or any(len(values & other) >= 0.8 * len(values) for other in seen):
        return None
    first = next(iter(next(iter(series.values())).values()))
    unit = first["unit"]
    measure = first["measure"] if len(series) > 1 else next(iter(series))
    measure = (measure or first["entity"] or "").strip()
    if not measure or not _valid_name(measure, 9):
        return None
    measure = measure[:1].upper() + measure[1:]
    title = measure if not unit or unit.casefold() in measure.casefold() else f"{measure} ({unit})"
    labels = sorted({lbl for pts in series.values() for lbl in pts}, key=lambda l: period_key(l) or (0, 0, 0))
    spec_series, points, passage_ids, source_ids = [], [], [], []
    for name, pts in series.items():
        data = []
        for lbl in labels:
            p = pts.get(lbl)
            if p is None:
                continue
            # what the source itself states the number is: its own row label and column for a table row; the
            # measure / entity words it contains (never words only the report uses)
            point = {"label": lbl, "value": p["value"], "evidenceSource": p["passage"].source_id,
                     "evidenceQuote": p["quote"], "evidenceValue": p["raw"], "evidencePeriod": lbl}
            stated_metric = _words_in(measure, p.get("context", p["quote"]))
            if stated_metric:
                point["evidenceMetric"] = stated_metric
            if p.get("row"):
                point["evidenceRowLabel"] = p["row"]
                point["evidenceEntity"] = p["row"]
                if p.get("column"):
                    point["evidenceColumn"] = p["column"]
                    point["evidenceColumnRole"] = "period"
            else:
                stated_entity = _words_in(name, p["quote"]) if len(series) > 1 else ""
                if stated_entity:
                    point["evidenceEntity"] = stated_entity
            data.append(point)
            points.append((name, lbl, p["value"]))
            if p["passage"].passage_id not in passage_ids:
                passage_ids.append(p["passage"].passage_id)
            if p["passage"].source_id not in source_ids:
                source_ids.append(p["passage"].source_id)
        spec_series.append({"name": name, "data": data})
    readings = []
    for name, pts in series.items():
        reading = growth_reading([(lbl, pts[lbl]["value"]) for lbl in labels if lbl in pts], unit)
        if reading:
            readings.append(f"{name} went {reading}" if len(series) > 1 else reading)
    spec = {"type": "line" if len(labels) >= 3 else "grouped_bar", "title": title, "unit": unit,
            "xLabel": "Period", "yLabel": unit or measure, "series": spec_series,
            # each point quotes one sentence of a fetched page passage, like utils.prose_charts: the quote is
            # registered as chart evidence and verified exactly like a sentence chart's
            "evidenceBasis": "reported", "fromReportText": True, "fromSourceSentence": True,
            "basisNote": "Values stated in the report's text, each found next to its date in a cited source."}
    chart = PlannedChart(chart_id=f"q{index}", spec=spec, passage_ids=passage_ids, source_ids=source_ids,
                         points=points, section_id=getattr(draft, "section_id", ""),
                         match_text=" ".join([title, *series.keys()]), heading=paragraph[:200])
    sentences = [s for s in _SENTENCE_RE.split(paragraph.strip()) if s.strip()]
    chart.claim = re.sub(r"\s+", " ", sentences[-1].replace("⁠", "")).strip()[:400] if sentences else ""
    if readings:
        chart.reading = (f"{measure} went " + readings[0] + "." if len(series) == 1
                         else "; ".join(readings) + ".")
    seen.append(values)
    return chart
