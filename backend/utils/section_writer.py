"""Grounded section writer (REPORT_PIPELINE_V2).

Each analysis section is written by its own LLM call that receives ONLY:
  * its own evidence passages (ranked relevance x reliability, with ids),
  * the figures planned for it (data listed, numbered "Figure N"),
  * a short running summary of the sections written before it, and the goals
    of the other planned sections (so it does not repeat or pre-empt them).

Rules enforced in the prompt and checked afterwards:
  * citations must be passage ids given to this section (e.g. [S12.3]);
    any other citation is removed and counted;
  * numbers must appear in this section's passages or figure data; others are
    counted as unsupported (Stage C rewrites them; Stage B reports them);
  * each figure must be referenced and interpreted ("Figure N shows ...");
    an unreferenced figure is dropped together with its marker, so nothing dangles.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field

from utils.figure_refs import rewrite_removed_figure_refs
from utils.number_policy import apply_number_policy, statistic_tokens
from utils.evidence_text import SENTENCE_END  # one sentence-boundary rule for every checker

log = logging.getLogger("section_writer")

SUMMARY_MARKER = "<<<SUMMARY>>>"
CITE_RE = re.compile(r"\[(S\d+(?:\.\d+)?(?:\s*[,;]\s*S\d+(?:\.\d+)?)*)\]")
FIGURE_MARKER_RE = re.compile(r"\[\[\s*FIGURE\s+(\d+)\s*\]\]", re.I)
FIGURE_REF_RE = re.compile(r"\b(?:Figure|Fig\.)\s+(\d+)\b")
_NUM_RE = re.compile(r"(?<![\w.\[])[-+−]?\d[\d,]*(?:\.\d+)?(?!\.\d)")

SYSTEM = """You are a senior research analyst writing ONE section of a longer research report.

NON-NEGOTIABLE RULES
1. Use only the EVIDENCE PASSAGES given to you. Do not use memory or outside knowledge for facts or figures.
2. Cite every factual sentence with the passage ids it relies on, in square brackets, e.g. [S12.3] or [S4.1, S9.2].
   Only cite ids that appear in EVIDENCE PASSAGES.
3. Numbers: state only numbers that appear in your passages or in the FIGURE data. Copy them exactly (same units).
   Quote each number with exactly the unit and scale the passage uses (crore stays crore, lakh crore stays lakh
   crore, trillion stays trillion, %, bps); never convert units, change the scale or round. Name the entity a number
   belongs to in the same sentence. NEVER compute a number yourself: to state a sum, difference, share or change,
   write [[calc: A + B]], [[calc: A - B]], [[calc: A / B]] (share, %) or [[calc: change from A to B]] with A, B copied
   exactly from the passages; the system computes the value and shows its inputs. If a number you would need is
   not in the evidence, say the evidence does not show it. Sentences with numbers the evidence does not contain are removed.
   When a FACT SHEET is given, write a number that is on it by its id ([[F3]]) instead of typing it, and only use ids
   listed there; the system inserts the exact value, unit and citation. Write an id exactly as [[F3]]: two square
   brackets, nothing else inside, never a bare F3, never inside the [S..] citation.
4. Figures: for each figure listed, put its marker [[FIGURE N]] on its own line where it belongs, and in the adjacent
   paragraph refer to it by number ("Figure N") in a sentence that states what the data shows - the key pattern with
   the figure's own values - and why it matters for the section's goal. Never restate the figure's title or describe
   what the chart displays ("Figure N shows the quarterly returns of ..."): the caption already says that.
5. Do not repeat what is listed under ALREADY COVERED, and do not take over the goals of OTHER SECTIONS.
6. Write analytical prose (mechanisms, comparisons, implications), using the H3 subheadings given. Bullets only for
   genuine lists. No executive summary, no conclusion for the whole report, no source list.
