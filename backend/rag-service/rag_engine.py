"""
Paperly RAG Engine v2
=====================
Architecture:
  Upload → Chunk (RecursiveCharacterTextSplitter) → Embed (sentence-transformers)
         → Store (Supabase Postgres pgvector, per-session) → Query → Adaptive Retrieval
         → Grounded Prompt → LLM (hallucination-free answer)

Fixes in v2:
  - Dedup by doc ID not hash (re-upload after delete works)
  - Adaptive threshold (0.15 → 0.09 → 0.05 → 0.0)
  - Query expansion for vague queries
  - Report mode: full-coverage retrieval with report-specific prompt
  - Session cleanup on delete
"""

import os
import re
import logging
import threading
import asyncio
import hashlib
from typing import List, Dict, Any, Tuple

import numpy as np
from sentence_transformers import SentenceTransformer, CrossEncoder
from langchain_text_splitters import RecursiveCharacterTextSplitter
from sheet_schema import extract_sheet_schema_chunks, matching_schema_chunk_indices

import okf_store
import pgvector_store

log = logging.getLogger("rag-engine")

MODEL_NAME          = os.getenv("EMBED_MODEL",            "all-MiniLM-L6-v2")
CHUNK_SIZE          = int(os.getenv("CHUNK_SIZE",         "1000"))
CHUNK_OVERLAP       = int(os.getenv("CHUNK_OVERLAP",      "150"))
RELEVANCE_THRESHOLD = float(os.getenv("RELEVANCE_THRESHOLD", "0.15"))

