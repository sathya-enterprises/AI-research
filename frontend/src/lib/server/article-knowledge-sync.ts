import { createHash } from 'crypto';
import { promises as fs } from 'fs';
import path from 'path';
import { getBackendBaseUrl } from '@/lib/backend-url';

export interface KnowledgeArticle {
  id?: string;
  title: string;
  source?: string;
  url: string;
  time?: string;
  time_ms?: number;
  published_at?: string;
  tag?: string;
  category?: string;
  summary?: string;
  knowledgeSummary?: string;
  content?: string;
  paragraphs?: string[];
  full_content_fetched?: boolean;
  fullContentFetched?: boolean;
  source_url?: string;
  image?: string;
  metadata?: Record<string, unknown>;
}

export interface KnowledgeSyncResult {
  status: 'stored' | 'skipped' | 'partial' | 'failed' | 'syncing';
  received: number;
  accepted: number;
  storedSources: number;
  storedVersions: number;
  storedChunks: number;
  batchesSucceeded: number;
  batchesFailed: number;
  batchesQueued?: number;
  reason?: string;
}

const BATCH_SIZE = 75; // Leave headroom under the backend's 100-record request limit.
const MAX_PARALLEL_BATCHES = 3;
const REQUEST_TIMEOUT_MS = 15_000;
const MAX_TRANSIENT_BATCH_RETRIES = 1;
const SUCCESSFUL_BATCH_CACHE_TTL_MS = 30 * 60 * 1000;
const QUEUED_BATCH_CACHE_TTL_MS = 30 * 60 * 1000;
const SUCCESSFUL_BATCH_CACHE_MAX = 500;
const recentSuccessfulBatches = new Map<string, { at: number; storedSources: number; storedVersions: number; storedChunks: number }>();
const recentQueuedBatches = new Map<string, { at: number }>();

// ─── Backend-unreachable circuit breaker + local spool ───────────────────────
// When only the frontend is running (or the backend is restarting), every feed
// crawl used to fire 5 failing batches with retries and log ~10 lines each, and
// the scraped articles were simply lost. Now the first connection failure opens
// a short circuit (no further requests for BACKEND_DOWN_MS), the records are
// appended to a bounded local JSONL spool, and the spool is replayed to the
// backend after the next successful ingest.
const BACKEND_DOWN_MS = Math.max(10_000, Number(process.env.ARTICLE_KB_BACKOFF_MS || 60_000));
const SPOOL_MAX_RECORDS = Math.max(500, Number(process.env.ARTICLE_KB_SPOOL_MAX_RECORDS || 20_000));
let backendDownUntil = 0;
let lastDownNoticeAt = 0;
let spoolFlushing = false;
let spoolWriteChain: Promise<void> = Promise.resolve();

function spoolFile(): string {
  const dir = String(process.env.ARTICLE_KB_SPOOL_DIR || '').trim() || path.join(process.cwd(), '.cache');
  return path.join(dir, 'article-kb-spool.jsonl');
}

function isConnectionRefused(error: unknown): boolean {
  const cause = (error as { cause?: { code?: string } } | null)?.cause;
  const code = String(cause?.code || (error as { code?: string } | null)?.code || '');
  return ['ECONNREFUSED', 'ENOTFOUND', 'EAI_AGAIN', 'EHOSTUNREACH', 'ECONNRESET', 'UND_ERR_CONNECT_TIMEOUT'].includes(code);
}

function markBackendDown(reason: string, spooled: number) {
  backendDownUntil = Date.now() + BACKEND_DOWN_MS;
  if (Date.now() - lastDownNoticeAt > BACKEND_DOWN_MS) {
    lastDownNoticeAt = Date.now();
    console.warn(`[article-kb] backend not reachable (${reason}); ${spooled} article record(s) saved to the local spool and will be sent when the backend is back. Pausing ingest for ${Math.round(BACKEND_DOWN_MS / 1000)}s.`);
  }
}

