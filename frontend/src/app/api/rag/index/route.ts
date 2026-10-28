import { NextRequest, NextResponse } from 'next/server';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
// Local SentenceTransformer startup and hosted RAG cold starts can be slow.
export const maxDuration = 300;

// Index directly into the same RAG service that the backend uses for retrieval.
// This removes a redundant frontend -> backend -> RAG hop and avoids a misleading
// generic 502 when BACKEND_URL points at a different local backend port.
const RAG_URL = (process.env.RAG_SERVICE_URL ?? 'http://127.0.0.1:7860').trim().replace(/\/$/, '');

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const response = await fetch(`${RAG_URL}/index`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(270_000),
      cache: 'no-store',
    });

    const payload = await response.json().catch(() => ({
      error: `RAG service returned a non-JSON response (HTTP ${response.status}).`,
      chunks_added: 0,
    }));
    return NextResponse.json(payload, { status: response.status });
  } catch (err) {
    const detail = err instanceof Error ? `${err.name}: ${err.message}` : 'Unknown network error';
    // Server-side diagnostics retain the real exception; the browser receives
    // an actionable message without environment values or credentials.
    console.error('[api/rag/index] RAG service request failed:', detail);
    return NextResponse.json({
      error: 'Could not reach the RAG service. Check that backend/rag-service is running and that RAG_SERVICE_URL points to it.',
      code: 'RAG_SERVICE_UNREACHABLE',
      chunks_added: 0,
    }, { status: 502 });
  }
}
