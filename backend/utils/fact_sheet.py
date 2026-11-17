"""Per-section fact sheet and number lookup (Stage F).

Deterministic (no LLM calls). Every number in an evidence passage becomes a fact:
value (as written), unit, metric, entity, period and passage id. A fact exists
only because its value string appears in the passage, so the sheet cannot
contain a number the evidence does not hold.

  * Pipe tables: the row label is the entity, the column header is the metric
    (and its period / unit); a header-level unit ("(CR)", "(%)") applies to the
    column's cells.
  * Space-separated PDF tables ("Realty -6.7% -8.5% 18% ..." under a header
    "Instrument VaR5 ES5 DnVol ..."): cells are mapped to header tokens by position.
  * Prose: the entity is the nearest named sector/index before the number in the
    same sentence; the metric is the words around the number.

The V2 number check is a lookup in this sheet (value + unit + explicit sign +
metric/entity). Derived numbers are computed here, in code, from facts the writer
names ("[[calc: 47,955 + 21,745 + 14,116]]"), never by the LLM.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_NUM_RE = re.compile(r"(?<![\w.,])([+\-−–]?)\s?(?:₹|Rs\.?\s?|INR\s?|US\$|USD\s?|\$)?\s?(\d[\d,]*(?:\.\d+)?)(?!\.\d)")
_UNIT_AFTER_RE = re.compile(r"^\s?(%|per\s?cent|lakh\s+crores?|lakh\s+crs?\.?|crores?|crs?\.?(?![a-z])|trillion|tn\b|billion|bn\b|"
                            r"million|mn\b|bps|x(?![a-z])|times)", re.I)
_PERIOD_RE = re.compile(r"(?i)\b(?:Q[1-4]\s*(?:FY|CY)?\s*'?\d{2,4}|H[12]\s*FY\s*'?\d{2,4}|FY\s*'?\d{2,4}(?:\s*[-/–]\s*\d{2,4})?|"
                        r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+'?\d{2,4}|"
                        r"\d{1,2}\s*[- ]?(?:w|wk|week|weeks|m|month|months|y|yr|year|years)\b|(?:19|20)\d{2}|ytd|qtd)")
_VALUE_CELL_RE = re.compile(r"\s*[+\-−–]?\s?(?:₹|Rs\.?\s?|INR\s?|US\$|\$)?\s?\d[\d,]*(?:\.\d+)?\s?(?:%|x|bps|cr|crore|bn|mn)?\s*", re.I)
_STOP = {"the", "and", "for", "with", "from", "that", "this", "was", "were", "are", "its", "over", "into", "while", "which",
         "than", "also", "has", "had", "have", "per", "cent", "index", "nifty", "sector", "sectors", "market"}


def unit_family(text: str) -> str:
    match = _UNIT_AFTER_RE.match(text or "")
    if not match:
        return ""
    word = re.sub(r"\s+", " ", match.group(1).casefold()).rstrip(".")
    if word in {"%", "percent", "per cent"}:
        return "%"
    if word.startswith("lakh"):
        return "lakh crore"
    if word.startswith("cr"):
        return "crore"
    return {"tn": "trillion", "bn": "billion", "mn": "million", "times": "x"}.get(word, word)


def _plain(raw: str) -> str:
    raw = raw.replace(",", "")
    try:
        return f"{float(raw):.6f}".rstrip("0").rstrip(".")
    except ValueError:
        return raw


_ACRONYM_WORDS = {"ev": {"elect", "vehic"}, "evs": {"elect", "vehic"}, "pcs": {"publi", "charg", "stati"}}
_WHEELER_RE = re.compile(r"(?i)(?<![A-Za-z0-9])(?:e-?)?([234])\s*-?\s*w(?:s|heelers?)?(?![A-Za-z])")


def _stems(text: str) -> set[str]:
    out = set()
    # Vehicle segments are written "e-2W", "E3W", "3Ws", "2-wheelers" or "two wheelers".
    if _WHEELER_RE.search(text or "") or re.search(r"(?i)\b(?:two|three|four)[\s-]*wheelers?\b", text or ""):
        out.add("wheel")
    words = re.findall(r"[A-Za-z][A-Za-z&]+", text or "")
    for raw in words:
        word = raw.casefold()
        if raw.isupper() and 2 <= len(raw) <= 5:
            out.add(word)
        elif raw.endswith("s") and raw[:-1].isupper() and 2 <= len(raw) - 1 <= 5:
            out.add(word[:-1])  # a plural acronym ("EVs", "NPAs") is the acronym
        elif len(word) >= 4 and word not in _STOP:
            if word.endswith("s") and len(word) > 4 and not word.endswith("ss"):
                word = word[:-1]
            out.add(word[:5])
    # An acronym is also the words it stands for ("EVs" in a table header, "vehicles" in the claim).
    for acronym, expansion in _ACRONYM_WORDS.items():
        if acronym in out:
            out.update(expansion)
    # Acronyms of multi-word names: "Oil & Natural Gas Corporation Ltd." is cited as "ONGC".
    for run in re.findall(r"(?:[A-Z][a-z]+(?:\s+(?:&|and|of)\s+|\s+)){1,5}[A-Z][a-z]+", text or ""):
        initials = "".join(w[0] for w in re.findall(r"[A-Z][a-z]+", run) if w not in {"Ltd", "Limited", "The", "Inc"})
        if 2 <= len(initials) <= 6:
            out.add(initials.casefold())
    # Metric families: the writer says "net selling" where the table says "net investment".
    for family, members in _FAMILIES.items():
        if out & members:
            out.add(family)
    return out


_FAMILIES = {
    "~flow": {"inves", "outfl", "inflo", "flow", "selli", "buyin", "sold", "bough", "purch", "diver", "liqui", "fpi", "fii", "dii"},
    "~return": {"retur", "gain", "gaine", "rose", "risen", "fell", "falle", "decli", "perfo", "rally", "chang", "under",
                "outpe", "laggi", "trail", "lagge", "advan"},
    "~valuation": {"valua", "multi", "pe", "pb", "earni", "price"},
}


@dataclass
class Fact:
    fid: str
    raw: str
    value: str            # normalised digits ("47955", "-7.1")
    sign: str             # "-", "+" or ""
    unit: str
    metric: str
    entity: str
    period: str
    passage_id: str
    stems: set[str] = field(default_factory=set)
    kind: str = "prose"     # table | prose
    label: str = ""         # a table row's own label ("Nifty PSU Bank")
    unit_text: str = ""     # the column's unit as written ("MnT", "Rs per quintal") when it is not a scale unit
    context: str = ""       # the sentence (prose) or row text the value was read from

    def to_dict(self) -> dict:
        return {"id": self.fid, "value": self.raw, "unit": self.unit, "metric": self.metric[:60], "entity": self.entity[:40],
                "period": self.period, "passage": self.passage_id}


def _entity_in(text: str) -> str:
    from utils.chart_titles import _SECTOR_ALIAS_RE

    best = ""
    best_pos = -1
    for canon, pattern in _SECTOR_ALIAS_RE.items():
        for match in pattern.finditer(text or ""):
            if re.match(r"(?i)\s+(?:year|years|results?|statements?|position|inclusion|literacy)\b", (text or "")[match.end():]):
                continue  # "financial year" is a period, not the Financial Services sector
            if match.start() > best_pos:
                best, best_pos = canon, match.start()
    return best


_SCHEMA_FIRST_RE = re.compile(r"(?i)^(?:column|field|variable|attribute)(?:\s+name)?$")
_SCHEMA_OTHER_RE = re.compile(r"(?i)^(?:type|data\s*type|dtype|distinct|unique|count|non[- ]?null|nulls?|missing|description)$")


def is_schema_table(header: list[str]) -> bool:
    """A dataset's column listing ("Column | Type | Distinct | Count | Description"): it describes the data,
    its numbers are counts of values, not statistics (it became a "Distinct: year, ..." chart)."""
    cells = [c.strip() for c in header]
    return (len(cells) >= 3 and bool(_SCHEMA_FIRST_RE.match(cells[0]))
            and sum(bool(_SCHEMA_OTHER_RE.match(c)) for c in cells[1:]) >= 2)


def is_column_numbering(cells: list[str]) -> bool:
    """A row that only numbers the table's columns: "1 | 2 | 3 | 4" or "(1) | (2) | (3)" (Indian government
    tables put one under the header; read as data it became a "1, 2, 3" chart and facts like Wheat = 4)."""
    numbers = [re.fullmatch(r"\(?\s*(\d{1,2})\s*\)?", c.strip()) for c in cells]
    if len(cells) < 3 or not all(numbers):
        return False
    values = [int(m.group(1)) for m in numbers]
    return values == list(range(values[0], values[0] + len(values))) and values[0] in (0, 1, 2)


def extract_facts(passage) -> list[Fact]:
    # Page markup splits a decimal ("6 .09 percent"): read as two numbers, "6%" became a fact (inflation run).
    text = re.sub(r"(?<=\d) \.(?=\d)", ".", str(passage.text or ""))
    pid = passage.passage_id
    facts: list[Fact] = []
    lines = text.splitlines()
    header: list[str] = []
    header_units: list[str] = []
    header_unit_texts: list[str] = []
    space_header: list[str] = []
    heading = next((ln.strip() for ln in lines if ln.strip() and " | " not in ln), "")
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            header, space_header = [], []
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        # A two-cell row ("Total | 257") under an active header is a row of that table, and so is a
        # two-cell row next to another one: a two-column table ("Year | India" then "2019 | 29.52")
        # starts with its header, which was read as prose, so none of its values had a metric or row.
        if (" | " in stripped and (stripped.startswith("|") or len(cells) >= 3 or (header and len(cells) == 2)
                                   or (len(cells) == 2 and _two_cell_neighbour(lines, index)))
                and max(len(c) for c in cells) <= 160):
            if re.fullmatch(r"[\s|:-]+", stripped):
                continue  # Markdown separator row
            if header and is_column_numbering(cells):
                continue  # "1 | 2 | 3 | ... | 8" under the header numbers the columns; it is not data
            following = lines[index + 1].strip() if index + 1 < len(lines) else ""
            above_separator = bool(re.fullmatch(r"\|?[\s|:-]*-{3,}[\s|:-]*", following))
            is_header = (not any(re.search(r"\d", c) for c in cells[1:]) or above_separator
                         or (not header and not any(_VALUE_CELL_RE.fullmatch(c) for c in cells[1:])))
            if (is_header and len(cells) == 2 and not header and not above_separator
                    and any(re.search(r"\d", c) for c in cells[1:])):
                # A two-column label/value row ("Reliance Jio | 908.34 million subscribers") is data,
                # not a header: read as a header, its value vanished from the evidence.
                is_header = False
            if is_header:
                if header and (not any(re.search(r"[A-Za-z]{3,}", c) for c in cells)
                               or len(cells) < len(header) - 1):
                    # A formula / column-code row under the header ("(A) | (B) | (C)=(A)/(B)") or a
                    # partial sub-header from merged cells ("Installed | Decommissioned" under a
                    # nine-column header) does not rename the columns.
                    continue
                # The table's header (a row without values, or the row above the separator):
                # column names ("4-week return (%)" has digits but is a name) and their units.
                header = cells
                header_units = [(unit_family(_header_unit_text(c)) or unit_family(re.sub(r"^.*?\(", "", c))) if "(" in c
                                else ("%" if "%" in c else "") for c in cells]
                header_unit_texts = [_header_unit_text(c) for c in cells]
                continue
            if header and is_schema_table(header):
                continue  # a dataset's column listing: "26 distinct values" is not a statistic
            # A rank / serial-number first column is not the row's entity: the next text cell is.
            # A row with no text cell ("2024 | 19,50,490 | ...") is labelled by its first cell,
            # not by the first value that has a comma in it.
            label = next((c for c in cells if c and re.search(r"[A-Za-z]", c)), cells[0])
            # A total row often spans the serial-number column ("Total | 257" under
            # "Sr. No. | State | Number of Units"): its cells align from the right.
            offset = (len(header) - len(cells)
                      if header and len(cells) < len(header)
                      and re.match(r"(?i)\s*(?:sr\b|sr\.|s\.?\s*no\b|sl\b|sl\.|#|serial)", header[0] or "") else 0)
            for col, cell in enumerate(cells[1:], 1):
                for match in _NUM_RE.finditer(cell):
                    hcol = col + offset
                    unit = unit_family(cell[match.end():]) or (header_units[hcol] if hcol < len(header_units) else "")
                    metric = header[hcol] if hcol < len(header) else ""
                    facts.append(_fact(pid, len(facts), match, unit, metric, _entity_in(label) or label,
                                       _period(metric) or _period(label), f"{label} {metric} {heading}"))
                    facts[-1].kind, facts[-1].label = "table", label.strip()[:60]
                    if not unit and hcol < len(header_unit_texts):
                        facts[-1].unit_text = header_unit_texts[hcol]
            continue
        tokens = stripped.split()
        numeric = [t for t in tokens if re.fullmatch(r"[+\-−–]?\d[\d,]*(?:\.\d+)?%?x?", t)]
        words = [t for t in tokens if t not in numeric]
        if len(tokens) >= 4 and not re.search(r"\d", stripped) and not stripped.endswith((".", "!", "?", ":")):
            space_header = tokens  # a candidate header of a space-separated table
            continue
        if space_header and len(numeric) >= 3 and len(numeric) >= len(tokens) - 3:
            label = " ".join(words)
            names = space_header[-len(numeric):]
            for i, tok in enumerate(numeric):
                match = _NUM_RE.search(tok)
                if match:
                    unit = "%" if tok.endswith("%") else ""
                    facts.append(_fact(pid, len(facts), match, unit, names[i] if i < len(names) else "",
                                       _entity_in(label) or label, "", f"{label} {names[i] if i < len(names) else ''} {heading}"))
                    facts[-1].kind, facts[-1].label = "table", label.strip()[:60]
            continue
        for sentence in _sentences(stripped):
            for match in _NUM_RE.finditer(sentence):
                if re.fullmatch(r"(?:19|20)\d{2}", match.group(2)) and not match.group(1):
                    continue
                unit = unit_family(sentence[match.end():])
                before = sentence[:match.start()]
                facts.append(_fact(pid, len(facts), match, unit, " ".join(before.split()[-6:]), _entity_in(before),
                                   _period(sentence), sentence))
    return facts


_UNIT_TEXT_RE = re.compile(r"(?i)^(?:in\s+)?([₹$€£]?[A-Za-z₹$€£][A-Za-z₹$€£ ./-]{0,23})$")
_NOT_A_UNIT_RE = re.compile(r"(?i)\b(?:continued|cont|approx|provisional|estimated?|notes?|source|[per])\.?$")


def _header_unit_text(cell: str) -> str:
    """The unit in a column header's trailing parentheses: "Quantity (in MnT)" -> "MnT",
    "MSP (Rs per quintal)" -> "Rs per quintal". Not units: years, "continued", notes, long phrases."""
    m = re.search(r"\(([^()]{1,30})\)\s*$", cell or "")
    if not m:
        return ""
    inner = m.group(1).strip()
    unit = _UNIT_TEXT_RE.match(inner)
    if not unit or _NOT_A_UNIT_RE.search(inner):
        return ""
    return unit.group(1).strip()


def _two_cell_neighbour(lines: list[str], index: int) -> bool:
    """The line before or after is also a two-cell " | " row (the rows of a two-column table)."""
    for j in (index - 1, index + 1):
        if 0 <= j < len(lines):
            other = lines[j].strip()
            if " | " in other and len(other.strip("|").split("|")) == 2:
                return True
    return False


_ABBREVIATION_END_RE = re.compile(r"(?i)(?:^|[\s(])(?:rs|re|no|nos|mr|mrs|ms|dr|st|vs|approx|est|inc|ltd|co|corp|fig|figs|"
                                  r"e\.g|i\.e|etc|u\.s|u\.k|sr|jr|bn|mn|cr|lakh|govt|dept|vol|op|cit)\.$")


def _sentences(text: str) -> list[str]:
    """Split prose into sentences, but not after abbreviations: "Sales Turnover of Rs. 104545 crore" was cut
    at "Rs.", so the value lost its currency and its subject."""
    parts = re.split(r"(?<=[.!?;])\s+", text)
    out: list[str] = []
    for part in parts:
        if out and _ABBREVIATION_END_RE.search(out[-1]):
            out[-1] = f"{out[-1]} {part}"
        else:
            out.append(part)
    return out


def facts_in_row(cells: list[str]) -> bool:
    return any(_NUM_RE.search(c) for c in cells[1:])


def _period(text: str) -> str:
    match = _PERIOD_RE.search(text or "")
    return match.group(0) if match else ""


def _fact(pid: str, index: int, match: re.Match, unit: str, metric: str, entity: str, period: str, context: str) -> Fact:
    sign = "-" if match.group(1) in {"-", "−", "–"} else ("+" if match.group(1) == "+" else "")
    value = _plain(match.group(2))
    return Fact(fid=f"{pid}#{index + 1}", raw=match.group(0).strip(), value=("-" if sign == "-" else "") + value, sign=sign,
                unit=unit, metric=metric.strip(), entity=entity.strip(), period=period, passage_id=pid,
                stems=_stems(f"{metric} {entity} {context}"), context=context)


_SCALED_COUNT_RE = re.compile(r"(?<![\w.,])(\d[\d,]*(?:\.\d+)?)\s*(lakhs?|crores?)\b(?!\s+crores?)", re.I)


class FactSheet:
    def __init__(self, passages):
        self.facts: list[Fact] = []
        self.texts: dict[str, str] = {}  # passage id -> text, for callers that cite a passage found here
        for passage in passages:
            self.facts.extend(extract_facts(passage))
            self.texts[str(getattr(passage, "passage_id", ""))] = str(getattr(passage, "text", "") or "")
        # Row labels and column headers of each passage's tables: a claim that names one of them is about it.
        self.table_names: dict[str, set[str]] = {}
        for fact in self.facts:
            if fact.kind == "table":
                names = self.table_names.setdefault(fact.passage_id, set())
                names.update(n for n in (fact.label, fact.metric) if n)
        self.by_value: dict[str, list[Fact]] = {}
        for fact in self.facts:
            self.by_value.setdefault(fact.value.lstrip("-"), []).append(fact)
        # A count written with an Indian scale word is also the plain count: "15 lakh" in a passage
        # supports "1,500,000" / "15,00,000" in the text ("lakh crore" amounts are not expanded).
        by_passage_value = {(f.passage_id, f.value.lstrip("-")): f for f in self.facts}
        for pid, text in self.texts.items():
            for match in _SCALED_COUNT_RE.finditer(text):
                fact = by_passage_value.get((pid, _plain(match.group(1))))
                if fact is None:
                    continue
                scale = 1e5 if match.group(2).casefold().startswith("lakh") else 1e7
                try:
                    scaled = _plain(f"{float(match.group(1).replace(',', '')) * scale:.4f}")
                except ValueError:
                    continue
                if fact not in self.by_value.setdefault(scaled, []):
                    self.by_value[scaled].append(fact)

    def passage_stems(self, passage_id: str) -> set[str]:
        cache = self.__dict__.setdefault("_passage_stems", {})
        if passage_id not in cache:
            cache[passage_id] = _stems(self.texts.get(passage_id, ""))
        return cache[passage_id]

    def candidates(self, raw: str, passage_ids: set[str] | None = None) -> list[Fact]:
        facts = self.by_value.get(_plain(raw.lstrip("+-−–")), [])
        return [f for f in facts if not passage_ids or f.passage_id in passage_ids]


@dataclass
class Lookup:
    status: str                  # ok | entity_missing | metric_mismatch | unit_mismatch | sign_mismatch | absent
    fact: Fact | None = None


def lookup(sheet: FactSheet, raw: str, *, sign: str, unit: str, sentence: str, passage_ids: set[str] | None,
           context_entity: str = "") -> Lookup:
    """Is a claimed number supported by a fact (value + unit + explicit sign + metric/entity)?"""
    found = sheet.candidates(raw, passage_ids)
    if not found:
        return Lookup("absent")
    unit_ok = [f for f in found if not unit or not f.unit or f.unit == unit]
    if not unit_ok:
        return Lookup("unit_mismatch", found[0])
    sign_ok = [f for f in unit_ok if not sign or not f.sign or (sign == "-") == (f.sign == "-")]
    if not sign_ok:
        return Lookup("sign_mismatch", unit_ok[0])
    claim = _stems(sentence)
    context = _stems(context_entity)
    position = _number_position(sentence, raw)
    claim_period = _nearest_period(sentence, position)

    def other_period(fact: Fact) -> bool:
        """A table row's value quoted with another period next to it ("29.52% in 2020" for the 2019 row)."""
        periods = _period_keys(f"{fact.label} | {fact.metric}") if fact.kind == "table" else set()
        return bool(claim_period and periods and claim_period not in periods)

    for fact in sign_ok:
        # Matching words are accepted without a period check: over the 30-prompt benchmark a "nearest
        # period" check here removed 140+ correct sentences that quote several years in one sentence.
        if not fact.stems or claim & fact.stems:
            return Lookup("ok", fact)
    for fact in sign_ok:
        if context & fact.stems and fact.entity:
            return Lookup("entity_missing", fact)  # right value; the sentence does not name its entity
    # Words are not the only evidence that a number is this fact. The writer's own data rows
    # ("2018-19 1750 1700 1840", "FY 2017-18: 7,926.62 crore") have no words, and writers paraphrase
    # ("penetration" for "Individuals using the Internet"). Accept the value when the evidence itself
    # pins it down - the period next to it is the table row's period, or the value is too specific to
    # recur by chance in the cited passage - unless the sentence names another row/column of the same
    # table or another period (the right value attached to the wrong entity or year).
    digits = _significant_digits(raw)
    decimal = "." in raw
    cited = passage_ids is not None
    for fact in sign_ok:
        fact_periods = _period_keys(f"{fact.label} | {fact.metric}") if fact.kind == "table" else set()
        if other_period(fact):
            continue  # another year's value
        if _names_sibling(sheet, fact, claim):
            continue
        source_words = sheet.passage_stems(fact.passage_id)
        if claim and len(source_words) >= 6 and not claim & source_words:
            # The sentence's subject appears nowhere in the source ("passenger car sales" for a goods-vehicle
            # row). A source that is a bare grid of numbers has no words to compare, so it cannot judge.
            continue
        owner = _owner_stems(fact)
        if claim and owner and not claim & owner and _competing_columns(sheet, fact):
            # The value belongs to a named row/column ("2 Uttar Pradesh 2,78,178") the sentence does not
            # name. A single-column table ("Year | India") has nothing to confuse it with.
            continue
        same_period = bool(claim_period and claim_period in fact_periods and (cited or digits >= 3))
        specific = not _year_like(raw) and (digits >= 4 + (0 if cited else 1) or (decimal and digits >= 3 + (0 if cited else 1)))
        if same_period or specific:
            return Lookup("ok", fact)
    return Lookup("metric_mismatch", sign_ok[0])


