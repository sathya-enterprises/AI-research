# Persistent Web Research Library — v16

Growth Gradual now keeps a durable cross-report web research library in the existing Supabase/Postgres research store.

## What is stored

For every successful Tavily/SerpAPI web-search result, the backend can persist:

- canonical source URL
- title/publisher/domain
- publication date when supplied
- the provider's returned content/snippet
- content hash and source versions
- chunked evidence text for later retrieval
- the original search query and result rank
- first/last seen timestamps

Report generation also persists the richer page content obtained during source enrichment.

## Reuse behavior

A later report or Action can search the persistent library before relying only on a fresh web search.

- historical/structural requests may reuse the durable library
- recent/current requests use a bounded freshness window and still permit fresh web search
- Action editing can reuse stored evidence and then perform a fresh web refresh when the instruction requires it

Stored research never replaces evidence validation. New factual/quantitative claims still need support from the retrieved evidence.

## Failure behavior

Research-library persistence is best-effort and non-blocking. If Supabase/Postgres is unavailable, web search, report generation and Action editing continue normally and log the persistence failure.

## Storage location

The canonical store remains the `gg_research` schema in Supabase/Postgres. No web-search content is written to the frontend.
