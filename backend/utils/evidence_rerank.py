"""Section evidence reranking: BM25 candidates reranked by the RAG service's cross-encoder (no embeddings).

Configuration D of backend/benchmark/retrieval: on 24 judged queries it raised nDCG@5 from 0.540 to 0.692
(paired 95% CI +0.071 to +0.236) and precision@5 from 0.633 to 0.783, and matched BM25 + MiniLM + reranker
without embedding any passage.

For each outline leaf (subsection, or section without subsections):
  1. candidates: the production BM25 search (EvidenceStore.search, BM25 x reliability x table boost, <= 3 per
     source) for each of the leaf's queries, max-merged, top GG_V2_RERANK_DEPTH (40);
  2. the RAG service's cross-encoder scores (leaf title + queries, title + passage);
  3. score = sigmoid(cross-encoder) x (0.5 + reliability) x 1.1 for tables - reliability weighting is kept;
  4. <= 3 passages per source (source diversity), and a near-duplicate (5-word-shingle Jaccard >= 0.6) of a
     higher-ranked passage is skipped ONLY when it states exactly the same numbers - a similar passage with
     different figures or periods is kept, so conflicting evidence reaches the writer;
  5. the same weak-match cut as report_outline._leaf_hits (top 6, then >= 20% of the best score).

Nothing is embedded or stored. Any failure - switched off (GG_V2_RERANK=0), service unreachable, an error, a
per-leaf timeout or the run's time budget - leaves that leaf on the BM25 ranking, so a report never waits on or
fails because of the reranker.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import re
import time
from collections import Counter

log = logging.getLogger("evidence_rerank")

_NUM_RE = re.compile(r"(?<![\w.])[-+]?\d[\d,]*(?:\.\d+)?%?")
DUP_SHINGLE = 0.6


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


def enabled() -> bool:
    return os.getenv("GG_V2_RERANK", "1").strip().casefold() not in {"0", "false", "no", "off"}


def leaf_queries(leaf) -> list[str]:
    """The queries production retrieval searches for a leaf (report_outline._leaf_hits)."""
    return list(leaf.queries) + ([" ".join(leaf.entities)] if leaf.entities else []) + [leaf.title]


def rerank_query(leaf) -> str:
    return (str(leaf.title) + ": " + "; ".join(str(q) for q in list(leaf.queries)[:3]))[:400]


def bm25_candidates(store, leaf, *, k: int, depth: int) -> list[str]:
    best: dict[str, float] = {}
    for query in leaf_queries(leaf):
        for passage, score in store.search(query, k=k, max_per_source=3):
            best[passage.passage_id] = max(best.get(passage.passage_id, 0.0), score)
    return [pid for pid, _ in sorted(best.items(), key=lambda t: -t[1])][:depth]


def _shingles(text: str, n: int = 5) -> set:
    words = re.findall(r"[a-z0-9]+", str(text or "").casefold())
    return {" ".join(words[i:i + n]) for i in range(max(1, len(words) - n + 1))}


def figures(text: str) -> frozenset:
    """The numbers a passage states (2+ characters), used to tell a true duplicate from a passage that
    reports different values, periods or units."""
    return frozenset(x.replace(",", "") for x in _NUM_RE.findall(str(text or "")) if len(x.strip("%")) >= 2)


def is_redundant(a_text: str, b_text: str, cache: dict | None = None) -> bool:
    """True only when two passages are near-duplicates AND state the same numbers (nothing would be lost)."""
    cache = cache if cache is not None else {}
    sa = cache.setdefault(a_text, _shingles(a_text))
    sb = cache.setdefault(b_text, _shingles(b_text))
    if not sa or not sb or len(sa & sb) / len(sa | sb) < DUP_SHINGLE:
        return False
    return figures(a_text) == figures(b_text)


def rank_reranked(store, candidates: list[str], raw: dict[str, float]) -> list[tuple[str, float]]:
    scored = []
    for pid in candidates:
        passage = store.get(pid)
        if passage is None or pid not in raw:
            continue
        boost = 1.1 if passage.kind in {"table", "calculated"} else 1.0
        relevance = 1.0 / (1.0 + math.exp(-float(raw[pid])))
        scored.append((pid, round(relevance * (0.5 + passage.reliability) * boost, 6)))
    scored.sort(key=lambda t: -t[1])
    out: list[tuple[str, float]] = []
    per_source: Counter = Counter()
    cache: dict = {}
    for pid, score in scored:
        passage = store.get(pid)
        if per_source[passage.source_id] >= 3:
            continue
        if any(is_redundant(passage.text, store.get(o).text, cache) for o, _ in out):
            continue
        per_source[passage.source_id] += 1
        out.append((pid, score))
    if not out:
        return []
    top = out[0][1]
    return [(pid, s) for i, (pid, s) in enumerate(out) if i < 6 or s >= 0.2 * top]


async def rerank_leaves(store, leaves: list, *, k: int, client=None) -> tuple[dict[str, list[tuple[str, float]]], dict]:
    """Reranked hits per leaf id; leaves missing from the result use BM25. ``client`` (tests) replaces the
    RAG service call: async (query, [{"id", "text"}]) -> {id: score} or None."""
    stats: dict = {"enabled": enabled(), "leaves": len(leaves), "reranked": 0, "fallback": 0,
                   "fallbackReasons": {}, "pairs": 0, "seconds": 0.0, "serviceMs": 0}
    if not stats["enabled"] or not leaves:
        stats["fallback"] = len(leaves) if leaves else 0
        if leaves:
            stats["fallbackReasons"] = {"disabled": len(leaves)}
        return {}, stats
    if client is None:
        from utils.rag_client import rag_rerank as client  # noqa: N813
    depth = max(5, int(_env_float("GG_V2_RERANK_DEPTH", 40)))
    per_leaf_timeout = _env_float("GG_V2_RERANK_TIMEOUT_S", 20.0)
    budget = _env_float("GG_V2_RERANK_BUDGET_S", 150.0)
    slots = asyncio.Semaphore(max(1, int(_env_float("GG_V2_RERANK_CONCURRENCY", 2))))
    started = time.perf_counter()
    reasons: Counter = Counter()
    state = {"down": False}
    results: dict[str, list[tuple[str, float]]] = {}

    async def one(leaf) -> None:
        candidates = bm25_candidates(store, leaf, k=k, depth=depth)
        if len(candidates) < 2:
            reasons["too_few_candidates"] += 1
            return
        async with slots:
            if state["down"]:
                reasons["service_unavailable"] += 1
                return
            if time.perf_counter() - started > budget:
                reasons["time_budget"] += 1
                return
            payload = [{"id": pid, "text": (store.get(pid).title + ". " + store.get(pid).text)[:1500]}
                       for pid in candidates]
            t0 = time.perf_counter()
            try:
                raw = await asyncio.wait_for(client(rerank_query(leaf), payload), timeout=per_leaf_timeout)
            except asyncio.TimeoutError:
                reasons["timeout"] += 1
                return
            except Exception as exc:  # never fatal: the leaf keeps its BM25 ranking
                reasons[f"error:{type(exc).__name__}"] += 1
                state["down"] = True
                return
            stats["serviceMs"] += int((time.perf_counter() - t0) * 1000)
        if not raw:
            reasons["service_unavailable"] += 1
            state["down"] = True  # unreachable or refused: do not wait on it for every other leaf
            return
        ranked = rank_reranked(store, candidates, raw)
        if ranked:
            results[leaf.section_id] = ranked
            stats["pairs"] += len(candidates)

    await asyncio.gather(*(one(leaf) for leaf in leaves))
    stats["reranked"] = len(results)
    stats["fallback"] = len(leaves) - len(results)
    stats["fallbackReasons"] = dict(reasons)
    stats["seconds"] = round(time.perf_counter() - started, 1)
    log.info("Evidence rerank: %d of %d leaves reranked in %.1fs (%d pairs); BM25 fallback %s",
             stats["reranked"], len(leaves), stats["seconds"], stats["pairs"], dict(reasons) or "none")
    return results, stats


# ── per-section evidence budget ───────────────────────────────────────────────────────────────────────────
def evidence_tokens(passage) -> int:
    """Tokens a passage takes in a section prompt (header line + text, ~4 characters per token)."""
    header = f"[{passage.passage_id}] {passage.publisher or passage.title[:60]} | {passage.published or 'undated'} | " \
             f"{passage.authority_tier} | reliability {passage.reliability:.2f}\n"
    return (len(header) + len(passage.text)) // 4 + 1


def section_evidence_budget() -> int:
    """GG_V2_SECTION_EVIDENCE_TOKENS (16,000): saved runs had a median of ~5,500 and a p99 of ~21,000 evidence
    tokens per section call, so the default only trims the largest calls."""
    return max(2000, int(_env_float("GG_V2_SECTION_EVIDENCE_TOKENS", 16000)))


def fit_evidence(passages: list, *, scores: dict[str, float], keep: set[str], budget: int) -> tuple[list, dict]:
    """Passages that fit ``budget`` tokens, in their original order. Whole passages are dropped (never cut, so
    no citation loses its text), lowest relevance first; ``keep`` (passages behind the section's figures) are
    never dropped. Passages without a score rank in the middle."""
    tokens = {p.passage_id: evidence_tokens(p) for p in passages}
    total = sum(tokens.values())
    info = {"passages": len(passages), "tokens": total, "budget": budget, "dropped": 0, "droppedIds": []}
    if total <= budget:
        return list(passages), info
    top = max([s for s in scores.values() if s > 0] or [1.0])
    order = sorted((p for p in passages if p.passage_id not in keep),
                   key=lambda p: scores.get(p.passage_id, 0.5 * top) / top)
    dropped: set[str] = set()
    for passage in order:
        if total <= budget:
            break
        dropped.add(passage.passage_id)
        total -= tokens[passage.passage_id]
    info.update({"tokensAfter": total, "dropped": len(dropped), "droppedIds": sorted(dropped)[:40]})
    return [p for p in passages if p.passage_id not in dropped], info
