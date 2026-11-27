# Configuration guide: report pipeline reliability settings

This guide covers the settings added for Gemini quota management, claim verification, report length, charts,
source disagreements, data.gov.in and the run summary. Every setting is an environment variable read by the
backend. All of them are optional, and the defaults are safe. `backend/.env.example` lists each one with an example.

Never commit real keys. `.env` is git-ignored. Put keys in the host's secret store (Render, Railway, Docker
secrets and so on), not in the repository.

---

## 1. Gemini keys, projects and quotas (`utils/llm_quota.py`)

Google counts Gemini limits **per Google Cloud project and per model**, not per API key. Ten keys created in one
project share one allowance, so rotating them adds no capacity and only spreads the 429s around.

| Variable | Meaning | Default |
|---|---|---|
| `GEMINI_API_KEY` | The key pool (existing setting, comma-separated). | — |
| `GG_GEMINI_KEY_PROJECTS` | The project of each key, in pool order (`proj-a,proj-a,proj-b`), or JSON `{"<last 6 chars of key>": "<project>"}`, or `per_key`: each key is its own project, named `gemini_project_01` … `gemini_project_16` in pool order. Use `per_key` only when every key was created in a different Google Cloud project. | Unset: every key belongs to one `shared` pool, the safe assumption. |
| `GG_GEMINI_QUOTAS` | JSON limits per project and model: `{"default": {"rpm": 10, "tpm": 250000, "rpd": 250}, "proj-b": {"*": {"rpm": 1000}}}` | Unset: nothing is enforced in advance, and only Google's 429s cool a pool. |
| `GG_GEMINI_DAILY_RESET_HOURS` | How long a pool stays cooled after a per-day-quota 429 when Google gives no delay. | `6` |

How to fill these in:

1. In Google Cloud console → *APIs & Services → Credentials*, note which project each key belongs to.
2. In *Gemini API → Quotas* (or AI Studio → *Usage & billing*), read each project's RPM, TPM and RPD for the
   models in use (`gemini-3-flash-preview`, `gemini-3.6-flash`, the lite models).
3. Set `GG_GEMINI_KEY_PROJECTS` and `GG_GEMINI_QUOTAS` with those numbers.

The Gemini API itself does not tell a client which project a key belongs to, so check the mapping in AI Studio
(*Get API key* lists each key with its project) or the Cloud console before you set it.

What the gateway does with them (when keys are mapped to projects, `per_key` included):

- It sends a request only to a pool that has room for its estimated tokens, choosing the least busy and
  healthiest project (requests in flight, requests in the last minute, recent failures). Within one attempt
  plan each model goes to a different project, so a retry never lands on the project that just failed.
- On a 429 it cools only that project and model, for the delay Google asks for (the `Retry-After` header or
  the `RetryInfo` in the body), plus jitter. The project's other models and the other projects keep serving.
- A daily-quota 429 cools the pool for the configured hours.
- Google's own limit is read from the 429 body (`QuotaFailure`: quota id and value) and reported in
  `runSummary.llm.quotaLimitsReported`. Use those numbers to fill `GG_GEMINI_QUOTAS`.
- A 503 is model-serving capacity, not quota. It cools only that project and model for
  `GG_GEMINI_503_PROJECT_COOLDOWN_S` (20 s, or longer if Google gives a delay).
- **Circuit breaker.** When `GG_GEMINI_503_BREAKER_PROJECTS` (2) different projects get a 503 on the same model
  within a minute, or `GG_GEMINI_429_BREAKER_PROJECTS` (3) get a 429, the model is cooled for every project:
  30 s for 503 and 60 s for 429 (`GG_GEMINI_503_BREAKER_S` / `GG_GEMINI_429_BREAKER_S`), doubling on each trip
  in a row up to 5 minutes, with jitter. After that, one probe request at a time is let through until the
  model answers again.
- A fallback (Groq) answer shorter than half the requested words, or a section answer without the `[S…]`
  citations its prompt asked for, is held back while Gemini is retried, and is used only if Gemini never
  answers.

With no mapping (the `shared` pool) the earlier behaviour is unchanged: one 503 cools the model, and 429s on two
keys within a minute cool the model.

**Adaptive concurrency.** The gateway starts at `GG_V2_SECTION_CONCURRENCY` (5). A 429 or a breaker trip lowers
it by one, at most once per `GG_V2_CONCURRENCY_DECREASE_EVERY_S` (30 s), never below `GG_V2_CONCURRENCY_MIN` (3).
After `GG_V2_CONCURRENCY_INCREASE_AFTER` (10) successes in a row it rises by one, never above
`GG_V2_CONCURRENCY_MAX`. That defaults to the starting value, so more keys never raise concurrency on their own.

