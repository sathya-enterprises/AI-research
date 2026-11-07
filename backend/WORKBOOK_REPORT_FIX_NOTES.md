# Workbook report pipeline fixes

## Changes in this update

- Deterministic PMS chart points now carry an explicit, source-derived evidence quote containing worksheet, row, metric, raw workbook cell value, and original row context. This addresses cases where the serialized CSV row omitted a normalized period cell or represented a return as a percentage string, causing chart evidence validation to reject valid values.
- Numeric validation accepts raw percentage strings such as `24.8%` while still requiring the value to appear in the quoted workbook evidence and the plotted/raw scale to be consistent.
- If the report planner fails or returns malformed JSON, workbook-only PMS reports preserve the selected depth: Detailed uses eight analysis sections, and In-depth adds further evidence-driven sections. The writer must still omit analyses unsupported by available data.
- Added regression coverage for workbook chart publication and raw percentage strings.

## Validation performed

- `python -m compileall -q routes/report.py utils/chart_evidence_audit.py utils/uploaded_workbook_analytics.py`
- `python -m pytest -q utils/test_workbook_chart_publication_gate.py utils/test_uploaded_workbook_analytics.py utils/test_chart_publication_gate_v65.py routes/test_publication_chart_omissions.py`
- Result: 17 tests passed.

A separate legacy test module, `routes/test_report_post_gallery_chart_gate.py`, fails collection because it imports `_final_chart_evidence_gate`, which is not present in the uploaded backend version. This was not caused by this patch and was not included in the passing focused run.

Live generation against the user's running Supabase/model-provider environment has not been performed; regenerate the PMS workbook report and inspect the chart pipeline logs to confirm the end-to-end outcome.
