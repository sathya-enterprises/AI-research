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
    "An excerpt's publication date dates what it states in the present tense. "
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


# ── code-first verification ─────────────────────────────────────────────────────────────────────────
# A claim is classified, checked by code where code can decide (its words and figures are in the passages it
# cites; a stated percentage change agrees with the stated start and end values), and only the rest go to the
# judge model. Each claim gets a structured record (id, sentence, type, sources, method, status, confidence,
# explanation, correction). Statuses: VERIFIED, PARTIALLY_VERIFIED, UNVERIFIED, CONTRADICTED, REQUIRES_REVIEW.
# A figure appearing in a source is not enough on its own: the sentence's subject words must be there as well.
NUMBERS_ACCEPT_OVERLAP = 0.4
_FORECAST_RE = re.compile(r"(?i)\b(?:will|would|expected|expects?|projected|projections?|forecast\w*|likely|"
                          r"outlook|estimate[sd]? to|set to|poised|anticipat\w+|target(?:s|ed)? (?:of|to)|by 20[3-9]\d)\b")
_INTERPRET_RE = re.compile(r"(?i)\b(?:because|due to|driven by|owing to|as a result|reflect\w*|suggest\w*|indicat\w*|"
                           r"implies|implying|led to|leading to|caus\w+|thanks to|amid|underscor\w+|signal\w*)\b")
_COMPARE_RE = re.compile(r"(?i)\b(?:higher|lower|more|less|larger|smaller|exceed\w*|outpac\w+|compared|versus|"
                         r"vs\.?|than|ahead of|behind|lead\w*|largest|highest|lowest)\b")
_CHANGE_RE = re.compile(
    r"(?i)from\s+(?:₹|rs\.?\s?|inr\s?|us\$|\$)?(?P<a>\d[\d,]*(?:\.\d+)?)\s*(?P<ua>%|[a-z]{1,8})?[^.;]{0,60}?\bto\s+"
    r"(?:₹|rs\.?\s?|inr\s?|us\$|\$)?(?P<b>\d[\d,]*(?:\.\d+)?)\s*(?P<ub>%|[a-z]{1,8})?[^.;]{0,80}?"
    r"(?P<sign>[-+−]?)(?P<pct>\d+(?:\.\d+)?)\s?%\s*(?:\)|,)?\s*(?P<word>increase|rise|growth|jump|gain|decline|fall|drop|"
    r"decrease|contraction|surge)")


def claim_type(sentence: str) -> str:
    text = CITE_RE.sub(" ", sentence)
    if _FORECAST_RE.search(text):
        return "forecast"
    if _CHANGE_RE.search(text):
        return "calculation"
    if _INTERPRET_RE.search(text):
        return "qualitative"  # an explanation ("reflecting", "because") is judged even when it holds figures
    if re.search(r"\d+(?:\.\d+)?\s?%|per ?cent", text):
        return "percentage"
    if re.search(r"\d", re.sub(r"\b(?:19|20)\d{2}\b", "", text)):
        return "comparison" if _COMPARE_RE.search(text) else "numerical"
    if re.search(r"\b(?:19|20)\d{2}\b", text):
        return "date"
    if _INTERPRET_RE.search(text):
        return "qualitative"
    return "historical"


