import { getBackendBaseUrl } from '@/lib/backend-url';
import { NextRequest, NextResponse } from 'next/server';
import { createLogger, logRequest } from '@/lib/logger';
export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
export const maxDuration = 45;
const log = createLogger('api/chat/charts');
export async function POST(req: NextRequest) {
  const done = logRequest(log, 'POST', '/api/chat/charts');
  let target = '';
  try {
    target = `${getBackendBaseUrl()}/api/chat/charts`;
    const upstream = await fetch(target, { method: 'POST', headers: { 'Content-Type': req.headers.get('content-type') || 'application/json' }, body: await req.arrayBuffer(), signal: AbortSignal.timeout(40_000), cache: 'no-store' });
    const payload = await upstream.arrayBuffer();
    const contentType = upstream.headers.get('content-type') || 'application/json';
    done(upstream.status, upstream.ok ? 'upstream ok' : 'upstream error');
    return new Response(payload, { status: upstream.status, headers: { 'Content-Type': contentType, 'Cache-Control': 'no-store' } });
  } catch (err) {
    const timedOut = err instanceof Error && (err.name === 'TimeoutError' || err.name === 'AbortError');
    log.error('Charts backend request failed at %s (%s)', target || 'invalid BACKEND_URL', err instanceof Error ? err.name : 'unknown');
    done(timedOut ? 504 : 502, timedOut ? 'backend timeout' : 'backend unreachable');
    return NextResponse.json({ error: timedOut ? 'Chart extraction timed out.' : 'Chart backend unavailable.' }, { status: timedOut ? 504 : 502 });
  }
}
