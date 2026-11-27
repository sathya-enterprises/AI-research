# Retrieval benchmark: evidence passages per report section

Offline comparison of section-level evidence retrieval on saved research captures. Run on 27 Nov 2026. No network
calls and no LLM calls were made.

## Data

- **Runs:** every saved capture with a V2 outline and a final report, keeping only the latest run of each distinct
  question. That gives 44 topics (15 In-Depth, 29 Detailed), listed in `selected_runs.json`.
- **Retrieval units:** 21,029 evidence passages across 572 outline leaves (subsections, or sections without
  subsections).
- **Queries:** each leaf's own queries, entities and title. These are what production retrieval
  (`report_outline._leaf_hits`) searches.

## Configurations

| | Ranking |
|---|---|
| A | Production `EvidenceStore.search`: BM25 × (0.5 + reliability) × 1.1 for tables. Top 2 × k_leaf per query, ≤ 3 passages per source, max-merged across the leaf's queries, then the weak-match cut. |
| B | A's per-query ranking fused with a MiniLM (`all-MiniLM-L6-v2`) cosine × reliability ranking by reciprocal-rank fusion (k = 60). ≤ 3 per source. |
| C | B's top candidates (≤ 2 × k_leaf) reranked by `cross-encoder/ms-marco-MiniLM-L6-v2`. Score = sigmoid(ce) × reliability × table boost. ≤ 3 per source. A near-duplicate (cosine ≥ 0.92 or 5-word-shingle Jaccard ≥ 0.6) is skipped only if it states exactly the same numbers. |
| D | A's candidates (max-merged, top 40) reranked by the same cross-encoder. No embeddings. Near-duplicates are found by shingles only. |

Configuration A comes from the production code itself (`scripts/retrieval_bench_export.py`). The copy of BM25 used
for paraphrased queries reproduces production's top-20 in 132 of 132 checks.

## Relevance judgments

- **No ground-truth labels exist.** The writer's citations can't serve as labels: the writer only ever saw
  A's passages, so they are biased toward A.
- **Sample:** 24 queries. These are 16 leaves drawn with a fixed seed from 8 topics (coal, pharma, EV two-wheelers,
  aviation, gold, FTAs, bank asset quality, US labour market), plus 8 paraphrased queries written to avoid the
  leaves' keywords.
- **Pooling:** the union of the four configurations' top 5, shuffled, with the configuration hidden. That is 268
  passages.
- **Grading:** 2 = directly serves the leaf's stated need, 1 = related or partial, 0 = not relevant. The grades are
  in `relevance_labels.json`.
- **Single judge:** the judging was done by Claude, not a human panel. Treat the numbers as indicative.

## Results (24 judged queries, K = 5)

| Metric | A BM25 | B BM25 + MiniLM | C BM25 + MiniLM + reranker | D BM25 + reranker |
|---|---:|---:|---:|---:|
| Precision@5 (grade ≥ 1) | 0.633 | 0.617 | 0.775 | 0.783 |
| Precision@5 (grade 2) | 0.250 | 0.267 | 0.317 | 0.333 |
| Recall@5 (of the pooled relevant) | 0.484 | 0.473 | 0.599 | 0.613 |
| MRR | 0.858 | 0.849 | 0.938 | 0.917 |
| nDCG@5 | 0.540 | 0.561 | 0.690 | 0.692 |
| nDCG@5, paraphrased queries only (8) | 0.415 | 0.365 | 0.615 | 0.558 |

Paired bootstrap, 5,000 resamples:

| Comparison | nDCG@5 difference (95% CI) | Queries better / worse / tied |
|---|---|---|
| C − A | +0.150 (+0.057 to +0.241) | 17 / 6 / 1 |
| D − A | +0.152 (+0.071 to +0.236) | 17 / 5 / 2 |
| B − A | +0.021 (−0.057 to +0.103) | not significant |
| C − D | −0.002 (−0.058 to +0.056) | no difference |

## Automatic metrics (all 572 leaves, K = 10)

| | A | B | C | D |
|---|---:|---:|---:|---:|
| Source diversity (distinct sources ÷ passages) | 0.670 | 0.658 | 0.667 | 0.677 |
| Near-duplicate rate in top 10 | 7.3% | 8.3% | 6.0% | 5.2% |
| Near-duplicates kept because their numbers differ | 364 | 420 | 343 | 295 |
| Passages without source id or URL | 0 | 0 | 0 | 0 |
| Mean overlap between leaves' top 10 (Jaccard) | 0.055 | 0.070 | 0.055 | 0.052 |

## Cost

Hardware: AMD Ryzen 5 5500H (8 threads), CPU only, 9.9 GB RAM, Python 3.12, sentence-transformers 3.0.1.

| | A | B | C | D |
|---|---:|---:|---:|---:|
| Query time per leaf | 6.5 ms | +48 ms | +48 ms + 2.09 s | +3.39 s |
| Passage embedding (index time) | – | 43.5 ms per passage (about 63 s for a 1,450-passage In-Depth report) | same as B | – |
| Pairs reranked per leaf | – | – | ≤ 2 × k_leaf (average 26.5) | 40 |
| Memory | – | torch + sentence-transformers import about 298 MB, plus about 15 MB per model | same | same |

The models already run inside the RAG service, so reusing that process adds no model memory to the backend.

## Reproduce

```bash
python scripts/retrieval_bench_export.py OUT/export                     # backend Python; rebuilds stores from captures
py -3.12 scripts/retrieval_bench.py run OUT/export selected_runs.json OUT/run      # RAG-service stack
py -3.12 scripts/retrieval_bench.py pool OUT/run sample_queries.json    # set exportDir in the sample first
py -3.12 scripts/retrieval_bench.py score OUT/run relevance_labels.json
```
