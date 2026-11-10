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
import re
from dataclasses import dataclass, field

from utils.figure_refs import rewrite_removed_figure_refs
from utils.number_policy import apply_number_policy, statistic_tokens

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
    removed_sentences: list[dict] = field(default_factory=list)
    rewritten_numbers: list[dict] = field(default_factory=list)
    figures_referenced: list[int] = field(default_factory=list)
    figures_dropped: list[int] = field(default_factory=list)
    calls: int = 0
    words: int = 0


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
    text = re.sub(r"[-+−]?\d[\d,]*(?:\.\d+)?[^()\d\n]{0,30}\(calculated:[^)]*\)", " ", body)
    sheet = None
    if passage_texts:
        from types import SimpleNamespace

        from utils.fact_sheet import FactSheet
        sheet = FactSheet([SimpleNamespace(passage_id=k, text=v) for k, v in passage_texts.items()])
    out = []
    for token in statistic_tokens(text):
        norm = _normalize_number(token)
        plain = norm.lstrip("-")
        if norm not in allowed and plain not in allowed and not (sheet and sheet.candidates(token.rstrip(",."))):
            out.append(token)
    return out


def build_section_prompt(*, question: str, thesis: str, title: str, goal: str, subsections: list[dict], word_budget: int,
                         passages: list, figures: list, already_covered: list[str], other_sections: list[str]) -> str:
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
{fact_block(passages)}
EVIDENCE PASSAGES (cite by id):
{ev}"""


def fact_block(passages, limit: int = 60) -> str:
    """The section's fact sheet, compact: the numbers the writer may quote, each with its unit, what it
    measures, whose it is and its passage (Stage G: most removed numbers were a right value attached to
    the wrong metric/entity, or a calculation on inexact inputs)."""
    try:
        from utils.fact_sheet import FactSheet
        facts = [f for f in FactSheet(passages).facts if f.kind == "table" and f.metric and f.entity
                 and not re.fullmatch(r"(?:19|20)\d{2}", f.value)]
    except Exception:  # the block is an aid; the section still has its passages
        return ""
    seen, lines = set(), []
    for f in facts:
        key = (f.value, f.unit, f.entity.casefold(), f.metric.casefold()[:30])
        if key in seen:
            continue
        seen.add(key)
        metric = re.sub(r"\s+", " ", f.metric)[:50]
        lines.append(f"- {f.raw}{(' ' + f.unit) if f.unit and f.unit not in f.raw else ''} | {metric or '-'} | "
                     f"{(f.label or f.entity)[:40] or '-'} | {f.period or '-'} | [{f.passage_id}]")
        if len(lines) >= limit:
            break
    if not lines:
        return ""
    return ("\nFACT SHEET (numbers you may quote, as value | what it measures | whose | period | passage). Quote a number "
            "only with the metric and entity shown here; use these exact values as [[calc: ...]] inputs:\n"
            + "\n".join(lines) + "\n")


def _sentence_numbers(sentence: str) -> list[str]:
    """Factual numbers in a sentence (no citations, figure refs, periods, durations, counts, small ordinals)."""
    return [_normalize_number(t).lstrip("-") for t in statistic_tokens(re.sub(r"\(calculated:[^)]*\)", " ", sentence))]


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
    pieces = re.split(r"(?<=[.!?])(\s+)", body)
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
                context_entity: str = "") -> SectionDraft:
    text = str(raw or "").strip()
    if SUMMARY_MARKER in text:
        body, summary = text.split(SUMMARY_MARKER, 1)
    else:
        body, summary = text, ""
    body = re.sub(r"(?m)^\s*#\s+.*$|^\s*##\s+.*$", "", body).strip()  # the pipeline owns H1/H2 headings
    body = unfence(body)
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
                                     section_id=draft.section_id, context_entity=context_entity)
        body = policy.body
        draft.calculated_numbers += policy.calculated
        draft.removed_sentences += policy.removed
        draft.rewritten_numbers += policy.rewritten
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
        sentence = next((s for s in re.split(r"(?<=[.!?])\s+|\n\s*\n", prose) if re.search(rf"\bFigure\s+{n}\b", s)), "")
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


def _chunks(subsections: list, max_words: int = 2600) -> list[list]:
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
    bodies, summaries = [], []
    for gi, group in enumerate(groups):
        group_passage_ids = set(pid for s in group for pid in s.passage_ids) or allowed_passages
        group_passages = [p for p in passages if p.passage_id in group_passage_ids] or passages
        group_figures = [f for f in figures if not group or set(f.passage_ids) & group_passage_ids or gi == 0]
        budget = sum(s.word_budget for s in group) if group else section.word_budget
        budget = max(150, int(budget * budget_scale))
        prompt = build_section_prompt(
            question=question, thesis=thesis, title=section.title, goal=section.goal,
            subsections=[{"title": s.title, "goal": s.goal, "words": s.word_budget} for s in group],
            word_budget=budget, passages=group_passages, figures=group_figures,
            already_covered=already_covered + summaries, other_sections=other_sections,
        )
        max_tokens = min(16384, max(3072, int(budget * 2.2) + 800))
        result = await gateway.generate(purpose="section", section_id=f"{section.section_id}#{gi + 1}", system=SYSTEM,
                                        prompt=prompt, max_tokens=max_tokens, temperature=0.25, timeout=150, retries=1)
        draft.calls += 1
        if not result.ok:
            draft.error = f"{draft.error}; " if draft.error else ""
            draft.error += f"part {gi + 1}: {result.error}"
            continue
        part = SectionDraft(section_id=draft.section_id, title=draft.title)
        postprocess(part, result.text, allowed_passages=allowed_passages, allowed_numbers=allowed_numbers,
                    figures=[f for f in group_figures if f.status != "referenced"], passage_numbers=passage_numbers,
                    passage_texts=passage_texts, own_figures=own_figures, context_entity=getattr(section, "entity", ""))
        if not part.ok:
            draft.error = (f"{draft.error}; " if draft.error else "") + f"part {gi + 1}: {part.error}"
        if part.ok:
            bodies.append(part.body)
            summaries.append(part.summary)
            draft.invalid_citations += part.invalid_citations
            draft.repaired_citations += part.repaired_citations
            draft.unsupported_numbers += part.unsupported_numbers
            draft.calculated_numbers += part.calculated_numbers
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
                                        temperature=0.25, timeout=150, retries=1)
        draft.calls += 1
        if result.ok and not re.match(r"^\s*<<<SUMMARY>>>\s*NONE", result.text.strip()):
            extra = SectionDraft(section_id=draft.section_id, title=draft.title)
            postprocess(extra, result.text, allowed_passages={p.passage_id for p in unused} | cited_so_far,
                        allowed_numbers=allowed_numbers, figures=[], min_words=40, passage_numbers=passage_numbers,
                        passage_texts=passage_texts, own_figures=own_figures, context_entity=getattr(section, "entity", ""))
            if extra.ok:
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
