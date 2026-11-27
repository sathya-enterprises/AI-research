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


## RAG service security rollout (shared secret and session ownership)

These changes protect the uploaded-document (RAG) service and tie each session to its owner:

- **Shared secret.** Every RAG service endpoint except `/ping` and `/health` requires the backend's shared secret.
  If no secret is configured, the service refuses everything (fail closed).
- **Signed-in sessions** belong to the verified Supabase user.
- **Anonymous sessions** keep working through their random session ID.

Deploy in this order. With this order the RAG service is never more exposed than it is today, no request fails for
lack of the secret, and once step 4 is done the service refuses every unauthenticated call.

1. **Create the secret** on your own machine. Store it only in the hosts' secret settings; never commit it or
   paste it into a ticket.
   `python -c "import secrets; print(secrets.token_urlsafe(32))"`
2. **Frontend (Vercel): deploy.** The browser now sends the user's existing Supabase token on uploads, report
   starts and report edits, and the Next.js proxies forward it.
   - No new environment variables.
   - Safe with the old backend, which ignores the header on these routes.
3. **Backend (Render): set `RAG_SERVICE_TOKEN` to the secret, then deploy.**
   - From then on the backend sends the secret and the caller's verified identity (`X-RAG-Principal`) on every RAG
     call.
   - It rejects session IDs that are not UUIDs, and rate-limits failed session lookups.
   - The old RAG service ignores the new headers, so nothing breaks.
4. **RAG service (Hugging Face Space or Render): set the same `RAG_SERVICE_TOKEN`, then deploy.**
   - Also set `RESEARCH_DATABASE_URL` (recommended) so ownership changes run as atomic Postgres transactions; see
     "Ownership storage" below.
   - Do not set `RAG_ALLOW_UNAUTHENTICATED` in production. It is only for local development without a secret.
   - Do step 4 straight after step 3. Until step 4 is done, the old RAG service is still publicly reachable, as it
     is today.
5. **Verify:**
   - `curl <rag>/health` returns only `{"status", "ready"}`.
   - `curl -X POST <rag>/query` returns `401`.
   - The backend's `/api/rag/health` reports `ok`.
   - An anonymous upload and chat about the file both work.
   - A signed-in upload, then a chat and a report about the file, all work.
   - A second account using the same session ID gets nothing back.

**Existing sessions** are not invalidated:

- A session uploaded before this change has no owner recorded and keeps working as an anonymous session.
- It becomes private the next time a signed-in user uploads to it.

**Ownership storage:**

- The owner is recorded in the existing `rag_chunks.source_metadata` JSON column (`gg_owner`). No table or
  migration was added.
- With `RESEARCH_DATABASE_URL` set, moving an anonymous session to a user is one transaction under a per-session
  advisory lock. Of two concurrent claims, exactly one wins.
- Through the Supabase REST API alone, the move is done row by row, each row conditional on still being anonymous.
  A race between two accounts there can, at worst, record both owners. The session is then refused to both (fail
  closed), never shared.

**Rollback:** roll back the RAG service first, then the backend.

- A new backend talking to an old RAG service works, because the old service ignores the headers.
- An old backend talking to a new RAG service gets `401`, so RAG features would stop.
- Removing the secret from the new RAG service does not open it up: it fails closed.

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
