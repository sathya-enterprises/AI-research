# Growth Gradual

AI research workspace for Indian markets. Ask a question, get a source-linked answer, then turn it
into a **Brief**, **In-Detail** or **Deep Research** report with verified charts, tables, citations
and an editorial PDF export.

- **Frontend:** Next.js 16 + React 19 + TypeScript. Research workspace at `/`; market feeds at
  `/feed`, `/stocks`, `/banks`, `/finance` and `/mutual_funds`; article viewer and data search.
- **Backend:** FastAPI with research pipeline, evidence and chart validation, publication audit,
  and HTML/PDF/DOCX export.
- **RAG service:** FastAPI with FAISS and sentence-transformers retrieval over uploaded documents
  and the OKF knowledge store.
- **Data:** Supabase/Postgres (research store, sessions, Screener.in fundamentals).

---

## Project structure

```
growth-gradual/
├── README.md                     ← you are here
├── .env.example                  shared variables (frontend + backend URLs, LLM keys)
|
├── backend/                      FastAPI app — `uvicorn main:app`
│   ├── main.py                   app + router registration (/api/chat, /api/chat/report, …)
│   ├── routes/                   API routers + their tests (tests live beside the code)
│   │   ├── report.py             research → report pipeline, evidence/chart audits, job worker
│   │   ├── html_report.py        HTML report renderer (also the source of the PDF layout)
│   │   ├── pdf.py                PDF export (Chromium, WeasyPrint fallback) + static SVG charts
│   │   ├── report_design.py      editorial design tokens, fonts, report/PDF stylesheet
│   │   ├── chat.py docx_report.py email.py okf.py rag.py stocks.py datasearch.py knowledge.py
│   │   └── fixtures/             test fixtures (sample PDF, two research captures)
│   ├── utils/                    shared modules (search, extraction, evidence dataset, chart gallery, stores) + tests
│   ├── assets/fonts/             bundled Lora + Inter (OFL) embedded in PDFs — no network font fetch
│   ├── rag-service/              separate FastAPI RAG service (port 7860)
│   ├── screener_kb/              Screener.in fundamentals loader + schema
│   ├── jobs/                     daily Screener → Supabase updater (cron)
│   ├── scripts/                  install_chromium.ps1, check_gemini_keys.py (manual key/model check)
│   ├── diagnostics/              research captures are written here when enabled
│ 
└── frontend/                     Next.js app
    ├── src/app/                  pages + API routes (thin proxies to the backend)
    ├── src/components/           GrowthGradualChat (workspace), feed, auth, market widgets
    ├── src/lib/ src/data/ src/types/ src/contexts/
    ├── public/fonts/             same Lora + Inter, self-hosted for the workspace
    └── scripts/                  node tests + diagnostics (`npm run qa:*`, `npm run diagnose:api`)
```

---

## Requirements

| Tool | Version |
|---|---|
| Python | 3.12 (backend and RAG service) |
| Node.js | 20+ (frontend, Node services) |
| Chromium | via Playwright (`python -m playwright install chromium`), used for PDF export |
| Postgres | Supabase project (research store, sessions, fundamentals) |

WeasyPrint is optional; it's only used as a PDF fallback when Chromium is unavailable. On
Windows, `backend/setup_windows.bat` prints the extra Pango install steps.

---

## Setup

### 1. Environment files (not included; they hold secrets)

Copy each example and fill in your keys:

| File | Copy from | Key variables |
|---|---|---|
| `backend/.env` | `.env.example` + `backend/.env.example` | `GROQ_API_KEYS`, `GEMINI_API_KEY`, `EXA_API_KEY`, `RESEARCH_DATABASE_URL` (or `DATABASE_URL`), `RAG_SERVICE_URL` |
| `frontend/.env.local` | `.env.example` + `frontend/.env.example` | `BACKEND_URL=http://127.0.0.1:8765`, `RAG_SERVICE_URL`, `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY` |
| `backend/rag-service/.env` | `backend/rag-service/.env.example` | `SUPABASE_URL`, `SUPABASE_SECRET_KEY`, `HF_TOKEN` |

`backend/README.md` documents every backend variable.

`RAG_SERVICE_URL` must point to the **same** rag-service in both `backend/.env` and
`frontend/.env.local`. The frontend indexes attachments there, and the backend reads them from
there. Both default to `http://127.0.0.1:7860`.

Before first use, run these once in the Supabase SQL editor:
- `backend/supabase_key_bans.sql` (remembers rate-limited API keys across restarts);
- `backend/supabase_sessions_user_id.sql`.

