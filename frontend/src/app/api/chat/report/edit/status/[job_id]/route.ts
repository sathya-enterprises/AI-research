import { getBackendBaseUrl } from '@/lib/backend-url';
/**
 * Poll a queued Action edit started via /api/chat/report/edit/start until the
 * atomic edit is committed or safely rejected.
 *
 * Reconstructed 2026-10-21: this dynamic route folder was missing from the
 * uploaded archive. It mirrors ../../start/route.ts. Keep your own copy if it differs.
 */
import { NextRequest, NextResponse } from 'next/server';
import { createLogger, logRequest } from '@/lib/logger';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
export const maxDuration = 30;

const log = createLogger('api/chat/report/edit/status');
const BACKEND = getBackendBaseUrl();

export async function GET(_req: NextRequest, { params }: { params: Promise<{ job_id: string }> }) {
  const { job_id: jobId } = await params;
  const done = logRequest(log, 'GET', `/api/chat/report/edit/status/${jobId}`);
  try {
    const upstream = await fetch(`${BACKEND}/api/chat/report/edit/status/${encodeURIComponent(jobId)}`, {
      cache: 'no-store',
      signal: AbortSignal.timeout(15_000),
    });
    const contentType = String(upstream.headers.get('content-type') || '').toLowerCase();
    const text = await upstream.text();
    if (!contentType.includes('application/json')) {
      const looksLikeHtml = /<!doctype\s+html|<html[\s>]/i.test(text.slice(0, 1000)) || contentType.includes('text/html');
      done(502, looksLikeHtml ? 'upstream returned HTML' : 'upstream returned non-JSON');
      return NextResponse.json({
        error: looksLikeHtml
          ? 'The Action status endpoint returned a web page instead of JSON. Check BACKEND_URL and confirm the latest FastAPI backend is running.'
          : `The Action status endpoint returned an unexpected response type (${contentType || 'missing'}).`,
        expectedContentType: 'application/json',
        upstreamStatus: upstream.status,
      }, { status: 502, headers: { 'Cache-Control': 'no-store' } });
    }
    let payload: unknown;
    try { payload = JSON.parse(text); }
    catch {
      done(502, 'invalid upstream JSON');
      return NextResponse.json({ error: 'The Action status endpoint returned invalid JSON.' }, { status: 502, headers: { 'Cache-Control': 'no-store' } });
    }
    done(upstream.status, upstream.ok ? 'status' : 'status failed');
    return NextResponse.json(payload, { status: upstream.status, headers: { 'Cache-Control': 'no-store' } });
  } catch (err) {
    log.error('Action status proxy failed: %s', err);
    done(502, 'backend unavailable');
    return NextResponse.json({ error: 'Backend unavailable while checking the Action status.' }, { status: 502, headers: { 'Cache-Control': 'no-store' } });
  }
}
