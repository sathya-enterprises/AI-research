# Report pipeline diagnosis (Phase 1)

Branch `backend-edit` @ `81a0fcc`. Read-only investigation: no code was changed for this document.

## How this was measured

- **Two live runs**, the same topic, driven through the real API exactly like the UI
  (`POST /api/chat/report/start` → poll `/status/{job}` → `POST /api/chat/report/pdf`), with
  `GG_RESEARCH_CAPTURE=1`:
  *"Sector rotation in the Indian stock markets over the last 4 quarters"*, tiers `detailed` and `in_depth`,
  2026-11-07, model `gemini-3-flash-preview` (writer, planner, expansion).
- Evidence used: the research capture JSON for each run, the final job result, the server log, and the
  rendered PDF (page images inspected). Captures and PDFs are kept locally (gitignored), not committed.
- **Caveat — one run per tier, one topic.** "Sector rotation" also takes several topic-specific code
  branches (hard-coded queries, a dedicated relevance branch, a calculated sector dataset). The
  mechanisms below are generic, but the exact numbers will differ for other topics.
- **Caveat — the detailed baseline is not 25–30 pages here.** This run produced **11 pages** and in-depth
  produced **15**. The 25–30 pages seen previously are not a property of the pipeline; they depend on
  how many charts, tables and source cards survive for a given topic (see RC1/RC2).

## Headline numbers

| | detailed | in-depth |
|---|---|---|
| Query angles → raw results | 5 → 60 | 8 → 144 |
| Sources after first relevance gate | 44 of 48 | 50 of 113 |
| Pages fetched OK / empty extraction / search-extract only | 32 / 10 / 12 | 44 / 3 (+3 timeouts, 1 empty PDF) / 8 |
| Site crawl: requested → fetched (limit) | 21 → 14 (24) | 42 → 28 (**28**, budget 48) |
| Fetched evidence → evidence shown to writer | 430,634 → 80,590 chars (−81%) | 666,481 → 162,839 chars (−76%) |
| **Writer draft** (single LLM call) | **12,330 chars (~1.9k words)** | **12,407 chars** |
| After expansion passes | 22,505 chars | 36,532 chars (7 passes, ~70 s of 480 s) |
| **After evidence guard** | **14,177 chars (−37%; 128 values redacted)** | **34,742 (20 pages) → 26,527 (14 pages), −24%; 131 values redacted** |
| Final report body | 13,860 chars / 1,866 words | 25,899 chars / 3,538 words |
| H2 sections / H3–H4 subsections | 6 / 24 | 10 / 31 |
| Charts: writer → evidence-validated → +gallery → **final** | 8 → 2 → 5 → **1** (2 data points) | 12 → 1 → 2 → **1** |
| Markdown tables in final body | 0 | 0 |
| Sources in appendix / cited in body | 47 / 10 (21%) | 55 / 21 (38%) |
| Sources with a publication date | 7 of 47 | 13 of 55 |
| **PDF pages / words** | **11 / 3,204** | **15 / 5,132** |
| Publication status / audit | WARNING (critical "quadruple repetition" finding, published anyway) | WARNING (critical: chart observations lack verifying excerpts; published anyway) |
| Wall time | 196 s | 331 s |

## Pipeline trace (both tiers take the same path)

1. **Job** — `routes/report.py:start_report_job` (L20012) → `_run_report_job_async` (L625) →
   `generate_report` (L20177, ~5,000 lines). Tier validated, explicit page count resolved
   (`utils/report_tiers.py:requested_report_page_count`, accepts 6–100 only).
2. **Intent** — `utils/intent.py:resolve_request_intent`.
3. **Query planning** — `report.py:_build_multi_angle_search_queries` (L9315). Cap 6/12 angles
   (L9338), but "sector rotation" returns a **hard-coded list**: 5 queries (detailed) / 8 (in-depth),
   all asking for "the last **eight** quarters" (L9355–9368) regardless of the requested window.
4. **Search** — `routes/chat.py:exa_search_multi`, per-query limit `_report_search_results_per_query`
   (12 / 18), plus stored-research reuse (`retrieve_web_research`) and news-KB rows.
