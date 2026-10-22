"""Durable research provenance store backed by PostgreSQL/Supabase.

The research engine keeps its canonical provenance state in Supabase Postgres:
research runs, sources, documents, chunks, evidence, claims, refresh tasks,
revisions, artifacts, and stage/provider telemetry.

The frontend never receives the database credentials. This module runs only in
the FastAPI backend and connects with a backend-only PostgreSQL connection URI.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

from utils.source_identity import canonical_http_url

import psycopg2
from psycopg2.extras import Json, RealDictCursor


_LOCK = threading.RLock()
_INIT_LOCK = threading.RLock()
_INITIALISED_URL: str | None = None

# Kept as a compatibility label for startup logging and diagnostics. The
# canonical research store is no longer a local SQLite file.
DB_PATH = Path("supabase://research")
STORE_BACKEND = "supabase_postgres"


def _database_url() -> str:
    value = (
        os.getenv("RESEARCH_DATABASE_URL")
        or os.getenv("DATABASE_URL")
        or os.getenv("SUPABASE_DATABASE_URL")
        or ""
    ).strip()
    if not value:
        raise RuntimeError(
            "Research store requires a Supabase/Postgres connection string. "
            "Set RESEARCH_DATABASE_URL (preferred) or DATABASE_URL in the backend .env."
        )
    return value


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_pg_text(value: Any) -> str:
    """Remove PostgreSQL-forbidden NUL characters from text values."""
    return str(value if value is not None else "").replace("\x00", "")


def _clean_json_value(value: Any) -> Any:
    """Recursively sanitize JSON values before psycopg2 serializes them.

    A single embedded NUL in a scraped page's metadata is rejected by Postgres
    and used to abort persistence of an otherwise valid source batch.
    """
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, dict):
        return {_clean_pg_text(k): _clean_json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean_json_value(item) for item in value]
    return value


def _json(value: Any) -> Json:
    return Json(_clean_json_value(value if value is not None else {}))


def canonical_url(value: Any) -> str:
    """Shared URL identity used by search reuse and the report citation registry."""
    return canonical_http_url(value)


def _connect():
    try:
        conn = psycopg2.connect(_database_url(), sslmode="require", connect_timeout=8, cursor_factory=RealDictCursor)
    except psycopg2.OperationalError as exc:
        message = str(exc).lower()
        if "getaddrinfo" in message or "could not translate host name" in message or "name or service not known" in message:
            log = logging.getLogger("research_store")
            log.error("Supabase/Postgres DNS resolution failed. Verify the hostname in RESEARCH_DATABASE_URL (or DATABASE_URL/SUPABASE_DATABASE_URL) and the machine's DNS/network; credentials are not logged.")
        raise
    conn.autocommit = False
    # Keep the research store isolated from any pre-existing public tables in
    # the Supabase project. Earlier/local versions used unqualified table names
    # such as research_runs; an existing public table with an older schema can
    # otherwise satisfy CREATE TABLE IF NOT EXISTS and then fail on a later
    # statement (e.g. missing research_run_id).
    with conn.cursor() as cur:
        cur.execute("CREATE SCHEMA IF NOT EXISTS gg_research")
        cur.execute("SET search_path TO gg_research, public")
    return conn


def _legacy_schema_name() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = uuid.uuid4().hex[:8]
    return f"gg_research_legacy_{stamp}_{suffix}"


def _quarantine_existing_schema() -> str:
    """Rename an incompatible gg_research schema instead of destroying it.

    Older setup attempts can leave partially-created tables behind. PostgreSQL
    then makes CREATE TABLE IF NOT EXISTS a no-op, and a later index/statement
    fails because the old table is missing canonical columns. Renaming preserves
    the old schema for inspection while allowing a clean canonical schema to be
    created.
    """
    legacy = _legacy_schema_name()
    url = _database_url()
    conn = psycopg2.connect(url, sslmode="require", cursor_factory=RealDictCursor)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_namespace WHERE nspname = 'gg_research'")
            if cur.fetchone() is None:
                return legacy
            cur.execute('ALTER SCHEMA gg_research RENAME TO %s' % psycopg2.extensions.quote_ident(legacy, cur))
            return legacy
    finally:
        conn.close()


def initialise() -> None:
    """Create/upgrade the research schema in Supabase exactly once per URL.

    If an earlier app version left an incompatible gg_research schema behind,
    quarantine it by renaming the schema (non-destructive) and recreate the
    canonical schema. This avoids both SQLite-style local corruption and
    Postgres "CREATE TABLE IF NOT EXISTS" collisions with stale definitions.
    """
    global _INITIALISED_URL
    url = _database_url()
    with _INIT_LOCK:
        if _INITIALISED_URL == url:
            return
        schema_path = Path(__file__).resolve().parents[1] / "supabase_research_store.sql"
        schema_sql = schema_path.read_text(encoding="utf-8")

        try:
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(schema_sql)
        except (psycopg2.errors.UndefinedColumn, psycopg2.errors.UndefinedTable, psycopg2.errors.DuplicateColumn, psycopg2.errors.DuplicateTable) as exc:
            # The transaction is aborted after a DDL error; discard it before
            # performing the quarantine operation on a fresh connection.
            try:
                with psycopg2.connect(url, sslmode="require") as cleanup_conn:
                    cleanup_conn.rollback()
            except Exception:
                pass
            legacy = _quarantine_existing_schema()
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(schema_sql)
            print(f"[GG-Backend] research_store — quarantined incompatible gg_research as {legacy}")
        _INITIALISED_URL = url
        print("[GG-Backend] research_store — Supabase Postgres ready (schema=gg_research)")


def _one(cur):
    row = cur.fetchone()
    return row


def start_run(question: str, metadata: dict | None = None) -> str:
    initialise()
    run_id = str(uuid.uuid4())
    now = _now()
    with _LOCK, _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO research_runs
            (research_run_id, question, started_at, completed_at, status, metadata_json,
             completeness_json, last_heartbeat_at, worker_id, updated_at)
            VALUES (%s,%s,%s,NULL,'queued',%s,%s,%s,%s,%s)""",
            (run_id, _clean_pg_text(question or ""), now, _json(metadata), _json({}), now, _clean_pg_text((metadata or {}).get("worker_id", "")), now),
        )
    return run_id


