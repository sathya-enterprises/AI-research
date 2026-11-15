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

Scorecards in `results/`: `before-today` (code at e6248af) and `today-fixes-9` (the 9 prompts captured
on 15 Nov, replayed after the fixes of 15-16 Nov).
