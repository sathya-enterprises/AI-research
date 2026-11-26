"""Stage M: where sources disagree - for any topic.

The report states numbers from different publishers. When two sentences give the SAME measure for the SAME
entity and period with materially different amounts (after unit conversion: "₹1.8 trillion" vs
"₹1,52,692 crore"), the reader must be told, not left to notice. Detection is generic:

  * a statement = (period label, amount in a common base, currency / percent family, the content words just
    before the number, the capitalised names in the sentence, citations);
  * two statements disagree when period and family match, they do not name different entities, their
    measure words overlap (>= 2 shared words and >= 50% of the smaller set), they come from different
    citations, and the amounts differ by more than 15%;
  * a bare year with a partial-period phrase ("the first four months of 2026") is not that year;
  * the methodology gets a short "Where sources disagree" list (both values, both citations).
"""
from __future__ import annotations

import re
from utils.evidence_text import SENTENCE_END  # one sentence-boundary rule for every checker

_CITE_RE = re.compile(r"\[(S\d+(?:\.\d+)?(?:\s*[,;]\s*S\d+(?:\.\d+)?)*)\]")
_PERIOD_RE = re.compile(
    r"(?i)\b(?:Q[1-4]\s*(?:FY|CY)?\s*'?\d{2,4}|H[12]\s*(?:FY|CY)?\s*'?\d{2,4}|FY\s*'?\d{2}(?:-\d{2})?|CY\s*'?\d{2,4}|"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+(?:19|20)\d{2}|(?:19|20)\d{2})\b")
_AMOUNT_RE = re.compile(
    r"(?P<cur>₹|Rs\.?\s?|INR\s?|US\$|\$|€|£)?\s?(?P<num>[-+−]?\d[\d,]*(?:\.\d+)?)\s?"
    r"(?P<unit>lakh crore|crore|cr\b|trillion|tn\b|billion|bn\b|million tonnes|million|mn\b|%|per cent|"
    r"gwh\b|twh\b|mwh\b|gw\b|mw\b|tonnes\b|mtpa\b)?", re.I)
# Physical measures compare within their own family, in one base unit (116 GW vs 118,000 MW is a comparison).
_PHYSICAL = {"gw": ("power", 1.0), "mw": ("power", 1e-3), "gwh": ("energy", 1.0), "twh": ("energy", 1e3),
             "mwh": ("energy", 1e-3), "tonnes": ("mass", 1.0), "million tonnes": ("mass", 1e6), "mtpa": ("mass", 1e6)}
_PARTIAL_RE = re.compile(r"(?i)\b(?:first|last|months?|half|quarter|fortnight|weeks?|so far|ytd|year-to-date|till|until|"
                         r"through|by (?:end|mid)|since)\b")
_STOP = {"the", "and", "with", "from", "that", "this", "were", "was", "are", "into", "over", "during", "which", "while",
         "their", "its", "for", "than", "about", "approximately", "around", "nearly", "roughly", "total", "recorded",
         "reached", "stood", "worth", "rose", "fell", "grew", "saw", "had", "has", "have", "been", "also", "only", "just",
         "some", "such", "these", "those", "same", "year", "years", "period", "month", "quarter", "fiscal", "calendar"}
_NOT_ENTITY = {"The", "In", "On", "At", "By", "For", "From", "During", "While", "This", "That", "These", "Those", "Over",
               "Despite", "However", "Meanwhile", "As", "With", "Between", "After", "Before", "When", "Although", "According",
               "January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
               "November", "December", "Rs", "INR", "US", "FY", "CY", "QTD", "YTD", "YoY", "QoQ"}
_LEADING_ENTITY_PREDICATES = {
    "began", "declined", "expanded", "fell", "grew", "increased", "led", "maintained", "overtook",
    "posted", "ranked", "reached", "recorded", "remained", "reported", "rose", "saw", "stood",
    "surpassed", "experienced", "registered", "continued", "ended", "averaged",
}
_SCALE = {"lakh crore": 1e12, "crore": 1e7, "cr": 1e7, "trillion": 1e12, "tn": 1e12, "billion": 1e9, "bn": 1e9,
          "million": 1e6, "mn": 1e6}
_FAMILY = {"₹": "inr", "rs": "inr", "inr": "inr", "us$": "usd", "$": "usd", "€": "eur", "£": "gbp"}


