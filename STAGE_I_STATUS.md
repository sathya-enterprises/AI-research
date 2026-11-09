# Stage I — client-readiness status

Read this file instead of the chat history. Branch `backend-edit`; integration branch `integrate/backend-ui`
(not yet updated for Stage I). Test baseline: 1183 passed, 15 xfailed (known failures, `routes/conftest.py`).

Offline replay: `python scripts/eval_report.py <run_dir> --replay-v2` where `<run_dir>` holds `capture.json` and
`status_final.json` (the Stage H in-depth capture is staged at `backend/diagnostics/eval_runs/i_replay/h_c`).

| # | Fix | Status | Commit | Evidence / what is left |
|---|-----|--------|--------|--------------------------|
| 1 | Leaked writer instructions (Methodology) | DONE | 2bb7731, c91db98 | `utils/leak_gate.py` (8-word runs vs prompt templates + instruction markers, self-heal by removal); methodology built from templates without the question/window note. On the Stage G/H reports, only the leaked methodology block is detected. |
| 2 | Stale / out-of-window data, period labels | PARTIAL | 849f556 | `utils/period_hygiene.py`; layout tables carry period/as-of labels, out-of-window and undated tables are filtered; fiscal labels in dataset charts. TODO: a period gate on the final report (e.g. the "April 2024" appendix table in Stage H), and fiscal labels on the generic heatmap (Figure 14). |
| 3 | Flattened tables and text diagrams | DONE | 121d77b | `utils/flow_text.py` (arrow chains → sentences, box art removed, flattened "--- — ---" tables dropped); a headerless pipe table is dropped, never flattened (`markdown_normalizer`); rendered-PDF gate `rendered_flow_findings` inside `_audit_rendered_pdf_bytes`. Residue left in all saved reports: 0. |
| 4 | Thesis from data (regime) | PARTIAL | c91db98 | `utils/regime.py`: groups (configurable via `REGIME_GROUPS_JSON`), per-quarter/compounded group returns, leaders/laggards, half-window leadership; given to the outline and exec summary; contradicted rotation claims rewritten; mislabels fixed. TODO: dispersion test (within-group range vs between-group gap). |
| 5 | Self-description from the final structure | DONE | 2bb7731 | `utils/boilerplate.py` resolves the cited-source count from the final report; how-to-read counts from written sections; no [S#] sentence when markers are hidden; no Sources line in navigation sections. |
| 6 | Repetition | PARTIAL | a2b6f6e (WIP) | `utils/repetition.py` written; on the h_c replay: duplicate tables 7 → 0, max repeats per stat 6 → 4, 30/30 figure boxes echo the body. TODO: wire into V2, data takeaways, drop CONTEXT/"Supports:", safer stat cap with cross-references. |
| 7 | Data sanity (FS QTD) | DONE | 1d926e6 | +17.97% was wrong (an alias ticker's monthly bar against the official quarter-end close; the live level gave about +10.4%, in line with Bank +7.61%). QTD now comes from the same series' latest daily close, with its date; `utils/data_sanity.py` blocks constituent divergence, a mismatch with the live level, and out-of-range values. |
| 8 | Chart/table titles, labels, types, lint, inventory | TODO | — | See the user's list: titles, axes/units, chart type, Figure/Table numbering, Source captions, quality gate, CHART LINT, collision check, inventory. |
| 9 | Smaller items (typos, cut-offs, empty boxes, truncated dates, run-together sources, ordinal gaps) | TODO | — | Also: "First … Fourth" ordinal gaps after evidence removals. |
| 10 | Rendered-PDF gates in the job | PARTIAL | 121d77b | Flow/table residue gate is in the rendered audit. TODO: all new checks run in the in-job rendered check with self-healing (3 rounds). |
