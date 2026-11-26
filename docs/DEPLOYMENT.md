# Deploying the report pipeline changes

These changes add no database tables, no new services and no frontend changes. The API contracts the frontend uses
are unchanged: report start, status, PDF export, edit, RAG and email. Deployment is a normal backend redeploy.

## Steps

1. **Merge the branch.** The changes are on `backend-edit` (pushed from the local `story-first` branch). Review it
   and merge it into `main` through a pull request.
2. **Dependencies.** No new Python packages: `httpx`, `playwright` and the existing requirements cover everything.
   The `Dockerfile` and `render.yaml` build commands are unchanged.
3. **Environment.** All new settings are optional (see `docs/CONFIGURATION.md` and `backend/.env.example`). Set
   them in the host's secret store, never in the repository:
   - `GG_GEMINI_KEY_PROJECTS` and `GG_GEMINI_QUOTAS`: strongly recommended once the project of each Gemini key is
     known. Without them, all keys are treated as one shared pool, which is safe but slower.
   - `DATA_GOV_IN_API_KEY` and `GG_DATAGOV_RESOURCES`: only if data.gov.in data is wanted.
   - Defaults that are already on and need nothing: claim records, length accounting, chart-title fixes, duplicate
     and metadata chart rejection, in-section disagreement notes, the run summary.
4. **Writable cache directory (only with data.gov.in).** Responses are cached in `backend/.cache/datagov`. On
   hosts with an ephemeral disk (Render free tier) the cache is lost on restart. That only means it is
   re-downloaded; nothing breaks. Set `GG_DATAGOV_CACHE_DIR` to a persistent disk path if one exists.
5. **Redeploy.** Render: push to the deployed branch, or use *Manual Deploy*. Docker:
   `docker build -t gg-backend backend/ && docker run -p 10000:10000 --env-file backend/.env gg-backend`.

## Checks after deploying

1. Generate one Detailed report.
2. In the backend log, find the line `V2 run summary:`. It should show:
   - stage timings;
   - `429x…` / `503x…` counts;
   - claims by status;
   - `pages … (within)` or a stated reason the report is short.
3. Find `V2 length:`. A `below_band_writing` verdict lists the sections that came back short and the model that
   wrote each part.
4. Download the PDF. Check there are no blank or near-empty pages, and that every chart title names its subject.
5. If data.gov.in is configured, look for `data.gov.in: N selected, M fetched`, plus a reason for each resource
   skipped. Confirm the key never appears in the log (it is masked as `api-key=[redacted]`).

## Rollback

Every change can be switched off by environment variable, or reverted with one commit:

| Change | Switch |
|---|---|
| Disagreement notes in sections | `GG_V2_DISAGREEMENT_NOTES=0` |
| data.gov.in | unset `DATA_GOV_IN_API_KEY` |
| Claim check (whole stage) | `GG_V2_CLAIM_CHECK=0` |
| Quota limits enforced in advance | unset `GG_GEMINI_QUOTAS` (429-driven cooling stays) |

Commits on `backend-edit` for this work, oldest first:

- `bdfd139`: Gemini quota manager.
- `7d46f5d`: claim records.
- `968b188`: length accounting.
- `f29b03b`, `ee3417d`: chart titles and rejections.
- `7281026`: disagreement notes.
- `41932b8`: data.gov.in.
- `fb97a36`: PDF tables.
- `37f820f`: run summary.
