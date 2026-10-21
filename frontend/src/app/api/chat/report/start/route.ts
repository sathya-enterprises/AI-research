import { getBackendBaseUrl } from '@/lib/backend-url';
/**
 * Start a report job for Brief, In-Detail or Deep Research. The backend returns
 * immediately with a job ID; the browser reads progress and preview chunks from
 * the SSE stream, with status polling available as a fallback.
 */
import { NextRequest, NextResponse } from 'next/server';
import { createLogger, logRequest } from '@/lib/logger';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
export const maxDuration = 30;

const log = createLogger('api/chat/report/start');
const BACKEND = getBackendBaseUrl();

export async function POST(req: NextRequest) {
  const done = logRequest(log, 'POST', '/api/chat/report/start');
  try {
    const body = await req.arrayBuffer();
    const upstream = await fetch(`${BACKEND}/api/chat/report/start`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body,
      signal: AbortSignal.timeout(15_000),
    });
    const contentType = String(upstream.headers.get('content-type') || '').toLowerCase();
    const text = await upstream.text();
    // Never wrap an upstream HTML app shell into JSON as `error: "<html…>"`.
    // The chat renders API error messages, so this response shape previously
    // caused the entire Growth Gradual page source to appear in the assistant bubble.
    if (!contentType.includes('application/json')) {
      const looksLikeHtml = /<!doctype\s+html|<html[\s>]/i.test(text.slice(0, 1000))
        || contentType.includes('text/html');
      log.error('Report start proxy received non-JSON upstream response (status=%d, type=%s, html=%s, prefix=%s)',
        upstream.status, contentType || '(missing)', looksLikeHtml, text.slice(0, 100).replace(/\s+/g, ' '));
      done(502, looksLikeHtml ? 'upstream returned HTML' : 'upstream returned non-JSON');
      return NextResponse.json({
        error: looksLikeHtml
          ? 'The report start endpoint returned a web page instead of JSON. Check BACKEND_URL and confirm the latest FastAPI backend is running.'
          : `The report start endpoint returned an unexpected response type (${contentType || 'missing'}).`,
        expectedContentType: 'application/json',
        upstreamStatus: upstream.status,
      }, { status: 502, headers: { 'Cache-Control': 'no-store' } });
    }

    let payload: unknown;
    try { payload = JSON.parse(text); }
    catch (err) {
      log.error('Report start proxy received malformed JSON: %s', err);
      done(502, 'invalid upstream JSON');
      return NextResponse.json({ error: 'The report start endpoint returned invalid JSON. Check the backend deployment and retry.' },
        { status: 502, headers: { 'Cache-Control': 'no-store' } });
    }
    done(upstream.status, upstream.ok ? 'job started' : 'start failed');
    return NextResponse.json(payload, { status: upstream.status, headers: { 'Cache-Control': 'no-store' } });
  } catch (err) {
    log.error('Deep Research start proxy failed: %s', err);
    done(502, 'backend unavailable');
    return NextResponse.json({ error: 'Backend unavailable while starting Deep Research.' }, { status: 502 });
  }
}