5. **Relevance gate #1** — `report.py:_filter_relevant_sources` (L7865, 572 lines) then
   `_dedupe_enriched_sources` (L2657).
6. **Page fetch** — `_enrich_report_source` (L2776) → `utils/source_extractor.py:fetch_source_document`
   (httpx + BeautifulSoup, **no browser fallback**), page text cut to 15k / 20k chars (L21107).
7. **Relevance gate #2** (post-fetch, `require_context=True`) — same function.
8. **Site crawl** — `utils/site_crawler.py:crawl_relevant_internal_pages` (L242); the only place
   Playwright is used (rendered-HTML fallback). Results pass gate #2 again.
9. **Market data** — `utils/market_data.py` (sector quarterly returns, hard-coded `quarters=8`, L20933).
10. **Chart needs** — `utils/chart_planner.py:plan_chart_needs` (data top-up for planned charts).
11. **Writer context** — all source blocks joined, then `_compact_report_source_blocks` (L5751) to
    96k / 180k chars, giving every source an **equal slice** (~1.8k chars) ranked by question overlap.
12. **Parallel analysts** — `run_parallel_evidence_analysis` (L6477): 5 analyst calls whose findings
    are spliced into the writer prompt.
13. **Outline** — `_plan_report_structure` (L18627): one planner call that sees a 500/650-char
    preview per source and returns `sections[{heading, purpose, format}]`.
14. **Write** — `routes/report_llm.py:call_report_writer` (L714): **one** call returns the whole
    report as JSON (markdown + charts + keyStats).
15. **Expansion** — `_expand_report_semantically` (L16685): up to 4 / 7 continuation passes.
16. **~45 post-processing transforms** (~90 call sites) on `report_text`: dedupe, sanitizers, chart
    gates, the **evidence guard** `_sanitize_unverified_financial_claims` (L12634), evidence chart gallery
    `_append_evidence_chart_gallery` (L4125 → `utils/chart_gallery.py:blend_gallery_into_report`),
    requested-window gate (`utils/chart_evidence_audit.py:drop_charts_outside_requested_window`),
    scanability pass (L17200), page measurement `_rendered_page_count_preflight` (L11958, the full PDF
    is rendered 7–9 times per run), independent audit + repair `_run_independent_report_audit` (L15533).
17. **PDF** — `routes/pdf.py:generate_pdf` (L5299) → `build_pdf` (L4890) →
    `routes/html_report.py:build_html_report` (L4880) → Chromium print.

## Root causes, ranked by impact

### RC1 — Length is decided by one LLM call that both tiers make identically
*Impact: in-depth ≈ detailed; both far below target.*

- The writer is a **single generation** (`report.py:22099` → `report_llm.py:714`). Detailed returned
  **12,330 chars**, in-depth **12,407 chars**: the same size, despite in-depth having more sources and a
  14-section plan.
- The tier spec's generation controls are **never read**: `chunked_generation`, `modular_generation`,
  `chunk_size_sections` and `max_output_tokens` (`utils/report_tiers.py:56–106`) have no readers outside
  that file. Both tiers get the same 65,536-token cap (`report.py:5477`).
- The prompts actively push for short output:
  - `utils/chart_catalog.py:134`: "keep prose to short interpretive notes (2–3 sentences)".
  - `utils/report_tiers.py:77`: "Keep analytical paragraphs short".
  - `utils/report_tiers.py:106`: Deep Research should increase density "not paragraph count", using
    "short narrative bridges".
  - `utils/report_tiers.py:185`: "Do not target a page, word, or character count".
- Page targets are only metadata: `PageTarget(None, None, 30/100)` (`report_tiers.py:58, 86`) is a
  warning cap. `_ensure_report_page_budget` (L15575) expands only when the user typed an explicit page
  count (L15600 returns early otherwise), and `requested_report_page_count` rejects values above 100
  (`report_tiers.py:145, 159`). The UI still promises "70–100 pages"
  (`GrowthGradualChat.tsx:33`).
