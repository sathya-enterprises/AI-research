"""What the question asks for (Stage F): windows, structures, deliverables, audience, comparisons.

Deterministic parsing of the client's question, so the outline can be checked against it:

  * window       "past 8 quarters" = the 8 completed quarters + the current quarter to date (QTD,
                 reported separately); "past 1 year" = the 12 months to today;
  * structure    "why, what, how" of each item ("all the FTAs") -> per-item subsections;
  * deliverable  "list down" / "exhaustive list of all ..." -> a table of items; "exhaustive" is
                 never claimed unless the evidence proves completeness (coverage note says so);
  * audience     "UHNIs" / "easy to understand" -> plain-language takeaways and stat strips;
  * comparison   "longer past" / "internationally" / "effective or vice-versa" -> comparison sections;
  * proportion / reasons / detailed reasoning -> a share (calculated in code) and a reasons section.

``map_asks`` maps each ask to the outline section(s) that answer it; ``repair_for_asks`` adds what is
missing (a section, or why/what/how subsections); ``coverage_note`` is the methodology paragraph.
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date

_NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
              "ten": 10, "eleven": 11, "twelve": 12}


@dataclass
class Ask:
    ask_id: str
    kind: str            # window | structure | deliverable | audience | comparison | analysis
    text: str            # what the client asked, in their words
    requirement: str     # what the report must contain
    terms: list[str] = field(default_factory=list)   # words a section answering it would use
    sections: list[str] = field(default_factory=list)
    status: str = "unmapped"   # mapped | added | unmapped | not claimable

    def to_dict(self) -> dict:
        return {"id": self.ask_id, "kind": self.kind, "text": self.text, "requirement": self.requirement,
                "sections": self.sections, "status": self.status}


@dataclass
class Asks:
    asks: list[Ask]
    window_note: str = ""
    items: str = ""                 # "FTAs", "promoter exits": the things listed / analysed one by one
    per_item: list[str] = field(default_factory=list)   # ["why", "what", "how"]
    exhaustive_requested: bool = False

    def prompt_block(self) -> str:
        if not self.asks:
            return ""
        lines = ["THE QUESTION ASKS FOR (each item must be answered by a named section or subsection; say which in \"asks\"):"]
        lines += [f"- [{a.ask_id}] {a.requirement}" for a in self.asks]
        if self.per_item:
            lines.append(f"For EACH of the {self.items or 'items'}: one section (or subsection) with subsections "
                         + ", ".join(f'"{w.capitalize()}"' for w in self.per_item) + ".")
        if self.exhaustive_requested:
            lines.append("The client asked for an exhaustive list: plan a table of every item the sources name, and a note on "
                         "how complete it is. Never call the list exhaustive or complete unless a source states it is complete.")
        return "\n".join(lines) + "\n"

    def to_dict(self) -> dict:
        return {"asks": [a.to_dict() for a in self.asks], "windowNote": self.window_note, "items": self.items,
                "perItem": self.per_item, "exhaustiveRequested": self.exhaustive_requested}


def _count(word: str) -> int:
    word = word.casefold()
    return int(word) if word.isdigit() else _NUM_WORDS.get(word, 0)


def _minus_months(day: date, months: int) -> date:
    month = day.month - months
    year = day.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def _fy_quarter(day: date) -> tuple[str, date]:
    """Indian FY quarter label and start date of the quarter containing ``day``."""
    q_start_month = ((day.month - 1) // 3) * 3 + 1
    start = date(day.year, q_start_month, 1)
    fy = day.year + 1 if day.month >= 4 else day.year
    q = {4: 1, 7: 2, 10: 3, 1: 4}[q_start_month]
    return f"Q{q} FY{fy % 100:02d}", start


def window_note(question: str, today: date) -> str:
    q = str(question or "")
    quarters = re.search(r"(?i)\b(?:past|last|previous)\s+(\d{1,2}|" + "|".join(_NUM_WORDS) + r")\s+quarters?\b", q)
    if quarters:
        label, start = _fy_quarter(today)
        return (f"CURRENT QUARTER TO DATE: {label} ({start:%b %d, %Y} - {today:%b %d, %Y}) is not one of the completed "
                f"quarters; report it separately, labelled \"{label} QTD (partial)\", never mixed into completed-quarter "
                "figures or rankings.")
    years = re.search(r"(?i)\b(?:past|last|previous)\s+(\d{1,2}|" + "|".join(_NUM_WORDS) + r")\s+years?\b", q)
    months = re.search(r"(?i)\b(?:past|last|previous)\s+(\d{1,2}|" + "|".join(_NUM_WORDS) + r")\s+months?\b", q)
    span = (_count(years.group(1)) * 12) if years else (_count(months.group(1)) if months else 0)
    if re.search(r"(?i)\b(?:past|last)\s+year\b", q) and not span:
        span = 12
    if span:
        start = _minus_months(today, span)
        return (f"TIME WINDOW: the {span} months to today, {start:%b %d, %Y} - {today:%b %d, %Y}. Items dated inside it are in "
                "scope; earlier ones are context or comparison only and must be labelled as such.")
    return ""


def parse_asks(question: str, today: date) -> Asks:
    q = " ".join(str(question or "").split())
    low = q.casefold()
    asks: list[Ask] = []

    def add(kind, text, requirement, terms):
        asks.append(Ask(f"A{len(asks) + 1}", kind, text, requirement, terms))

    note = window_note(q, today)
    if note:
        add("window", re.search(r"(?i)\b(?:past|last|previous)\s+\S+\s+(?:quarters?|years?|months?|year)\b", q).group(0),
            "the requested time window, with the current quarter to date shown separately" if "QTD" in note
            else "the requested time window (dates inside it in scope, older items labelled as context)",
            ["quarter", "qtd", "window", "period", "month", "year"])
    items = ""
    item_match = re.search(r"(?i)\ball['\"‘’]?(?:\s+the)?\s+['\"‘’]?([A-Za-z][A-Za-z\- ]{1,40}?)['\"‘’]?\s+(?:signed|by|in|of|made|announced|done|that|which|over|during|from)\b", q)
    if item_match:
        items = item_match.group(1).strip()
    per_item = [w for w in ("why", "what", "how") if re.search(rf"(?i)\b{w}\b", q)]
    if len(per_item) >= 2 and re.search(r"(?i)\bwhy\s*,?\s*what\s*,?\s*(?:and\s+)?how\b|\bwhat\s*,?\s*why\s*,?\s*(?:and\s+)?how\b", q):
        add("structure", f"the {', '.join(per_item)} of {items or 'each item'}",
            f"for each of the {items or 'items'}: {', '.join(w.capitalize() for w in per_item)} subsections",
            ["why", "what", "how", *(items.casefold().split() if items else [])])
    else:
        per_item = []
    exhaustive = bool(re.search(r"(?i)\b(?:exhaustive|complete|comprehensive)\s+list\b|\blist\s+(?:down\s+)?(?:of\s+)?['\"‘’]?all\b|\b'all'", q))
    if re.search(r"(?i)\blist\s+down\b|\blist\s+of\b|\bexhaustive\b", q):
        add("deliverable", "list" + (" (exhaustive)" if exhaustive else ""),
            f"a table listing each of the {items or 'items'} the sources name (with date, parties and source), "
            "and a statement of how complete it is", ["list", "table", "all", *(items.casefold().split() if items else [])])
    if re.search(r"(?i)\buhnis?\b|\bhnis?\b|\bhigh[- ]net[- ]worth\b|\beasy to understand\b|\bplain (?:english|language)\b", q):
        add("audience", "easy to understand by UHNIs" if "uhni" in low else "easy to understand",
            "plain-language takeaways for wealthy private investors: what it means for their portfolio, with key numbers "
            "in stat strips", ["implication", "investor", "portfolio", "takeaway", "uhni", "wealth"])
    if re.search(r"(?i)\blonger past\b|\bhistor(?:y|ical)\b|\bpast decade\b|\bearlier\b", q) and re.search(r"(?i)\bcompar", q):
        add("comparison", "comparison with the longer past", "a comparison with earlier agreements / periods (how the "
            "earlier ones performed), labelled as context outside the window", ["earlier", "history", "historical", "past", "previous", "comparison", "legacy"])
    if re.search(r"(?i)\binternational(?:ly)?\b|\bglobal(?:ly)?\b|\bother countries\b|\bpeers?\b", q):
        add("comparison", "international comparison", "an international comparison (how comparable agreements or "
            "markets elsewhere fared)", ["international", "global", "countries", "peer", "comparison", "world"])
    if re.search(r"(?i)\beffective(?:ness)?\b|\bvice[- ]versa\b|\boutcomes?\b|\bimpact\b", q):
        add("analysis", "effectiveness (or not)", "evidence of effectiveness: trade, utilisation or other outcome data, "
            "including where agreements under-delivered", ["effective", "utilisation", "utilization", "impact", "outcome", "trade", "export", "deficit"])
    if re.search(r"(?i)\bproportion\b|\bshare\b|\bpercentage\b", q):
        add("analysis", "proportion", "the proportion / share, calculated in code from reported figures", ["proportion", "share", "percent"])
    if re.search(r"(?i)\breasons?\b|\breasoning\b|\bwhy\b|\bdrivers?\b|\bexplain\b", q) and not per_item:
        add("analysis", "reasons", "the reasons / drivers, each backed by evidence", ["reason", "driver", "why", "motive", "because"])
    if re.search(r"(?i)\bsubstantial data\b|\bsupport (?:it )?with (?:data|numbers)\b", q):
        add("deliverable", "substantial data", "data tables and charts behind each main claim", ["data", "table", "figure"])
    return Asks(asks=asks, window_note=note, items=items, per_item=per_item, exhaustive_requested=exhaustive)


def _section_text(section) -> str:
    parts = [section.title, getattr(section, "goal", ""), *getattr(section, "queries", [])]
    for sub in getattr(section, "subsections", []) or []:
        parts += [sub.title, getattr(sub, "goal", ""), *getattr(sub, "queries", [])]
    return " ".join(parts).casefold()


def map_asks(asks: Asks, sections: list, llm_mapping: dict | None = None) -> None:
    """Each ask -> the sections that answer it: the outline's own "asks" mapping when it names real
    sections, else sections whose title/goal/queries use the ask's terms."""
    titles = {s.title.casefold(): s for s in sections}
    for ask in asks.asks:
        named = [str(t) for t in ((llm_mapping or {}).get(ask.ask_id) or []) if isinstance(t, str)] if llm_mapping else []
        hits = [titles[t.casefold()].title for t in named if t.casefold() in titles]
        if not hits:
            scored = []
            for s in sections:
                text = _section_text(s)
                score = sum(1 for term in ask.terms if term and re.search(rf"\b{re.escape(term)}", text))
                if score >= (1 if ask.kind in {"window", "audience"} else 2) or (ask.kind == "comparison" and score >= 1 and "compar" in text):
                    scored.append((score, s.title))
            hits = [t for _, t in sorted(scored, reverse=True)[:3]]
        if ask.kind == "window":
            hits = ["every section"]  # the window note is given to every writer and to the fetchers
        ask.sections = hits
        ask.status = "mapped" if hits else "unmapped"
    if asks.exhaustive_requested:
        for ask in asks.asks:
            if ask.kind == "deliverable" and "exhaustive" in ask.text:
                ask.status = (ask.status + "; exhaustive not claimable") if ask.status == "mapped" else "not claimable"