# Optional Hugging Face cross-encoder reranker. It is deliberately applied only
# to query retrieval (not full-coverage report retrieval), so report generation
# never loses source coverage. The model is pretrained; no fine-tuning is
# required. Disable with HF_RERANK_ENABLED=0 if a deployment is resource
# constrained.
HF_TOKEN = os.getenv("HF_TOKEN", "").strip()
HF_RERANK_ENABLED = os.getenv("HF_RERANK_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}
HF_RERANK_MODEL = os.getenv("HF_RERANK_MODEL", "cross-encoder/ms-marco-MiniLM-L6-v2").strip()
HF_RERANK_CANDIDATES = max(4, int(os.getenv("HF_RERANK_CANDIDATES", "24")))
HF_RERANK_BATCH_SIZE = max(1, int(os.getenv("HF_RERANK_BATCH_SIZE", "16")))




class SessionIndex:
    """Session metadata/text cache. Embeddings live only in Postgres pgvector."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.chunks: List[Dict[str, Any]] = []
        self.doc_ids: set = set()
        self.doc_names: Dict[str, str] = {}
        self.doc_hashes: Dict[str, str] = {}
        self.okf_concepts: List[Dict[str, str]] = []
        self.lock = threading.RLock()
        self._entities: set = set()

    def load_chunks(self, chunks: List[Dict[str, Any]]):
        """Replace the local text/metadata cache from durable pgvector rows."""
        with self.lock:
            self.chunks = sorted(
                (dict(c) for c in chunks),
                key=lambda c: (str(c.get("doc_id") or ""), int(c.get("chunk_index") or 0), str(c.get("chunk_id") or "")),
            )
            self.doc_ids.clear()
            self.doc_names.clear()
            self.doc_hashes.clear()
            self.okf_concepts.clear()
            self._entities.clear()
            seen_concepts = set()
            for chunk in self.chunks:
                did = str(chunk.get("doc_id") or "")
                name = str(chunk.get("source_name") or "Unknown")
                if did:
                    self.doc_ids.add(did)
                    self.doc_names[did] = name
                    if chunk.get("source_sha256"):
                        self.doc_hashes[did] = str(chunk["source_sha256"])
                self._entities.update(RAGEngine._extract_entities_from_text(str(chunk.get("text") or "")))
                metadata = chunk.get("source_metadata") if isinstance(chunk.get("source_metadata"), dict) else {}
                rel_path = str(metadata.get("okf_path") or chunk.get("original_location") or "")
                if rel_path and rel_path not in seen_concepts:
                    seen_concepts.add(rel_path)
                    self.okf_concepts.append({"name": name, "rel_path": rel_path})

    def add(self, chunks: List[Dict[str, Any]]):
        """Merge chunk metadata into the cache; vector values are never cached."""
        with self.lock:
            by_id = {str(c.get("chunk_id")): c for c in self.chunks if c.get("chunk_id")}
            for chunk in chunks:
                clean = {k: v for k, v in chunk.items() if k != "embedding"}
                by_id[str(clean.get("chunk_id"))] = clean
            self.load_chunks(list(by_id.values()))

    def remove_doc(self, doc_id: str):
        with self.lock:
            remaining = [c for c in self.chunks if str(c.get("doc_id") or "") != doc_id]
            self.load_chunks(remaining)

    def search(self, q_vec: np.ndarray, k: int) -> Tuple[np.ndarray, np.ndarray]:
        """Query Postgres similarity search and map results to cached chunk indices."""
        if not self.chunks or k <= 0:
            return np.array([]), np.array([])
        query_vector = np.asarray(q_vec, dtype=np.float32).reshape(-1).tolist()
        matches = pgvector_store.search_chunks(self.session_id, query_vector, k)
        by_chunk_id = {str(c.get("chunk_id")): i for i, c in enumerate(self.chunks)}
        scores, indices = [], []
        for match in matches:
            idx = by_chunk_id.get(str(match.get("chunk_id") or ""))
            if idx is None:
                continue
            scores.append(float(match.get("score") or 0.0))
            indices.append(idx)
        return np.asarray(scores, dtype=np.float32), np.asarray(indices, dtype=np.int64)

    @property
    def total(self) -> int:
        return len(self.chunks)

    def has_doc(self, doc_id: str) -> bool:
        return bool(doc_id) and doc_id in self.doc_ids

    def register_doc(self, doc_id: str, name: str):
        if doc_id:
            self.doc_ids.add(doc_id)
            self.doc_names[doc_id] = name

    def register_entities(self, entities: set):
        self._entities.update(entities)

    @property
    def entities(self) -> set:
        return self._entities


class RAGEngine:
    """
    Manages persistent pgvector embeddings with a lightweight in-process text/metadata cache.
    Full pipeline: chunk → embed → store in Postgres → retrieve → grounded prompt.
    """

    def __init__(self):
        log.info(f"Loading embedding model: {MODEL_NAME} ...")
        self._model    = SentenceTransformer(MODEL_NAME, token=HF_TOKEN or None)
        self._dim      = self._model.get_sentence_embedding_dimension()
        self._reranker = None
        if HF_RERANK_ENABLED:
            try:
                log.info("Loading Hugging Face reranker: %s ...", HF_RERANK_MODEL)
                # sentence-transformers 3.0.x does not accept ``token`` on
                # CrossEncoder even though newer releases do.  The HF Hub
                # client already reads HF_TOKEN from the environment, so keep
                # the loader compatible with the pinned 3.0.1 dependency.
                self._reranker = CrossEncoder(
                    HF_RERANK_MODEL,
                    max_length=512,
                )
                log.info("Hugging Face reranker ready — model=%s", HF_RERANK_MODEL)
            except Exception as exc:
                # Reranking is an enhancement, never a reason for the RAG
                # service to fail. Retrieval continues with pgvector similarity scores.
                self._reranker = None
                log.warning("Hugging Face reranker unavailable; using pgvector only: %s", type(exc).__name__)
        self._sessions: Dict[str, SessionIndex] = {}
        self._lock     = threading.Lock()
        self._restore_locks: Dict[str, asyncio.Lock] = {}
        self._restore_states: Dict[str, str] = {}
        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=CHUNK_SIZE,
            chunk_overlap=CHUNK_OVERLAP,
            separators=["\n\n", "\n", ". ", "! ", "? ", ", ", " ", ""],
            length_function=len,
        )
        log.info(f"✅ RAG Engine ready — model={MODEL_NAME} dim={self._dim}")

    @property
    def model_name(self) -> str:
        return MODEL_NAME

    # ── Session management ──────────────────────────────────────

    def _sess(self, sid: str) -> SessionIndex:
        with self._lock:
            if sid not in self._sessions:
                self._sessions[sid] = SessionIndex(sid)
            return self._sessions[sid]

    def list_sessions(self) -> List[str]:
        with self._lock:
            return list(self._sessions.keys())

    def get_session_info(self, sid: str) -> Dict[str, Any]:
        s = self._sessions.get(sid)
        if not s:
            return {"session_id": sid, "chunk_count": 0, "exists": False}
        return {
            "session_id":  sid,
            "chunk_count": s.total,
            "embedding_model": MODEL_NAME,
            "vector_store": "supabase_pgvector",
            "hf_rerank_enabled": self._reranker is not None,
            "hf_rerank_model": HF_RERANK_MODEL if self._reranker is not None else None,
            "doc_count":   len(s.doc_ids),
            "restore_status": self._restore_states.get(sid, "not_loaded"),
            "exists":      True,
        }

    async def ensure_session_loaded(self, sid: str) -> str:
        """Load cached chunk metadata from pgvector; migrate legacy OKF docs on demand."""
        current = self._sessions.get(sid)
        if current and current.total:
            return self._restore_states.get(sid, "in_memory_metadata_cache")
        with self._lock:
            restore_lock = self._restore_locks.setdefault(sid, asyncio.Lock())
        async with restore_lock:
            current = self._sessions.get(sid)
            if current and current.total:
                return self._restore_states.get(sid, "in_memory_metadata_cache")
            try:
                chunks = await asyncio.to_thread(pgvector_store.get_session_chunks, sid)
                session = self._sess(sid)
                if chunks:
                    session.load_chunks(chunks)
                    self._restore_states[sid] = "restored_from_pgvector"
                    log.info("Loaded pgvector session metadata: session=%s chunks=%d", sid[:8], len(chunks))
                    return self._restore_states[sid]

                # One-time migration path for previously saved OKF documents whose
                # embeddings previously existed only in transient RAM. Re-embed
                # and persist them once; subsequent starts load vectors from Postgres.
                documents = await okf_store.load_session_documents(sid)
                if not documents:
                    self._restore_states[sid] = "empty_or_unavailable"
                    return self._restore_states[sid]
                result = await self._index_documents(sid, documents, persist=False)
                state = "migrated_okf_to_pgvector" if result.get("chunks_added", 0) else "empty_or_unavailable"
                self._restore_states[sid] = state
                if state == "migrated_okf_to_pgvector":
                    log.info("Migrated legacy OKF session to pgvector: session=%s docs=%d chunks=%d", sid[:8], len(documents), result["chunks_added"])
                return self._restore_states[sid]
            except Exception as exc:
                self._restore_states[sid] = "restore_failed"
                log.error("pgvector session load failed: session=%s error=%s", sid[:8], str(exc)[:300])
                raise RuntimeError(f"RAG pgvector storage is unavailable: {exc}") from exc

    async def delete_session(self, sid: str):
        """Delete persistent vector rows for a cleared session; retain OKF originals."""
        await asyncio.to_thread(pgvector_store.delete_session, sid)
        with self._lock:
            self._sessions.pop(sid, None)
            self._restore_states.pop(sid, None)
            self._restore_locks.pop(sid, None)
        log.info("Deleted pgvector rows and cleared metadata cache for session %s; OKF originals retained", sid[:8])

    # ── Indexing ────────────────────────────────────────────────

    async def index(self, session_id: str, documents: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Index documents. Idempotent per document id and content hash."""
        await self.ensure_session_loaded(session_id)
        return await self._index_documents(session_id, documents, persist=True)

    async def _index_documents(self, session_id: str, documents: List[Dict[str, Any]], *, persist: bool) -> Dict[str, Any]:
        sess = self._sess(session_id)
        chunks_added = 0
        persistence_attempted = 0
        persistence_succeeded = 0

        for doc in documents:
            doc_id = str(doc.get("id", ""))
            name = str(doc.get("name") or "Unknown")
            text = (doc.get("text") or "").strip()
            if not text or len(text) < 20:
                log.warning("Skipping empty document (id omitted)")
                continue

            metadata = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
            source_hash = str(metadata.get("source_sha256") or hashlib.sha256(text.encode("utf-8")).hexdigest())
            sheet_schemas = extract_sheet_schema_chunks(text)
            if sess.has_doc(doc_id):
                if sess.doc_hashes.get(doc_id) == source_hash:
                    existing_schema_sheets = {
                        str((c.get("source_metadata") or {}).get("sheet_name") or "")
                        for c in sess.chunks
                        if str(c.get("doc_id") or "") == doc_id
                        and (c.get("source_metadata") or {}).get("rag_chunk_kind") == "sheet_schema"
                    }
                    required_schema_sheets = {str(item["sheet_name"]) for item in sheet_schemas}
                    # Existing sessions may predate structured sheet-schema chunks. Reindex
                    # once on re-upload so schema questions can be answered deterministically.
                    if not required_schema_sheets or required_schema_sheets.issubset(existing_schema_sheets):
                        continue
                    log.info(
                        "Rebuilding workbook schema chunks for existing document: missing_sheets=%d",
                        len(required_schema_sheets - existing_schema_sheets),
                    )
                # Replaced document or one-time schema migration: remove old vectors from
                # the persistent store before inserting the complete chunk set. The OKF
                # source archive is preserved.
                await asyncio.to_thread(pgvector_store.delete_document, session_id, doc_id)
                sess.remove_doc(doc_id)

            raw_chunks = self._splitter.split_text(text)
            chunk_specs = [(chunk, None) for chunk in raw_chunks]
            # Append one complete, small schema chunk per workbook sheet. These records
            # preserve all column names together even when ordinary text chunks cross a
            # sheet boundary.
            chunk_specs.extend((item["text"], item) for item in sheet_schemas)
            if not chunk_specs:
                continue
            total_chunk_count = len(chunk_specs)
            metas = []
            for i, (chunk, schema) in enumerate(chunk_specs):
                chunk_metadata = dict(metadata)
                if schema is not None:
                    chunk_metadata.update({
                        "rag_chunk_kind": "sheet_schema",
                        "sheet_name": schema["sheet_name"],
                        "column_count": len(schema["columns"]),
                        "column_names": schema["columns"],
                    })
                metas.append({
                    "text": chunk,
                    "source_name": name,
                    "source_type": doc.get("source_type", "file"),
                    "file_type": doc.get("file_type", ""),
                    "doc_id": doc_id,
                    "chunk_index": i,
                    "total_chunks": total_chunk_count,
                    "chunk_id": hashlib.sha256(f"{doc_id}:{source_hash}:{i}".encode("utf-8")).hexdigest()[:24],
                    "source_sha256": source_hash,
                    "original_location": metadata.get("original_location") or metadata.get("okf_path"),
                    "source_metadata": chunk_metadata,
                })

            vecs = await asyncio.to_thread(self._embed, [m["text"] for m in metas])
            store_rows = []
            for meta, vector in zip(metas, vecs):
                store_rows.append({
                    "session_id": session_id,
                    **meta,
                    "embedding": [float(value) for value in vector.tolist()],
                })
            await asyncio.to_thread(pgvector_store.upsert_chunks, store_rows)

            # Keep only text/metadata in memory. Do not retain vector arrays locally.
            sess.add(metas)
            sess.register_doc(doc_id, name)
            sess.doc_hashes[doc_id] = source_hash
            sess.register_entities(RAGEngine._extract_entities_from_text(text))
            chunks_added += len(metas)
            log.info(
                "Indexed document in pgvector: chunks=%d raw_chunks=%d sheet_schema_chunks=%d source_type=%s",
                len(metas), len(raw_chunks), len(sheet_schemas), str(doc.get("source_type") or "file"),
            )

            # Keep the existing inspectable OKF bundle as the source document archive.
            # Embeddings themselves are no longer stored in a local index or files.
            if persist:
                persistence_attempted += 1
                rel_path = await okf_store.write_document_concept(session_id, doc)
                if rel_path:
                    persistence_succeeded += 1
                    sess.okf_concepts = [c for c in sess.okf_concepts if c.get("rel_path") != rel_path]
                    sess.okf_concepts.append({"name": name, "rel_path": rel_path})
            else:
                rel_path = str(metadata.get("okf_path") or "")
                if rel_path:
                    sess.okf_concepts = [c for c in sess.okf_concepts if c.get("rel_path") != rel_path]
                    sess.okf_concepts.append({"name": name, "rel_path": rel_path})

        manifest_persisted = None
        if chunks_added > 0 and persist:
            manifest_persisted = await okf_store.write_bundle_manifest(session_id, sess.okf_concepts)

        result = {"chunks_added": chunks_added, "total_chunks": sess.total, "vector_store": "supabase_pgvector"}
        if persist:
            storage = okf_store.storage_status()
            if not storage.get("writeAvailable"):
                persistence_state = "disabled"
            elif persistence_attempted == 0:
                persistence_state = "unchanged"
            elif persistence_succeeded == persistence_attempted and manifest_persisted is not False:
                persistence_state = "persisted"
            else:
                persistence_state = "partial_or_failed"
            result["persistence"] = {
                "status": persistence_state,
                "mode": storage.get("mode"),
                "documentsAttempted": persistence_attempted,
                "documentsPersisted": persistence_succeeded,
                "bundleManifestPersisted": manifest_persisted,
            }
        return result

    # ── Q&A Retrieval ───────────────────────────────────────────

    def retrieve(
        self,
        session_id: str,
        question:   str,
        top_k:      int   = 8,
        min_score:  float = RELEVANCE_THRESHOLD,
    ) -> Dict[str, Any]:
        """Retrieve relevant chunks for a Q&A query with adaptive thresholding."""
        sess = self._sessions.get(session_id)
        if not sess or sess.total == 0:
            return self._no_content(question, "no_index", "No documents indexed for this session.")

        forced_schema_indices = matching_schema_chunk_indices(question, sess.chunks)
        scope_info = self._detect_scope(question, sess)
        if forced_schema_indices:
            # A named-sheet header question has an exact structured answer; do not
            # let a broad semantic query or reranker discard its dedicated schema.
            scope_info = {
                "scope": "specific",
                "entity": str((sess.chunks[forced_schema_indices[0]].get("source_metadata") or {}).get("sheet_name") or ""),
                "top_k_hint": max(8, min(15, top_k or 8)),
                "schema_query": True,
            }
        # Always let scope detection drive top_k — caller's value is just a floor
        # top_k=0 means "let scope detection fully decide"
        top_k = scope_info["top_k_hint"] if top_k == 0 else max(top_k, scope_info["top_k_hint"])
        # General queries: always use zero threshold so we pull wide coverage
        if scope_info["scope"] == "general":
            min_score = 0.0

        expanded = self._expand_query(question, sess)
        q_vec    = self._embed([expanded])
        scores, indices = sess.search(q_vec, min(top_k * 2, sess.total))

        if len(scores) == 0:
            return self._no_content(question, "no_results", "Search returned no results.")

        # Adaptive threshold
        good, used_thr = [], min_score
        for thr in [min_score, min_score * 0.6, min_score * 0.35, 0.0]:
            good = [
                (float(scores[i]), int(indices[i]))
                for i in range(len(scores))
                if scores[i] >= thr and 0 <= int(indices[i]) < len(sess.chunks)
            ]
            if forced_schema_indices:
                forced_set = set(forced_schema_indices)
                good = [(1.0, idx) for idx in forced_schema_indices] + [
                    item for item in good if item[1] not in forced_set
                ]
            if good:
                used_thr = thr
                break

        if not good:
            best = float(scores[0]) if len(scores) else 0
            return self._no_content(
                question, "low_relevance",
                f"No relevant content found (best score: {best:.3f}). "
                "The documents may not contain information about this topic."
            )

        if used_thr < min_score:
            log.info(f"Adaptive threshold: {used_thr:.3f} (base: {min_score:.3f})")

        # First-stage retrieval comes from Postgres pgvector. For normal Q&A, rerank a
        # bounded candidate set with a pretrained Hugging Face cross-encoder.
        # This improves precision without changing the report's full-coverage
        # retrieval contract.
        candidate_pairs = []
        candidate_items = []
        rerank_ran = False
        for score, idx in good[:max(top_k, HF_RERANK_CANDIDATES)]:
            chunk = sess.chunks[idx]
            candidate_items.append((float(score), int(idx), chunk))
            candidate_pairs.append((question, chunk["text"]))

        if self._reranker is not None and candidate_pairs:
            try:
                rr_scores = self._reranker.predict(
                    candidate_pairs,
                    batch_size=HF_RERANK_BATCH_SIZE,
                    show_progress_bar=False,
                )
                rerank_ran = True
                ranked = sorted(
                    zip(rr_scores, candidate_items),
                    key=lambda x: float(x[0]),
                    reverse=True,
                )
                candidate_items = [item for _, item in ranked]
                log.info("HF rerank: %d candidates → %d results model=%s",
                         len(candidate_pairs), min(top_k, len(candidate_items)), HF_RERANK_MODEL)
            except Exception as exc:
                log.warning("HF rerank failed; retaining pgvector ordering: %s", type(exc).__name__)

        seen, retrieved = set(), []
        # Guarantee the exact schema for a named-sheet header question is included,
        # independent of semantic ordering or cross-encoder preferences.
        for idx in forced_schema_indices:
            if 0 <= idx < len(sess.chunks):
                chunk = sess.chunks[idx]
                sig = chunk["text"][:80]
                if sig not in seen:
                    seen.add(sig)
                    retrieved.append({**chunk, "score": 1.0})
        for score, idx, chunk in candidate_items:
            if idx in forced_schema_indices:
                continue
            sig = chunk["text"][:80]
            if sig not in seen:
                seen.add(sig)
                retrieved.append({**chunk, "score": score})
            if len(retrieved) >= top_k:
                break
        retrieved = retrieved[:top_k]

        source_files = list(dict.fromkeys(c["source_name"] for c in retrieved))
        context      = self._fmt_context(retrieved)
        prompt       = self._qa_prompt(context, source_files, question, scope_info)

        log.info(f"Q&A: {len(retrieved)} chunks from {len(source_files)} source(s) scope={scope_info['scope']} (thr={used_thr:.3f})")
        return {
            "context":       context,
            "system_prompt": prompt,
            "retrieved":     len(retrieved),
            "source_files":  source_files,
                "evidence":      self._evidence_records(retrieved),
            "reranker_ran":  rerank_ran,
            "has_content":   True,
        }

    # ── Report Retrieval ────────────────────────────────────────

    def retrieve_for_report(
        self,
        session_id:  str,
        report_spec: str,
        report_type: str = "comprehensive",
    ) -> Dict[str, Any]:
        """
        Query-relevant retrieval for reports. Full-coverage means all matching
        evidence across relevant documents, not every chunk in the session.
        """
        sess = self._sessions.get(session_id)
        if not sess or sess.total == 0:
            return self._no_content(report_spec, "no_index",
                "No documents indexed. Upload files before generating a report.")

        query = self._expand_query(report_spec, sess)
        query_vector = self._embed([query])
        candidate_count = min(sess.total, max(HF_RERANK_CANDIDATES * 2, 24))
        scores, indices = sess.search(query_vector, candidate_count)
        if len(scores) == 0:
            return self._no_content(report_spec, "no_results", "The saved resources returned no usable evidence.")

        threshold = max(0.04, min(RELEVANCE_THRESHOLD, 0.12))
        candidates = [
            (float(scores[i]), int(indices[i]), sess.chunks[int(indices[i])])
            for i in range(len(scores))
            if 0 <= int(indices[i]) < len(sess.chunks) and float(scores[i]) >= threshold
        ]
        if not candidates:
            return self._no_content(report_spec, "low_relevance", "No saved OKF evidence was sufficiently relevant to this report request.")

        rerank_ran = False
        if self._reranker is not None and candidates:
            try:
                pairs = [(report_spec, chunk["text"]) for _, _, chunk in candidates]
                scores2 = self._reranker.predict(pairs, batch_size=HF_RERANK_BATCH_SIZE, show_progress_bar=False)
                candidates = [item for _, item in sorted(zip(scores2, candidates), key=lambda pair: float(pair[0]), reverse=True)]
                rerank_ran = True
            except Exception as exc:
                log.warning("Report reranking unavailable; retaining embedding order (%s)", type(exc).__name__)

        seen, retrieved, per_source = set(), [], {}
        for score, idx, chunk in candidates:
            signature = hashlib.sha256(chunk["text"].encode("utf-8")).hexdigest()
            if signature in seen:
                continue
            seen.add(signature)
            source_key = str(chunk.get("doc_id") or chunk.get("source_name") or "")
            if per_source.get(source_key, 0) >= 8:
                continue
            retrieved.append({**chunk, "score": score})
            per_source[source_key] = per_source.get(source_key, 0) + 1
            if len(retrieved) >= 24:
                break

        source_files = list(dict.fromkeys(c["source_name"] for c in retrieved))

        # Build context (larger limit for reports)
        context = self._fmt_context_report(retrieved, max_chars=150000)
        prompt  = self._report_prompt(context, source_files, report_spec, report_type)

        log.info("Report retrieval: chunks=%d sources=%d reranker_ran=%s", len(retrieved), len(source_files), rerank_ran)
        return {
            "context":       context,
            "system_prompt": prompt,
            "retrieved":     len(retrieved),
            "source_files":  source_files,
            "evidence":      self._evidence_records(retrieved),
            "reranker_ran":  rerank_ran,
            "has_content":   True,
        }

    @staticmethod
    def _evidence_records(chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Return structured provenance for the trusted backend report pipeline."""
        return [{
            "text": str(c.get("text") or ""),
            "sourceName": str(c.get("source_name") or "Uploaded OKF document"),
            "sourceType": str(c.get("source_type") or "uploaded_document"),
            "fileType": str(c.get("file_type") or ""),
            "documentId": str(c.get("doc_id") or ""),
            "chunkId": str(c.get("chunk_id") or ""),
            "chunkIndex": int(c.get("chunk_index") or 0),
            "sourceSha256": str(c.get("source_sha256") or ""),
            "originalLocation": c.get("original_location"),
            "metadata": dict(c.get("source_metadata") or {}),
            "score": float(c.get("score") or 0),
        } for c in chunks if str(c.get("text") or "").strip()]

    # ── Scope detection ──────────────────────────────────────────

    def _detect_scope(self, question: str, sess: SessionIndex) -> Dict[str, Any]:
        """
        Determine whether the question is:
          - "specific": targets one named entity (fund, person, section…)
          - "general":  asks about everything / all entities in the docs

        Entity matching is 100% dynamic — derived from what was actually
        indexed in this session (via _extract_entities_from_text).
        No hardcoded names.

        Returns a dict with:
          scope       "specific" | "general"
          entity      the matched entity string (if specific), or None
          top_k_hint  suggested top_k for retrieval
        """
        q = question.strip().lower()

        schema_indices = matching_schema_chunk_indices(question, sess.chunks)
        if schema_indices:
            metadata = sess.chunks[schema_indices[0]].get("source_metadata") or {}
            return {
                "scope": "specific",
                "entity": str(metadata.get("sheet_name") or ""),
                "top_k_hint": 8,
                "schema_query": True,
            }

        # ── Step 1: Match against DYNAMICALLY extracted entities (highest priority) ──
        # Sort longest-first so "helios flexi cap fund" beats "flexi cap"
        known_entities = sorted(sess.entities, key=len, reverse=True)
        matched_entity = None
        for entity in known_entities:
            if entity in q:
                matched_entity = entity
                break

        if matched_entity:
            return {
                "scope":      "specific",
                "entity":     matched_entity,
                "top_k_hint": 15,
            }

        # ── Step 2: General/all-entity signals ──
        general_signals = [
            "all funds", "each fund", "every fund", "all schemes",
            "compare all", "comparison of all", "overview of all", "list all",
            "summarize all", "summarise all",
            "across all", "for all funds", "all the funds",
        ]
        vague_signals = [
            "explain this", "summarize this", "summarise this",
            "what is this", "describe this", "tell me about this",
            "what does this say", "what is in this",
            "what does this document", "what does this file",
        ]

        if any(s in q for s in general_signals):
            return {"scope": "general", "entity": None, "top_k_hint": 30}

        # Short/vague queries with no entity → general sweep
        if len(q.split()) <= 5 or any(p in q for p in vague_signals):
            return {"scope": "general", "entity": None, "top_k_hint": 30}

        # ── Step 3: Default — no entity detected, treat as specific query ──
        return {"scope": "specific", "entity": None, "top_k_hint": 15}

    # ── Query expansion ─────────────────────────────────────────

    def _expand_query(self, question: str, sess: SessionIndex) -> str:
        scope_info = self._detect_scope(question, sess)

        if scope_info["scope"] == "general":
            doc_context = " ".join(list(sess.doc_names.values())[:5])
            return (
                f"{question} overview all funds all schemes summary "
                f"introduction main points {doc_context}"
            )

        # Specific with named entity: reinforce entity name for better embedding match
        if scope_info.get("entity"):
            return f"{question} {scope_info['entity']}"

        return question


    # ── Dynamic entity extraction ────────────────────────────────

    @staticmethod
    def _extract_entities_from_text(text: str) -> set:
        """
        Dynamically extract named entities (fund names, scheme names, section
        headers, product names, etc.) from raw document text.

        Strategy:
          - Look for repeated Title Case noun phrases (≥2 words, ≥8 chars)
            that appear as headings or labels in the text.
          - Common financial patterns: "X Fund", "X Scheme", "X Plan",
            "X Cap Fund", "X Services Fund", etc.
          - Also capture anything that looks like a proper-noun section header.

        Returns a set of lower-cased entity strings.
        """
        entities = set()

        # Pattern 1: "<Name> Fund / Scheme / Plan / Trust / Portfolio"
        fund_pattern = re.compile(
            r"\b([A-Z][A-Za-z&\s]{3,50}?)"
            r"\s+(Fund|Scheme|Plan|Trust|Portfolio|ETF|Index|Account)\b"
        )
        for m in fund_pattern.finditer(text):
            full = (m.group(1).strip() + " " + m.group(2)).strip()
            if len(full) >= 8:
                entities.add(full.lower())
                # Also add just the prefix (e.g. "helios flexi cap" from "helios flexi cap fund")
                prefix = m.group(1).strip().lower()
                if len(prefix) >= 6:
                    entities.add(prefix)

        # Pattern 2: Standalone Title Case phrases on their own line (headers/labels)
        for line in text.splitlines():
            stripped = line.strip()
            # Must be a short title-case line (2-8 words, no sentence-ending punctuation)
            words = stripped.split()
            if (2 <= len(words) <= 8
                    and not stripped.endswith((".", "?", "!"))
                    and stripped == stripped.title()
                    and len(stripped) >= 8):
                entities.add(stripped.lower())

        # Deduplicate substrings — keep only the longest forms
        sorted_entities = sorted(entities, key=len, reverse=True)
        final = set()
        for e in sorted_entities:
            if not any(e != f and e in f for f in final):
                final.add(e)

        return final

    # ── Embedding ────────────────────────────────────────────────

    def _embed(self, texts: List[str]) -> np.ndarray:
        return self._model.encode(
            texts, batch_size=32,
            show_progress_bar=False,
            normalize_embeddings=True,
        ).astype(np.float32)

    # ── Context formatters ───────────────────────────────────────

    def _fmt_context(self, chunks: List[Dict]) -> str:
        parts = []
        for i, c in enumerate(chunks, 1):
            parts.append(
                f"[CHUNK {i} | Source: {c['source_name']} | "
                f"Part {c['chunk_index']+1}/{c.get('total_chunks','?')} | "
                f"Score: {c.get('score',0):.3f}]\n{c['text']}"
            )
        return "\n\n---\n\n".join(parts)

    def _fmt_context_report(self, chunks: List[Dict], max_chars: int = 150000) -> str:
        """Format context for reports — grouped by source, ordered."""
        by_source: Dict[str, List[Dict]] = {}
        for c in chunks:
            by_source.setdefault(c["source_name"], []).append(c)

        parts = []
        total = 0
        for src, src_chunks in by_source.items():
            header = f"\n\n{'='*60}\nSOURCE: {src}\n{'='*60}\n"
            if total + len(header) > max_chars:
                break
            parts.append(header)
            total += len(header)

            for c in src_chunks:
                if total + len(c["text"]) + 2 > max_chars:
                    parts.append("\n[...content truncated due to length limit...]\n")
                    return "".join(parts)
                parts.append(c["text"] + "\n")
                total += len(c["text"]) + 1

        return "".join(parts)

    # ── System prompts ───────────────────────────────────────────

    def _qa_prompt(self, context: str, sources: List[str], question: str,
                   scope_info: Dict[str, Any] = None) -> str:
        src_list = "\n".join(f"  • {s}" for s in sources)

        # Build scope-aware instruction. Sheet-schema questions must use the
        # dedicated complete header record, not infer columns from sample rows.
        if scope_info and scope_info.get("schema_query"):
            entity = scope_info.get("entity") or "the requested sheet"
            scope_instruction = f"""**SCOPE: SPREADSHEET SCHEMA — \"{entity}\"**
- Use the dedicated `[Workbook sheet schema]` excerpt when present.
- Return every entry from its `Columns:` line, exactly as written, preserving spaces and underscores.
- If `Column count:` is provided, list exactly that many columns.
- Do not infer the schema from a truncated header fragment or from data rows.
- If the complete schema excerpt is missing, say that the retrieved excerpts do not show the complete header; do not guess."""
        elif scope_info and scope_info.get("scope") == "general":
            scope_instruction = """**SCOPE: GENERAL / ALL-ENTITIES QUERY**
- The user is asking about ALL entities (funds, schemes, sections) in the documents.
- You MUST cover EVERY relevant entity found in the excerpts — do NOT limit to just one.
- Organise your answer with a sub-section or bullet per entity/fund/scheme.
- If some entities have the same field (e.g. Top 10 Stocks), list each one separately."""
        elif scope_info and scope_info.get("entity"):
            entity = scope_info["entity"]
            scope_instruction = f"""**SCOPE: SPECIFIC ENTITY — "{entity}"**
- The user is asking specifically about "{entity}".
- Focus your answer ONLY on "{entity}". Do not pad with other entities unless directly relevant.
- If information about "{entity}" is not in the excerpts, say so explicitly."""
        else:
            scope_instruction = """**SCOPE: SPECIFIC QUERY**
- Answer precisely for the entity/topic mentioned in the question.
- Do not pad with unrelated entities unless the user asked for comparisons."""

        return f"""You are Paperly, a precise document Q&A assistant.
Answer EXCLUSIVELY from the document excerpts provided below.

## SOURCES
{src_list}

## SCOPE INSTRUCTION
{scope_instruction}

## RETRIEVED EXCERPTS
{context}

## STRICT RULES
1. Only use the excerpts above. No prior knowledge.
2. Cite every claim: *(Source: filename)*
3. If not found: "The uploaded documents do not contain information about [topic]."
4. Multi-doc: separate by source, then synthesise.
5. Format: ## headers, bullet points, **bold** key terms.
6. End with `**→ Sources used:**` listing files referenced.
7. No filler preamble — answer directly.

## QUESTION
{question}

## ANSWER"""

    def _report_prompt(
        self,
        context:     str,
        sources:     List[str],
        report_spec: str,
        report_type: str,
    ) -> str:
        src_list = "\n".join(f"  • {s}" for s in sources)

        # NOTE: report_type is accepted for backward compatibility with
        # callers/signature, but is no longer used to select a fixed section
        # skeleton — see structure_guidance below. The model decides section
        # headings/ordering itself, per report_spec.
        structure_guidance = """## REPORT STRUCTURE
The report structure must be determined by the user's request, topic,
requested style, requested depth, and desired format. Choose section
headings and ordering that are appropriate for the request — do not use a
universal template. Two reports on different requests should not read as if
they were forced into the same shape.

Regardless of the structure you choose:
- Start with a single `#` title line.
- Use `##` for major section headings.
- End with a line listing the sources actually used, e.g.
  "**→ Sources:** file1.pdf, file2.pdf"."""

        return f"""You are Paperly, an expert report writer. Generate a professional report
based EXCLUSIVELY on the document content provided below.

## DOCUMENTS USED
{src_list}

## FULL DOCUMENT CONTENT
{context}

## STRICT RULES
1. **Use ONLY the provided document content.** No outside knowledge, no invention.
2. **Every claim must be cited**: *(Source: filename)*
3. **If information is insufficient** for a section you chose to include, write: "[Insufficient information in provided documents]"
4. **Do not fabricate data, statistics, quotes, or facts** not present in the documents.
5. **Use professional language** appropriate for a formal report.

{structure_guidance}

## REPORT REQUEST
{report_spec}

## YOUR REPORT (cite all claims)"""

    def _no_content(self, question: str, reason: str, message: str) -> Dict[str, Any]:
        prompt = f"""You are Paperly, a document Q&A assistant.
No relevant document content was found: {message}

Respond with ONLY this message:

"⚠️ **No relevant content found.**

{message}

**What you can do:**
- Upload the document(s) using the 📎 button (PDF, DOCX, images)
- Paste text directly into the chat input box
- Check that uploaded files show ✓ (indexed) status

Once content is added, ask again."

Question: {question}"""

        return {
            "context":       "",
            "system_prompt": prompt,
            "retrieved":     0,
            "source_files":  [],
            "has_content":   False,
            "reason":        reason,
            "message":       message,
        }