def _stem(w: str) -> str:
    w = w.casefold()
    for suf in ("ings", "ing", "ies", "es", "s", "ed"):
        if len(w) > 5 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def _entities(sentence: str) -> set[str]:
    """Capitalised names in the sentence (not its first word, months or units): who the number is about."""
    out = set()
    # "Assets Under Custody (AUC)": a defined abbreviation and the phrase it abbreviates name a MEASURE
    defined = set()
    for m in re.finditer(r"((?:[A-Z][A-Za-z]+\s+){1,5})\(([A-Z]{2,6})\)", sentence):
        defined.add(m.group(2))
        defined.add(m.group(1).strip())
    sentence = re.sub(r"\(([A-Z]{2,6})\)", " ", sentence)
    for match in re.finditer(r"\b[A-Z][A-Za-z&]+(?:\s+[A-Z][A-Za-z&]+)*", sentence):
        name = match.group(0)
        if name in defined or any(name.endswith(d) and len(d) > 6 for d in defined) or name in {d for d in defined if d.isupper()}:
            continue
        parts = [x for x in name.split() if x not in _NOT_ENTITY]
        # Grammar words are filtered above; a sentence-initial proper noun is
        # still its subject (for example, "Brazil ..." or "Kenya ...").
        if not parts:
            continue
        if sentence[:match.start()].strip() == "" and len(parts) == 1 and len(name.split()) == 1:
            tail = sentence[match.end():]
            if not re.match(r"(?i)^(?:['\u2019]s\b|,|\s+(?:" + "|".join(sorted(_LEADING_ENTITY_PREDICATES)) + r")\b)", tail):
                continue  # don't turn a capitalized sentence-initial metric noun into an entity
        out.add(" ".join(parts).casefold())
    return out


# Clause boundaries inside a sentence: a list "84% in Brazil, 65% in India and 35% in Kenya" gives each
# number its own subject. Thousands separators ("1,824") are not boundaries (no space after the comma).
_CLAUSE_SPLIT_RE = re.compile(r",\s+|;\s*|\s+(?:and|while|whereas|but|versus|vs\.?|compared with|against)\s+", re.I)