/** Append records to the local spool (bounded; newest kept). Never throws. */
function spoolRecords(records: Record<string, unknown>[], crawlId: string): Promise<void> {
  if (!records.length || process.env.ARTICLE_KB_SPOOL === '0') return Promise.resolve();
  spoolWriteChain = spoolWriteChain.then(async () => {
    try {
      const file = spoolFile();
      await fs.mkdir(path.dirname(file), { recursive: true });
      const lines = records.map(record => JSON.stringify({ crawlId, record, at: Date.now() })).join('\n') + '\n';
      await fs.appendFile(file, lines, 'utf8');
      const stat = await fs.stat(file);
      if (stat.size > 40 * 1024 * 1024) {
        const all = (await fs.readFile(file, 'utf8')).split('\n').filter(Boolean);
        await fs.writeFile(file, all.slice(-SPOOL_MAX_RECORDS).join('\n') + '\n', 'utf8');
      }
    } catch (error) {
      console.warn(`[article-kb] could not write local spool (${error instanceof Error ? error.name : 'error'})`);
    }
  });
  return spoolWriteChain;
}

/** Number of records currently waiting in the spool (for diagnostics). */
export async function articleKnowledgeSpoolSize(): Promise<number> {
  try {
    return (await fs.readFile(spoolFile(), 'utf8')).split('\n').filter(Boolean).length;
  } catch {
    return 0;
  }
}

/** Replay spooled records once the backend is reachable again. Single-flight. */
async function flushSpool(accessToken: string | null): Promise<void> {
  if (spoolFlushing || Date.now() < backendDownUntil) return;
  spoolFlushing = true;
  const file = spoolFile();
  const sending = `${file}.sending`;
  try {
    await spoolWriteChain;
    try { await fs.rename(file, sending); } catch { return; }
    const lines = (await fs.readFile(sending, 'utf8')).split('\n').filter(Boolean);
    // De-duplicate by URL, keeping the richest record (full article over RSS summary).
    const byUrl = new Map<string, { crawlId: string; record: Record<string, unknown> }>();
    for (const line of lines.slice(-SPOOL_MAX_RECORDS)) {
      try {
        const row = JSON.parse(line) as { crawlId?: string; record?: Record<string, unknown> };
        const record = row.record;
        if (!record || typeof record.url !== 'string') continue;
        const prev = byUrl.get(record.url);
        const size = String(record.content || record.summary || '').length;
        if (!prev || size >= String(prev.record.content || prev.record.summary || '').length) {
          byUrl.set(record.url, { crawlId: String(row.crawlId || 'spool'), record });
        }
      } catch { /* skip corrupt line */ }
    }
    await fs.unlink(sending).catch(() => undefined);
    const records = [...byUrl.values()].map(item => item.record);
    if (!records.length) return;
    const result = await sendRecords(accessToken, records, `spool-replay-${Date.now()}`);
    console.info(`[article-kb] replayed ${records.length} spooled article record(s): status=${result.status}${result.reason ? ` (${result.reason})` : ''}`);
  } catch (error) {
    console.warn(`[article-kb] spool replay failed (${error instanceof Error ? error.name : 'error'})`);
  } finally {
    spoolFlushing = false;
  }
}

/** Server-only shared credential for background crawl ingestion. Never use a NEXT_PUBLIC_ name. */
function articleKnowledgeIngestToken(): string {
  return String(process.env.ARTICLE_KB_INGEST_TOKEN || '').trim();
}

/** True when this server route can authenticate an ingestion request. */
export function canSyncScrapedArticlesToKnowledgeBase(accessToken: string | null): boolean {
  return Boolean(articleKnowledgeIngestToken() || accessToken);
}

function articleBatchFingerprint(batch: Record<string, unknown>[], crawlId: string): string {
  // Hash content/version metadata only. Never log the fingerprint or auth token.
  return createHash('sha256').update(JSON.stringify({ crawlId, batch })).digest('hex');
}

