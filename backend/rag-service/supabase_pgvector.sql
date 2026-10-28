-- Run once in Supabase SQL Editor before starting the pgvector RAG service.
-- Embedding dimension 384 matches sentence-transformers/all-MiniLM-L6-v2.

create extension if not exists vector with schema extensions;

create table if not exists public.rag_chunks (
    session_id text not null,
    chunk_id text not null,
    doc_id text not null,
    text text not null,
    source_name text not null default 'Unknown',
    source_type text not null default 'file',
    file_type text not null default '',
    chunk_index integer not null default 0,
    total_chunks integer not null default 0,
    source_sha256 text not null default '',
    original_location text,
    source_metadata jsonb not null default '{}'::jsonb,
    embedding extensions.vector(384) not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    primary key (session_id, chunk_id)
);

create index if not exists rag_chunks_session_doc_idx
    on public.rag_chunks (session_id, doc_id);
create index if not exists rag_chunks_session_chunk_idx
    on public.rag_chunks (session_id, chunk_index);
create index if not exists rag_chunks_embedding_hnsw_idx
    on public.rag_chunks using hnsw (embedding extensions.vector_cosine_ops);

alter table public.rag_chunks enable row level security;
revoke all on table public.rag_chunks from anon, authenticated;
grant all on table public.rag_chunks to service_role;

create or replace function public.rag_match_chunks(
    query_embedding extensions.vector(384),
    match_session_id text,
    match_count integer default 8
)
returns table (
    session_id text,
    chunk_id text,
    doc_id text,
    text text,
    source_name text,
    source_type text,
    file_type text,
    chunk_index integer,
    total_chunks integer,
    source_sha256 text,
    original_location text,
    source_metadata jsonb,
    similarity double precision
)
language sql
stable
set search_path = public, extensions
set hnsw.iterative_scan = 'strict_order'
set hnsw.ef_search = '100'
as $$
    select
        c.session_id,
        c.chunk_id,
        c.doc_id,
        c.text,
        c.source_name,
        c.source_type,
        c.file_type,
        c.chunk_index,
        c.total_chunks,
        c.source_sha256,
        c.original_location,
        c.source_metadata,
        (1 - (c.embedding <=> query_embedding))::double precision as similarity
    from public.rag_chunks as c
    where c.session_id = match_session_id
    order by c.embedding <=> query_embedding asc
    limit greatest(1, least(coalesce(match_count, 8), 100));
$$;

revoke all on function public.rag_match_chunks(extensions.vector, text, integer) from public, anon, authenticated;
grant execute on function public.rag_match_chunks(extensions.vector, text, integer) to service_role;

comment on table public.rag_chunks is 'Persistent session-scoped RAG chunks and embeddings; vectors are stored only in Postgres pgvector.';
