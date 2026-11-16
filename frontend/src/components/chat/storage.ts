// Moved verbatim from GrowthGradualChat.tsx (split for maintainability; no behaviour change).
import { DefaultChatTransport } from 'ai';
import type { ReportRevision, Conversation } from './types';
export const _EDIT_INTENT_RE = /\b(edit|update|modify|change|revise|rewrite|redo|rephrase|elaborate|expand|shorten|condense|add\s+more|add\s+detail|go\s+deeper|more\s+detail|more\s+depth|fix|correct|improve|rework|tweak)\b/i;


export function uid() { return Math.random().toString(36).slice(2, 10); }
export function fmtTime(ts: number) {
  return new Date(ts).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' });
}
export function fmtDate(ts: number) {
  const d = new Date(ts);
  const today = new Date();
  const diff = today.getDate() - d.getDate();
  if (diff === 0) return 'Today';
  if (diff === 1) return 'Yesterday';
  return d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short' });
}

// ─── Storage helpers ──────────────────────────────────────────────────────────
export const STORAGE_KEY = 'growth_gradual_conversations';
export const SESSION_ID_KEY = 'growth_gradual_session_id';

// Official Vercel AI SDK transport for normal chat. The adapter route preserves
// Growth Gradual's current search/RAG/attachment backend while exposing the UI
// Message Stream protocol expected by useChat.
export const growthGradualAiTransport = new DefaultChatTransport({ api: '/api/chat/ai-sdk' });

/**
 * Module-level fallback for environments where localStorage is unavailable
 * (incognito/private mode on Safari/Firefox, SSR, blocked storage).
 *
 * CRITICAL: must be module-level so every call within the same page load
 * returns the SAME UUID even when localStorage is blocked.
 *
 * Without this, incognito mode returns a fresh UUID on every call:
 *   - File upload indexes under UUID "A"
 *   - Chat message sends sessionId "B" → RAG finds nothing, file ignored
 */
export let _inMemorySessionId: string | null = null;

/** Return a stable UUID for this browser tab/page-load.
 *  Priority: localStorage (persists across reloads) → in-memory (stable for
 *  this page load, works in incognito/private/Safari/Firefox strict mode). */
export function getOrCreateSessionId(): string {
  // 1. Try localStorage — persists across browser reloads (normal mode)
  try {
    const existing = localStorage.getItem(SESSION_ID_KEY);
    if (existing) {
      _inMemorySessionId = existing; // keep in-memory copy in sync
      return existing;
    }
    const id = crypto.randomUUID();
    localStorage.setItem(SESSION_ID_KEY, id);
    _inMemorySessionId = id;
    return id;
  } catch {
    // localStorage blocked (incognito, Safari ITP, Firefox strict, SSR)
  }

  // 2. In-memory fallback — stable for this page load even in incognito
  if (!_inMemorySessionId) {
    _inMemorySessionId = crypto.randomUUID();
  }
  return _inMemorySessionId;
}
export function loadConversations(): Conversation[] {
  if (typeof window === 'undefined') return [];
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '[]'); } catch { return []; }
}
export function saveConversations(convs: Conversation[]) {
  if (typeof window === 'undefined') return false;
  const cleaned = convs.slice(0, 50).map(c => ({
    ...c,
    messages: c.messages.map(m => {
      const rest = { ...m };
      delete rest.reportLoading;
      delete rest.reportFiles;
      delete rest.reportProgress;
      delete rest.reportProgressPercent;
      delete rest.reportPhase;
      delete rest.reportStartedAt;
      delete rest.reportPreview;
      delete rest.reportPreviewTruncated;
      const rd = rest.reportData;
      if (rd) {
        // Keep report text and presentation metadata, but never persist large
        // binary payloads that can overflow localStorage.
        const safeRd = {
          ...rd,
          fileImages: undefined,
          sourceDocuments: undefined,
          images: (rd.images || []).slice(0, 6),
          revisionHistory: (rd.revisionHistory || []).slice(-8).map((revision: ReportRevision) => ({
            ...revision,
            images: (revision.images || []).slice(0, 3).filter(image => !String(image.url || '').startsWith('data:')),
          })),
        };
        return { ...rest, reportLoading: false, reportData: safeRd };
      }
      return { ...rest, reportLoading: false };
    }),
  }));
  const attempts = [
    cleaned,
    cleaned.map(c => ({ ...c, messages: c.messages.slice(-80) })),
    cleaned.map(c => ({ ...c, messages: c.messages.slice(-40).map(m => ({ ...m, sources: undefined, inlineCharts: undefined })) })),
  ];
  for (const payload of attempts) {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
      return true;
    } catch {
      // Keep the last known-good conversations if the enlarged snapshot hits
      // quota. A compact retry can still replace it; if that also fails, the
      // previous saved report and revision history remain available on reload.
      console.warn('[history] localStorage save failed; retrying with compact history');
    }
  }
  return false;
}

// ─── Markdown renderer ────────────────────────────────────────────────────────
/**
 * Repair formatting-only Markdown serialization errors before display. Report
 * generations can contain literal `\\n` sequences or flattened pipe-table rows;
 * normalize them here as a backwards-compatible fallback for reports already
 * stored in browser history, as well as new reports returned by the backend.
 */
