# Growth Gradual diagnostics

## Research capture

Research captures are opt-in. Set `GG_RESEARCH_CAPTURE=1` in the backend environment and restart the backend. By default, each request writes a JSON file directly into:

`backend/diagnostics/research_capture_<captureId>.json`

The backend logs the absolute path when capture begins and when it is finalized. If no file appears, look for the startup/request log stating that capture is disabled, or a `Research capture write failed` traceback; write failures are no longer silently ignored. Set `GG_RESEARCH_CAPTURE_DIR` to an absolute path to use a different folder. A report that exits before normal finalization is written with `incomplete` or `failed` status rather than remaining `in_progress`.

Capture files can include the research question, public-source extracts, citation metadata, chart data, and report text. Do not enable captures for confidential queries or uploaded private documents. Do not commit capture files into source control.