function toRecord(article: KnowledgeArticle): Record<string, unknown> | null {
  const title = String(article.title || '').replace(/\s+/g, ' ').trim().slice(0, 500);
  const url = String(article.url || article.source_url || '').trim();
  try {
    const parsed = new URL(url);
    if (!['https:', 'http:'].includes(parsed.protocol) || !parsed.hostname || parsed.username || parsed.password) return null;
  } catch {
    return null;
  }
  if (title.length < 3) return null;

  const paragraphs = Array.isArray(article.paragraphs) ? article.paragraphs : [];
  const content = String(article.content || paragraphs.join('\n\n') || '').replace(/\u0000/g, '').trim().slice(0, 14_000);
  const summary = String(article.knowledgeSummary || article.summary || '').replace(/\s+/g, ' ').trim().slice(0, 4_000);
  const fullFetched = Boolean(article.full_content_fetched || article.fullContentFetched) && content.length >= 500;
  const publishedAt = article.published_at || (Number.isFinite(article.time_ms) && article.time_ms! > 0 ? new Date(article.time_ms!).toISOString() : '');

  return {
    title,
    url: url.slice(0, 2048),
    source: String(article.source || '').slice(0, 180),
    published_at: String(publishedAt || '').slice(0, 100),
    category: String(article.category || '').slice(0, 80),
    tag: String(article.tag || '').slice(0, 80),
    summary,
    content: fullFetched ? content : '',
    full_content_fetched: fullFetched,
    metadata: {
      article_id: String(article.id || '').slice(0, 180),
      source_url: String(article.source_url || '').slice(0, 2048),
      has_image: Boolean(article.image),
      capture_stage: fullFetched ? 'reader_full_article' : 'feed_rss',
    },
  };
}

/**
 * Send genuine scraper output to the backend-only Supabase/Postgres research
 * library. HTTP 202 means queued, not stored; callers must consult the backend's
 * crawl status endpoint for durable completion. One transient network retry avoids
 * dropping an entire crawl after a single timeout. No database credential is sent
 * to the browser.
 */
export async function syncScrapedArticlesToKnowledgeBase(
  accessToken: string | null,
  articles: KnowledgeArticle[],
  crawlId = '',
): Promise<KnowledgeSyncResult> {
  const internalToken = articleKnowledgeIngestToken();
  if (!internalToken && !accessToken) {
    return { status: 'skipped', received: articles.length, accepted: 0, storedSources: 0, storedVersions: 0, storedChunks: 0, batchesSucceeded: 0, batchesFailed: 0, reason: 'article_kb_ingest_auth_unconfigured' };
  }
  const mapped = (articles || []).map(toRecord).filter((item): item is Record<string, unknown> => Boolean(item));
  if (!mapped.length) {
    return { status: 'skipped', received: articles.length, accepted: 0, storedSources: 0, storedVersions: 0, storedChunks: 0, batchesSucceeded: 0, batchesFailed: 0, reason: 'no_valid_article_records' };
  }
  if (Date.now() < backendDownUntil) {
    // Circuit open: don't hammer an unreachable backend; keep the records.
    await spoolRecords(mapped, crawlId);
    return { status: 'failed', received: articles.length, accepted: mapped.length, storedSources: 0, storedVersions: 0, storedChunks: 0, batchesSucceeded: 0, batchesFailed: 0, reason: 'backend_unreachable_spooled' };
  }
  const result = await sendRecords(accessToken, mapped, crawlId);
  if ((result.batchesSucceeded > 0 || (result.batchesQueued || 0) > 0) && !spoolFlushing) {
    void flushSpool(accessToken);
  }
  return { ...result, received: articles.length };
}

