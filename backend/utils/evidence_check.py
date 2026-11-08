"""V2 number check = report.py's source-evidence check (Stage E).

V2's own token check ignored units (crore / lakh crore / trillion / billion / %)
and what a number measures, so it passed claims that report.py's publication
check then failed. Rather than re-implement that check, V2 calls the SAME
functions report.py uses (_attach_audit_source_evidence ->
_merge_source_evidence_statuses -> _source_evidence_quality_audit) on its own
assembled text, and fixes what they flag BEFORE report.py's final check runs:

  * a flagged claim is dropped with a log line (a bullet loses its line, a table
    row its row); the flag is never hidden -- whatever is left is checked again
    and anything still flagged stays visible to report.py.

Live, report.py passes an auditor bound to its own manifest and fetched pages.
Offline (replay, tests) the auditor reads the evidence-store passages instead.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger("evidence_check")

CITE_SRC_RE = re.compile(r"\[(S\d+(?:\s*[,;]\s*S\d+)*)\]")


def store_auditor(store, manifest: list[dict], fetched_rows: list[dict] | None = None, charts: list | None = None):
    """An auditor for offline replays and tests: the live run's fetched page rows where the capture
    has them, the evidence-store passages for every source, and chart rows registered on the
    manifest the way report.py registers V2 chart evidence."""
    from routes import report as R

    texts: dict[str, list[str]] = {}
    for passage in store.passages:
        texts.setdefault(passage.source_id, []).append(str(passage.text or ""))
    rows = [m for m in manifest if isinstance(m, dict)]  # shared: chart evidence registered on them counts
    if charts:
        R._register_v2_chart_evidence(charts, store)
    by_url = {str(r.get("url") or "").rstrip("/"): r for r in (fetched_rows or []) if isinstance(r, dict)}
    enriched = []
    for m in rows:
        sid = str(m.get("id") or "").upper()
        fetched = by_url.get(str(m.get("url") or "").rstrip("/"), {})
        body = "\n\n".join([str(fetched.get("fullContent") or ""), *texts.get(sid, [])]).strip()
        if not body:
            continue
        enriched.append({**fetched, **m, "id": sid, "fullContent": body, "pageContent": body,
                         "snippet": str(fetched.get("snippet") or body[:600]), "pageFetchStatus": "ok"})
    return manifest_auditor(rows, enriched)


def manifest_auditor(manifest: list[dict], enriched: list[dict]):
    """An auditor bound to a source manifest and fetched source rows (what report.py's final check uses)."""
    from routes import report as R

    def audit(report_text: str) -> list[dict]:
        # Exactly what report.py's final check runs (no status merge in between).
        attached = R._attach_audit_source_evidence(manifest, enriched, report_text)
        result = R._source_evidence_quality_audit(attached)
        return [g for g in (result.get("mismatchedCitationGroups") or []) if isinstance(g, dict)]

    return audit


def agreeing_auditor(auditor, store, overrides: list[dict] | None = None):
    """Make report.py's check and the fact-sheet check agree (Stage F).

    report.py's check reads units off the text next to a number, so it misreads values whose unit
    sits in a table header ("1-month Returns (%)" | 7.09), in a compact form ("INR52bn") or in a
    neighbouring sentence. A flag it raises is cleared ONLY when every statistic in the flagged claim
    is a fact in the claim's own cited sources (value + compatible unit + sign + metric); each
    cleared flag is recorded in ``overrides`` for the publication report. Anything else stays flagged."""
    from utils.fact_sheet import FactSheet

    sheet = FactSheet(store.passages)
    sink = overrides if overrides is not None else []

    def audit(report_text: str) -> list[dict]:
        return _reconcile_groups(auditor(report_text), report_text, sheet, store, sink)

    return audit


def _fact_supported(sheet, store, claim: str, sources: set[str], context: str = "") -> bool:
    """Every statistic in ``claim`` is a fact in one of ``sources`` (value + unit + sign + metric).

    ``sources`` is widened by the citations inside the claim itself (one sentence can cite two
    groups); ``context`` (the surrounding line) supplies the subject when the claim says "its"."""
    from utils.fact_sheet import lookup, unit_family
    from utils.number_policy import statistic_tokens

    from utils.number_policy import number_pool, verify_calculation_labels

    sources = set(sources) | {t.strip().split(".")[0].upper() for g in CITE_SRC_RE.findall(claim) for t in re.split(r"\s*[,;]\s*", g)}
    pids = {p.passage_id for p in store.passages if p.passage_id.split(".")[0].upper() in sources}
    if not pids:
        return False
    # A labelled calculation ("-15.3 (calculated: 49.0 - 64.3)") is checked through its inputs and
    # arithmetic, exactly as the number policy does; its result is not a reported fact.
    if "(calculated" in claim:
        if verify_calculation_labels(claim, number_pool({p.passage_id: p.text for p in store.passages if p.passage_id in pids})):
            return False
        # (one level of nested parentheses: "(calculated: 13 - (-91) = 104 crore)")
        claim = re.sub(r"[-+−]?\d[\d,]*(?:\.\d+)?[^()\d\n]{0,30}\(calculated(?:[^()]|\([^()]*\))*\)", " ", claim)
        claim = re.sub(r"\(calculated(?:[^()]|\([^()]*\))*\)", " ", claim)
    tokens = statistic_tokens(claim)
    if not tokens:
        return False
    plain = CITE_SRC_RE.sub(" ", f"{context} {claim}")
    for token in tokens:
        at = claim.find(token)
        end = at + len(token)
        sign = "-" if token[:1] in "-−" else ("+" if token[:1] == "+" else "")
        unit = unit_family(claim[end:end + 16]) if at >= 0 else ""
        if lookup(sheet, token, sign=sign, unit=unit, sentence=plain, passage_ids=pids).status != "ok":
            return False
    return True


_CLAIM_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z*])")


