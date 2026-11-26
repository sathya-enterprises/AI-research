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
| `GG_GEMINI_KEY_PROJECTS` | The project of each key, in pool order (`proj-a,proj-a,proj-b`), or JSON `{"<last 6 chars of key>": "<project>"}`. | Unset: every key belongs to one `shared` pool, the safe assumption. |
| `GG_GEMINI_QUOTAS` | JSON limits per project and model: `{"default": {"rpm": 10, "tpm": 250000, "rpd": 250}, "proj-b": {"*": {"rpm": 1000}}}` | Unset: nothing is enforced in advance, and only Google's 429s cool a pool. |
| `GG_GEMINI_DAILY_RESET_HOURS` | How long a pool stays cooled after a per-day-quota 429 when Google gives no delay. | `6` |

How to fill these in:

1. In Google Cloud console → *APIs & Services → Credentials*, note which project each key belongs to.
2. In *Gemini API → Quotas* (or AI Studio → *Usage & billing*), read each project's RPM, TPM and RPD for the
   models in use (`gemini-3-flash-preview`, `gemini-3.6-flash`, the lite models).
3. Set `GG_GEMINI_KEY_PROJECTS` and `GG_GEMINI_QUOTAS` with those numbers.

What the gateway does with them:

- It sends a request only to a pool that has room for its estimated tokens.
- On a 429 it cools the whole project and model pool for the delay Google asks for (the `Retry-After` header or
  the `RetryInfo` in the body), plus jitter, so waiting calls don't all retry in the same second.
- A daily-quota 429 cools the pool for the configured hours.
- A 503 is upstream overload: the call retries after Google's delay, if one is given.
- A fallback (Groq) answer shorter than half the requested words is held back while Gemini is retried, and is
  used only if Gemini never answers.

Other gateway settings (existing):

- `GG_V2_SECTION_CONCURRENCY` (default 5): parallel calls.
- `GG_V2_HEDGE=1`: hedged calls (off by default, because hedging added 429/503s in live runs).
- `GG_V2_LLM_CALL_BUDGET_DETAILED` / `_IN_DEPTH`: call budgets per report.

Logs and telemetry show at most the last 4 characters of a key.

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
