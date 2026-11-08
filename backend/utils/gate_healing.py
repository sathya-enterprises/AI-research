"""Every publication gate heals itself (Stage G).

Each gate is HARD or SOFT:

  * HARD (an unsupported number or claim, a fabricated/missing source, a broken figure reference,
    a duplicate, a chart point the evidence does not hold): the remedy rewrites to what the
    evidence supports where that is certain, else removes the sentence / row / chart with a log
    line. Then ALL deterministic gates run again; up to 4 rounds; anything still failing after
    that is removed. A report is "published" only when what is left passes every hard gate.
  * SOFT (coverage / scope findings such as "only a non-official source for a signing date",
    "signing vs entry into force not separated", "exhaustive universe not established"): never
    blocks. A visible qualifier goes into the text (a NOTE block placed before the methodology),
    and the item is listed in the publication report.

When V2's check and report.py's check disagree about a number, the fact sheet decides
(utils.evidence_check._reconcile_groups): a report.py flag blocks only if the fact sheet also
lacks the claim's number. Nothing is hidden: every removal, rewrite and qualifier is recorded in
the returned actions and shown in the publication report and the methodology note.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

log = logging.getLogger("gate_healing")


@dataclass(frozen=True)
class Gate:
    key: str
    audit: str           # publicationAudit | sourceEvidenceAudit | scopeCoverageAudit | v2
    kind: str            # hard | soft
    name: str
    remedy: str


GATES: list[Gate] = [
    Gate("mismatchedCitationGroups", "sourceEvidenceAudit", "hard", "Source evidence (cited number not in cited source)",
         "fact sheet decides; else name the entity whose row holds the value; else remove the sentence/row"),
    Gate("v2UnresolvedTableValues", "v2", "hard", "V2 number policy (table value not in the evidence)",
         "remove the table row holding the value"),
    Gate("controlMarkerCount", "publicationAudit", "hard", "Leaked control markers", "strip the marker"),
    Gate("danglingChartRefs", "publicationAudit", "hard", "Broken chart reference", "remove the [CHART_n] placeholder"),
    Gate("danglingImageRefs", "publicationAudit", "hard", "Broken image reference", "remove the image placeholder"),
    Gate("unresolvedCitationIds", "publicationAudit", "hard", "Citation to a source not in the registry",
         "remove sentences with numbers that rely on it; otherwise drop the citation id"),
    Gate("missingSourceRecords", "publicationAudit", "hard", "Source record unresolved",
         "as for an unresolved citation"),
    Gate("duplicateTableCount", "publicationAudit", "hard", "Same table twice", "drop the later copy"),
    Gate("duplicateObservations", "publicationAudit", "hard", "Same numeric row twice", "drop the repeated row"),
    Gate("orphanedChartMentions", "publicationAudit", "hard", "Chart promised in text but absent", "remove the line"),
    Gate("crossMetricProjectionBasisIssues", "publicationAudit", "hard", "Projection on another metric's basis",
         "remove the sentence"),
    Gate("timeframeLabelMismatches", "publicationAudit", "hard", "Table period label contradicts its rows",
         "drop the table/chart"),
    Gate("incompleteNumericFragments", "publicationAudit", "hard", "Number cut off mid-claim", "remove the sentence"),
    Gate("currencyConversionAnomalies", "publicationAudit", "hard", "Implausible currency conversion", "remove the sentence"),
    Gate("emptyChartIndices", "publicationAudit", "hard", "Empty chart", "drop the chart"),
    Gate("untitledChartIndices", "publicationAudit", "hard", "Untitled chart", "title it from its data, else drop it"),
    Gate("chartTableVerificationConflicts", "publicationAudit", "hard", "Chart contradicts a report table",
         "prune the point, else drop the chart"),
    Gate("chartEvidenceFailures", "publicationAudit", "hard", "Chart point not in its evidence",
         "prune the point, else drop the chart"),
    Gate("renderedDuplicateObservation", "renderedPdf", "hard", "Rendered PDF repeats an observation (export)",
         "drop the repeated line / caption and re-render (export route)"),
    Gate("scopeFlags", "scopeCoverageAudit", "soft", "Requested scope / coverage (incl. FTA official-source and dates)",
         "visible qualifier in the text; listed in the publication report"),
    Gate("chartDensity", "chartDensity", "soft", "Chart density", "advisory only"),
    Gate("structure", "structure", "soft", "Heading structure", "advisory only"),
]
HARD_PUBLICATION_KEYS = [g.key for g in GATES if g.audit == "publicationAudit" and g.kind == "hard"]

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z*\[(\"'₹$])")


def gate_table() -> list[dict]:
    return [{"gate": g.name, "key": g.key, "blocking": g.kind == "hard", "remedy": g.remedy} for g in GATES]


def _norm(text: str) -> str:
    return re.sub(r"\W+", " ", re.sub(r"\[S\d+(?:\.\d+)?(?:\s*[,;]\s*S\d+(?:\.\d+)?)*\]", " ", str(text or "")).casefold()).strip()


def _snippet(item) -> str:
    if isinstance(item, dict):
        for key in ("sentence", "text", "claim", "line", "row", "fragment", "quote", "value"):
            if item.get(key):
                return str(item[key])
        return ""
    return str(item or "")


def _remove_containing(text: str, snippet: str) -> tuple[str, str]:
    """Remove the sentence (prose) or the whole line (table row / bullet) that contains ``snippet``."""
    key = _norm(snippet)[:120]
    if len(key) < 5:
        return text, ""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if key not in _norm(line) or line.lstrip().startswith("#"):
            continue
        if line.lstrip().startswith(("|", "-", "*", ">")) or len(line) < 400:
            removed = lines.pop(i)
            return "\n".join(lines), removed.strip()
        parts = _SENTENCE_END.split(line)
        keep = [p for p in parts if key not in _norm(p)]
        removed = " ".join(p for p in parts if key in _norm(p))
        if not removed:  # the snippet spans sentences: drop the line
            removed = lines.pop(i)
            return "\n".join(lines), removed.strip()
        lines[i] = " ".join(keep)
        return "\n".join(lines), removed.strip()
    return text, ""


def _drop_charts(text: str, charts: list, indices: set[int]) -> tuple[str, list]:
    from utils.chart_evidence_audit import remap_chart_placeholders
    mask = [i not in indices for i in range(1, len(charts) + 1)]
    return remap_chart_placeholders(text, mask), [c for c, keep in zip(charts, mask) if keep]


def _tables(text: str) -> list[tuple[int, int]]:
    """(start_line, end_line_exclusive) of Markdown tables."""
    lines, out, start = text.split("\n"), [], None
    for i, line in enumerate(lines + [""]):
        is_row = line.strip().startswith("|")
        if is_row and start is None:
            start = i
        elif not is_row and start is not None:
            if i - start >= 2:
                out.append((start, i))
            start = None
    return out


def _drop_duplicate_tables(text: str) -> tuple[str, int]:
    lines = text.split("\n")
    seen, drop = set(), []
    for start, end in _tables(text):
        sig = re.sub(r"\s+", " ", re.sub(r"\[S\d+(?:\.\d+)?\]", "", "\n".join(lines[start:end])).casefold())
        if sig in seen:
            cap = start - 2 if start >= 2 and lines[start - 2].startswith("**Table") else (start - 1 if start >= 1 and lines[start - 1].startswith("**Table") else start)
            drop.append((cap, end))
        seen.add(sig)
    for start, end in reversed(drop):
        del lines[start:end]
    return "\n".join(lines), len(drop)


_FRAGMENT_RE = re.compile(
    r"(?m)(?:^|(?<=[.!?]\s))[ 	]*[-+−]?\d[\d.,]*%?[ 	]*\[S\d+(?:\.\d+)?(?:\s*[,;]\s*S\d+(?:\.\d+)?)*\]\.?[ 	]*")


def remove_fragments(text: str, actions: list[dict]) -> str:
    """A bare 'number [S#].' at the start of a line or sentence is the tail of a cut sentence (live run
    G: '1 [S94]. Manufacturing PMI ...' was left after an earlier removal split '...58.1'). Remove it."""
    def repl(match: re.Match) -> str:
        actions.append({"gate": "fragment", "action": "removed fragment", "detail": match.group(0).strip()})
        return ""
    return _FRAGMENT_RE.sub(repl, text)


def _remove_short_claim(text: str, claim: str, sources: list) -> tuple[str, str]:
    """A claim too short to locate by its text ('1'): find it by value + its citation."""
    ids = "|".join(re.escape(str(s).upper()) for s in sources or []) or r"S\d+"
    pattern = re.compile(rf"(?<![\w.]){re.escape(claim.strip())}\s*%?\s*\[(?:{ids})(?:\.\d+)?(?:\s*[,;]\s*S\d+(?:\.\d+)?)*\]\.?")
    match = pattern.search(text)
    if not match:
        return text, ""
    return text[:match.start()] + text[match.end():], match.group(0)


def _remedy_publication(text: str, charts: list, pub: dict, actions: list[dict], *, force: bool) -> tuple[str, list]:
    def act(gate, action, detail):
        actions.append({"gate": gate, "action": action, "detail": re.sub(r"\s+", " ", str(detail))[:240]})
        log.info("Gate healing: %s -> %s: %s", gate, action, str(detail)[:160])

    if pub.get("controlMarkerCount"):
        text = re.sub(r"\[\[(?:SECTION:[^\]]*|END SECTION|SECTION TO EXPAND|END_SCAN_SECTION)[^\]]*\]\]|\[\s*RISK_MATRIX\s*\]", "", text, flags=re.I)
        act("controlMarkerCount", "stripped", f"{pub['controlMarkerCount']} marker(s)")
    for n in pub.get("danglingChartRefs") or []:
        text = re.sub(rf"\[CHART_{int(n)}\]", "", text)
        act("danglingChartRefs", "removed placeholder", f"[CHART_{n}]")
    for n in pub.get("danglingImageRefs") or []:
        text = re.sub(rf"\[(?:WEB_IMG|IMAGE)_{int(n)}\]", "", text)
        act("danglingImageRefs", "removed placeholder", f"image {n}")
    bad_ids = {str(x).upper() for x in (pub.get("unresolvedCitationIds") or []) + (pub.get("missingSourceRecords") or [])}
    if bad_ids:
        def fix_group(m):
            ids = [t for t in re.split(r"\s*[,;]\s*", m.group(1)) if t.strip().upper() not in bad_ids]
            return f"[{', '.join(ids)}]" if ids else "[UNSOURCED]"
        text = re.sub(r"\[(S\d+(?:\.\d+)?(?:\s*[,;]\s*S\d+(?:\.\d+)?)*)\]", fix_group, text)
        # A sentence whose only source was unresolved and that states a number is removed.
        while "[UNSOURCED]" in text:
            at = text.index("[UNSOURCED]")
            line_start = text.rfind("\n", 0, at) + 1
            line_end = text.find("\n", at)
            line = text[line_start: line_end if line_end >= 0 else len(text)]
            sentence = next((s for s in _SENTENCE_END.split(line) if "[UNSOURCED]" in s), line)
            if re.search(r"\d", sentence.replace("[UNSOURCED]", "")):
                text, removed = _remove_containing(text, sentence.replace("[UNSOURCED]", ""))
                if not removed:
                    text = text.replace("[UNSOURCED]", "", 1)
                act("unresolvedCitationIds", "removed", removed or sentence)
            else:
                text = text.replace(" [UNSOURCED]", "", 1).replace("[UNSOURCED]", "", 1)
                act("unresolvedCitationIds", "dropped citation", sentence)
    if pub.get("duplicateTableCount"):
        text, n = _drop_duplicate_tables(text)
        act("duplicateTableCount", "dropped later copy", f"{n} table(s)")
    for item in pub.get("duplicateObservations") or []:
        row = _snippet(item)
        lines = text.split("\n")
        hits = [i for i, ln in enumerate(lines) if ln.strip().startswith("|") and _norm(row) and _norm(row) in _norm(ln)]
        if len(hits) >= 2:
            del lines[hits[-1]]
            text = "\n".join(lines)
            act("duplicateObservations", "dropped repeated row", row)
    for key in ("orphanedChartMentions", "crossMetricProjectionBasisIssues", "incompleteNumericFragments", "currencyConversionAnomalies"):
        for item in pub.get(key) or []:
            text, removed = _remove_containing(text, _snippet(item))
            if removed:
                act(key, "removed", removed)
    drop: set[int] = set(int(i) for i in (pub.get("emptyChartIndices") or []))
    for idx in pub.get("untitledChartIndices") or []:
        chart = charts[int(idx) - 1] if 0 < int(idx) <= len(charts) else None
        names = [str(s.get("name") or "") for s in (chart or {}).get("series") or [] if isinstance(s, dict)]
        if chart is not None and any(names):
            chart["title"] = ", ".join(n for n in names if n)[:90]
            act("untitledChartIndices", "titled from its data", chart["title"])
        else:
            drop.add(int(idx))
    for item in pub.get("timeframeLabelMismatches") or []:
        where = str((item or {}).get("source") or (item or {}).get("table") or "") if isinstance(item, dict) else ""
        m = re.match(r"chart_(\d+)", where)
        if m:
            drop.add(int(m.group(1)))
        else:
            text, removed = _remove_containing(text, _snippet(item))
            if removed:
                act("timeframeLabelMismatches", "removed", removed)
    if force:
        for item in (pub.get("chartEvidenceFailures") or []) + (pub.get("chartTableVerificationConflicts") or []):
            if isinstance(item, dict) and str(item.get("chartIndex") or "").isdigit():
                drop.add(int(item["chartIndex"]))
    if drop:
        # Index-based drops first; chart-point pruning waits for the next round's fresh audit
        # (indices shift once charts are removed).
        text, charts = _drop_charts(text, charts, {i for i in drop if 0 < i <= len(charts)})
        act("chart", "dropped", f"chart(s) {sorted(drop)}")
        return text, charts
    if pub.get("chartTableVerificationConflicts") or pub.get("chartEvidenceFailures"):
        from utils.chart_evidence_audit import prune_unsupported_chart_points
        kept, removed_idx, pruned = prune_unsupported_chart_points(list(charts), pub)
        if removed_idx:
            from utils.chart_evidence_audit import remap_chart_placeholders
            gone = {int(i) for i in removed_idx}
            text = remap_chart_placeholders(text, [i not in gone for i in range(1, len(charts) + 1)])
        if removed_idx or pruned:
            charts = kept
            act("chartEvidenceFailures", "pruned/dropped", f"{len(removed_idx)} chart(s), {pruned} point(s)")
    return text, charts


def _recite(snippet: str, store) -> str | None:
    """Every number of the claim is a fact in ONE other source: cite that source instead (rewrite)."""
    from utils.evidence_check import _fact_supported
    from utils.fact_sheet import FactSheet

    if store is None or not snippet:
        return None
    sheet = getattr(store, "_gate_sheet", None)
    if sheet is None:
        sheet = FactSheet(store.passages)
        store._gate_sheet = sheet
    current = {t.strip().split(".")[0].upper() for g in re.findall(r"\[(S[^\]]+)\]", snippet) for t in g.split(",")}
    for source in dict.fromkeys(p.source_id.upper() for p in store.passages):
        if source in current or not _fact_supported(sheet, store, snippet, {source}):
            continue
        pid = next(p.passage_id for p in store.passages if p.source_id.upper() == source)
        recited = re.sub(r"\[(S\d+(?:\.\d+)?(?:\s*[,;]\s*S\d+(?:\.\d+)?)*)\]", f"[{source}]", snippet)
        return recited if recited != snippet else f"{snippet.rstrip()} [{source}]".replace(f"[{source}]", f"[{pid.split('.')[0]}]")
    return None


def _remedy_evidence(text: str, groups: list[dict], actions: list[dict], rewriter=None, *, force: bool, store=None) -> str:
    from utils.evidence_check import _locate
    for group in groups:
        claim = str(group.get("claim") or "")
        span = _locate(text, claim)
        snippet = text[span[0]:span[1]] if span else ""
        fixed = rewriter(snippet, group) if (rewriter and snippet and not force) else None
        if not fixed and snippet and not force:
            fixed = _recite(snippet, store)
            if fixed:
                text = text[:span[0]] + fixed + text[span[1]:]
                actions.append({"gate": "mismatchedCitationGroups", "action": "rewritten (citation corrected)", "detail": fixed[:240]})
                continue
        if fixed and fixed != snippet:
            text = text[:span[0]] + fixed + text[span[1]:]
            actions.append({"gate": "mismatchedCitationGroups", "action": "rewritten", "detail": fixed[:240]})
            continue
        if span:
            # Remove only the sentence(s) that hold the flagged values (a bullet's other sentences stay),
            # unless it is a table row (the row goes).
            from utils.evidence_check import _claim_scope
            scope = _claim_scope(snippet, group, text, span)
            if not snippet.strip().startswith("|") and scope != snippet and scope.strip() and scope in snippet:
                text = text[:span[0]] + snippet.replace(scope, "").replace("  ", " ") + text[span[1]:]
                removed = scope
            else:
                text = text[:span[0]] + text[span[1]:]
                removed = snippet
        else:
            text, removed = _remove_containing(text, claim)
            if not removed and len(_norm(claim)) < 5:
                text, removed = _remove_short_claim(text, claim, list(group.get("sourceIds") or []))
        if removed:
            actions.append({"gate": "mismatchedCitationGroups", "action": "removed", "detail": re.sub(r"\s+", " ", removed)[:240],
                            "values": list(group.get("unmatchedNumericClaims") or [])[:6]})
            log.info("Gate healing: removed an unsupported claim: %s", ", ".join(map(str, group.get("unmatchedNumericClaims") or []))[:120])
    return re.sub(r"[ \t]{2,}", " ", re.sub(r"\n{3,}", "\n\n", text))


def hard_findings(audits: dict) -> list[str]:
    pub = audits.get("publicationAudit") if isinstance(audits.get("publicationAudit"), dict) else {}
    ev = audits.get("sourceEvidenceAudit") if isinstance(audits.get("sourceEvidenceAudit"), dict) else {}
    out = [k for k in HARD_PUBLICATION_KEYS if pub.get(k)]
    if ev.get("mismatchedCitationGroups"):
        out.append("mismatchedCitationGroups")
    return out


def soften_scope(audits: dict, actions: list[dict]) -> list[str]:
    """Scope / coverage findings never block: they become visible qualifiers (returned)."""
    scope = audits.get("scopeCoverageAudit") if isinstance(audits.get("scopeCoverageAudit"), dict) else None
    if not scope or not scope.get("flags"):
        return []
    qualifiers = []
    for flag in scope.get("flags") or []:
        text = str(flag)
        if re.search(r"(?i)official", text):
            text = "Official confirmation not found for every agreement date: " + text
        qualifiers.append(text)
        actions.append({"gate": "scopeFlags", "action": "qualified (soft)", "detail": text[:240]})
    scope["blocking"] = False
    scope["softenedFlags"] = list(scope.get("flags") or [])
    return qualifiers


def qualifier_block(qualifiers: list[str]) -> str:
    if not qualifiers:
        return ""
    body = " ".join(q.rstrip(".") + "." for q in qualifiers)
    return "> [!NOTE:Verification notes] " + body


def place_qualifiers(text: str, qualifiers: list[str]) -> str:
    block = qualifier_block(qualifiers)
    if not block or block in text:
        return text
    match = re.search(r"(?m)^##\s+Methodology", text)
    if match:
        return text[:match.start()] + block + "\n\n" + text[match.start():]
    return text.rstrip() + "\n\n" + block


def _remove_unresolved_rows(text: str, values: list, actions: list[dict]) -> str:
    """V2 table values the evidence lacks: drop the table row whose cell holds exactly that value."""
    for value in values or []:
        token = str(value).strip().rstrip(",")
        if not token or not re.search(r"\d", token):
            continue
        cell = re.compile(rf"\|\s*[+\-−]?(?:₹|Rs\.?\s*)?{re.escape(token.lstrip('+-−'))}\s*(?:%|x|cr|crore)?\s*(?:\[[^\]]*\])?\s*\|")
        lines = text.split("\n")
        for i, line in enumerate(lines):
            if line.lstrip().startswith("|") and cell.search(line):
                actions.append({"gate": "v2UnresolvedTableValues", "action": "removed", "detail": line.strip()[:240]})
                del lines[i]
                break
        text = "\n".join(lines)
    return text


def add_verification_note(text: str, summary: dict, v2_meta: dict | None = None) -> str:
    """The methodology opens with the verification note (counts of removed / rewritten / qualified claims)."""
    note = verification_note(summary, v2_meta)
    match = re.search(r"(?m)^##\s+Methodology[^\n]*\n+", text or "")
    if not match or "**Verification.**" in text:
        return text
    return text[:match.end()] + note + "\n\n" + text[match.end():]


def heal(text: str, charts: list, audits: dict, rerun, *, store=None, max_rounds: int = 4,
         v2_unresolved: list | None = None) -> tuple[str, list, dict, dict]:
    """Self-heal all gates. ``rerun(text, charts) -> audits`` re-runs every deterministic gate.

    Returns (text, charts, audits, summary) where summary has the actions, rounds, qualifiers and
    whatever is still failing (empty when the report may publish)."""
    actions: list[dict] = []
    cleaned = remove_fragments(text, actions)
    if cleaned != text:
        text = cleaned
        audits = rerun(text, charts)
    if v2_unresolved:
        text = _remove_unresolved_rows(text, v2_unresolved, actions)
        audits = rerun(text, charts)
    rewriter = None
    if store is not None:
        from utils.evidence_check import fact_rewriter, reconcile_final_audit
        rewriter = fact_rewriter(store)
        reconcile_final_audit(audits, text, store)
    rounds = 0
    for rounds in range(1, max_rounds + 2):
        force = rounds > max_rounds
        remaining = hard_findings(audits)
        if not remaining:
            rounds -= 1
            break
        pub = audits.get("publicationAudit") or {}
        text, charts = _remedy_publication(text, list(charts or []), pub, actions, force=force)
        groups = list((audits.get("sourceEvidenceAudit") or {}).get("mismatchedCitationGroups") or [])
        if groups:
            text = _remedy_evidence(text, groups, actions, rewriter, force=force, store=store)
        audits = rerun(text, charts)
        if store is not None:
            from utils.evidence_check import reconcile_final_audit
            reconcile_final_audit(audits, text, store)
    qualifiers = soften_scope(audits, actions)
    if qualifiers:
        text = place_qualifiers(text, qualifiers)
    still = hard_findings(audits)
    summary = {"rounds": rounds, "actions": actions, "qualifiers": qualifiers, "stillFailing": still,
               "removed": sum(1 for a in actions if a["action"].startswith(("removed", "dropped", "pruned"))),
               "rewritten": sum(1 for a in actions if a["action"].startswith(("rewritten", "titled"))),
               "qualified": len(qualifiers)}
    log.info("Gate healing: rounds=%d removed=%d rewritten=%d qualified=%d still_failing=%s",
             rounds, summary["removed"], summary["rewritten"], summary["qualified"], still)
    return text, charts, audits, summary


def verification_note(summary: dict, v2_meta: dict | None = None) -> str:
    """Short methodology note: how many claims were removed, rewritten or qualified, and by which check."""
    meta = v2_meta or {}
    pol = meta.get("numberPolicy") or {}
    ev = meta.get("evidenceCheck") or {}
    removed = len(pol.get("sentencesRemoved") or []) + len(ev.get("removedClaims") or []) + int(summary.get("removed") or 0)
    rewritten = len(pol.get("numbersRewritten") or []) + len(ev.get("rewrittenClaims") or []) + int(summary.get("rewritten") or 0)
    return (f"**Verification.** Every number was checked against the fact sheet of its cited evidence and against the "
            f"publication gates. {removed} claim(s) that the evidence did not support were removed, {rewritten} were "
            f"rewritten to what the evidence states (for example naming the entity a value belongs to), and "
            f"{int(summary.get('qualified') or 0)} item(s) carry a visible qualifier. The publication report lists each one.")