def record_stage(run_id: str, stage: str, count: int, payload: dict | None = None) -> None:
    if not run_id:
        return
    now = _now()
    status = stage if stage in {"planning", "researching", "adjudicating", "composing", "rendering", "qa"} else "running"
    with _LOCK, _connect() as conn, conn.cursor() as cur:
        cur.execute("UPDATE research_runs SET status=%s,last_heartbeat_at=%s,updated_at=%s WHERE research_run_id=%s", (status, now, now, run_id))
        cur.execute(
            "INSERT INTO stage_events(research_run_id,stage,count,payload_json,created_at) VALUES(%s,%s,%s,%s,%s)",
            (run_id, _clean_pg_text(stage), int(count or 0), _json(payload), now),
        )
        cur.execute(
            "UPDATE report_jobs SET status='running', stage=%s, detail=%s, progress_count=%s, updated_at=%s WHERE research_run_id=%s",
            (stage, f"{stage} — {int(count or 0)}", int(count or 0), now, run_id),
        )


def record_provider_call(run_id: str, provider: str, query: str = "", page_or_offset: Any = "", status: str = "ok", candidate_count: int = 0, metadata: dict | None = None) -> None:
    with _LOCK, _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO provider_calls
            (research_run_id,provider,query,page_or_offset,status,candidate_count,metadata_json,created_at)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s)""",
            (run_id, _clean_pg_text(provider), _clean_pg_text(query), _clean_pg_text(page_or_offset), _clean_pg_text(status), int(candidate_count or 0), _json(metadata), _now()),
        )


def upsert_source(run_id: str, source: dict, stage: str = "discovered") -> str:
    url = canonical_url(source.get("url") or source.get("canonical_url"))
    source_id = _clean_pg_text(source.get("source_id") or source.get("id") or "").strip()
    if not source_id:
        source_id = "SRC-" + hashlib.sha256((url or str(source.get("title") or "unknown")).encode()).hexdigest()[:16]
    domain = _clean_pg_text(urlparse(url).hostname or "").lower()
    content = _clean_pg_text(source.get("pageContent") or source.get("evidenceContent") or source.get("fullContent") or source.get("text") or source.get("snippet") or "")
    digest = hashlib.sha256(content.encode("utf-8", "ignore")).hexdigest() if content else ""
    with _LOCK, _connect() as conn, conn.cursor() as cur:
        if url and not source_id.startswith("S"):
            cur.execute(
                "SELECT source_id FROM source_registry WHERE research_run_id=%s AND canonical_url=%s LIMIT 1",
                (run_id, url),
            )
            existing = _one(cur)
            if existing:
                source_id = existing["source_id"]
        cur.execute(
            """INSERT INTO source_registry
            (source_id,research_run_id,canonical_url,title,publisher,organization,source_type,published_at,
             retrieved_at,domain,acquisition_method,http_status,content_hash,authority_score,relevance_score,
             source_role,stage,metadata_json)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(research_run_id,source_id) DO UPDATE SET
              stage=EXCLUDED.stage,
              retrieved_at=COALESCE(EXCLUDED.retrieved_at,source_registry.retrieved_at),
              content_hash=COALESCE(EXCLUDED.content_hash,source_registry.content_hash),
              metadata_json=EXCLUDED.metadata_json""",
            (
                source_id, run_id, url,
                _clean_pg_text(source.get("title") or source.get("name") or ""),
                _clean_pg_text(source.get("publisher") or ""),
                _clean_pg_text(source.get("organization") or ""),
                str(source.get("source_type") or source.get("kind") or "web"),
                _clean_pg_text(source.get("published_at") or source.get("published") or ""), _now(), domain,
                _clean_pg_text(source.get("acquisition_method") or "search"), source.get("http_status"), digest,
                source.get("authority_score"), source.get("relevance_score"),
                _clean_pg_text(source.get("source_role") or source.get("role") or ""), stage, _json(source),
            ),
        )
    return source_id


def save_document(run_id: str, source_id: str, text: str, raw_text: str = "", page_info: dict | None = None, metadata: dict | None = None, document_id: str | None = None) -> str:
    document_id = document_id or str(uuid.uuid5(uuid.NAMESPACE_URL, f"{run_id}:{source_id}"))
    extracted = _clean_pg_text(text or "")
    raw_text = _clean_pg_text(raw_text or extracted)
    with _LOCK, _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO documents
            (document_id,research_run_id,source_id,raw_text,extracted_text,page_info_json,acquisition_metadata_json,content_hash,created_at)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(document_id) DO UPDATE SET
              raw_text=EXCLUDED.raw_text, extracted_text=EXCLUDED.extracted_text,
              page_info_json=EXCLUDED.page_info_json, acquisition_metadata_json=EXCLUDED.acquisition_metadata_json,
              content_hash=EXCLUDED.content_hash""",
            (document_id, run_id, source_id, raw_text or extracted, extracted, _json(page_info), _json(metadata), hashlib.sha256(extracted.encode("utf-8", "ignore")).hexdigest(), _now()),
        )
    return document_id