def arithmetic_check(sentence: str) -> tuple[str, str] | None:
    """A stated percentage change that disagrees with its own start and end values: (explanation, corrected
    sentence). Only a clear "from A to B ... C% increase/decline" with like units is checked."""
    match = _CHANGE_RE.search(CITE_RE.sub(lambda m: " " * len(m.group(0)), sentence))
    if not match or (match.group("ua") or "") != (match.group("ub") or "") or (match.group("ua") or "") == "%":
        return None
    try:
        a, b = float(match.group("a").replace(",", "")), float(match.group("b").replace(",", ""))
        stated = float(match.group("pct"))
    except ValueError:
        return None
    if a <= 0:
        return None
    actual = (b / a - 1) * 100
    decline = match.group("word").casefold() in {"decline", "fall", "drop", "decrease", "contraction"}
    if (actual < 0) != decline and abs(actual) > 0.5:
        return None  # direction words disagree with the figures: not a clean change statement, leave it to the judge
    decimals = len(match.group("pct").split(".")[1]) if "." in match.group("pct") else 0

    def half_unit(raw: str) -> float:
        digits = raw.replace(",", "")
        return 0.5 * 10 ** -(len(digits.split(".")[1]) if "." in digits else 0)
    # The start and end values are rounded too: 9.2 -> 16.3 tonnes can be anything from 75.7% to 78.7%, so a
    # source's "76%" is consistent. Only a percentage outside every rounding of the inputs is contradicted.
    ha, hb = half_unit(match.group("a")), half_unit(match.group("b"))
    if a - ha <= 0:
        return None
    lowest = abs((b - hb) / (a + ha) - 1) * 100
    highest = abs((b + hb) / (a - ha) - 1) * 100
    low, high = min(lowest, highest), max(lowest, highest)
    slack = 0.5 * 10 ** -decimals
    if low - slack <= stated <= high + slack:
        return None
    fixed = f"{abs(actual):.{decimals}f}"
    start, end = match.span("pct")
    return (f"{match.group('a')} to {match.group('b')} is a {abs(actual):.1f}% change, not {stated:g}%",
            sentence[:start] + fixed + sentence[end:])


def _figures(text: str) -> set[str]:
    """The claim's figures (not years, not citation numbers), digits only."""
    text = CITE_RE.sub(" ", text)
    out = set()
    for raw in re.findall(r"(?<![\w.])\d[\d,]*(?:\.\d+)?", text):
        plain = raw.replace(",", "")
        if re.fullmatch(r"(?:19|20)\d{2}", plain) or len(plain.replace(".", "")) < 2:
            continue
        out.add(plain.rstrip("0").rstrip(".") if "." in plain else plain)
    return out


def code_verdict(sentence: str, cited: list[tuple[str, str]], score: float, dating: str = "") -> dict | None:
    """The verdict code can give with confidence, or None (the judge decides). ``dating``: the cited passages'
    publication dates and titles (an undated "now" in a November 2026 article is late 2026)."""
    kind = claim_type(sentence)
    sources = sorted({pid.split(".")[0] for pid, _ in cited})
    if kind in {"calculation"}:
        wrong = arithmetic_check(sentence)
        if wrong:
            return {"type": kind, "status": "CONTRADICTED", "method": "arithmetic", "confidence": 0.95,
                    "explanation": wrong[0], "correction": wrong[1], "sources": sources}
    figures = _figures(sentence)
    evidence_figures = _figures(" ".join(text for _pid, text in cited)) if figures else set()
    claim_years = set(re.findall(r"\b(?:19|20)\d{2}\b", CITE_RE.sub(" ", sentence)))
    # a year the cited passages never name is the writer's dating, not the source's ("most recent quarter" -> 2024)
    years_cited = all(year in dating or any(year in text for _pid, text in cited) for year in claim_years)
    if score >= ACCEPT_OVERLAP and years_cited and (kind != "forecast" or figures <= evidence_figures):
        # a forecast passes only when its figures are the source's own (the source's projection, not the writer's)
        return {"type": kind, "status": "VERIFIED", "method": "word_overlap", "confidence": round(min(0.95, score), 2),
                "explanation": f"{int(score * 100)}% of the claim's words are in its cited passages", "sources": sources}
    if figures and score >= NUMBERS_ACCEPT_OVERLAP and kind not in {"forecast", "qualitative"}:
        # Figures alone prove nothing ("₹15,000-crore" is not "sub-₹15,000 pricing"; a "most recent quarter" table
        # does not date a figure to Q1 2024): every figure, every year the claim names and half its words must sit
        # together in ONE stretch of a cited passage.
        window = _best_excerpts(sentence, cited, keep=1)[0][1] if cited else ""
        if (figures <= _figures(window) and overlap(sentence, [window]) >= 0.5
                and all(year in window or year in dating for year in claim_years)):
            return {"type": kind, "status": "VERIFIED", "method": "figures_in_cited_passages", "confidence": 0.85,
                    "explanation": f"every figure ({', '.join(sorted(figures))}), its years and most of its words "
                                   "appear together in one stretch of a cited passage", "sources": sources}
    return None


