"""Whole-report editorial pass for V2 output (Stage C2).

Runs once on the assembled analysis sections, before methodology/appendices:

  * section order   -- cross-cutting context first, then sector deep-dives, then
                       compact comparisons and history, then outlook / risks;
  * merge           -- an H2 whose title nearly duplicates an earlier H2 is
                       merged into it (its subsections move, nothing is deleted);
  * headings        -- repeated H3 titles are made specific (section name added);
  * repetition      -- a sentence that repeats an earlier sentence (same claim,
                       same numbers) is removed from the later section;
  * contradictions  -- the same entity + metric + period stated with different
                       values in different sections is detected and recorded
                       (both values stay, the reader sees both citations);
  * stray labels    -- a line that is only "Report" is removed;
  * transitions     -- one optional LLM call writes a one-sentence bridge per
                       section from the section summaries; a bridge with digits,
                       citations or more than 40 words is rejected, so a bridge can
                       never add a fact. Without the call, sections stay as written.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

log = logging.getLogger("editorial_pass")

CITE_RE = re.compile(r"\[(S\d+(?:\.\d+)?(?:\s*[,;]\s*S\d+(?:\.\d+)?)*)\]")
STRAY_LABEL_RE = re.compile(r"(?im)^\s*(?:#{1,4}\s*)?(?:\*\*|__)?Report(?:\*\*|__)?\s*$")
_STOP = {"the", "and", "of", "in", "a", "an", "to", "for", "vs", "on", "with", "by", "its", "india", "indian", "sector",
         "sectors", "analysis", "market", "markets", "dynamics", "trends", "performance", "nifty"}
_LATE_RE = re.compile(r"(?i)\b(?:outlook|risks?|scenarios?|implications?|strateg(?:y|ic)|conclusions?|what to watch|positioning)\b")
_HISTORY_RE = re.compile(r"(?i)\b(?:histor(?:y|ical)|long[- ]term|seasonality|past cycles?)\b")


@dataclass
class EditedSection:
    section_id: str
    title: str
    body: str
    role: str = "cross_cutting"
    entity: str = ""
    summary: str = ""


@dataclass
class EditorialReport:
    order_changed: bool = False
    merged_sections: list[str] = field(default_factory=list)
    renamed_headings: list[str] = field(default_factory=list)
    repeated_sentences_removed: list[str] = field(default_factory=list)
    contradictions: list[dict] = field(default_factory=list)
    stray_labels_removed: int = 0
    transitions_added: int = 0
    transitions_rejected: int = 0

    def to_dict(self) -> dict:
        return {"orderChanged": self.order_changed, "mergedSections": self.merged_sections,
                "renamedHeadings": self.renamed_headings[:30],
                "repeatedSentencesRemoved": len(self.repeated_sentences_removed),
                "repeatedSentenceSamples": self.repeated_sentences_removed[:10],
                "contradictions": self.contradictions[:20], "strayLabelsRemoved": self.stray_labels_removed,
                "transitionsAdded": self.transitions_added, "transitionsRejected": self.transitions_rejected}


def _terms(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z&]{2,}", str(text or "").casefold()) if w not in _STOP}


def _rank(section: EditedSection) -> int:
    if section.role == "deep_dive":
        return 1
    if _LATE_RE.search(section.title):
        return 4
    if _HISTORY_RE.search(section.title):
        return 3
    if section.role == "compact":
        return 2
    return 0


def order_sections(sections: list[EditedSection]) -> tuple[list[EditedSection], bool]:
    ordered = sorted(sections, key=lambda s: _rank(s))  # stable: outline order within a group
    return ordered, [s.section_id for s in ordered] != [s.section_id for s in sections]


def merge_near_duplicates(sections: list[EditedSection], report: EditorialReport, threshold: float = 0.6) -> list[EditedSection]:
    out: list[EditedSection] = []
    for sec in sections:
        target = None
        if sec.role != "deep_dive":
            for prev in out:
                if prev.role == "deep_dive":
                    continue
                a, b = _terms(prev.title), _terms(sec.title)
                if a and b and len(a & b) / len(a | b) >= threshold:
                    target = prev
                    break
        if target is None:
            out.append(sec)
            continue
        body = sec.body if re.match(r"\s*###\s", sec.body) else f"### {sec.title}\n\n{sec.body}"
        existing = {h.strip().casefold() for h in re.findall(r"(?m)^###\s+(.+?)\s*$", target.body)}
        # A moved subsection whose title the target already uses is named after the section it came from.
        body = re.sub(r"(?m)^(###\s+)(.+?)\s*$",
                      lambda m: f"{m.group(1)}{sec.title}: {m.group(2)}" if m.group(2).strip().casefold() in existing else m.group(0),
                      body)
        target.body = f"{target.body.rstrip()}\n\n{body.strip()}"
        target.summary = f"{target.summary} {sec.summary}".strip()
        report.merged_sections.append(f"{sec.title} -> {target.title}")
    return out


def _h3_key(title: str) -> str:
    title = re.sub(r"^[^:]{2,40}:\s+", "", title)  # "Bank: Performance" -> "Performance"
    return " ".join(sorted(_terms(title)))


def disambiguate_headings(sections: list[EditedSection], report: EditorialReport) -> None:
    seen: dict[str, str] = {}
    for sec in sections:
        def repl(match: re.Match) -> str:
            title = match.group(2).strip()
            key = title.casefold() if sec.role == "deep_dive" else _h3_key(title)
            if not key:
                return match.group(0)
            if key in seen and seen[key] != sec.section_id:
                short = re.split(r"[:—–-]", sec.title)[0].strip()
                if short.casefold() not in title.casefold():
                    report.renamed_headings.append(f"{title} -> {title} ({short})")
                    return f"{match.group(1)}{title} ({short})"
            seen.setdefault(key, sec.section_id)
            return match.group(0)
        sec.body = re.sub(r"(?m)^(###\s+)(.+?)\s*$", repl, sec.body)


def _sentence_key(sentence: str) -> tuple[frozenset, frozenset]:
    text = CITE_RE.sub(" ", sentence)
    numbers = frozenset(re.findall(r"\d[\d,]*(?:\.\d+)?", text))
    return frozenset(_terms(text)), numbers


def remove_repetition(sections: list[EditedSection], report: EditorialReport, threshold: float = 0.8) -> None:
    """Remove a sentence that repeats an earlier one: same numbers and >= 80% of the same words."""
    earlier: list[tuple[frozenset, frozenset]] = []
    for sec in sections:
        lines = sec.body.split("\n")
        for li, line in enumerate(lines):
            if not line.strip() or line.lstrip().startswith(("|", "#", "[CHART_", "[[FIGURE")):
                continue
            pieces = re.split(r"(?<=[.!?])(\s+)(?=[A-Z*\[(\"'₹$])", line)
            kept = []
            for piece in pieces:
                words, numbers = _sentence_key(piece)
                if len(words) >= 8 and numbers and re.search(r"[A-Za-z]", piece):
                    repeat = any(numbers == n and len(words & w) / max(1, len(words | w)) >= threshold for w, n in earlier)
                    if repeat and not re.search(r"\bFigure\s+\d+", piece):
                        report.repeated_sentences_removed.append(re.sub(r"\s+", " ", CITE_RE.sub("", piece)).strip()[:160])
                        continue
                    earlier.append((words, numbers))
                kept.append(piece)
            new = "".join(kept).strip()
            lines[li] = "" if re.fullmatch(r"\s*(?:[-*+]|\d+\.)\s*(?:\*\*[^*]*\*\*:?)?\s*", new or "") else new
        sec.body = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


_METRIC_RE = re.compile(r"(?i)\b(P/?E|P/?B|returns?|returned|gain(?:ed|s)?|fell|fall(?:s|en)?|declin(?:e|ed|es)|rose|rise[sn]?|"
                        r"outflows?|inflows?|weight|EPS|ROE)\b")
_METRIC_FAMILY = {"gain": "return", "gained": "return", "gains": "return", "rose": "return", "rise": "return", "rises": "return",
                  "risen": "return", "returned": "return", "return": "return", "returns": "return", "fell": "fall", "fall": "fall",
                  "falls": "fall", "fallen": "fall", "decline": "fall", "declined": "fall", "declines": "fall"}
_ENTITY_RE = re.compile(r"\bNifty\s+(?:[A-Z][A-Za-z&]+(?:\s+[A-Z][A-Za-z&]+)?)")
_PERIOD_RE = re.compile(r"(?i)\b(?:Q[1-4]\s*(?:FY)?\s*'?\d{2,4}|FY\s*'?\d{2}|(?:1|3|6|12)[- ](?:month|year|week)|YTD|one[- ]year|1Y|3M)\b")


def detect_contradictions(sections: list[EditedSection], report: EditorialReport) -> None:
    facts: dict[tuple[str, str, str], tuple[str, str]] = {}
    for sec in sections:
        for sentence in re.split(r"(?<=[.!?])\s+", CITE_RE.sub("", sec.body)):
            entities = _ENTITY_RE.findall(sentence)
            metric = _METRIC_RE.search(sentence)
            period = _PERIOD_RE.search(sentence)
            values = re.findall(r"[-+−]?\d+(?:\.\d+)?%", sentence)
            if len(entities) != 1 or not metric or not period or len(values) != 1:
                continue
            word = metric.group(1).casefold()
            key = (entities[0].casefold(), _METRIC_FAMILY.get(word, word.rstrip("s")), period.group(0).casefold())
            value = values[0].replace("−", "-").lstrip("+")
            if key in facts and facts[key][0].lstrip("-") != value.lstrip("-"):
                report.contradictions.append({"entity": entities[0], "metric": key[1], "period": key[2],
                                              "values": [facts[key][0], value], "sections": [facts[key][1], sec.section_id]})
            facts.setdefault(key, (value, sec.section_id))


TRANSITION_SYSTEM = """You are the editor of a research report. For each section after the first, write ONE bridging
sentence (at most 30 words) that links it to the section before it, using the section summaries provided. Rules: no
numbers, no citations, no new facts, no hype. Return JSON only: {"<section_id>": "<sentence>", ...}."""


def _valid_bridge(text: str) -> bool:
    text = str(text or "").strip()
    return bool(text) and not re.search(r"\d|\[|\]", text) and len(text.split()) <= 40 and text.endswith(".")


async def add_transitions(sections: list[EditedSection], gateway, report: EditorialReport) -> None:
    if gateway is None or len(sections) < 3:
        return
    listing = "\n".join(f"{s.section_id} | {s.title} | {CITE_RE.sub('', s.summary)[:400]}" for s in sections)
    try:
        result = await gateway.generate(purpose="editorial_transitions", section_id="editorial", system=TRANSITION_SYSTEM,
                                        prompt=f"SECTIONS IN ORDER (id | title | summary):\n{listing}", max_tokens=2048,
                                        temperature=0.2, timeout=90)
    except Exception as exc:  # the editorial pass must never fail the report
        log.warning("V2 editorial transitions failed: %s", type(exc).__name__)
        return
    if not getattr(result, "ok", False):
        return
    match = re.search(r"\{.*\}", result.text or "", re.S)
    try:
        bridges = json.loads(match.group(0)) if match else {}
    except ValueError:
        bridges = {}
    for sec in sections[1:]:
        bridge = str(bridges.get(sec.section_id) or "").strip()
        if not bridge:
            continue
        if _valid_bridge(bridge):
            sec.body = f"{bridge}\n\n{sec.body}"
            report.transitions_added += 1
        else:
            report.transitions_rejected += 1


async def editorial_pass(sections: list[EditedSection], *, gateway=None) -> tuple[list[EditedSection], EditorialReport]:
    report = EditorialReport()
    for sec in sections:
        before = len(STRAY_LABEL_RE.findall(sec.body))
        sec.body = STRAY_LABEL_RE.sub("", sec.body)
        report.stray_labels_removed += before
    sections, report.order_changed = order_sections(sections)
    sections = merge_near_duplicates(sections, report)
    disambiguate_headings(sections, report)
    remove_repetition(sections, report)
    detect_contradictions(sections, report)
    await add_transitions(sections, gateway, report)
    if report.repeated_sentences_removed:
        log.info("V2 editorial pass removed %d repeated sentence(s)", len(report.repeated_sentences_removed))
    if report.contradictions:
        log.info("V2 editorial pass found %d possible contradiction(s) (recorded, not resolved)", len(report.contradictions))
    return sections, report