Other gateway settings (existing):

- `GG_V2_SECTION_CONCURRENCY` (default 5): parallel calls.
- `GG_V2_HEDGE=1`: hedged calls (off by default, because hedging added 429/503s in live runs).
- `GG_V2_LLM_CALL_BUDGET_DETAILED` / `_IN_DEPTH`: call budgets per report.

Logs and telemetry name a key only by its alias (`gemini_project_03`, or `gemini_key_03` in the shared pool);
no part of a key is logged. Every HTTP attempt is logged as `V2 LLM request <id> <purpose>/<section> <provider>
<model> <alias> status=… <category> <seconds> tokens=in/out attempt=n`. Per-project and per-model totals (requests,
429/503, p50/p95/p99 latency, peak requests and tokens per minute, Groq share) are in `runSummary.llm.requests`.

## Evidence retrieval and reranking (`utils/evidence_rerank.py`)

Each section's evidence is the BM25 candidate set reranked by the RAG service's cross-encoder
(`cross-encoder/ms-marco-MiniLM-L6-v2`). Web passages are not embedded. The benchmark behind this choice is in
`backend/benchmark/retrieval/README.md`.

| Variable | Meaning | Default |
|---|---|---|
| `GG_V2_RERANK` | `0` turns reranking off: every section uses the BM25 ranking, exactly as before. | `1` |
| `GG_V2_RERANK_DEPTH` | BM25 candidates reranked per subsection. | `40` |
| `GG_V2_RERANK_TIMEOUT_S` | Per-subsection timeout. That subsection stays on BM25 if it is exceeded. | `20` |
| `GG_V2_RERANK_BUDGET_S` | Time budget for all reranking in one report. Later subsections stay on BM25 once it is used up. | `150` |
| `GG_V2_RERANK_CONCURRENCY` | Rerank requests in flight at once. | `2` |
| `GG_V2_SECTION_EVIDENCE_TOKENS` | Evidence tokens per section call. Whole passages are dropped, least relevant first, and passages behind the section's figures are always kept. | `16000` |
| `RAG_SERVICE_TOKEN` | Shared secret between the backend and the RAG service, sent as `X-RAG-Service-Token`. Set the same value on both. When it is set, the service refuses requests without it. | unset |

How retrieval behaves:

- Reliability weighting (0.5 + reliability), the limit of 3 passages per source and the table boost are kept.
- A near-duplicate passage is dropped only when it states exactly the same numbers. A similar passage with a
  different value, period or unit is kept, so conflicting figures reach the writer and the disagreement check.
- If the service is unreachable, refuses, errors or times out, that subsection keeps its BM25 ranking. After a
  refusal or connection error, the rest of the report uses BM25 without waiting on the service again.
- `runSummary.retrieval` reports the subsections reranked and the reasons for any BM25 fallback, the seconds
  spent, and the section calls whose evidence was trimmed by the budget.

## Uploaded-document (RAG) session security

| Variable | Where | Meaning | Default |
|---|---|---|---|
| `RAG_SERVICE_TOKEN` | backend **and** RAG service (same value) | Shared secret, sent as `X-RAG-Service-Token` and compared in constant time. The RAG service refuses every endpoint except `/ping` and `/health` without it. | none. The RAG service then refuses requests (fail closed). |
| `RAG_ALLOW_UNAUTHENTICATED` | RAG service, local development only | `1` with no token set allows unauthenticated calls. Never set it in production. | off |
| `GG_RAG_FAILED_LOOKUPS_PER_10MIN` | backend | Failed session lookups allowed per caller per 10 minutes before `429`. A failed lookup is an unknown, not-yours or malformed session ID. The caller is the signed-in user, or the forwarded client address for anonymous callers. | `60` |
| `RESEARCH_DATABASE_URL` | RAG service (recommended) | Lets ownership changes run as atomic Postgres transactions. | unset (REST path) |

Rules:

- **Identity.** The caller's identity comes only from a verified Supabase access token. It is never taken from the
  request body or a client header.
- **Session IDs.** They must be UUIDs. The browser creates them with `crypto.randomUUID()`.
- **Signed-in sessions** are usable only by that user:
  - another account, or an anonymous caller, gets exactly the response for a session that does not exist;
  - this covers query, report, info, download (`/session/{id}/okf`), upload and delete.
- **Anonymous sessions** are usable by whoever holds the session ID, as before. When a signed-in user uploads to an
  anonymous session, it moves to their account.
