"""Retrieval benchmark, step 2: configurations A / B / C on the exported evidence stores (no network, no LLM).

Runs on the RAG service's Python stack (sentence-transformers 3.0.1, CPU torch; Python 3.12 here) with the RAG
service's models: all-MiniLM-L6-v2 embeddings and the cross-encoder/ms-marco-MiniLM-L6-v2 reranker.

  A  production: EvidenceStore.search (BM25 x (0.5 + reliability) x 1.1 for tables), per query, top 2*k_leaf
     with <= 3 passages per source, max-merged over the leaf's queries, then _leaf_hits' weak-match cut.
     Taken from the export (the production code itself) for the leaves' own queries; re-computed here with the
     same formula (verified to match) for the paraphrased queries.
  B  hybrid: per query, reciprocal-rank fusion (k=60) of the A ranking and a semantic ranking
     (cosine x (0.5 + reliability) x table boost), <= 3 passages per source, top 2*k_leaf, max-merged.
  C  B's top 40 per leaf reranked by the cross-encoder on (leaf query, title + passage);
     score = sigmoid(ce) x (0.5 + reliability) x table boost; <= 3 per source; near-duplicate suppression
     (a passage is skipped if it is a near-duplicate of a higher-ranked one AND states the same numbers).

usage (python 3.12):
  retrieval_bench.py run   EXPORT_DIR SELECTED.json OUT_DIR      embeddings (cached), A/B/C, automatic metrics
  retrieval_bench.py pool  OUT_DIR SAMPLE.json                   judging pool for the sampled queries
  retrieval_bench.py score OUT_DIR LABELS.json                   precision / recall / MRR / nDCG from labels
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")          # cached models only
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

K_LEAF = {"detailed": 12, "in_depth": 14}
RRF_K = 60
RERANK_DEPTH = 40
# configurations computed by "run" (BENCH_CONFIGS=A,D to run a subset)
CONFIGS = [c for c in os.getenv("BENCH_CONFIGS", "A,B,C,C_nodedup,D").split(",") if c]
DUP_COSINE = 0.92
DUP_SHINGLE = 0.6

# ── production tokenizer and BM25 (utils/evidence_store.py), copied verbatim ──────────────────────────────
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9.%-]*")
_STOP = {"the", "and", "of", "in", "a", "an", "to", "for", "on", "with", "by", "is", "are", "was", "were", "as", "at",
         "that", "this", "from", "or", "be", "it", "its", "has", "have", "had", "which", "their", "than", "over", "into"}
_NUM_RE = re.compile(r"(?<![\w.])[-+]?\d[\d,]*(?:\.\d+)?%?")


def _tokens(text: str) -> list[str]:
    return [t.strip(".%-") for t in _TOKEN_RE.findall(str(text or "").casefold()) if t.strip(".%-") and t not in _STOP]


class Lexical:
    def __init__(self, passages: list[dict]) -> None:
        self.p = passages
        self.tf = [Counter(_tokens(x["title"] + " " + x["text"])) for x in passages]
        self.df = Counter()
        for tf in self.tf:
            self.df.update(tf.keys())
        self.avg = (sum(sum(tf.values()) for tf in self.tf) / len(self.tf)) if self.tf else 1.0

    def ranking(self, query: str) -> list[tuple[str, float]]:
        terms = list(dict.fromkeys(_tokens(query)))
        n, out = len(self.p), []
        for x, tf in zip(self.p, self.tf):
            length = sum(tf.values()) or 1
            bm25 = 0.0
            for term in terms:
                f = tf.get(term, 0)
                if f:
                    idf = math.log(1 + (n - self.df[term] + 0.5) / (self.df[term] + 0.5))
                    bm25 += idf * f * 2.2 / (f + 1.2 * (0.25 + 0.75 * length / self.avg))
            if bm25 > 0:
                boost = 1.1 if x["kind"] in {"table", "calculated"} else 1.0
                out.append((x["id"], round(bm25 * (0.5 + x["reliability"]) * boost, 4)))
        out.sort(key=lambda t: -t[1])
        return out


def _cap(ranked: list[tuple[str, float]], src: dict, k: int, per_source: int = 3) -> list[tuple[str, float]]:
    seen: Counter = Counter()
    out = []
    for pid, s in ranked:
        if seen[src[pid]] >= per_source:
            continue
        seen[src[pid]] += 1
        out.append((pid, s))
        if len(out) >= k:
            break
    return out


def _merge(per_query: list[list[tuple[str, float]]]) -> list[tuple[str, float]]:
    best: dict[str, float] = {}
    for ranked in per_query:
        for pid, s in ranked:
            best[pid] = max(best.get(pid, 0.0), s)
    return sorted(best.items(), key=lambda t: -t[1])


def _weak_cut(ranked: list[tuple[str, float]]) -> list[tuple[str, float]]:
    if not ranked:
        return []
    top = ranked[0][1]
    return [(pid, s) for i, (pid, s) in enumerate(ranked) if i < 6 or s >= 0.2 * top]


def _shingles(text: str, n: int = 5) -> set:
    words = re.findall(r"[a-z0-9]+", text.casefold())
    return {" ".join(words[i:i + n]) for i in range(max(1, len(words) - n + 1))}


def _numbers(text: str) -> frozenset:
    return frozenset(x.replace(",", "") for x in _NUM_RE.findall(text) if len(x.strip("%")) >= 2)


class Run:
    """One exported report with its embeddings."""

    def __init__(self, doc: dict, emb, model, ce) -> None:
        import numpy as np
        self.doc, self.np = doc, np
        self.p = doc["passages"]
        self.idx = {x["id"]: i for i, x in enumerate(self.p)}
        self.src = {x["id"]: x["sourceId"] for x in self.p}
        self.rel = np.array([0.5 + x["reliability"] for x in self.p]) * np.array(
            [1.1 if x["kind"] in {"table", "calculated"} else 1.0 for x in self.p])
        self.emb, self.model, self.ce = emb, model, ce
        self.k = 2 * K_LEAF.get(doc["tier"], 12)
        self.lex = Lexical(self.p)
        self.timing = Counter()
        self._sh: dict[str, set] = {}
        self._ce_cache: dict = {}
        self._sem_cache: dict = {}

    def queries(self, leaf: dict) -> list[str]:
        return leaf["queries"] + ([" ".join(leaf["entities"])] if leaf["entities"] else []) + [leaf["title"]]

    def lexical_for(self, leaf: dict, query: str) -> list[tuple[str, float]]:
        exported = self.doc["lexical"].get(leaf["leafId"], {}).get(query)
        if exported is not None:
            return [(pid, s) for pid, s in exported]
        t0 = time.perf_counter()
        ranked = self.lex.ranking(query)[:100]
        self.timing["lexical_s"] += time.perf_counter() - t0
        return ranked

    def config_a(self, leaf: dict, queries: list[str] | None = None) -> list[str]:
        per = [_cap(self.lexical_for(leaf, q), self.src, self.k) for q in (queries or self.queries(leaf))]
        return [pid for pid, _ in _weak_cut(_merge(per))]

    def _semantic(self, query: str):
        if query in self._sem_cache:  # B is computed again inside C: one embedding per query, timed once
            return self._sem_cache[query]
        self._sem_cache[query] = self._semantic_uncached(query)
        return self._sem_cache[query]

    def _semantic_uncached(self, query: str):
        np = self.np
        t0 = time.perf_counter()
        qv = self.model.encode([query], normalize_embeddings=True, show_progress_bar=False)[0]
        self.timing["query_embed_s"] += time.perf_counter() - t0
        t0 = time.perf_counter()
        scores = (self.emb @ qv) * self.rel
        order = np.argsort(-scores)[:200]
        self.timing["vector_search_s"] += time.perf_counter() - t0
        return [(self.p[i]["id"], float(scores[i])) for i in order]

    def config_b(self, leaf: dict, queries: list[str] | None = None) -> list[str]:
        per = []
        for q in queries or self.queries(leaf):
            lex = self.lexical_for(leaf, q)
            sem = self._semantic(q)
            t0 = time.perf_counter()
            fused: dict[str, float] = {}
            for rank, (pid, _s) in enumerate(lex):
                fused[pid] = fused.get(pid, 0.0) + 1.0 / (RRF_K + rank + 1)
            for rank, (pid, _s) in enumerate(sem):
                fused[pid] = fused.get(pid, 0.0) + 1.0 / (RRF_K + rank + 1)
            per.append(_cap(sorted(fused.items(), key=lambda t: -t[1]), self.src, self.k))
            self.timing["fusion_s"] += time.perf_counter() - t0
        return [pid for pid, _ in _merge(per)][: self.k]

    def config_d(self, leaf: dict, queries: list[str] | None = None) -> list[str]:
        """BM25 candidates (A's per-query lists, max-merged, top 40) reranked by the cross-encoder - no embeddings.
        Near-duplicates are judged by word shingles only (there are no vectors in this configuration)."""
        per = [_cap(self.lexical_for(leaf, q), self.src, self.k) for q in (queries or self.queries(leaf))]
        cands = [pid for pid, _ in _merge(per)][:RERANK_DEPTH]
        if not cands:
            return []
        q = self.ce_query(leaf, queries)
        t0 = time.perf_counter()
        raw = self.ce.predict([(q, (self.p[self.idx[pid]]["title"] + ". " + self.p[self.idx[pid]]["text"])[:1500])
                               for pid in cands], batch_size=16, show_progress_bar=False)
        self.timing["rerank_d_s"] += time.perf_counter() - t0
        scored = sorted(((pid, float(1 / (1 + math.exp(-s))) * self.rel[self.idx[pid]]) for pid, s in zip(cands, raw)),
                        key=lambda t: -t[1])
        out, seen = [], Counter()
        for pid, _s in scored:
            if seen[self.src[pid]] >= 3:
                continue
            if any(self._shingle_dup(pid, other) and self.same_figures(pid, other) for other in out):
                continue
            seen[self.src[pid]] += 1
            out.append(pid)
        return out

    def _shingle_dup(self, a: str, b: str) -> bool:
        sa = self._sh.setdefault(a, _shingles(self.p[self.idx[a]]["text"]))
        sb = self._sh.setdefault(b, _shingles(self.p[self.idx[b]]["text"]))
        return bool(sa and sb) and len(sa & sb) / len(sa | sb) >= DUP_SHINGLE

    def ce_query(self, leaf: dict, queries: list[str] | None = None) -> str:
        qs = queries or leaf["queries"]
        return (leaf["title"] + ": " + "; ".join(qs[:3]))[:400]

    def config_c(self, leaf: dict, queries: list[str] | None = None, dedup: bool = True) -> list[str]:
        np = self.np
        cands = self.config_b(leaf, queries)[:RERANK_DEPTH]
        if not cands:
            return []
        q = self.ce_query(leaf, queries)
        key = (q, tuple(cands))
        if key in self._ce_cache:  # the no-dedup variant reuses the same reranker scores (timed once)
            raw = self._ce_cache[key]
        else:
            t0 = time.perf_counter()
            raw = self.ce.predict([(q, (self.p[self.idx[pid]]["title"] + ". " + self.p[self.idx[pid]]["text"])[:1500])
                                   for pid in cands], batch_size=16, show_progress_bar=False)
            self.timing["rerank_s"] += time.perf_counter() - t0
            self.timing["reranked_pairs"] += len(cands)
            self._ce_cache[key] = raw
        scored = sorted(((pid, float(1 / (1 + math.exp(-s))) * self.rel[self.idx[pid]]) for pid, s in zip(cands, raw)),
                        key=lambda t: -t[1])
        out, seen = [], Counter()
        for pid, _s in scored:
            if seen[self.src[pid]] >= 3:
                continue
            if dedup and any(self.near_dup(pid, other) and self.same_figures(pid, other) for other in out):
                continue
            seen[self.src[pid]] += 1
            out.append(pid)
        return out

    def near_dup(self, a: str, b: str) -> bool:
        ia, ib = self.idx[a], self.idx[b]
        if float(self.emb[ia] @ self.emb[ib]) >= DUP_COSINE:
            return True
        sa = self._sh.setdefault(a, _shingles(self.p[ia]["text"]))
        sb = self._sh.setdefault(b, _shingles(self.p[ib]["text"]))
        return bool(sa and sb) and len(sa & sb) / len(sa | sb) >= DUP_SHINGLE

    def same_figures(self, a: str, b: str) -> bool:
        return _numbers(self.p[self.idx[a]]["text"]) == _numbers(self.p[self.idx[b]]["text"])


def _embed_run(doc: dict, model, cache: Path):
    import numpy as np
    texts = [(x["title"] + "\n" + x["text"])[:2000] for x in doc["passages"]]
    digest = hashlib.sha256("\x00".join(texts).encode("utf-8")).hexdigest()[:16]
    path = cache / f"{doc['capture']}_{digest}.npy"
    if path.exists():
        return np.load(path), 0.0, True
    t0 = time.perf_counter()
    emb = model.encode(texts, batch_size=64, normalize_embeddings=True, show_progress_bar=False)
    seconds = time.perf_counter() - t0
    np.save(path, emb.astype("float32"))
    return emb.astype("float32"), seconds, False


def _list_metrics(run: Run, ranked: list[str], k: int) -> dict:
    top = ranked[:k]
    if not top:
        return {"n": 0}
    dups = dup_diff = 0
    for i, pid in enumerate(top):
        earlier = [o for o in top[:i] if run.near_dup(pid, o)]
        if earlier:
            dups += 1
            if all(not run.same_figures(pid, o) for o in earlier):
                dup_diff += 1
    return {"n": len(top), "sources": len({run.src[p] for p in top}), "dups": dups, "dupsDifferentFigures": dup_diff,
            "missingProvenance": sum(1 for p in top if not run.p[run.idx[p]]["sourceId"] or not run.p[run.idx[p]]["url"])}


def cmd_run(export_dir: str, selected: str, out_dir: str) -> None:
    import numpy as np
    import psutil
    from sentence_transformers import CrossEncoder, SentenceTransformer

    out, cache = Path(out_dir), Path(out_dir) / "emb_cache"
    cache.mkdir(parents=True, exist_ok=True)
    proc = psutil.Process()
    mem = {"start_mb": proc.memory_info().rss / 2**20}
    t0 = time.perf_counter()
    model = SentenceTransformer("all-MiniLM-L6-v2", device="cpu")
    mem["after_embedder_mb"] = proc.memory_info().rss / 2**20
    ce = CrossEncoder("cross-encoder/ms-marco-MiniLM-L6-v2", device="cpu")
    mem["after_reranker_mb"] = proc.memory_info().rss / 2**20
    load_s = time.perf_counter() - t0
    names = json.load(open(selected, encoding="utf-8"))
    totals = {c: Counter() for c in CONFIGS}
    per_run, lists = [], {}
    embed_s = embed_passages = 0
    lex_s = lex_q = 0.0
    timing = Counter()
    peak = 0.0
    a_check = Counter()
    for name in names:
        doc = json.load(open(Path(export_dir) / f"{name}.json", encoding="utf-8"))
        emb, secs, cached = _embed_run(doc, model, cache)
        if not cached:
            embed_s += secs
            embed_passages += len(doc["passages"])
        run = Run(doc, emb, model, ce)
        lex_s += doc["lexicalQuerySeconds"]
        lex_q += doc["lexicalQueries"]
        # the re-implemented BM25 must reproduce production's ranking (it is used for paraphrased queries)
        for leaf in doc["leaves"][:3]:
            q = leaf["queries"][0]
            mine = [pid for pid, _ in run.lex.ranking(q)[:20]]
            prod = [pid for pid, _ in doc["lexical"][leaf["leafId"]][q][:20]]
            a_check["same" if mine == prod else "different"] += 1
        run_lists = {}
        for leaf in doc["leaves"]:
            makers = {"A": lambda: run.config_a(leaf), "B": lambda: run.config_b(leaf), "C": lambda: run.config_c(leaf),
                      "C_nodedup": lambda: run.config_c(leaf, dedup=False), "D": lambda: run.config_d(leaf)}
            ranked = {cfg: makers[cfg]() for cfg in CONFIGS}
            run_lists[leaf["leafId"]] = ranked
            for cfg, items in ranked.items():
                for k in (5, 10):
                    m = _list_metrics(run, items, k)
                    for key, v in m.items():
                        totals[cfg][f"{key}@{k}"] += v
                totals[cfg]["leaves"] += 1
                totals[cfg]["empty"] += 0 if items else 1
        # overlap between leaves of one report (identical context for every section is a failure)
        for cfg in [c for c in CONFIGS if c != "C_nodedup"]:
            sets = [set(v[cfg][:10]) for v in run_lists.values() if v[cfg]]
            pairs = [(i, j) for i in range(len(sets)) for j in range(i + 1, len(sets))]
            if pairs:
                totals[cfg]["leafOverlapSum"] += sum(len(sets[i] & sets[j]) / len(sets[i] | sets[j]) for i, j in pairs)
                totals[cfg]["leafPairs"] += len(pairs)
        # agreement with what the writer finally cited in the section (biased to A: the writer only saw A)
        for leaf in doc["leaves"]:
            cited = set()
            for title, ids in doc["cited"].items():
                if title.strip().casefold() == leaf["sectionTitle"].strip().casefold():
                    cited |= set(ids)
            if not cited:
                continue
            for cfg in [c for c in CONFIGS if c != "C_nodedup"]:
                top = set(run_lists[leaf["leafId"]][cfg][:run.k // 2])
                totals[cfg]["citedFound"] += len(top & cited)
                totals[cfg]["citedTotal"] += len(cited)
        timing.update(run.timing)
        lists[name] = run_lists
        peak = max(peak, proc.memory_info().rss / 2**20)
        per_run.append({"capture": name, "passages": len(doc["passages"]), "leaves": len(doc["leaves"]),
                        "embedSeconds": round(secs, 2), "cached": cached})
        print(f"{name[17:]} passages={len(doc['passages']):5d} leaves={len(doc['leaves']):3d} embed={secs:6.1f}s", flush=True)
    mem["peak_mb"] = peak
    leaves = totals["A"]["leaves"]
    summary = {"runs": len(names), "leaves": leaves, "passages": sum(r["passages"] for r in per_run),
               "bm25ReimplementationCheck": dict(a_check), "modelLoadSeconds": round(load_s, 1),
               "memoryMB": {k: round(v) for k, v in mem.items()},
               "embedding": {"passages": embed_passages, "seconds": round(embed_s, 1),
                             "msPerPassage": round(1000 * embed_s / embed_passages, 2) if embed_passages else None},
               "latencyMsPerLeaf": {
                   "A": round(1000 * lex_s / max(1, lex_q) * (lex_q / max(1, leaves)), 1),
                   "B_extra": round(1000 * (timing["query_embed_s"] + timing["vector_search_s"] + timing["fusion_s"] / 3) / max(1, leaves), 1),
                   "C_rerank_extra": round(1000 * timing["rerank_s"] / max(1, leaves), 1),
                   "D_rerank_extra": round(1000 * timing["rerank_d_s"] / max(1, leaves), 1)},
               "timing": {k: round(v, 2) for k, v in timing.items()},
               "configs": {}}
    for cfg, t in totals.items():
        n = t["leaves"] or 1
        summary["configs"][cfg] = {
            "emptyLeaves": t["empty"],
            "sourceDiversity@10": round(t["sources@10"] / max(1, t["n@10"]), 3),
            "duplicateRate@10": round(t["dups@10"] / max(1, t["n@10"]), 4),
            "duplicatesWithDifferentFigures@10": t["dupsDifferentFigures@10"],
            "duplicateRate@5": round(t["dups@5"] / max(1, t["n@5"]), 4),
            "missingProvenance@10": t["missingProvenance@10"],
            "meanLeafOverlap@10": round(t["leafOverlapSum"] / max(1, t["leafPairs"]), 4) if t["leafPairs"] else None,
            "writerCitedFound": round(t["citedFound"] / max(1, t["citedTotal"]), 3) if t["citedTotal"] else None,
        }
    (out / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    (out / "lists.json").write_text(json.dumps(lists), encoding="utf-8")
    print(json.dumps(summary, indent=1))


def cmd_pool(out_dir: str, sample_path: str) -> None:
    """Judging pool: for each sampled query, the union of the three configurations' top 5, shuffled so the
    judge does not see which configuration retrieved what."""
    import random

    import numpy as np
    from sentence_transformers import CrossEncoder, SentenceTransformer
    out = Path(out_dir)
    sample = json.load(open(sample_path, encoding="utf-8"))
    model = SentenceTransformer("all-MiniLM-L6-v2", device="cpu")
    ce = CrossEncoder("cross-encoder/ms-marco-MiniLM-L6-v2", device="cpu")
    pools, runs_cache = [], {}
    for item in sample["queries"]:
        name = item["capture"]
        if name not in runs_cache:
            doc = json.load(open(Path(sample["exportDir"]) / f"{name}.json", encoding="utf-8"))
            emb, _s, _c = _embed_run(doc, model, out / "emb_cache")
            runs_cache[name] = Run(doc, emb, model, ce)
        run = runs_cache[name]
        leaf = next(lf for lf in run.doc["leaves"] if lf["leafId"] == item["leafId"])
        queries = item.get("queries")  # paraphrased queries replace the leaf's own
        got = {"A": run.config_a(leaf, queries)[:10], "B": run.config_b(leaf, queries)[:10],
               "C": run.config_c(leaf, queries)[:10], "D": run.config_d(leaf, queries)[:10]}
        union = list(dict.fromkeys(pid for cfg in "ABCD" for pid in got[cfg][:5]))
        random.Random(item["qid"]).shuffle(union)
        pools.append({"qid": item["qid"], "capture": name, "leafId": item["leafId"],
                      "need": item.get("need") or f"{leaf['title']}: {leaf['goal']}",
                      "queries": queries or leaf["queries"], "rankings": got,
                      "pool": [{"id": pid, "title": run.p[run.idx[pid]]["title"][:120],
                                "text": re.sub(r"\s+", " ", run.p[run.idx[pid]]["text"])[:700]} for pid in union]})
    (out / "pool.json").write_text(json.dumps(pools, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(pools)} queries, {sum(len(p['pool']) for p in pools)} passages to judge")


def cmd_score(out_dir: str, labels_path: str) -> None:
    out = Path(out_dir)
    pools = {p["qid"]: p for p in json.load(open(out / "pool.json", encoding="utf-8"))}
    labels = json.load(open(labels_path, encoding="utf-8"))
    result = {}
    for group in ("original", "paraphrase", "all"):
        acc = {c: Counter() for c in "ABCD"}
        n = 0
        for qid, grades in labels.items():
            if qid.startswith("_"):
                continue
            pool = pools[qid]
            if group != "all" and (qid.startswith("p") != (group == "paraphrase")):
                continue
            n += 1
            relevant = {pid for pid, g in grades.items() if g >= 1}
            ideal = sorted(grades.values(), reverse=True)[:5]
            idcg = sum((2 ** g - 1) / math.log2(i + 2) for i, g in enumerate(ideal)) or 1.0
            for cfg in "ABCD":
                top = pool["rankings"][cfg][:5]
                g5 = [grades.get(pid, 0) for pid in top]
                acc[cfg]["p5"] += sum(1 for g in g5 if g >= 1) / 5
                acc[cfg]["p5strict"] += sum(1 for g in g5 if g >= 2) / 5
                acc[cfg]["r5"] += (sum(1 for pid in top if pid in relevant) / len(relevant)) if relevant else 0
                acc[cfg]["mrr"] += next((1 / (i + 1) for i, g in enumerate(g5) if g >= 1), 0.0)
                acc[cfg]["ndcg5"] += sum((2 ** g - 1) / math.log2(i + 2) for i, g in enumerate(g5)) / idcg
        result[group] = {"queries": n, **{cfg: {k: round(v / max(1, n), 3) for k, v in acc[cfg].items()} for cfg in "ABCD"}}
    (out / "judged.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    {"run": cmd_run, "pool": cmd_pool, "score": cmd_score}[sys.argv[1]](*sys.argv[2:])
