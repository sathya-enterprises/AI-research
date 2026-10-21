-- Growth Gradual canonical research store for Supabase/PostgreSQL.
-- The backend executes this schema automatically on startup. A stale/incompatible
-- gg_research schema is non-destructively quarantined and recreated automatically.
--
-- IMPORTANT: the canonical research store lives in its own schema so it cannot
-- collide with pre-existing public tables from older app versions.

create schema if not exists gg_research;
set search_path to gg_research, public;

create table if not exists research_runs (
  research_run_id text primary key,
  question text not null,
  started_at timestamptz not null,
  completed_at timestamptz,
  status text not null,
  metadata_json jsonb not null default '{}'::jsonb,
  completeness_json jsonb not null default '{}'::jsonb,
  last_heartbeat_at timestamptz,
  worker_id text,
  failure_reason text,
  updated_at timestamptz not null default now()
);

create table if not exists source_registry (
  source_id text not null,
  research_run_id text not null references research_runs(research_run_id) on delete cascade,
  canonical_url text not null,
  title text,
  publisher text,
  organization text,
  source_type text,
  published_at text,
  retrieved_at text,
  domain text,
  acquisition_method text,
  http_status integer,
  content_hash text,
  authority_score double precision,
  relevance_score double precision,
  source_role text,
  stage text not null,
  metadata_json jsonb not null default '{}'::jsonb,
  primary key (research_run_id, source_id)
);
create index if not exists idx_source_url on source_registry(research_run_id, canonical_url);

create table if not exists documents (
  document_id text primary key,
  research_run_id text not null references research_runs(research_run_id) on delete cascade,
  source_id text not null,
  raw_text text,
  extracted_text text,
  page_info_json jsonb not null default '{}'::jsonb,
  acquisition_metadata_json jsonb not null default '{}'::jsonb,
  content_hash text,
  created_at timestamptz not null
);
create index if not exists idx_documents_run on documents(research_run_id);

create table if not exists chunks (
  chunk_id text primary key,
  research_run_id text not null references research_runs(research_run_id) on delete cascade,
  source_id text not null,
  document_id text not null,
  ordinal integer not null,
  section text,
  page integer,
  text text not null,
  metadata_json jsonb not null default '{}'::jsonb,
  embedding_ref text
);
create index if not exists idx_chunks_run on chunks(research_run_id);
create index if not exists idx_chunks_fts on chunks using gin(to_tsvector('simple', text || ' ' || metadata_json::text));

create table if not exists evidence_assets (
  evidence_id text primary key,
  research_run_id text not null references research_runs(research_run_id) on delete cascade,
  source_id text not null,
  source_version_id text,
  chunk_id text,
  topic text,
  evidence_type text,
  quantitative_json jsonb not null default '{}'::jsonb,
  provenance_json jsonb not null default '{}'::jsonb,
  text text,
  created_at timestamptz not null
);
create index if not exists idx_evidence_run on evidence_assets(research_run_id);

create table if not exists claims (
  claim_id text primary key,
  research_run_id text not null references research_runs(research_run_id) on delete cascade,
  claim_text text not null,
  research_dimension text,
  claim_type text,
  support_status text not null default 'unknown',
  metadata_json jsonb not null default '{}'::jsonb,
  created_at timestamptz not null
);
create index if not exists idx_claims_run_dimension on claims(research_run_id, research_dimension);

create table if not exists claim_evidence (
  claim_id text not null references claims(claim_id) on delete cascade,
  evidence_id text not null references evidence_assets(evidence_id) on delete cascade,
  relationship text not null,
  primary key (claim_id, evidence_id, relationship)
);

create table if not exists stage_events (
  event_id bigserial primary key,
  research_run_id text not null references research_runs(research_run_id) on delete cascade,
  stage text not null,
  count integer not null,
  payload_json jsonb not null default '{}'::jsonb,
  created_at timestamptz not null
);
create index if not exists idx_stage_events_run on stage_events(research_run_id, created_at);

create table if not exists provider_calls (
  call_id bigserial primary key,
  research_run_id text not null references research_runs(research_run_id) on delete cascade,
  provider text not null,
  query text,
  page_or_offset text,
  status text,
  candidate_count integer not null default 0,
  metadata_json jsonb not null default '{}'::jsonb,
  created_at timestamptz not null
);
create index if not exists idx_provider_calls_run on provider_calls(research_run_id, created_at);

create table if not exists dimension_retrieval (
  retrieval_id bigserial primary key,
  research_run_id text not null references research_runs(research_run_id) on delete cascade,
  dimension_id text not null,
  chunk_id text not null,
  retrieval_score double precision not null default 0,
  retrieval_method text not null,
  match_reason text not null default '',
  rerank_score double precision not null default 0,
  metadata_json jsonb not null default '{}'::jsonb,
  created_at timestamptz not null,
  unique(research_run_id, dimension_id, chunk_id)
);
create index if not exists idx_dimension_retrieval_run on dimension_retrieval(research_run_id, dimension_id, rerank_score desc);