- **No takeover.** A session owned by one account is never moved to another.
- **Logs** show a session only as a one-way tag (`s:` + 10 hex characters). Secrets, tokens and user IDs are never
  logged. Session paths are also masked in `httpx` request logs.

## 2. Claim verification (`utils/claim_check.py`)

| Variable | Meaning | Default |
|---|---|---|
| `GG_V2_CLAIM_CHECK` | `0` turns the claim check off entirely (not recommended). | `1` |

Each cited sentence gets a record (`meta.claimCheck[].claims`) with:

- type;
- sources;
- method (`word_overlap`, `figures_in_cited_passages`, `arithmetic`, `arithmetic_corrected` or `judge`);
- status: `VERIFIED`, `PARTIALLY_VERIFIED`, `UNVERIFIED`, `CONTRADICTED` or `REQUIRES_REVIEW`;
- confidence;
- explanation;
- correction, where one applies.

Code decides what it safely can. Forecasts, explanations ("because", "reflecting") and claims with a year the
sources never name go to the judge model. If the judge fails, claims stay `REQUIRES_REVIEW` and nothing is removed.

## 3. Report length (`utils/page_policy.py`)

Page bands are defined in code: Detailed 25–30 pages, Deep Research 60–100. They are not environment settings.
`meta.length` says whether a run is within its band and, if it is short, whether the cause is the evidence
(`below_band_evidence`, disclosed rather than padded) or the writing (`below_band_writing`: short or failed
sections, listed by id, with the model that wrote each part).

## 4. Charts

| Variable | Meaning | Default |
|---|---|---|
| `GG_V2_DATASET_EXTRACT` | `1` re-enables model-based dataset extraction (off: charts come from deterministic builders). | off |
| `GG_OFFICIAL_DATA_PLANNER` | `code` uses the deterministic official-data planner instead of the model. | `llm` |
| `GG_OFFICIAL_DATA` | `0` turns off official datasets (World Bank, IMF, OWID, FRED). | `1` |

No settings are needed for the chart-title subject, metadata-table rejection or duplicate detection.

## 5. Source disagreements

| Variable | Meaning | Default |
|---|---|---|
| `GG_V2_DISAGREEMENT_NOTES` | `0` stops the in-section "Sources differ" notes. The methodology list is unaffected. | `1` |

## 6. data.gov.in (`utils/datagov_in.py`)

The connector is off unless both a key and a resource list are set.

| Variable | Meaning | Default |
|---|---|---|
| `DATA_GOV_IN_API_KEY` | The key of a registered data.gov.in account. | unset (off) |
| `GG_DATAGOV_RESOURCES` | JSON list: `[{"id": "<resource id>", "title": "...", "keywords": "solar capacity installed", "unit": "MW", "label_field": "", "value_fields": [], "page_url": ""}]`. `id` and `keywords` are required. | unset (off) |
| `GG_DATAGOV_MAX_RESOURCES` | Resources per report. | 2 detailed / 4 in-depth |
| `GG_DATAGOV_MAX_RECORDS` | Records read per resource (pages of 100). | 500 |
| `GG_DATAGOV_CACHE_HOURS` / `GG_DATAGOV_CACHE_DIR` | Response cache, in `backend/.cache/datagov` (git-ignored). | 24 h |
| `GG_DATAGOV_BASE_URL` | API base. | `https://api.data.gov.in` |

How to find a resource id: open a dataset on data.gov.in, then open its *API* tab. The id is the part after
`/resource/` in the sample URL.

**Confirm once with a real key.** The platform's documentation pages refused automated access, so the client
relies only on the widely used record endpoint (`GET {base}/resource/{id}?api-key=…&format=json&offset=…&limit=…`)
and parses the response defensively. Catalogue keyword search is not used. After setting the key:

1. Run one report on a matching topic.
2. Check the `datagov_in` stage of the research capture for `fetched` or `skipped` reasons.

The key is masked in the httpx logs and is never written to the cache or to source rows.

## 7. Observability

`meta.runSummary` (in the `pipeline_v2` capture stage) and one `V2 run summary:` log line per report record:

- seconds per stage (research and evidence, outline, datasets and charts, section writing, claim checks, executive
  summary, charts from the report, editorial, assembly);
- LLM calls, failures and tokens;
- answers per model;
- HTTP 429 and 503 counts;
- retries, quota waits and fallbacks;
- sources gathered and cited;
- claims by status;
- charts;
- sections that were short or failed;
- words and estimated pages, with the length verdict.

`GG_RESEARCH_CAPTURE=1` additionally saves the full research capture in `backend/diagnostics/`.
