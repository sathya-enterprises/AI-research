import { getBackendBaseUrl } from '@/lib/backend-url';
import { NextRequest, NextResponse } from 'next/server';
import { createLogger, logRequest } from '@/lib/logger';
import { hydrateReportImagesForArtifact } from '@/lib/hydrate-report-images';

/** Editable DOCX export proxy → Python FastAPI backend. */
export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
export const maxDuration = 120;

const log = createLogger('api/chat/report/docx');
const BACKEND = getBackendBaseUrl();

export async function POST(req: NextRequest) {
  const done = logRequest(log, 'POST', '/api/chat/report/docx');
  let bodyObj: Record<string, unknown>;
  try {
    bodyObj = await req.json();
  } catch {
    done(400, 'invalid json');
    return NextResponse.json({ error: 'Invalid request body' }, { status: 400 });
  }

  await hydrateReportImagesForArtifact(bodyObj, req.nextUrl.origin);

  let upstream: Response;
  try {
    upstream = await fetch(`${BACKEND}/api/chat/report/docx`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(bodyObj),
      signal: AbortSignal.timeout(110_000),
    });
  } catch (err) {
    const timedOut = err instanceof Error && (err.name === 'TimeoutError' || err.name === 'AbortError');
    log.error('Backend unreachable or timed out: %s', err);
    done(timedOut ? 504 : 502, timedOut ? 'backend timeout' : 'backend unreachable');
    return NextResponse.json({ error: timedOut ? 'Word export timed out. Please retry.' : 'The report service is temporarily unavailable.' }, { status: timedOut ? 504 : 502 });
  }

  if (!upstream.ok) {
    const raw = await upstream.text();
    let payload: unknown;
    try {
      const parsed = JSON.parse(raw);
      payload = parsed && typeof parsed === 'object' ? parsed : { error: raw || `Upstream error (${upstream.status})` };
    } catch {
      payload = { error: raw || `Upstream error (${upstream.status})` };
    }
    log.error('DOCX export failed upstream: HTTP %d — %s', upstream.status, raw.slice(0, 160));
    done(upstream.status, 'upstream error');
    return NextResponse.json(payload, { status: upstream.status });
  }

  const bytes = await upstream.arrayBuffer();
  if (!bytes.byteLength) {
    done(502, 'empty docx');
    return NextResponse.json({ error: 'The Word export was empty. Please retry.' }, { status: 502 });
  }
  const dateStr = new Date().toISOString().slice(0, 10);
  done(200, `${(bytes.byteLength / 1024).toFixed(1)} KB`);
  return new NextResponse(bytes, {
    headers: {
      'Content-Type': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      'Content-Disposition': `attachment; filename="growth-gradual-report-${dateStr}.docx"`,
      'Cache-Control': 'no-store',
    },
  });
}
