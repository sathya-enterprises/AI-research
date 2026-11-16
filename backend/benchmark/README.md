# Research engine benchmark

A fixed set of questions (`prompts.json`) across sectors, countries, time windows, report tiers and an
uploaded workbook. It exists so a fix is judged on **every** prompt, not only on the one that exposed
the bug.

1. **Run** each prompt live once (backend running with `GG_RESEARCH_CAPTURE=1`):
   `python scripts/benchmark_run.py` (only prompts without a capture; `--ids a,b` to re-run).
   Captures stay in `backend/diagnostics/` (not committed); `captures.json` maps prompt ids to them.
2. **Score** with the current code (no LLM, search or fetch calls; a few minutes for all prompts):
   `python scripts/benchmark_score.py --label <name> --compare benchmark/results/<previous>.json`

The scorecard has two kinds of numbers:
- **live**: outcome, charts published, words written vs planned, as the run produced them;
- **replay**: the deterministic checks re-run on the saved evidence with the current code:
  numbers removed by the number policy, unsupported claims after the fact-sheet check, chart-gate
  failures on the published charts, and chart candidates that pass pre-writing verification.

Replay counts are store-wide (each draft is checked against the whole evidence store), so they are not
identical to the live run's per-section counts; compare scorecards with each other, not with live logs.

A change is acceptable when the totals improve and the "prompts worse on any metric" line is empty,
or every listed prompt has been looked at and explained.

Scorecards in `results/`: `before-today` (code at e6248af) and `after-fixes` (the same captures replayed
with the fixes of 15-16 Nov). Explained "worse" entries in `after-fixes`: ev_india_in_depth (charts the old
code's 100-failure batch cap hid, so its earlier "verified" count was false) and internet_use_comparison (a
dataset-documentation table, "No. Countries: Basic cross-section ...", no longer passes).

Each capture is replayed from a clean report state (`replay_capture.reset_run_state`); without it, chart
evidence registered for one capture's S1 leaked into the next capture scored by the same process.

## Baseline (30 prompts, 16 Nov) and what it showed

`results/baseline-30.md` scores all 30 prompts; `results/reasons-baseline-30.md` ranks the causes across them
(`python scripts/benchmark_reasons.py`). The largest general causes:

- numbers removed (325): "in the evidence for another metric or entity" 130 (25 prompts); "requested
  calculation uses numbers that are not facts" 92 (23 prompts); "not in the evidence" 52 (16 prompts);
- chart candidates rejected (133): "period outside the requested window" 79 (19 prompts; many are correct
  rejections of out-of-window or undated tables); "quoted line not present" 21 (13 prompts).

First fix chosen from the ranking (`results/unit-calcs.md`): 92 of 106 refused calculation requests had
facts for every operand and failed only on units ("160.9% - 124.4%", "USD 100 billion - USD 12 billion",
"29.90 - (-0.73)"). Numbers removed 325 -> 273 across the 30 prompts, no prompt worse.