- Tests lock this "measure only" behaviour in place:
  - `test_report_reliability_fixes.py:95`
  - `test_report_tier_page_budget_v59.py:21, 123`
  - `test_report_tier_regressions.py:12, 20`
  - `test_report_depth_recovery_v13.py:29`
  - `test_report_tier_controls_v8.py:51`

### RC2 — Generate freely, then delete: the evidence guard removes a third of the text
*Impact: lost content, broken prose, empty sections.*

- The writer sees ~1.8k chars per source (RC4) and is also fed analyst notes and computed market data.
  It then writes numbers that the guard cannot match to a source excerpt.
- `_sanitize_unverified_financial_claims` (L12634) deletes the **whole sentence** or table row around
  an unmatched number. Detailed: **128 values redacted, 3 KPIs removed, 22,637 → 14,177 chars (−37%)**
  (log 14:28:57). In-depth: **131 values redacted, 4 KPIs removed, 34,742 → 26,527 chars; rendered pages 20 → 14** (log 14:34:17–14:34:23).
- Residue that reaches the PDF:
  - A callout box containing only `[S5], [S37]` (p2).
  - A numbered item containing only `[S5], [S7].` (p8).
  - A "Risk Matrix" heading whose only content is "One or more numerical statements were omitted…" (p8).
- Two repair patches were rejected ("introduced unsupported numeric tokens"). The gate removes
  content but nothing rewrites around the gap.

### RC3 — Expansion is the only growth mechanism, and it repeats rather than deepens
*Impact: repetition, out-of-order sections, still far short of target.*

- Bounded by `_TIER_EXPANSION_PASSES` (4 / 7, L11850), a 180 / 480 s budget (L11858), 35 s per
  attempt (L11863), and 4,500 / 6,000 output tokens per continuation (`report_llm.py:834`). Each pass
  added ~4k chars in ~10 s, so 7 passes can add at most ~30k chars (~5k words). In-depth: 6,286 →
  36,532 chars in ~70 s, which is only 15% of its 480 s budget. The **pass count**, not time, is what
  binds. Even before the guard, in-depth peaked at **20 rendered pages**.
- "Deepen existing" picks the *thinnest* sections, so the same sections are deepened again and again.
  Detailed deepened 2 sections in all 4 passes. The audit flagged a **critical "quadruple repetition of
  Volatility/VIX and Valuation subsections"**, and it is visible in the PDF:
  - "Volatility-Driven Timing" ×4
  - "Seasonal…" ×3
  - "Valuation…" ×3
- Missing planned sections are **appended at the end of the report** (L16892), after the conclusion,
  instead of at their planned position. In-depth: "Defensive Pivots: Pharma and FMCG", created by pass 1,
  ended up **after** "Methodology & Data Dictionary".
- The continuation sees only a coverage digest (8k chars), not the surrounding text.

### RC4 — In-depth does not get a meaningfully larger or better evidence pool
*Impact: no material for a 100-page report; detailed quality limited too.*

- **Crawler cap**: `min(..., 28)` (`site_crawler.py:263`). In-depth's 48-page budget never takes effect
  (logged `limit=28`).
- **Sector-rotation relevance cap**: `rotation_limit = 40` for both tiers (L8041). In-depth went from
  53 fetched sources to 40 kept. Before that, the first gate already cut 113 search results to 50.
  In-depth's 80-page enrichment limit and 18-results-per-query budget therefore never come into play.
- **Queries**: 5 vs 8 hard-coded queries (L9355–9368). They ask for 8 quarters when 4 were requested,
  and no queries are generated per outline section.
- **Writer context**: equal ~1.8k-char slice per source, ranked by overlap with the *question* rather
  than assigned to the *section* that needs it (L5751–5790). 81% of fetched text never reaches the
  writer (detailed).
- **Outline**: the planner sees a 500/650-char preview per source (L18645) and gets no section-count
  or subsection guidance per tier.

### RC5 — Charts are produced apart from the text that should explain them
*Impact: charts missing or meaningless.*

- Charts come from three places:
  1. The writer's JSON, with values often from memory: 8 proposed, 6 removed by evidence validation
     (detailed). In-depth: 12 proposed; the sector-rotation chart gate removed 10, and the guard
     removed 4 more of the remaining charts and points.
  2. The post-hoc evidence **gallery**, which rejected 868 of 1,422 observations for lacking an exact
     quote.
  3. Deterministic market-data charts.