`supabase_research_store.sql` is applied automatically by the backend at startup.

### 2. Backend

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
uvicorn main:app --host 127.0.0.1 --port 8765 --reload
```

### 3. RAG service

```bash
cd backend/rag-service
pip install -r requirements.txt
python main.py                # http://127.0.0.1:7860
```

### 4. Frontend

```bash
cd frontend
npm install
npm run dev                   # http://127.0.0.1:3000
```

### One-click on Windows

```
START_GROWTH_GRADUAL_WINDOWS.bat
```

This starts the backend (8765), RAG service (7860) and frontend (3000), skipping any port that's
already in use. Check `http://127.0.0.1:3000/api/health`, or run `npm run diagnose:api` in
`frontend/`.

---

## Deploying (Render)

Configure everything in the Render dashboard:

| Service | Type | Root dir | Build / start |
|---|---|---|---|
| Backend | Web service, **Docker** runtime (`backend/Dockerfile` installs Chromium for PDF export) | `backend` | uses the Dockerfile; it listens on `$PORT` |
| RAG service | Web service, Docker (`backend/rag-service/Dockerfile.render`) or Hugging Face Space (`Dockerfile.hf`) | `backend/rag-service` | uses the Dockerfile; on Render also set `RAG_PORT=10000` (it listens on 7860 otherwise) |
| Frontend | Web service, Node | `frontend` | `npm ci && npm run build` / `npm run start` |
| Screener refresh (optional) | Cron job, `30 6 * * *` (12:00 IST) | `backend` | `pip install -r requirements.txt` / `python jobs/update_screener_daily.py --mode daily --from-db` |

Environment variables:
- **Backend:** API keys, `RESEARCH_DATABASE_URL`, and `RAG_SERVICE_URL` set to the RAG service's
  URL.
- **Frontend:** `BACKEND_URL` set to the backend's URL, the same `RAG_SERVICE_URL`, and the
  Supabase public keys.
- **Cron job:** `DATABASE_URL`.

## How a report is produced

1. **Research:** the backend plans searches, fetches publisher pages and extracts tables and
   figures.
2. **Writing:** the report is written by tier (Brief, In-Detail, Deep Research).
3. **Verification:**
   - every cited number is matched to the cited source, with unit scaling for
     lakh/crore/million/billion;
   - every chart point must be backed by source evidence;
   - unsupported values are removed rather than invented.
4. **Publication audit:** the result is one of:
   - **published**;
   - **draft**: generated, but with unresolved findings, so it is labelled and the draft PDF is
     still downloadable;
   - **structural failure**.
5. **Export:**
   - `html_report.py` builds the HTML;
   - `pdf.py` converts its charts to static SVG and prints it with Chromium;
   - fonts are embedded, so export doesn't depend on the network.

The browser starts a job (`/api/chat/report/start`), follows progress over SSE
(`/stream/{jobId}`), and falls back to polling (`/status/{jobId}`) if the stream drops.

---

## Tests

```bash
# backend (from backend/)
pip install -r requirements-dev.txt
pytest routes utils rag-service .

# frontend (from frontend/)
npx tsc --noEmit
npm run qa:test-report-stream-html-fallback
npm run qa:test-report-mode-toggle
npm run qa:test-chat-stream-animation
npm run qa:test-feed-pagination
npm run build
```

Replay tests use the two captures in `backend/routes/fixtures/` and skip themselves if those are
absent. Some older backend tests need optional packages (`weasyprint`) or call functions that no
longer exist; they failed before this cleanup too.

---

## Design system

`backend/routes/report_design.py` is the single source for report and PDF styling:
- **Palette:** warm paper `#FAF9F5`, ink `#2A2A2A`, hairlines `#E1DDD5`, metric tint `#F3EFE6`,
  forest accent `#162721` (thin rails only).
- **Type:** Lora headings and Inter body.
- **Charts:** a fixed, colour-blind-checked palette.

The workspace (`GrowthGradualChat.tsx`, `.chat-shell.gg-premium` styles) uses the same tokens.

---

## Housekeeping notes

- Never commit `.env*` files (other than `.env.example`), `node_modules/`, `.next/`,
  `__pycache__/`, research captures or the runtime caches (`growth_gradual_cache.json`,
  `market_cache.json`, created on first run). `.gitignore` already covers them.
- Research captures are opt-in: set `GG_RESEARCH_CAPTURE=1`; see `backend/diagnostics/README.md`.
- Release notes are in `docs/history/`. `docs/history/CLEANUP_2026-10-21.md` lists the files
  removed in the latest cleanup.