def candidates(body: str, passage_texts: dict[str, str],
               passage_dates: dict[str, str] | None = None) -> tuple[list[dict], int, list[dict]]:
    """Sentences code could not verify (to be judged), how many cited sentences were checked, and a structured
    record for every checked claim (code verdicts filled in; judged ones REQUIRES_REVIEW until the judge answers)."""
    checked, judge, records = 0, [], []
    for sentence in _prose_sentences(body):
        cited = _cited_texts(sentence, passage_texts)
        if not cited:
            continue
        checked += 1
        score = overlap(sentence, [t for _, t in cited])
        dating = " ".join(passage_dates.get(pid, "") for pid, _ in cited) if passage_dates else ""
        verdict = code_verdict(sentence, cited, score, dating)
        record = {"id": f"c{checked}", "sentence": sentence[:400], "type": claim_type(sentence),
                  "sources": sorted({pid.split(".")[0] for pid, _ in cited}), "overlap": round(score, 2)}
        if verdict:
            record.update(verdict)
        else:
            record.update({"status": "REQUIRES_REVIEW", "method": "judge", "confidence": None,
                           "explanation": "sent to the judge model"})
            excerpts = _best_excerpts(sentence, cited)
            if passage_dates:  # the judge sees when each source was published, as the reader of the report would
                excerpts = [(pid, f"(published {passage_dates[pid]}) {text}" if passage_dates.get(pid) else text)
                            for pid, text in excerpts]
            judge.append({"sentence": sentence, "overlap": round(score, 2), "record": record, "excerpts": excerpts})
        records.append(record)
    if len(judge) > MAX_JUDGED_PER_SECTION:
        for item in judge[MAX_JUDGED_PER_SECTION:]:
            item["record"]["explanation"] = "not judged (per-section limit)"
    return judge[:MAX_JUDGED_PER_SECTION], checked, records


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


async def check_section(gateway, draft, passage_texts: dict[str, str], passage_dates: dict[str, str] | None = None) -> dict:
    """Judge one section's weakly matched sentences and remove the unsupported ones. Returns its log."""
    items, checked, records = candidates(draft.body, passage_texts, passage_dates)
    entry = {"section": draft.section_id, "checked": checked, "judged": len(items), "removed": [], "partial": 0,
             "claims": records, "corrected": []}
    # arithmetic: a stated percentage change that disagrees with its own start and end values is corrected
    for record in records:
        if record.get("status") == "CONTRADICTED" and record.get("correction"):
            original = next((s for s in _prose_sentences(draft.body) if s.startswith(record["sentence"][:120])), "")
            if original and original in draft.body:
                draft.body = draft.body.replace(original, record["correction"], 1)
                record["status"], record["method"] = "VERIFIED", "arithmetic_corrected"
                entry["corrected"].append({"before": original[:300], "after": record["correction"][:300],
                                           "reason": record["explanation"]})
    entry["byStatus"] = _status_counts(records)
    if not items or gateway is None:
        return entry
    result = await gateway.generate(purpose="claim_check", section_id=draft.section_id, system=JUDGE_SYSTEM,
                                    prompt=judge_prompt(items), max_tokens=2048, temperature=0.0, json_mode=True,
                                    timeout=90)
    if not getattr(result, "ok", False):
        entry["error"] = str(getattr(result, "error", "") or "judge failed")[:120]
        for item in items:  # nothing is removed; the claims stay REQUIRES_REVIEW in the record
            item["record"]["explanation"] = "judge unavailable"
        entry["byStatus"] = _status_counts(records)
        return entry
    verdicts = parse_verdicts(result.json() if hasattr(result, "json") else result.text, len(items))
    status_of = {"SUPPORTED": "VERIFIED", "PARTIAL": "PARTIALLY_VERIFIED", "UNSUPPORTED": "UNVERIFIED"}
    for i, (verdict, reason) in verdicts.items():
        items[i - 1]["record"].update({"status": status_of[verdict], "method": "judge",
                                       "confidence": 0.8 if verdict == "SUPPORTED" else 0.6,
                                       "explanation": reason or verdict.casefold()})
    entry["byStatus"] = _status_counts(records)
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


def _status_counts(records: list[dict]) -> dict:
    out: dict = {}
    for record in records:
        out[record.get("status", "?")] = out.get(record.get("status", "?"), 0) + 1
    return out