def save_chunks(run_id: str, source_id: str, document_id: str, text: str, metadata: dict | None = None, chunk_size: int = 1800, overlap: int = 200) -> list[str]:
    text = str(text or "").strip()
    if not text:
        return []
    ids: list[str] = []
    start = 0
    ordinal = 0
    with _LOCK, _connect() as conn, conn.cursor() as cur:
        while start < len(text):
            end = min(len(text), start + chunk_size)
            piece = text[start:end].strip()
            if piece:
                cid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{document_id}:{ordinal}:{piece[:80]}"))
                cur.execute(
                    """INSERT INTO chunks
                    (chunk_id,research_run_id,source_id,document_id,ordinal,section,page,text,metadata_json,embedding_ref)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(chunk_id) DO NOTHING""",
                    (cid, run_id, source_id, document_id, ordinal, "", None, piece, _json(metadata), cid),
                )
                ids.append(cid)
            if end >= len(text):
                break
            start = max(end - overlap, start + 1)
            ordinal += 1
    return ids


def save_evidence(run_id: str, source_id: str, chunk_id: str | None, text: str, evidence_type: str = "text", topic: str = "", quantitative: dict | None = None, provenance: dict | None = None, source_version_id: str | None = None) -> str:
    eid = str(uuid.uuid4())
    with _LOCK, _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO evidence_assets
            (evidence_id,research_run_id,source_id,source_version_id,chunk_id,topic,evidence_type,quantitative_json,provenance_json,text,created_at)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (eid, run_id, source_id, source_version_id, chunk_id, topic, evidence_type, _json(quantitative), _json(provenance), text, _now()),
        )
    return eid


def save_claim(run_id: str, claim_text: str, dimension: str = "", claim_type: str = "factual", support_status: str = "unknown", metadata: dict | None = None) -> str:
    cid = str(uuid.uuid4())
    with _LOCK, _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO claims(claim_id,research_run_id,claim_text,research_dimension,claim_type,support_status,metadata_json,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
            (cid, run_id, claim_text, dimension, claim_type, support_status, _json(metadata), _now()),
        )
    return cid


def relate_claim_evidence(claim_id: str, evidence_id: str, relationship: str = "supports") -> None:
    with _LOCK, _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO claim_evidence(claim_id,evidence_id,relationship) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
            (claim_id, evidence_id, relationship),
        )


def retrieve_hybrid(run_id: str, query: str, limit: int = 12, dimension: str = "") -> list[dict]:
    """Deterministic PostgreSQL full-text retrieval + metadata/token reranking."""
    terms = [t for t in re.findall(r"[A-Za-z0-9]{3,}", query.lower()) if t not in {"the", "and", "for", "with", "from", "into"}]
    if not terms:
        return []
    search_query = " ".join(terms[:24])
    with _LOCK, _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """WITH q AS (SELECT to_tsquery('simple', %s) AS tsq)
            SELECT c.*,
                   ts_rank_cd(to_tsvector('simple', c.text || ' ' || c.metadata_json::text), q.tsq) AS lexical_score
            FROM chunks c, q
            WHERE c.research_run_id=%s
              AND to_tsvector('simple', c.text || ' ' || c.metadata_json::text) @@ q.tsq
            ORDER BY lexical_score DESC, c.chunk_id
            LIMIT %s""",
            (search_query, run_id, max(1, int(limit))),
        )
        rows = cur.fetchall()
    results = []
    for row in rows:
        item = dict(row)
        hay = f"{item.get('text','')} {json.dumps(item.get('metadata_json') or {}, ensure_ascii=False)}".lower()
        dim_terms = [t for t in re.findall(r"[a-z0-9]{3,}", query.lower()) if t not in {"the", "and", "for", "with", "from"}]
        dim_hits = [t for t in dim_terms if t in hay]
        lexical = float(item.get("lexical_score") or 0.0)
        retrieval_score = max(0.0, min(1.0, lexical))
        semantic_overlap = len(set(dim_hits)) / max(1, len(set(dim_terms)))
        rerank_score = min(1.0, retrieval_score * 0.75 + min(0.25, semantic_overlap))
        match_reason = f"keyword overlap for {dimension or 'query'}"
        if dim_hits:
            match_reason += f"; dimension metadata/text matched: {', '.join(list(dict.fromkeys(dim_hits))[:5])}"
        item.update(
            {
                "dimension_id": dimension,
                "retrieval_score": round(retrieval_score, 6),
                "semantic_score": round(semantic_overlap, 6),
                "retrieval_method": "postgres_tsvector+semantic_token_overlap+metadata_rerank",
                "match_reason": match_reason,
                "rerank_score": round(rerank_score, 6),
            }
        )
        results.append(item)
    results.sort(key=lambda x: (-float(x.get("rerank_score") or 0), str(x.get("chunk_id"))))
    return results[:max(1, int(limit))]



def _web_source_key(url: str) -> str:
    canonical = canonical_url(url)
    try:
        parsed = urlparse(canonical)
        query_parts = []
        for key, value in re.findall(r"([^=&]+)=?([^&]*)", parsed.query or ""):
            if key.lower() in {
                "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
                "gclid", "fbclid", "ref", "referrer", "source",
            }:
                continue
            query_parts.append((key, value))
        query = "&".join(f"{k}={v}" if v else k for k, v in query_parts)
        canonical = urlunparse((parsed.scheme, parsed.netloc, parsed.path.rstrip("/") or "/", parsed.params, query, ""))
    except Exception:
        pass
    return "WEB-" + hashlib.sha256(canonical.encode("utf-8", "ignore")).hexdigest()[:20]


