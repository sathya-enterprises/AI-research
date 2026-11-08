---
title: Paperly RAG Service
emoji: 📄
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
---

# Paperly RAG Service-backend

FastAPI-based RAG (Retrieval-Augmented Generation) service for Paperly.

- Embeds documents using `all-MiniLM-L6-v2`
- Persistent per-session vector storage in Supabase Postgres using pgvector (no local vector index)
- Adaptive similarity thresholding
- Endpoints: `/health`, `/index`, `/query`, `/report`, `/session/{id}`

## Hugging Face research retrieval layer

The RAG service uses `all-MiniLM-L6-v2` to create 384-dimensional embeddings and stores them in Supabase Postgres with pgvector and adds an optional second-stage pretrained Hugging Face cross-encoder reranker for normal Q&A retrieval. The default model is `cross-encoder/ms-marco-MiniLM-L6-v2`.

- `HF_TOKEN`: optional Hugging Face access token; never commit the real value.
- `HF_RERANK_ENABLED=1`: enables reranking (default).
- `HF_RERANK_MODEL`: change the pretrained model without changing code.
- `HF_RERANK_CANDIDATES=24`: number of pgvector candidates passed to the reranker.
- `HF_RERANK_BATCH_SIZE=16`: inference batch size.

The reranker is intentionally **not** applied to full-coverage report retrieval. Reports must retain complete source/chunk coverage; reranking is used to improve precision for targeted Q&A.

Compatibility note: the project pins sentence-transformers 3.0.1. The CrossEncoder loader intentionally does not pass a `token` keyword because that version can reject it with `TypeError`; Hugging Face Hub authentication is still supplied through the `HF_TOKEN` environment variable.

## Persistent pgvector setup (required)

1. In Supabase, enable the `vector` extension.
2. Run `supabase_pgvector.sql` in the Supabase SQL Editor. The migration creates the private `rag_chunks` table, HNSW cosine index, and `rag_match_chunks` RPC.
3. Configure `SUPABASE_URL` and a **server-only** `SUPABASE_SECRET_KEY` (preferred) or legacy `SUPABASE_SERVICE_ROLE_KEY` in `backend/rag-service/.env`. The service also reads `backend/.env` for local development. Do not place server credentials in frontend variables.
4. Restart the RAG service and verify `/health` reports `vector_storage.ready: true`.

The migration expects the 384-dimensional `all-MiniLM-L6-v2` embedding model. If you change `EMBED_MODEL`, update the vector column, RPC argument, and HNSW index dimensions together. Existing sessions stored as OKF documents but not yet in pgvector are re-embedded once on first use and then persisted in Postgres.

Vectors are never written to local FAISS indexes or vector files. Text and chunk metadata may be cached in RAM during a running session; Postgres is the durable source of truth.


## Private OKF Storage

Legacy resources remain readable from `OKF_SUPABASE_BUCKET` using `SUPABASE_ANON_KEY`. New OKF documents and manifests are written to `OKF_PRIVATE_BUCKET` only when the backend has `SUPABASE_SECRET_KEY` configured (preferred), or the legacy `SUPABASE_SERVICE_ROLE_KEY`. Keep either server-side credential in this service's local environment only. Modern `sb_secret_...` credentials are sent as `apikey` and are never sent as bearer tokens; legacy JWT credentials retain the bearer header required by the older Storage API.

The configured bucket name does not prove that the bucket exists or is reachable. After creating `rag-knowledge-private` in the Supabase dashboard and configuring the server credential locally, check Storage access and round-trip a uniquely named temporary diagnostic object with:

```bash
python okf_storage_ops.py probe
```

The probe removes only the temporary object it created. Once it reports `round_trip_pass`, copy the legacy OKF namespace with the resumable, hash-verified, copy-only migration:

```bash
python okf_storage_ops.py migrate
```

The migration copies only `paperly-okf/`, skips destination objects whose hashes already match, stops on conflicting content, verifies source/destination hashes, and never deletes source objects. Review counts before any later cleanup of the old shared bucket. No Storage policies are changed by these commands.
