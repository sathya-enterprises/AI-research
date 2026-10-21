# Report tier routing regression checklist

The report-depth picker has three values: `brief`, `detailed`, and `in_depth`.

Before generation, verify all of the following:

- Changing the composer picker to In-Detail changes the value captured by `send()`.
- Changing the composer picker to Deep Research changes the value captured by `send()`.
- The direct report request body contains the selected `reportTier`.
- In-Detail and Deep Research are routed through `/api/chat/report/start`.
- The start endpoint returns the same `reportTier` it received.
- `status/:jobId` returns that same tier.
- The completed report's `reportTier` matches the requested tier.
- `ReportPanel` mirrors `initialTier` before generation and the returned report tier after generation.

The UI labels and displayed page ranges are intentionally unchanged.