- The gallery inserts `[CHART_n]` into whichever section matches its keywords best
  (`chart_gallery.py:1898`, with banking-specific boosts). Its "takeaway" is templated: min/max of the
  plotted values, or the generic default "The figure makes the key comparison… visible" (L1799). No
  prose is written about the chart.
- **Window mismatch.** The data layer fetches 8 quarters (`quarters=8`, L20933) and the queries ask
  for 8. The requested-window gate then removes every chart covering more than the 4 requested
  quarters, including the calculated **8-quarter sector-return heatmap**, the most relevant chart for
  this topic (log 14:29:22: 4 charts removed).
- Final result: **1 chart in each tier**. Detailed: "Sector Index Performance Dispersion", 2 bars,
  one month. In-depth: a 2-entity P/E lollipop. Neither is discussed by the surrounding text.
- When a chart is removed, `reconcile_orphaned_chart_narration` / `remove_stale_chart_narration`
  delete the sentences about it, so the text is cut back instead of being rewritten.

### RC6 — Retrieval quality is unscored for authority and recency; off-topic pages pass
*Impact: unreliable inputs, low citation rate.*

- **Relevance is lexical.** `_source_relevance_score` (L7534) = keyword hits ×8, +30 for an exact
  topic match, plus bonuses for `.gov`/regulator hosts. It has hard-coded aliases for past topics
  ("electric two-wheeler", "data center"). There are no embeddings and no LLM relevance judgement.
- **The sector-rotation branch admits any page matching one of five broad regex groups.**
  `macro_volatility` matches "rbi", "inflation" or "volatility", which almost any Indian finance page
  contains. Final sources include blogspot, tipsheet.markets, useflock.in, ijrpr.com, and
  `downstox.com` (looks like an Upstox look-alike, 4 sources).
- **Authority** is a boolean host allow-list, and only within topic branches. **Recency** weighting
  exists only in the banking branch (L8161–8176). Only 7 of 47 sources carried a publication date.
- **No per-source reliability score** is stored or shown, so you cannot tell how reliable the data is.
- **Fetching.** 10 of 47 pages were empty extractions (JS-rendered sites such as NSE). The main fetch
  has no browser fallback. `utils/selenium_fetch.py` and `utils/playwright_fetch.py` are imported only
  by tests. The crawler's Playwright fallback made 0 attempts.
- **Dedup** is by URL/document identity only (L2657), so syndicated near-duplicates survive.
  Boilerplate removal is a line-length/regex filter (`source_extractor.py:32`).
- Market-data gaps: the Nifty Indices historical fallback returned non-JSON for NIFTY MEDIA, PRIVATE
  BANK, HEALTHCARE and CONSUMER DURABLES (log 14:26:41–43).
- Only **21%** of the sources shown to the writer are cited, yet the appendix lists all 48, including
  rows titled "INR295.3b" and "$13 (No public URL supplied)".

### RC7 — No argument holds the report together
*Impact: "stitched together" feel.*

- There is no thesis or argument plan: the outline gives sections a `purpose`, but there is no
  report-level claim and no per-section goal linking it to the next section.
- The single writer call has no running context to lose. The cohesion problems come from what happens
  after it:
  - expansion appends sections in isolation (RC3);
  - the guard deletes sentences (RC2);
  - the gallery drops charts into sections (RC5);
  - about 45 deterministic transforms rewrite the text.
- Nothing re-reads the finished report for flow. The independent audit *detects* duplication (critical
  severity) but its repairs are rejected and the report is published anyway (status WARNING).
- Contradictions survive: "Nifty IT recorded a cumulative loss of −18.47%" sits next to "the IT sector
  emerged as a surprise leader" (detailed).
- Sections overlap: in-depth opens with "Executive Summary" followed by "Strategic Overview", both
  titled "The Great Rotation of 2025-2026", and later has two crude-oil sections.

### RC8 — Accumulated topic-specific gates make behaviour hard to reason about
*Impact: maintenance and inconsistency, not a direct page-count cause.*