def _flag_values(group: dict) -> list[str]:
    out = []
    for token in group.get("unmatchedNumericClaims") or []:
        raw = str(token).split(":", 1)[-1].split("~")[0].lstrip("-+")
        if raw.endswith(".0"):
            raw = raw[:-2]
        if raw:
            out.append(raw)
    return out


def _claim_scope(text: str, group: dict, report_text: str, span) -> str:
    """What the flag is about: the sentence(s) holding the flagged values (a bullet's other, separate
    sentences are not part of the claim); for a table row, the row plus its table's header row."""
    stripped = text.strip()
    if stripped.startswith("|") and span:
        before = report_text[:span[0]].rstrip("\n").split("\n")
        header = ""
        for line in reversed(before):
            if not line.strip().startswith("|"):
                break
            if not re.fullmatch(r"\s*\|[\s:|-]+\|?\s*", line):
                header = line
        return f"{header}\n{text}" if header else text
    values = _flag_values(group)
    sentences = [s for s in _CLAIM_SENTENCE_RE.split(text) if s.strip()]
    if len(sentences) < 2 or not values:
        return text
    plain = lambda s: s.replace(",", "")  # noqa: E731
    hit = [s for s in sentences if any(re.search(rf"(?<![\d.]){re.escape(v)}(?![\d])", plain(s)) for v in values)]
    if not hit:
        return text
    # Keep each sentence's trailing citation (it may follow the full stop).
    return " ".join(hit)


def _reconcile_groups(groups: list[dict], report_text: str, sheet, store, sink: list[dict]) -> list[dict]:
    kept = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        claim = str(group.get("claim") or "")
        span = _locate(report_text, claim)
        text = report_text[span[0]:span[1]] if span else claim
        if span and len(text) < 0.6 * len(claim):
            # An abbreviation ("Ltd. Saw ...") ended the located sentence early: take the claim's
            # sentence up to its citation, on the same line.
            line_end = report_text.find("\n", span[0])
            line = report_text[span[0]:line_end if line_end >= 0 else len(report_text)]
            cite = re.search(r"\]", line[int(0.6 * len(claim)):])
            text = line[: int(0.6 * len(claim)) + cite.end()] if cite else line
        sources = {str(s).upper() for s in group.get("sourceIds") or []}
        line_start = report_text.rfind("\n", 0, span[0]) + 1 if span else 0
        line_end = report_text.find("\n", span[1]) if span else -1
        # The whole line (bullet / paragraph) the claim sits in: its subject may be in another sentence.
        context = report_text[line_start:(line_end if line_end >= 0 else len(report_text))][:1200] if span else ""
        text = _claim_scope(text, group, report_text, span)
        # The fact sheet decides (Stage G): its lookup checks value, unit, sign AND metric/entity, so a
        # report.py "different metric" flag is also cleared when the fact sheet holds the claim's numbers.
        if _fact_supported(sheet, store, text, sources, context=context):
            sink.append({"claim": re.sub(r"\s+", " ", CITE_SRC_RE.sub("", text)).strip()[:200],
                         "values": list(group.get("unmatchedNumericClaims") or [])[:6],
                         "sources": sorted(sources)[:6],
                         "reason": "report.py could not read the unit; the fact sheet holds every value in the cited source"})
            continue
        kept.append(group)
    return kept


