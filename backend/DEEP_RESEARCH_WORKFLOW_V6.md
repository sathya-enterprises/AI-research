# Deep Research workflow improvements (v6)

## What changed

This backend update is aimed at broad, evidence-heavy investigations such as the 100-page FII/FPI flow hypothesis report. It changes the research workflow rather than forcing a longer prompt or adding a fixed report outline.

- **Explicit page targets survive the job lifecycle.** A clear request such as `100-page report` selects `in_depth`, sets the page target, and passes that target through normal, post-evidence, and recovery pagination paths.
- **Search planning is wider and bounded.** Deep Research gets up to 12 independent query angles. FII/FPI India-flow requests receive targeted angles for official FPI flows, shareholding, primary issuance, sector holdings, FDI, debt instruments, macro drivers and the named analyst claim. Each angle is bounded to a modest result count so a 12-query plan does not multiply into 12 searches asking for nearly 100 results each.
- **Counter-evidence is no longer filtered merely for lacking the word “FII”.** The FPI relevance gate can retain substantive sources about primary-market issuance, sector holdings, debt, FDI and expert claims even if those pages do not repeat the original acronym. Off-topic pages still need to match a meaningful evidence dimension and, after fetching, contain substantive dated/numeric context.
- **A thin source pool triggers a bounded top-up.** If the first relevance pass produces fewer than 35 candidates for a Deep Research FPI report, the backend runs up to six distinct supplemental searches before enrichment.
- **Search provenance is preserved.** Search-query, publisher and publication-date metadata are retained when moving results into the report source pool.
- **Coverage is measurable.** The API response and background-job status include `researchCoverage`: unique source count, publisher-domain count, fetched publisher pages, official domains, search-angle count, missing FPI dimensions and page-target shortfall. This is diagnostic, not a correctness guarantee.
- **The writer receives FPI-specific comparison rules.** It must keep net secondary-market equity flows, gross purchases/sales, IPO/QIP and other primary issuance, debt, FDI, listed ownership and portfolio mark-to-market changes distinct. It must directly verify any attribution to Gautam Duggad and not claim the amounts are “equivalent” unless the period, currency and accounting basis are comparable.
- **PDF page preflight and page budget are separated.** The PDF renderer is called only with parameters it actually accepts; the explicit page target belongs to the page-budget controller. This prevents a bad keyword argument from being caught as an “unavailable pagination” fallback.

## How to run a 100-page test

1. Replace the existing backend source with this version, retaining your own `.env` files locally. Do not copy `.env.example` over an actual local environment file.
2. Restart the backend and any separate service that loads backend modules. Confirm the startup log shows the new version of `routes/report.py` loaded.
3. Submit the original FII/FPI prompt with `100-page report` in the question or with `requestedPageCount: 100` in the report request. The server should use `in_depth`, regardless of a stale, shorter tier value in the request.
4. In the logs, look for:
   - `Deep Research FPI query plan: 12 independent evidence angles`
   - `Report search budget: angles=... per_query_limit=... per_query_minimum=...`
   - `Deep Research FPI source top-up...` if the source pool is thin
   - `Deep Research coverage snapshot...`
   - `Report page budget: explicit requested target=100...`
   - `Report: export-renderer pagination preflight — pages=...`
5. Inspect `researchCoverage` in the completed result. `status: adequate` means the tracked minimums and topic dimensions were represented in the source pool; it does not certify individual claims. Review `coverageGaps` and `missingTopicDimensions` before treating the report as comprehensive.
6. Confirm the final PDF's `renderedPages` and `pageBudget.minimumReached`. A page target is never permission to fill gaps with fabricated content. If the audit exposes evidence gaps or the PDF is short, use the coverage fields/logs to identify which research angle or source fetch failed.

## Test status

The focused backend regression suite for this change set is run with:

```bash
python -m pytest routes/test_fpi_flow_research_v6.py utils/test_report_tiers.py utils/test_uploaded_workbook_analytics.py utils/test_workbook_chart_publication_gate.py utils/test_chart_publication_gate_v65.py routes/test_report_chart_evidence_v50.py routes/test_report_validation_pipeline_v80.py routes/test_workbook_chart_provenance_normalization.py -q
```

This suite validates query planning, relevant multi-channel FPI evidence retention, explicit page-count parsing, coverage diagnostics and previously fixed workbook/chart behavior. External search, live provider authentication and final PDF generation still have to be verified in the user's running environment; test success does not claim those providers returned a real 100-page report.
