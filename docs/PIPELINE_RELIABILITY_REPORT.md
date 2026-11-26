# Report pipeline reliability: final report

Branch `backend-edit` (pushed from local `story-first`), commits `bdfd139` → `a796b97`, 26 Nov 2026.
All numbers below were measured: on saved runs (offline replays of `backend/diagnostics/research_capture_*.json`)
or on two controlled live Deep Research runs. Nothing is estimated unless it says so.

## 1. Architecture audit (what was there)

```
routes/report.py   search → fetch → enrichment → official datasets → evidence store            (~760-775 s live)
utils/report_pipeline_v2.py
  outline → official data / chart candidates → section waves (gateway, 5 parallel calls)
  → claim checks per wave → executive summary → editorial bridges → assembly → chart lint
routes/report.py   final evidence gates → publication decision → PDF (routes/pdf.py, Chromium)
utils/llm_gateway.py   Gemini key pool (16 keys) → lite models → Groq fallback
```

Findings that shaped the work:

| Area | Finding |
|---|---|
| Gemini quota | Keys were rotated as if each were its own allowance. Google counts quota per **project and model**, so same-project keys add nothing. 429/503 were retried on fixed sleeps without `Retry-After` or jitter. |
| Fallbacks | A short Groq answer was accepted as a section. In the smartphones run (25 Nov), 12 of 42 calls were answered by Groq, and the sections it wrote came out at 30–35% of their budget. |
| Claim check | One judge call per section for every sentence that didn't overlap its source enough. No per-claim record or status, and no arithmetic check. |
| Length | No record of planned vs written words or of which model wrote what. Sections averaged 55–65% of plan in 25 saved runs. |
| Charts | Titles were rebuilt from metric + entities + period and lost the subject ("Market Share (%), 2021 to 2023"). Junk tables were charted (page numbers, regression output, page sentences), and duplicates shipped. |
| Disagreements | Detected, but listed only in the methodology, which is not in the client report. GW/tonnes were never compared. |
| Observability | Only the total elapsed time. No per-stage timing or 429/503 counts per run. |

## 2. Implementation (by phase)

| Phase | What changed | Commit |
|---|---|---|
| 2 Gemini quota | `utils/llm_quota.py`: keys → projects (`GG_GEMINI_KEY_PROJECTS`); RPM/TPM/RPD per project and model (`GG_GEMINI_QUOTAS`); token-aware scheduling; a 429 cools the whole project/model pool for Google's `Retry-After`/`RetryInfo` delay (daily quota → hours) with jitter; 503 honours `Retry-After`; a fallback answer under half its requested length is held back while Gemini is retried; telemetry for HTTP status, model, seconds, quota waits and fallbacks. | `bdfd139` |
| 3 Claim verification | A record per claim (type, sources, method, status, confidence, explanation, correction). Statuses VERIFIED / PARTIALLY_VERIFIED / UNVERIFIED / CONTRADICTED / REQUIRES_REVIEW. Rounding-aware arithmetic check that corrects a wrong % change. Figures verify a claim only together with its words and years in one stretch of the source. Forecasts and explanations always go to the judge. A year the sources never name is not accepted (publication dates count, and the judge sees them). Judge failure removes nothing. | `7d46f5d` |
| 4 Length | Per section: budget, models, extension, short flag. `meta.length` gives the verdict (within / below band because of evidence / below band because of writing). Sections are written in calls of ≤ 1,400 words (`GG_V2_SECTION_CALL_WORDS`). | `968b188`, `95b5c3c` |
| 5 Charts | The source table caption restores the subject and measure. Metadata, regression and correlation output, page sentences, cut cells, Remarks columns and unnamed "Value" measures are rejected. A chart whose points are all in another chart is dropped. Heatmaps are titled as returns only for market indices. "(PDF page N)" is removed from titles. | `f29b03b`, `ee3417d`, `bee0a73`, `a796b97` |
| 6 Sources | In-section "Sources differ" note with both figures and citations, under a strict rule (same subjects, different sources, 1–33% gap). GW/MW, GWh/TWh and tonnes are compared within their family. data.gov.in connector: env key, configured resources, paging, cache, failure reasons, and the key masked in the httpx logs. Source quality scoring already existed (`utils/source_quality.py`) and was kept. | `7281026`, `41932b8` |
| 7 PDF | Tables of 5+ rows break between rows instead of jumping whole to the next page (that left a heading alone on a page). | `fb97a36` |
| 8 Observability | `meta.runSummary` plus one `V2 run summary` log line: seconds per stage (including the research phase), calls, tokens, models, 429/503, retries, quota waits, fallbacks, sources, claims by status, charts, sections, pages, length verdict. | `37f820f` |
| 9 Docs | `docs/CONFIGURATION.md`, `docs/DEPLOYMENT.md`, `backend/.env.example`, `render.yaml`. | `b5fecb1` |

No frontend changes, no new Supabase tables, no new Python packages. API contracts are unchanged.

## 3. Performance comparison (measured)

### Live Deep Research runs

Before = the last three live runs before this work. After = the two controlled runs on this branch. The topics
differ, so this is evidence, not an A/B test of one prompt.

| | Renewables (before) | Aviation (before) | Smartphones (before) | Coal (after) | Pharma (after) |
|---|---|---|---|---|---|
| End-to-end seconds | 2,580 | 2,492 | 2,894 | **2,357** | **2,352** |
| Writing pipeline seconds | – | 1,527 | 2,026 | **1,316** | **1,322** |
| LLM calls / failed | – | 51 / 5 | 42 / 11 | **45 / 0** | **45 / 0** |
| Calls answered by Groq | – | 6 | 12 | **0** | **0** |
| HTTP 429 / 503 seen | – | – | – | 0 / 7 | 0 / 5 |
| PDF pages | 52 | 83 | 49 | 68 | 54 |
| Blank / near-empty pages | 0 / 0 | 0 / 1 | 0 / 0 | 0 / 0 | 0 / 0 |
| Charts shipped | 6 | 33 | 23 | 38 | 24 |

