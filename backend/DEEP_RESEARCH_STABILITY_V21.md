# Growth Gradual v21 — Deep Research Stability

> Historical release note. Any page-band language in this document describes a superseded implementation. Current report depth is evidence-proportionate and has no minimum page, word, character, section, statistic, or visualization quota; page count is layout metadata only.

This release responds to the supplied Deep Research generation log and companion banking-sector PDFs.

## Observed issues

- Gemini continuation requests timed out or were rate-limited (HTTP 429).
- Groq continuation calls returned HTTP 413, then repeatedly retried similarly oversized payloads.
- On Windows, the synchronous Playwright wrapper failed inside its worker thread with `NotImplementedError` while creating subprocesses; the application then used system Chromium.
- The captured log only shows the report at 36 pages during one `in_depth` expansion cycle and ends during status polling. It does not contain an authoritative final completion/page-count line for that run. The 20-page and 6-page attached PDFs also have earlier timestamps than the continuation log and should not be treated as the final artifact for that exact execution.
- The 20-page sample contains a standalone executive-summary panel followed by a qualified executive-summary section, some repeated credit/deposit and asset-quality analysis, and duplicate value/change data in cover KPI cards.

## Changes

- Reduced and bounded continuation payload/output budgets. Groq requests now preserve both the prompt contract and relevant evidence, and a 413 triggers one significantly smaller prompt/token-budget retry before provider fallback continues.
- Gemini continuation uses bounded retries across a small model/key set and a longer read timeout for substantive continuation output; provider timeout/429 states are cooled down to avoid hammering the same pair.
- Windows PDF export attempts system Chromium first, avoiding the Playwright sync/thread subprocess failure observed in the log. Other platforms retain Playwright-first rendering.
- Pagination preflight, quality snapshot, and export share the same `build_pdf()` rendering route and its theme/image inputs.
- Qualified executive-summary headings suppress a duplicate standalone summary panel. Section numbering counts only rendered sections, and duplicate KPI change values are omitted.
- Repeated H3/H4 modules are checked across the report, not just within one H2, while protecting new citations and quantitative anchors. The matcher catches concise repeats and same-analysis rewrites under different headings only when the cited/quantitative evidence is already covered.
- The HTML/PDF renderer retains compatibility with explicitly selected, provenance-gated evidence assets: recreatable tables/charts are rendered from extracted data, with attribution to the verified source webpage. Unselected or unverified asset markers do not leak into the report.

## Validation

Run the focused suites from `backend`:

```text
pytest -q routes/test_deep_research_stability_v21.py routes/test_report_preflight_v20.py routes/test_report_rendering_regressions_v20.py routes/test_dynamic_presentation_composition.py routes/test_report_tier_regressions.py routes/test_pdf_css_warnings.py
pytest -q routes/test_v18_layout_sources.py routes/test_pdf_overflow_and_sources.py routes/test_pdf_pagination_v7.py routes/test_pdf_offline_dynamic_heavy.py routes/test_report_depth_recovery_v13.py routes/test_report_tier_controls_v8.py utils/test_report_tiers.py
```

The focused regression set passes 72 tests in this workspace. These tests validate local logic; they do not replace a live run with the configured Gemini/Groq keys and Windows system Chromium. The historical 70–100 page band is superseded. Current Deep Research should broaden source coverage and analysis only where evidence supports it; there is no page target to reach or report as unmet.
