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
