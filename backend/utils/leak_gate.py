"""Leak gate: no writer-prompt text in the published report (Stage I).

Stage H's methodology printed the window instructions given to the writer ("QUARTER WINDOW
(pre-computed - use these exact labels and dates, do NOT recompute ...") and the raw question. Any
sentence of the final text that shares an 8-word run with a prompt template, or that carries an
imperative instruction phrase, is removed (logged); the caller re-runs the other gates.
"""
from __future__ import annotations

import logging
import re
from functools import lru_cache

log = logging.getLogger("leak_gate")

# Imperative phrases from the prompts. The prompts' own markers are upper case, so they match case-
# sensitively ("eight-quarter window" or "evidence passages" in prose are not leaks).
INSTRUCTION_RE = re.compile(
    r"(?i:\bdo\s+not\s+recompute\b|\buse these exact\b|\bpre-computed\b|\(\s*goal\s*:|~\s?\d{2,5}\s+words\b"
    r"|\breport it separately\b|\bnever mixed into completed-quarter\b|\bcite by id\b)"
    r"|\bdo NOT\b|\bQUARTER WINDOW\b|\bTIME WINDOW:|\bCURRENT QUARTER TO DATE:|\bEVIDENCE PASSAGES\b"
    r"|\bTHE QUESTION ASKS FOR\b|\bReturn (?:valid )?JSON\b|<<<SUMMARY>>>")
# Our own report boilerplate (built from code templates) is not a leak.
_OWN_BOILERPLATE_RE = re.compile(r"^\s*\*\*(?:Coverage of the question|Verification|Evidence base|Reliability scoring|"
                                 r"How the report was built|Entity coverage|Scope decisions|Length)\.\*\*")
_WORD_RE = re.compile(r"[a-z0-9]+")
_N = 8


def _words(text: str) -> list[str]:
    return _WORD_RE.findall(str(text or "").casefold())


@lru_cache(maxsize=1)
def _template_ngrams() -> frozenset:
    """8-word runs of every prompt template the pipeline sends (rendered with neutral sample values)."""
    texts: list[str] = []
    try:
        from utils import section_writer
        texts.append(section_writer.SYSTEM)
        texts.append(section_writer.build_section_prompt(
            question="QQ", thesis="TT", title="XX", goal="GG", subsections=[], word_budget=500, passages=[],
            figures=[], already_covered=[], other_sections=[]))
    except Exception:
        pass
    try:
        from utils.report_pipeline_v2 import EXEC_SYSTEM
        texts.append(EXEC_SYSTEM)
    except Exception:
        pass
    try:
        from utils.editorial_pass import TRANSITION_SYSTEM
        texts.append(TRANSITION_SYSTEM)
    except Exception:
        pass
    try:
        from utils.report_outline import outline_prompt
        for tier in ("detailed", "in_depth"):
            system, prompt = outline_prompt("QQ", tier, "OVERVIEW", "2026-01-01", "")
            texts += [system, prompt]
    except Exception:
        pass
    try:
        from datetime import date
        from utils.question_asks import parse_asks, window_note
        texts.append(window_note("in the past 8 quarters", date(2026, 11, 8)))
        texts.append(window_note("in the past 1 year", date(2026, 11, 8)))
        texts.append(parse_asks("List down the why, what, how of all the FTAs signed in the past 1 year, "
                                "with an exhaustive list, easy to understand by UHNIs, compared internationally",
                                date(2026, 11, 8)).prompt_block())
    except Exception:
        pass
    try:
        from datetime import date
        from routes.report import _past_n_quarters_anchor
        texts.append(_past_n_quarters_anchor("in the past 8 quarters", date(2026, 11, 8)))
    except Exception:
        pass
    grams = set()
    for text in texts:
        words = _words(text)
        grams.update(" ".join(words[i:i + _N]) for i in range(max(0, len(words) - _N + 1)))
    return frozenset(grams)


def leaked(sentence: str) -> str:
    """Why ``sentence`` is leaked prompt text ('' when it is not)."""
    if _OWN_BOILERPLATE_RE.match(sentence or ""):
        return ""
    if INSTRUCTION_RE.search(sentence or ""):
        return "instruction phrase"
    words = _words(sentence)
    grams = _template_ngrams()
    for i in range(max(0, len(words) - _N + 1)):
        if " ".join(words[i:i + _N]) in grams:
            return "matches a prompt template"
    return ""


_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z*\[(\"'])")


def remove_leaks(text: str) -> tuple[str, list[dict]]:
    """Remove every line (list item / heading-free line) or sentence that is leaked prompt text."""
    actions: list[dict] = []
    out_lines = []
    for line in str(text or "").split("\n"):
        if not line.strip() or line.lstrip().startswith("#"):
            out_lines.append(line)
            continue
        if line.lstrip().startswith(("|", "-", "*", ">")) or len(line) < 200:
            reason = leaked(line)
            if reason:
                actions.append({"gate": "leakedInstructions", "action": "removed", "detail": line.strip()[:240], "reason": reason})
                continue
            out_lines.append(line)
            continue
        kept = []
        for sentence in _SPLIT_RE.split(line):
            reason = leaked(sentence)
            if reason:
                actions.append({"gate": "leakedInstructions", "action": "removed", "detail": sentence.strip()[:240], "reason": reason})
                continue
            kept.append(sentence)
        if kept:
            out_lines.append(" ".join(kept))
    for action in actions:
        log.info("Leak gate: removed %s (%s)", action["detail"][:100], action["reason"])
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out_lines)), actions
