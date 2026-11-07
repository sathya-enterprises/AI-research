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

import re
from dataclasses import dataclass, field

SUMMARY_MARKER = "<<<SUMMARY>>>"
CITE_RE = re.compile(r"\[(S\d+(?:\.\d+)?(?:\s*[,;]\s*S\d+(?:\.\d+)?)*)\]")
FIGURE_MARKER_RE = re.compile(r"\[\[\s*FIGURE\s+(\d+)\s*\]\]", re.I)
FIGURE_REF_RE = re.compile(r"\b(?:Figure|Fig\.)\s+(\d+)\b")
_NUM_RE = re.compile(r"(?<![\w.\[])[-+−]?\d[\d,]*(?:\.\d+)?(?!\.\d)")

SYSTEM = """You are a senior equity research analyst writing ONE section of a longer research report.

NON-NEGOTIABLE RULES
1. Use only the EVIDENCE PASSAGES given to you. Do not use memory or outside knowledge for facts or figures.
2. Cite every factual sentence with the passage ids it relies on, in square brackets, e.g. [S12.3] or [S4.1, S9.2].
   Only cite ids that appear in EVIDENCE PASSAGES.
3. Numbers: state only numbers that appear in your passages or in the FIGURE data. Copy them exactly (same units).
   Do not calculate new numbers. If a number you would need is not in the evidence, say the evidence does not show it.
4. Figures: for each figure listed, put its marker [[FIGURE N]] on its own line where it belongs, and in the adjacent
   paragraph write "Figure N shows ..." and interpret it: what it compares, the key pattern, and why it matters for the
   section's goal. Use the figure's own values.
5. Do not repeat what is listed under ALREADY COVERED, and do not take over the goals of OTHER SECTIONS.
6. Write analytical prose (mechanisms, comparisons, implications), using the H3 subheadings given. Bullets only for
   genuine lists. No executive summary, no conclusion for the whole report, no source list.
7. If the evidence for a subsection is thin, write less rather than pad.

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
    unsupported_numbers: list[str] = field(default_factory=list)
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


def unsupported_numbers(body: str, allowed: set[str]) -> list[str]:
    text = CITE_RE.sub(" ", body)
    text = FIGURE_MARKER_RE.sub(" ", text)
    text = FIGURE_REF_RE.sub(" ", text)
    text = re.sub(r"\b(?:Q[1-4]|H[12])\s*(?:FY|CY)?\s*'?\d{2,4}\b|\bFY\s*'?\d{2,4}(?:\s*[-/–]\s*\d{2,4})?\b", " ", text, flags=re.I)
    out = []
    for token in _NUM_RE.findall(text):
        norm = _normalize_number(token)
        plain = norm.lstrip("-")
        if re.fullmatch(r"(?:19|20)\d{2}", plain) or plain in {"1", "2", "3", "4", "5", "0"}:
            continue  # years and small ordinals
        if norm not in allowed and plain not in allowed:
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

EVIDENCE PASSAGES (cite by id):
{ev}"""


def postprocess(draft: SectionDraft, raw: str, *, allowed_passages: set[str], allowed_numbers: set[str],
                figures: list, min_words: int = 60) -> SectionDraft:
    text = str(raw or "").strip()
    if SUMMARY_MARKER in text:
        body, summary = text.split(SUMMARY_MARKER, 1)
    else:
        body, summary = text, ""
    body = re.sub(r"(?m)^\s*#\s+.*$|^\s*##\s+.*$", "", body).strip()  # the pipeline owns H1/H2 headings
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
    summary = CITE_RE.sub(fix, summary.strip())
    # Figures: keep only those referenced in the prose; place missing markers after the first reference.
    numbers = {f.figure_number for f in figures}
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
        sentence = next((s for s in re.split(r"(?<=[.!?])\s+", body) if re.search(rf"\bFigure\s+{n}\b", s)), "")
        fig.claim = CITE_RE.sub("", sentence).strip()[:400]
        fig.status = "referenced"
        draft.figures_referenced.append(n)
    # Stray markers for figures this section was not given.
    body = FIGURE_MARKER_RE.sub(lambda m: m.group(0) if int(m.group(1)) in referenced else "", body)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    draft.body = body
    draft.summary = re.sub(r"\s+", " ", summary)[:900]
    draft.invalid_citations = invalid
    draft.cited_passages = sorted({t.strip().upper() for g in CITE_RE.findall(body) for t in re.split(r"\s*[,;]\s*", g) if "." in t})
    draft.unsupported_numbers = unsupported_numbers(body, allowed_numbers)
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
                        already_covered: list[str], other_sections: list[str]) -> SectionDraft:
    draft = SectionDraft(section_id=section.section_id, title=section.title)
    passages = [p for p in (store.get(pid) for pid in section.passage_ids) if p]
    for leaf in section.leaves():
        if leaf is not section:
            passages += [p for p in (store.get(pid) for pid in leaf.passage_ids) if p and p not in passages]
    passages.sort(key=lambda p: -p.reliability)
    if not passages:
        draft.error = "no evidence passages assigned"
        return draft
    allowed_passages = {p.passage_id for p in passages}
    allowed_numbers = _number_set(p.text for p in passages) | _number_set(
        f"{v:g}" for f in figures for _, _, v in f.points)
    subs = [s for s in section.subsections if s.status == "keep"]
    groups = _chunks(subs) if subs else [[]]
    bodies, summaries = [], []
    for gi, group in enumerate(groups):
        group_passage_ids = set(pid for s in group for pid in s.passage_ids) or allowed_passages
        group_passages = [p for p in passages if p.passage_id in group_passage_ids] or passages
        group_figures = [f for f in figures if not group or set(f.passage_ids) & group_passage_ids or gi == 0]
        budget = sum(s.word_budget for s in group) if group else section.word_budget
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
                    figures=[f for f in group_figures if f.status != "referenced"])
        if not part.ok:
            draft.error = (f"{draft.error}; " if draft.error else "") + f"part {gi + 1}: {part.error}"
        if part.ok:
            bodies.append(part.body)
            summaries.append(part.summary)
            draft.invalid_citations += part.invalid_citations
            draft.unsupported_numbers += part.unsupported_numbers
            draft.figures_referenced += part.figures_referenced
    # Evidence-driven extension: a short section that left several of its own
    # passages uncited gets ONE more call with only those unused passages. More
    # words come only from evidence not yet used, so this cannot become padding.
    body_so_far = "\n\n".join(bodies).strip()
    words_so_far = len(re.findall(r"[A-Za-z][A-Za-z'’-]*", CITE_RE.sub(" ", body_so_far)))
    cited_so_far = {t.strip().upper() for g in CITE_RE.findall(body_so_far) for t in re.split(r"\s*[,;]\s*", g)}
    unused = [p for p in passages if p.passage_id not in cited_so_far]
    if body_so_far and words_so_far < 0.7 * section.word_budget and len(unused) >= 3:
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
                        allowed_numbers=allowed_numbers, figures=[], min_words=40)
            if extra.ok:
                bodies.append(extra.body)
                if extra.summary:
                    summaries.append(extra.summary)
                draft.invalid_citations += extra.invalid_citations
                draft.unsupported_numbers += extra.unsupported_numbers
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