def _web_content_chunks(text: str, chunk_size: int = 1800, overlap: int = 200) -> list[str]:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + chunk_size)
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


def persist_web_search_results(
    query: str,
    sources: list[dict] | None,
    *,
    provider: str = "exa",
    research_run_id: str | None = None,
    metadata: dict | None = None,
) -> dict[str, int]:
    """Persist web-search results in a cross-report library.

    The store is deliberately best-effort at the call site: callers can use
    this function from a background task so a database outage never breaks a
    report or chat response. Only source content actually returned by the web
    provider is stored; no model-generated prose is treated as source content.
    """
    initialise()
    rows = [x for x in (sources or []) if isinstance(x, dict)]
    if not rows:
        return {"sources": 0, "versions": 0, "chunks": 0, "events": 0}
    now = _now()
    saved_sources = saved_versions = saved_chunks = saved_events = 0
    query_text = _clean_pg_text(query or "").strip()[:1000]
    # Use this function's own PostgreSQL transaction without the process-global
    # Python lock. Long article ingests must not block independent chat retrievals.
    with _connect() as conn, conn.cursor() as cur:
        for rank, source in enumerate(rows, 1):
            url = canonical_url(source.get("url") or source.get("canonical_url"))
            if not url or url.startswith("internal://"):
                continue
            source_key = _web_source_key(url)
            title = _clean_pg_text(source.get("title") or source.get("name") or "").strip()
            publisher = _clean_pg_text(source.get("publisher") or source.get("source") or "").strip()
            domain = _clean_pg_text(urlparse(url).hostname or "").lower()
            published_at_raw = source.get("published_at") or source.get("published") or source.get("publication_date")
            published_at = _clean_pg_text(published_at_raw).strip() or None
            content = _clean_pg_text(source.get("pageContent") or source.get("evidenceContent") or source.get("fullContent") or source.get("text") or source.get("content") or source.get("snippet") or "").strip()
            if len(content) > 20000:
                content = content[:20000]
            content_hash = hashlib.sha256(content.encode("utf-8", "ignore")).hexdigest() if content else ""
            source_type = str(source.get("source_type") or source.get("kind") or "web")
            direct_page = bool(source.get("pageContent") and str(source.get("pageFetchStatus") or "") == "ok")
            evidence_origin = str(source.get("evidenceOrigin") or ("publisher_page" if direct_page else "search_extract_only" if content else "metadata_only"))
            evidence_metadata = {
                "evidenceOrigin": evidence_origin,
                "pageFetchStatus": str(source.get("pageFetchStatus") or ("ok" if direct_page else "not_attempted")),
                "pageFetchUrl": str(source.get("pageFetchUrl") or ""),
                "pageFetchFinalUrl": str(source.get("pageFetchFinalUrl") or source.get("pageFetchUrl") or ""),
                "pageFetchHttpStatus": source.get("pageFetchHttpStatus"),
                "pageFetchContentType": str(source.get("pageFetchContentType") or source.get("contentType") or ""),
            }
            per_source_metadata = source.get("metadata") if isinstance(source.get("metadata"), dict) else {}
            source_metadata_json = _json({"provider": provider, **evidence_metadata, **(metadata or {}), **per_source_metadata})
            # Both source_key (the tracking-stripped URL hash) and canonical_url
            # may conflict independently. ON CONFLICT(canonical_url) alone still
            # raised UniqueViolation when two tracking variants shared source_key,
            # aborting persistence for the entire search batch. Make persistence
            # idempotent against either identity, then resolve the canonical key.
            cur.execute(
                """INSERT INTO web_source_library
                (source_key,canonical_url,title,publisher,domain,source_type,published_at,first_seen_at,last_seen_at,latest_content_hash,metadata_json)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT DO NOTHING""",
                (source_key, url, title, publisher, domain, source_type,
                 published_at, now, now, content_hash, source_metadata_json),
            )
            cur.execute(
                """SELECT source_key FROM web_source_library
                   WHERE canonical_url=%s OR source_key=%s
                   ORDER BY (canonical_url=%s) DESC
                   LIMIT 1""",
                (url, source_key, url),
            )
            actual = _one(cur)
            if not actual:
                # Keep this row best-effort; a malformed/contended source should
                # not prevent later sources from being persisted in this batch.
                continue
            source_key = str(actual["source_key"])
            cur.execute(
                """UPDATE web_source_library SET
                     title=COALESCE(NULLIF(%s,''), title),
                     publisher=COALESCE(NULLIF(%s,''), publisher),
                     domain=COALESCE(NULLIF(%s,''), domain),
                     source_type=CASE
                         WHEN source_type='market_news_article' OR %s='market_news_article'
                         THEN 'market_news_article'
                         ELSE COALESCE(NULLIF(%s,''), source_type)
                     END,
                     published_at=COALESCE(%s, published_at),
                     last_seen_at=%s,
                     latest_content_hash=COALESCE(NULLIF(%s,''), latest_content_hash),
                     metadata_json=metadata_json || %s::jsonb
                   WHERE source_key=%s""",
                (title, publisher, domain, source_type, source_type, published_at, now,
                 content_hash, source_metadata_json, source_key),
            )
            saved_sources += 1

            if content_hash:
                version_id = "WEBV-" + hashlib.sha256((source_key + ":" + content_hash).encode("utf-8", "ignore")).hexdigest()[:24]
                cur.execute(
                    """INSERT INTO web_source_versions
                    (source_version_id,source_key,research_run_id,query_text,retrieved_at,content_hash,content_text,metadata_json)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(source_version_id) DO NOTHING""",
                    (version_id, source_key, research_run_id, query_text, now, content_hash, content,
                     _json({"title": title, "publisher": publisher, "url": url, "provider": provider, **evidence_metadata, **(metadata or {}), **per_source_metadata})),
                )
                if cur.rowcount:
                    saved_versions += 1
                    for ordinal, chunk in enumerate(_web_content_chunks(content)):
                        chunk_id = "WEBC-" + hashlib.sha256((version_id + ":" + str(ordinal) + ":" + chunk[:100]).encode("utf-8", "ignore")).hexdigest()[:24]
                        cur.execute(
                            """INSERT INTO web_source_chunks
                            (chunk_id,source_version_id,ordinal,text,metadata_json)
                            VALUES(%s,%s,%s,%s,%s)
                            ON CONFLICT(chunk_id) DO NOTHING""",
                            (chunk_id, version_id, ordinal, chunk, _json({"source_key": source_key, "title": title, "url": url, **per_source_metadata})),
                        )
                        saved_chunks += 1

            event_hash = hashlib.sha256(f"{query_text.lower()}|{source_key}".encode("utf-8", "ignore")).hexdigest()
            cur.execute(
                """INSERT INTO web_search_events
                (query_text,source_key,result_rank,provider,searched_at,research_run_id,metadata_json)
                SELECT %s,%s,%s,%s,%s,%s,%s
                WHERE NOT EXISTS (
                    SELECT 1 FROM web_search_events WHERE metadata_json->>'event_key'=%s
                )""",
                (query_text, source_key, rank, provider, now, research_run_id,
                 _json({"event_key": event_hash, **(metadata or {})}), event_hash),
            )
            if cur.rowcount:
                saved_events += 1
    return {"sources": saved_sources, "versions": saved_versions, "chunks": saved_chunks, "events": saved_events}


