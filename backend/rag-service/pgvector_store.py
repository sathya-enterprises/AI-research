"""Persistent Supabase Postgres/pgvector storage for RAG chunks.

Embeddings are stored only in Postgres (pgvector), never in a local FAISS index or
local vector file. Text/chunk metadata can be cached in process memory to support
scope detection, but the database remains the source of truth.

Configure SUPABASE_URL and a server-only SUPABASE_SECRET_KEY (preferred) or
SUPABASE_SERVICE_ROLE_KEY in backend/rag-service/.env. Run the SQL migration
supabase_pgvector.sql in the Supabase SQL editor before indexing.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import httpx

log = logging.getLogger("rag-pgvector")

_SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
_SERVER_KEY = (
    os.getenv("SUPABASE_SECRET_KEY", "").strip()
    or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
)
_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "").strip()
_TABLE = "rag_chunks"
_RPC = "rag_match_chunks"
_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=60.0, pool=10.0)
_CLIENT: Optional[httpx.Client] = None
_CLIENT_LOCK = threading.Lock()


class PgVectorStoreError(RuntimeError):
    """A safe-to-display RAG vector-store configuration or request error."""


def _credentials() -> tuple[str, str]:
    if not _SUPABASE_URL:
        raise PgVectorStoreError(
            "pgvector is not configured: set SUPABASE_URL in backend/rag-service/.env."
        )
    key = _SERVER_KEY
    if not key:
        raise PgVectorStoreError(
            "pgvector needs a server-only SUPABASE_SECRET_KEY or "
            "SUPABASE_SERVICE_ROLE_KEY in backend/rag-service/.env. Do not use a public key for private RAG data."
        )
    return _SUPABASE_URL, key


def _client() -> httpx.Client:
    global _CLIENT
    if _CLIENT is None:
        with _CLIENT_LOCK:
            if _CLIENT is None:
                _CLIENT = httpx.Client(timeout=_TIMEOUT, follow_redirects=False)
    return _CLIENT


def close() -> None:
    global _CLIENT
    with _CLIENT_LOCK:
        if _CLIENT is not None:
            _CLIENT.close()
            _CLIENT = None


def _headers(key: str, *, prefer: Optional[str] = None, range_header: Optional[str] = None) -> dict[str, str]:
    headers = {"apikey": key, "Content-Type": "application/json"}
    # Legacy service-role keys are JWTs. Modern sb_secret_ keys are API keys,
    # not JWT bearer tokens, so they must remain in the apikey header only.
    if key.startswith("eyJ") and key.count(".") == 2:
        headers["Authorization"] = f"Bearer {key}"
    if prefer:
        headers["Prefer"] = prefer
    if range_header:
        headers["Range"] = range_header
    return headers


def _request(method: str, path: str, *, params: Optional[dict] = None, json: Any = None,
             prefer: Optional[str] = None, range_header: Optional[str] = None,
             timeout: Optional[httpx.Timeout] = None) -> httpx.Response:
    base, key = _credentials()
    url = f"{base}/rest/v1/{path.lstrip('/')}"
    try:
        response = _client().request(
            method,
            url,
            params=params,
            json=json,
            headers=_headers(key, prefer=prefer, range_header=range_header),
            timeout=timeout or _TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise PgVectorStoreError(f"Supabase pgvector request failed ({type(exc).__name__}). Check SUPABASE_URL and network access.") from exc
    if not response.is_success:
        # Server response text is bounded and doesn't include request headers or keys.
        detail = " ".join((response.text or "").split())[:500]
        if response.status_code in (404, 406, 400) and ("rag_match_chunks" in path or "rag_chunks" in path):
            detail += " | Ensure backend/rag-service/supabase_pgvector.sql has been run in the Supabase SQL editor."
        raise PgVectorStoreError(f"Supabase pgvector returned HTTP {response.status_code}: {detail or 'no response details'}")
    return response


# The returned columns intentionally exclude the embedding to avoid copying
# vectors into Python memory. The vector remains only in the pgvector column.
_CHUNK_COLUMNS = (
    "session_id,chunk_id,doc_id,text,source_name,source_type,file_type,chunk_index,"
    "total_chunks,source_sha256,original_location,source_metadata"
)


def get_session_chunks(session_id: str) -> List[Dict[str, Any]]:
    """Load chunk text/metadata for scope detection; no embeddings are downloaded."""
    rows: List[Dict[str, Any]] = []
    start, page_size = 0, 500
    while True:
        response = _request(
            "GET", _TABLE,
            params={
                "select": _CHUNK_COLUMNS,
                "session_id": f"eq.{session_id}",
                "order": "doc_id.asc,chunk_index.asc,chunk_id.asc",
                "limit": str(page_size),
            },
            range_header=f"{start}-{start + page_size - 1}",
        )
        batch = response.json()
        if not isinstance(batch, list):
            raise PgVectorStoreError("Supabase pgvector returned an invalid chunk list.")
        rows.extend(_normalize_chunk(row) for row in batch if isinstance(row, dict))
        if len(batch) < page_size:
            break
        start += page_size
    return rows


def _normalize_chunk(row: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(row)
    result["chunk_index"] = int(result.get("chunk_index") or 0)
    result["total_chunks"] = int(result.get("total_chunks") or 0)
    result["source_metadata"] = result.get("source_metadata") if isinstance(result.get("source_metadata"), dict) else {}
    return result


def upsert_chunks(rows: List[Dict[str, Any]]) -> None:
    """Upsert document chunks and vectors in manageable PostgREST batches."""
    if not rows:
        return
    batch_size = 50
    for start in range(0, len(rows), batch_size):
        batch = rows[start:start + batch_size]
        payload = []
        for row in batch:
            embedding = row.get("embedding")
            if not isinstance(embedding, list) or len(embedding) != 384:
                raise PgVectorStoreError("Embedding dimension mismatch: this pgvector schema expects 384 values from all-MiniLM-L6-v2.")
            payload.append({
                "session_id": str(row["session_id"]),
                "chunk_id": str(row["chunk_id"]),
                "doc_id": str(row["doc_id"]),
                "text": str(row.get("text") or ""),
                "source_name": str(row.get("source_name") or "Unknown"),
                "source_type": str(row.get("source_type") or "file"),
                "file_type": str(row.get("file_type") or ""),
                "chunk_index": int(row.get("chunk_index") or 0),
                "total_chunks": int(row.get("total_chunks") or 0),
                "source_sha256": str(row.get("source_sha256") or ""),
                "original_location": row.get("original_location"),
                "source_metadata": row.get("source_metadata") if isinstance(row.get("source_metadata"), dict) else {},
                "embedding": embedding,
            })
        _request(
            "POST", f"{_TABLE}?on_conflict=session_id,chunk_id",
            json=payload,
            prefer="resolution=merge-duplicates,return=minimal",
        )


def delete_document(session_id: str, doc_id: str) -> None:
    _request("DELETE", _TABLE, params={
        "session_id": f"eq.{session_id}",
        "doc_id": f"eq.{doc_id}",
    }, prefer="return=minimal")


def delete_session(session_id: str) -> None:
    _request("DELETE", _TABLE, params={"session_id": f"eq.{session_id}"}, prefer="return=minimal")


def search_chunks(session_id: str, query_embedding: List[float], limit: int = 8) -> List[Dict[str, Any]]:
    if len(query_embedding) != 384:
        raise PgVectorStoreError("Query embedding dimension mismatch; expected 384 values from all-MiniLM-L6-v2.")
    response = _request(
        "POST", f"rpc/{_RPC}",
        json={
            "query_embedding": query_embedding,
            "match_session_id": session_id,
            "match_count": max(1, min(int(limit), 100)),
        },
    )
    data = response.json()
    if not isinstance(data, list):
        raise PgVectorStoreError("pgvector similarity search returned an invalid result.")
    results = []
    for row in data:
        if not isinstance(row, dict):
            continue
        normalized = _normalize_chunk(row)
        normalized["score"] = float(row.get("similarity") or 0.0)
        results.append(normalized)
    return results


def health_check() -> Dict[str, Any]:
    """Return safe storage diagnostics without disclosing URLs or API keys."""
    if not _SUPABASE_URL:
        return {"backend": "supabase_pgvector", "configured": False, "ready": False, "reason": "missing_supabase_url"}
    if not _SERVER_KEY:
        return {"backend": "supabase_pgvector", "configured": False, "ready": False, "reason": "missing_server_key"}
    try:
        # A select confirms the table exists and the server credential can read it.
        _request("GET", _TABLE, params={"select": "session_id", "limit": "1"}, timeout=httpx.Timeout(connect=5, read=5, write=5, pool=5))
        return {"backend": "supabase_pgvector", "configured": True, "ready": True, "table": _TABLE}
    except PgVectorStoreError as exc:
        return {"backend": "supabase_pgvector", "configured": True, "ready": False, "reason": str(exc)[:300]}