def reconcile_final_audit(audits: dict, report_text: str, store) -> list[dict]:
    """report.py's final source-evidence gate, made to agree with the fact sheet (V2 only).

    Clears a mismatched citation group only when the fact sheet holds every statistic of the claim
    in its cited sources; recomputes ``blocking``; records each override (returned, and stored on
    the audit as ``factSheetOverrides``) so the publication report lists them."""
    from utils.fact_sheet import FactSheet

    evidence = audits.get("sourceEvidenceAudit") if isinstance(audits, dict) else None
    if not isinstance(evidence, dict) or not evidence.get("mismatchedCitationGroups"):
        return []
    overrides: list[dict] = []
    kept = _reconcile_groups(list(evidence.get("mismatchedCitationGroups") or []), report_text,
                             FactSheet(store.passages), store, overrides)
    if overrides:
        cleared = {_norm(o["claim"])[:60] for o in overrides}
        evidence["mismatchedCitationGroups"] = kept
        evidence["blocking"] = bool(kept)
        evidence["flags"] = [f for f in evidence.get("flags") or []
                             if not any(c and c in _norm(str(f)) for c in cleared)]
        evidence["factSheetOverrides"] = overrides
        log.info("V2: the fact sheet cleared %d source-evidence flag(s) report.py raised on unit reading; %d remain",
                 len(overrides), len(kept))
    return overrides


def _norm(text: str) -> str:
    text = CITE_SRC_RE.sub(" ", re.sub(r"[*_`]", "", str(text or "")))
    return re.sub(r"\W+", " ", text.casefold()).strip()


def _locate(report_md: str, claim: str) -> tuple[int, int] | None:
    """(start, end) of the sentence / bullet / table row in ``report_md`` that the audit's claim text came from."""
    target = _norm(claim)[:160]
    if len(target) < 6:
        return None
    for line_match in re.finditer(r"(?m)^.*$", report_md):
        line = line_match.group(0)
        if not line.strip():
            continue
        # Table rows / bullets: a short claim ("**1-Year** | -0.44%") is the row itself.
        if line.lstrip().startswith(("|", "-", "*", ">")) and target[:60] in _norm(line):
            return line_match.start(), line_match.end()
        if len(target) < 12:
            continue  # a short fragment only identifies a table row or bullet, never a prose sentence
        # Whole sentences only: a full stop inside a number ("0.5% to 0.7%") does not end a sentence.
        # The audit's claim text can be a fragment of one (it splits at decimals), so the sentence
        # that CONTAINS the claim fragment is the one removed.
        bounds = [0] + [m.end() for m in re.finditer(r"(?<!\d)[.!?](?:\s*\[[^\]]*\])*(?=\s+[A-Z*\[(\"'₹$]|\s*$)", line)]
        for start, end in zip(bounds, bounds[1:] + ([len(line)] if bounds[-1] < len(line) else [])):
            sentence = line[start:end]
            norm = _norm(sentence)
            if norm and (target[:80] in norm or (len(norm) >= 24 and norm[:80] in target)):
                lead = len(sentence) - len(sentence.lstrip())
                return line_match.start() + start + lead, line_match.start() + end
    return None


