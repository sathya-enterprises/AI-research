"""Claim check: does the cited passage say what the sentence says?

The number checks pin every figure to its source, but a sentence without a figure ("growth was driven by rural
demand", "the RBI cut rates because of food inflation") was never compared with the passage it cites. Each
cited prose sentence is checked:

1. word overlap - a sentence whose content words are mostly in its cited passages is accepted;
2. the rest go, in one batched call per section, to a judge model that reads the sentence and the best-matching
   excerpt of each cited passage: SUPPORTED / PARTIAL / UNSUPPORTED.

UNSUPPORTED sentences are removed (logged with the judge's reason, like the number policy's removals); a judge
failure removes nothing. At most a third of a section's checked sentences are removed: a judge that rejects
more than that is more likely wrong than the section.
"""
from __future__ import annotations

import json
import logging
import re

from utils.evidence_text import SENTENCE_END

log = logging.getLogger("claim_check")

CITE_RE = re.compile(r"\[(S\d+(?:\.\d+)?(?:\s*[,;]\s*S\d+(?:\.\d+)?)*)\]")
_STOP = set("""the a an and or of in on to for from by with as at is are was were be been being has have had that this
these those which who whom whose it its their there than then also such into over under between during after before
about against across while within without more most less least other some any each both all per not but can could
would should may might will shall do does did so very much many own same new year years report reports data""".split())
ACCEPT_OVERLAP = 0.6
MAX_JUDGED_PER_SECTION = 24
MAX_REMOVED_SHARE = 1 / 3

JUDGE_SYSTEM = (
    "You check a research report against its sources. For each numbered CLAIM you get the EXCERPTS of the "
    "sources it cites. Answer whether the excerpts support the claim:\n"
    "SUPPORTED - the excerpts state it, or it follows directly from them;\n"
    "PARTIAL - the excerpts support the main point, but a detail (cause, scope, wording) goes further;\n"
    "UNSUPPORTED - the excerpts do not say it, or say something different.\n"
    "Judge only against the excerpts, not your own knowledge. Reply with JSON only: "
    '[{"i": 1, "verdict": "SUPPORTED|PARTIAL|UNSUPPORTED", "reason": "<12 words>"}]'
)


def _stems(text: str) -> set[str]:
    words = re.findall(r"[A-Za-z][A-Za-z\-]{3,}", str(text or "").casefold())
    return {w[:6] for w in words if w not in _STOP}


def _prose_sentences(body: str) -> list[str]:
    """Cited prose sentences (not tables, headings, figure markers, callouts or list labels)."""
    out = []
    for paragraph in re.split(r"\n\s*\n", body or ""):
        # Headings sit on the paragraph's first line; wrapped lines belong to one sentence; list items are
        # their own lines.
        lines = [ln for ln in paragraph.split("\n") if ln.strip() and not ln.lstrip().startswith(("#", ">"))]
        if not lines or lines[0].lstrip().startswith(("|", ">", "[[", "[CHART", "**Table", "Source:")):
            continue
        blocks, current = [], []
        for line in lines:
            if re.match(r"^\s*(?:[-*•]|\d+[.)])\s+", line):
                if current:
                    blocks.append(" ".join(current))
                current = [re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", line)]
            else:
                current.append(line.strip())
        if current:
            blocks.append(" ".join(current))
        for block in blocks:
            for sentence in re.split(SENTENCE_END + r"\s+", block):
                if CITE_RE.search(sentence) and len(_stems(CITE_RE.sub(" ", sentence))) >= 4:
                    out.append(sentence.strip())
    return out


def _cited_texts(sentence: str, passage_texts: dict[str, str]) -> list[tuple[str, str]]:
    ids = [t.strip().upper() for g in CITE_RE.findall(sentence) for t in re.split(r"\s*[,;]\s*", g)]
    out = []
    for cited in dict.fromkeys(ids):
        if "." in cited:
            if cited in passage_texts:
                out.append((cited, passage_texts[cited]))
        else:  # a source-level citation covers all of the source's passages
            out += [(pid, text) for pid, text in passage_texts.items() if pid.split(".")[0] == cited]
    return out


def overlap(sentence: str, texts: list[str]) -> float:
    claim = _stems(CITE_RE.sub(" ", sentence))
    if not claim:
        return 1.0
    evidence = set().union(*(_stems(t) for t in texts)) if texts else set()
    return len(claim & evidence) / len(claim)


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "").rstrip("0").rstrip(".") for n in re.findall(r"\d[\d,]*(?:\.\d+)?", str(text or ""))
            if len(n.replace(",", "")) >= 2}


def _score(sentence: str, text: str) -> int:
    """Shared words, with the claim's numbers weighted: the passage that holds "339 EUR/MWh" is the one to read."""
    claim = CITE_RE.sub(" ", sentence)
    return len(_stems(claim) & _stems(text)) + 4 * len(_numbers(claim) & _numbers(text))


