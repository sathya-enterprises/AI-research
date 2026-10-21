# V63 — multi-entity evidence gate fix

Symptom: "Insufficient relevant evidence: only 1-2 source(s) passed" for a 4-bank, 6-metric comparison.

Changes
1. routes/report.py `_subject_terms`: instruction words (using, collect, verified, credible, publications...) are stop-words; term cap 24 -> 36, so metric words (profit, assets, deposits, advances, equity, gross) are kept.
2. routes/report.py `_interleave_by_entity`: enrichment budget is round-robined across named entities so one company's site cannot take every fetch slot.
3. routes/report.py `_entity_metric_rescue`: pages naming a requested entity with real metric content are re-admitted after the keyword gate.
4. routes/report.py: hard FAIL only when 0 sources; with 1-2 sources a partial report is written with an EVIDENCE LIMITATION block (n/a for missing cells, no estimates).
5. utils/intent.py: max_tokens 400 -> 1500 and reasoning_effort=low for the gpt-oss intent call (truncated JSON caused the HTTP 400s and heuristic fallback).
6. routes/test_report_multi_entity_gate_v63.py: regression tests.

## V64 additions
- utils/supabase_env.py (new): cleans SUPABASE_URL values (quotes, /rest/v1, trailing slash), `screener_kb` falls back to SUPABASE_URL when SCREENER_SUPABASE_URL is a placeholder/does not resolve, and the backend logs one plain-language Supabase diagnosis at startup (typo / paused project / DNS / missing anon key). Wired into screener_kb, chat, auth, main.
- utils/chart_catalog.py: writer chart guidance now includes a data-shape -> chart-type map and an instruction to vary types across the full catalog (no invented data). The v60 evidence chart gallery (all chart types the data supports) is unchanged and is on by default; startup now logs its mode. .env.example documents REPORT_CHART_GALLERY.
- utils/test_supabase_env_v63.py: tests.