7. If the evidence for a subsection is thin, write less rather than pad.
8. Never use code blocks (```). Present structured data as a Markdown table or as prose.

OUTPUT FORMAT
Markdown for the section body (start with the first H3 or an opening paragraph; do NOT write the H2 title).
Then a line containing exactly <<<SUMMARY>>> followed by 2-4 sentences stating what this section established,
including its key numbers with their passage citations."""


@dataclass
class SectionDraft:
    section_id: str
    title: str
    body: str = ""
    summary: str = ""
    ok: bool = False
    error: str = ""
    cited_passages: list[str] = field(default_factory=list)
    invalid_citations: int = 0
    repaired_citations: int = 0
    unsupported_numbers: list[str] = field(default_factory=list)
    calculated_numbers: list[dict] = field(default_factory=list)
    fact_refs_used: list[dict] = field(default_factory=list)
    removed_sentences: list[dict] = field(default_factory=list)
    rewritten_numbers: list[dict] = field(default_factory=list)
    figures_referenced: list[int] = field(default_factory=list)
    figures_dropped: list[int] = field(default_factory=list)
    calls: int = 0
    words: int = 0
    budget: int = 0                                          # words planned for the section
    models: list[str] = field(default_factory=list)          # model that wrote each part (fallback visibility)
    extended: bool = False                                   # an evidence-driven extension was added


def _normalize_number(token: str) -> str:
    raw = token.replace("−", "-").replace(",", "").lstrip("+")
    try:
        value = float(raw)
    except ValueError:
        return raw
    return f"{value:.4f}".rstrip("0").rstrip(".")


def _number_set(texts) -> set[str]:
    out = set()
    for text in texts:
        for token in _NUM_RE.findall(str(text or "")):
            out.add(_normalize_number(token))
            out.add(_normalize_number(token.lstrip("-−")))
    return out


def unsupported_numbers(body: str, allowed: set[str], passage_texts: dict[str, str] | None = None) -> list[str]:
    """Statistics in the text that are not in the evidence (labelled calculations are checked by the policy).

    With ``passage_texts`` the section's fact sheet decides too (it reads "INR494bn", "1,91,520"), so this
    count agrees with the number policy that already accepted the sentence."""
    # A labelled calculation ("₹83,816 crore (calculated: 47,955 + ...)") was verified by the
    # number policy against its inputs: neither the number nor its inputs are "unsupported".
    text = re.sub(r"[-+−]?\d[\d,]*(?:\.\d+)?[^()\d\n]{0,30}\(calculated:(?:[^()]|\([^()]*\))*\)", " ", body)
    sheet = None
    if passage_texts:
        from types import SimpleNamespace

        from utils.fact_sheet import FactSheet, fact_sheet_for
        sheet = FactSheet([SimpleNamespace(passage_id=k, text=v) for k, v in passage_texts.items()])
    out = []
    for token in statistic_tokens(text):
        norm = _normalize_number(token)
        plain = norm.lstrip("-")
        if norm not in allowed and plain not in allowed and not (sheet and sheet.candidates(token.rstrip(",."))):
            out.append(token)
    return out


def build_section_prompt(*, question: str, thesis: str, title: str, goal: str, subsections: list[dict], word_budget: int,
                         passages: list, figures: list, already_covered: list[str], other_sections: list[str],
                         facts: list | None = None) -> str:
    sub_text = "\n".join(f"  ### {s['title']} — goal: {s.get('goal') or 'as titled'} (~{s['words']} words)" for s in subsections) or "  (none: write it as one argument with 1-3 H3 subheadings of your choice)"
    covered = "\n".join(f"- {c}" for c in already_covered) or "- (nothing yet: this is among the first sections)"
    others = "\n".join(f"- {o}" for o in other_sections) or "- (none)"
    figs = "\n".join(f.brief() for f in figures) or "(no figures for this section)"
    ev = "\n\n".join(
        f"[{p.passage_id}] {p.publisher or p.title[:60]} | {p.published or 'undated'} | {p.authority_tier} | reliability {p.reliability:.2f}\n{p.text}"
        for p in passages)
    return f"""REPORT QUESTION: {question}
REPORT THESIS: {thesis}

SECTION TO WRITE: {title}
SECTION GOAL: {goal}
LENGTH: target {word_budget} words (acceptable range {int(word_budget * 0.85)}-{int(word_budget * 1.1)}). The budget was
sized from the evidence below: work through every relevant passage (comparisons, mechanisms, what the numbers imply).
Only stop short if the passages genuinely run out; never pad or repeat.
SUBSECTIONS (use these H3 headings, in this order):
{sub_text}

ALREADY COVERED by earlier sections (do not repeat; build on it where useful):
{covered}

OTHER SECTIONS in this report (leave their goals to them):
{others}

FIGURES FOR THIS SECTION:
{figs}
{fact_block(passages, facts=facts)}
EVIDENCE PASSAGES (cite by id):
{ev}"""


_MONTH_RE = re.compile(r"(?i)^(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?$")
_NUMBERING_RE = re.compile(r"(?i)^(?:table|figure|fig|chart|section|annex|annexure|chapter|para|paragraph|appendix|box|"
                           r"exhibit|note|schedule|clause|rule|item|no|sl|s\.no)\.?$")
# A unit written as a word after a prose number, kept with the value: "INR 1.8 lakh", "19.240 MT".
_PROSE_UNIT_RE = re.compile(r"^(lakhs?|thousand|MT|MnT|mt|tonnes?|tons?|GW|MW|GWh|TWh|kWh|km|kg|units|bps|"
                            r"million\s+tonnes|lakh\s+tonnes|crore\s+tonnes)\b")


def _prose_unit_text(fact) -> str:
    context = fact.context or ""
    at = context.find(fact.raw)
    if at < 0 or fact.unit:
        return ""
    m = _PROSE_UNIT_RE.match(context[at + len(fact.raw):].lstrip())
    return m.group(1) if m else ""


def _prose_statistic(fact) -> bool:
    """A number in passage prose worth an id: it has a unit or scale, or enough digits to be a statistic.
    Not years, dates, small counts or list numbers."""
    plain = fact.value.lstrip("-")
    if re.fullmatch(r"(?:19|20)\d{2}(?:\.\d+)?", plain):
        return False  # a year, or a year with a footnote stuck to it ("by 2047.57")
    if not fact.unit and "," not in fact.raw and re.fullmatch(r"(?:19|20)\d{2}[1-9]", plain):
        return False  # "compared to 20231": the year 2023 with footnote 1 (europe_gas live run)
    context = fact.context or ""
    at = context.find(fact.raw)
    if at >= 0:
        before = context[:at].split()[-1:] or [""]
        after = context[at + len(fact.raw):].split()[:1] or [""]
        if _MONTH_RE.match(before[0].strip(",.")) or _MONTH_RE.match(after[0].strip(",.")):
            return False  # "15 November", "November 15"
        if _NUMBERING_RE.match(before[0].strip(",.:")):
            return False  # "Table 1.6", "Section 3.2"
        if not fact.unit and not context[:at].strip() and re.match(r"\s*:?\s*[A-Z][a-z]", context[at + len(fact.raw):]):
            return False  # a heading number ("1.3 Highlights of ...")
        # A flattened table ("Total MF AUM (lakh crore) 53.4 172 461 1,152 2,791"): a run of bare numbers
        # has lost its rows and columns, so which number belongs to which year cannot be told.
        # Numbers with their own unit ("94 %") and day numbers of a date ("1 October") are not part of a run:
        # "( 94 % or 1,083 TWh on 1 October" lost 1,083 TWh, and the writer wrote "94% (94% TWh)".
        before_w, after_w = context[:at].split()[-4:], context[at + len(fact.raw):].split()[:4]

        def bare_numbers(words: list[str]) -> int:
            # Years and currency-marked amounts are not part of a flattened run: "USD 78 in 2019 (Life- USD 58
            # and Non-Life - USD 20)" offered only 58, and the writer used that one id for 78, 58 and 20.
            count = 0
            for k, w in enumerate(words):
                token = w.strip("(),.")
                nxt = words[k + 1].strip("(),.") if k + 1 < len(words) else ""
                prev = words[k - 1].strip("(),.-") if k else ""
                if (re.fullmatch(r"[-+−]?[\d,]+(?:\.\d+)?", token) and not re.fullmatch(r"%|per|percent", nxt)
                        and not _MONTH_RE.match(nxt) and not re.fullmatch(r"(?:19|20)\d{2}", token)
                        and not re.fullmatch(r"(?i)usd|us\$|\$|rs|inr|₹|eur|€|£", prev)):
                    count += 1
            return count

        if bare_numbers(before_w[-3:]) + bare_numbers(after_w[:3]) >= 2:
            return False
    digits = len(re.sub(r"\D", "", plain).strip("0"))
    has_scale = bool(fact.unit) or bool(re.match(r"[₹$€£]|Rs|INR|USD", fact.raw))
    return has_scale or digits >= 3 or "." in plain


def prose_snippet(fact, before_words: int = 9, after_words: int = 7) -> str:
    """The words around a prose number, so the writer sees what it measures."""
    context = re.sub(r"\s+", " ", fact.context or "").strip()
    at = context.find(fact.raw)
    if at < 0:
        return context[:110]
    before = context[:at].split()[-before_words:]
    after = context[at + len(fact.raw):].split()[:after_words]
    return ("…" if len(context[:at].split()) > before_words else "") + " ".join(before + [fact.raw] + after) + "…"


_MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
                "October", "November", "December"]
_MONTH_WORD_RE = re.compile(r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|"
                            r"Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\b")


def _article_dated_month(fact, passage) -> str:
    """"Month YYYY" for a prose statistic dated only by a month ("rose to 6.09 percent in October"): the most
    recent such month on or before the article's date. "" when the sentence names a year or no month.

    inflation live run: an article of 17 Nov 2026 said "in October"; the writer wrote "October 2024"."""
    context = str(getattr(fact, "context", "") or "")
    published = str(getattr(passage, "published", "") or "")
    if re.search(r"\b(?:19|20)\d{2}\b|\bFY\s*'?\d{2}\b", context) or not re.match(r"(?:19|20)\d{2}-\d{2}", published):
        return ""
    month = _MONTH_WORD_RE.search(context)
    if not month:
        return ""
    index = next(i for i, name in enumerate(_MONTH_NAMES) if name.lower().startswith(month.group(1).lower()[:3]))
    year, published_month = int(published[:4]), int(published[5:7])
    return f"{_MONTH_NAMES[index]} {year if index + 1 <= published_month else year - 1}"


def numbered_facts(passages, limit: int = 60, prose_limit: int = 60) -> list:
    """The section's facts in fact-sheet order (table cells, then statistics from passage prose); fact Fn is
    element n-1.

    Built once per prompt and handed to postprocess, so an id means the same value in both."""
    try:
        from utils.fact_sheet import FactSheet, fact_sheet_for
        all_facts = FactSheet(passages).facts
    except Exception:  # the block is an aid; the section still has its passages
        return []
    table = [f for f in all_facts if f.kind == "table" and f.metric and f.entity
             and not re.fullmatch(r"(?:19|20)\d{2}", f.value)]
    seen, out = set(), []
    for f in table:
        key = (f.value, f.unit, f.entity.casefold(), f.metric.casefold()[:30])
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
        if len(out) >= limit:
            break
    # Statistics written in the passages' sentences ("exports reached $27.85 billion in FY24"): most of the
    # numbers the writer still typed came from prose, where only word matching could check them.
    by_id = {getattr(p, "passage_id", ""): p for p in passages or []}
    prose_seen, prose = set(), []
    for f in all_facts:
        if f.kind != "prose" or not _prose_statistic(f):
            continue
        key = (f.value, f.unit, f.passage_id)
        if key in prose_seen:
            continue
        prose_seen.add(key)
        if not f.unit_text:
            f.unit_text = _prose_unit_text(f)
        dated = _article_dated_month(f, by_id.get(f.passage_id))
        if dated:
            f.period = dated
            f.dated_from_article = True  # checked against the year the writer puts beside its id
        prose.append(f)
        if len(prose) >= prose_limit:
            break
    return out + prose


_ASCII_ART_CELL_RE = re.compile(r"^[\s\-*/\\_=.+o|#~^<>]{8,}$")
_TABLE_CAPTION_RE = re.compile(r"^\s*(?:\*\*)?\s*(?:Table|Figure|Chart)\s+\d+\b.*$", re.I)


def strip_ascii_charts(body: str) -> str:
    """Remove charts the writer drew with characters inside a Markdown table.

    Live EV run: "| 1,000 | -------*---- |" rows under "**Table 10: Karnataka Annual EVPCS Installations**".
    The number check removed some rows and left the rest, a broken picture. Rows whose cells are drawing
    characters are dropped, then a table left with no data rows and its caption."""
    lines = str(body or "").split("\n")
    kept = []
    for line in lines:
        cells = [c.strip() for c in line.strip().strip("|").split("|")] if line.lstrip().startswith("|") else []
        if any(_ASCII_ART_CELL_RE.match(c) and c.count("-") + c.count("*") + c.count("/") >= 6
               and not re.fullmatch(r":?-{3,}:?", c) for c in cells):
            continue
        kept.append(line)
    out: list[str] = []
    i = 0
    while i < len(kept):
        if kept[i].lstrip().startswith("|"):
            j = i
            while j < len(kept) and kept[j].lstrip().startswith("|"):
                j += 1
            block = kept[i:j]
            data = [ln for ln in block if not re.fullmatch(r"[\s|:\-]+", ln.strip())]
            if len(data) <= 1:  # a header (or nothing) left: the table was all drawing
                while out and not out[-1].strip():
                    out.pop()
                if out and _TABLE_CAPTION_RE.match(out[-1]):
                    out.pop()
            else:
                out.extend(block)
            i = j
            continue
        out.append(kept[i])
        i += 1
    return "\n".join(out)


_ODD_CITATION_RE = re.compile(r"[【〔［(]\s*(S\d+(?:\.\d+)?(?:\s*[,;]\s*S\d+(?:\.\d+)?)*)\s*[】〕］)]")


def normalize_citation_brackets(text: str) -> str:
    """"【S23.2】" (full-width brackets, one writer model) and "(S12)" become "[S23.2]" / "[S12]".

    Live run: 22 of pharma's citations reached the report in 【】, unlinked and unchecked, and the number
    check saw those sentences as uncited."""
    return _ODD_CITATION_RE.sub(lambda m: f"[{m.group(1)}]", str(text or ""))


_RS_DOT_RE = re.compile(r"\bRs\.\s*(?=\d)")
_CURRENCY_AFTER_RE = re.compile(
    r"(?<![\d,.])(\d[\d,]*(?:\.\d+)?)\s+([₹$€£])\s?(Cr|crore|lakh(?:\s+crore)?|Mn|Bn|million|billion|trillion)\b")
_CURRENCY_UNIT_RE = re.compile(r"^(₹|US\$|\$|€|£|Rs\.?|INR|USD)\s*(.*)$")


def fact_value_text(fact) -> str:
    """A fact as it is written in the report: the cell's own text plus its unit ("₹1,750", "7.44%", "300 crore")."""
    raw = fact.raw
    if fact.unit and fact.unit not in raw:
        return f"{raw}{fact.unit}" if fact.unit in {"%", "x"} else f"{raw} {fact.unit}"
    unit_text = getattr(fact, "unit_text", "")
    if not fact.unit and unit_text and unit_text.casefold() not in raw.casefold():
        currency = _CURRENCY_UNIT_RE.match(unit_text)
        if currency and not re.match(r"\s*[-+−]?\s*(?:₹|US\$|\$|€|£|Rs|INR|USD)", raw):
            symbol, rest = currency.group(1).rstrip("."), currency.group(2).strip()
            value = f"{symbol}{raw}" if symbol in {"₹", "$", "US$", "€", "£"} else f"{symbol} {raw}"
            return f"{value} {rest}" if rest else value  # "(₹ Cr)" column: "₹11,455 Cr", not "11,455 ₹ Cr"
        return f"{raw} {unit_text}"  # the column's unit as written: "109.14 MnT"
    return raw


def fact_block(passages, limit: int = 60, facts: list | None = None) -> str:
    """The section's fact sheet, compact: the numbers the writer may quote, each with an id, its unit, what
    it measures, whose it is and its passage (Stage G: most removed numbers were a right value attached to
    the wrong metric/entity, or a calculation on inexact inputs)."""
    facts = numbered_facts(passages, limit) if facts is None else facts
    lines, prose_lines = [], []
    for i, f in enumerate(facts, 1):
        if f.kind == "prose":
            dated = f" (that is {f.period}, from the article date)" if getattr(f, "dated_from_article", False) else ""
            prose_lines.append(f"- F{i} | {fact_value_text(f)} | {prose_snippet(f)}{dated} | [{f.passage_id}]")
            continue
        metric = re.sub(r"\s+", " ", f.metric)[:50]
        lines.append(f"- F{i} | {fact_value_text(f)} | {metric or '-'} | "
                     f"{(f.label or f.entity)[:40] or '-'} | {f.period or '-'} | [{f.passage_id}]")
    if not lines and not prose_lines:
        return ""
    if prose_lines:
        lines += ["NUMBERS IN THE PASSAGES' TEXT (id | value | the sentence around it | passage); state each with "
                  "what its sentence says it measures:"] + prose_lines
    return ("\nFACT SHEET (id | value | what it measures | whose | period | passage). To state one of these numbers, "
            "write its id in double brackets, e.g. [[F3]], instead of typing the number: the system inserts the value "
            "exactly as shown here, and its citation. If the value shown has no unit, write the unit after the id "
            "([[F3]] per quintal). Name the metric and entity shown here in the same sentence. Ids work in "
            "calculations too: [[calc: F3 - F1]].\n"
            + "\n".join(lines) + "\n")


FACT_REF_RE = re.compile(r"\[\[\s*(?:fact\s*:\s*)?F(\d+)\s*\]\](\s?%)?", re.I)
FACT_ID_IN_CALC_RE = re.compile(r"(?<![\w.])F(\d+)(?![\w])")
VERIFIED_MARK = "⁠"  # word joiner, invisible: the number before it was inserted from a fact id
UNKNOWN_FACT = "[[UNKNOWN_FACT]]"
BOUND_FLIP = "[[BOUND_FLIP]]"
PERIOD_FLIP = "[[PERIOD_FLIP]]"
REPEATED_FACT = "[[REPEATED_FACT]]"
_CURRENCY_TAIL_RE = re.compile(r"(?:\brs\.?|\binr|\busd|us\$|[₹$€£])\s*$", re.I)
_UPPER_BOUND_RE = re.compile(r"(?:\bnot\s+(?:exceeding|more\s+than|above|over|greater\s+than|higher\s+than)|"
                             r"\b(?:less|lower|fewer)\s+than|\bbelow|\bunder|\bup\s*to|\bat\s+most|\bmaximum\s+of|"
                             r"\bwithin|\bnot\s+to\s+exceed)\s*$", re.I)
_LOWER_BOUND_RE = re.compile(r"(?:\bnot\s+(?:less\s+than|below|under|lower\s+than)|\bexceed(?:ing|s)?|"
                             r"\b(?:more|greater|higher)\s+than|\bover|\babove|\bat\s+least|\bminimum\s+of|"
                             r"\bin\s+excess\s+of|\bbeyond)\s*$", re.I)


def _bound(text_before: str) -> str:
    """"upper" / "lower" when the words right before a value bound it ("not exceeding Rs." / "at least")."""
    tail = _CURRENCY_TAIL_RE.sub("", text_before.rstrip()).rstrip()
    if _UPPER_BOUND_RE.search(tail):
        return "upper"
    return "lower" if _LOWER_BOUND_RE.search(tail) else ""


def _flips_bound(fact, sentence_before: str) -> bool:
    """The draft bounds the value the other way from its passage: "net worth not exceeding Rs. 25 crores"
    written as "a net worth exceeding [[F23]]" (live run, IPO)."""
    context = re.sub(r"\s+", " ", getattr(fact, "context", "") or "")
    at = context.find(fact.raw) if fact.raw else -1
    if at < 0:
        return False
    source, draft = _bound(context[:at]), _bound(sentence_before)
    return bool(source and draft and source != draft)


_BRACKET_GROUP_RE = re.compile(r"\[(?:[^\[\]]|\[[^\[\]]*\])*\]")
_ID_WITH_VALUE_RE = re.compile(r"\[\[\s*F[\d.]+\s*:\s*([-+−]?(?:₹|Rs\.?|\$|US\$)?\s*\d[\d,]*(?:\.\d+)?\s*%?)\s*\]\]", re.I)
_DOUBLE_ID_RE = re.compile(r"\[\[\s*(?:fact\s*:\s*)?F(\d{1,3})\s*(%?)\s*\]\]", re.I)
_SINGLE_ID_RE = re.compile(r"(?<!\[)\[\s*F(\d{1,3})\s*\](?!\])")
_BARE_ID_RE = re.compile(r"(?<![\w\[\-/.&])F(\d{1,3}(?:\.\d+)?)(?![\w\]\-&])")
_LEFTOVER_ID_RE = re.compile(r"\[\[\s*F(?!IGURE)(?!\d{1,3}\]\])[^\]]*\]\]", re.I)


def normalize_fact_refs(text: str, fact_count: int) -> str:
    """Every way a writer wrote a fact id becomes "[[Fn]]" (or UNKNOWN_FACT).

    Live 30-prompt run: besides "[[F12]]" the writers wrote bare "F61", "(F30)", "[F21]", "[[F14%]]",
    "[[F56.1: 5171.97]]" and ids inside citations ("[[F15], [F21], S30.9]"); none were resolved and the
    raw ids reached the published reports (52 in one EV report, 67 in the internet one)."""
    def citation_group(m: re.Match) -> str:
        group = m.group(0)
        if not re.search(r"\bS\d", group) and len(re.findall(r"\bF\d", group)) >= 2 and re.fullmatch(
                r"\[\s*(?:\[?\s*F\d{1,3}\s*\]?\s*[,;]?\s*)+\]", group):
            return ""  # "[[F15], [F16]]" used as a citation (auto_sales_ice): ids are not sources
        if not re.search(r"\bS\d", group) or not re.search(r"\bF\d", group):
            return group
        parts = [p.strip().strip("[]").strip() for p in re.split(r"\s*[,;]\s*", group[1:-1])]
        kept = [p for p in parts if re.fullmatch(r"S\d+(?:\.\d+)?", p, re.I)]
        return f"[{', '.join(kept)}]" if kept else ""

    text = _BRACKET_GROUP_RE.sub(citation_group, text)
    text = _ID_WITH_VALUE_RE.sub(lambda m: m.group(1).strip(), text)  # the typed value is checked as typed
    # "608.3 international dollars (F30)": the id labels a number the writer typed; resolved, the value would
    # be printed twice. The label goes; the typed number is checked like any other.
    text = re.sub(r"(\d[\d,]*(?:\.\d+)?[^()\n\d]{0,40}?)\s*\(\s*F\d{1,3}\s*\)", r"\1", text)
    text = _DOUBLE_ID_RE.sub(lambda m: f"[[F{m.group(1)}]]{m.group(2)}", text)
    text = _SINGLE_ID_RE.sub(lambda m: f"[[F{m.group(1)}]]", text)
    text = _LEFTOVER_ID_RE.sub(UNKNOWN_FACT, text)  # malformed "[[FFF:=F31 ...]]": nothing to resolve
    # "[[F7]] ([[F7]] TWh)": the same value twice; the parenthetical goes (europe_gas: "94% (94% TWh)").
    text = re.sub(r"(\[\[F(\d{1,3})\]\])\s*\(\s*\[\[F\2\]\][^()\n]{0,20}\)", r"\1", text)

    def bare(m: re.Match) -> str:
        number = m.group(1)
        if "." not in number and 1 <= int(number) <= fact_count:
            return f"[[F{number}]]"
        return UNKNOWN_FACT

    # Bare ids outside "[[...]]" (the canonical ids are protected by the lookbehind on "[").
    return _BARE_ID_RE.sub(bare, text)


def resolve_fact_refs(text: str, facts: list | None) -> tuple[str, list[dict]]:
    """Replace the writer's fact ids with the facts' exact text.

    "[[F3]]" becomes the cell's value and unit, marked verified (the number policy does not second-guess
    a value the code inserted) and the sentence gains the fact's citation if it lacks it. Ids inside
    [[calc: ...]] become the values the calculation then checks. An id that is not in the list becomes
    UNKNOWN_FACT, and the number policy removes that sentence."""
    facts = facts or []
    used: list[dict] = []

    def fact_for(n: str):
        i = int(n)
        return facts[i - 1] if 1 <= i <= len(facts) else None

    def calc_sub(m: re.Match) -> str:
        inner = FACT_ID_IN_CALC_RE.sub(
            lambda f: fact_value_text(fact_for(f.group(1))) if fact_for(f.group(1)) else f.group(0), m.group(1))
        return f"[[calc: {inner}]]"

    text = re.sub(r"\[\[\s*calc\s*:\s*([^\]]+)\]\]", calc_sub, text, flags=re.I)
    if facts:
        text = normalize_fact_refs(text, len(facts))

    pieces = re.split(SENTENCE_END + r"(\s+)", text)
    for i, sentence in enumerate(pieces):
        if not FACT_REF_RE.search(sentence):
            continue
        # One fact is one value: the same id twice in a sentence ("from [[F104]] in FY15 to [[F104]] in FY23") or
        # in one table ("[[F70]]" for life, non-life and total density, insurance live run) stands for values
        # the fact sheet did not offer. Those occurrences become REPEATED_FACT; the policy removes the
        # sentence / table row.
        table_ids = [m.group(1) for line in sentence.split("\n") if line.lstrip().startswith("|")
                     for m in FACT_REF_RE.finditer(line)]
        prose_ids = [m.group(1) for line in sentence.split("\n") if not line.lstrip().startswith("|")
                     for m in FACT_REF_RE.finditer(line)]
        repeated = {n for ids in (table_ids, prose_ids) for n in ids if ids.count(n) >= 2}
        if repeated:
            sentence = FACT_REF_RE.sub(lambda m: REPEATED_FACT if m.group(1) in repeated else m.group(0), sentence)
        needed: list[str] = []
        first_used = len(used)

        def sub(m: re.Match) -> str:
            fact = fact_for(m.group(1))
            if fact is None:
                return UNKNOWN_FACT
            if _flips_bound(fact, sentence[:m.start()]):
                return BOUND_FLIP
            if getattr(fact, "dated_from_article", False):
                # The clause around the id names years; the article dates this value to another one.
                clause = sentence[max(0, m.start() - 70):m.end() + 70]
                years = set(re.findall(r"\b(?:19|20)\d{2}\b", clause))
                if years and fact.period[-4:] not in years:
                    return PERIOD_FLIP
            value = fact_value_text(fact)
            after = sentence[m.end():m.end() + 16].lstrip().casefold()
            if fact.unit and fact.unit != "%" and after.startswith(fact.unit.casefold()) and value.endswith(fact.unit):
                value = value[: -len(fact.unit)].rstrip()  # the writer wrote the unit after the id itself
            if fact.unit == "%" and after.startswith(("%", "per cent", "percent")) and value.endswith("%"):
                value = value[:-1]
            # A "%" the writer put after the id: kept only for a fact without a unit of its own. A fact in MW
            # read "Coal | 2,19,610%" (renewables live run); a percentage fact already carries its "%".
            percent = m.group(2) or ""
            if percent and (fact.unit or getattr(fact, "unit_text", "")):
                percent = ""
            needed.append(fact.passage_id)
            used.append({"id": f"F{m.group(1)}", "value": value, "passage": fact.passage_id, "_fact": fact})
            digits = re.search(r"\d[\d,]*(?:\.\d+)?", value)
            value = value[:digits.end()] + VERIFIED_MARK + value[digits.end():] if digits else value
            return value + percent.strip()

        sentence = FACT_REF_RE.sub(sub, sentence)
        cited = {t.strip().upper() for g in CITE_RE.findall(sentence) for t in re.split(r"\s*[,;]\s*", g)}
        missing = [pid for pid in dict.fromkeys(needed) if pid not in cited and pid.split(".")[0] not in cited]
        if missing:
            stripped = sentence.rstrip()
            end = len(stripped) - 1 if stripped and stripped[-1] in ".!?" else len(stripped)
            sentence = stripped[:end] + f" [{', '.join(missing)}]" + stripped[end:] + sentence[len(stripped):]
        for entry in used[first_used:]:
            entry["_sentence"] = sentence  # report-number charts group the facts a sentence compares
        pieces[i] = sentence
    return "".join(pieces), used


def _sentence_numbers(sentence: str) -> list[str]:
    """Factual numbers in a sentence (no citations, figure refs, periods, durations, counts, small ordinals)."""
    return [_normalize_number(t).lstrip("-") for t in statistic_tokens(re.sub(r"\(calculated:(?:[^()]|\([^()]*\))*\)", " ", sentence))]


def repair_citations(body: str, passage_numbers: dict[str, set[str]]) -> tuple[str, int]:
    """Re-point citations to the passage that actually holds each number.

    The writer sometimes cites [S6.2] for a figure that is in [S14.1]. When a
    sentence's number is absent from every passage it cites but present in
    another passage given to this section, that passage is added to the
    sentence's citation. Numbers found in no passage are left for the
    unsupported-number count (Stage C rewrites them).
    """
    if not passage_numbers:
        return body, 0
    repaired = 0
    pieces = re.split(SENTENCE_END + r"(\s+)", body)
    for i, sentence in enumerate(pieces):
        matches = list(CITE_RE.finditer(sentence))
        if not matches:
            # An uncited sentence whose numbers all sit in one given passage gets that citation.
            numbers = _sentence_numbers(sentence)
            if numbers and not sentence.lstrip().startswith("|"):
                holder = next((pid for pid, nums in passage_numbers.items() if all(n in nums for n in numbers)), None)
                if holder:
                    stripped = sentence.rstrip()
                    end = len(stripped) - 1 if stripped and stripped[-1] in ".!?" else len(stripped)
                    pieces[i] = stripped[:end] + f" [{holder}]" + stripped[end:] + sentence[len(stripped):]
                    repaired += 1
            continue
        cited = [t.strip().upper() for m in matches for t in re.split(r"\s*[,;]\s*", m.group(1))]
        have = set().union(*(passage_numbers.get(c, set()) for c in cited))
        additions = []
        for number in _sentence_numbers(sentence):
            if number in have:
                continue
            holder = next((pid for pid, nums in passage_numbers.items() if number in nums and pid not in cited), None)
            if holder and holder not in additions:
                additions.append(holder)
                have |= passage_numbers.get(holder, set())
        if additions:
            last = matches[-1]
            merged = ", ".join(dict.fromkeys([*[t.strip().upper() for t in re.split(r"\s*[,;]\s*", last.group(1))], *additions]))
            pieces[i] = sentence[:last.start()] + f"[{merged}]" + sentence[last.end():]
            repaired += len(additions)
    return "".join(pieces), repaired


_FENCE_RE = re.compile(r"```[A-Za-z0-9_+-]*[ \t]*\n?(.*?)```", re.S)


def unfence(body: str) -> str:
    """Render code blocks as a Markdown table (pipe rows), a bullet list (several lines) or plain text.

    Stage L: text art (bars, box tables, trees, panels) is resolved first as whole blocks
    (utils/text_art_fences.py) - a real table becomes a sourced Markdown table, art whose numbers the
    text already gives is dropped; only what is left falls through to the line rules below."""
    try:
        from utils.text_art_fences import resolve_fences
        body, _ = resolve_fences(body)
    except Exception:  # pragma: no cover - the fallback below still removes the fences
        pass

    def repl(match: re.Match) -> str:
        lines = [ln.rstrip() for ln in match.group(1).strip().splitlines() if ln.strip()]
        if not lines:
            return ""
        piped = [ln for ln in lines if "|" in ln]
        if len(piped) >= 2 and len(piped) >= 0.6 * len(lines):
            rows = [[c.strip() for c in ln.strip().strip("|").split("|")] for ln in piped]
            width = max(len(r) for r in rows)
            rows = [r + [""] * (width - len(r)) for r in rows]
            table = ["| " + " | ".join(rows[0]) + " |", "| " + " | ".join(["---"] * width) + " |"]
            table += ["| " + " | ".join(r) + " |" for r in rows[1:]]
            return "\n\n" + "\n".join(table) + "\n\n"
        if len(lines) > 1:
            items = [re.sub(r"^\s*(?:[-*•]\s*)?", "- ", ln) for ln in lines]
            return "\n\n" + "\n".join(items) + "\n\n"
        return "\n\n" + lines[0].strip() + "\n\n"
    body = _FENCE_RE.sub(repl, body)
    return re.sub(r"(?m)^\s*```[A-Za-z0-9_+-]*\s*$", "", body)  # unmatched fence lines


def postprocess(draft: SectionDraft, raw: str, *, allowed_passages: set[str], allowed_numbers: set[str],
                figures: list, min_words: int = 60, passage_numbers: dict[str, set[str]] | None = None,
                passage_texts: dict[str, str] | None = None, own_figures: set[int] | None = None,
                context_entity: str = "", fallback_sheet=None, fact_refs: list | None = None) -> SectionDraft:
    text = str(raw or "").strip()
    if SUMMARY_MARKER in text:
        body, summary = text.split(SUMMARY_MARKER, 1)
    else:
        body, summary = text, ""
    # Citations in "【S3】" / "(S3)" become "[S3]" first: fact-id resolution reads them to decide whether
    # a sentence already cites its fact's passage.
    body, summary = normalize_citation_brackets(body), normalize_citation_brackets(summary)
    # "Rs. 25 crore" -> "Rs 25 crore" before anything splits sentences: the fact-id resolver itself split
    # "... to Rs. 70,253 crore" at "Rs." and put the citation there ("Rs [S14]. 70,253 crore", insurance run).
    # "17,68,217 ₹ Cr" (typed by the writer) -> "₹17,68,217 Cr".
    body, summary = (_CURRENCY_AFTER_RE.sub(r"\2\1 \3", _RS_DOT_RE.sub("Rs ", part)) for part in (body, summary))
    # Fact ids ([[F3]]) become the exact cell text before anything else reads the numbers.
    body, used = resolve_fact_refs(body, fact_refs)
    summary, used_in_summary = resolve_fact_refs(summary, fact_refs)
    draft.fact_refs_used += used + used_in_summary
    body, summary = _RS_DOT_RE.sub("Rs ", body), _RS_DOT_RE.sub("Rs ", summary)  # inserted values ("Rs. 25 crore")
    body = re.sub(r"(?m)^\s*#\s+.*$|^\s*##\s+.*$", "", body).strip()  # the pipeline owns H1/H2 headings
    body = unfence(body)
    body = strip_ascii_charts(body)
    # The writer sometimes copies the prompt's subsection spec into the heading:
    # "### Title — goal: Review ... (~475 words)" -> "### Title".
    # (also echoed with another label, e.g. "— regard: returns by quarter, ...").
    body = re.sub(r"(?m)^(#{3,6}\s+.+?)\s*[—–-]+\s*(?:goal|regard|focus|aim|scope|covers?)\s*:.*$", r"\1", body)
    body = re.sub(r"(?m)^(#{3,6}\s+.+?)\s*\(~?\d+\s+words\)\s*$", r"\1", body)
    allowed_sources = {pid.split(".")[0] for pid in allowed_passages}
    invalid = 0

    def fix(match: re.Match) -> str:
        nonlocal invalid
        kept = []
        for token in re.split(r"\s*[,;]\s*", match.group(1)):
            token = token.strip().upper()
            if token in allowed_passages or ("." not in token and token in allowed_sources):
                kept.append(token)
            else:
                invalid += 1
        return f"[{', '.join(dict.fromkeys(kept))}]" if kept else ""

    body = CITE_RE.sub(fix, body)
    body, draft.repaired_citations = repair_citations(body, passage_numbers or {})
    if passage_texts:
        # Number policy: label calculations with their inputs; remove sentences whose
        # numbers are not in the evidence (logged and recorded, never hidden).
        policy = apply_number_policy(body, passage_texts=passage_texts, allowed_numbers=allowed_numbers,
                                     section_id=draft.section_id, context_entity=context_entity,
                                     fallback_sheet=fallback_sheet)
        body = policy.body
        draft.calculated_numbers += policy.calculated
        draft.removed_sentences += policy.removed
        draft.rewritten_numbers += policy.rewritten
    # The verified marks have done their job (the number policy skipped those numbers): remove them.
    body = body.replace(VERIFIED_MARK, "")
    # Any other double-bracket token the writer made up ("[[Part IV]]", upi live run) is not text: removed.
    # Figure markers stay; the policy's own markers were handled (their sentences removed) above.
    body = re.sub(r"\s*\[\[(?!\s*FIGURE\b)[^\[\]\n]{1,80}\]\]", "", body)
    summary = summary.replace(VERIFIED_MARK, "").replace(UNKNOWN_FACT, "")
    if any(m in summary for m in (BOUND_FLIP, PERIOD_FLIP, REPEATED_FACT)):  # the summary: not checked per sentence
        summary = " ".join(x for x in re.split(SENTENCE_END + r"\s+", summary)
                           if not any(m in x for m in (BOUND_FLIP, PERIOD_FLIP, REPEATED_FACT)))
    summary = CITE_RE.sub(fix, summary.strip())
    # Figures: keep only those referenced in the prose; place missing markers after the first reference.
    numbers = {f.figure_number for f in figures}
    # "Figure N" for a number this section was not given (another section's figure, or a
    # label the writer put on its own table) would be renumbered onto the wrong chart.
    foreign = {int(n) for n in FIGURE_REF_RE.findall(body)} - numbers - set(own_figures or ())
    if foreign:
        body = rewrite_removed_figure_refs(body, foreign)
    referenced = {int(n) for n in FIGURE_REF_RE.findall(body)} & numbers
    for fig in figures:
        n = fig.figure_number
        marker = f"[[FIGURE {n}]]"
        if n not in referenced:
            body = re.sub(rf"\[\[\s*FIGURE\s+{n}\s*\]\]", "", body, flags=re.I)
            draft.figures_dropped.append(n)
            fig.status, fig.drop_reason = "dropped", "not referenced or interpreted by the section text"
            continue
        if not re.search(rf"\[\[\s*FIGURE\s+{n}\s*\]\]", body, flags=re.I):
            paragraphs = re.split(r"\n\s*\n", body)
            idx = next((i for i, p in enumerate(paragraphs) if re.search(rf"\bFigure\s+{n}\b", p)), len(paragraphs) - 1)
            paragraphs.insert(idx + 1, marker)
            body = "\n\n".join(paragraphs)
        # Claim = the sentence that interprets the figure, without markers/headings
        # (a marker on the line above used to end up inside the chart takeaway).
        prose = FIGURE_MARKER_RE.sub(" ", re.sub(r"(?m)^\s*#{1,6}\s.*$", " ", body))
        sentence = next((s for s in re.split(SENTENCE_END + r"\s+|\n\s*\n", prose) if re.search(rf"\bFigure\s+{n}\b", s)), "")
        fig.claim = re.sub(r"\s+([.,;:])", r"\1", re.sub(r"\s+", " ", CITE_RE.sub("", sentence))).strip()[:400]
        fig.status = "referenced"
        draft.figures_referenced.append(n)
    # Stray markers for figures this section was not given.
    body = FIGURE_MARKER_RE.sub(lambda m: m.group(0) if int(m.group(1)) in referenced else "", body)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    draft.body = body
    draft.summary = re.sub(r"\s+", " ", summary)[:900]
    draft.invalid_citations = invalid
    draft.cited_passages = sorted({t.strip().upper() for g in CITE_RE.findall(body) for t in re.split(r"\s*[,;]\s*", g) if "." in t})
    draft.unsupported_numbers = unsupported_numbers(body, allowed_numbers, passage_texts)
    draft.words = len(re.findall(r"[A-Za-z][A-Za-z'’-]*", CITE_RE.sub(" ", body)))
    draft.ok = draft.words >= min_words
    if not draft.ok:
        draft.error = draft.error or f"section text too short ({draft.words} words)"
    return draft


def section_call_words() -> int:
    """Words asked of one writing call. Gemini answers ~1,100-1,300 words per call whatever it is asked: in the
    coal live run (2026-11-26) every section asked for 2,520 words in one call came back at ~45% even with the
    extension, while calls asked for 720-1,320 words delivered 83-110%. Sections are split into calls of this size."""
    try:
        return max(600, int(os.getenv("GG_V2_SECTION_CALL_WORDS", "1400")))
    except ValueError:
        return 1400


def _chunks(subsections: list, max_words: int | None = None) -> list[list]:
    max_words = max_words or section_call_words()
    groups, current, words = [], [], 0
    for sub in subsections:
        if current and words + sub.word_budget > max_words:
            groups.append(current)
            current, words = [], 0
        current.append(sub)
        words += sub.word_budget
    if current:
        groups.append(current)
    return groups or [[]]


def _store_fact_sheet(store):
    """One fact sheet over the whole evidence store, built once and reused by every section and by the checks
    after writing (utils.fact_sheet.fact_sheet_for keeps the single cache; a second cache here under the same
    attribute name crashed the checks with "'FactSheet' object is not subscriptable")."""
    if store is None or not getattr(store, "passages", None):
        return None
    from utils.fact_sheet import fact_sheet_for
    return fact_sheet_for(store)


async def write_section(gateway, store, section, figures: list, *, question: str, thesis: str,
                        already_covered: list[str], other_sections: list[str], allow_extension: bool = True,
                        budget_scale: float = 1.0) -> SectionDraft:
    """``allow_extension`` / ``budget_scale``: the in-depth runtime deadline (Stage H) skips the optional
    extension call and, past the hard stop, writes the section concisely from the same evidence."""
    draft = SectionDraft(section_id=section.section_id, title=section.title)
    passages = [p for p in (store.get(pid) for pid in section.passage_ids) if p]
    for leaf in section.leaves():
        if leaf is not section:
            passages += [p for p in (store.get(pid) for pid in leaf.passage_ids) if p and p not in passages]
    passages.sort(key=lambda p: -p.reliability)
    if not passages:
        draft.error = "no evidence passages assigned"
        return draft
    # Passages the writer may legitimately cite beyond its own: those behind its figures,
    # and those cited in the ALREADY COVERED summaries it is told to build on (repeating
    # "FMCG fell 8.8% [S19.5]" from an earlier section is reported, not unsupported).
    extra_ids = {pid for f in figures for pid in f.passage_ids}
    extra_ids |= {t.strip().upper() for line in already_covered for g in CITE_RE.findall(line)
                  for t in re.split(r"\s*[,;]\s*", g) if "." in t}
    extra = [p for p in (store.get(pid) for pid in sorted(extra_ids)) if p and p not in passages]
    citable = passages + extra
    allowed_passages = {p.passage_id for p in citable}
    passage_numbers = {p.passage_id: _number_set([p.text]) for p in citable}
    passage_texts = {p.passage_id: p.text for p in citable}
    from utils.section_charts import plain_number
    allowed_numbers = _number_set(p.text for p in citable) | _number_set(
        f"{v:g} {plain_number(v)}" for f in figures for _, _, v in f.points)
    own_figures = {f.figure_number for f in figures}
    subs = [s for s in section.subsections if s.status == "keep"]
    groups = _chunks(subs) if subs else [[]]
    draft.budget = max(150, int(section.word_budget * budget_scale))
    bodies, summaries = [], []
    for gi, group in enumerate(groups):
        group_passage_ids = set(pid for s in group for pid in s.passage_ids) or allowed_passages
        group_passages = [p for p in passages if p.passage_id in group_passage_ids] or passages
        group_figures = [f for f in figures if not group or set(f.passage_ids) & group_passage_ids or gi == 0]
        budget = sum(s.word_budget for s in group) if group else section.word_budget
        budget = max(150, int(budget * budget_scale))
        group_facts = numbered_facts(group_passages)
        prompt = build_section_prompt(
            question=question, thesis=thesis, title=section.title, goal=section.goal,
            subsections=[{"title": s.title, "goal": s.goal, "words": s.word_budget} for s in group],
            word_budget=budget, passages=group_passages, figures=group_figures,
            already_covered=already_covered + summaries, other_sections=other_sections, facts=group_facts,
        )
        max_tokens = min(16384, max(3072, int(budget * 2.2) + 800))
        result = await gateway.generate(purpose="section", section_id=f"{section.section_id}#{gi + 1}", system=SYSTEM,
                                        prompt=prompt, max_tokens=max_tokens, temperature=0.25, timeout=150, retries=1,
                                        # a fallback answer under half the budget waits for Gemini (fallback consistency)
                                        min_words=int(budget * 0.5))
        draft.calls += 1
        if result.ok:
            draft.models.append(getattr(result, "model", "") or "?")
        if not result.ok:
            draft.error = f"{draft.error}; " if draft.error else ""
            draft.error += f"part {gi + 1}: {result.error}"
            continue
        part = SectionDraft(section_id=draft.section_id, title=draft.title)
        postprocess(part, result.text, allowed_passages=allowed_passages, allowed_numbers=allowed_numbers,
                    figures=[f for f in group_figures if f.status != "referenced"], passage_numbers=passage_numbers,
                    passage_texts=passage_texts, own_figures=own_figures, context_entity=getattr(section, "entity", ""),
                    fallback_sheet=_store_fact_sheet(store), fact_refs=group_facts)
        if not part.ok:
            draft.error = (f"{draft.error}; " if draft.error else "") + f"part {gi + 1}: {part.error}"
        if part.ok:
            bodies.append(part.body)
            summaries.append(part.summary)
            draft.invalid_citations += part.invalid_citations
            draft.repaired_citations += part.repaired_citations
            draft.unsupported_numbers += part.unsupported_numbers
            draft.calculated_numbers += part.calculated_numbers
            draft.fact_refs_used += part.fact_refs_used
            draft.removed_sentences += part.removed_sentences
            draft.rewritten_numbers += part.rewritten_numbers
            draft.figures_referenced += part.figures_referenced
    # Evidence-driven extension: a short section that left several of its own
    # passages uncited gets ONE more call with only those unused passages. More
    # words come only from evidence not yet used, so this cannot become padding.
    body_so_far = "\n\n".join(bodies).strip()
    words_so_far = len(re.findall(r"[A-Za-z][A-Za-z'’-]*", CITE_RE.sub(" ", body_so_far)))
    cited_so_far = {t.strip().upper() for g in CITE_RE.findall(body_so_far) for t in re.split(r"\s*[,;]\s*", g)}
    unused = [p for p in passages if p.passage_id not in cited_so_far]
    if allow_extension and body_so_far and words_so_far < 0.7 * section.word_budget * budget_scale and len(unused) >= 3:
        remaining = max(250, section.word_budget - words_so_far)
        prompt = (
            f"REPORT QUESTION: {question}\nREPORT THESIS: {thesis}\n\nSECTION: {section.title}\nSECTION GOAL: {section.goal}\n\n"
            f"The section so far is below. Extend it by about {remaining} words using ONLY the UNUSED EVIDENCE PASSAGES: "
            "add analysis the section does not yet contain (further comparisons, mechanisms, exceptions, implications). "
            "Do not restate existing paragraphs. Output only new markdown to append (paragraphs and, if useful, one or two "
            "new H3 subsections), then <<<SUMMARY>>> and 1-2 sentences on what the addition established. If the unused "
            "passages add nothing material, output only <<<SUMMARY>>> followed by NONE.\n\n"
            f"SECTION SO FAR:\n{body_so_far[-9000:]}\n\nALREADY COVERED by earlier sections:\n"
            + "\n".join(f"- {c}" for c in already_covered) + "\n\nUNUSED EVIDENCE PASSAGES (cite by id):\n\n"
            + "\n\n".join(f"[{p.passage_id}] {p.publisher or p.title[:60]} | {p.published or 'undated'} | {p.authority_tier} | "
                          f"reliability {p.reliability:.2f}\n{p.text}" for p in unused)
        )
        result = await gateway.generate(purpose="section_extend", section_id=f"{section.section_id}#ext", system=SYSTEM,
                                        prompt=prompt, max_tokens=min(12288, int(remaining * 2.2) + 800),
                                        temperature=0.25, timeout=150, retries=1, min_words=int(remaining * 0.5))
        draft.calls += 1
        if result.ok and not re.match(r"^\s*<<<SUMMARY>>>\s*NONE", result.text.strip()):
            extra = SectionDraft(section_id=draft.section_id, title=draft.title)
            postprocess(extra, result.text, allowed_passages={p.passage_id for p in unused} | cited_so_far,
                        allowed_numbers=allowed_numbers, figures=[], min_words=40, passage_numbers=passage_numbers,
                        passage_texts=passage_texts, own_figures=own_figures, context_entity=getattr(section, "entity", ""),
                    fallback_sheet=_store_fact_sheet(store))
            if extra.ok:
                draft.extended = True
                draft.models.append(getattr(result, "model", "") or "?")
                # The extension continues the section: a heading it repeats (the section already has
                # it) is dropped and its text joins the section, so no heading appears twice.
                seen = {re.sub(r"\W+", " ", h).strip().casefold()
                        for h in re.findall(r"(?m)^#{3,6}\s+(.+?)\s*$", "\n\n".join(bodies))}
                extra.body = re.sub(r"(?m)^#{3,6}\s+(.+?)\s*$\n?",
                                    lambda m: "" if re.sub(r"\W+", " ", m.group(1)).strip().casefold() in seen else m.group(0),
                                    extra.body).strip()
                bodies.append(extra.body)
                if extra.summary:
                    summaries.append(extra.summary)
                draft.invalid_citations += extra.invalid_citations
                draft.repaired_citations += extra.repaired_citations
                draft.unsupported_numbers += extra.unsupported_numbers
                draft.calculated_numbers += extra.calculated_numbers
                draft.removed_sentences += extra.removed_sentences
                draft.rewritten_numbers += extra.rewritten_numbers
    # Fetched-dataset charts the text did not interpret keep their place: the figure goes at the end of
    # the section with its deterministic interpretation (built from the plotted rows), next to it.
    for fig in figures:
        if fig.status != "referenced" and (getattr(fig, "interpretation", "") or getattr(fig, "reading", "")) and bodies:
            n = fig.figure_number
            text = getattr(fig, "interpretation", "") or getattr(fig, "reading", "")
            if re.match(r"(?:Compounded|In)\b", text) or (
                    not getattr(fig, "interpretation", "") and text[:2] != text[:2].upper()):
                text = text[0].lower() + text[1:]
            bodies[-1] = f"{bodies[-1].rstrip()}\n\n[[FIGURE {n}]]\n\nAs Figure {n} shows, {text}"
            fig.status, fig.drop_reason = "referenced", ""
            fig.claim = re.sub(r"\s+", " ", CITE_RE.sub("", f"As Figure {n} shows, {text}")).strip()[:400]
            draft.figures_dropped = [x for x in draft.figures_dropped if x != n]
            draft.figures_referenced.append(n)
    # Figures never referenced in any part are dropped.
    for fig in figures:
        if fig.status != "referenced":
            fig.status = "dropped"
            fig.drop_reason = fig.drop_reason or "not referenced or interpreted by the section text"
            draft.figures_dropped.append(fig.figure_number)
    draft.body = "\n\n".join(bodies).strip()
    draft.summary = " ".join(summaries)[:1400]
    draft.cited_passages = sorted({t.strip().upper() for g in CITE_RE.findall(draft.body) for t in re.split(r"\s*[,;]\s*", g) if "." in t})
    draft.words = len(re.findall(r"[A-Za-z][A-Za-z'’-]*", CITE_RE.sub(" ", draft.body)))
    draft.ok = draft.words >= 60
    if draft.ok:
        draft.error = ""
    return draft