_PERIOD_KEY_RE = re.compile(
    r"(?i)\b(?:(?P<q>[QH][1-4])\s*(?:FY|CY)?\s*'?(?P<qy>(?:19|20)?\d{2}))\b"
    r"|\b(?:FY\s*'?)?(?P<a>(?:19|20)?\d{2})\s*[-/–]\s*(?P<b>\d{2,4})\b"
    r"|\bFY\s*'?(?P<fy>(?:19|20)?\d{2})\b"
    r"|\b(?P<y>(?:19|20)\d{2})\b")


def _period_matches(text: str):
    for match in _PERIOD_KEY_RE.finditer(text or ""):
        if match.group("q"):
            year = match.group("qy")
            key = f"{match.group('q').upper()} {('20' + year) if len(year) == 2 else year}"
        elif match.group("a"):
            start, end = match.group("a"), match.group("b")
            if len(start) == 2 and not re.match(r"(?i)\s*FY", match.group(0)):
                continue  # "12-15" is not a period without an FY prefix
            start = ("20" + start) if len(start) == 2 else start
            end = end[-2:]
            if int(end) != (int(start[-2:]) + 1) % 100:
                continue  # "2018-2024" is a range, "10-12" a count; a fiscal year runs into the next
            key = f"{start}-{end}"
        elif match.group("fy"):
            year = match.group("fy")
            key = f"FY{('20' + year) if len(year) == 2 else year}"
        else:
            key = match.group("y")
        yield match.start(), key


