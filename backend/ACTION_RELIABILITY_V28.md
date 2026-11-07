# Growth Gradual v28 — Action evidence handoff and research persistence

## Fixes based on the reported 422 Action failure

- Fresh web evidence IDs are parsed from actual evidence-block headers, rather than every `S<number>` token that happens to appear in source text or URLs.
- The Action writer is explicitly given the exact new citation IDs and reminded to use them only beside supported claims.
- If a provider leaves out the source marker, a citation-only mapping step matches evidence excerpts to the candidate's paragraphs and attaches valid `[Sxx]` markers without rewriting prose. The deterministic evidence gate runs again afterwards; unsupported or unmatched evidence is not silently forced into the report.
- The same citation handoff runs before the first validation gate and again after correction passes, preventing a later repair from erasing citations.

## Fixes based on the generation log

- Action web search queries now prioritize the currently edited section and its own instruction paragraph. A long report title or another section's heading no longer consumes the whole query before the requested dimensions (financing, tariffs, transmission, PSAs, grid stability) are included.
- Research-store persistence handles collisions on either `source_key` or `canonical_url` idempotently, instead of aborting the batch on a tracking-variant URL.
- Research-store query, title, publisher, domain, publication date and page text are sanitized for PostgreSQL-forbidden NUL characters before insertion.
- The **top-level `frontend` used by the local app** now also uses the async Action start/status routes; the earlier async handler only existed under `backend/frontend`, explaining why logs still showed the old synchronous `/api/chat/report/edit` call. The required Next.js proxy routes are copied into the runtime frontend too.
- The Growth Gradual icon styles include an explicit `height: auto` rule to avoid the reported Next/Image aspect-ratio warning.

## Safety and atomicity

The original report is still returned unchanged if a requested edit fails the evidence, completion, table, chart or overlap validations. Multi-section edits are merged only when every target succeeds. This patch does not bypass the validation gate; it fixes the source-ID handoff so supported evidence can pass validation correctly.

## Validation performed

- Python compilation passed for `routes/report.py` and `utils/research_store.py`.
- Focused Action/research regression suite: **66 passed, 1 skipped** (including a live-mocked Action citation-handoff test and the runtime frontend path assertion).
- `utils/test_web_research_library.py` could not be included in this environment because `psycopg2` is not installed here; tests requiring live PostgreSQL were not run.
- Frontend dependencies were not installed, so a full Next.js build could not be run locally.
