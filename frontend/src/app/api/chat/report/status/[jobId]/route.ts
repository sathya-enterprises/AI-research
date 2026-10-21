import { getBackendBaseUrl } from '@/lib/backend-url';
/**
 * Durable report-job status. The browser polls this when the SSE progress
 * stream is interrupted (proxy timeout, network change) so a long Brief,
 * In-Detail or Deep Research job is never lost.
 *
 * Reconstructed 2026-10-21: this dynamic route folder was missing from the
 * uploaded archive. It mirrors ../../start/route.ts and the contract checked
 * by scripts/test-report-stream-html-fallback.cjs. Keep your own copy if it differs.
 */
import { NextRequest, NextResponse } from 'next/server';
import { createLogger, logRequest } from '@/lib/logger';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
export const maxDuration = 30;

const log = createLogger('api/chat/report/status');
const BACKEND = getBackendBaseUrl();

export async function GET(_req: NextRequest, { params }: { params: Promise<{ jobId: string }> }) {
  const { jobId } = await params;
  const done = logRequest(log, 'GET', `/api/chat/report/status/${jobId}`);
  try {
    const upstream = await fetch(`${BACKEND}/api/chat/report/status/${encodeURIComponent(jobId)}`, {
      cache: 'no-store',
      signal: AbortSignal.timeout(15_000),
    });
    const contentType = String(upstream.headers.get('content-type') || '').toLowerCase();
    const text = await upstream.text();
    // Never wrap an upstream HTML app shell into an assistant-visible error.
    if (!contentType.includes('application/json')) {
      const looksLikeHtml = /<!doctype\s+html|<html[\s>]/i.test(text.slice(0, 1000))
        || contentType.includes('text/html');
      log.error('Report status proxy received non-JSON upstream response (status=%d, type=%s, html=%s)',
        upstream.status, contentType || '(missing)', looksLikeHtml);
      done(502, looksLikeHtml ? 'upstream returned HTML' : 'upstream returned non-JSON');
      return NextResponse.json({
        error: looksLikeHtml
          ? 'The report status endpoint returned a web page instead of JSON. Check BACKEND_URL and confirm the latest FastAPI backend is running.'
          : `The report status endpoint returned an unexpected response type (${contentType || 'missing'}).`,
        expectedContentType: 'application/json',
        upstreamStatus: upstream.status,
      }, { status: 502, headers: { 'Cache-Control': 'no-store' } });
    }
    let payload: unknown;
    try { payload = JSON.parse(text); }
    catch (err) {
      log.error('Report status proxy received malformed JSON: %s', err);
      done(502, 'invalid upstream JSON');
      return NextResponse.json({ error: 'The report status endpoint returned invalid JSON. Check the backend deployment and retry.' },
        { status: 502, headers: { 'Cache-Control': 'no-store' } });
    }
    done(upstream.status, upstream.ok ? 'status' : 'status failed');
    return NextResponse.json(payload, { status: upstream.status, headers: { 'Cache-Control': 'no-store' } });
  } catch (err) {
    log.error('Report status proxy failed: %s', err);
    done(502, 'backend unavailable');
    return NextResponse.json({ error: 'Backend unavailable while checking report status.' }, { status: 502, headers: { 'Cache-Control': 'no-store' } });
  }
}
