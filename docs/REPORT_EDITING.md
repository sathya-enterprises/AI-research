# Follow-up report editing

How an edit to a generated report works, and the settings that control it. The editor changes only what the user
asked for; it never regenerates the report.

## Flow

```
Browser → /api/chat/report/edit/start (Next.js proxy forwards the user's token)
  → POST /api/chat/report/edit/start: refuses a second edit of the SAME report (409) and an edit started
     from an older revision (409); queues the job (GG_ACTION_WORKERS jobs run at once)
  → job: own Phase 2 gateway (project-aware Gemini, circuit breaker, telemetry) + per-edit metrics
  → _edit_report_section_impl
      1. uploaded-document evidence, only when the report has uploads and the edit needs evidence; the
         service is never waited on for more than GG_ACTION_RAG_WAKE_S
      2. _plan_and_route_edit (utils/edit_plan.py):
           - a page reference renders the current PDF with the export handler and maps pages to elements
             (utils/pdf_page_map.py)
           - the position is resolved to one element (utils/edit_location.py); if it is unclear, the editor
             asks (HTTP 422 with clarification=true) instead of guessing
           - explain a chart or table           → one new passage from the chart's own data, spliced after it
           - add text at a precise place        → one new block, spliced at that element
           - anything else                       → the existing section editor, pointed at the resolved section;
                                                   a new chart or table is moved to the requested place
      3. section editor (unchanged contract): rewrites one section. Web research runs only for facts the
         report's evidence does not already cover (utils/edit_evidence.py)
  → _finalize_action_response:
      - new chart/table placement
      - claim check of the NEW facts (utils/claim_check): wrong arithmetic corrected, unsupported claims removed
      - audit: re-fetches only sources cited in changed paragraphs, new sources, and sources with no or stale
        saved evidence; refuses any new evidence finding; stamps evidenceVerifiedAt on re-verified sources
      - for page-targeted edits, re-renders and returns the element's new page (pagePlacement)
  → result carries editPlan, evidence (covered / missing / researched), claimCheck and editMetrics
```

Untouched text stays byte-identical:

- An element-level edit changes only the spliced block.
- A section edit changes only its section.
- Chart registry indexes never change. New charts are appended to the registry and placed by their marker.

## Evidence

The editor reuses evidence in this order:

1. the report's cited statements;
2. each source's saved excerpts;
3. chart data;
4. uploaded-document passages;
5. stored web research.

All of these are indexed in the report pipeline's `EvidenceStore`. A requested fact (an entity, a measure and its
periods) counts as covered only when one passage names all three and states a number.

- **Covered facts** need no search.
- **Missing facts** go to the existing web-research helper as a focused request for those facts only.
- An explicit request for "latest", "current" or "search the web" always researches.
- What still cannot be supported is reported back in `evidence.stillMissing`. It is never filled in.

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `GG_EDIT_ENGINE` | `1` | `0` turns off planning, page and element targeting, element-level edits and the evidence-sufficiency check. Every edit then goes to the section editor as before. |
| `GG_ACTION_WORKERS` | `3` | Edit jobs that run at once (maximum 8). Edits of one report never run together. |
| `GG_ACTION_GATEWAY` | `1` | `0` sends edit model calls through the previous path: Groq first, then Gemini in waves of 4. |
| `GG_ACTION_LLM_BUDGET` / `GG_ACTION_LLM_CONCURRENCY` | `24` / `2` | Model calls allowed per edit, and how many run at once. |
| `GG_ACTION_AUDIT_SCOPE` | `changed` | `full` re-fetches every cited source on every edit, the previous behaviour. |
| `GG_ACTION_EVIDENCE_MAX_AGE_DAYS` | `7` | Saved source evidence older than this is fetched again. |
| `GG_ACTION_RAG_WAKE_S` | `6` | Longest wait for the uploaded-document service during an edit. |
| `GG_EDIT_EVIDENCE_TOKENS` | `6000` | Evidence tokens sent with an element-level edit. |
| `GG_EDIT_CHART_LINT` | `1` | Charts an edit adds must pass the report chart rules: duplicates and mixed periods are refused, titles are fixed. A user-requested two-point comparison is allowed. |
| `GG_EDIT_CLAIM_CHECK` | `1` | Claim check of the new facts an edit introduces. |
| `GG_EDIT_REMAP_PAGES` | `1` | Re-render after a page-targeted edit and return the new page. |

## Rollback

Each part can be switched off with the variables above, without a deploy of code. To return to the previous
editor completely, set:

```
GG_EDIT_ENGINE=0
GG_ACTION_GATEWAY=0
GG_ACTION_AUDIT_SCOPE=full
GG_EDIT_CHART_LINT=0
GG_EDIT_CLAIM_CHECK=0
GG_ACTION_WORKERS=1
```

Note that `GG_ACTION_WORKERS=1` still queues a second user's edit rather than refusing it.