- `_filter_relevant_sources` has dedicated branches for local investors, FPI, sector rotation, banking
  and promoter exits. Query building has the same pattern.
- About 45 distinct post-generation transforms, many domain-specific (banking labels, FTA tables,
  sector heatmaps, promoter ledgers). Each fixed a past run; together they remove more than they add.
- Per the instructions, the Phase 2 plan **does not add gates**. It moves verification earlier
  (evidence-first writing) so that most late gates have nothing left to remove.

## PDF rendering issues (detailed run, page images inspected)

| # | Page | Issue | Likely cause |
|---|---|---|---|
| R1 | 2, 8 | Callout / numbered item containing only citation markers (`[S5], [S37]`) | Sentence redacted by the evidence guard; markers kept |
| R2 | 8 | "Risk Matrix" heading with no content except a limitation banner | Section emptied by the guard; heading kept |
| R3 | 5–8 | Same subsection topics repeated 3–4× | Expansion re-deepening the same sections (RC3) |
| R4 | 1 | Cover summary in monospace, truncated mid-sentence | Cover style / summary clipping |
| R5 | 2–3 | Large blank areas (~25% of page) before the chart | Chart block forced to the next page |
| R6 | 3 | Only chart: 2 bars from a single month | Charts removed upstream (RC5) |
| R7 | 8–10 | Source appendix ~2.3 pages; lists all 48 sources, 38 uncited; junk titles | Appendix not limited to cited sources; no quality filter |
| R8 | 11 | Last page is mostly disclaimer | Trailing furniture page |

In-depth PDF (15 pages) shows the same R1–R8 issues, plus:

| # | Page | Issue | Likely cause |
|---|---|---|---|
| R9 | 2–3 | Two consecutive summary sections ("Executive Summary: The Great Rotation…" and "Strategic Overview: The Great Rotation…") | Planner + expansion produce overlapping sections; no editorial pass |
| R10 | 4, 5, 10 | Sentence fragments starting in lower case ("despite moderate cumulative returns…", "despite FII exits [S20], [S44] \|"), "Sharpe ratio of 0." | Guard redacts part of a sentence/number and keeps the rest |
| R11 | 10–11 | "Defensive Pivots: Pharma and FMCG" placed **after** "Methodology & Data Dictionary" | Missing planned section appended at the end (L16892) |
| R12 | 6, 9 | Duplicate topics ("Crude Oil and Geopolitical Overlays" / "…Risk Premiums") | Expansion deepening + no cross-section memory |
| R13 | 12 | "Comparative Defensive Metrics" heading with only the limitation banner | Section emptied by the guard |
| R14 | 9 | Only chart is a 2-entity P/E lollipop | 12 writer charts → 1 (RC5) |
| R15 | 9 | "Methodology & Data Dictionary" is a 3-bullet stub | Methodology is just another LLM section, not generated from the run's actual provenance |

## Is 100+ pages realistic for this topic?

**Not with the current retrieval; plausibly yes with a working per-sector data layer.**

- **Page density.** The rendered layout carries ~350–430 words per text page. Honest 100+ pages need
  roughly **22–28k words of distinct analysis**, plus ~20–30 charts/tables with interpretation, plus
  real appendices (source-quality table, sector × quarter data tables, methodology). The appendices can
  add ~15–20 pages without padding, because they hold the run's own data.
- **This run's evidence.** In-depth fetched 666k chars (~100k words) from 55 sources, but only 21 were
  cited, 10 pages failed or were empty, many sources are low-authority blogs, and much of the text is
  navigation, repetition or off-window. A realistic synthesis ratio of 0.3–0.5 words of analysis per
  word of *distinct, relevant* source text gives roughly **10–20k words**. That is **~50–70 pages**
  including appendices, which is the honest ceiling for this run.