create table if not exists source_versions (
  source_version_id text primary key,
  source_id text not null,
  research_run_id text not null references research_runs(research_run_id) on delete cascade,
  canonical_url text not null,
  publisher text,
  title text,
  published_at text,
  retrieved_at text,
  content_hash text,
  source_role text,
  data_as_of text,
  metadata_json jsonb not null default '{}'::jsonb,
  created_at timestamptz not null
);
create index if not exists idx_source_versions_source on source_versions(source_id, retrieved_at);



-- Persistent cross-report web research library. These tables intentionally live
-- outside a single research_run so web evidence discovered for one report can
-- be reused by later reports/actions, while each observed retrieval remains
-- traceable through web_search_events and per-source versions.
create table if not exists web_source_library (
  source_key text primary key,
  canonical_url text not null unique,
  title text,
  publisher text,
  domain text,
  source_type text not null default 'web',
  published_at text,
  first_seen_at timestamptz not null,
  last_seen_at timestamptz not null,
  latest_content_hash text,
  metadata_json jsonb not null default '{}'::jsonb
);
create index if not exists idx_web_source_library_domain on web_source_library(domain);
create index if not exists idx_web_source_library_last_seen on web_source_library(last_seen_at desc);
-- Targeted indexes for the paginated scraped-news knowledge-base API. Partial
-- indexes keep the general-purpose research library fast while making article
-- category filters and newest-first page traversal cheaper.
create index if not exists idx_web_source_library_market_news_latest
  on web_source_library(last_seen_at desc, source_key asc)
  where source_type = 'market_news_article';
create index if not exists idx_web_source_library_market_news_category
  on web_source_library(lower(coalesce(metadata_json->>'category','')), last_seen_at desc)
  where source_type = 'market_news_article';

create table if not exists web_source_versions (
  source_version_id text primary key,
  source_key text not null references web_source_library(source_key) on delete cascade,
  research_run_id text,
  query_text text,
  retrieved_at timestamptz not null,
  content_hash text not null,
  content_text text not null default '',
  metadata_json jsonb not null default '{}'::jsonb
);
create unique index if not exists uq_web_source_version_hash on web_source_versions(source_key, content_hash);
create index if not exists idx_web_source_versions_source on web_source_versions(source_key, retrieved_at desc);
create index if not exists idx_web_source_versions_fts on web_source_versions using gin(to_tsvector('simple', content_text || ' ' || coalesce(metadata_json::text, '')));

create table if not exists web_source_chunks (
  chunk_id text primary key,
  source_version_id text not null references web_source_versions(source_version_id) on delete cascade,
  ordinal integer not null,
  text text not null,
  metadata_json jsonb not null default '{}'::jsonb
);
create index if not exists idx_web_source_chunks_fts on web_source_chunks using gin(to_tsvector('simple', text || ' ' || coalesce(metadata_json::text, '')));

create table if not exists web_search_events (
  event_id bigserial primary key,
  query_text text not null,
  source_key text not null references web_source_library(source_key) on delete cascade,
  result_rank integer,
  provider text not null default 'exa',
  searched_at timestamptz not null,
  research_run_id text,
  metadata_json jsonb not null default '{}'::jsonb
);
create index if not exists idx_web_search_events_query on web_search_events(searched_at desc);
create index if not exists idx_web_search_events_source on web_search_events(source_key, searched_at desc);

create table if not exists targeted_refresh_tasks (
  refresh_task_id text primary key,
  research_run_id text not null references research_runs(research_run_id) on delete cascade,
  dimension_id text not null,
  claim_ids_json jsonb not null default '[]'::jsonb,
  reason text not null,
  status text not null,
  task_json jsonb not null default '{}'::jsonb,
  created_at timestamptz not null,
  completed_at text
);
create index if not exists idx_refresh_tasks_run on targeted_refresh_tasks(research_run_id, created_at);

create table if not exists report_revisions (
  revision_id text primary key,
  research_run_id text not null references research_runs(research_run_id) on delete cascade,
  parent_revision_id text,
  content_hash text,
  artifact_hash text,
  qa_status text not null default 'pending',
  artifact_identity text,
  created_at timestamptz not null
);
create index if not exists idx_report_revisions_run on report_revisions(research_run_id, created_at);

create table if not exists artifacts (
  artifact_id text primary key,
  revision_id text not null references report_revisions(revision_id) on delete cascade,
  artifact_type text not null,
  object_identity text not null,
  content_hash text,
  created_at timestamptz not null
);
create index if not exists idx_artifacts_revision on artifacts(revision_id);

create table if not exists report_jobs (
  job_id text primary key,
  question text not null,
  status text not null,
  stage text not null default 'queued',
  detail text not null default '',
  progress_count integer not null default 0,
  research_run_id text references research_runs(research_run_id) on delete set null,
  result_json jsonb not null default '{}'::jsonb,
  error text,
  created_at timestamptz not null,
  updated_at timestamptz not null,
  completed_at timestamptz
);
create index if not exists idx_report_jobs_status on report_jobs(status, updated_at);
create index if not exists idx_report_jobs_run on report_jobs(research_run_id);