def fact_rewriter(store):
    """Rewrite a flagged claim to what the evidence supports, when it can be done without guessing:
    the claimed value IS in a cited source, on a row/sentence whose entity the claim does not name
    ("ES5: -8.5%" -> "ES5: -8.5% for Realty"). Returns None when no such rewrite exists."""
    from utils.fact_sheet import FactSheet

    sheet = FactSheet(store.passages)

    def rewrite(snippet: str, group: dict) -> str | None:
        cited = {str(s).upper() for s in group.get("sourceIds") or []}
        text = snippet
        changed = False
        for token in group.get("unmatchedNumericClaims") or []:
            value = str(token).split(":", 1)[-1].split("~")[0]
            facts = [f for f in sheet.by_value.get(value.lstrip("-").rstrip("0").rstrip(".") if "." in value else value.lstrip("-"), [])
                     if f.passage_id.split(".")[0] in cited and f.entity]
            entities = {f.entity for f in facts}
            if len(entities) != 1:
                continue  # absent, or ambiguous between several entities: no rewrite
            entity = entities.pop()
            if re.search(rf"(?i)(?<![A-Za-z]){re.escape(entity)}(?![A-Za-z])", text):
                continue
            raw = next((f.raw for f in facts), "")
            number = re.escape(raw.lstrip("+-−–").strip())
            match = re.search(rf"{number}\s?(?:%|crore|cr|lakh crore|trillion|billion|million|bn|mn|x)?", text)
            if not match:
                continue
            text = f"{text[:match.end()]} for {entity}{text[match.end():]}"
            changed = True
        return text if changed else None

    return rewrite


def fix_flagged_claims(report_md: str, auditor, *, max_rounds: int = 4, rewriter=None) -> tuple[str, list[dict], list[dict]]:
    """Loop until clean: each flagged claim is rewritten to what the evidence supports (once) or removed,
    re-checked, up to ``max_rounds``; whatever is still flagged after that is removed with its line.
    Returns (text, actions, still_flagged); each action is logged and has ``action`` = rewritten|removed."""
    actions: list[dict] = []
    flagged: list[dict] = []
    rewritten_once: set[str] = set()
    for _round in range(max_rounds + 1):
        try:
            flagged = auditor(report_md)
        except Exception as exc:  # the check must never break the report
            log.warning("V2 evidence check failed: %s", type(exc).__name__)
            return report_md, actions, []
        if not flagged:
            break
        last_round = _round == max_rounds
        changed = False
        for group in flagged:
            claim = str(group.get("claim") or "")
            span = _locate(report_md, claim)
            if not span and last_round:
                # Forced removal: the line that holds the start of the claim.
                key = _norm(claim)[:40]
                for line_match in re.finditer(r"(?m)^.*$", report_md):
                    if key and key in _norm(line_match.group(0)) and not line_match.group(0).lstrip().startswith("#"):
                        span = (line_match.start(), line_match.end())
                        break
            if not span:
                continue
            start, end = span
            snippet = report_md[start:end]
            values = list(group.get("unmatchedNumericClaims") or [])[:6]
            reason = ("cited sources hold the number for a different metric" if group.get("subjectMismatchTokens")
                      else "number not found in the cited sources (value or unit)")
            fixed = rewriter(snippet, group) if rewriter and not last_round and _norm(snippet) not in rewritten_once else None
            if fixed and fixed != snippet:
                report_md = report_md[:start] + fixed + report_md[end:]
                rewritten_once.add(_norm(fixed))
                actions.append({"action": "rewritten", "claim": re.sub(r"\s+", " ", fixed).strip()[:240], "values": values,
                                "reason": reason + "; named the entity whose row holds the value",
                                "sources": list(group.get("sourceIds") or [])[:6]})
                log.info("V2 evidence check rewrote a claim (named its entity): %s", ", ".join(map(str, values))[:120])
                changed = True
                continue
            line_start = report_md.rfind("\n", 0, start) + 1
            line_end = report_md.find("\n", end)
            line_end = len(report_md) if line_end < 0 else line_end
            whole_line = report_md[line_start:line_end]
            if whole_line.lstrip().startswith(("|", "-", "*", ">")) or whole_line.strip() == snippet.strip():
                report_md = report_md[:line_start] + report_md[line_end + 1:]
            else:
                report_md = report_md[:start] + report_md[end:]
            actions.append({"action": "removed", "claim": re.sub(r"\s+", " ", snippet).strip()[:240], "values": values,
                            "reason": reason, "sources": list(group.get("sourceIds") or [])[:6]})
            log.info("V2 evidence check removed a claim (%s): %s", reason, ", ".join(map(str, values))[:120])
            changed = True
        report_md = re.sub(r"[ \t]{2,}", " ", re.sub(r"\n{3,}", "\n\n", report_md))
        if not changed:
            break
    return report_md, actions, flagged