- **What 100+ needs for this topic.** The analysis should be organised by sector: ~12–15 NSE sectoral
  indices × 4 quarters, each with returns, FPI/DII flows (NSDL fortnightly sector tables), valuation
  (P/E, P/B vs history), earnings growth and drivers. That gives ~3–5 pages per sector = 45–70 pages,
  plus macro/flows/methodology/appendices.
  - Evidence volume needed: **~100–150 relevant fetched pages** or the equivalent structured data,
    i.e. complete quarterly series for all sector indices and the matching NSDL/AMFI tables.
  - This run had 55 sources, and the sector series **failed** for NIFTY MEDIA, PRIVATE BANK, HEALTHCARE
    and CONSUMER DURABLES. The NSE JS pages produced empty extractions.
- **Commitment.** The Phase 2 outline stage will size the report from the evidence actually available
  and state the achievable page band. If in-depth cannot reach 100 pages without repetition, the report
  says so and lists the missing data, rather than padding.

## Proposed Phase 2 plan (for approval)

The plan ships as small commits, with new modules called from `report.py`. Each step is measured with
`scripts/eval_report.py`, which I build **first** and run offline against the two saved captures (no
API credits).

| Step | Change | Fixes | Size |
|---|---|---|---|
| 0 | `scripts/eval_report.py` (pages, words, sources, % evidence cited, % charts explained, unsupported claims, dangling refs, render issues) with offline replay from captures | measurement | small |
| 1 | **Evidence store** `utils/evidence_store.py`: chunk fetched pages into ~1k-char passages with id, URL, publisher, date, authority tier, recency and relevance score. Calculated market datasets become chunks with provenance. | RC2, RC4, RC6 | medium |
| 2 | **Retrieval fixes** inside the existing flow: crawler cap from tier budget (not 28); `rotation_limit` scaled by tier; requested-window quarters instead of hard-coded 8 (queries + `fetch_sector_quarterly_returns`); browser fallback for `empty_extraction` pages via the existing `playwright_fetch`; near-duplicate dedup; recency/authority weights. Sector-rotation fixes (NSE tickers, niftyindices session, source gate, crawler denylist, retries) stay intact. | RC4, RC5, RC6 | medium |
| 3 | **Outline stage** `utils/report_outline.py`: thesis + sections + subsections with goals and evidence queries. Per-tier budget computed from the evidence actually available (detailed ≈ 8 sections / ~8–10k words; in-depth ≈ 14–18 sections × 3–5 subsections / up to ~30k words), capped and reported honestly when evidence is thin. | RC1, RC7 | medium |
| 4 | **Section-by-section writer** `utils/section_writer.py`: each section gets its own retrieved chunks plus a running summary of earlier sections, writes to a word budget, and may cite only chunk ids. Numbers come only from chunks/datasets. Chart specs are planned with the section from the same chunks, and the prose must reference and interpret each figure ("Figure N shows…"). | RC1, RC2, RC5, RC7 | **large — needs your OK** |
| 5 | **Editorial pass** over the assembled report: transitions, repetition and contradictions, working from section summaries plus flagged spans. Bounded, and it may not add new numbers. | RC3, RC7 | medium |
| 6 | **Make the guard rewrite, not delete**: an unsupported sentence is regenerated from its section's chunks or removed together with its citation markers; empty headings are dropped; a dropped chart triggers a rewrite of its paragraph. Expansion is retired for the new path. | RC2, RC3, R1–R3 | small–medium |
| 7 | **Page policy**: replace the "never target a page count" contract with section/word budgets; raise the `requested_report_page_count` limit; update the 4 test files listed in RC1, recording each changed assertion; revisit `test_tier_expansion_uses_plain_continuation_contract`; re-add the "Report" label cleanup for all tiers. | RC1 | small |
| 8 | **PDF fixes** R1–R8, plus a **source-quality appendix** (cited sources only, with publisher, date, authority tier and reliability note) and data-table appendices for in-depth. | R1–R8, RC6 | medium |

**Rollout:** steps 3–6 sit behind a flag (e.g. `GG_SECTION_PIPELINE=1`) so the current path keeps
working until the new path beats it on the eval for both tiers. Then the flag becomes the default.

**Expected cost and time:** in-depth goes from ~15 LLM calls to roughly 30–45 (outline + one per
section, some in parallel, + editorial). Expect ~8–15 min per in-depth report; detailed stays ~3–5 min.