def retrieve_web_research(
    query: str, limit: int = 12, max_age_days: int | None = None,
    *, source_type: str | None = None, require_page_text: bool = False,
) -> list[dict]:
    """Retrieve previously stored web research across report/chat sessions.

    ``source_type`` restricts results (e.g. ``"market_news_article"`` for the
    scraped-feed knowledge base); ``require_page_text`` keeps only versions whose
    text came from an actual publisher-page extraction (not RSS summaries).
    """
    initialise()
    terms = [t for t in re.findall(r"[A-Za-z0-9]{3,}", str(query or "").lower()) if t not in {"the", "and", "for", "with", "from", "into", "through", "about"}]
    if not terms:
        return []
    search_query = " ".join(terms[:32])
    ts_terms = [re.sub(r"[^a-z0-9_]+", "", t) for t in terms[:24]]
    ts_query = " | ".join(f"{t}:*" for t in ts_terms if t)
    freshness_clause = ""
    params: list[Any] = [ts_query]
    if max_age_days is not None:
        freshness_clause = "AND wsv.retrieved_at >= now() - (%s || ' days')::interval"
        params.append(int(max_age_days))
    if source_type:
        freshness_clause += " AND wsl.source_type = %s"
        params.append(str(source_type)[:80])
    if require_page_text:
        freshness_clause += (" AND wsv.metadata_json->>'pageFetchStatus' = 'ok'"
                             " AND wsv.metadata_json->>'evidenceOrigin' IN ('publisher_page','publisher_page_and_search_extract')")
    params.append(max(1, int(limit)))
    sql = f"""
        WITH ranked AS (
          SELECT DISTINCT ON (wsl.source_key)
            wsl.source_key, wsl.canonical_url, wsl.title, wsl.publisher, wsl.domain,
            wsl.source_type, wsl.published_at, wsl.last_seen_at, wsl.metadata_json,
            wsv.content_text, wsv.retrieved_at, wsv.source_version_id,
            wsv.metadata_json AS version_metadata_json,
            ts_rank_cd(
              to_tsvector('simple', coalesce(wsl.title,'') || ' ' || coalesce(wsl.publisher,'') || ' ' || coalesce(wsv.content_text,'')),
              to_tsquery('simple', %s)
            ) AS lexical_score
          FROM web_source_library wsl
          JOIN web_source_versions wsv ON wsv.source_key=wsl.source_key
          WHERE to_tsvector('simple', coalesce(wsl.title,'') || ' ' || coalesce(wsl.publisher,'') || ' ' || coalesce(wsv.content_text,''))
                @@ to_tsquery('simple', %s)
            {freshness_clause}
          ORDER BY wsl.source_key,
            CASE
              WHEN wsv.metadata_json->>'evidenceOrigin' IN ('publisher_page','publisher_page_and_search_extract')
               AND wsv.metadata_json->>'pageFetchStatus' = 'ok' THEN 2
              WHEN wsv.metadata_json->>'evidenceOrigin' = 'rss_feed_summary' THEN 1
              ELSE 0
            END DESC,
            length(coalesce(wsv.content_text,'')) DESC,
            wsv.retrieved_at DESC
        )
        SELECT * FROM ranked
        ORDER BY lexical_score DESC, retrieved_at DESC
        LIMIT %s
    """
    params.insert(1, ts_query)
    with _LOCK, _connect() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    out=[]
    for row in rows:
        item=dict(row)
        item["url"]=item.pop("canonical_url","")
        stored_text=item.pop("content_text","")
        version_meta=item.pop("version_metadata_json", {}) or {}
        if isinstance(version_meta, str):
            try:
                version_meta=json.loads(version_meta)
            except Exception:
                version_meta={}
        if not isinstance(version_meta, dict):
            version_meta={}
        item["fullContent"]=stored_text
        item["evidenceOrigin"]=str(version_meta.get("evidenceOrigin") or "stored_search_extract")
        item["pageFetchStatus"]=str(version_meta.get("pageFetchStatus") or "unknown")
        item["pageFetchUrl"]=str(version_meta.get("pageFetchUrl") or "")
        item["pageFetchFinalUrl"]=str(version_meta.get("pageFetchFinalUrl") or version_meta.get("pageFetchUrl") or "")
        item["pageFetchHttpStatus"]=version_meta.get("pageFetchHttpStatus")
        item["pageFetchContentType"]=str(version_meta.get("pageFetchContentType") or version_meta.get("contentType") or "")
        if item["evidenceOrigin"] in {"publisher_page", "publisher_page_and_search_extract"} and item["pageFetchStatus"] == "ok":
            item["pageContent"]=stored_text
        item["retrieved_at"]=item.get("retrieved_at").isoformat() if hasattr(item.get("retrieved_at"), "isoformat") else item.get("retrieved_at")
        item["stored_research"] = True
        item["kind"] = "Stored web research"
        out.append(item)
    return out


