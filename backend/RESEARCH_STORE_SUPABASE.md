# Supabase Research Store

Growth Gradual now uses **Supabase Postgres as the canonical research provenance store** instead of the local SQLite `backend/data/research.sqlite3` file.

## Required environment variable

Set this in `backend/.env` (server-side only):

```env
RESEARCH_DATABASE_URL=postgresql://postgres.<project-ref>:<password>@<pooler-host>:5432/postgres
```

A long-running FastAPI backend can use the Supabase **Session Pooler** connection (port 5432). Supabase's connection guidance recommends direct connections for persistent backends when IPv6/IPv4 requirements permit, and the Session Pooler is the IPv4-compatible alternative for persistent backends. Keep the password in the backend environment; do not expose it to Next.js or the browser.

The existing `DATABASE_URL` is also accepted as a fallback, so an existing Supabase/Postgres connection used by the Screener knowledge base can be reused without adding another variable.

## Schema

The backend automatically executes `backend/supabase_research_store.sql` on first startup. It is safe to run that SQL manually in the Supabase SQL Editor as well.

The store contains:

- research runs and lifecycle state
- source registry and immutable source versions
- documents and chunks
- evidence assets
- claims and claim/evidence relationships
- dimension retrieval telemetry
- provider calls and stage events
- targeted refresh tasks
- report revisions and artifacts

## What changes from SQLite

The canonical research state is no longer stored in `backend/data/research.sqlite3`. No SQLite corruption/quarantine path is required for normal operation, and multiple backend processes can share the same research database.

FAISS remains an acceleration layer; it is not the canonical provenance database.

## Existing corrupted SQLite

The previously reported local database is malformed and Windows has it locked. The Supabase migration does not require that file for a fresh research run. Keep the old SQLite file as forensic backup until you no longer need it; do not copy it into the new runtime.

If historical SQLite data must be preserved later, migrate only from a verified healthy copy into the Supabase schema rather than making the corrupted file part of the live runtime.