def _excerpt(sentence: str, text: str, width: int = 700) -> str:
    """The window of ``text`` that best matches ``sentence`` (its words and numbers)."""
    text = re.sub(r"\s+", " ", text or "")
    if len(text) <= width:
        return text
    best, best_at = -1, 0
    for at in range(0, len(text) - width + 1, max(100, width // 4)):
        score = _score(sentence, text[at:at + width])
        if score > best:
            best, best_at = score, at
    return text[best_at:best_at + width]


def _best_excerpts(sentence: str, cited: list[tuple[str, str]], keep: int = 3) -> list[tuple[str, str]]:
    """The ``keep`` cited passages that match the sentence best (a source-level citation covers dozens), each
    cut to its best window. Europe gas: the first three passages of [S13] were sent, not the one with the claim."""
    ranked = sorted(cited, key=lambda item: -_score(sentence, item[1]))
    return [(pid, _excerpt(sentence, text)) for pid, text in ranked[:keep]]


def candidates(body: str, passage_texts: dict[str, str]) -> tuple[list[dict], int]:
    """Sentences the overlap pass could not accept (to be judged), and how many cited sentences were checked."""
    checked, judge = 0, []
    for sentence in _prose_sentences(body):
        cited = _cited_texts(sentence, passage_texts)
        if not cited:
            continue
        checked += 1
        score = overlap(sentence, [t for _, t in cited])
        if score < ACCEPT_OVERLAP:
            judge.append({"sentence": sentence, "overlap": round(score, 2),
                          "excerpts": _best_excerpts(sentence, cited)})
    return judge[:MAX_JUDGED_PER_SECTION], checked


def judge_prompt(items: list[dict]) -> str:
    parts = []
    for i, item in enumerate(items, 1):
        claim = re.sub(r"\s+", " ", CITE_RE.sub("", item["sentence"].replace("⁠", ""))).strip()
        excerpts = "\n".join(f"  [{pid}] {text}" for pid, text in item["excerpts"])
        parts.append(f"CLAIM {i}: {claim}\nEXCERPTS:\n{excerpts}")
    return "\n\n".join(parts)


def parse_verdicts(raw, count: int) -> dict[int, tuple[str, str]]:
    """{claim number: (verdict, reason)} from the judge's JSON; unknown or malformed entries are ignored."""
    if isinstance(raw, str):
        try:
            start = raw.find("[")
            raw = json.loads(raw[start:raw.rfind("]") + 1]) if start >= 0 else None
        except ValueError:
            raw = None
    out = {}
    for entry in raw if isinstance(raw, list) else []:
        try:
            i = int(entry.get("i"))
        except (TypeError, ValueError, AttributeError):
            continue
        verdict = str(entry.get("verdict") or "").upper()
        if 1 <= i <= count and verdict in {"SUPPORTED", "PARTIAL", "UNSUPPORTED"}:
            out[i] = (verdict, str(entry.get("reason") or "")[:160])
    return out


def remove_sentences(body: str, sentences: list[str]) -> str:
    for sentence in sentences:
        body = body.replace(sentence, "", 1)
    body = re.sub(r"[ \t]{2,}", " ", body)
    body = re.sub(r"(?m)^[ \t]+(?=\S)", "", body)
    return re.sub(r"\n{3,}", "\n\n", body).strip()


async def check_section(gateway, draft, passage_texts: dict[str, str]) -> dict:
    """Judge one section's weakly matched sentences and remove the unsupported ones. Returns its log."""
    items, checked = candidates(draft.body, passage_texts)
    entry = {"section": draft.section_id, "checked": checked, "judged": len(items), "removed": [], "partial": 0}
    if not items or gateway is None:
        return entry
    result = await gateway.generate(purpose="claim_check", section_id=draft.section_id, system=JUDGE_SYSTEM,
                                    prompt=judge_prompt(items), max_tokens=2048, temperature=0.0, json_mode=True,
                                    timeout=90)
    if not getattr(result, "ok", False):
        entry["error"] = str(getattr(result, "error", "") or "judge failed")[:120]
        return entry
    verdicts = parse_verdicts(result.json() if hasattr(result, "json") else result.text, len(items))
    unsupported = [(items[i - 1], reason) for i, (v, reason) in sorted(verdicts.items()) if v == "UNSUPPORTED"]
    entry["partial"] = sum(1 for v, _ in verdicts.values() if v == "PARTIAL")
    limit = max(1, int(checked * MAX_REMOVED_SHARE))
    if len(unsupported) > limit:
        entry["capped"] = len(unsupported) - limit
        unsupported = sorted(unsupported, key=lambda x: x[0]["overlap"])[:limit]  # the weakest matches go first
    if unsupported:
        draft.body = remove_sentences(draft.body, [item["sentence"] for item, _ in unsupported])
        entry["removed"] = [{"sentence": item["sentence"][:300], "reason": reason, "overlap": item["overlap"]}
                            for item, reason in unsupported]
    return entry
