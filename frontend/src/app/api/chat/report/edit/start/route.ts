import { getBackendBaseUrl } from '@/lib/backend-url';
/**
 * Queue a long-running Action edit. The Python backend does the actual work
 * on its Action worker and the browser polls /edit/status/:job_id until the
 * full atomic edit is either committed or rejected safely.
 */
import { NextRequest, NextResponse } from 'next/server';
import { createLogger, logRequest } from '@/lib/logger';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
export const maxDuration = 30;

const log = createLogger('api/chat/report/edit/start');
const BACKEND = getBackendBaseUrl();

export async function POST(req: NextRequest) {
  const done = logRequest(log, 'POST', '/api/chat/report/edit/start');
  let body: ArrayBuffer;
  try {
    body = await req.arrayBuffer();
  } catch (err) {
    log.error('Unable to read Action request body: %s', err);
    done(400, 'invalid request body');
    return NextResponse.json({ error: 'Could not read the Action request. Please try again.' }, { status: 400 });
  }

  try {
    const upstream = await fetch(`${BACKEND}/api/chat/report/edit/start`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body,
      signal: AbortSignal.timeout(20_000),
    });
    const data = await upstream.json().catch(() => ({ error: `Action service returned HTTP ${upstream.status}.` }));
    done(upstream.status, `status=${String((data as Record<string, unknown>)?.status ?? 'error')}`);
    return NextResponse.json(data, { status: upstream.status });
  } catch (err) {
    const timedOut = err instanceof Error && (err.name === 'TimeoutError' || err.name === 'AbortError');
    log.error('Could not queue Action: %s', err);
    done(timedOut ? 504 : 502, timedOut ? 'queue timeout' : 'backend unreachable');
    return NextResponse.json({
      error: timedOut
        ? 'The Action could not be queued in time. Your report is unchanged; please retry.'
        : 'The Action service is temporarily unavailable. Your report is unchanged; please retry shortly.',
      preserved: true,
      transient: true,
    }, { status: timedOut ? 504 : 502 });
  }
}