def _period_keys(text: str) -> set[str]:
    return {key for _pos, key in _period_matches(text)}


def _nearest_period(sentence: str, position: int) -> str:
    if position < 0:
        return ""
    best, distance = "", 10 ** 9
    for pos, key in _period_matches(sentence):
        gap = abs(pos - position)
        if gap < distance:
            best, distance = key, gap
    return best


def _number_position(sentence: str, raw: str) -> int:
    match = re.search(r"(?<![\d.,])" + re.escape(raw.lstrip("+-−–")) + r"(?![\d])", sentence or "")
    return match.start() if match else -1


def _significant_digits(raw: str) -> int:
    digits = re.sub(r"\D", "", raw.split(".")[0]) + (raw.split(".")[1] if "." in raw else "")
    return len(digits.strip("0"))


def _year_like(raw: str) -> bool:
    plain = raw.replace(",", "").lstrip("+-−–")
    return bool(re.fullmatch(r"(?:19|20)\d{2}", plain))


def _competing_columns(sheet: "FactSheet", fact: Fact) -> bool:
    """Whether the fact's source has other values it could be confused with: prose (always), or a table
    with more than one value column or more than one row label that is not a period."""
    if fact.kind != "table":
        return True
    columns = {f.metric for f in sheet.facts if f.passage_id == fact.passage_id and f.kind == "table" and f.metric}
    rows = {f.label for f in sheet.facts if f.passage_id == fact.passage_id and f.kind == "table"
            and f.label and not _period_keys(f.label)}
    return len(columns) > 1 or len(rows) > 1