def search_market_news_articles(
    query: str = "",
    *,
    page: int = 1,
    page_size: int = 20,
    category: str | None = None,
) -> dict[str, Any]:
    """Search the persisted scraped-article knowledge base with stable page pagination.

    The knowledge base reuses ``web_source_library`` and its version/chunk tables,
    so article evidence can be retrieved by report generation without duplicating
    article bodies in a second store.
    """
    initialise()
    page = max(1, int(page or 1))
    page_size = min(100, max(1, int(page_size or 20)))
    offset = (page - 1) * page_size
    terms = [t for t in re.findall(r"[A-Za-z0-9]{2,}", str(query or "").lower()) if t not in {"the", "and", "for", "with", "from", "into", "through", "about"}]
    ts_query = " | ".join(f"{re.sub(r'[^a-z0-9_]+', '', term)}:*" for term in terms[:24] if re.sub(r"[^a-z0-9_]+", "", term))
    category = re.sub(r"[^a-z0-9_-]", "", str(category or "").lower())[:80] or None
    filters = ["wsl.source_type = 'market_news_article'"]
    params: list[Any] = []
    if category:
        filters.append("lower(coalesce(wsl.metadata_json->>'category','')) = %s")
        params.append(category)
    if ts_query:
        filters.append("to_tsvector('simple', coalesce(wsl.title,'') || ' ' || coalesce(wsl.publisher,'') || ' ' || coalesce(wsv.content_text,'')) @@ to_tsquery('simple', %s)")
        params.append(ts_query)
    where = " AND ".join(filters)
    with _LOCK, _connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"""SELECT count(*) AS total
                FROM web_source_library wsl
                JOIN LATERAL (
                    SELECT content_text, retrieved_at, source_version_id, metadata_json
                    FROM web_source_versions
                    WHERE source_key=wsl.source_key
                    ORDER BY
                      CASE
                        WHEN metadata_json->>'evidenceOrigin' IN ('publisher_page','publisher_page_and_search_extract')
                         AND metadata_json->>'pageFetchStatus' = 'ok' THEN 2
                        WHEN metadata_json->>'evidenceOrigin' = 'rss_feed_summary' THEN 1
                        ELSE 0
                      END DESC,
                      length(coalesce(content_text,'')) DESC,
                      retrieved_at DESC
                    LIMIT 1
                ) wsv ON TRUE
                WHERE {where}""",
            params,
        )
        total_row = cur.fetchone() or {}
        total = int(total_row.get("total", 0))
        cur.execute(
            f"""SELECT wsl.source_key, wsl.canonical_url, wsl.title, wsl.publisher,
                       wsl.domain, wsl.published_at, wsl.first_seen_at, wsl.last_seen_at,
                       wsl.metadata_json, wsv.content_text, wsv.retrieved_at,
                       wsv.source_version_id, wsv.metadata_json AS version_metadata_json
                FROM web_source_library wsl
                JOIN LATERAL (
                    SELECT content_text, retrieved_at, source_version_id, metadata_json
                    FROM web_source_versions
                    WHERE source_key=wsl.source_key
                    ORDER BY
                      CASE
                        WHEN metadata_json->>'evidenceOrigin' IN ('publisher_page','publisher_page_and_search_extract')
                         AND metadata_json->>'pageFetchStatus' = 'ok' THEN 2
                        WHEN metadata_json->>'evidenceOrigin' = 'rss_feed_summary' THEN 1
                        ELSE 0
                      END DESC,
                      length(coalesce(content_text,'')) DESC,
                      retrieved_at DESC
                    LIMIT 1
                ) wsv ON TRUE
                WHERE {where}
                ORDER BY wsl.last_seen_at DESC, wsv.retrieved_at DESC, wsl.source_key ASC
                LIMIT %s OFFSET %s""",
            [*params, page_size, offset],
        )
        rows = cur.fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        content = str(item.pop("content_text", "") or "")
        meta = item.get("metadata_json") or {}
        version_meta = item.pop("version_metadata_json", {}) or {}
        if isinstance(meta, str):
            try: meta = json.loads(meta)
            except Exception: meta = {}
        if isinstance(version_meta, str):
            try: version_meta = json.loads(version_meta)
            except Exception: version_meta = {}
        item["metadata"] = meta if isinstance(meta, dict) else {}
        item["evidence_metadata"] = version_meta if isinstance(version_meta, dict) else {}
        item["url"] = item.pop("canonical_url", "")
        item["content_preview"] = content[:1800]
        item["content_length"] = len(content)
        item["retrieved_at"] = item.get("retrieved_at").isoformat() if hasattr(item.get("retrieved_at"), "isoformat") else item.get("retrieved_at")
        item["first_seen_at"] = item.get("first_seen_at").isoformat() if hasattr(item.get("first_seen_at"), "isoformat") else item.get("first_seen_at")
        item["last_seen_at"] = item.get("last_seen_at").isoformat() if hasattr(item.get("last_seen_at"), "isoformat") else item.get("last_seen_at")
        items.append(item)
    return {"articles": items, "total": total, "page": page, "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size if total else 0,
            "has_next": offset + len(items) < total, "has_previous": page > 1}


