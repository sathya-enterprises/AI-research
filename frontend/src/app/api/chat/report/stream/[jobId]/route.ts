import { getBackendBaseUrl } from '@/lib/backend-url';
/**
 * Report progress stream (Server-Sent Events). Pipes the backend's
 * data-report-status / data-report-preview / data-report-result events to the
 * browser without buffering. Only a real text/event-stream is passed through;
 * anything else becomes a short JSON error so the client falls back to status
 * polling instead of showing an HTML page.
 *
 * Reconstructed 2026-10-21: this dynamic route folder was missing from the
 * uploaded archive. It follows the contract checked by
 * scripts/test-report-stream-html-fallback.cjs. Keep your own copy if it differs.
 */
import { NextRequest, NextResponse } from 'next/server';
import { createLogger, logRequest } from '@/lib/logger';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
// Long jobs keep this connection open; the client switches to status polling
// if a platform limit closes it earlier.
export const maxDuration = 800;

const log = createLogger('api/chat/report/stream');
const BACKEND = getBackendBaseUrl();

export async function GET(req: NextRequest, { params }: { params: Promise<{ jobId: string }> }) {
  const { jobId } = await params;
  const done = logRequest(log, 'GET', `/api/chat/report/stream/${jobId}`);
  try {
    const upstream = await fetch(`${BACKEND}/api/chat/report/stream/${encodeURIComponent(jobId)}`, {
      cache: 'no-store',
      headers: { Accept: 'text/event-stream' },
      signal: req.signal,
    });
    const upstreamContentType = String(upstream.headers.get('content-type') || '').toLowerCase();
    if (upstream.ok && upstream.body && upstreamContentType.includes('text/event-stream')) {
      done(200, 'streaming');
      return new Response(upstream.body, {
        status: 200,
        headers: {
          'Content-Type': 'text/event-stream; charset=utf-8',
          'Cache-Control': 'no-store, no-transform',
          Connection: 'keep-alive',
          'X-Accel-Buffering': 'no',
          'x-vercel-ai-ui-message-stream': upstream.headers.get('x-vercel-ai-ui-message-stream') || 'v1',
        },
      });
    }
    // Not a progress stream: never proxy an HTML page or raw body verbatim.
    const contentType = upstreamContentType;
    const text = await upstream.text().catch(() => '');
    if (contentType.includes('application/json')) {
      let payload: Record<string, unknown> = {};
      try { payload = JSON.parse(text) as Record<string, unknown>; } catch { /* malformed upstream JSON */ }
      const message = typeof payload.error === 'string' ? payload.error
        : typeof payload.detail === 'string' ? payload.detail
        : `The report stream endpoint returned HTTP ${upstream.status}.`;
      done(upstream.ok ? 502 : upstream.status, 'upstream JSON instead of SSE');
      return NextResponse.json({ error: message, expectedContentType: 'text/event-stream', upstreamStatus: upstream.status },
        { status: upstream.ok ? 502 : upstream.status, headers: { 'Cache-Control': 'no-store' } });
    }
    const looksLikeHtml = /<!doctype\s+html|<html[\s>]/i.test(text.slice(0, 1000)) || contentType.includes('text/html');
    log.error('Report stream proxy received non-SSE upstream response (status=%d, type=%s, html=%s)',
      upstream.status, contentType || '(missing)', looksLikeHtml);
    done(502, looksLikeHtml ? 'upstream returned HTML' : 'upstream returned non-SSE');
    return NextResponse.json({
      error: looksLikeHtml
        ? 'The configured report stream endpoint returned a web page instead of a progress stream. Check BACKEND_URL and confirm the latest FastAPI backend is running.'
        : `The report stream endpoint returned an unexpected response type (${contentType || 'missing'}).`,
      expectedContentType: 'text/event-stream',
      upstreamStatus: upstream.status,
    }, { status: 502, headers: { 'Cache-Control': 'no-store' } });
  } catch (err) {
    log.error('Report stream proxy failed: %s', err);
    done(502, 'backend unavailable');
    return NextResponse.json({ error: 'Backend unavailable while opening the report progress stream.', expectedContentType: 'text/event-stream' },
      { status: 502, headers: { 'Cache-Control': 'no-store' } });
  }
}