_TOTAL_STEMS = {"total", "all", "overa", "grand", "world", "tt", "wt", "wtwt", "aat"}


def _owner_stems(fact: Fact) -> set[str]:
    """Whose value this is: a table row's label (or, in a year-first table, its column), or in flattened
    text the name written just before the number ("... 2 Uttar Pradesh 2,78,178"). Totals have no owner."""
    if fact.kind == "table":
        name = fact.label if re.search(r"[A-Za-z]{3,}", fact.label or "") and not _period_keys(fact.label) else fact.metric
    else:
        tail = re.split(r"\d", fact.metric or "")[-1]
        run = re.findall(r"(?:[A-Z][a-z]{2,}(?:\s+|$))+", tail + " ")
        name = run[-1] if run else ""
    stems = _stems(name) - {"conti", "table"}
    return set() if not stems or stems <= _TOTAL_STEMS else stems


def _names_sibling(sheet: "FactSheet", fact: Fact, claim: set[str]) -> bool:
    """The claim names another row or column of the fact's table, and not the fact's own row/column."""
    if fact.kind != "table":
        return False
    own = _stems(f"{fact.label} {fact.metric}")
    if claim & own:
        return False
    for name in sheet.table_names.get(fact.passage_id, ()):
        if name in {fact.label, fact.metric} or _period_keys(name) and len(_stems(name)) <= 1:
            continue
        if claim & (_stems(name) - own):
            return True
    return False