def _clause_entities(plain: str, names: set[str], start: int, end: int) -> set[str]:
    """The sentence's named entities that appear in the clause holding the amount at [start, end).

    A sentence-level entity set made every number in a multi-country list "about" every country, so
    Brazil's 84% was compared with Kenya's 35% as the same measure. When the clause names nobody, the
    sentence's subject applies only if the sentence names exactly one entity.
    """
    bounds = [0] + [m.end() for m in _CLAUSE_SPLIT_RE.finditer(plain)] + [len(plain)]
    clause_start = max(b for b in bounds if b <= start)
    clause_end = min((m.start() for m in _CLAUSE_SPLIT_RE.finditer(plain) if m.start() >= end), default=len(plain))
    clause = plain[clause_start:clause_end].casefold()
    local = {name for name in names if re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", clause)}
    if local:
        return local
    return set(names) if len(names) == 1 else set()


def statements(report: str) -> list[dict]:
    out = []
    body = re.split(r"(?im)^#{1,2}\s+(?:Methodology|Appendix)\b", str(report or ""))[0]
    section = ""
    for line in body.splitlines():
        if line.startswith("#"):
            section = line.lstrip("# ").strip()
            continue
        if line.lstrip().startswith(("|", ">", "!")):
            continue
        for sentence in re.split(SENTENCE_END + r"\s+(?=[A-Z])", line):
            cites = [c.strip() for g in _CITE_RE.findall(sentence) for c in re.split(r"\s*[,;]\s*", g)]
            plain = _CITE_RE.sub("", sentence)
            periods = {re.sub(r"\s+", " ", p.casefold()) for p in _PERIOD_RE.findall(plain)}
            if len(periods) != 1:
                continue
            if re.search(r"(?i)\b(?:19|20)\d0s\b", plain):
                # "around 40% in the mid-1980s ... 7.4% by 2024": a decade is a second period, so the
                # sentence's values cannot all be dated to its one year (a false "sources disagree").
                continue
            period = next(iter(periods))
            if re.fullmatch(r"(?:19|20)\d{2}", period) and _PARTIAL_RE.search(plain):
                continue  # "in the first four months of 2026" is not the year 2026
            names = _entities(plain)
            if not names and ":" in section:  # "the sector" in "Realty: ..." - the section's subject is implied
                names = {section.split(":", 1)[0].strip().casefold()}
            for m in _AMOUNT_RE.finditer(plain):
                unit = (m.group("unit") or "").casefold()
                cur = (m.group("cur") or "").strip().casefold().rstrip(".")
                if not unit and not cur:
                    continue
                if re.fullmatch(r"(?:19|20)\d{2}", m.group("num")):
                    continue
                try:
                    value = float(m.group("num").replace(",", "").replace("−", "-"))
                except ValueError:
                    continue
                family = "%" if unit in {"%", "per cent"} else (_PHYSICAL[unit][0] if unit in _PHYSICAL else _FAMILY.get(cur, ""))
                if not family:
                    continue
                base = value if family == "%" else (value * _PHYSICAL[unit][1] if unit in _PHYSICAL
                                                    else value * _SCALE.get(unit, 1.0))
                before = re.findall(r"[A-Za-z][A-Za-z-]{3,}", plain[max(0, m.start() - 70):m.start()])[-6:]
                words = {_stem(w) for w in before if w.casefold() not in _STOP}
                if len(words) < 2:
                    continue
                out.append({"period": period, "family": family, "value": base, "shown": m.group(0).strip(),
                            # anchor on the number itself: the match can begin with the space before it
                            "words": words, "entities": _clause_entities(plain, names, m.start("num"), m.end()),
                            "cites": cites, "section": section,
                            "sentence": plain.strip()[:200]})
    return out


def _numbered_names(sentence: str) -> dict[str, str]:
    """Names carrying a number of their own: {'nifty': '50'} from 'Nifty 50', {'nifty': '500'} from 'Nifty 500'."""
    return {m.group(1).casefold(): m.group(2)
            for m in re.finditer(r"\b([A-Z][A-Za-z&.-]+)\s+(\d{1,4})\b(?!\s*(?:%|per|crore|lakh|billion|million|trillion|GW|MW))",
                                 str(sentence or ""))
            if not re.fullmatch(r"(?:19|20)\d{2}", m.group(2))}


def find_disagreements(report: str, *, max_items: int = 6, strict: bool = False) -> list[dict]:
    """``strict``: only pairs safe to show the reader inside a section - the same named subjects exactly, two
    different sentences, mostly the same measure words, and a gap of 1-33% (116 GW vs 118 GW is a disagreement;
    exports to the UK vs exports to all FTA partners, or a sector's slice of a total, are different measures)."""
    items = statements(report)
    found, seen = [], set()
    for i, a in enumerate(items):
        for b in items[i + 1:]:
            if a["period"] != b["period"] or a["family"] != b["family"] or not a["value"] or not b["value"]:
                continue
            if strict:
                if a["sentence"] == b["sentence"] or not a["entities"] or a["entities"] != b["entities"]:
                    continue
                na, nb = _numbered_names(a["sentence"]), _numbered_names(b["sentence"])
                if any(na[k] != nb[k] for k in set(na) & set(nb)):
                    continue  # "Nifty 50" and "Nifty 500" are different subjects
                common = a["words"] & b["words"]
                if len(common) < 2 or len(common) < 0.7 * max(len(a["words"]), len(b["words"])):
                    continue
                if a["cites"] and b["cites"] and set(a["cites"]) & set(b["cites"]):
                    continue
                hi, lo = max(abs(a["value"]), abs(b["value"])), min(abs(a["value"]), abs(b["value"]))
                if not hi or not 0.01 <= (hi - lo) / hi <= 0.33 or (a["value"] > 0) != (b["value"] > 0):
                    continue
                key = (a["period"], round(lo, 4), round(hi, 4))
                if key not in seen:
                    seen.add(key)
                    found.append({"period": a["period"], "measure": " ".join(sorted(common))[:60],
                                  "a": {"shown": a["shown"], "cites": a["cites"], "section": a["section"], "sentence": a["sentence"]},
                                  "b": {"shown": b["shown"], "cites": b["cites"], "section": b["section"], "sentence": b["sentence"]}})
                    if len(found) >= max_items:
                        return found
                continue
            if not a["entities"] or not b["entities"]:
                continue  # without a named subject, the same entity cannot be established
            if (a["value"] > 0) != (b["value"] > 0) and a["family"] != "%":
                continue  # an inflow and an outflow are different measures
            if not (a["entities"] & b["entities"]):
                continue  # only the SAME named subjects can disagree (Realty's inflow is not the market total;
                #           a wrong "sources disagree" note is worse than none)
            shared = a["words"] & b["words"]
            if len(shared) < 2 or len(shared) < 0.5 * min(len(a["words"]), len(b["words"])):
                continue
            if a["cites"] and b["cites"] and set(a["cites"]) & set(b["cites"]):
                continue  # the same source: two figures there are two measures, not a disagreement
            hi, lo = max(abs(a["value"]), abs(b["value"])), min(abs(a["value"]), abs(b["value"]))
            if hi == 0 or (hi - lo) / hi <= 0.15:
                continue
            # Percentages keep their precision: rounding to thousands made every 2024 percentage pair the
            # same key, so the first (possibly false) pair hid every real disagreement after it.
            precision = 2 if a["family"] == "%" else -3
            key = (a["period"], round(lo, precision), round(hi, precision))
            if key in seen:
                continue
            seen.add(key)
            found.append({"period": a["period"], "measure": " ".join(sorted(shared))[:60],
                          "a": {"shown": a["shown"], "cites": a["cites"], "section": a["section"], "sentence": a["sentence"]},
                          "b": {"shown": b["shown"], "cites": b["cites"], "section": b["section"], "sentence": b["sentence"]}})
            if len(found) >= max_items:
                return found
    return found


def disagreement_note(items: list[dict]) -> str:
    if not items:
        return ""
    lines = ["**Where sources disagree.** The sources give different figures for the same measure and period; "
             "readers should check the definitions (gross or net, equity only or all assets, calendar or fiscal "
             "year) in the cited sources:"]
    for d in items:
        ca = f" [{', '.join(d['a']['cites'])}]" if d["a"]["cites"] else ""
        cb = f" [{', '.join(d['b']['cites'])}]" if d["b"]["cites"] else ""
        period = d["period"].upper() if re.match(r"(?i)(?:fy|cy|q|h)", d["period"]) else d["period"].title()
        lines.append(f"- {period}, {d['measure']}: {d['a']['shown']}{ca} vs {d['b']['shown']}{cb}.")
    return "\n".join(lines)


def _period_text(period: str) -> str:
    return period.upper() if re.match(r"(?i)(?:fy|cy|q|h)", period) else period.title()


def section_note(item: dict) -> str:
    """One line placed where the reader meets the second figure: both values, both citations, no verdict."""
    ca = f" [{', '.join(item['a']['cites'])}]" if item["a"]["cites"] else ""
    cb = f" [{', '.join(item['b']['cites'])}]" if item["b"]["cites"] else ""
    # rendered as a labelled note box (utils.layout_blocks NOTE admonition), not a decorative pull-quote
    return (f"> [!NOTE:Sources differ] For {_period_text(item['period'])}, one source gives {item['a']['shown']}{ca} and "
            f"another {item['b']['shown']}{cb}; the gap may reflect different definitions, coverage or dates.")


def _paragraph_with(body: str, sentence: str) -> int:
    """Index of the paragraph of ``body`` holding ``sentence`` (citations ignored), or -1."""
    probe = re.sub(r"\s+", " ", sentence).strip()[:70]
    if len(probe) < 25:
        return -1
    for i, para in enumerate(body.split("\n\n")):
        if probe in re.sub(r"\s+", " ", _CITE_RE.sub("", para)):
            return i
    return -1


def place_notes(sections: list, items: list[dict]) -> list[dict]:
    """Put each disagreement's note in the section where its second figure is stated, after that paragraph
    (both figures are kept: the reader sees 116 GW [S3] and 118 GW [S9] side by side). ``sections`` have a
    mutable ``body``. Returns [{"section", "period"}] for the notes placed."""
    placed = []
    for item in items:
        for side in ("b", "a"):
            target = next((sec for sec in sections if _paragraph_with(sec.body, item[side].get("sentence", "")) >= 0), None)
            if target is None:
                continue
            paras = target.body.split("\n\n")
            at = _paragraph_with(target.body, item[side]["sentence"])
            note = section_note(item)
            if note not in target.body:
                paras.insert(at + 1, note)
                target.body = re.sub(r"\n{3,}", "\n\n", "\n\n".join(p.strip("\n") for p in paras)).strip("\n") + "\n"
                placed.append({"section": getattr(target, "title", ""), "period": item["period"]})
            break
    return placed
