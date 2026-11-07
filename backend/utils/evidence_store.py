"""Passage-level evidence store.

Each fetched source is split into passages of ~400–1,400 chars. A passage has a
stable id derived from its canonical source id (``S12.3`` = third passage of
[S12]), plus the source URL, publisher, publication date, authority tier and
reliability score from utils/source_quality.py.

Later stages use the store to:
  * retrieve the passages for one outline section (``search``), ranked by
    BM25 relevance x reliability, so each section writer sees only its own
    evidence;
  * require citations to point at stored passages (``get``);
  * show per-source reliability in the appendix (``source_summary``).

Kinds: ``table`` (rows kept with their header), ``narrative`` (publisher text),
``search_excerpt`` (search-index text when the page was not read; lower
weight), ``calculated`` (internal datasets with recorded provenance).
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9.%-]*")
_NUMBER_RE = re.compile(r"(?<![\w.])[-+]?\d[\d,]*(?:\.\d+)?%?")
_STOP = {
    "the", "and", "of", "in", "a", "an", "to", "for", "on", "with", "by", "is", "are", "was", "were", "as", "at",
    "that", "this", "from", "or", "be", "it", "its", "has", "have", "had", "which", "their", "than", "over", "into",
}
MIN_PASSAGE = 400
MAX_PASSAGE = 1400


@dataclass
class Passage:
    passage_id: str
    source_id: str
    text: str
    kind: str
    url: str = ""
    title: str = ""
    publisher: str = ""
    published: str = ""
    authority_tier: str = "unknown"
    reliability: float = 0.45
    numbers: list[str] = field(default_factory=list)

    def to_dict(self, max_text: int | None = None) -> dict:
        return {
            "id": self.passage_id, "sourceId": self.source_id, "kind": self.kind, "url": self.url,
            "title": self.title, "publisher": self.publisher, "published": self.published,
            "authorityTier": self.authority_tier, "reliability": self.reliability,
            "numbers": self.numbers[:40], "text": self.text if max_text is None else self.text[:max_text],
        }


def _tokens(text: str) -> list[str]:
    return [t.strip(".%-") for t in _TOKEN_RE.findall(str(text or "").casefold()) if t.strip(".%-") and t not in _STOP]


def _split_tables_and_text(page: str) -> tuple[list[tuple[str, list[str]]], str]:
    """Separate extractor table blocks ("TABLE n — heading" + pipe rows) from narrative text."""
    tables: list[tuple[str, list[str]]] = []
    narrative: list[str] = []
    current: tuple[str, list[str]] | None = None
    for line in str(page or "").splitlines():
        stripped = line.strip()
        if re.match(r"^TABLE \d+\b", stripped):
            current = (stripped, [])
            tables.append(current)
            continue
        if current is not None and " | " in stripped:
            current[1].append(stripped)
            continue
        current = None
        if stripped.startswith("[") and stripped.endswith("]") and "PUBLISHER" in stripped:
            continue  # extractor section labels
        narrative.append(line)
    return tables, "\n".join(narrative)


def _table_passages(heading: str, rows: list[str]) -> list[str]:
    if not rows:
        return []
    header, body = rows[0], rows[1:] or []
    out, chunk = [], [heading, header]
    for row in body:
        if sum(len(x) + 1 for x in chunk) + len(row) > MAX_PASSAGE and len(chunk) > 2:
            out.append("\n".join(chunk))
            chunk = [heading + " (continued)", header]
        chunk.append(row)
    if len(chunk) > 2 or not body:
        out.append("\n".join(chunk))
    return out


def _narrative_passages(text: str) -> list[str]:
    paragraphs = [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n\s*\n|\n(?=[A-Z#])", text or "")]
    paragraphs = [p for p in paragraphs if len(p) >= 40]
    out, buf = [], ""
    for para in paragraphs:
        if len(para) > MAX_PASSAGE:
            sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", para)
            for sentence in sentences:
                if len(buf) + len(sentence) + 1 > MAX_PASSAGE and len(buf) >= MIN_PASSAGE:
                    out.append(buf.strip())
                    buf = ""
                buf += " " + sentence
            continue
        if len(buf) + len(para) + 1 > MAX_PASSAGE and len(buf) >= MIN_PASSAGE:
            out.append(buf.strip())
            buf = ""
        buf += "\n" + para
    if buf.strip():
        if out and len(buf.strip()) < MIN_PASSAGE // 2:
            out[-1] = (out[-1] + "\n" + buf.strip())[: MAX_PASSAGE + MIN_PASSAGE]
        else:
            out.append(buf.strip())
    return out


class EvidenceStore:
    def __init__(self) -> None:
        self.passages: list[Passage] = []
        self._by_id: dict[str, Passage] = {}
        self._tf: list[Counter] = []
        self._df: Counter = Counter()
        self._avg_len = 1.0

    # ── building ─────────────────────────────────────────────────────────────
    def add_source(self, source_id: str, row: dict) -> int:
        url = str(row.get("url") or "")
        meta = dict(
            url=url, title=str(row.get("publisherPageTitle") or row.get("title") or "")[:300],
            publisher=str(row.get("publisherPagePublisher") or row.get("publisher") or "")[:180],
            published=str(row.get("publication_date") or row.get("publisherPagePublishedAt") or row.get("published") or "")[:40],
            authority_tier=str(row.get("authorityTier") or "unknown"),
            reliability=float(row.get("reliabilityScore") or 0.45),
        )
        page = str(row.get("pageContent") or "")
        internal = not url.startswith(("http://", "https://"))
        chunks: list[tuple[str, str]] = []
        if page.strip():
            tables, narrative = _split_tables_and_text(page)
            for heading, rows in tables:
                chunks += [("calculated" if internal else "table", c) for c in _table_passages(heading, rows)]
            chunks += [("calculated" if internal else "narrative", c) for c in _narrative_passages(narrative)]
        if not chunks:
            excerpt = str(row.get("fullContent") or row.get("snippet") or "")
            if internal:
                chunks = [("calculated", c) for c in (_narrative_passages(excerpt) or ([excerpt.strip()] if excerpt.strip() else []))]
            else:
                chunks = [("search_excerpt", c) for c in _narrative_passages(excerpt)]
                meta["reliability"] = round(meta["reliability"] * 0.85, 3)
        added = 0
        for kind, text in chunks:
            text = text.strip()
            if len(text) < 60:
                continue
            added += 1
            passage = Passage(passage_id=f"{source_id}.{added}", source_id=source_id, text=text, kind=kind,
                              numbers=_NUMBER_RE.findall(text)[:60], **meta)
            self.passages.append(passage)
            self._by_id[passage.passage_id] = passage
        return added

    def finalize(self) -> "EvidenceStore":
        self._tf = [Counter(_tokens(p.title + " " + p.text)) for p in self.passages]
        self._df = Counter()
        for tf in self._tf:
            self._df.update(tf.keys())
        self._avg_len = (sum(sum(tf.values()) for tf in self._tf) / len(self._tf)) if self._tf else 1.0
        return self

    # ── querying ─────────────────────────────────────────────────────────────
    def get(self, passage_id: str) -> Passage | None:
        return self._by_id.get(str(passage_id or "").strip().upper())

    def source_ids(self) -> list[str]:
        return list(dict.fromkeys(p.source_id for p in self.passages))

    def mean_reliability(self) -> float:
        return sum(p.reliability for p in self.passages) / len(self.passages) if self.passages else 0.0

    def search(self, query: str, k: int = 12, *, min_reliability: float = 0.0, exclude: Iterable[str] = (),
               max_per_source: int = 3, kinds: Iterable[str] | None = None) -> list[tuple[Passage, float]]:
        """BM25 relevance x reliability; at most ``max_per_source`` passages per source."""
        terms = [t for t in dict.fromkeys(_tokens(query))]
        if not terms or not self.passages:
            return []
        excluded = {str(x).upper() for x in exclude}
        allowed = set(kinds) if kinds else None
        n = len(self.passages)
        scored = []
        for passage, tf in zip(self.passages, self._tf):
            if passage.passage_id in excluded or passage.reliability < min_reliability:
                continue
            if allowed and passage.kind not in allowed:
                continue
            length = sum(tf.values()) or 1
            bm25 = 0.0
            for term in terms:
                f = tf.get(term, 0)
                if not f:
                    continue
                idf = math.log(1 + (n - self._df[term] + 0.5) / (self._df[term] + 0.5))
                bm25 += idf * f * 2.2 / (f + 1.2 * (0.25 + 0.75 * length / self._avg_len))
            if bm25 <= 0:
                continue
            boost = 1.1 if passage.kind in {"table", "calculated"} else 1.0
            scored.append((passage, round(bm25 * (0.5 + passage.reliability) * boost, 4)))
        scored.sort(key=lambda item: -item[1])
        per_source: Counter = Counter()
        out = []
        for passage, score in scored:
            if per_source[passage.source_id] >= max_per_source:
                continue
            per_source[passage.source_id] += 1
            out.append((passage, score))
            if len(out) >= k:
                break
        return out

    def source_summary(self) -> list[dict]:
        rows: dict[str, dict] = {}
        for p in self.passages:
            item = rows.setdefault(p.source_id, {
                "sourceId": p.source_id, "url": p.url, "title": p.title, "publisher": p.publisher,
                "published": p.published, "authorityTier": p.authority_tier, "reliability": p.reliability,
                "passages": 0, "kinds": Counter(),
            })
            item["passages"] += 1
            item["kinds"][p.kind] += 1
        return [{**v, "kinds": dict(v["kinds"])} for v in rows.values()]

    def capture_summary(self) -> dict:
        kinds = Counter(p.kind for p in self.passages)
        tiers = Counter(p.authority_tier for p in self.passages)
        return {
            "passages": len(self.passages), "sources": len(self.source_ids()),
            "meanReliability": round(self.mean_reliability(), 3), "kinds": dict(kinds), "authorityTiers": dict(tiers),
            "chars": sum(len(p.text) for p in self.passages),
            "sample": [p.to_dict(max_text=240) for p in self.passages[:5]],
        }


def build_evidence_store(rows: list[dict], *, question: str = "", manifest: list[dict] | None = None) -> EvidenceStore:
    """Build a store from enriched source rows.

    With ``manifest`` (the canonical source registry), passages take the
    canonical [S#] id of their source; rows not in the manifest are skipped
    (they are not citable). Without it, ids are positional (``S1``, ``S2``…).
    """
    store = EvidenceStore()
    id_for: dict[str, str] = {}
    if manifest:
        try:
            from utils.source_identity import source_identity_key
        except Exception:  # pragma: no cover
            source_identity_key = None  # type: ignore
        for entry in manifest:
            sid = str(entry.get("id") or "").upper()
            for key in (str(entry.get("url") or ""), str(entry.get("publisher_page_url") or "")):
                if key:
                    id_for.setdefault("url:" + key.rstrip("/"), sid)
            if source_identity_key:
                try:
                    ident = source_identity_key(entry)
                    if ident:
                        id_for.setdefault("key:" + ident, sid)
                except Exception:
                    pass
            title = str(entry.get("title") or "").casefold().strip()
            if title and not entry.get("url"):
                id_for.setdefault("title:" + title, sid)
    used: set[str] = set()
    for index, row in enumerate(rows or [], 1):
        if not isinstance(row, dict):
            continue
        if manifest:
            sid = id_for.get("url:" + str(row.get("url") or "").rstrip("/"), "")
            if not sid:
                try:
                    from utils.source_identity import source_identity_key as _key
                    sid = id_for.get("key:" + (_key(row) or ""), "")
                except Exception:
                    sid = ""
            if not sid:
                sid = id_for.get("title:" + str(row.get("title") or "").casefold().strip(), "")
            if not sid or sid in used:
                continue
        else:
            sid = f"S{index}"
        used.add(sid)
        store.add_source(sid, row)
    return store.finalize()
