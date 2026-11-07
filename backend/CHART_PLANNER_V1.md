# Evidence-first chart selection and source-ID consistency (v1)

## Flow (before -> after)
Before: writer proposes charts -> audits prune -> keyword gallery fills in. A dataset that was never
retrieved simply disappeared late.
After: question -> chart plan -> coverage check on retrieved sources -> targeted gap fetch -> final
canonical S# IDs -> plan bound to those IDs in the writer prompt -> writer/gallery -> ID reconciliation
at every gate.

## Modules
- `utils/chart_planner.py` - `plan_chart_needs`, `assess_chart_coverage`, `gap_queries`,
  `planned_chart_types`, `bind_charts_to_plan`, `writer_plan_block`. Deterministic, no network.
  Domains: sector rotation, FPI flows, promoter sales, FTA, banking, generic fallback.
- `utils/source_ids.py` - one definition of "which sources a chart cites": `chart_source_ids`,
  `reconcile_chart_source_ids`, `next_source_number`, `unresolved_chart_references`.

## Pipeline hooks (`routes/report.py`, `generate_report`)
1. Before the manifest is built: plan, assess, fetch for unmet needs (<= 3 queries, <= 8 pages, 30 s),
   append rows. Existing S# never move (`build_source_registry` appends).
2. After the manifest: re-assess on final IDs; `CHART PLAN` block added to the writer prompt
   (draw / skip-because-missing / which IDs).
3. Source-less writer charts are bound to the planned IDs; then `reconcile_chart_source_ids` runs
   before grounding, after the post-writer cleanup, and inside `_final_chart_evidence_gate`.
4. Gallery: `gallery_types_for(..., planned_types=)` narrows the four domain branches to the types the
   plan can support (needs >= 2 survivors, otherwise unchanged).
5. `chartPipeline` gets a `planned` stage (needs, unmet, queries, pages added).

## ID defects fixed
- Action/edit flow: `build_source_registry(prior + additions)` renumbered every source from S1 when the
  appendix had gaps; now `build_source_registry(prior, additional_sources=additions)`.
- Evidence engine: a row with no canonical ID fell back to `S{position}`, colliding with another source.
  It is now excluded.
- Charts cited IDs the audit read from different keys (`sourceIds` vs `source`); orphans survived in
  one key. One reconciliation now cleans all keys and re-points quoted points to the source that holds
  the quote (or drops the quote so grounding can retry).

## Switches
`REPORT_CHART_PLANNER=off` disables everything above. `REPORT_CHART_GAP_QUERIES` (3),
`REPORT_CHART_GAP_FETCH_SECONDS` (30).

## Known limits
- Coverage is regex/line based; it proves a figure line exists, not that the value is right (the
  existing grounding and audit still do that).
- Only sector rotation has a structured fetcher (Yahoo/niftyindices); other domains use search queries.
- Gap-query wording and official hosts are from memory and should be checked against live results.
