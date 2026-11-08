"""Agreement ledger for list questions about agreements signed in a window (Stage F).

"List down ... all the FTAs signed by India in the past 1 year" asks for a list whose dates are
facts, with signing kept apart from entry into force. This module reads the evidence store only:

  * every sentence naming an agreement ("India-New Zealand FTA", "India-UAE CEPA") with an event
    verb (signed / concluded / came into force / implemented / operationalised) and a date;
  * one row per agreement: date signed, entry into force, each with the passage it comes from;
  * a statement of what the official Indian sources (commerce.gov.in, pib.gov.in, mea.gov.in,
    egazette.gov.in) verify inside the window - including, honestly, when they verify no signing.

Dates are copied as the passage writes them (a month-only date stays month-only). Nothing is
inferred; the block is an ordinary Markdown table plus one sentence (no new block type).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

_OFFICIAL = ("commerce.gov.in", "pib.gov.in", "mea.gov.in", "egazette.gov.in", "sansad.in", "indiantradeportal.gov.in")
_ORD_DATE = r"\d{1,2}(?:st|nd|rd|th)?\s+(?:January|February|March|April|May|June|July|August|September|October|November|December),?\s+20\d{2}"
# A register row in an official table (e.g. a Lok Sabha answer annexure, PDF text):
# "India – Oman CEPA 18th December 2025 01st June 2026" / "India-New Zealand FTA 27th April 2026 Under ratification process"
_REGISTER_ROW_RE = re.compile(
    r"(?P<name>India[\s\-–]+[^\d]{2,170}?(?:\([A-Z][A-Za-z\- ]{1,20}\)|\b(?:CEPA|CECPA|ECTA|TEPA|CETA|CECA|FTA)\b))"
    r"(?:\s*\([^)]{0,80}\))?\s+(?P<signed>" + _ORD_DATE + r")\s+(?P<eif>" + _ORD_DATE + r"|Under ratification(?: process)?)")
_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_FULL_DATE_RE = re.compile(rf"(?i)\b(\d{{1,2}}\s+{_MONTH},?\s+20\d{{2}}|{_MONTH}\s+\d{{1,2}},\s+20\d{{2}})")
_MONTH_DATE_RE = re.compile(rf"(?i)\b({_MONTH}\s+20\d{{2}})\b")
_AGREEMENT_RE = re.compile(
    r"\b(India[\s\-–]+(?:[A-Z][A-Za-z]+|UK|UAE|EFTA|EU|ASEAN)(?:[\s\-–](?:[A-Z][a-z]+|UK|UAE|EFTA|EU))?\s+"
    r"(?:FTA|CEPA|CETA|ECTA|TEPA|CECA|BTA|PTA|Free Trade Agreement|Comprehensive Economic Partnership Agreement|"
    r"Trade and Economic Partnership Agreement|Comprehensive Economic and Trade Agreement))\b")
_SIGNED_RE = re.compile(r"(?i)\b(?:signed|signing|signature|inked)\b")
_IN_FORCE_RE = re.compile(r"(?i)\b(?:came into (?:force|effect)|entered into force|into effect|implemented|operationali[sz]ed|took effect|effective from)\b")
_MONTHS = "jan feb mar apr may jun jul aug sep oct nov dec".split()


@dataclass
class Event:
    when: str          # as written in the passage
    day: date | None   # parsed when the passage gives a full date
    month: tuple[int, int] | None
    passage_id: str
    official: bool


@dataclass
class Row:
    agreement: str
    signed: list[Event] = field(default_factory=list)
    in_force: list[Event] = field(default_factory=list)
    status: str = ""   # e.g. "Under ratification process" (from an official register row)


def _register_rows(store, official: set[str], rows: dict[str, "Row"]) -> None:
    """Rows of official agreement registers (signing date + entry-into-force date per agreement)."""
    by_source: dict[str, list] = {}
    for passage in store.passages:
        by_source.setdefault(passage.source_id, []).append(passage)
    for source_id, passages in by_source.items():
        flat = [(p, re.sub(r"\s+", " ", str(p.text or ""))) for p in passages]
        joined = " ".join(text for _, text in flat)  # a register table can be split across passages
        for match in _REGISTER_ROW_RE.finditer(joined):
            name = _norm_name(re.sub(r"\s*\((?:Iceland|[A-Z][a-z]+,)[^)]*\)", "", match.group("name")))
            if len(name) > 140:
                continue
            pid = next((p.passage_id for p, text in flat if match.group("signed") in text), passages[0].passage_id)
            is_official = source_id.upper() in official
            row = rows.setdefault(name, Row(name))
            day, month = _parse(match.group("signed"))
            row.signed.insert(0, Event(match.group("signed"), day, month, pid, is_official))
            eif = match.group("eif")
            if eif.casefold().startswith("under"):
                row.status = eif
            else:
                day, month = _parse(eif)
                eif_pid = next((p.passage_id for p, text in flat if eif in text), pid)
                row.in_force.insert(0, Event(eif, day, month, eif_pid, is_official))


def _parse(text: str) -> tuple[date | None, tuple[int, int] | None]:
    from utils.deep_research_coverage import _parse_calendar_date

    day = _parse_calendar_date(text)
    match = re.search(rf"(?i)({_MONTH})\s+(?:\d{{1,2}},\s+)?(20\d{{2}})", text)
    month = (int(match.group(2)), _MONTHS.index(match.group(1)[:3].casefold()) + 1) if match else None
    return day, month


def _norm_name(name: str) -> str:
    name = re.sub(r"\s*[\-–]\s*", "-", re.sub(r"\s+", " ", name.strip()))
    return name.replace("Free Trade Agreement", "FTA")


def _official_ids(manifest: list[dict]) -> set[str]:
    out = set()
    for row in manifest or []:
        url = str(row.get("url") or row.get("publisher_page_url") or "")
        host = re.sub(r"^https?://(?:www\.)?", "", url).split("/")[0].casefold()
        if any(host == d or host.endswith("." + d) for d in _OFFICIAL):
            out.add(str(row.get("id") or "").upper())
    return out


def build_ledger(store, manifest: list[dict]) -> dict[str, Row]:
    official = _official_ids(manifest)
    rows: dict[str, Row] = {}
    _register_rows(store, official, rows)
    for passage in store.passages:
        for sentence in re.split(r"(?<=[.!?])\s+", str(passage.text or "")):
            names = [_norm_name(m.group(1)) for m in _AGREEMENT_RE.finditer(sentence)]
            if len(set(names)) != 1:
                continue  # none, or several agreements in one sentence (dates could belong to either)
            when = (_FULL_DATE_RE.search(sentence) or _MONTH_DATE_RE.search(sentence))
            if not when:
                continue
            day, month = _parse(when.group(1))
            event = Event(when.group(1).strip(), day, month, passage.passage_id, passage.source_id.upper() in official)
            signed = _SIGNED_RE.search(sentence)
            in_force = _IN_FORCE_RE.search(sentence)
            if signed and in_force:
                continue  # both verbs: which date is which is not certain
            row = rows.setdefault(names[0], Row(names[0]))
            if signed:
                row.signed.append(event)
            elif in_force:
                row.in_force.append(event)
    return {k: v for k, v in rows.items() if v.signed or v.in_force}


def _in_window(event: Event, start: date, end: date) -> bool:
    if event.day:
        return start <= event.day <= end
    if event.month:
        first = date(event.month[0], event.month[1], 1)
        return date(start.year, start.month, 1) <= first <= end
    return False


def _best(events: list[Event]) -> Event | None:
    # Official and fully dated first.
    return sorted(events, key=lambda e: (not e.official, e.day is None))[0] if events else None


def ledger_block(store, manifest: list[dict], start: date, end: date, *, items: str = "agreements") -> tuple[str, dict]:
    """(markdown, meta): the table of agreements with an event in the window and the official-source statement."""
    rows = build_ledger(store, manifest)
    official_ids = _official_ids(manifest)
    in_window = []
    for row in rows.values():
        signed, in_force = _best(row.signed), _best(row.in_force)
        if any(_in_window(e, start, end) for e in row.signed + row.in_force):
            in_window.append((row, signed, in_force))
    meta = {"agreementsFound": len(rows), "inWindow": len(in_window), "officialSources": sorted(official_ids)}
    if not in_window:
        return "", meta
    in_window.sort(key=lambda r: ((r[1] or r[2]).month or (0, 0)), reverse=True)

    def cell(event: Event | None) -> str:
        if event is None:
            return "not stated in the sources"
        label = event.when + ("" if event.day else " (month only)")
        return f"{label} [{event.passage_id}]"

    lines = [f"**Table: {items[:1].upper()}{items[1:]} with a signing or entry-into-force date in the window "
             f"({start:%d %b %Y} - {end:%d %b %Y})**", "",
             "| Agreement | Date signed | Entry into force | Signing date from an official source |", "| --- | --- | --- | --- |"]
    for row, signed, in_force in in_window:
        official_sources = {e.passage_id.split(".")[0] for e in row.signed if e.official and signed and e.month == signed.month}
        official = ("no (official confirmation not found)" if not (signed and signed.official)
                    else "yes, two or more official sources" if len(official_sources) >= 2
                    else "yes (one official source; no second official source found)")
        eif = cell(in_force) if in_force or not row.status else f"{row.status} [{signed.passage_id}]" if signed else row.status
        lines.append(f"| {row.agreement} | {cell(signed)} | {eif} | {official} |")
    verified = [(row, s) for row, s, _ in in_window if s and s.official and s.day and start <= s.day <= end]
    cite = ", ".join(sorted({p.passage_id for p in store.passages if p.source_id.upper() in official_ids
                             and re.search(r"(?i)\b(?:fta|cepa|ecta|tepa|trade agreement)\b", p.text)
                             and re.search(r"(?i)\bsign", p.text)})[:3])
    if verified:
        single = [row.agreement for row, s in verified
                  if len({e.passage_id.split(".")[0] for e in row.signed if e.official and e.month == s.month}) < 2]
        statement = ("Signing dates inside the window confirmed by an official Indian source: "
                     + "; ".join(f"{row.agreement}, signed on {s.when} [{s.passage_id}]" for row, s in verified) + "."
                     + (f" Only one official source was found for {', '.join(single)}; no second official source "
                        "confirms these dates." if single else ""))
    elif cite:
        statement = (f"No FTA signed inside the window ({start:%d %b %Y} - {end:%d %b %Y}) is verified with a dated "
                     f"signing in the official Indian sources reviewed [{cite}]; the signing dates above come from the "
                     "sources cited in each row and are shown as reported.")
    else:
        statement = ("None of the official Indian sources retrieved (commerce.gov.in, pib.gov.in, mea.gov.in) states a "
                     "signing date for these agreements; the dates above come from the sources cited in each row.")
    meta.update({"verifiedOfficialSignings": len(verified), "statement": statement})
    return "\n".join(lines) + "\n\n" + statement, meta


_PARTNER_RE = re.compile(r"^India-(.+?)\s+(?:FTA|CEPA|CECPA|CETA|ECTA|TEPA|CECA|BTA|PTA|Comprehensive|Economic|Trade|Free)\b")


def _partners(name: str) -> list[str]:
    """Partner names of an agreement, lower case, with the acronym in parentheses as an alias:
    'India-European Free Trade Association (EFTA) Trade ...' -> ['european free trade association', 'efta']."""
    named = re.match(r"India-([^()]+?)\s*\(([A-Z]{2,6})\)", name)
    if named and named.group(2) not in {"FTA", "CEPA", "CETA", "ECTA", "TEPA", "CECA", "CECPA"}:
        return [named.group(1).strip().casefold(), named.group(2).casefold()]
    match = _PARTNER_RE.match(name)
    if not match:
        return []
    raw = match.group(1)
    out = [re.sub(r"\s*\(.*$", "", raw).strip().casefold()]
    out += [a.casefold() for a in re.findall(r"\(([A-Z]{2,6})\)", name) if a not in {"FTA", "CEPA", "CETA", "ECTA", "TEPA", "CECA", "CECPA"}]
    aliases = {"uk": "united kingdom", "uae": "united arab emirates", "us": "united states", "usa": "united states",
               "eu": "european union", "aus": "australia"}
    out += [aliases[o] for o in list(out) if o in aliases]
    return [o for o in dict.fromkeys(out) if o]


def partner_windows(store, start: date, end: date, *, signed_only: bool = True) -> dict[str, bool]:
    """{partner or alias (lower case): the agreement was signed inside the window} from the evidence
    (``signed_only=False``: a signing OR entry-into-force date inside the window)."""
    out: dict[str, bool] = {}
    for row in build_ledger(store, []).values():
        events = row.signed if signed_only else row.signed + row.in_force
        inside = any(_in_window(e, start, end) for e in events)
        for partner in _partners(row.agreement):
            out[partner] = out.get(partner, False) or inside
    return out


def signed_in_window(store, start: date, end: date) -> list[tuple[str, str]]:
    """[(agreement name, partner)] for agreements whose signing date falls inside the window."""
    out = []
    for row in build_ledger(store, []).values():
        partners = _partners(row.agreement)
        if partners and any(_in_window(e, start, end) for e in row.signed):
            out.append((row.agreement, partners[0]))
    return out


def ledger_timeline(store, manifest: list[dict], start: date, end: date) -> str:
    """'> [!TIMELINE] ...' of the agreements' own dated events inside the window (signed / entry into force)."""
    events = []
    for row in build_ledger(store, manifest).values():
        for kind, items in (("signed", row.signed), ("entered into force", row.in_force)):
            event = _best([e for e in items if _in_window(e, start, end)])
            if event and event.month:
                events.append((event.month, f"{_MONTHS[event.month[1] - 1].capitalize()} {event.month[0]} — "
                                            f"{row.agreement} {kind} ({event.when}) [{event.passage_id}]"))
    if len(events) < 2:
        return ""
    return "> [!TIMELINE] " + " | ".join(text for _, text in sorted(events)[:8])