async function sendRecords(
  accessToken: string | null,
  mapped: Record<string, unknown>[],
  crawlId: string,
): Promise<KnowledgeSyncResult> {
  const internalToken = articleKnowledgeIngestToken();
  const deliveredBatches = new Set<number>();

  const batches: Record<string, unknown>[][] = [];
  for (let i = 0; i < mapped.length; i += BATCH_SIZE) batches.push(mapped.slice(i, i + BATCH_SIZE));

  let storedSources = 0;
  let storedVersions = 0;
  let storedChunks = 0;
  let batchesSucceeded = 0;
  let batchesFailed = 0;
  let batchesQueued = 0;
  let cursor = 0;
  // A route/auth-level failure applies to every batch. Stop the fan-out after
  // the first such response instead of issuing dozens of identical failing POSTs.
  let fatalIngestFailure: string | null = null;
  let consecutiveTransientNetworkFailures = 0;
  const worker = async () => {
    while (true) {
      if (fatalIngestFailure) return;
      const index = cursor++;
      if (index >= batches.length) return;
      const fingerprint = articleBatchFingerprint(batches[index], crawlId);
      const now = Date.now();
      for (const [key, value] of recentSuccessfulBatches) {
        if (now - value.at > SUCCESSFUL_BATCH_CACHE_TTL_MS) recentSuccessfulBatches.delete(key);
      }
      for (const [key, value] of recentQueuedBatches) {
        if (now - value.at > QUEUED_BATCH_CACHE_TTL_MS) recentQueuedBatches.delete(key);
      }
      const cachedSuccess = recentSuccessfulBatches.get(fingerprint);
      if (cachedSuccess && now - cachedSuccess.at <= SUCCESSFUL_BATCH_CACHE_TTL_MS) {
        storedSources += cachedSuccess.storedSources;
        storedVersions += cachedSuccess.storedVersions;
        storedChunks += cachedSuccess.storedChunks;
        batchesSucceeded += 1;
        deliveredBatches.add(index);
        continue;
      }
      const cachedQueued = recentQueuedBatches.get(fingerprint);
      if (cachedQueued && now - cachedQueued.at <= QUEUED_BATCH_CACHE_TTL_MS) {
        batchesQueued += 1;
        deliveredBatches.add(index);
        continue;
      }
      try {
        const url = `${getBackendBaseUrl()}/api/knowledge/articles/ingest`;
        let response: Response | null = null;
        let lastNetworkError: unknown = null;
        for (let attempt = 0; attempt <= MAX_TRANSIENT_BATCH_RETRIES; attempt += 1) {
          try {
            response = await fetch(url, {
              method: 'POST',
              headers: {
                ...(internalToken
                  ? { 'X-Article-KB-Token': internalToken }
                  : accessToken
                    ? { Authorization: `Bearer ${accessToken}` }
                    : {}),
                'Content-Type': 'application/json',
                // Backend treats duplicate source/version writes idempotently.
                'X-Article-KB-Batch': fingerprint,
              },
              body: JSON.stringify({ articles: batches[index], crawl_id: crawlId }),
              signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
              cache: 'no-store',
            });
            lastNetworkError = null;
            consecutiveTransientNetworkFailures = 0;
            break;
          } catch (error) {
            lastNetworkError = error;
            const err = error instanceof Error ? error : new Error(String(error));
            const transientNetworkError = ['TypeError', 'TimeoutError', 'AbortError'].includes(err.name);
            // "Connection refused" is deterministic (backend not running): no retry.
            if (!transientNetworkError || isConnectionRefused(error) || attempt >= MAX_TRANSIENT_BATCH_RETRIES) throw error;
            console.warn(`[article-kb] transient network failure for batch ${index + 1}/${batches.length} (${err.name}); retrying once`);
            await new Promise((resolve) => setTimeout(resolve, 400));
          }
        }
        if (!response) throw lastNetworkError || new Error('ingest request did not return a response');
        if (!response.ok) {
          batchesFailed += 1;
          // Do not log response bodies, tokens, URLs or database details.
          if ([401, 403, 404, 405].includes(response.status)) {
            fatalIngestFailure = `backend_http_${response.status}`;
            console.warn(`[article-kb] ingest stopped after batch ${index + 1}/${batches.length} returned HTTP ${response.status}; remaining batches were not sent`);
            return;
          }
          console.warn(`[article-kb] ingest batch ${index + 1}/${batches.length} returned HTTP ${response.status}`);
          continue;
        }
        const data = await response.json() as {
          stored_sources?: number; stored_versions?: number; stored_chunks?: number; ok?: boolean;
          persisted?: { sources?: number; versions?: number; chunks?: number };
          // The backend answers queued ingests with { ok, queued: true, status: 'queued' }.
          queued?: boolean; status?: string;
        };
        if (!data.ok) {
          batchesFailed += 1;
          console.warn(`[article-kb] ingest batch ${index + 1}/${batches.length} returned HTTP 200 without ok=true`);
          continue;
        }
        if (data.queued === true || data.status === 'queued') {
          batchesQueued += 1;
          deliveredBatches.add(index);
          recentQueuedBatches.set(fingerprint, { at: Date.now() });
          while (recentQueuedBatches.size > SUCCESSFUL_BATCH_CACHE_MAX) {
            const oldest = recentQueuedBatches.keys().next().value;
            if (oldest === undefined) break;
            recentQueuedBatches.delete(oldest);
          }
          continue;
        }
        const saved = {
          at: Date.now(),
          storedSources: Number(data.stored_sources || data.persisted?.sources || 0),
          storedVersions: Number(data.stored_versions || data.persisted?.versions || 0),
          storedChunks: Number(data.stored_chunks || data.persisted?.chunks || 0),
        };
        storedSources += saved.storedSources;
        storedVersions += saved.storedVersions;
        storedChunks += saved.storedChunks;
        batchesSucceeded += 1;
        deliveredBatches.add(index);
        recentSuccessfulBatches.set(fingerprint, saved);
        while (recentSuccessfulBatches.size > SUCCESSFUL_BATCH_CACHE_MAX) {
          const oldest = recentSuccessfulBatches.keys().next().value;
          if (oldest === undefined) break;
          recentSuccessfulBatches.delete(oldest);
        }
      } catch (error) {
        batchesFailed += 1;
        const err = error instanceof Error ? error : new Error(String(error));
        const safeMessage = String(err.message || 'no error message')
          .replace(/Bearer\s+\S+/gi, 'Bearer [redacted]')
          .replace(/https?:\/\/\S+/gi, '[url]')
          .replace(/((?:api[_-]?key|authorization|token)\s*[:=]\s*)\S+/gi, '$1[redacted]')
          .slice(0, 180);
        const stackLocation = String(err.stack || '').split('\n').slice(1, 3).join(' ').replace(/https?:\/\/\S+/gi, '[url]').slice(0, 180);
        if (['TypeError', 'AbortError', 'TimeoutError'].includes(err.name)) {
          consecutiveTransientNetworkFailures += 1;
          if (isConnectionRefused(error)) {
            // Backend not running/reachable: stop at once, spool below, log once.
            fatalIngestFailure = 'backend_unreachable';
            return;
          }
          if (consecutiveTransientNetworkFailures >= 3) {
            fatalIngestFailure = `client_${err.name.toLowerCase()}`;
            console.warn(`[article-kb] ingest paused after ${consecutiveTransientNetworkFailures} transient network failures; last batch ${index + 1}/${batches.length} (${err.name}): ${safeMessage}${stackLocation ? ` | ${stackLocation}` : ''}; remaining batches were not sent`);
            return;
          }
          // One timed-out batch should not prevent unrelated source batches from
          // being accepted. Stop only after repeated network failures suggest the
          // backend cannot be reached, avoiding dozens of doomed retry attempts.
          console.warn(`[article-kb] ingest batch ${index + 1}/${batches.length} failed transiently (${err.name}); continuing with remaining batches (${consecutiveTransientNetworkFailures}/3 failures)`);
          continue;
        }
        if (err.name === 'SyntaxError') {
          fatalIngestFailure = 'client_invalid_response';
          console.warn(`[article-kb] ingest stopped after batch ${index + 1}/${batches.length} returned an invalid response; remaining batches were not sent`);
          return;
        }
        console.warn(`[article-kb] ingest batch ${index + 1}/${batches.length} failed (${err.name}): ${safeMessage}${stackLocation ? ` | ${stackLocation}` : ''}`);
      }
    }
  };
  await Promise.all(Array.from({ length: Math.min(MAX_PARALLEL_BATCHES, batches.length) }, () => worker()));

  if (fatalIngestFailure === 'backend_unreachable' || (fatalIngestFailure || '').startsWith('client_')) {
    const undelivered = batches.filter((_batch, index) => !deliveredBatches.has(index)).flat();
    await spoolRecords(undelivered, crawlId);
    markBackendDown(fatalIngestFailure || 'network', undelivered.length);
    fatalIngestFailure = 'backend_unreachable_spooled';
  }

  return {
    status: batchesFailed === 0 && !fatalIngestFailure ? (batchesQueued > 0 ? 'syncing' : mapped.length > 0 ? 'stored' : 'skipped') : (batchesSucceeded > 0 || batchesQueued > 0 ? 'partial' : 'failed'),
    received: mapped.length,
    accepted: mapped.length,
    storedSources,
    storedVersions,
    storedChunks,
    batchesSucceeded,
    batchesFailed,
    batchesQueued,
    ...(fatalIngestFailure ? { reason: fatalIngestFailure } : batchesFailed ? { reason: 'one_or_more_backend_batches_failed' } : {}),
  };
}