def format_stored_web_research(results: list[dict], max_chars: int = 14000) -> str:
    """Create a bounded evidence dossier from persistent web research."""
    blocks=[]; used=0
    for item in results or []:
        text = str(item.get("fullContent") or "").strip()
        if not text:
            continue
        if len(text) > 2200:
            text = text[:2200] + " …"
        origin = str(item.get("evidenceOrigin") or "stored_search_extract")
        if origin in {"publisher_page", "publisher_page_and_search_extract"} and str(item.get("pageFetchStatus") or "") == "ok":
            evidence_scope = "Evidence scope: extracted publisher article page."
        elif origin == "rss_feed_summary":
            evidence_scope = "Evidence scope: RSS feed summary only; the full publisher article has not been captured. Use as a discovery lead, not as proof for detailed or quantitative claims."
        elif origin == "metadata_only":
            evidence_scope = "Evidence scope: metadata only; no usable article text was captured."
        else:
            evidence_scope = "Evidence scope: stored search extract; this is not verified full-page text."
        block = f"STORED WEB SOURCE: {item.get('title') or 'Untitled'}\nURL: {item.get('url','')}\nPublisher: {item.get('publisher','')}\nSource type: {item.get('source_type') or 'web'}\nRetrieved: {item.get('retrieved_at','')}\n{evidence_scope}\nEvidence: {text}"
        if used + len(block) > max_chars:
            break
        blocks.append(block); used += len(block) + 2
    return "\n\n".join(blocks)

def record_dimension_retrieval(run_id: str, dimension_id: str, hits: list[dict], intent: dict | None = None) -> None:
    if not run_id or not dimension_id:
        return
    with _LOCK, _connect() as conn, conn.cursor() as cur:
        for hit in hits or []:
            chunk_id = str(hit.get("chunk_id") or "")
            if not chunk_id:
                continue
            cur.execute(
                """INSERT INTO dimension_retrieval
                (research_run_id,dimension_id,chunk_id,retrieval_score,retrieval_method,match_reason,rerank_score,metadata_json,created_at)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(research_run_id,dimension_id,chunk_id) DO UPDATE SET
                  retrieval_score=EXCLUDED.retrieval_score,retrieval_method=EXCLUDED.retrieval_method,
                  match_reason=EXCLUDED.match_reason,rerank_score=EXCLUDED.rerank_score,metadata_json=EXCLUDED.metadata_json""",
                (run_id, dimension_id, chunk_id, float(hit.get("retrieval_score") or 0),
                 str(hit.get("retrieval_method") or "postgres_tsvector"), str(hit.get("match_reason") or ""),
                 float(hit.get("rerank_score") or 0), _json({"intent": intent or {}, "source_id": hit.get("source_id")}), _now()),
            )


def dimension_retrieval_snapshot(run_id: str) -> dict[str, list[dict]]:
    with _LOCK, _connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM dimension_retrieval WHERE research_run_id=%s ORDER BY dimension_id, rerank_score DESC", (run_id,))
        rows = cur.fetchall()
    out: dict[str, list[dict]] = {}
    for row in rows:
        item = dict(row)
        item["metadata"] = item.pop("metadata_json") or {}
        out.setdefault(str(item["dimension_id"]), []).append(item)
    return out


def complete_run(run_id: str, completeness: dict | None = None, status: str = "completed") -> None:
    with _LOCK, _connect() as conn, conn.cursor() as cur:
        cur.execute("UPDATE research_runs SET completed_at=%s,status=%s,completeness_json=%s,updated_at=%s WHERE research_run_id=%s", (_now(), status, _json(completeness), _now(), run_id))


def save_source_version(run_id: str, source: dict, source_version_id: str | None = None) -> str:
    source_version_id = source_version_id or str(uuid.uuid4())
    url = canonical_url(source.get("url") or source.get("canonical_url"))
    content = str(source.get("pageContent") or source.get("evidenceContent") or source.get("fullContent") or source.get("text") or source.get("snippet") or "")
    with _LOCK, _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO source_versions
            (source_version_id,source_id,research_run_id,canonical_url,publisher,title,published_at,retrieved_at,content_hash,source_role,data_as_of,metadata_json,created_at)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (source_version_id, str(source.get("source_id") or source.get("id") or ""), run_id, url,
             str(source.get("publisher") or ""), str(source.get("title") or ""), source.get("published_at") or source.get("published"),
             source.get("retrieved_at") or _now(), hashlib.sha256(content.encode("utf-8", "ignore")).hexdigest() if content else "",
             str(source.get("source_role") or source.get("role") or ""), source.get("data_as_of") or source.get("as_of"), _json(source), _now()),
        )
    return source_version_id


def save_refresh_task(task: dict[str, Any]) -> str:
    task_id = str(task.get("refresh_task_id") or uuid.uuid4())
    with _LOCK, _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO targeted_refresh_tasks(refresh_task_id,research_run_id,dimension_id,claim_ids_json,reason,status,task_json,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
            (task_id, str(task.get("research_run_id") or ""), str(task.get("dimension_id") or ""), _json(task.get("claim_ids") or []), str(task.get("reason") or ""), str(task.get("status") or "queued"), _json(task), _now()),
        )
    return task_id


