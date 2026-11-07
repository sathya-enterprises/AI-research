# Uploaded workbook chart reliability fixes

## Root causes addressed

1. **The workbook chart set was hard-coded to three horizons.** The deterministic PMS analytics only generated 1-year, 3-year and 5-year rankings, silently ignoring other return periods present in the uploaded workbook. It now creates a ranking for each return period with at least two numeric observations.
2. **Generic report prompts could fail to activate workbook analytics.** Workbook-only mode previously depended on narrow wording such as “uploaded workbook”. A structured workbook plus a report/analysis request now selects the workbook analytics path by default; explicit requests for web/current-market research still override it.
3. **The chart relevance heuristic could discard valid deterministic charts.** Generic prompts such as “analyze this data and create an in-depth report” may not mention “PMS” or “return”, so the generic topic filter could drop all the already-computed workbook charts. The narrowly scoped bypass applies only to server-generated charts marked `_serverWorkbookEvidence=True` and `sourceOrigin=uploaded_workbook`. Duplicate checks and numeric evidence validation still apply.
4. **Renderer normalization could strip workbook provenance.** The final chart normalization pass could remove the server-only provenance flag and each point's evidence quote/value/source row, causing a later evidence gate to reject otherwise valid workbook charts. The final normalizer now reattaches those fields from the server-generated chart; the evidence audit remains authoritative.
5. **Percentage strings could be scaled twice.** Decimal Excel returns (e.g. `0.248`) are converted to `24.8%`, while values already serialized as `24.8%` remain `24.8%` rather than becoming `2480%`.
6. **Chart logs lacked horizon coverage detail.** The workbook analytics log now records the count of numeric observations for each return period, helping distinguish missing data from downstream chart removal.

## Validation performed

- Focused tests: 43 passed across workbook parsing, evidence gates, chart validation and provenance normalization.
- Python compilation passed for the changed modules.
- A local parse of `PMSPRFFEB2026.xlsx` found 506 distinct AMC/strategy combinations and 9 available return horizons; it generated 9 ranked charts with 10 plotted points each (90 points total).
- A simulated final evidence audit reported zero chart evidence failures, and the publication sanitizer retained all 9 charts after the relevance fix.

## Remaining validation boundary

The backend was not run against the user's live Supabase/model-provider configuration and no new production PDF was rendered in this environment. The end-to-end live run is still required to confirm the renderer/export output.
