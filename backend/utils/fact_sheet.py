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


def _stems(text: str) -> set[str]:
    words = re.findall(r"[A-Za-z][A-Za-z&]+", text or "")
    out = set()
    for raw in words:
        word = raw.casefold()
        if raw.isupper() and 2 <= len(raw) <= 5:
            out.add(word)
        elif len(word) >= 4 and word not in _STOP:
            if word.endswith("s") and len(word) > 4 and not word.endswith("ss"):
                word = word[:-1]
            out.add(word[:5])
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


def extract_facts(passage) -> list[Fact]:
    text = str(passage.text or "")
    pid = passage.passage_id
    facts: list[Fact] = []
    lines = text.splitlines()
    header: list[str] = []
    header_units: list[str] = []
    space_header: list[str] = []
    heading = next((ln.strip() for ln in lines if ln.strip() and " | " not in ln), "")
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            header, space_header = [], []
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if " | " in stripped and (stripped.startswith("|") or len(cells) >= 3) and max(len(c) for c in cells) <= 160:
            if re.fullmatch(r"[\s|:-]+", stripped):
                continue  # Markdown separator row
            following = lines[index + 1].strip() if index + 1 < len(lines) else ""
            if (not any(re.search(r"\d", c) for c in cells[1:])
                    or re.fullmatch(r"\|?[\s|:-]*-{3,}[\s|:-]*", following)
                    or (not header and not any(_VALUE_CELL_RE.fullmatch(c) for c in cells[1:]))):
                # The table's header (a row without values, or the row above the separator):
                # column names ("4-week return (%)" has digits but is a name) and their units.
                header = cells
                header_units = [unit_family(re.sub(r"^.*?\(", "", c)) if "(" in c else ("%" if "%" in c else "") for c in cells]
                continue
            # A rank / serial-number first column is not the row's entity: the next text cell is.
            label = next((c for c in cells if c and not re.fullmatch(r"[#\d.\s]+", c)), cells[0])
            for col, cell in enumerate(cells[1:], 1):
                for match in _NUM_RE.finditer(cell):
                    unit = unit_family(cell[match.end():]) or (header_units[col] if col < len(header_units) else "")
                    metric = header[col] if col < len(header) else ""
                    facts.append(_fact(pid, len(facts), match, unit, metric, _entity_in(label) or label,
                                       _period(metric) or _period(label), f"{label} {metric} {heading}"))
                    facts[-1].kind, facts[-1].label = "table", label.strip()[:60]
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
        for sentence in re.split(r"(?<=[.!?;])\s+", stripped):
            for match in _NUM_RE.finditer(sentence):
                if re.fullmatch(r"(?:19|20)\d{2}", match.group(2)) and not match.group(1):
                    continue
                unit = unit_family(sentence[match.end():])
                before = sentence[:match.start()]
                facts.append(_fact(pid, len(facts), match, unit, " ".join(before.split()[-6:]), _entity_in(before),
                                   _period(sentence), sentence))
    return facts


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
                stems=_stems(f"{metric} {entity} {context}"))


class FactSheet:
    def __init__(self, passages):
        self.facts: list[Fact] = []
        for passage in passages:
            self.facts.extend(extract_facts(passage))
        self.by_value: dict[str, list[Fact]] = {}
        for fact in self.facts:
            self.by_value.setdefault(fact.value.lstrip("-"), []).append(fact)

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
    for fact in sign_ok:
        if not fact.stems or claim & fact.stems:
            return Lookup("ok", fact)
    for fact in sign_ok:
        if context & fact.stems and fact.entity:
            return Lookup("entity_missing", fact)  # right value; the sentence does not name its entity
    return Lookup("metric_mismatch", sign_ok[0])


# ── calculations in code ─────────────────────────────────────────────────────
CALC_RE = re.compile(r"\[\[\s*calc\s*:\s*([^\]]+)\]\]", re.I)


def compute(expression: str, sheet: FactSheet, passage_ids: set[str] | None) -> tuple[str, str] | None:
    """'47,955 + 21,745 + 14,116' -> ('83,816', '47,955 + 21,745 + 14,116') when every operand is a fact."""
    expr = expression.strip()
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
        if result is None:
            failed.append(match.group(1))
            return "[[UNSUPPORTED_CALC]]"
        value, inputs = result
        done.append({"value": value, "inputs": inputs})
        return f"{value} (calculated: {inputs})"

    return CALC_RE.sub(repl, body), done, failed
