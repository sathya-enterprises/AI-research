// Moved verbatim from GrowthGradualChat.tsx (split for maintainability; no behaviour change).
import { DefaultChatTransport } from 'ai';
import type { ReportRevision, Conversation } from './types';
export const _EDIT_INTENT_RE = /\b(edit|update|modify|change|revise|rewrite|redo|rephrase|elaborate|expand|shorten|condense|add\s+more|add\s+detail|go\s+deeper|more\s+detail|more\s+depth|fix|correct|improve|rework|tweak)\b/i;

// Verbs that ask for a change to existing content ("make the outlook section shorter", "remove the second chart").
const _EDIT_VERB_RE = /\b(edit|update|modify|change|revise|rewrite|redo|rephrase|elaborate|expand|extend|shorten|shorter|longer|condense|reduce|trim|cut|remove|delete|drop|replace|swap|add|include|insert|make|turn|convert|fix|correct|improve|rework|tweak|simplify|clarify|summari[sz]e|reword|restructure|reorder|merge|split|go\s+deeper|more\s+detail|more\s+depth)\b/i;
// What the change applies to: a part of the report (or "it"/"this" when a report was just produced).
const _EDIT_TARGET_RE = /\b(sections?|sub-?sections?|paragraphs?|charts?|graphs?|tables?|figures?|headings?|titles?|summary|conclusion|introduction|intro|outlook|bullet\s+points?|points|wording|tone|report|it|this|that)\b/i;
// Asking for a different report is not an edit of this one.
const _NEW_REPORT_RE = /\b(new|another|fresh|separate|second)\s+report\b|\breport\s+(on|about|for|covering)\b|\b(create|generate|produce|prepare|write|make)\s+(me\s+)?(a|an)\s+(\w+\s+)?report\b/i;

/** Whether a chat message asks to change the report already in this conversation (not to write a new one).
 *  A message that names one of the report's section headings, or pairs an edit verb with a part of the
 *  report, is an edit; "make a new report on X" / "report on X" is not. Used whether or not the Report
 *  toggle is on, so an edit request never starts a full new report run. */
export function isReportEditRequest(text: string, sectionTitles: string[] = []): boolean {
  const q = String(text || '').trim();
  if (!q || _NEW_REPORT_RE.test(q)) return false;
  const folded = q.toLowerCase();
  const namesSection = sectionTitles.some(title => {
    const t = String(title || '').toLowerCase().replace(/[^a-z0-9 ]+/g, ' ').replace(/\s+/g, ' ').trim();
    if (t.length < 6) return false;
    const plain = folded.replace(/[^a-z0-9 ]+/g, ' ').replace(/\s+/g, ' ');
    if (plain.includes(t)) return true;
    // "the outlook section" names "Market Outlook and Risk Scenarios": a distinctive heading word plus "section"
    const words = t.split(' ').filter(w => w.length >= 5 && !['market', 'analysis', 'overview', 'sector', 'india', 'indian'].includes(w));
    return /\bsection\b/.test(plain) && words.some(w => new RegExp(`\\b${w}\\b`).test(plain));
  });
  return _EDIT_VERB_RE.test(q) && (namesSection || _EDIT_TARGET_RE.test(q));
}

/** The "## " headings of a report body (section titles the user can name in an edit request). */
export function reportSectionTitles(report: string): string[] {
  return Array.from(String(report || '').matchAll(/^#{2,3}\s+(.+?)\s*$/gm)).map(m => m[1].replace(/[*_`]/g, '').trim());
}


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
