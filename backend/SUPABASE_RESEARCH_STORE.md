# Supabase research store

The canonical research store uses the dedicated PostgreSQL schema `gg_research`.
The backend sets `search_path` to `gg_research, public` for every connection, so older
`public.*` tables from previous project versions cannot shadow the research schema.

Set `RESEARCH_DATABASE_URL` in `backend/.env` to the Supabase Postgres Session Pooler
connection string. The backend creates the schema and all research tables automatically
on startup. No local `research.sqlite3` file is used by the canonical store.

## Persistent web research library

The `gg_research` schema also contains a cross-report web research library:
`web_source_library`, `web_source_versions`, `web_source_chunks`, and `web_search_events`.

Successful Exa/SerpAPI results are persisted in the background. Report source enrichment
also stores richer page text. Later reports and Action/Follow-up edits can retrieve relevant
stored web evidence and reuse it, with a freshness window for current/recent requests.
Persistence is best-effort and never blocks report/chat generation when Supabase is unavailable.
