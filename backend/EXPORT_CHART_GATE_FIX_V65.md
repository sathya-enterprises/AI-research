# V65 — "This report contains chart values that conflict with verification status…" (HTTP 422 on Download PDF)

Symptom: report generates fine, then `POST /api/chat/report/pdf` returns 422 and the UI shows
"This report contains chart values that conflict with verification status or lack supporting source evidence."

Cause (from the 18-Oct-2026 log):
1. The chart evidence prune ran once, *before* `_append_evidence_chart_gallery` and before the post-audit
   sanitizers. Gallery charts (25 added) and later redactions were never re-checked.
2. `prune_unsupported_chart_points` always kept `table` charts, even empty ones
   ("Supporting Data Table — insufficient columns/rows" = "Chart 2 has no data rows").
3. The PDF/HTML/DOCX exporters re-run the same audit and answered 422 for the whole report.

Changes
- utils/chart_evidence_audit.py: tables/heatmaps/graphs with no usable payload are now removed by the prune step
  (hard reject wins over the "always keep tables" rule). New `remap_chart_placeholders` and
  `sanitize_charts_for_publication` (audit -> prune loop, renumbers [CHART_n], returns remaining findings).
- routes/report.py: final chart gate right before the response is built (same validator the exporters use),
  followed by a fresh audit when it changed anything.
- routes/pdf.py, html_report.py, docx_report.py: export now prunes unsupported charts/points and exports the rest;
  422 is only returned if findings *remain* after pruning. Unverified values are never published.
- utils/test_chart_publication_gate_v65.py: regression tests.

Not changed (seen in the same log, separate issues): Playwright `NotImplementedError` on Windows (asyncio loop policy),
`DeadlockDetected` when persisting the research library, and the entity extractor treating "How" as a company (HWM).
