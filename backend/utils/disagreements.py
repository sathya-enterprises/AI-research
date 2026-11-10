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

_CITE_RE = re.compile(r"\[(S\d+(?:\.\d+)?(?:\s*[,;]\s*S\d+(?:\.\d+)?)*)\]")
_PERIOD_RE = re.compile(
    r"(?i)\b(?:Q[1-4]\s*(?:FY|CY)?\s*'?\d{2,4}|H[12]\s*(?:FY|CY)?\s*'?\d{2,4}|FY\s*'?\d{2}(?:-\d{2})?|CY\s*'?\d{2,4}|"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+(?:19|20)\d{2}|(?:19|20)\d{2})\b")
_AMOUNT_RE = re.compile(
    r"(?P<cur>₹|Rs\.?\s?|INR\s?|US\$|\$|€|£)?\s?(?P<num>[-+−]?\d[\d,]*(?:\.\d+)?)\s?"
    r"(?P<unit>lakh crore|crore|cr\b|trillion|tn\b|billion|bn\b|million|mn\b|%|per cent)?", re.I)
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
    stripped = sentence.lstrip()
    for name in re.findall(r"\b[A-Z][A-Za-z&]+(?:\s+[A-Z][A-Za-z&]+)*", sentence):
        if name in defined or any(name.endswith(d) and len(d) > 6 for d in defined) or name in {d for d in defined if d.isupper()}:
            continue
        parts = [x for x in name.split() if x not in _NOT_ENTITY]
        if not parts or (stripped.startswith(name) and len(parts) == 1):
            continue
        out.add(" ".join(parts).casefold())
    return out


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
        for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z])", line):
            cites = [c.strip() for g in _CITE_RE.findall(sentence) for c in re.split(r"\s*[,;]\s*", g)]
            plain = _CITE_RE.sub("", sentence)
            periods = {re.sub(r"\s+", " ", p.casefold()) for p in _PERIOD_RE.findall(plain)}
            if len(periods) != 1:
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
                family = "%" if unit in {"%", "per cent"} else _FAMILY.get(cur, "")
                if not family:
                    continue
                base = value if family == "%" else value * _SCALE.get(unit, 1.0)
                before = re.findall(r"[A-Za-z][A-Za-z-]{3,}", plain[max(0, m.start() - 70):m.start()])[-6:]
                words = {_stem(w) for w in before if w.casefold() not in _STOP}
                if len(words) < 2:
                    continue
                out.append({"period": period, "family": family, "value": base, "shown": m.group(0).strip(),
                            "words": words, "entities": names, "cites": cites, "section": section,
                            "sentence": plain.strip()[:200]})
    return out


def find_disagreements(report: str, *, max_items: int = 6) -> list[dict]:
    items = statements(report)
    found, seen = [], set()
    for i, a in enumerate(items):
        for b in items[i + 1:]:
            if a["period"] != b["period"] or a["family"] != b["family"] or not a["value"] or not b["value"]:
                continue
            if (a["value"] > 0) != (b["value"] > 0) and a["family"] != "%":
                continue  # an inflow and an outflow are different measures
            if a["entities"] != b["entities"]:
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
            key = (a["period"], round(lo, -3), round(hi, -3))
            if key in seen:
                continue
            seen.add(key)
            found.append({"period": a["period"], "measure": " ".join(sorted(shared))[:60],
                          "a": {"shown": a["shown"], "cites": a["cites"], "section": a["section"]},
                          "b": {"shown": b["shown"], "cites": b["cites"], "section": b["section"]}})
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
