# Stage I — client-readiness status

Read this file instead of the chat history. Branch `backend-edit`; integration branch `integrate/backend-ui`
(updated at the end of Stage I). Tests: **1197 passed, 15 xfailed** (baseline 1183 / 15; no new failures).

## How to verify offline

```
cd backend
# replay V2 offline (run dir holds capture.json + status_final.json)
python scripts/eval_report.py diagnostics/eval_runs/i_replay/h_c --replay-v2
# replay every publication gate + self-healing over all saved captures
python scripts/eval_report.py --replay-gates
# checklist (every Stage I finding PASS/FAIL + chart and table inventory) for one report + its PDF
python scripts/stage_i_checklist.py --result <status_final.json | v2_replay_result.json> --pdf <report.pdf> --capture <capture.json>
```

The in-job rendered check (`utils/stage_i_gates.rendered_check`) runs inside the report job for V2 runs
(`GG_STAGE_I_RENDERED_CHECK=1`, default; the test conftest sets 0).

## Fixes

| # | Fix | Status | Commits | Evidence / notes |
|---|-----|--------|---------|------------------|
| 1 | Leaked writer instructions | DONE | 2bb7731, c91db98, 33b8c63 | `utils/leak_gate.py` (8-word runs vs prompt templates + instruction markers); methodology from templates; leak check also on the rendered PDF text in the job. Stage H in-depth: FAIL → replay PASS. |
| 2 | Stale / out-of-window data, period labels | DONE | 849f556, 33b8c63, 99e432f | `utils/period_hygiene.py`; layout tables dated and in-window; appendix keeps out-of-window tables only in a labelled, capped (2) "Historical reference" block; period gate drops any other out-of-window table ("Sectoral Indices performance in April, 2024" removed); fiscal labels in chart titles, heatmap columns and prose. |
| 3 | Flattened tables and text diagrams | DONE | 121d77b, 6f4a0b4 | `utils/flow_text.py`: arrow chains → sentences, box art cleaned, side-by-side diagrams dropped, flattened "--- — ---" tables dropped; headerless pipe tables dropped (normalizer); rendered gate in the job. |
| 4 | Thesis from data (regime + dispersion) | DONE | c91db98, 9dc6e61 | `utils/regime.py`: configurable groups (`REGIME_GROUPS_JSON`), per-quarter / compounded group returns, leaders/laggards, half-window leadership, **dispersion test** (within-group range vs between-group gap); facts given to outline + exec summary; thesis rewritten when contradicted or when it omits a sector-specific finding; exec summary opens with the computed statement; rotation claims checked; mislabels fixed. Stage H data: ranges 44.93 / 45.63 pp vs 8.79 pp gap → "sector-specific". Group summary is part of the dataset source (auditable). |
| 5 | Self-description from the final structure | DONE | 2bb7731 | `utils/boilerplate.py`; how-to-read counts from written sections; no [S#] sentence when markers are hidden; no Sources line in navigation sections. |
| 6 | Repetition | DONE | a2b6f6e, 19ecea9, 99e432f | `utils/repetition.py`: data-derived chart takeaways (no CONTEXT/"Supports:" box), duplicate tables removed, fact-keyed headline-stat cap (≤2 in body prose, +1 in the exec summary) that never deletes a sentence or an entity mention — repeats become "the figure given in Section N / above"; every action logged in the publication report. Stage H in-depth replay: max repeats 5 → 2, over cap 11 → 0, duplicate tables 8 → 0, figure boxes echoing the body 28 → 0; no section lost an entity or fell below 90 % of its words. |
| 7 | Data sanity (FS QTD) | DONE | 1d926e6, 9dc6e61 | +17.97 % was wrong (alias ticker's monthly bar vs the official quarter-end close; the live level gave ≈ +10.4 %, in line with Bank +7.61 %). QTD now = same series' latest daily close with its date; `utils/data_sanity.py` blocks constituent divergence, live-level mismatch and out-of-range values (logged); replays rebuild the dataset with the checks. |
| 8 | Chart / table titles, labels, types, lint, inventory | DONE | 5e2fbd2, 99e432f | `utils/chart_lint.py`: quality gate (equal values, Total+components, example tables, duplicates), auto-fix (title form, counts, benchmark named, units, axes, casing, sorted horizontal bars for rankings, fiscal labels, precision via `decimals`, dated source line), lint, unique titles, inventory; tables numbered separately with captions and sources (or dropped); chart-type rationale removed; inventory in eval output and the publication report. Stage H in-depth: 28/28 charts failing lint → 0/21. |
| 9 | Smaller items | DONE | 6f4a0b4, 99e432f | a/an, ordinal gaps, empty or backward-looking "What to watch" boxes, raw bullets, LaTeX symbols, sentences broken before a comma, calculated datasets not "primary official 1.00", source names separated, dates never truncated, low-reliability headline claims qualified. |
| 10 | Rendered-PDF gates in the job | DONE | 33b8c63 | `rendered_check` renders the final payload inside the job, runs routes.pdf's audit + leaks / text art / periods / split words / label collisions (PyMuPDF), heals and re-renders up to 3 rounds; remaining blocking findings keep the report a draft (`failedStage: rendered_pdf`). |

## Known limitations

* The offline V2 replay is not fully deterministic (number-policy removals vary 83–94 between runs of the same capture); it is not caused by Stage I code.
* Report.py's evidence check removes some deterministic QTD interpretation sentences ("Nifty Bank returned +7.61% against +8.39% for the Nifty 50") in the replay; pre-existing, unchanged by Stage I.
* In replays the FS QTD is withheld (the captured value fails the sanity check); a live run computes it from the daily close.
* Statistic cap: a number is matched to the entity named just before it, so "Telecom and IT saw 11,065 crore and 10,915 crore" (respectively-style) can be attributed to the wrong sector; f1b keeps one fact at 3 mentions in the body.
* Short section-end pages remain (sections start on a new page: layout, unchanged by the no-design-change rule); header-only pages are trimmed in the job as at export.
* g_c (Stage G) cannot be replayed: the live run itself failed at the outline call (ConnectError).

## Offline verification (section 5)

Stage H reports, original vs offline replay with Stage I (checklist: 26 checks):

| Report | Before (live Stage H PDF) | After (replay + in-job rendered check) |
|---|---|---|
| In-depth, client prompt 1 | 10 PASS, 16 FAIL | 26 PASS, 0 FAIL (116 pages, 1 healing round) |
| Detailed, client prompt 1 | 9 PASS, 16 FAIL | 26 PASS, 0 FAIL (26 pages) |

All saved captures (--replay-v2 + in-job rendered check + checklist):

| Run | Replay | In-job rendered check | Checklist | Failing checks |
|---|---|---|---|---|
| live_20261107_c1b_in_depth | missing calls 5 | pages 105 rounds 0 blocking 0 warnings 0 | 26 PASS, 0 FAIL |  |
| live_20261108_d_in_depth | missing calls 6 | pages 90 rounds 0 blocking 0 warnings 0 | 26 PASS, 0 FAIL |  |
| live_20261108_e_detailed | missing calls 3 | pages 24 rounds 0 blocking 0 warnings 0 | 26 PASS, 0 FAIL |  |
| live_20261108_e2_in_depth | missing calls 0 | pages 107 rounds 0 blocking 0 warnings 0 | 26 PASS, 0 FAIL |  |
| live_20261108_f1_in_depth | missing calls 0 | pages 126 rounds 1 blocking 0 warnings 0 | 26 PASS, 0 FAIL |  |
| live_20261108_f1b_in_depth | missing calls 0 | pages 103 rounds 0 blocking 0 warnings 0 | 25 PASS, 1 FAIL | Max mentions per statistic in body prose <= 2 |
| live_20261108_f3_detailed | missing calls 3 | pages 16 rounds 0 blocking 0 warnings 0 | 26 PASS, 0 FAIL |  |
| live_20261108_f3b_detailed | missing calls 2 | pages 20 rounds 0 blocking 0 warnings 0 | 26 PASS, 0 FAIL |  |
| live_20261108_g_a_detailed | missing calls 0 | pages 26 rounds 0 blocking 0 warnings 0 | 26 PASS, 0 FAIL |  |
| live_20261108_g_b_detailed | missing calls 2 | pages 21 rounds 0 blocking 0 warnings 0 | 26 PASS, 0 FAIL |  |
| live_20261108_g_c_in_depth | replay failed | ls\report_pipeline_v2.py", line 436, in run_pipeline_v2
    raise PipelineV2Error(str(exc)) from exc
utils.report_pipeline_v2.PipelineV2Error: outline call failed: ConnectError model=gemini-3.5-flash
 |  |  |
| live_20261108_g_c2_in_depth | missing calls 0 | pages 126 rounds 0 blocking 0 warnings 0 | 26 PASS, 0 FAIL |  |
| live_20261108_h_c_in_depth | missing calls 0 | pages 115 rounds 1 blocking 0 warnings 0 | 26 PASS, 0 FAIL |  |

--replay-gates over all 21 captures: every one would publish, 0 hard findings after healing.

---

# Stage J — multi-domain live testing and generalisation (10 Nov 2026)

Goal: check that the engine works beyond the sector-rotation prompt, and fix what does not generalise.

## Live runs (detailed tier)

| Run | Prompt | Outcome | Checklist | Charts | What it showed |
|---|---|---|---|---|---|
| j_ev | India EV market, past 3 years | published, 19 pp | 26/26 | 0 | Only stock-market questions had a data layer; incidental tables were junk ("Founding Year", "1., 2., 3.", forecasts tagged "QTD") |
| j_ev2 – j_ev5 | same | published | — | 0 | Each run exposed one more blocker (below) |
| j_ev6 | same | published, 18 pp | — | 2 | First verified charts; they came from a teaser table whose rows the source marks "Forecast" → quality rule added, these charts are now dropped |
| j_semi | Semiconductor supply-chain shifts since 2022 | published, 23 pp | 26/26 | 0 | "since 2022" was read as "only 2022"; evidence has single figures, not series |

## Root causes found and fixed

| # | Problem (domain-wide) | Fix | Commit |
|---|---|---|---|
| 1 | No structured data outside the stock-market path | `utils/dataset_extractor.py`: model proposes series from relevant, data-dense, in-window page passages (BM25 vs question + outline); code accepts only when every value and label is in the passage AND the value→label mapping is checkable (contiguous run aligned to a printed period run, or label words beside the value); unstated units become "as reported"; clipped to the verifier's own window | c7fe088, 98aa36d, 530f743, e9aba89, 898034e |
| 2 | The chart verifier ignored 32 of 36 fetched pages (origin "publisher_page_and_search_extract") | such a source is verified against its fetched page text only (never the mixed excerpt) | 98aa36d |
| 3 | `niti.gov.in` ≠ `www.niti.gov.in` split a source from its page | URL matching ignores "www." (live and replay) | 98aa36d |
| 4 | Extracted charts quoting prose / PDF chart text were never registered as chart evidence | their exact quotes are registered when they come from page-derived passages (never search snippets) | 898034e |
| 5 | "past N years" accepted only N−1 whole years; "since 2022" meant only 2022 | windows in whole calendar years (like quarters); "since/from YEAR" = Jan YEAR – end of current year | 898034e, a4b2c49 |
| 6 | Retrieval never asked for data | `utils/data_angles.py`: one (detailed) / two (in-depth) data-seeking search angles for year-wise statistics inside the window, skipped when an angle already asks for data | e9aba89 |
| 7 | Misleading data | quality gate drops template/teaser tables ("$#%") and values the source labels as forecasts (unless the chart says forecast); future periods never "QTD"; no scientific notation; year-valued attributes and list-number labels dropped before writing | c7fe088, 6a00835 |

Regression: the Stage H in-depth and detailed replays still pass 26/26. Tests: 1201 passed, 15 xfailed.

## Verdict on the architecture

The pipeline stages (retrieval → evidence store → outline → data-first charts → grounded writing → number policy → gates → in-job rendered check) generalise: every new-domain report published and passed all 26 checks. What does **not** generalise yet is the **data layer**: charts appear only when the fetched pages contain clean, in-window series. The extractor now finds and verifies such series wherever they exist, and refuses to chart anything it cannot verify (this is why the EV and semiconductor reports have no charts: their evidence held single figures, older series, or forecast teaser tables).

## Recommended next stage (not started)

Structured-data connectors for authoritative open datasets, used like `market_data` is for NSE indices: World Bank / IMF / OECD indicators, UN Comtrade trade flows, US FRED and Census, India data.gov.in / RBI DBIE / MOSPI, and sector sources with public tables (e.g. VAHAN registrations). The outline names the core metrics; a connector fetches the series for the window; the series enters the evidence store as a calculated/official dataset and flows through the existing data-first charts, sanity checks and verification.

---

# Stage K — a research engine for every topic (10 Nov 2026)

Goal (user): one pipeline that does everything correctly from data to report for ANY topic - proper charts, no
duplication or repetition, a cohesive report.

## What was built

| Area | Change | Module / commit |
|---|---|---|
| Data layer | Official open-data connectors (World Bank WDI, IMF WEO, Our World in Data; free, keyless APIs). One LLM call plans up to 3 (detailed) / 5 (in-depth) datasets from a curated catalog of 72 indicators (economy, trade, energy, EVs, health, technology, climate, agriculture...); code validates every id and country; keyword fallback. Each dataset becomes an ordinary cited source (publisher, public dataset URL, year-by-year table retrieved from the publisher's API) and a data-first chart. Large values scaled to billions/millions; one retry on API timeouts | `utils/official_data.py` - ef6aca1, 1958aef, dc5be45 |
| Charts | Official charts always placed (assignment fallback); official tables not re-charted per column; absolute series on very different scales keep the question's entities; titles name the entity and keep the dataset's own unit; human source line kept in `sourceLine` (source ids normalisation no longer wipes it) | 0ce692d, 1958aef |
| Cohesion | Chart interpretations vary their phrasing; "Figure N shows <its own title>" sentences removed or folded into the next sentence as "(Figure N)"; writer prompt is domain-neutral and asks for the data's pattern; near-duplicate-sentence metric + checklist row | 8d9f146, 3697aa9, 5aaeeba, f8d1d10 |
| Planning | Outline prompt and outline repair are domain-aware (market topics keep sector/flows/valuation language; any other topic gets neutral entities, cross-cutting themes and deep-dive subsections) | ffba731 |
| Small fixes | Dates in column headers are not numeric observations (rendered audit); ordinal sequences judged per paragraph with an implicit first item allowed | 0ce692d, d56e4f7 |

## Live verification (detailed tier, final code unless noted)

| Topic | Official data | Charts | Outcome | Checklist (27 checks) |
|---|---|---|---|---|
| India EV market, past 3 years (k3_ev) | OWID EV sales, share, stock | 3 | published, 24 pp | 27/27 |
| Semiconductor supply-chain shifts since 2022 (k4_semi) | World Bank high-tech exports (US$ bn, % of manuf. exports), FDI | 3 | published, 16 pp | 27/27 (after the ordinal fix) |
| Global childhood vaccination since 2015 (k3_vacc) | OWID measles coverage (India, Nigeria, US, World) | 1 | published, 11 pp | 27/27 |
| Client prompt 1, sector rotation (k2_sector) | none needed (NSE dataset) | 5 | published, 28 pp | 27/27 |

Offline regression: Stage H in-depth and detailed replays pass (in-depth near-duplicates 10 -> 4).
Tests: 1204 passed, 15 xfailed.

## Limits

* The catalog is curated (72 indicators); a topic with no matching indicator gets charts only from verified page data.
* Country-level open data is annual; quarterly or company-level series still come from web pages (verified extractor).
* Very new data (the current year) is often not yet in World Bank / OWID; IMF values for the current and future years are projections and are excluded.

## Stage K, part 2 — depth and correctness for any topic (in-depth)

| Finding (live in-depth vaccination run) | Fix | Commit |
|---|---|---|
| In-depth reports outside finance had no entities (the finder only knew NSE sector names) → no deep-dives, no per-entity searches, 4 of 12 sections, 18 pages | Text-based entity discovery for non-market topics: countries/regions (with aliases such as U.S./US), recurring named entities from >= 3 sources; publishers never deep-dives; the topic and its own scope excluded; places first when the question asks about countries/regions; at most 8 deep-dives; neutral per-entity search queries with a year window | ecd93cb |
| Outline prompt, outline repair and the writer prompt assumed a stock-market report | Domain-aware planning (market topics keep sector/flows/valuation language) | ffba731, f8d1d10 |
| The statistic cap merged two facts sharing a number (measles 86% / DTP3 86%) and replaced one with a reference to the other | Entities may contain digits; a one-sided entity must appear in the other context | 8dfe831 |
| Every Gemini key/model returned 429 → V2 could not run, labelled fallback draft | The gateway waits out a rate limit (20 s, 40 s) before failing | 66c4981 |

Entity discovery on saved captures (no LLM): semiconductors → United States, China, European Union, Taiwan, CHIPS Act;
vaccination → WHO regions + United States, India, Nigeria, DTP3; EV → DRIVE Scheme, PLI, CMVR, China, US, Uttar Pradesh;
sector rotation → unchanged (Bank, IT, Metal, ...).

**Pending:** the confirming in-depth vaccination run (k2_vacc_deep) ran into exhausted Gemini quota and a degraded
network (Exa timeouts, research-library DB disconnects, many 403s) after ~16 live runs that day; it was correctly labelled
a fallback draft. Re-run it once the quota resets:
`python <scratchpad>/tools/run_live.py diagnostics/eval_runs/live_<date>_vacc_deep in_depth "How have global childhood vaccination rates changed since 2015, and what explains the differences between regions?"`

Tests: 1205 passed, 15 xfailed.
