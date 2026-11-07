# Growth Gradual

AI research workspace. Users can ask a question, attach documents, receive source-linked answers, and create Brief, Detailed or In-Depth research reports with evidence-backed charts, tables, citations and export formats.

## Runtime architecture at a glance

- **Next.js frontend** (`frontend/`): chat/report UI, browser-side file extraction, API adapters and report stream handling.
- **Growth Gradual FastAPI backend** (`backend/main.py`): chat orchestration, search/data tools, report research and synthesis, Supabase persistence, and export API routers.
- **Separate FastAPI RAG service** (`backend/rag-service/`): document chunking, sentence-transformer embeddings, Supabase pgvector indexing/retrieval, optional CrossEncoder reranking, and OKF document archiving.
- **Supabase**: user sessions/messages, the `gg_research` provenance and stored-web-research schema, Screener fundamentals tables, pgvector chunks, and optional Storage buckets.
- **External providers**: Groq (primary planner/writer/streaming provider where configured), Gemini (fallback and vision), Exa/other configured web-search providers, and market-data sources.

The exact request and data flows, route mapping, service boundaries, observability endpoints, deployment topology and known limitations are documented in [`ARCHITECTURE.md`](ARCHITECTURE.md).

## Project structure

```text
ai-research/
├── ARCHITECTURE.md
├── CLEANUP_REPORT.md
├── README.md
├── backend/
│   ├── main.py                 FastAPI application and router registration
│   ├── routes/                 Chat, report, RAG proxy, export and data API routes
│   ├── utils/                  Orchestrator, tools, providers, stores and helpers
│   ├── rag-service/            Independent FastAPI RAG service
│   ├── screener_kb/            Screener fundamentals loader/client
│   ├── jobs/                   Scheduled data refresh jobs
│   ├── scripts/                QA, data and operational scripts
│   └── supabase_research_store.sql  Runtime-applied research-store schema
└── frontend/
    ├── src/app/                UI pages and Next.js API proxy routes
    ├── src/components/         Chat, report and market UI
    ├── src/lib/                Browser/server utilities and API URL config
    ├── public/                 Static assets and self-hosted fonts
    └── scripts/                Frontend QA and diagnostic scripts
```

## Local development

Use Python 3.12 for the backend and RAG service, and Node.js 20 or newer for the frontend. Install dependencies from the retained lock/manifests; this source bundle intentionally excludes `node_modules`, Python virtual environments, build outputs and runtime caches.

### 1. Configure environment

Create local environment files yourself and set the server-side variables described in `backend/README.md` and `backend/rag-service/README.md`. The frontend server needs `BACKEND_URL`; the RAG indexing proxy also reads `RAG_SERVICE_URL`. Keep API keys and Supabase secret/service-role credentials on the server only. Do not commit `.env` files.

### 2. Start the backend

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
uvicorn main:app --host 127.0.0.1 --port 8765 --reload
```

### 3. Start the RAG service

```bash
cd backend/rag-service
pip install -r requirements.txt
python main.py
```

The default local RAG port is `7860`. Set `RAG_SERVICE_URL` in the backend and frontend server environments to the same reachable RAG service.

### 4. Start the frontend

```bash
cd frontend
npm ci
npm run dev
```

The frontend defaults to port `3000`; configure `BACKEND_URL=http://127.0.0.1:8765` for local development.

## Database and already-applied migrations

The owner confirmed that the one-time Supabase migrations for session metadata, API-key bans, Screener fundamentals and RAG pgvector are already applied. Those one-time SQL scripts and diagnostic SQL scripts are therefore not included in this cleaned source archive. The application expects those existing database objects to remain present.

`backend/supabase_research_store.sql` is intentionally retained: `backend/utils/research_store.py` reads and applies this schema during backend startup. Do not delete it without changing that bootstrap code. The live attachment RAG vector schema is separate and lives in Supabase Postgres (`public.rag_chunks` plus `rag_match_chunks`).

## Report lifecycle

The report UI starts a backend job via `/api/chat/report/start`, receives progress and draft previews through `/stream/{jobId}`, and can recover through `/status/{jobId}` polling. The backend performs research, evidence gathering, report drafting and quality gates before returning the structured report payload. Separate export endpoints render HTML, PDF and DOCX; email delivery is also exposed when configured. See `ARCHITECTURE.md` for the full pipeline and current process boundaries.

## Tests and checks

Backend tests are kept beside the route/utility modules and in the RAG service. Frontend QA scripts remain under `frontend/scripts/`. Typical checks:

```bash
# From backend/
pytest routes utils rag-service .

# From frontend/
npx tsc --noEmit
```

The clean ZIP is a source handoff, not a verified deployment image. Install dependencies and run the checks in the target environment before deploying.
