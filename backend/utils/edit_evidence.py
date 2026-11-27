"""Evidence for a report edit: what is already available, whether it covers what the edit asks for, and what is
missing.

Sources, in order of reuse (nothing here fetches from the web):
  1. the report's own cited statements (a paragraph is evidence for the source ids it cites);
  2. each cited source's saved excerpts (evidenceExcerpt / chartEvidenceExcerpt / page or document text that the
     report payload carries);
  3. the report's chart data (every series point as a "label: value unit" row, tied to the chart's sources);
  4. uploaded-document passages and stored web research the edit path already retrieved.

They are indexed in the same EvidenceStore the report pipeline uses (BM25 x reliability), so retrieval for an edit
is section- and need-specific and never sends the whole library to the model.

Sufficiency is decided per requested fact (utils.edit_plan.EvidenceNeed): a fact is covered when one passage
names the entity, the measure and every requested period, and states a number. Only uncovered facts go to
targeted research, through the existing web-research helper - there is no second research implementation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from utils.evidence_store import EvidenceStore, Passage, _tokens

_NUM_RE = re.compile(r"(?<![\w.])[-+]?\d[\d,]*(?:\.\d+)?%?")
_CITE_RE = re.compile(r"\[(S\d+(?:\.\d+)?(?:\s*[,;]\s*S\d+(?:\.\d+)?)*)\]")
_SYNONYMS = {"sales": {"sales", "sold", "registrations", "deliveries", "units"},
             "registrations": {"registrations", "sales", "registered"},
             "share": {"share", "market share"}, "market share": {"share", "market share"},
             "production": {"production", "output", "produced"}, "output": {"production", "output"},
             "prices": {"price", "prices"}, "price": {"price", "prices"}, "cost": {"cost", "costs", "price"},
             "imports": {"imports", "import"}, "exports": {"exports", "export"}}


@dataclass
class Sufficiency:
    needs: list
    covered: list = field(default_factory=list)      # (need, passage ids)
    missing: list = field(default_factory=list)      # needs without support

    @property
    def sufficient(self) -> bool:
        return not self.missing

    def research_instruction(self, instruction: str) -> str:
        """A research request for the missing facts only (the original wording keeps the context)."""
        wanted = "; ".join(n.describe() for n in self.missing)
        return f"Find reliable, comparable figures for: {wanted}. Context of the edit: {instruction[:400]}"

    def to_dict(self) -> dict:
        return {"needs": [n.describe() for n in self.needs], "covered": [n.describe() for n, _ in self.covered],
                "missing": [n.describe() for n in self.missing]}


def _source_text(source: dict) -> str:
    parts = []
    for key in ("evidenceExcerpt", "chartEvidenceExcerpt", "evidenceContent", "pageContent", "page_content",
                "fullContent", "full_content", "snippet"):
        value = str(source.get(key) or "").strip()
        if value and value not in parts:
            parts.append(value)
    return "\n".join(parts)


def chart_rows(spec: dict) -> str:
    unit = str(spec.get("unit") or "")
    lines = [str(spec.get("title") or "")]
    for series in spec.get("series") or []:
        for point in series.get("data") or []:
            lines.append(f"{series.get('name', '')} | {point.get('label', '')}: {point.get('value')} {unit}".strip())
    return "\n".join(lines)


def build_store(report: str, sources: list, charts: list, extra: list | None = None) -> EvidenceStore:
    """``extra``: already-retrieved evidence rows ({"id", "title", "text", "url", "origin"}) - uploads, stored or
    fresh web research."""
    store = EvidenceStore()
    reliability = {str(s.get("id") or "").upper(): float(s.get("reliability_score") or s.get("reliabilityScore") or 0.6)
                   for s in sources or [] if isinstance(s, dict)}
    by_source: dict[str, list[tuple[str, str]]] = {}
    for line in str(report or "").splitlines():
        ids = {c.strip().upper().split(".")[0] for g in _CITE_RE.findall(line) for c in re.split(r"\s*[,;]\s*", g)}
        for sid in ids:
            by_source.setdefault(sid, []).append(("report", line.strip()))
    for s in sources or []:
        if isinstance(s, dict) and str(s.get("id") or "").strip():
            text = _source_text(s)
            if text:
                by_source.setdefault(str(s["id"]).upper(), []).append(("source", text[:6000]))
    for n, spec in enumerate(charts or [], 1):
        if isinstance(spec, dict) and spec.get("series"):
            ids = [str(x).upper() for x in (spec.get("sourceIds") or [])] or [f"CHART{n}"]
            by_source.setdefault(ids[0], []).append(("chart", chart_rows(spec)))
    for row in extra or []:
        sid = str(row.get("id") or row.get("title") or "X").upper()
        by_source.setdefault(sid, []).append((str(row.get("origin") or "retrieved"), str(row.get("text") or "")[:6000]))
    url = {str(s.get("id") or "").upper(): str(s.get("url") or "") for s in sources or [] if isinstance(s, dict)}
    title = {str(s.get("id") or "").upper(): str(s.get("title") or "") for s in sources or [] if isinstance(s, dict)}
    for sid, items in by_source.items():
        for k, (origin, text) in enumerate(items, 1):
            if len(text) < 20:
                continue
            p = Passage(f"{sid}.E{k}", sid, text,
                        "table" if origin == "chart" else "narrative", url=url.get(sid, ""), title=title.get(sid, ""),
                        reliability=reliability.get(sid, 0.6))
            store.passages.append(p)
            store._by_id[p.passage_id] = p
    return store.finalize()


def _covers(text: str, need) -> bool:
    low = text.casefold()
    if need.entity and not all(w in low for w in _tokens(need.entity)):
        return False
    if need.metric:
        words = _SYNONYMS.get(need.metric, {need.metric})
        if not any(w in low for w in words):
            return False
    for period in need.periods:
        digits = re.findall(r"\d{2,4}", period)
        if digits and not any(d in low for d in digits):
            return False
    return bool(_NUM_RE.search(text))


def assess(store: EvidenceStore, needs: list) -> Sufficiency:
    result = Sufficiency(needs=list(needs))
    for need in needs:
        query = need.describe()
        hits = [p for p, _ in store.search(query, k=12, max_per_source=3)] if query.strip() else []
        support = [p.passage_id for p in hits if _covers(p.text, need)]
        (result.covered.append((need, support)) if support else result.missing.append(need))
    return result


def passages_for(store: EvidenceStore, query: str, *, k: int = 12, budget_tokens: int = 6000) -> list[Passage]:
    """The most relevant passages for one edit, within a token budget (no duplicates of one source's text)."""
    from utils.evidence_rerank import evidence_tokens
    out, used, seen = [], 0, set()
    for passage, _ in store.search(query, k=k * 2, max_per_source=3):
        sig = re.sub(r"\W+", "", passage.text.casefold())[:200]
        if sig in seen:
            continue
        cost = evidence_tokens(passage)
        if used + cost > budget_tokens:
            continue
        seen.add(sig)
        out.append(passage)
        used += cost
        if len(out) >= k:
            break
    return out
