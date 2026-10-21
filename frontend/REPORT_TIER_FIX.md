# Report tier routing fix

The report-depth picker is stateful and must be authoritative for report generation.

Two stale-state paths are guarded:

1. `send()` includes `selectedReportTier` in its `useCallback` dependency list, so a
   direct report request cannot keep an old `brief` closure after the user switches
   to `detailed` or `in_depth`.
2. `ReportPanel` synchronizes its pre-generation `selectedTier` from the current
   `initialTier` prop. Once a report exists, the report's own returned `reportTier`
   remains authoritative for artifact downloads.

The UI labels and page ranges are intentionally unchanged.
