# Action reliability hardening — v27

## Why this update exists

A two-section Action could return a generic HTTP 502 after roughly 45 seconds. The synchronous request combined bounded web research with sequential LLM rewrite and validation calls; the last provider pool could return an empty response after exhausting its retry window. The browser then displayed only “Couldn't generate the edit,” which did not identify the provider/retry failure.

## Changes

- Added a background Action-job endpoint (`POST /api/chat/report/edit/start`) and polling endpoint (`GET /api/chat/report/edit/status/{job_id}`). Existing synchronous `POST /edit` remains available for backwards compatibility.
- Updated the UI to queue the edit, poll status, and only merge the returned report after the backend marks the full edit successful. Multi-section Actions therefore remain atomic in the visible report.
- Added explicit status/error details and protection against duplicate running Actions. An unexpected backend exception is surfaced as a clear failed job; the current report isn't merged.
- Increased the bounded Action provider retry window from 28 to 50 seconds and fixed a busy loop when all provider keys/model pairs are rate-limited.
- Bounded fresh Action web search to 25 seconds so it cannot stall the background job indefinitely.
- Added exact heading auto-detection from normal prompts, including Markdown-bold section names, so a user can name multiple report headings naturally without learning `Target section:` syntax.
- Added targeted regression tests for natural multi-section Actions, job queue/status behavior, provider cooldown behavior, and front-end polling.

## Limitations

External providers can still be down, rate-limited, or return unusable content. In those cases the Action now has more retry time and gives a concrete failure reason; it does not fabricate a replacement section or silently commit a partial multi-section edit. Action job state is in-process, matching the existing report-job design, so production deployments should run the backend as a single process or move job state to a shared store if multiple workers are enabled.
