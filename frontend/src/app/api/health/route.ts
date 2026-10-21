import { NextResponse } from 'next/server';
import { getBackendBaseUrl, getBackendDiagnosticTarget } from '@/lib/backend-url';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/**
 * Local diagnostic endpoint. The sourcePackage marker identifies which of the
 * two packaged Next.js copies is currently serving requests. This endpoint
 * deliberately returns HTTP 200 even if the backend is down; backend status
 * is reported separately so route discovery can be tested independently.
 */
export async function GET() {
  const base = getBackendBaseUrl();
  const backendTarget = getBackendDiagnosticTarget();
  let backendStatus: number | null = null;
  let backendReachable = false;
  let backendError: string | null = null;

  try {
    const response = await fetch(`${base}/health`, {
      cache: 'no-store',
      signal: AbortSignal.timeout(2500),
    });
    backendStatus = response.status;
    backendReachable = response.ok;
  } catch (error) {
    backendError = error instanceof Error ? error.message.slice(0, 160) : 'Backend health check failed';
  }

  return NextResponse.json({
    ok: true,
    build: 'v39-report-stream-api-guard-20261019',
    sourcePackage: 'frontend',
    backendTarget,
    backendReachable,
    backendStatus,
    backendError,
    expectedFrontendRoutes: [
      'GET /api/market?healthcheck=1',
      'OPTIONS /api/scrape',
      'POST /api/chat/report',
      'POST /api/chat/report/start',
      'GET /api/chat/report/stream/[jobId]',
      'GET /api/chat/report/status/[jobId]',
      'POST /api/chat/report/docx',
    ],
  });
}