# ── calculations in code ─────────────────────────────────────────────────────
CALC_RE = re.compile(r"\[\[\s*calc\s*:\s*([^\]]+)\]\]", re.I)


_QTY_RE = re.compile(
    r"\s*\(?\s*(?P<sign>[-+−])?\s*(?P<cur>₹|Rs\.?|INR|USD|US\$|\$|€|£)?\s*(?P<num>\d[\d,]*(?:\.\d+)?)\s*"
    r"(?P<pct>%|per\s?cent)?\s*(?P<word>[A-Za-z]{1,12})?\s*\)?\s*", re.I)
_SCALE_WORDS = {"trillion": 1e12, "tn": 1e12, "t": 1e12, "billion": 1e9, "bn": 1e9, "b": 1e9, "million": 1e6, "mn": 1e6,
                "m": 1e6, "crore": 1e7, "crores": 1e7, "cr": 1e7, "lakh": 1e5, "lakhs": 1e5, "thousand": 1e3, "k": 1e3}


def _quantity(text: str) -> dict | None:
    """'USD 100 billion' -> number '100', value 1e11, currency 'usd', unit ''; '160.9%' -> pct. None if not one quantity."""
    match = _QTY_RE.fullmatch(text or "")
    if not match:
        return None
    word = (match.group("word") or "").casefold()
    scale = _SCALE_WORDS.get(word, 1.0)
    unit = "" if word in _SCALE_WORDS else word
    currency = (match.group("cur") or "").casefold().replace("us$", "$").replace("usd", "$").rstrip(".")
    currency = {"rs": "₹", "inr": "₹"}.get(currency, currency)
    try:
        number = float(match.group("num").replace(",", ""))
    except ValueError:
        return None
    sign = -1.0 if match.group("sign") in {"-", "−"} else 1.0
    return {"raw": match.group("num"), "value": sign * number * scale, "scale": scale, "scale_word": word if scale != 1.0 else "",
            "currency": currency, "unit": unit, "pct": bool(match.group("pct")),
            "decimals": len(match.group("num").split(".")[1]) if "." in match.group("num") else 0}