def repair_for_asks(asks: Asks, sections: list, decisions: list[str], section_factory) -> list:
    """Add a section for each unmapped comparison/analysis/deliverable ask, and Why/What/How
    subsections to item sections that lack them. ``section_factory(title, goal, queries)`` builds
    a Section; passages are assigned afterwards from the queries, like any planned section."""
    added = []
    for ask in asks.asks:
        if ask.status != "unmapped" or ask.kind in {"window", "audience"}:
            continue
        title = {"comparison": f"{ask.text[:1].upper()}{ask.text[1:]}", "analysis": f"{ask.text[:1].upper()}{ask.text[1:]}",
                 "deliverable": f"List of {asks.items or 'items'} in the window"}.get(ask.kind, ask.text)
        section = section_factory(title, ask.requirement, [" ".join(ask.terms[:4]), f"{asks.items} {ask.text}".strip()])
        sections.append(section)
        added.append(section)
        ask.sections, ask.status = [section.title], "added"
        decisions.append(f"Question ask [{ask.ask_id}] '{ask.text}' had no section: added '{section.title}'.")
    if asks.per_item:
        item_terms = [t for t in asks.items.casefold().split() if len(t) > 2]
        for section in sections:
            text = _section_text(section)
            if getattr(section, "role", "") != "deep_dive" and not (item_terms and any(t in section.title.casefold() for t in item_terms)):
                continue
            have = {w for w in asks.per_item if any(re.match(rf"(?i)\s*{w}\b", sub.title) for sub in section.subsections)}
            missing = [w for w in asks.per_item if w not in have]
            if not missing:
                continue
            for word in missing:
                sub_title = {"why": "Why it was signed", "what": "What it covers", "how": "How it works and is implemented"}[word]
                section.subsections.append(section_factory(sub_title, f"{word.capitalize()}: {section.title}",
                                                           [f"{section.title} {word}"]))
            decisions.append(f"'{section.title}': added {', '.join(w.capitalize() for w in missing)} subsection(s) the question asks for.")
    return added