def update_refresh_task(task_id: str, status: str, payload: dict[str, Any] | None = None) -> None:
    with _LOCK, _connect() as conn, conn.cursor() as cur:
        cur.execute("UPDATE targeted_refresh_tasks SET status=%s,task_json=%s,completed_at=%s WHERE refresh_task_id=%s", (status, _json(payload or {}), _now() if status in {"completed", "failed", "cancelled"} else None, task_id))


def create_report_revision(run_id: str, content_hash: str, artifact_hash: str = "", parent_revision_id: str | None = None, qa_status: str = "pending", artifact_identity: str = "") -> str:
    revision_id = "revision-" + uuid.uuid4().hex
    with _LOCK, _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO report_revisions(revision_id,research_run_id,parent_revision_id,content_hash,artifact_hash,qa_status,artifact_identity,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
            (revision_id, run_id, parent_revision_id, content_hash, artifact_hash, qa_status, artifact_identity, _now()),
        )
    return revision_id


def save_artifact(revision_id: str, artifact_type: str, object_identity: str, content_hash: str = "") -> str:
    artifact_id = "artifact-" + uuid.uuid4().hex
    with _LOCK, _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO artifacts(artifact_id,revision_id,artifact_type,object_identity,content_hash,created_at) VALUES(%s,%s,%s,%s,%s,%s)",
            (artifact_id, revision_id, artifact_type, object_identity, content_hash, _now()),
        )
    return artifact_id


def evidence_snapshot(run_id: str) -> dict[str, dict[str, Any]]:
    with _LOCK, _connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM evidence_assets WHERE research_run_id=%s", (run_id,))
        rows = cur.fetchall()
    return {str(row["evidence_id"]): dict(row) for row in rows}



def create_report_job(job_id: str, question: str, metadata: dict | None = None) -> None:
    initialise()
    now = _now()
    with _LOCK, _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO report_jobs
            (job_id,question,status,stage,detail,progress_count,research_run_id,result_json,error,created_at,updated_at,completed_at)
            VALUES(%s,%s,'queued','queued','',0,NULL,%s,NULL,%s,%s,NULL)
            ON CONFLICT(job_id) DO NOTHING""",
            (job_id, str(question or ''), _json({"metadata": metadata or {}}), now, now),
        )


def attach_report_job_run(job_id: str, research_run_id: str) -> None:
    if not job_id or not research_run_id:
        return
    with _LOCK, _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE report_jobs SET research_run_id=%s,status='running',stage='planning',detail='Research planning started',updated_at=%s WHERE job_id=%s",
            (research_run_id, _now(), job_id),
        )


def update_report_job(job_id: str, *, status: str | None = None, stage: str | None = None,
                      detail: str | None = None, progress_count: int | None = None,
                      result: dict | None = None, error: str | None = None) -> None:
    if not job_id:
        return
    fields = []
    values = []
    if status is not None:
        fields.append('status=%s'); values.append(status)
    if stage is not None:
        fields.append('stage=%s'); values.append(stage)
    if detail is not None:
        fields.append('detail=%s'); values.append(detail)
    if progress_count is not None:
        fields.append('progress_count=%s'); values.append(int(progress_count))
    if result is not None:
        fields.append('result_json=%s'); values.append(_json(result))
    if error is not None:
        fields.append('error=%s'); values.append(error[:2000])
    if not fields:
        return
    fields.append('updated_at=%s'); values.append(_now())
    if status in {'completed', 'failed'}:
        fields.append('completed_at=%s'); values.append(_now())
    values.append(job_id)
    with _LOCK, _connect() as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE report_jobs SET {', '.join(fields)} WHERE job_id=%s", values)


def get_report_job(job_id: str) -> dict | None:
    initialise()
    with _LOCK, _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT job_id,question,status,stage,detail,progress_count,research_run_id,result_json,error,
                      created_at,updated_at,completed_at
               FROM report_jobs WHERE job_id=%s""",
            (job_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return dict(row)

def snapshot(run_id: str) -> dict:
    tables = (
        ("source_registry", "sources"), ("documents", "documents"), ("chunks", "chunks"),
        ("evidence_assets", "evidence"), ("claims", "claims"), ("claim_evidence", "claim_evidence"),
        ("provider_calls", "provider_calls"), ("stage_events", "stage_events"), ("dimension_retrieval", "dimension_retrieval"),
    )
    with _LOCK, _connect() as conn, conn.cursor() as cur:
        counts = {}
        for table, label in tables:
            if table == "claim_evidence":
                cur.execute("SELECT COUNT(*) AS n FROM claim_evidence WHERE claim_id IN (SELECT claim_id FROM claims WHERE research_run_id=%s)", (run_id,))
            else:
                cur.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE research_run_id=%s", (run_id,))
            counts[label] = int(_one(cur)["n"])
        cur.execute("SELECT COUNT(DISTINCT canonical_url) AS n FROM source_registry WHERE research_run_id=%s AND canonical_url<>''", (run_id,))
        counts["unique_sources"] = int(_one(cur)["n"])
        cur.execute("SELECT completeness_json,status FROM research_runs WHERE research_run_id=%s", (run_id,))
        row = _one(cur)
    counts.update({"research_run_id": run_id, "status": row["status"] if row else "unknown", "completeness": row["completeness_json"] if row and row.get("completeness_json") else {}})
    return counts


initialise()