def _with_units(value: float, like: dict) -> str:
    """Write ``value`` (base units) in the currency / scale / unit of the operand ``like``."""
    shown = value / (like["scale"] or 1.0)
    number = f"{shown:,.{like['decimals']}f}"
    scale_word = {"b": "billion", "bn": "billion", "t": "trillion", "tn": "trillion", "m": "million", "mn": "million",
                  "k": "thousand", "cr": "crore"}.get(like["scale_word"], like["scale_word"])
    currency = {"$": "$", "₹": "₹", "€": "€", "£": "£"}.get(like["currency"], "")
    tail = " ".join(x for x in (scale_word, like["unit"].upper() if like["unit"] in {"gw", "mw", "mmt", "gwh", "twh"} else like["unit"]) if x)
    return f"{currency}{number}{(' ' + tail) if tail else ''}{'%' if like['pct'] else ''}"


def _compute_with_units(expression: str, sheet: FactSheet, passage_ids: set[str] | None) -> tuple[str, str] | None:
    """The unit-aware path of :func:`compute`; None hands the expression to the plain-number path."""
    expr = re.sub(r"\s*[*×]\s*100\s*$", "", expression.strip())
    expr = re.sub(r"\(\s*([-−]\s*[\d.,]+\s*%?)\s*\)", r"\1", expr)  # "29.90 - (-0.73)"
    if not re.search(r"%|per\s?cent|[₹$€£]|\b(?:USD|INR|Rs\.?|US)\b|\d\s*[A-Za-z]|[-−]\s*\d[\d,.]*\s*$|[-−]\s*[-−]", expr, re.I):
        if re.fullmatch(r"\s*\d[\d,]*(?:\.\d+)?\s*", expr) and sheet.candidates(expr.strip(), passage_ids):
            return expr.strip(), ""  # "[[calc: 7.3]]": the reported value itself
        return None

    def fact(q: dict) -> bool:
        return bool(sheet.candidates(q["raw"], passage_ids))

    def change(a_text: str, b_text: str) -> tuple[str, str] | None:
        a, b = _quantity(a_text), _quantity(b_text)
        if not (a and b and fact(a) and fact(b)) or not a["value"]:
            return None
        if a["currency"] != b["currency"] or a["unit"] != b["unit"]:
            return None
        return f"{100 * (b['value'] - a['value']) / abs(a['value']):.1f}%", f"change from {a_text.strip()} to {b_text.strip()}"

    growth = re.fullmatch(r"\(\s*(.+?)\s+[-−]\s+(.+?)\s*\)\s*[/÷]\s*(.+)", expr)
    if growth and _quantity(growth.group(2)) and _quantity(growth.group(3)) and \
            abs(_quantity(growth.group(2))["value"] - _quantity(growth.group(3))["value"]) < 1e-9:
        return change(growth.group(2), growth.group(1))
    moved = re.fullmatch(r"(?i)change\s+from\s+(.+?)\s+to\s+(.+)", expr)
    if moved:
        return change(moved.group(1), moved.group(2))

    parts = re.split(r"\s+([+\-−÷/×*])\s+", expr)
    operands, ops = parts[0::2], parts[1::2]
    quantities = [_quantity(o) for o in operands]
    if any(q is None for q in quantities):
        return None
    if len(quantities) == 1:
        return (operands[0].strip(), "") if fact(quantities[0]) else None
    if not all(fact(q) for q in quantities):
        return None
    label = expression.strip()
    first = quantities[0]
    if all(op in {"+", "-", "−"} for op in ops):
        if len({(q["currency"], q["unit"], q["pct"]) for q in quantities}) != 1:
            return None
        total = quantities[0]["value"]
        for op, q in zip(ops, quantities[1:]):
            total = total + q["value"] if op == "+" else total - q["value"]
        if first["pct"]:
            decimals = max(q["decimals"] for q in quantities)
            return f"{total:.{decimals}f} percentage points", label
        return _with_units(total, {**first, "decimals": max(q["decimals"] for q in quantities)}), label
    if len(quantities) == 2 and ops[0] in {"/", "÷"}:
        a, b = quantities
        if not b["value"]:
            return None
        if a["currency"] == b["currency"] and a["unit"] == b["unit"] and a["pct"] == b["pct"]:
            return f"{100 * a['value'] / b['value']:.1f}%", label  # a share of a like quantity
        if a["currency"] and not b["currency"] and not b["pct"]:
            per = a["value"] / b["value"]
            return f"{a['currency']}{per:,.0f}" if abs(per) >= 100 else f"{a['currency']}{per:,.2f}", label
        return None
    if len(quantities) == 2 and ops[0] in {"*", "×"}:
        a, b = quantities
        plain = [q for q in (a, b) if not (q["currency"] or q["unit"] or q["pct"] or q["scale"] != 1.0)]
        if len(plain) != 1:
            return None
        other = b if plain[0] is a else a
        return _with_units(a["value"] * b["value"] / (1.0 if other is b else 1.0), other), label
    return None


