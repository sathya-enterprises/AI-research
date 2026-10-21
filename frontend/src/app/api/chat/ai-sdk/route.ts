import { NextRequest } from 'next/server';
import { getBackendBaseUrl } from '@/lib/backend-url';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
export const maxDuration = 900;

/**
 * Direct FastAPI -> Vercel AI SDK UI-message stream adapter.
 * The old adapter first awaited another Next.js /api/chat proxy before returning
 * any Response. That proxy could time out after minutes, and it doubled the hop.
 * This route now returns SSE immediately, emits a status part, and forwards the
 * backend's OpenAI-compatible SSE as SDK UI Message Stream chunks.
 */
export async function POST(request: NextRequest) {
  let incoming: Record<string, unknown>;
  try {
    incoming = await request.json() as Record<string, unknown>;
  } catch {
    return Response.json({ error: 'Invalid chat request body.' }, { status: 400 });
  }

  const sdkMessages = Array.isArray(incoming.messages) ? incoming.messages : [];
  const messages = sdkMessages
    .filter((message: any) => message && (message.role === 'user' || message.role === 'assistant'))
    .map((message: any) => ({
      role: message.role,
      content: Array.isArray(message.parts)
        ? message.parts.filter((part: any) => part?.type === 'text').map((part: any) => String(part.text ?? '')).join('')
        : String(message.content ?? ''),
    }));

  const legacyBody = {
    messages,
    fileContext: typeof incoming.fileContext === 'string' ? incoming.fileContext : '',
    sessionId: typeof incoming.sessionId === 'string' ? incoming.sessionId : '',
    hasRag: incoming.hasRag === true,
    fileImages: Array.isArray(incoming.fileImages) ? incoming.fileImages : [],
  };

  const messageId = crypto.randomUUID();
  const textId = `text-${messageId}`;
  const encoder = new TextEncoder();
  const decoder = new TextDecoder();
  const backend = getBackendBaseUrl();

  const body = new ReadableStream<Uint8Array>({
    async start(controller) {
      let ended = false;
      let buffer = '';
      let streamFailed = false;
      let upstream: Response | null = null;
      const emit = (part: Record<string, unknown>) => {
        if (ended) return;
        controller.enqueue(encoder.encode(`data: ${JSON.stringify(part)}\n\n`));
      };
      const end = (reason = 'stop') => {
        if (ended) return;
        // Emit terminal UI-message parts before marking the stream ended;
        // emit() intentionally ignores any parts after `ended` is true.
        emit({ type: 'text-end', id: textId });
        emit({ type: 'finish', finishReason: reason });
        controller.enqueue(encoder.encode('data: [DONE]\n\n'));
        ended = true;
        controller.close();
      };
      const fail = (message: string) => {
        streamFailed = true;
        emit({ type: 'error', errorText: message });
        end('error');
      };
      const handleLine = (line: string) => {
        const clean = line.replace(/\r$/, '');
        if (!clean.startsWith('data:')) return;
        const raw = clean.slice(5).trim();
        if (!raw || raw === '[DONE]') return;
        let payload: any;
        try { payload = JSON.parse(raw); } catch { return; }
        if (payload?.type === 'meta') {
          emit({ type: 'data-chat-meta', data: payload });
          return;
        }
        if (payload?.type === 'status') {
          emit({
            type: 'data-chat-status',
            data: {
              message: String(payload.message || 'Working…'),
              phase: typeof payload.phase === 'string' ? payload.phase : undefined,
              status: typeof payload.status === 'string' ? payload.status : undefined,
              tool: typeof payload.tool === 'string' ? payload.tool : undefined,
              sourceCount: Number.isFinite(payload.sourceCount) ? payload.sourceCount : undefined,
              elapsedMs: Number.isFinite(payload.elapsedMs) ? payload.elapsedMs : undefined,
            },
          });
          return;
        }
        if (payload?.error) {
          const text = typeof payload.error === 'string' ? payload.error : 'The chat provider returned an error.';
          fail(text);
          return;
        }
        const delta = payload?.choices?.[0]?.delta?.content;
        if (typeof delta === 'string' && delta.length) {
          emit({ type: 'text-delta', id: textId, delta });
        } else if (Array.isArray(delta)) {
          for (const part of delta) {
            if (part?.type === 'text' && typeof part.text === 'string' && part.text.length) {
              emit({ type: 'text-delta', id: textId, delta: part.text });
            }
          }
        }
      };

      emit({ type: 'start', messageId });
      emit({ type: 'text-start', id: textId });
      emit({ type: 'data-chat-status', data: { message: messages.length ? 'Connecting to the research engine…' : 'Preparing your response…' } });

      try {
        const headers = new Headers({ 'content-type': 'application/json', 'accept': 'text/event-stream' });
        const authorization = request.headers.get('authorization');
        if (authorization) headers.set('authorization', authorization);
        upstream = await fetch(`${backend}/api/chat`, {
          method: 'POST', headers, body: JSON.stringify(legacyBody), cache: 'no-store',
          signal: request.signal,
        });
        if (!upstream.ok) {
          const detail = (await upstream.text().catch(() => '')).slice(0, 1000);
          fail(`Chat backend returned HTTP ${upstream.status}${detail ? `: ${detail}` : '.'}`);
          return;
        }
        if (!upstream.body) {
          fail('The chat backend returned no streaming body.');
          return;
        }

        const reader = upstream.body.getReader();
        while (!ended) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop() ?? '';
          for (const line of lines) handleLine(line);
        }
        buffer += decoder.decode();
        if (!ended && buffer.trim()) handleLine(buffer);
        if (!ended) end(streamFailed ? 'error' : 'stop');
      } catch (error) {
        if (!ended) fail(error instanceof Error ? error.message : 'The chat stream was interrupted.');
      } finally {
        upstream?.body?.cancel().catch(() => {});
      }
    },
    cancel() {
      // The request signal is propagated into the upstream fetch; cancellation
      // of this stream causes that signal to abort the active FastAPI request.
    },
  });

  return new Response(body, {
    status: 200,
    headers: {
      'content-type': 'text/event-stream; charset=utf-8',
      'cache-control': 'no-cache, no-transform',
      connection: 'keep-alive',
      'x-vercel-ai-ui-message-stream': 'v1',
      'x-accel-buffering': 'no',
    },
  });
}
