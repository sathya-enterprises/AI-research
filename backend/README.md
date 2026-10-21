# Growth Gradual — Python FastAPI Backend

Pure-Python backend replacing all Next.js API routes. No browser automation, no Node.js dependencies.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/chat` | SSE streaming chat (Groq → Gemini fallback + Exa web search) |
| POST | `/api/chat/report` | Research report generation (JSON) |
| POST | `/api/chat/report/pdf` | PDF export via the branded HTML/print renderer |
| POST | `/api/chat/report/docx` | Editable Word export: native text, headings, tables, source hyperlinks, and supported charts with embedded Excel data |
| GET | `/api/stocks/search` | Search the Screener.in fundamentals knowledge base by ticker/name |
| GET | `/api/stocks/{company_id}` | Full fundamentals snapshot for a company |
| POST | `/api/datasearch` | Free-text query → structured data points for the frontend Data Search Engine (`/search`) |
| GET | `/health` | Health check |

## Environment Variables

| Variable | Description |
|----------|-------------|
| `GROQ_API_KEYS` | Comma/semicolon/newline-separated Groq API keys; shared 60-second per-key cooldown rotates requests across eligible keys |
| `EXA_API_KEY` | First Exa Search API key (backend only) |
| `EXA_API_KEY_2` | Optional second Exa API key |
| `EXA_API_KEY_3` | Optional third Exa API key |
| `EXA_API_KEYS` | Optional comma/semicolon/newline-separated additional Exa API keys |
| `GROQ_KEY_COOLDOWN_MS` | Optional shared per-key cooldown in milliseconds; defaults to `60000` (one minute). |
| `WEB_SEARCH_PROVIDER` | Search provider selector; defaults to `exa` |
| `GEMINI_API_KEY` | Comma-separated Gemini API keys |
| `DATAWRAPPER_API_TOKEN` | Datawrapper API token (Settings → API Tokens). Powers the report's bar/line/pie charts in both the chat view and PDF export. If unset, charts fall back to the built-in lightweight SVG renderer. |
| `LOGO_B64` | (Optional) Base64-encoded logo image for PDF cover |
| `SUPABASE_URL` / `SUPABASE_ANON_KEY` | Also powers the Screener.in fundamentals knowledge base (see `screener_kb/README.md`) unless overridden below |
| `ARTICLE_KB_INGEST_TOKEN` | Optional shared server-to-server secret for authenticated website-crawl ingestion. Set the same random value on the frontend server and backend; never prefix with `NEXT_PUBLIC_`. |
| `SCREENER_SUPABASE_URL` / `SCREENER_SUPABASE_ANON_KEY` | (Optional) Point the fundamentals knowledge base at a separate Supabase project instead of reusing `SUPABASE_URL`/`SUPABASE_ANON_KEY` |

Website crawl records are stored in the existing `gg_research` PostgreSQL research library (`web_source_library`, `web_source_versions`, and related chunk/event tables), not in the Storage bucket and not in the stock-company tables.

For background crawl ingestion, set `ARTICLE_KB_INGEST_TOKEN` to the same strong random value in `backend/.env` and `frontend/.env.local` (and as an environment variable on both deployed services). The frontend uses it only in server-side API code via `X-Article-KB-Token`; it is never exposed through a `NEXT_PUBLIC_` variable. If it is not set, the existing authenticated Supabase-user flow remains available.

See `screener_kb/README.md` for how to load the fundamentals knowledge base (Screener.in workbooks → Postgres) and how chat/report use it.

## Local Development

```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload --port 10000
```

## Render Deployment

1. Create a new **Web Service** in Render, point to the `backend/` folder.
2. Build command: `pip install -r requirements.txt`
3. Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
4. Add environment variables above.
5. After deploy, copy the service URL (e.g. `https://growth-gradual-backend.onrender.com`).

## Frontend Integration

In your **frontend** Render service (or `.env.local`), set:

```
BACKEND_URL=https://growth-gradual-backend.onrender.com
```

The Next.js API routes (`/api/chat`, `/api/chat/report`, `/api/chat/report/pdf`, and `/api/chat/report/docx`) are thin proxies — they forward all requests to this backend unchanged.

## PDF Notes

PDF generation uses **ReportLab** (pure Python). Features:
- Branded cover page with logo, domain tag, key stats
- Full report body with headings, paragraphs, bullet lists, tables
- Bar / line / pie charts rendered natively (no browser needed)
- Footer with disclaimer on every page

To include your logo, either:
- Place `logo.jpg` / `logo.png` in the working directory, **or**
- Set `LOGO_B64` env var to the base64-encoded image bytes.

## Editable Word Reports

The `POST /api/chat/report/docx` endpoint creates a native Office Open XML document. Text, headings, lists, citations, source appendix, key-stat cells, report tables and chart-data tables are editable. Supported chart types are real Word chart objects linked to embedded Excel workbooks; unsupported chart types keep their supplied data in editable Word tables rather than being converted to a screenshot. Supplied hydrated images are ordinary replaceable Word images.

Install `backend/requirements.txt` after updating the project so `python-docx` and `openpyxl` are present. In the frontend, use **Download Editable Word** from the report panel.