def compute(expression: str, sheet: FactSheet, passage_ids: set[str] | None) -> tuple[str, str] | None:
    """'47,955 + 21,745 + 14,116' -> ('83,816', '47,955 + 21,745 + 14,116') when every operand is a fact.

    Operands may carry their units: "160.9% - 124.4%" (percentage points), "USD 100 billion - USD 12 billion",
    "change from $140B to $217B", "$217B / $1.20T" (a share), "₹12.12 trillion / 7.31 billion" (per unit),
    "29.90 - (-0.73)". Every operand's number must still be a fact; only the unit reading is new (the
    benchmark found 92 of 106 refused requests had facts for operands and failed only on their units).
    A single number ("[[calc: 7.3]]") that is a fact is returned as written, with an empty label.
    """
    result = _compute_with_units(expression, sheet, passage_ids)
    if result is not None:
        return result
    expr = expression.strip()
    # "(560.4 - 268.8) / 268.8" (optionally "* 100") is the percent change from 268.8 to 560.4:
    # the writer's usual way of asking for growth, which used to be refused as unsupported.
    growth = re.fullmatch(r"\(\s*(\S+)\s*[-−]\s*(\S+)\s*\)\s*[/÷]\s*(\S+?)(?:\s*[*×]\s*100)?", expr)
    if growth and _plain(growth.group(2).lstrip("+")) == _plain(growth.group(3).lstrip("+")):
        expr = f"change from {growth.group(2)} to {growth.group(1)}"
    change = re.fullmatch(r"(?i)change\s+from\s+(\S+)\s+to\s+(\S+)", expr)
    if change:
        a, b = change.group(1), change.group(2)
        if not (sheet.candidates(a, passage_ids) and sheet.candidates(b, passage_ids)):
            return None
        va, vb = float(_plain(a.lstrip("+"))), float(_plain(b.lstrip("+")))
        if not va:
            return None
        return f"{100 * (vb - va) / abs(va):.1f}%", f"change from {a} to {b}"
    parts = re.split(r"\s*([+\-−÷/×*])\s*", expr)
    operands, ops = parts[0::2], parts[1::2]
    if len(operands) < 2 or any(not sheet.candidates(o, passage_ids) for o in operands):
        return None
    values = [float(_plain(o.lstrip("+"))) for o in operands]
    decimals = max((len(o.split(".")[1]) if "." in o else 0) for o in operands)
    if all(op in {"+", "-", "−"} for op in ops):
        total = values[0]
        for op, v in zip(ops, values[1:]):
            total = total + v if op == "+" else total - v
        text = f"{total:,.{decimals}f}"
    elif len(values) == 2 and ops[0] in {"÷", "/"}:
        if not values[1]:
            return None
        text = f"{100 * values[0] / values[1]:.1f}%"
    elif len(values) == 2 and ops[0] in {"×", "*"}:
        text = f"{values[0] * values[1]:,.{decimals}f}"
    else:
        return None
    return text, " ".join(f"{o} {p}" for o, p in zip(operands, ops + [""])).strip()