Where the time goes in the two new runs (from `runSummary`):

| Stage | Coal (s) | Pharma (s) |
|---|---|---|
| Research and evidence | 774 | 759 |
| Section writing | 967 | 948 |
| Claim-check tail | 158 | 182 |
| Outline | 82 | 82 |
| Executive summary | 30 | 34 |
| Editorial | 29 | 32 |
| Assembly and lint | 34 | 32 |

### Section length

| | Planned words written | Short sections (< 60% of plan) |
|---|---|---|
| Coal: Gemini only, one call per 2,520-word chapter | 51% | 10 of 13 |
| Pharma: calls of ≤ 1,400 words | 67% | 3 of 12 |

### Claim verification

**Offline**, 8 saved reports with 2,559 cited sentences:

- Sentences sent to the judge: 1,033 before, 1,050 after. The year check sends more to the judge; the old code
  auto-accepted 34 sentences whose year the source never named.
- 30 sentences are now verified by figures. A spot-check of the sample found each one supported by its source.

**Live:**

| | Coal | Pharma |
|---|---|---|
| Claims checked | 516 | 429 |
| Verified by code | 316 | 268 |
| Judged | 200 | 161 |
| VERIFIED | 470 | 388 |
| PARTIALLY_VERIFIED | 29 | 31 |
| UNVERIFIED (removed) | 16 | 10 |
| REQUIRES_REVIEW | 1 | 0 |

### Charts (offline, 44 saved reports)

- Verified chart candidates went from 443 to 424, and placed charts from 326 to 312.
- Every removed chart is a rejected class: page numbers, row counts, regression output, page sentences, unnamed
  "Value" measures, date-only columns, contained duplicates.
- Re-linting the 180 shipped charts of 22 saved runs changed 22 titles, dropped 7 junk charts and dropped 4
  contained duplicates. Example: "Market Share (%), 2021 to 2024" became "Market Share (%): Xiaomi in India,
  2021 to 2024".

### Source disagreements

- The loose detector found 17 pairs in 42 saved reports; none passes the strict rule. Sampled, they were different
  measures (UK vs all-FTA exports, a sector vs the market total, Nifty 50 vs Nifty 500). So no wrong note reaches
  readers.
- Neither live run produced a strict disagreement, so **no in-section note has yet appeared in a live report**.
  The 116 GW vs 118 GW case is covered by tests only.

## 4. Testing

- Full suite: `cd backend && python -m pytest -q -p no:cacheprovider routes utils test_*.py rag-service/test_pgvector_store.py`
  gives **1,683 passed, 1 skipped, 15 xfailed** (1,653 passed after Phase 2; every phase was run against the full suite).
- New test files:
  - `test_llm_quota.py` (7)
  - `test_claim_records.py` (7)
  - `test_length_report.py` (4)
  - `test_chart_titles_subject.py` (8)
  - `test_disagreement_notes.py` (3)
  - `test_datagov_in.py` (5, mocked HTTP)
  - `test_run_summary.py` (3)
  - an end-to-end assertion added to `test_report_pipeline_v2.py`
- No unit test calls a live API.
- Offline replays after each change: chart funnel (`scripts/chart_funnel_replay.py`), title re-lint, claim
  benchmark, PDF re-render of 4 saved reports (blank and sparse pages, horizontal overflow).
- Live: 2 controlled Deep Research runs, both published, with PDFs checked.
- A test found that httpx logs request URLs, which would have logged the data.gov.in key. Key-like query
  parameters are now masked.

## 5. Remaining issues and what is needed from you

1. **Gemini projects and quotas (action needed).** All 16 keys are treated as one shared pool until you set
   `GG_GEMINI_KEY_PROJECTS` and `GG_GEMINI_QUOTAS`.
   - Needed: which Google Cloud project each key belongs to, and each project's RPM/TPM/RPD from the console.
   - Real extra capacity needs **separate projects or a paid tier**. More keys in one project add nothing.
   - The free tier (about 20 requests per project per model per day, per the code notes) cannot sustain several
     Deep Research reports a day, at ~45 calls each.
2. **data.gov.in (action needed).** It needs a registered API key and a list of resource ids
   (`GG_DATAGOV_RESOURCES`). Its documentation refused automated access, so the client relies only on the
   standard `/resource/{id}` endpoint and is **not yet verified against the live API**. One report with the key
   set will confirm it (see `docs/CONFIGURATION.md` section 6).
3. **Section length is still below plan** (67% in the pharma run). Smaller calls helped. Closing the rest needs
   either a second extension pass (more calls and time) or planning budgets nearer to what the evidence fills.
   Left as a decision for you.
4. **The page estimate overshoots.**
   - Measured: 104 estimated vs 68 actual (coal), 84 vs 54 (pharma), 70 vs 49 (smartphones).
   - The verdict in `meta.length` uses the estimate.
   - Calibrating `estimate_pages` affects outline budgets, so it was not changed blind.
5. **Some chart titles are still weak** in live output, for example:
   - "Initial observation: 5 product categories";
   - "Country Pre-Covid Post- start of Covid: 9 items";
   - "U s a, U k" casing;
   - the same company measure from two sources shipped twice with different values.
6. **Claim-check tail:** 158–182 s per Deep Research run is spent waiting for the last judge calls.
7. **Not merged.** Everything is on `backend-edit`; a pull request into `main` is the next step after your review.
