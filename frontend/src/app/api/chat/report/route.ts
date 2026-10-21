import { getBackendBaseUrl, getBackendDiagnosticTarget } from '@/lib/backend-url';
/**
 * Thin proxy → Python FastAPI backend /api/chat/report
 */
import { NextRequest, NextResponse } from 'next/server';
import { createLogger, logRequest } from '@/lib/logger';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
export const maxDuration = 900; // 15 min hard ceiling for genuinely long Deep Research runs

const log = createLogger('api/chat/report');
const BACKEND = getBackendBaseUrl();

export async function POST(req: NextRequest) {
  const done = logRequest(log, 'POST', '/api/chat/report');
  const body = await req.arrayBuffer();

  let upstream: Response;
  try {
    upstream = await fetch(`${BACKEND}/api/chat/report`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body,
      // Bound this explicitly to just under maxDuration so we always return
      // a clean JSON error in-band. Without this, a slow upstream (Gemini
      // retry cascade running past the platform's own gateway/function
      // timeout) gets its connection killed uncleanly — the fetch never
      // resolves through our try/catch, and the caller can end up treating
      // whatever partial/empty response comes back as a "successful" report.
      signal: AbortSignal.timeout(880_000),
    });
  } catch (err) {
    const timedOut = err instanceof Error && (err.name === 'TimeoutError' || err.name === 'AbortError');
    log.error('Backend unreachable or timed out: %s', err);
    done(timedOut ? 504 : 502, timedOut ? 'backend timeout' : 'backend unreachable');
    return NextResponse.json(
      { error: timedOut
        ? 'Report generation exceeded the 15-minute safety window. The backend did not return a complete report in time; please retry.'
        : 'Backend unavailable' },
      { status: timedOut ? 504 : 502 },
    );
  }

  // A 404 from the upstream is usually a wrong BACKEND_URL/base path or a
  // stale/different backend process. Report an actionable gateway error instead
  // of the frontend's generic "Report generation failed (HTTP 404)" message.
  if (upstream.status === 404) {
    const upstreamText = await upstream.text().catch(() => '');
    const target = getBackendDiagnosticTarget();
    log.error('Backend route not found at %s/api/chat/report; upstream body=%s', target, upstreamText.slice(0, 180));
    done(502, 'backend route not found (upstream 404)');
    return NextResponse.json({
      error: `The configured backend at ${target} returned 404 for /api/chat/report. Confirm the running backend is this project's backend/main.py on the expected port, then check BACKEND_URL.`,
      diagnostics: { upstreamStatus: 404, backendTarget: target, expectedPath: '/api/chat/report' },
    }, { status: 502 });
  }

  // Guard: upstream may return non-JSON (e.g. "Too Many Requests" plain text on 429)
  const contentType = upstream.headers.get('Content-Type') ?? '';
  if (!contentType.includes('application/json')) {
    const text = await upstream.text();
    log.warn('Non-JSON response from backend (%d): %s', upstream.status, text.slice(0, 120));
    done(upstream.status, 'non-json upstream');
    return NextResponse.json(
      { error: upstream.status === 503
        ? 'The report service is temporarily overloaded. Please retry in a moment.'
        : upstream.status === 504
          ? 'Report generation timed out. Please retry in a moment.'
          : `Backend returned ${upstream.status}: ${text.slice(0, 200)}` },
      { status: upstream.status },
    );
  }

  let data: unknown;
  try {
    data = await upstream.json();
  } catch (err) {
    const raw = await upstream.text().catch(() => '');
    log.error('Failed to parse backend JSON (%d): %s — %s', upstream.status, err, raw.slice(0, 120));
    done(upstream.status, 'json parse error');
    return NextResponse.json({ error: 'Invalid response from backend' }, { status: 502 });
  }

  const title = (typeof data === 'object' && data && 'title' in data)
    ? String((data as Record<string, unknown>).title ?? '').slice(0, 40)
    : '';
  done(upstream.status, `title="${title}"`);
  return NextResponse.json(data, { status: upstream.status });
}