def apply_calculations(body: str, sheet: FactSheet, passage_ids: set[str] | None) -> tuple[str, list[dict], list[str]]:
    """Replace the writer's [[calc: ...]] requests with computed values; a request on non-facts is removed."""
    done, failed = [], []

    def repl(match: re.Match) -> str:
        result = compute(match.group(1), sheet, passage_ids)
        tail = match.group("tail") or ""
        if result is None:
            failed.append(match.group(1))
            return "[[UNSUPPORTED_CALC]]" + tail
        value, inputs = result
        # The writer often adds the unit itself: "[[calc: ...]]%" or "[[calc: a% - b%]] percentage points".
        # Keep one copy of it, before the label.
        if tail.strip() and re.match(r"(?i)\s*(?:%|per\s?cent\b)", tail) and value.endswith("%"):
            value = value[:-1]
        if re.match(r"(?i)\s*(?:percentage\s+points?|pp\b)", tail) and value.endswith(" percentage points"):
            value = value[: -len(" percentage points")]
        if not inputs:
            return f"{value}{tail}"  # a reported value the writer wrapped in [[calc]]: no calculation label
        done.append({"value": value, "inputs": inputs})
        return f"{value}{tail} (calculated: {inputs})"

    pattern = re.compile(CALC_RE.pattern + r"(?P<tail>\s?(?:%|per\s?cent\b|percentage\s+points?\b|pp\b))?", CALC_RE.flags)
    return pattern.sub(repl, body), done, failed