_EXHAUSTIVE_RE = re.compile(r"(?i)\b(?:an?\s+|the\s+)?(?:exhaustive|complete|comprehensive|full)\s+(list|record|set|picture|account|universe|tally)\b")


def guard_exhaustive_claims(text: str, asks: Asks) -> tuple[str, int]:
    """'exhaustive list' -> 'list of those found in the sources reviewed' (no source proves completeness)."""
    if not asks.exhaustive_requested and not _EXHAUSTIVE_RE.search(text or ""):
        return text, 0
    count = 0

    def repl(match: re.Match) -> str:
        nonlocal count
        count += 1
        return f"the {match.group(1)} found in the sources reviewed"

    return _EXHAUSTIVE_RE.sub(repl, text or ""), count


def coverage_note(asks: Asks) -> str:
    """Methodology paragraph: how each ask was answered (or why it could not be)."""
    if not asks.asks:
        return ""
    parts = []
    for ask in asks.asks:
        where = "; ".join(ask.sections[:3]) if ask.sections else "no section (insufficient evidence)"
        status = {"mapped": "answered in", "added": "answered in an added section:", "unmapped": "not answered:",
                  "not claimable": "listed in"}.get(ask.status.split(";")[0], "answered in")
        parts.append(f"{ask.text} — {status} {where}")
    note = "Coverage of the question: " + "; ".join(parts) + "."
    if asks.exhaustive_requested:
        note += (" The client asked for an exhaustive list; the list contains every item found in the sources reviewed, and "
                 "no source reviewed states that it is complete, so it is not described as exhaustive.")
    qtd = re.match(r"CURRENT QUARTER TO DATE: (\S+ \S+) \(([^)]+)\)", asks.window_note or "")
    if qtd:
        note += (f" The current quarter to date ({qtd.group(1)}, {qtd.group(2)}) is not a completed quarter: it is shown "
                 "separately, labelled QTD (partial), and never ranked with completed quarters.")
    span = re.match(r"TIME WINDOW: the (\d+) months to today, ([^.]+)\.", asks.window_note or "")
    if span:
        note += (f" The window is the {span.group(1)} months to the report date ({span.group(2)}); older items appear only as "
                 "labelled context or comparison.")
    return note
