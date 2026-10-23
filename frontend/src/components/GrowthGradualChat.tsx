'use client';

import { useState, useRef, useEffect, useCallback } from 'react';
import { useChat } from '@ai-sdk/react';
import { DefaultChatTransport, type UIMessage } from 'ai';
import Image from 'next/image';
import { supabase } from '@/lib/supabaseClient';
import { loadPdfJs } from '@/lib/pdfjs-browser';

// ─── Types ────────────────────────────────────────────────────────────────────
interface Source { title: string; url: string; snippet: string; }
interface ReportSource { id?: string; title: string; url?: string; publisher?: string; kind?: string; page_fetch_status?: string; evidence_origin?: string; claim_evidence_matched?: boolean; }
interface ChartDataPoint { label: string; value: number; [key: string]: unknown; }
interface ChartSeries { name: string; data: ChartDataPoint[]; color?: string; }
/** Route a Tavily/third-party image URL through our server-side proxy to bypass hotlink protection. */
function proxyImg(url: string): string {
  if (!url) return url;
  if (url.startsWith('/') || url.startsWith('data:')) return url;
  return `/api/image-proxy?url=${encodeURIComponent(url)}`;
}

interface DatawrapperInfo { id: string; embedUrl: string; publicUrl: string; }
interface ChartSpec { type: string; title: string; series?: ChartSeries[]; unit?: string; columns?: string[]; rows?: string[][]; datawrapper?: DatawrapperInfo; [key: string]: unknown; }
interface WebImage { url: string; caption?: string; sourceUrl?: string; sourceURL?: string; sourceTitle?: string; publisher?: string; sourceId?: string; sourceIds?: string[]; assetUrl?: string; }
// Mirrors backend/routes/report.py's _sanitize_theme output shape — only
// ever set when the user's request explicitly asked for a visual style
// (see report.py SYSTEM_PROMPT's THEME section). fontFamily/customCss are not
// fully represented in the flat PDF export, which is the only report export exposed.
type ReportTier = 'brief' | 'detailed' | 'in_depth';

const REPORT_TIER_OPTIONS: { value: ReportTier; label: string; scope: string; description: string }[] = [
  { value: 'brief', label: 'Brief', scope: '6–10 pages', description: 'Core findings with the key charts — a fast, focused read.' },
  { value: 'detailed', label: 'In-Detail', scope: '25–30 pages', description: 'Brief coverage plus deeper comparisons, history and risks, with more charts.' },
  { value: 'in_depth', label: 'Deep Research', scope: '70–100 pages', description: 'Widest source base and granular, chart-heavy analysis; length follows the evidence.' },
];

type ReportStyle = 'market_currents' | 'feature_article';

const REPORT_STYLE_OPTIONS: { value: ReportStyle; label: string; summary: string; description: string }[] = [
  { value: 'market_currents', label: 'Market Currents', summary: 'Branded house report', description: 'Growth Gradual house style: branded navy cover, numbered sections, KPI cards, Desk View notes and ARN footer. Monthly market reviews use the fixed 10-section format.' },
  { value: 'feature_article', label: 'Feature Article', summary: 'Long-read editorial', description: 'Long-read editorial: kicker, headline, standfirst, narrative sections and infographic charts.' },
];

interface ReportTheme { primaryColor?: string; accentColor?: string; toneNote?: string; fontFamily?: string; customCss?: string; richStyleNeeded?: boolean; }
interface ReportPublicationAudit { status?: 'clean' | 'review_required'; flags?: string[]; controlMarkerCount?: number; danglingChartRefs?: number[]; danglingImageRefs?: number[]; unresolvedCitationIds?: string[]; duplicateTableCount?: number; suspiciousEntityTableValues?: { column?: string; repeatedValue?: string; rowsWithValue?: number; rowsWithAvailableValue?: number; reason?: string }[]; chartTableVerificationConflicts?: { chartTitle?: string; entity?: string; period?: string; reason?: string }[]; chartEvidenceFailures?: { chartTitle?: string; label?: string; value?: string; sourceIds?: string[]; reason?: string }[]; imagesMissingPublisherPage?: number[]; note?: string; }
interface GeminiAuditFinding { severity?: string; category?: string; location?: string; issue?: string; evidence?: string; recommended_fix?: string; }
interface GeminiReportAudit { status?: 'PASS' | 'WARNING' | 'FAIL' | 'unavailable' | 'not_run'; summary?: string; blocking?: boolean; reason?: string; criticalIssues?: string[]; findings?: GeminiAuditFinding[]; checks?: Record<string, string>; provider?: string; model?: string; }
interface ReportQualityReview { publicationAudit?: ReportPublicationAudit; chartDensity?: { profile?: { minimum?: number | null; maximum?: number | null; label?: string }; withinTarget?: number | null; measuredCharts?: number; applicableCharts?: number; exceptionsOrMisses?: { title?: string; status?: string; count?: number; minimum?: number | null; maximum?: number | null }[] }; }
interface ReportPageBudget { minimum?: number | null; target?: number | null; maximum?: number | null; renderedPages?: number | null; measured?: boolean; minimumReached?: boolean | null; warning?: string | null; }
interface ReportRagStatus { requested?: boolean; status?: string; retrievedChunks?: number; sourceCount?: number; sourceIds?: string[]; restoreStatus?: string; rerankerRan?: boolean; message?: string; }
interface ReportRevision { revision: number; savedAt: number; note: string; report: string; title?: string; charts: ChartSpec[]; images?: WebImage[]; sources?: ReportSource[]; presentation?: Record<string, unknown>; keyStats?: {label:string;value:string;change?:string}[]; summary?: string; theme?: ReportTheme; qualityReview?: ReportQualityReview; publicationStatus?: string; sessionId?: string; ragStatus?: ReportRagStatus; }
interface ReportData { reportId?: string; revision?: number; revisionHistory?: ReportRevision[]; report: string; reportTier?: ReportTier; reportStyle?: ReportStyle; kicker?: string; pageTarget?: {min:number | null; target:number | null; max:number}; pageBudget?: ReportPageBudget; title?: string; charts: ChartSpec[]; images?: WebImage[]; presentation?: Record<string, unknown>; keyStats: {label:string;value:string;change?:string}[]; summary: string; fileImages?: {name:string;mimeType:string;data:string}[]; sourceDocuments?: {name:string;text:string;file_type?:string}[]; sources?: ReportSource[]; recommendedFormat?: 'pdf' | 'html'; theme?: ReportTheme; qualityReview?: ReportQualityReview; geminiAudit?: GeminiReportAudit; publicationStatus?: 'PASS' | 'WARNING' | 'FAIL' | string; auditSummary?: {status?: string; newBlockingFindings?: number; remainingBlockingFindings?: number; publisherPagesVerified?: number}; sessionId?: string; ragStatus?: ReportRagStatus; publicationOutcome?: 'published' | 'draft' | 'structural_failure' | string; failedStage?: string | null; blockingCategories?: string[]; failureSummary?: ReportFailureItem[]; chartPipeline?: { stages?: { stage: string; count: number; removed?: { title?: string; reason?: string }[] }[]; finalCount?: number }; }
interface ReportFailureItem { stage?: string; issue?: string; claim?: string; sources?: string[]; values?: string[]; }

// Human-readable names for the backend's failedStage values.
const REPORT_STAGE_LABELS: Record<string, string> = {
  source_evidence: 'source-evidence check',
  chart_evidence: 'chart-evidence check',
  report_structure: 'report structure check',
  consistency: 'consistency check',
  publication_audit: 'publication audit',
  publication: 'publication audit',
  pdf_export: 'PDF export',
  report_generation: 'report generation',
  report_start: 'report start',
  report_stream: 'progress stream',
  report_status: 'status check',
};
const reportStageLabel = (stage?: string | null) => (stage ? (REPORT_STAGE_LABELS[stage] ?? stage.replace(/_/g, ' ')) : '');
const isDraftReport = (rd?: { publicationOutcome?: string; publicationStatus?: string } | null) =>
  !!rd && (rd.publicationOutcome === 'draft' || (rd.publicationStatus === 'FAIL' && rd.publicationOutcome !== 'published'));
/** Split a display value such as "₹2,41,47,183 crore" or "2.2%" into prefix / figure / unit.
 *  Presentation only — the figure text is kept exactly as supplied (no rounding). */
function splitMetricValue(value: string): { prefix: string; number: string; unit: string } | null {
  const m = /^(\s*[^\d\-+−.,]*?)([-+−]?\d[\d,]*(?:\.\d+)?\s*%?)(.*)$/.exec(String(value ?? ''));
  if (!m) return null;
  return { prefix: m[1].trim(), number: m[2].replace(/\s+%/, '%').trim(), unit: m[3].trim() };
}
/** Stable DOM id for a report section heading, used by the in-report navigation. */
function reportSectionDomId(msgId: string, heading: string): string {
  return `rpt-${msgId}-${String(heading).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 60)}`;
}
/** Level-1/2 headings of the generated report, in order (drives "In this report"). */
function reportOutline(markdown: string): string[] {
  const out: string[] = [];
  for (const m of String(markdown ?? '').matchAll(/^(#{1,2})\s+(.+)$/gm)) {
    const h = m[2].replace(/[*_`]/g, '').trim();
    if (h && !out.includes(h)) out.push(h);
  }
  return out;
}
interface KbCompany { id: number; ticker: string; name: string; downloadUrl: string; }
interface Message {
  id: string; role: 'user' | 'assistant'; text: string; ts: number;
  sources?: Source[]; searchPerformed?: boolean; queryType?: string;
  kbCompany?: KbCompany | null;
  reportData?: ReportData; reportLoading?: boolean; reportError?: string;
  reportProgress?: string; reportProgressPercent?: number; reportPreview?: string; reportPreviewTruncated?: boolean;
  inlineCharts?: ChartSpec[]; wantsVisual?: boolean;
  // Report request context is retained for retrying a failed opt-in report.
  reportEligible?: boolean; reportQuestion?: string; reportFiles?: AttachedFile[];
  followUpQuestions?: string[];
}
interface Conversation {
  id: string; title: string; messages: Message[]; ts: number;
}

// Matches a follow-up message that asks to edit/elaborate part of an
// already-generated report (e.g. "expand the risk factors section", "make
// the executive summary shorter"). When this fires AND a report already
// exists in the conversation, `send()` routes to editReportSection() instead
// of a fresh report/chat reply — see backend/routes/report.py:edit_report_section
// for how the single matching section is located and replaced.
const _EDIT_INTENT_RE = /\b(edit|update|modify|change|revise|rewrite|redo|rephrase|elaborate|expand|shorten|condense|add\s+more|add\s+detail|go\s+deeper|more\s+detail|more\s+depth|fix|correct|improve|rework|tweak)\b/i;


function uid() { return Math.random().toString(36).slice(2, 10); }
function fmtTime(ts: number) {
  return new Date(ts).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' });
}
function fmtDate(ts: number) {
  const d = new Date(ts);
  const today = new Date();
  const diff = today.getDate() - d.getDate();
  if (diff === 0) return 'Today';
  if (diff === 1) return 'Yesterday';
  return d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short' });
}

// ─── Storage helpers ──────────────────────────────────────────────────────────
const STORAGE_KEY = 'growth_gradual_conversations';
const SESSION_ID_KEY = 'growth_gradual_session_id';

// Official Vercel AI SDK transport for normal chat. The adapter route preserves
// Growth Gradual's current search/RAG/attachment backend while exposing the UI
// Message Stream protocol expected by useChat.
const growthGradualAiTransport = new DefaultChatTransport({ api: '/api/chat/ai-sdk' });

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
let _inMemorySessionId: string | null = null;

/** Return a stable UUID for this browser tab/page-load.
 *  Priority: localStorage (persists across reloads) → in-memory (stable for
 *  this page load, works in incognito/private/Safari/Firefox strict mode). */
function getOrCreateSessionId(): string {
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
function loadConversations(): Conversation[] {
  if (typeof window === 'undefined') return [];
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '[]'); } catch { return []; }
}
function saveConversations(convs: Conversation[]) {
  if (typeof window === 'undefined') return false;
  const cleaned = convs.slice(0, 50).map(c => ({
    ...c,
    messages: c.messages.map(m => {
      const { reportLoading, reportFiles, reportProgress, reportProgressPercent, reportPreview, reportPreviewTruncated, ...rest } = m;
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
    } catch (e) {
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
function normalizeReportMarkdownSource(value: string): string {
  let text = String(value ?? '')
    .replace(/\r\n/g, '\n')
    .replace(/\r/g, '\n')
    .replace(/(?<!\\)\\r\\n/g, '\n')
    .replace(/(?<!\\)\\n/g, '\n')
    .replace(/(?<!\\)\\r/g, '\n');

  // Some model responses serialize a mixed Markdown/HTML report (e.g. <div>,
  // <li>, <br>, or a full HTML table). Since renderMd deliberately escapes raw
  // HTML for safety, leaving those tags untouched makes the user see literal
  // markup. Normalize only common presentation tags into Markdown first, while
  // protecting code spans/fences so HTML examples remain literal code.
  const protectedCode: string[] = [];
  const protect = (source: string) => {
    const index = protectedCode.push(source) - 1;
    return `\u0001GGCODE${index}\u0001`;
  };
  text = text.replace(/```[\s\S]*?```|`[^`\n]+`/g, protect);
  if (/&lt;\s*\/?\s*(?:div|p|br|section|article|ul|ol|li|table|thead|tbody|tr|td|th|strong|b|em|i)\b/i.test(text)) {
    text = text.replace(/&lt;/gi, '<').replace(/&gt;/gi, '>').replace(/&quot;/gi, '"').replace(/&#39;/gi, "'");
  }
  const decodeCell = (cell: string) => cell
    .replace(/<br\s*\/?\s*>/gi, '; ')
    .replace(/<\/?(?:strong|b)\b[^>]*>/gi, '**')
    .replace(/<\/?(?:em|i)\b[^>]*>/gi, '*')
    .replace(/<[^>]+>/g, '')
    .replace(/&amp;/gi, '&').replace(/&nbsp;/gi, ' ')
    .replace(/&lt;/gi, '<').replace(/&gt;/gi, '>')
    .replace(/\s+/g, ' ').trim().replace(/\|/g, '\\|');
  text = text.replace(/<table\b[^>]*>([\s\S]*?)<\/table\s*>/gi, (_match, body: string) => {
    const rows = [...body.matchAll(/<tr\b[^>]*>([\s\S]*?)<\/tr\s*>/gi)].map(row =>
      [...String(row[1]).matchAll(/<t[dh]\b[^>]*>([\s\S]*?)<\/t[dh]\s*>/gi)].map(cell => decodeCell(String(cell[1]))),
    ).filter(row => row.length > 0);
    if (!rows.length) return '\n\n';
    const width = Math.max(...rows.map(row => row.length));
    const padded = rows.map(row => [...row, ...Array(Math.max(0, width - row.length)).fill('')]);
    return '\n\n' + padded.map((row, i) => '| ' + row.join(' | ') + ' |' + (i === 0 ? '\n| ' + Array(width).fill('---').join(' | ') + ' |' : '')).join('\n') + '\n\n';
  });
  text = text
    .replace(/<br\s*\/?\s*>/gi, '\n')
    .replace(/<h([1-6])\b[^>]*>([\s\S]*?)<\/h\1\s*>/gi, (_m, level: string, body: string) => '\n\n' + '#'.repeat(Number(level)) + ' ' + decodeCell(body) + '\n\n')
    .replace(/<li\b[^>]*>/gi, '\n- ')
    .replace(/<\/li\s*>/gi, '\n')
    .replace(/<\/?(?:ul|ol)\b[^>]*>/gi, '\n\n')
    .replace(/<\/?(?:strong|b)\b[^>]*>/gi, '**')
    .replace(/<\/?(?:em|i)\b[^>]*>/gi, '*')
    .replace(/<\/?(?:p|div|section|article|header|footer|aside|blockquote)\b[^>]*>/gi, '\n\n')
    .replace(/<\/?(?:span|figure|figcaption)\b[^>]*>/gi, '')
    .replace(/<[^>]+>/g, '')
    .replace(/&amp;/gi, '&').replace(/&nbsp;/gi, ' ')
    .replace(/&lt;/gi, '<').replace(/&gt;/gi, '>')
    .replace(/&#39;/gi, "'").replace(/&quot;/gi, '"');
  text = text.replace(/\u0001GGCODE(\d+)\u0001/g, (_m, index: string) => protectedCode[Number(index)] ?? '');
  // Last-mile cleanup for reports saved before the backend publication guard
  // was deployed. Internal continuation wrappers must never appear as prose.
  text = text
    .replace(/^\s*\[\[(?:SECTION:\s*.*?|END SECTION|SECTION TO EXPAND|END_SCAN_SECTION)\]\]\s*$/gim, '')
    .replace(/\[\[(?:SECTION:\s*.*?|END SECTION|SECTION TO EXPAND|END_SCAN_SECTION)\]\]/gi, '')
    .replace(/!\[[^\]\n]*\]\(\s*<?\s*\[?CHART_(\d+)\]?\s*>?\s*\)/gi, '[CHART_$1]')
    .replace(/[【〔［]\s*S(\d+)\s*[】〕］]/gi, '[S$1]');
  const lines = text.split('\n');
  const out: string[] = [];
  const rowCells = (line: string): string[] | null => {
    const match = line.match(/^\s*\|(.*)\|\s*$/);
    return match ? match[1].split('|').map(cell => cell.trim()) : null;
  };
  const isSeparator = (line: string) => /^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(line);
  const sourceLike = (value: string) => {
    const cell = value.trim();
    return !cell || /^\[S\d+\]$/i.test(cell) || ['—', '–', '-', 'n/a', 'na', 'unknown', 'source unavailable'].includes(cell.toLowerCase());
  };
  const inferFlattenedWidth = (cells: string[], headerWidth: number): number => {
    const compact = cells.filter(cell => cell.trim().length > 0);
    if (compact.length === headerWidth + 1 && sourceLike(compact[compact.length - 1])) return headerWidth + 1;
    if (compact.length < headerWidth * 2) return headerWidth;
    const candidates: Array<{ score: number; width: number }> = [];
    for (let width = headerWidth + 1; width <= Math.min(8, headerWidth + 3); width++) {
      const groups: string[][] = [];
      for (let j = 0; j < compact.length; j += width) groups.push(compact.slice(j, j + width));
      const complete = groups.filter(group => group.length === width);
      if (complete.length < 3) continue;
      const score = complete.filter(group => sourceLike(group[group.length - 1])).length / complete.length;
      if (score >= 0.7) candidates.push({ score, width });
    }
    if (!candidates.length) return headerWidth;
    const best = Math.max(...candidates.map(candidate => candidate.score));
    return Math.min(...candidates.filter(candidate => candidate.score === best).map(candidate => candidate.width));
  };

  for (let i = 0; i < lines.length;) {
    let header = rowCells(lines[i]);
    if (header && header.length >= 2 && i + 1 < lines.length && isSeparator(lines[i + 1])) {
      const firstData = i + 2 < lines.length ? rowCells(lines[i + 2]) : null;
      const inferredWidth = firstData && firstData.length > header.length ? inferFlattenedWidth(firstData, header.length) : header.length;
      if (inferredWidth === header.length + 1 && !header.some(cell => /source|citation|reference/i.test(cell))) header = [...header, 'Source'];
      const width = header.length;
      out.push(`| ${header.join(' | ')} |`);
      const sepCells = lines[i + 1].trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(c => c.trim());
      out.push(`| ${(sepCells.length === width ? sepCells : Array(width).fill('---')).join(' | ')} |`);
      i += 2;
      while (i < lines.length) {
        const cells = rowCells(lines[i]);
        if (!cells) break;
        if (cells.length <= width) {
          const row = [...cells, ...Array(Math.max(0, width - cells.length)).fill('')];
          out.push(`| ${row.join(' | ')} |`);
        } else {
          // Flattened rows can be recognised only after a proper separator
          // has established the table's width. Do not apply this to prose pipes.
          const compact = cells.filter(cell => cell.length > 0);
          if (compact.length < width * 2) {
            const row = [...cells.slice(0, width), ...Array(Math.max(0, width - cells.length)).fill('')];
            out.push(`| ${row.join(' | ')} |`);
          } else {
            for (let j = 0; j < compact.length; j += width) {
              const row = compact.slice(j, j + width);
              while (row.length < width) row.push('');
              out.push(`| ${row.join(' | ')} |`);
            }
          }
        }
        i += 1;
      }
      continue;
    }
    let line = lines[i].replace(/\\\s*$/, '');
    line = line.replace(/^(\s*)(\d+)\\\./, '$1$2.').replace(/^(\s*)\\>/, '$1>');
    out.push(line);
    i += 1;
  }
  return out.join('\n').replace(/\n{3,}/g, '\n\n').trim();
}


/**
 * Restore structured chart placement when an editing model removes [CHART_n]
 * but leaves that chart's exact title as a standalone line. This mirrors the
 * backend HTML/PDF fallback so Show Report and exported files stay aligned.
 */
function recoverChartTitlePlaceholders(value: string, charts: Array<{ title?: string }> | undefined): string {
  let text = String(value ?? '');
  (charts ?? []).forEach((chart, index) => {
    const token = `[CHART_${index + 1}]`;
    if (new RegExp(`\\[CHART_${index + 1}\\]`, 'i').test(text)) return;
    const title = String(chart?.title ?? '').trim();
    if (!title || /^(?:chart|graph|figure)\s*\d*$/i.test(title)) return;
    const escaped = title.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const pattern = new RegExp(`^[\\t ]*(?:#{1,6}\\s*)?(?:\\*\\*|__)?\\s*${escaped}\\s*(?:\\*\\*|__)?\\s*:?\\s*$`, 'im');
    if (pattern.test(text)) text = text.replace(pattern, token);
  });
  return text;
}

function renderMd(text: string): string {
  text = normalizeReportMarkdownSource(text);
  // Escape raw report/source text before creating our own renderer markup.
  // LLM output is untrusted input; raw HTML/JS must never reach
  // dangerouslySetInnerHTML.
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    // Normalize line endings, strip trailing spaces, and collapse runs of 3+
    // blank lines (common in LLM output) down to a single blank line so we
    // don't end up stacking extra empty paragraphs / gaps before tables etc.
    .replace(/\r\n/g, '\n')
    .replace(/[ \t]+$/gm, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
    .replace(/^### (.+)$/gm, '<h3 class="md-h3">$1</h3>')
    .replace(/^## (.+)$/gm,  '<h2 class="md-h2">$1</h2>')
    .replace(/^# (.+)$/gm,   '<h1 class="md-h1">$1</h1>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/`([^`]+)`/g, '<code class="md-code">$1</code>')
    // Collapse any blank line(s) sitting directly above a table into a
    // single newline, so the paragraph-break logic below doesn't insert a
    // stray empty <p> right before the <table> (the line break itself must
    // stay, since the per-line table-row regex below needs each row to
    // start at the beginning of a line).
    .replace(/\n{2,}(?=\|.+\|\n\|[\s|:-]+\|)/g, '\n')
    .replace(/^\|(.+)\|$/gm, (row) => {
      if (/^[\s|:-]+$/.test(row)) return '<!--sep-->';
      const cells = row.split('|').filter(Boolean).map(c => `<td class="md-td">${c.trim()}</td>`).join('');
      return `<tr>${cells}</tr>`;
    })
    .replace(
      /((?:<tr>.*?<\/tr>\n?))(<!--sep-->\n)?((?:<tr>.*?<\/tr>\n?)*)/gs,
      (_: string, firstRow: string, sep: string, restRows: string) => {
        if (!firstRow.trim()) return _;
        // The optional trailing \n? in each repeated group gets captured as
        // part of the group text itself (regex groups don't "consume and
        // discard" — whatever they match is included). Strip those here so
        // they don't survive into the table HTML and later get turned into
        // stray <br/> tags by the \n → <br/> pass below.
        const cleanFirst = firstRow.replace(/\n/g, '');
        const cleanRest = restRows.replace(/\n/g, '');
        if (sep) {
          const head = cleanFirst.replace(/<td class="md-td">/g, '<th class="md-th">').replace(/<\/td>/g, '<\/th>');
          return `<table class="md-table"><thead>${head}<\/thead><tbody>${cleanRest}<\/tbody><\/table>`;
        }
        return `<table class="md-table"><tbody>${cleanFirst}${cleanRest}<\/tbody><\/table>`;
      }
    )
    // Collapse blank line(s) sitting between two consecutive bullet-list
    // lines into a single line break. LLM output commonly separates list
    // items with a blank line; left as-is, each bullet ends up wrapped in
    // its own separate <ul> (with a stray paragraph gap between them),
    // which is what produces the large visible gap between list points.
    // Collapsing here lets every consecutive bullet line merge into one
    // continuous <ul> in the conversion below.
    .replace(/^([ \t]*[-*+][ \t]+.+)\n{2,}(?=[ \t]*[-*+][ \t]+)/gm, '$1\n')
    .replace(/^\s*[-*+]\s+(.+)$/gm, '<li class="md-li">$1</li>')
    .replace(/(<li[\s\S]*?<\/li>\n?)+/g, m => `<ul class="md-ul">${m}</ul>`)
    .replace(/\[(\d+)\]/g, '<sup class="md-ref">[$1]</sup>')
    .replace(/\n\n/g, '</p><p class="md-p">')
    .replace(/\n/g, '<br/>')
    .replace(/^(?!<)/, '<p class="md-p">')
    .replace(/(?<!>)$/, '</p>')
    // <table>/<ul>/<h1-3> are block-level elements that browsers refuse to
    // nest inside <p>, which silently mangles the DOM (auto-closing the <p>
    // and re-opening a new, often-empty one) and shows up as a big visual
    // gap. Unwrap any paragraph tags that ended up wrapping these blocks.
    .replace(/<p class="md-p">(\s|<br\/>)*(<table|<ul|<h[123])/g, '$2')
    .replace(/(<\/table>|<\/ul>|<\/h[123]>)(\s|<br\/>)*<\/p>/g, '$1')
    // A stray <br/> can also end up directly in front of a block element
    // even when it's not at the very start of the paragraph (e.g. "Here's
    // the data:<br/><table>...") — drop it, since the block element forces
    // its own line break anyway.
    .replace(/(?:<br\/>)+(?=<table|<ul|<h[123])/g, '')
    // Clean up any empty paragraphs left behind by the above.
    .replace(/<p class="md-p">(\s|<br\/>)*<\/p>/g, '');
}

// ─── Inline chart extraction from markdown tables ─────────────────────────────
/**
 * Parses markdown tables embedded in LLM text into ChartSpec objects for inline rendering.
 * Returns an array of { spec, startIndex, endIndex } so the caller can split the text
 * around them and render text + charts interleaved.
 */
function extractInlineCharts(text: string): { spec: ChartSpec; raw: string }[] {
  const results: { spec: ChartSpec; raw: string }[] = [];

  // Match markdown tables: header row | sep row | data rows
  const tableRe = /(\|.+\|\n\|[-| :]+\|\n(?:\|.+\|\n?)+)/g;
  let m: RegExpExecArray | null;
  while ((m = tableRe.exec(text)) !== null) {
    const raw = m[1].trim();
    const lines = raw.split('\n').filter(l => l.trim());
    if (lines.length < 3) continue;

    // Parse header
    const headerCells = lines[0].split('|').map(c => c.trim()).filter(Boolean);
    // Skip separator line (lines[1])
    const dataLines = lines.slice(2);

    if (headerCells.length < 2) continue;

    // Check if 2nd+ columns are numeric
    const dataRows = dataLines.map(l =>
      l.split('|').map(c => c.trim()).filter(Boolean)
    ).filter(r => r.length >= 2);

    if (dataRows.length < 2) continue;

    const isNumeric = (s: string) => !isNaN(parseFloat(s.replace(/[₹,%\s]/g, '').replace(/,/g, '')));
    const parseNum  = (s: string) => parseFloat(s.replace(/[₹,%\s]/g, '').replace(/,/g, '')) || 0;

    // Check that at least one data column is numeric
    const hasNumericCol = dataRows.some(r => r.slice(1).some(isNumeric));
    if (!hasNumericCol) continue;

    // Determine chart type: if first col looks like years/dates → line, else bar
    const firstColVals = dataRows.map(r => r[0]);
    const looksLikeTimeSeries = firstColVals.every(v =>
      /^\d{4}$/.test(v) ||           // Year: 2020
      /^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)/i.test(v) || // Month
      /^q[1-4]/i.test(v) ||          // Quarter
      /^\d{1,2}[\/\-]\d{2,4}/.test(v) // Date
    );

    // Build series for each numeric column beyond the first
    const numericColIndexes = headerCells
      .slice(1)
      .map((_, i) => i + 1)
      .filter(i => dataRows.some(r => r[i] && isNumeric(r[i])));

    if (numericColIndexes.length === 0) continue;

    const series: ChartSeries[] = numericColIndexes.map(colIdx => ({
      name: headerCells[colIdx] ?? `Series ${colIdx}`,
      data: dataRows
        .filter(r => r[colIdx] && isNumeric(r[colIdx]))
        .map(r => ({ label: r[0], value: parseNum(r[colIdx]) })),
    }));

    // Derive a title from surrounding context (look for text before the table)
    const beforeTable = text.slice(0, m.index);
    const titleMatch = beforeTable.match(/(?:^|\n)(?:#{1,3}\s+|[*_]{0,2})([^\n]{5,80})(?:[*_]{0,2})\s*\n?$/);
    const title = titleMatch
      ? titleMatch[1].replace(/[*_#]/g, '').trim()
      : `${headerCells[0]} vs ${headerCells.slice(1).join(', ')}`;

    // Detect % unit
    const unit = headerCells.slice(1).some(h => h.includes('%')) ||
      dataRows.some(r => r.slice(1).some(v => v.includes('%'))) ? '%' : undefined;

    const spec: ChartSpec = {
      type: looksLikeTimeSeries ? 'line' : 'bar',
      title,
      series,
      ...(unit ? { unit } : {}),
    };

    if (isValidChart(spec)) {
      results.push({ spec, raw });
    }
  }
  return results;
}

/**
 * Splits markdown text around embedded tables that were converted to charts,
 * returning an array of { type: 'text' | 'chart', content }.
 */
function splitTextAndCharts(text: string): Array<{ type: 'text'; content: string } | { type: 'chart'; spec: ChartSpec }> {
  const charts = extractInlineCharts(text);
  if (charts.length === 0) return [{ type: 'text', content: text }];

  const parts: Array<{ type: 'text'; content: string } | { type: 'chart'; spec: ChartSpec }> = [];
  let remaining = text;

  for (const { spec, raw } of charts) {
    const idx = remaining.indexOf(raw);
    if (idx === -1) continue;
    const before = remaining.slice(0, idx);
    if (before.trim()) parts.push({ type: 'text', content: before });
    parts.push({ type: 'chart', spec });
    remaining = remaining.slice(idx + raw.length);
  }
  if (remaining.trim()) parts.push({ type: 'text', content: remaining });
  return parts;
}

// ─── Chart validation ────────────────────────────────────────────────────────
function normalizeInlineChartType(value: unknown): string {
  const raw = String(value ?? '').trim().toLowerCase().replace(/\s+/g, ' ');
  const aliases: Record<string, string> = {
    'pie chart': 'pie', 'bar chart': 'bar', 'line chart': 'line', 'area chart': 'area',
    'scatter plot': 'scatter', 'bubble chart': 'bubble', 'doughnut chart': 'doughnut', 'donut': 'doughnut',
    'box plot': 'boxplot', 'waterfall chart': 'waterfall', 'funnel chart': 'funnel', 'radar chart': 'radar',
    'polar area chart': 'polar_area', 'heat map': 'heatmap', 'stacked bar chart': 'stacked_bar',
    'grouped bar chart': 'grouped_bar', 'horizontal bar chart': 'horizontal_bar', 'stacked area chart': 'stacked_area',
    'step line chart': 'step_line', 'spline chart': 'spline', 'combo chart': 'combo', 'candlestick chart': 'candlestick',
    'ohlc chart': 'ohlc', 'gantt chart': 'gantt', 'gunn chart': 'gantt', 'timeline chart': 'timeline',
    'sankey diagram': 'sankey', 'chord diagram': 'chord', 'network graph': 'network', 'tree diagram': 'tree',
    'flowchart': 'flowchart', 'venn diagram': 'venn', 'funnel pyramid': 'funnel_pyramid',
    'population pyramid': 'population_pyramid', 'progress chart': 'progress', 'kpi chart': 'kpi',
    'sparkline chart': 'sparkline', 'error bar chart': 'error_bar', 'control chart': 'control_chart',
  };
  if (aliases[raw]) return aliases[raw];
  return raw.replace(/[- ]+/g, '_').replace(/_(chart|plot|diagram|graph)$/, '');
}

function isValidChart(spec: ChartSpec): boolean {
  const type = normalizeInlineChartType(spec.type);
  if (type === 'table') {
    const cols = spec.columns ?? [];
    const rows = spec.rows ?? [];
    return cols.length >= 2 && rows.length >= 1;
  }
  if (type === 'heatmap') {
    const rows = spec.rows as unknown[] | undefined;
    const cols = spec.columns ?? [];
    const values = spec.values as unknown[] | undefined;
    return Array.isArray(rows) && rows.length >= 2 && cols.length >= 2 && Array.isArray(values)
      && values.length === rows.length && values.every((r) => Array.isArray(r) && r.length === cols.length);
  }
  if (['sankey', 'chord', 'network', 'tree', 'flowchart'].includes(type)) {
    const links = spec.links as Array<Record<string, unknown>> | undefined;
    const nodes = spec.nodes as unknown[] | undefined;
    return Array.isArray(nodes) && nodes.length >= 2 && Array.isArray(links) && links.length >= 1
      && links.every((l) => l && l.source != null && l.target != null);
  }
  if (type === 'venn') {
    const sets = spec.sets as unknown[] | undefined;
    return Array.isArray(sets) && sets.length >= 2;
  }
  if (type === 'gantt' || type === 'timeline') {
    const events = spec.events as Array<Record<string, unknown>> | undefined;
    return Array.isArray(events) && events.length >= 1 && events.every((e) => e && e.start != null && (e.end ?? e.start) != null);
  }

  const allPts = (spec.series ?? []).flatMap((s) => s.data ?? []);
  if (type === 'candlestick' || type === 'ohlc') {
    return allPts.length >= 2 && allPts.every((p) => ['open', 'high', 'low', 'close'].every((k) => p[k] != null && Number.isFinite(Number(p[k]))));
  }
  if (type === 'boxplot') {
    return allPts.length >= 1 && allPts.every((p) => ['min', 'q1', 'median', 'q3', 'max'].every((k) => p[k] != null && Number.isFinite(Number(p[k]))));
  }
  if (type === 'population_pyramid') {
    return allPts.length >= 2 && allPts.every((p) => (p.left != null || p.value != null) && (p.right != null || p.value != null));
  }
  if (type === 'density' || type === 'violin' || type === 'ridgeline') {
    const obs = allPts.flatMap((p) => Array.isArray(p.values) ? p.values.map(Number) : p.value == null ? [] : [Number(p.value)]).filter(Number.isFinite);
    return obs.length >= 3 && new Set(obs).size > 1;
  }
  if (['gauge', 'progress', 'kpi', 'bullet'].includes(type)) {
    return allPts.length >= 1 && allPts.every((p) => p.value != null && Number.isFinite(Number(p.value))
      && (type !== 'bullet' || (p.target != null && Number.isFinite(Number(p.target)))));
  }
  if (type === 'scatter' || type === 'bubble') {
    const required = type === 'bubble' ? 3 : 2;
    if ((spec.series ?? []).length < required) return false;
    const sets = (spec.series ?? []).slice(0, required).map((series) => new Map(series.data.filter((p) => p.value != null && Number.isFinite(Number(p.value))).map((p) => [p.label, p.value])));
    const common = [...sets[0].keys()].filter((label) => sets.every((set) => set.has(label)));
    return common.length >= 4;
  }

  if (allPts.length < 2) return false;
  const vals = allPts.map((d) => Number(d.value));
  if (vals.some((v) => !Number.isFinite(v))) return false;
  if (new Set(vals).size < 2 || vals.every((v) => v === 0)) return false;
  const labels = allPts.map((d) => d.label);
  return new Set(labels).size >= 2;
}

// ─── Mini SVG Charts ──────────────────────────────────────────────────────────
// ─── Editorial charts (v93) ───────────────────────────────────────────────────
// Premium in-app chart renderers shared by chat answers and the report preview.
// Presentation only: every value drawn is the value in the chart spec, at the
// spec's own precision. Colour follows the series (fixed order, never by rank);
// the palette is the same validated set used by the HTML/PDF renderer
// (backend/routes/report_design.py CHART_PALETTE).
const ED_PALETTE = ['#17845A', '#3461A8', '#C27C26', '#A64B63', '#0897AC', '#7E62B5'];
const ED_NEGATIVE = '#9A3B2E';
const ED_INK = '#2A2A2A';
const ED_MUTED = '#6F6B63';
const ED_GRID = '#ECE8E0';
const ED_RULE = '#E1DDD5';
const ED_FONT = "Inter, system-ui, -apple-system, 'Segoe UI', Arial, sans-serif";

function edDecimals(v: number): number {
  if (!Number.isFinite(v) || Number.isInteger(v)) return 0;
  const text = String(v);
  if (text.includes('e')) return 2;
  return Math.min(3, (text.split('.')[1] ?? '').length);
}

/** Format a plotted value exactly as precise as the source value (no forced decimals). */
function edFormatValue(v: number, unit?: string): string {
  const s = v.toLocaleString('en-IN', { minimumFractionDigits: 0, maximumFractionDigits: edDecimals(v) });
  const u = String(unit ?? '').trim();
  if (u === '%') return `${s}%`;
  return s;
}

function edNiceScale(minV: number, maxV: number, includeZero: boolean): { lo: number; hi: number; ticks: number[] } {
  let lo = includeZero ? Math.min(0, minV) : minV;
  let hi = includeZero ? Math.max(0, maxV) : maxV;
  if (hi === lo) { const pad = Math.abs(hi) * 0.1 || 1; hi += pad; lo -= includeZero && lo >= 0 ? 0 : pad; }
  const raw = (hi - lo) / 5;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const frac = raw / mag;
  const step = ([1, 2, 2.5, 5, 10].find((m) => frac <= m) ?? 10) * mag;
  lo = Math.floor(lo / step + 1e-9) * step;
  hi = Math.ceil(hi / step - 1e-9) * step;
  const ticks: number[] = [];
  for (let t = lo; t <= hi + step / 2; t += step) ticks.push(Math.round(t / step) * step);
  return { lo, hi, ticks };
}

function edTick(t: number, top: number): string {
  if (Math.abs(t) < 1e-12) return '0';
  if (top >= 1_000_000) return `${+(t / 1_000_000).toFixed(2)}M`;
  if (top >= 10_000) return `${+(t / 1_000).toFixed(2)}k`;
  return t.toLocaleString('en-IN', { maximumFractionDigits: 2 });
}

/** Word-wrap a category label; ellipsize only what cannot fit in maxLines. */
function edWrap(label: string, maxChars: number, maxLines = 2): string[] {
  const words = String(label ?? '').split(/\s+/).filter(Boolean);
  if (!words.length) return [''];
  const lines: string[] = [];
  let cur = '';
  for (const w of words) {
    const cand = cur ? `${cur} ${w}` : w;
    if (cand.length <= maxChars || !cur) cur = cand; else { lines.push(cur); cur = w; }
  }
  lines.push(cur);
  if (lines.length > maxLines) {
    const kept = lines.slice(0, maxLines);
    kept[maxLines - 1] = `${kept[maxLines - 1]} ${lines.slice(maxLines).join(' ')}`;
    return kept.map((l) => (l.length > maxChars ? `${l.slice(0, Math.max(1, maxChars - 1)).trimEnd()}…` : l));
  }
  return lines.map((l) => (l.length > maxChars ? `${l.slice(0, Math.max(1, maxChars - 1)).trimEnd()}…` : l));
}

function edSourceText(spec: ChartSpec): string {
  const raw = (spec as { source?: unknown; sources?: unknown; sourceIds?: unknown }).source
    ?? (spec as { sources?: unknown }).sources ?? (spec as { sourceIds?: unknown }).sourceIds;
  const list = Array.isArray(raw) ? raw.map(String) : raw ? [String(raw)] : [];
  return list.map((x) => x.trim()).filter(Boolean).map((x) => (/^S\d+$/i.test(x) ? `[${x.toUpperCase()}]` : x)).join(' · ');
}

function edUnitLabel(spec: ChartSpec): string {
  const unit = String(spec.unit ?? '').trim();
  if (!unit) return '';
  return unit === '%' ? 'Per cent' : unit;
}

/** Measured plot width so SVG text renders at its true pixel size on every screen. */
function useEdWidth(fallback = 640): [React.RefObject<HTMLDivElement | null>, number] {
  const ref = useRef<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(fallback);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    // ResizeObserver reports the initial size on observe(), so no synchronous setState here.
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect?.width ?? 0;
      if (w > 0) setWidth(Math.round(w));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, width];
}

function EdChartFrame({ spec, legend, children }: { spec: ChartSpec; legend?: { name: string; color: string; value?: string }[]; children: React.ReactNode }) {
  const source = edSourceText(spec);
  const unit = edUnitLabel(spec);
  const caption = typeof (spec as { caption?: unknown }).caption === 'string' ? String((spec as { caption?: string }).caption) : '';
  return (
    <figure className="ed-chart" role="group" aria-label={spec.title}>
      <figcaption className="ed-chart-head">
        <span className="ed-chart-title">{spec.title}</span>
        {unit ? <span className="ed-chart-unit">{unit}</span> : null}
      </figcaption>
      <div className="ed-chart-plot">{children}</div>
      {legend && legend.length > 1 ? (
        <ul className="ed-chart-legend">
          {legend.map((l, i) => (
            <li key={i}><i style={{ background: l.color }} aria-hidden="true" /><span>{l.name}</span>{l.value ? <b>{l.value}</b> : null}</li>
          ))}
        </ul>
      ) : null}
      {caption ? <p className="ed-chart-caption">{caption}</p> : null}
      {source ? <p className="ed-chart-source"><span>Source</span>{source}</p> : null}
    </figure>
  );
}

function EdBarChart({ spec, horizontal = false }: { spec: ChartSpec; horizontal?: boolean }) {
  const [plotRef, plotW] = useEdWidth();
  const W = Math.round(Math.max(300, Math.min(760, plotW)));
  const series = (spec.series ?? []).filter((s) => Array.isArray(s?.data) && s.data.length).slice(0, 6);
  const labels: string[] = [];
  series.forEach((s) => s.data.forEach((d) => { if (d && !labels.includes(String(d.label))) labels.push(String(d.label)); }));
  if (!series.length || labels.length < 1 || (labels.length < 2 && series.length < 2)) return null;
  const valueOf = (s: ChartSeries, label: string): number | null => {
    const p = s.data.find((d) => String(d.label) === label);
    const v = p ? Number(p.value) : NaN;
    return Number.isFinite(v) ? v : null;
  };
  const all = series.flatMap((s) => labels.map((l) => valueOf(s, l))).filter((v): v is number => v !== null);
  if (!all.length) return null;
  const { lo, hi, ticks } = edNiceScale(Math.min(...all), Math.max(...all), true);
  const top = Math.max(Math.abs(lo), Math.abs(hi));
  const colorFor = (si: number, v: number) => (series.length === 1 && v < 0 ? ED_NEGATIVE : (series[si].color as string | undefined) ?? ED_PALETTE[si % ED_PALETTE.length]);
  const legend = series.map((s, i) => ({ name: s.name, color: (s.color as string | undefined) ?? ED_PALETTE[i % ED_PALETTE.length] }));
  const showValues = labels.length * series.length <= 16;

  if (horizontal) {
    const longest = Math.max(...labels.map((l) => l.length), 6);
    const ml = Math.min(W * 0.4, Math.max(64, 16 + Math.min(longest, 34) * 6.2));
    const mr = 44, mt = 12, mb = 30;
    const rowH = Math.max(26, 14 + series.length * 12);
    const H = mt + mb + labels.length * rowH;
    const pw = W - ml - mr;
    const x = (v: number) => ml + ((v - lo) / (hi - lo || 1)) * pw;
    const barH = Math.min(18, (rowH * 0.66) / series.length);
    const labelChars = Math.max(6, Math.floor((ml - 14) / 6.2));
    return (
      <EdChartFrame spec={spec} legend={legend}>
        <div ref={plotRef}><svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label={spec.title} style={{ fontFamily: ED_FONT, display: 'block' }}>
          {ticks.map((t, i) => (
            <g key={i}>
              <line x1={x(t)} x2={x(t)} y1={mt} y2={H - mb} stroke={t === 0 ? ED_RULE : ED_GRID} strokeWidth={1} />
              <text x={x(t)} y={H - mb + 16} textAnchor="middle" fontSize={11} fill={ED_MUTED}>{edTick(t, top)}</text>
            </g>
          ))}
          {labels.map((label, li) => {
            const cy = mt + (li + 0.5) * rowH;
            return (
              <g key={li}>
                <text x={ml - 10} y={cy + 4} textAnchor="end" fontSize={12} fill={ED_INK}>{edWrap(label, labelChars, 1)[0]}<title>{label}</title></text>
                {series.map((s, si) => {
                  const v = valueOf(s, label);
                  if (v === null) return null;
                  const y = cy - (series.length * barH) / 2 + si * barH;
                  const x0 = x(Math.max(lo, Math.min(0, hi))), x1 = x(v);
                  return (
                    <g key={si}>
                      <rect x={Math.min(x0, x1)} y={y + 1} width={Math.max(1, Math.abs(x1 - x0))} height={Math.max(2, barH - 2)} rx={2} fill={colorFor(si, v)}>
                        <title>{`${label}${series.length > 1 ? ` · ${s.name}` : ''}: ${edFormatValue(v, spec.unit)}`}</title>
                      </rect>
                      {showValues ? <text x={Math.max(x0, x1) + 6} y={y + barH / 2 + 4} fontSize={11} fill={ED_INK}>{edFormatValue(v, spec.unit)}</text> : null}
                    </g>
                  );
                })}
              </g>
            );
          })}
        </svg></div>
      </EdChartFrame>
    );
  }

  const H = Math.round(Math.max(240, Math.min(340, W * 0.5))), ml = 48, mr = 12, mt = 18;
  const slot = (W - ml - mr) / labels.length;
  // Every bar is drawn; dense charts label every n-th category so text never overlaps.
  const labelStride = Math.max(1, Math.ceil(labels.length / Math.max(1, Math.floor((W - ml - mr) / 44))));
  const chars = Math.max(4, Math.floor((slot * labelStride) / 6.4));
  const wrapped = labels.map((l, li) => (li % labelStride === 0 ? edWrap(l, chars, 2) : []));
  const mb = 26 + 14 * Math.max(...wrapped.map((w) => w.length));
  const ph = H - mt - mb;
  const y = (v: number) => mt + ((hi - v) / (hi - lo || 1)) * ph;
  const groupW = slot * 0.72;
  const barW = Math.max(3, groupW / series.length);
  return (
    <EdChartFrame spec={spec} legend={legend}>
      <div ref={plotRef}><svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label={spec.title} style={{ fontFamily: ED_FONT, display: 'block' }}>
        {ticks.map((t, i) => (
          <g key={i}>
            <line x1={ml} x2={W - mr} y1={y(t)} y2={y(t)} stroke={t === 0 ? ED_RULE : ED_GRID} strokeWidth={1} />
            <text x={ml - 8} y={y(t) + 4} textAnchor="end" fontSize={11} fill={ED_MUTED}>{edTick(t, top)}</text>
          </g>
        ))}
        {labels.map((label, li) => {
          const gx = ml + li * slot + (slot - groupW) / 2;
          return (
            <g key={li}>
              {series.map((s, si) => {
                const v = valueOf(s, label);
                if (v === null) return null;
                const y0 = y(Math.max(lo, Math.min(0, hi))), y1 = y(v);
                const bx = gx + si * barW;
                return (
                  <g key={si}>
                    <rect x={bx + 1} y={Math.min(y0, y1)} width={Math.max(2, barW - 2)} height={Math.max(1, Math.abs(y1 - y0))} rx={2} fill={colorFor(si, v)}>
                      <title>{`${label}${series.length > 1 ? ` · ${s.name}` : ''}: ${edFormatValue(v, spec.unit)}`}</title>
                    </rect>
                    {showValues ? <text x={bx + barW / 2} y={v >= 0 ? Math.min(y0, y1) - 5 : Math.max(y0, y1) + 13} textAnchor="middle" fontSize={11} fill={ED_INK}>{edFormatValue(v, spec.unit)}</text> : null}
                  </g>
                );
              })}
              {wrapped[li].map((line, k) => (
                <text key={k} x={ml + (li + 0.5) * slot} y={H - mb + 18 + k * 14} textAnchor="middle" fontSize={11.5} fill={ED_INK}>{line}</text>
              ))}
            </g>
          );
        })}
      </svg></div>
    </EdChartFrame>
  );
}

function EdLineChart({ spec }: { spec: ChartSpec }) {
  const [plotRef, plotW] = useEdWidth();
  const W = Math.round(Math.max(300, Math.min(760, plotW)));
  const series = (spec.series ?? []).filter((s) => Array.isArray(s?.data) && s.data.length).slice(0, 6);
  const labels: string[] = [];
  series.forEach((s) => s.data.forEach((d) => { if (d && !labels.includes(String(d.label))) labels.push(String(d.label)); }));
  const all = series.flatMap((s) => s.data.map((d) => Number(d.value))).filter((v) => Number.isFinite(v));
  if (all.length < 2 || labels.length < 2) return null;
  const { lo, hi, ticks } = edNiceScale(Math.min(...all), Math.max(...all), Math.min(...all) >= 0 && Math.min(...all) < (Math.max(...all) * 0.35));
  const top = Math.max(Math.abs(lo), Math.abs(hi));
  const H = Math.round(Math.max(220, Math.min(320, W * 0.47))), ml = 48, mr = 24, mt = 22, mb = 40;
  const pw = W - ml - mr, ph = H - mt - mb;
  // Inset the first/last points from the axes so point labels never collide with tick text.
  const inset = Math.min(28, pw * 0.05);
  const x = (i: number) => ml + inset + (labels.length === 1 ? (pw - 2 * inset) / 2 : (i / (labels.length - 1)) * (pw - 2 * inset));
  const y = (v: number) => mt + ((hi - v) / (hi - lo || 1)) * ph;
  const step = Math.max(1, Math.ceil(labels.length / 8));
  const legend = series.map((s, i) => ({ name: s.name, color: (s.color as string | undefined) ?? ED_PALETTE[i % ED_PALETTE.length] }));
  const labelPoints = labels.length <= 12;
  return (
    <EdChartFrame spec={spec} legend={legend}>
      <div ref={plotRef}><svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label={spec.title} style={{ fontFamily: ED_FONT, display: 'block' }}>
        {ticks.map((t, i) => (
          <g key={i}>
            <line x1={ml} x2={W - mr} y1={y(t)} y2={y(t)} stroke={t === 0 ? ED_RULE : ED_GRID} strokeWidth={1} />
            <text x={ml - 8} y={y(t) + 4} textAnchor="end" fontSize={11} fill={ED_MUTED}>{edTick(t, top)}</text>
          </g>
        ))}
        {labels.map((l, i) => (i % step === 0 || i === labels.length - 1) ? (
          <text key={i} x={x(i)} y={H - mb + 20} textAnchor="middle" fontSize={11.5} fill={ED_INK}>{edWrap(l, 12, 1)[0]}</text>
        ) : null)}
        {series.map((s, si) => {
          const color = legend[si].color;
          const pts = labels.map((l, i) => {
            const p = s.data.find((d) => String(d.label) === l);
            const v = p ? Number(p.value) : NaN;
            return Number.isFinite(v) ? { x: x(i), y: y(v), v, l } : null;
          });
          // Missing observations break the line instead of being drawn as zero.
          const segments: { x: number; y: number; v: number; l: string }[][] = [];
          let cur: { x: number; y: number; v: number; l: string }[] = [];
          pts.forEach((p) => { if (p) cur.push(p); else if (cur.length) { segments.push(cur); cur = []; } });
          if (cur.length) segments.push(cur);
          return (
            <g key={si}>
              {segments.map((seg, k) => seg.length > 1 ? (
                <polyline key={k} points={seg.map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ')} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
              ) : null)}
              {pts.map((p, i) => p ? (
                <g key={i}>
                  <circle cx={p.x} cy={p.y} r={4} fill="#FFFFFF" stroke={color} strokeWidth={2}><title>{`${p.l}${series.length > 1 ? ` · ${s.name}` : ''}: ${edFormatValue(p.v, spec.unit)}`}</title></circle>
                  {(labelPoints && series.length === 1) || i === pts.length - 1 ? <text x={p.x} y={p.y - 10} textAnchor="middle" fontSize={11} fill={ED_INK}>{edFormatValue(p.v, spec.unit)}</text> : null}
                </g>
              ) : null)}
            </g>
          );
        })}
      </svg></div>
    </EdChartFrame>
  );
}

/** Keeps the 10 largest slices (original order) and sums the rest into "Other"; mirrors fold_slice_tail_for_display. */
function edFoldSliceTail<T extends { label?: unknown; value?: unknown }>(data: T[], limit = 10): Array<T | { label: string; value: number }> {
  if (data.length <= limit + 1) return data;
  const keep = new Set(data.map((d, i) => [Math.abs(Number(d.value)), i] as const).sort((a, b) => b[0] - a[0]).slice(0, limit).map(([, i]) => i));
  const tail = data.filter((_, i) => !keep.has(i));
  return [...data.filter((_, i) => keep.has(i)), { label: `Other (${tail.length})`, value: tail.reduce((acc, d) => acc + Number(d.value), 0) }];
}

function EdPieChart({ spec, donut = false }: { spec: ChartSpec; donut?: boolean }) {
  const data = edFoldSliceTail(((spec.series ?? [])[0]?.data ?? []).filter((d) => Number.isFinite(Number(d?.value)) && Number(d.value) >= 0));
  const total = data.reduce((acc, d) => acc + Number(d.value), 0);
  if (data.length < 2 || total <= 0) return null;
  const R = 92, r = donut ? 54 : 0, cx = 100, cy = 100;
  const starts = data.map((_, i) => data.slice(0, i).reduce((acc, d) => acc + Number(d.value), 0) / total);
  const slices = data.map((d, i) => {
    const frac = Number(d.value) / total;
    const a0 = -Math.PI / 2 + starts[i] * 2 * Math.PI, a1 = a0 + frac * 2 * Math.PI;
    const large = a1 - a0 > Math.PI ? 1 : 0;
    const p = (rad: number, ang: number) => `${(cx + rad * Math.cos(ang)).toFixed(2)} ${(cy + rad * Math.sin(ang)).toFixed(2)}`;
    const path = r > 0
      ? `M${p(R, a0)}A${R} ${R} 0 ${large} 1 ${p(R, a1)}L${p(r, a1)}A${r} ${r} 0 ${large} 0 ${p(r, a0)}Z`
      : `M${cx} ${cy}L${p(R, a0)}A${R} ${R} 0 ${large} 1 ${p(R, a1)}Z`;
    return { path, color: ED_PALETTE[i % ED_PALETTE.length], label: String(d.label), value: Number(d.value), pct: frac * 100 };
  });
  const isShare = String(spec.unit ?? '').trim() === '%';
  const legend = slices.map((s) => ({ name: s.label, color: s.color, value: isShare ? edFormatValue(s.value, '%') : `${edFormatValue(s.value, spec.unit)} · ${s.pct.toFixed(1)}%` }));
  return (
    <EdChartFrame spec={spec} legend={legend}>
      <div className="ed-chart-pie">
        <svg viewBox="0 0 200 200" role="img" aria-label={spec.title} style={{ fontFamily: ED_FONT }}>
          {slices.map((s, i) => (
            <path key={i} d={s.path} fill={s.color} stroke="#FFFFFF" strokeWidth={2}><title>{`${s.label}: ${edFormatValue(s.value, spec.unit)} (${s.pct.toFixed(1)}%)`}</title></path>
          ))}
        </svg>
      </div>
    </EdChartFrame>
  );
}

// ─── Table fallback (used only if Datawrapper isn't configured/available) ────
function TableChart({ spec }: { spec: ChartSpec }) {
  const columns = spec.columns ?? [];
  const rows = spec.rows ?? [];
  if (columns.length < 2 || rows.length < 1) return null;
  return (
    <div className="chart-wrap">
      <div className="chart-title">{spec.title}</div>
      <div style={{ overflowX: 'auto' }}>
        <table className="md-table">
          <thead>
            <tr>{columns.map((c,i) => <th key={i} className="md-th">{c}</th>)}</tr>
          </thead>
          <tbody>
            {rows.map((r,ri) => (
              <tr key={ri}>{r.map((cell,ci) => <td key={ci} className="md-td">{cell}</td>)}</tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function StructuredChartPreview({ spec }: { spec: ChartSpec }) {
  const type = normalizeInlineChartType(spec.type);
  const title = spec.title || `${type.replace(/_/g, ' ')} chart`;
  const rows = (spec.series ?? []).flatMap((s) => (s.data ?? []).map((p) => {
    if (type === 'candlestick' || type === 'ohlc') return [p.label, p.open, p.high, p.low, p.close].map((v) => String(v ?? '—'));
    if (type === 'boxplot') return [p.label, p.min, p.q1, p.median, p.q3, p.max].map((v) => String(v ?? '—'));
    if (type === 'bullet') return [p.label, p.value, p.target].map((v) => String(v ?? '—'));
    if (type === 'population_pyramid') return [p.label, p.left ?? p.value, p.right ?? p.value].map((v) => String(v ?? '—'));
    return [p.label, p.value].map((v) => String(v ?? '—'));
  }));
  const cols = type === 'candlestick' || type === 'ohlc' ? ['Period', 'Open', 'High', 'Low', 'Close']
    : type === 'boxplot' ? ['Group', 'Min', 'Q1', 'Median', 'Q3', 'Max']
    : type === 'bullet' ? ['Metric', 'Actual', 'Target']
    : type === 'population_pyramid' ? ['Cohort', 'Left group', 'Right group']
    : ['Label', (spec.series ?? [])[0]?.name ?? 'Value'];
  const structured = type === 'heatmap' && Array.isArray(spec.rows) && Array.isArray(spec.columns) && Array.isArray(spec.values)
    ? (spec.rows as unknown as string[]).flatMap((r, i) => (spec.columns as string[]).map((c, j) => [String(r), String(c), String((spec.values as unknown[][])[i]?.[j] ?? '—')]))
    : rows;
  const structuredCols = type === 'heatmap' ? ['Row', 'Column', 'Value'] : cols;
  return (
    <div className="chart-wrap">
      <div className="chart-title">{title}</div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        <span style={{ background: '#eaf2ff', color: '#244a86', padding: '3px 7px', borderRadius: 6, textTransform: 'uppercase', fontSize: 9, fontWeight: 800, letterSpacing: '.08em' }}>{type.replace(/_/g, ' ')}</span>
        <span style={{ color: '#7b849b', fontSize: 10 }}>Detailed visual available in the generated report</span>
      </div>
      {['sankey', 'chord', 'network', 'tree', 'flowchart'].includes(type) && Array.isArray(spec.nodes) && Array.isArray(spec.links) ? (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 8 }}>
          {(spec.nodes as Array<string | {label?: string; id?: string}>).slice(0, 16).map((n, i) => <span key={i} style={{ border: '1px solid #d9e1ee', borderRadius: 8, padding: '5px 8px', background: '#f7f9fd', fontSize: 11 }}>{typeof n === 'string' ? n : (n.label ?? n.id ?? `Node ${i+1}`)}</span>)}
        </div>
      ) : null}
      {structured.length ? <div style={{ overflow: 'auto', maxHeight: 360 }}><table className="md-table"><thead><tr>{structuredCols.map((c, i) => <th key={i} className="md-th">{c}</th>)}</tr></thead><tbody>{structured.map((row, ri) => <tr key={ri}>{row.map((cell, ci) => <td key={ci} className="md-td">{cell}</td>)}</tr>)}</tbody></table></div> : (
        <div style={{ color: '#69758c', fontSize: 12 }}>This chart uses a specialized structure (such as sets, events, or nodes/links) and is rendered in the full report.</div>
      )}
    </div>
  );
}

function ChartBlock({ spec }: { spec: ChartSpec }) {
  if (!isValidChart(spec)) return null;
  const type = normalizeInlineChartType(spec.type);
  const dwLink = spec.datawrapper?.publicUrl;
  let inner: React.ReactNode;
  // v93: editorial renderers (EdBarChart/EdLineChart/EdPieChart).
  if (type === 'table') inner = <TableChart spec={spec}/>;
  else if (type === 'pie') inner = <EdPieChart spec={spec}/>;
  else if (type === 'doughnut') inner = <EdPieChart spec={spec} donut />;
  else if (type === 'polar_area') inner = <StructuredChartPreview spec={{ ...spec, type }} />;
  else if (['line', 'area', 'stacked_area', 'step_line', 'spline', 'sparkline'].includes(type)) inner = <EdLineChart spec={spec}/>;
  else if (type === 'horizontal_bar') inner = <EdBarChart spec={spec} horizontal />;
  else if (['bar', 'stacked_bar', 'grouped_bar', 'waterfall', 'histogram', 'pareto', 'dot_plot', 'lollipop'].includes(type)) {
    const longLabels = ((spec.series ?? [])[0]?.data ?? []).some((d) => String(d?.label ?? '').length > 22) && ((spec.series ?? [])[0]?.data ?? []).length > 4;
    inner = <EdBarChart spec={spec} horizontal={longLabels} />;
  }
  else inner = <StructuredChartPreview spec={{ ...spec, type }}/>;
  if (inner === null || inner === undefined) return null;
  if (!dwLink) return inner;
  return (
    <div>
      {inner}
      <div style={{ textAlign: 'right', marginTop: 4 }}>
        <a href={dwLink} target="_blank" rel="noopener noreferrer"
          style={{ fontSize: 10, color: '#3b82f6', fontFamily: 'DM Sans,sans-serif', textDecoration: 'none' }}>
          Open in Datawrapper ↗
        </a>
      </div>
    </div>
  );
}

// ─── Presentation helpers ─────────────────────────────────────────────────────
type PresentationSection = {
  title?: string;
  section_type?: string;
  layout?: string;
  density?: string;
  emphasis?: string;
  order?: number;
  blocks?: Array<{ kind?: string }>;
};

type ReportPresentation = {
  domain?: string;
  cover?: { enabled?: boolean; treatment?: string };
  executive_summary?: { placement?: string };
  sections?: PresentationSection[];
  default_layout?: string;
  default_density?: string;
};

function getReportPresentation(rd?: ReportData): ReportPresentation | null {
  const p = rd?.presentation;
  return p && typeof p === 'object' ? p as ReportPresentation : null;
}

function normalizeSectionTitle(value: unknown): string {
  return String(value ?? '')
    .replace(/^\s*\d+[.)]\s*/, '')
    .trim()
    .toLowerCase();
}

function presentationSectionFor(title: string, presentation: ReportPresentation | null): PresentationSection | null {
  const wanted = normalizeSectionTitle(title);
  if (!wanted || !presentation?.sections?.length) return null;
  return presentation.sections.find((s) => normalizeSectionTitle(s.title) === wanted) ?? null;
}

function presentationClass(section: PresentationSection | null, presentation: ReportPresentation | null): string {
  const safe = (v: unknown, fallback: string) => String(v ?? fallback).toLowerCase().replace(/[^a-z0-9_-]+/g, '_');
  return [
    'report-section-shell',
    `report-domain-${safe(presentation?.domain, 'generic')}`,
    `report-layout-${safe(section?.layout, presentation?.default_layout || 'single_column')}`,
    `report-density-${safe(section?.density, presentation?.default_density || 'standard')}`,
    `report-emphasis-${safe(section?.emphasis, 'normal')}`,
    `report-type-${safe(section?.section_type, 'narrative')}`,
  ].join(' ');
}

// ─── Report Panel ─────────────────────────────────────────────────────────────
function getReportSectionOptions(reportData?: ReportData): string[] {
  if (!reportData) return [];
  const out: string[] = [];
  // The report markdown is the source of truth for Action targets. Presentation
  // plans may contain layout-only or stale labels, so they must never populate
  // the selector ahead of the actual generated sections.
  for (const line of String(reportData.report || '').split(/\r?\n/)) {
    const m = line.match(/^##\s+(.+?)\s*#*$/);
    if (!m) continue;
    const title = m[1].replace(/\*\*/g, '').trim();
    if (title && !out.some(x => x.toLowerCase() === title.toLowerCase())) out.push(title);
  }
  // Legacy reports may have no H2s; only then fall back to presentation labels.
  if (!out.length) {
    const presentation = reportData.presentation as any;
    for (const section of (Array.isArray(presentation?.sections) ? presentation.sections : [])) {
      const title = String(section?.title || section?.heading || '').trim();
      if (title && !out.some(x => x.toLowerCase() === title.toLowerCase())) out.push(title);
    }
  }
  return out.slice(0, 32);
}

// ─── Report Experiment / Action controls ─────────────────────────────────────
// Experiment = start a NEW report from a new prompt. It does not inherit the
// current report's prose or source list, so it is a clean research experiment.
// Action = edit the CURRENT generated report. It calls the existing section-edit
// endpoint and preserves every unrelated section/chart/source.
function ReportPromptModal({
  mode,
  initialPrompt = '',
  onClose,
  onSubmit,
  submitting = false,
  sectionOptions = [],
}: {
  mode: 'experiment' | 'action';
  initialPrompt?: string;
  onClose: () => void;
  onSubmit: (prompt: string) => void;
  submitting?: boolean;
  sectionOptions?: string[];
}) {
  const [value, setValue] = useState(initialPrompt);
  const [selectedSection, setSelectedSection] = useState('');
  const isExperiment = mode === 'experiment';
  const title = isExperiment ? 'Experiment' : 'Action';
  const subtitle = isExperiment
    ? 'Run a new research prompt and create a separate report.'
    : 'Change one part of the current report without regenerating the rest.';
  const placeholder = isExperiment
    ? 'Example: Compare EV adoption in India, China and Europe in a Brief report.'
    : 'Example: Make the Regional Landscape section shorter and add a comparison chart using only its existing evidence.';

  return (
    <div className="report-prompt-overlay" onClick={e => { if (e.target === e.currentTarget && !submitting) onClose(); }}>
      <div className="report-prompt-modal" role="dialog" aria-modal="true" aria-label={`${title} report`}>
        <div className="report-prompt-head">
          <div>
            <div className="report-prompt-kicker">Report {isExperiment ? 'Experiment' : 'Action'}</div>
            <div className="report-prompt-title">{title}</div>
            <div className="report-prompt-subtitle">{subtitle}</div>
          </div>
          <button type="button" onClick={onClose} disabled={submitting} className="report-prompt-close">×</button>
        </div>
        <div className="report-prompt-body">
          {!isExperiment && (
            <div style={{ marginBottom: 12 }}>
              <label className="report-prompt-label">Target section</label>
              <select
                value={selectedSection}
                onChange={e => setSelectedSection(e.target.value)}
                disabled={submitting}
                className="report-prompt-section-select"
              >
                <option value="">Auto-detect from your instruction</option>
                {sectionOptions.map(section => <option key={section} value={section}>{section}</option>)}
              </select>
            </div>
          )}
          <label className="report-prompt-label">{isExperiment ? 'New research prompt' : 'Instruction'}</label>
          <textarea
            autoFocus
            value={value}
            onChange={e => setValue(e.target.value)}
            placeholder={placeholder}
            disabled={submitting}
            className="report-prompt-textarea"
            rows={5}
          />
          <div className="report-prompt-hint">
            {isExperiment
              ? 'Starts a fresh report. The current report remains unchanged.'
              : 'Only the matched section is changed. Other sections, charts, images and sources are preserved.'}
          </div>
          <div className="report-prompt-actions">
            <button type="button" onClick={onClose} disabled={submitting} className="report-prompt-cancel">Cancel</button>
            <button
              type="button"
              onClick={() => {
                const q = value.trim();
                if (!q) return;
                const scoped = !isExperiment && selectedSection
                  ? `Target section: "${selectedSection}"\nInstruction: ${q}`
                  : q;
                onSubmit(scoped);
              }}
              disabled={!value.trim() || submitting}
              className={`report-prompt-submit ${isExperiment ? 'is-experiment' : 'is-action'}`}
            >
              {submitting ? 'Working…' : isExperiment ? 'Run Experiment' : 'Apply Action'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── Report Panel ─────────────────────────────────────────────────────────────
function ReportPanel({ msg, question, hasPriorContext, onGenerate, onExperiment, onAction, onRestore, initialTier = 'detailed' }: { msg: Message; question: string; hasPriorContext: boolean; onGenerate: (includeContext: boolean, tier: ReportTier) => void; onExperiment: (prompt: string, tier: ReportTier) => void; onAction: (instruction: string) => void; onRestore: (revision: ReportRevision) => void; initialTier?: ReportTier }) {
  const [open, setOpen]               = useState(false);
  const [pdfLoading, setPdfLoading] = useState(false);
  const [artifactError, setArtifactError] = useState<string | null>(null);
  const [promptMode, setPromptMode] = useState<'experiment' | 'action' | null>(null);
  const [promptSubmitting, setPromptSubmitting] = useState(false);
  // Default ON to match the previous always-include behaviour; only ever
  // shown when there's actually prior conversation to include (i.e. not the
  // very first response in the thread).
  const [includeContext, setIncludeContext] = useState(true);
  const [selectedTier, setSelectedTier] = useState<ReportTier>(msg.reportData?.reportTier ?? initialTier);

  useEffect(() => {
    // Before a report is generated, mirror the current composer selection.
    // After generation, the report's own tier remains authoritative for PDF export.
    if (msg.reportData?.reportTier) {
      setSelectedTier(msg.reportData.reportTier);
    } else {
      setSelectedTier(initialTier);
    }
  }, [msg.reportData?.reportTier, initialTier]);

  const rd = msg.reportData;
  const loading = msg.reportLoading ?? false;
  const error = msg.reportError;
  // done requires actual report text, not just a resolved reportData object —
  // an object with report: '' must never be treated as a completed report.
  const done = !!rd && !!rd.report && rd.report.trim().length > 0;
  const eligible = msg.reportEligible ?? false;

  useEffect(() => {
    if (done) setOpen(true);
  }, [done]);

  if (!loading && !done && !error) return null;

  const downloadPdf = async () => {
    if (!rd || !rd.report || !rd.report.trim() || pdfLoading) return;
    setPdfLoading(true);
    setArtifactError(null);
    try {
      const res = await fetch('/api/chat/report/pdf', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ report: rd.report, title: rd.title, charts: rd.charts, images: rd.images ?? [], question, keyStats: rd.keyStats, summary: rd.summary, fileImages: rd.fileImages ?? [], sources: rd.sources ?? [], theme: rd.theme ?? null, presentation: rd.presentation ?? null, reportTier: rd.reportTier ?? selectedTier,
          reportStyle: rd.reportStyle ?? 'market_currents', kicker: rd.kicker ?? '',
          // A draft (generated but not approved) is exported as a labelled
          // draft with the same charts as the preview; approved reports keep
          // the strict export gate.
          draftExport: isDraftReport(rd), publicationOutcome: rd.publicationOutcome ?? undefined }),
      });
      if (!res.ok) {
        let message = `PDF generation failed (HTTP ${res.status}).`;
        try {
          const body = await res.json();
          if (typeof body?.error === 'string' && body.error.trim()) message = body.error;
          if (body?.failedStage) message = `PDF export failed at the ${reportStageLabel(body.failedStage)}: ${message}`;
        } catch { /* non-JSON upstream error */ }
        setArtifactError(message);
        return;
      }
      const contentType = (res.headers.get('Content-Type') ?? '').toLowerCase();
      if (!contentType.includes('application/pdf')) {
        let message = 'The server returned an unexpected response instead of a PDF.';
        try {
          const body = await res.text();
          if (body.trim()) message = body.slice(0, 240);
        } catch { /* ignore */ }
        setArtifactError(message);
        return;
      }
      const blob = await res.blob();
      if (!blob.size) {
        setArtifactError('The generated PDF was empty. Please try again.');
        return;
      }
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `growth-gradual-report-${isDraftReport(rd) ? 'DRAFT-' : ''}${new Date().toISOString().slice(0,10)}.pdf`;
      a.style.display = 'none';
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 5000);
    } catch(e) {
      console.error('[downloadPdf]', e);
      setArtifactError('Could not download the PDF. Please try again.');
    } finally { setPdfLoading(false); }
  };

  return (
    <>
      {promptMode && (
        <ReportPromptModal
          mode={promptMode}
          sectionOptions={getReportSectionOptions(rd)}
          onClose={() => { if (!promptSubmitting) setPromptMode(null); }}
          submitting={promptSubmitting}
          onSubmit={async (prompt) => {
            setPromptSubmitting(true);
            try {
              if (promptMode === 'experiment') {
                await Promise.resolve(onExperiment(prompt, selectedTier));
              } else {
                await Promise.resolve(onAction(prompt));
              }
              setPromptMode(null);
            } finally {
              setPromptSubmitting(false);
            }
          }}
        />
      )}
      <div className="report-wrap">
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          {loading && (
            <div className="report-progress" role="status" aria-live="polite">
              <div className="report-progress-head">
                <span className="report-progress-kicker">
                  <span className="dots" aria-hidden="true"><i/><i/><i/></span>
                  {REPORT_TIER_OPTIONS.find(o => o.value === selectedTier)?.label ?? 'Report'} report in progress
                </span>
                {typeof msg.reportProgressPercent === 'number' ? <span className="report-progress-pct">{Math.max(0, Math.min(100, msg.reportProgressPercent))}%</span> : null}
              </div>
              {/* The stage text is the backend's own progress message — never simulated. */}
              <div className="report-progress-stage">{msg.reportProgress || 'Generating report…'}</div>
              {typeof msg.reportProgressPercent === 'number' && (
                <div className="report-progress-track" aria-label="Report generation progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.max(0, Math.min(100, msg.reportProgressPercent))}>
                  <div className="report-progress-fill" style={{ width:`${Math.max(2, Math.min(100, msg.reportProgressPercent))}%` }}/>
                </div>
              )}
            </div>
          )}
          {loading && !!msg.reportPreview?.trim() && (
            <div className="report-preview-panel">
              <div className="report-preview-head">
                <span className="report-preview-label">Working draft</span>
                <span>Evidence, citation and PDF checks are still running{msg.reportPreviewTruncated ? ' · opening sections shown' : ''}</span>
              </div>
              <div className="report-content report-preview-body" dangerouslySetInnerHTML={{ __html: renderMd(normalizeReportMarkdownSource(msg.reportPreview.slice(0, 32000))) }}/>
            </div>
          )}
          {!loading && !done && !!error && eligible && hasPriorContext && (
            <label className="report-context-toggle" title="When on, earlier questions and answers in this chat are included as background for the report">
              <input
                type="checkbox"
                checked={includeContext}
                onChange={(e) => setIncludeContext(e.target.checked)}
              />
              <span className="report-context-track"><span className="report-context-thumb"/></span>
              <span className="report-context-label">Include previous context</span>
            </label>
          )}
          {!loading && !done && error && (
            <div className="report-alert report-alert--error" role="alert">
              <span className="report-alert-label">Report not completed</span>
              <span>{error}</span>
            </div>
          )}
          {artifactError && (
            <div className="report-alert report-alert--error" role="alert">
              <span className="report-alert-label">PDF export</span>
              <span>{artifactError}</span>
            </div>
          )}
          {!loading && !done && !!error && eligible && (
            <div className="report-tier-status" aria-label="Selected report depth">
              <span className="report-tier-status-label">Report depth</span>
              <strong>{REPORT_TIER_OPTIONS.find(o => o.value === selectedTier)?.label ?? 'In-Detail'}</strong>
              <span>{REPORT_TIER_OPTIONS.find(o => o.value === selectedTier)?.scope ?? 'Expanded analysis'}</span>
            </div>
          )}
          {!loading && !done && !!error && eligible && (
            <button className="report-btn" onClick={() => onGenerate(hasPriorContext ? includeContext : false, selectedTier)}>
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="12" y1="11" x2="12" y2="17"/><line x1="9" y1="14" x2="15" y2="14"/></svg>
              Retry Report
            </button>
          )}
          {done && isDraftReport(rd) && (
            <div role="status" className="report-status-banner report-status-banner--draft">
              <span className="report-status-label">Draft · not publication-approved</span>
              <strong>Draft — not approved for publication.</strong>{' '}
              The report was generated, but the {reportStageLabel(rd?.failedStage) || 'publication audit'} found unresolved issues. You can review it and download a draft PDF; resolve the findings before sharing it externally.
              {!!rd?.failureSummary?.length && (
                <ul className="report-status-findings">
                  {rd.failureSummary.slice(0, 4).map((item, i) => (
                    <li key={i}>
                      {item.issue}
                      {item.claim ? <> — <em>“{item.claim.slice(0, 140)}{item.claim.length > 140 ? '…' : ''}”</em></> : null}
                      {item.sources?.length ? <> ({item.sources.join(', ')})</> : null}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
          {done && (
            <>
              <button className="report-btn" onClick={() => setOpen(o => !o)}>
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>
                {open ? 'Hide report' : 'Show report'}
              </button>
              <span className="report-rev" title={`Report ${rd?.reportId || 'legacy'}; revision ${rd?.revision || 1}`}>
                Rev {rd?.revision || 1}
              </span>
              <button className="report-btn report-btn--experiment report-edit-control" onClick={() => setPromptMode('experiment')} title="Experiment with a new research version without changing this report">
                <span aria-hidden="true">✦</span> Experiment
              </button>
              <button className="report-btn report-btn--action report-edit-control" onClick={() => setPromptMode('action')} title="Edit a selected section of this generated report">
                <span aria-hidden="true">↗</span> Action
              </button>
              {!!rd?.revisionHistory?.length && (
                <button className="report-btn" onClick={() => onRestore(rd.revisionHistory![rd.revisionHistory!.length - 1])} title="Restore the report content saved before the last successful Action">
                  Restore previous
                </button>
              )}
              <button className="report-btn report-btn--primary" onClick={downloadPdf} disabled={pdfLoading} aria-busy={pdfLoading}>
                {pdfLoading
                  ? <><span className="dots" style={{marginRight:4}}><i/><i/><i/></span>Building PDF…</>
                  : <><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>{isDraftReport(rd) ? 'Download draft PDF' : 'Download PDF'}</>}
              </button>
            </>
          )}
        </div>
        {open && done && (
          <div className={`report-body report-body--${String(getReportPresentation(rd)?.domain || 'generic').toLowerCase().replace(/[^a-z0-9_-]+/g, '_')}`}>
            {/* v93 report masthead: everything shown is read from the generated report payload. */}
            <header className="report-masthead">
              <div className="report-masthead-kicker">
                Growth Gradual · {REPORT_TIER_OPTIONS.find(o => o.value === (rd.reportTier ?? selectedTier))?.label ?? 'Research'} report
                {isDraftReport(rd) ? <span className="report-masthead-flag">Draft</span> : null}
              </div>
              {rd.title ? <h1 className="report-masthead-title">{rd.title}</h1> : null}
              {rd.summary?.trim() ? <p className="report-masthead-summary">{rd.summary}</p> : null}
              <dl className="report-masthead-meta">
                <div><dt>Revision</dt><dd>{rd.revision || 1}</dd></div>
                <div><dt>Charts</dt><dd>{rd.charts?.length ?? 0}</dd></div>
                <div><dt>Sources</dt><dd>{rd.sources?.length ?? 0}</dd></div>
                <div><dt>Prepared</dt><dd>{fmtDate(msg.ts)}</dd></div>
              </dl>
              {(() => {
                const outline = reportOutline(normalizeReportMarkdownSource(rd.report)).filter((h) => h !== rd.title);
                if (outline.length < 2) return null;
                return (
                  <nav className="report-outline" aria-label="In this report">
                    <span className="report-outline-label">In this report</span>
                    <ol>
                      {outline.map((h, i) => (
                        <li key={i}>
                          <button type="button" onClick={() => document.getElementById(reportSectionDomId(msg.id, h))?.scrollIntoView({ behavior: 'smooth', block: 'start' })}>
                            <span className="report-outline-num">{String(i + 1).padStart(2, '0')}</span>{h}
                          </button>
                        </li>
                      ))}
                    </ol>
                  </nav>
                );
              })()}
            </header>
            <div className="report-content">
              {(() => {
                const presentation = getReportPresentation(rd);
                const hasMetricSection = !!presentation?.sections?.some((section) =>
                  section.section_type === 'metrics_dashboard' ||
                  (section.blocks ?? []).some((b) => b?.kind === 'metrics')
                );
                const metricPlacement = presentation?.sections?.find((section) => section.section_type === 'metrics_dashboard')?.order;
                const metricClass = [
                  'key-stats-row',
                  hasMetricSection ? 'key-stats--planned' : 'key-stats--fallback',
                ].join(' ');

                const metrics = (rd.keyStats ?? []).slice(0, hasMetricSection || !presentation ? 12 : 6);
                const renderStats = metrics.length > 0 && (hasMetricSection || !presentation);

                const renderRichText = (rawText: string, prefix: string) => {
                  if (!rawText.trim()) return null;
                  const normalizedRawText = normalizeReportMarkdownSource(rawText);
                  // Always consume visual placeholders, even if the matching
                  // chart/image array is empty. Otherwise a dangling [CHART_n]
                  // from a legacy/saved report leaks directly into Show Report.
                  const hasVisualPlaceholder = /\[(?:CHART|WEB_IMG)_(\d+)\]/i.test(normalizedRawText);
                  const parts = hasVisualPlaceholder
                    ? normalizedRawText.split(/\[(CHART|WEB_IMG)_(\d+)\]/gi)
                    : [normalizedRawText];
                  const elements: React.ReactNode[] = [];
                  for (let i = 0; i < parts.length; i += 3) {
                    const text = parts[i];
                    if (text && text.trim()) elements.push(<div key={`${prefix}-t-${i}`} dangerouslySetInnerHTML={{ __html: renderMd(text) }} />);
                    const kind = parts[i + 1];
                    const num = parts[i + 2];
                    if (kind && num !== undefined) {
                      const n = parseInt(num, 10) - 1;
                      if (kind.toUpperCase() === 'CHART') {
                        const spec = rd.charts[n];
                        if (spec) elements.push(<div key={`${prefix}-c-${i}`} className="inline-report-chart"><ChartBlock spec={spec}/></div>);
                      } else {
                        const img = rd.images?.[n];
                        if (img) elements.push(
                          <figure key={`${prefix}-img-${i}`} className="inline-report-image">
                            <img src={proxyImg(img.url)} alt={img.caption ?? ''} loading="lazy" />
                            {img.caption ? <figcaption>{img.caption}</figcaption> : null}
                          </figure>
                        );
                      }
                    }
                  }
                  return <>{elements}</>;
                };

                const normalizedReport = recoverChartTitlePlaceholders(
                  normalizeReportMarkdownSource(rd.report), rd.charts,
                );
                const split = normalizedReport.split(/^(#{1,3})\s+(.+)$/gm);
                const sectionNodes: React.ReactNode[] = [];
                if (split.length === 1) {
                  sectionNodes.push(<div key="report-prose" className={presentationClass(null, presentation)}>{renderRichText(normalizedReport, 'report')}</div>);
                } else {
                  // split() yields preamble, marker, heading, body ...; build
                  // one renderer-controlled shell per actual report heading.
                  if (split[0]?.trim()) sectionNodes.push(<div key="report-preamble" className={presentationClass(null, presentation)}>{renderRichText(split[0], 'pre')}</div>);
                  for (let i = 1; i + 2 < split.length; i += 3) {
                    const level = split[i];
                    const heading = split[i + 1] ?? '';
                    const body = split[i + 2] ?? '';
                    const section = presentationSectionFor(heading, presentation);
                    const shellClass = `${presentationClass(section, presentation)} report-heading-level-${level.length}`;
                    sectionNodes.push(
                      <section key={`report-section-${i}`} id={reportSectionDomId(msg.id, heading.replace(/[*_`]/g, '').trim())} className={shellClass}>
                        {renderRichText(`${'#'.repeat(level.length)} ${heading}\n\n${body}`, `sec-${i}`)}
                      </section>
                    );
                  }
                }

                // Preserve any chart/image that the writer forgot to place in
                // the markdown. They are appended once, after the planned
                // sections, rather than being forced into a fixed location.
                const mentionedCharts = new Set(Array.from(normalizedReport.matchAll(/\[CHART_(\d+)\]/gi)).map((m) => Number(m[1]) - 1));
                const mentionedImages = new Set(Array.from(normalizedReport.matchAll(/\[WEB_IMG_(\d+)\]/gi)).map((m) => Number(m[1]) - 1));
                rd.charts.forEach((spec, i) => {
                  if (!mentionedCharts.has(i)) sectionNodes.push(<div key={`fallback-chart-${i}`} className="inline-report-chart report-unplanned-visual"><ChartBlock spec={spec}/></div>);
                });
                (rd.images ?? []).forEach((img, i) => {
                  if (!mentionedImages.has(i)) sectionNodes.push(
                    <figure key={`fallback-image-${i}`} className="inline-report-image report-unplanned-visual">
                      <img src={proxyImg(img.url)} alt={img.caption ?? ''} loading="lazy" />
                      {img.caption ? <figcaption>{img.caption}</figcaption> : null}
                    </figure>
                  );
                });

                if (renderStats) {
                  const statsNode = (
                    <div key="presentation-metrics" className={metricClass} data-metric-order={metricPlacement ?? ''}>
                      {metrics.map((s, i) => (
                        <div key={i} className="key-stat-card">
                          <div className="key-stat-label">{s.label}</div>
                          <div className="key-stat-value">{(() => {
                            const m = splitMetricValue(s.value);
                            return m ? <>{m.prefix ? <span className="key-stat-prefix">{m.prefix}</span> : null}<span className="key-stat-number">{m.number}</span>{m.unit ? <span className="key-stat-unit">{m.unit}</span> : null}</> : s.value;
                          })()}</div>
                          {s.change && <div className={`key-stat-change ${s.change.startsWith('+') ? 'pos' : s.change.startsWith('-') ? 'neg' : ''}`}>{s.change}</div>}
                        </div>
                      ))}
                    </div>
                  );
                  const insertAt = typeof metricPlacement === 'number' ? Math.max(0, Math.min(metricPlacement, sectionNodes.length)) : 0;
                  sectionNodes.splice(insertAt, 0, statsNode);
                }
                return <>{sectionNodes}</>;
              })()}
              {(rd.sources?.length ?? 0) > 0 && (
                <section className="report-sources" aria-label="Complete data sources">
                  <h2>Complete Data Sources <span>{rd.sources?.length}</span></h2>
                  <p>Includes sources retrieved or supplied. The PDF appendix lists the sources cited in the report body.</p>
                  <ol>
                    {rd.sources?.map((source, index) => (
                      <li key={`${source.url || source.title}-${index}`}>
                        <div className="report-source-title">{source.title}</div>
                        <div className="report-source-meta">{source.publisher || source.kind || 'Source'}{source.kind && source.publisher ? ` · ${source.kind}` : ''}</div>
                        {source.url
                          ? <a href={source.url} target="_blank" rel="noopener noreferrer">Open source ↗</a>
                          : <span className="report-source-provided">Provided in report data</span>}
                      </li>
                    ))}
                  </ol>
                </section>
              )}
            </div>
          </div>
        )}
      </div>
    </>
  );
}


// ─── Stream ───────────────────────────────────────────────────────────────────
// ─── File attachment types ─────────────────────────────────────────────────────
interface AttachedFile {
  id: string;
  name: string;
  size: number;
  type: string;
  content: string; // base64 data URL or raw text
  extractedText?: string;
  status: 'attaching' | 'attached' | 'failed';
  error?: string;
}
interface PastedText {
  id: string;
  label: string;
  text: string;
}
const ACCEPTED_TYPES = [
  'application/pdf',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  'application/msword',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', // .xlsx
  'application/vnd.ms-excel', // .xls
  'text/plain', 'text/csv', 'text/markdown',
  'image/jpeg', 'image/png', 'image/webp', 'image/gif',
];
// Browsers are inconsistent about the MIME type they report for .xlsx/.xls
// (some send 'application/octet-stream' or ''), so spreadsheet files are
// also recognised by extension as a fallback — see isSpreadsheetFile below.
function isSpreadsheetFile(f: { name: string; type: string }): boolean {
  return (
    f.type === 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' ||
    f.type === 'application/vnd.ms-excel' ||
    /\.(xlsx|xls)$/i.test(f.name)
  );
}
const MAX_ATTACH = 10;
function wordCount(s: string) { return s.trim().split(/\s+/).filter(Boolean).length; }
function readAsDataURL(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader(); r.onload = () => resolve(r.result as string);
    r.onerror = () => reject(new Error('Read failed')); r.readAsDataURL(file);
  });
}
function readAsText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader(); r.onload = () => resolve(r.result as string);
    r.onerror = () => reject(new Error('Read failed')); r.readAsText(file);
  });
}

async function extractPdfText(file: File): Promise<string> {
  try {
    // Load PDF.js only when a PDF is actually processed; keep it out of the
    // main Next.js/Turbopack client dependency graph to avoid large compile peaks.
    const pdfjsLib = await loadPdfJs();
    const dataUrl = await readAsDataURL(file);
    const base64  = dataUrl.split(',')[1];
    const binary  = atob(base64);
    const bytes   = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    const pdf       = await pdfjsLib.getDocument({ data: bytes }).promise;
    const numPages  = Math.min(pdf.numPages, 30);
    const textParts: string[] = [];
    for (let p = 1; p <= numPages; p++) {
      const page    = await pdf.getPage(p);
      const content = await page.getTextContent();
      const pageText = content.items
        .map((item: unknown) => (item as { str?: string }).str ?? '')
        .join(' ');
      if (pageText.trim()) textParts.push(`[Page ${p}]\n${pageText.trim()}`);
    }
    return textParts.join('\n\n');
  } catch (e) {
    console.warn('[processFile] pdf.js extraction failed:', e);
    return '';
  }
}

/** Parses an uploaded .xlsx/.xls workbook into plain text: one block per
 *  sheet, each row rendered as CSV. This is what feeds `extractedText`,
 *  which in turn becomes the report generator's PRIMARY SOURCE file content
 *  (see buildFilePayload / fileContext on the backend) — so multi-sheet
 *  data like fund/scheme performance tables survives intact rather than
 *  being dropped because the file wasn't a PDF or plain text.
 *  Fully blank rows are skipped; very large workbooks are capped per-sheet
 *  and in total so a single upload can't blow out the prompt budget. */
async function extractXlsxText(file: File): Promise<string> {
  try {
    const XLSX = await import('xlsx');
    const dataUrl = await readAsDataURL(file);
    const base64  = dataUrl.split(',')[1];
    const binary  = atob(base64);
    const bytes   = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    const wb = XLSX.read(bytes, { type: 'array' });

    const MAX_SHEETS = 40;
    const MAX_ROWS_PER_SHEET = 400;
    const MAX_TOTAL_CHARS = 150_000; // generous — Gemini's context handles this easily;
                                      // the Groq path auto-skips to Gemini for large prompts

    const parts: string[] = [];
    let totalChars = 0;
    for (const sheetName of wb.SheetNames.slice(0, MAX_SHEETS)) {
      if (totalChars >= MAX_TOTAL_CHARS) break;
      const ws = wb.Sheets[sheetName];
      const csv = XLSX.utils.sheet_to_csv(ws, { blankrows: false });
      const rows = csv.split('\n').filter(r => r.trim().length > 0).slice(0, MAX_ROWS_PER_SHEET);
      if (!rows.length) continue;
      const block = `[Sheet: ${sheetName}]\n${rows.join('\n')}`;
      totalChars += block.length;
      parts.push(block);
    }
    return parts.join('\n\n').slice(0, MAX_TOTAL_CHARS);
  } catch (e) {
    console.warn('[processFile] xlsx extraction failed:', e);
    return '';
  }
}

// Stable content hash — SHA-256 of the extracted text, so re-uploading the
// exact same file (same content) always yields the same doc ID, letting the
// backend's `sess.has_doc(doc_id)` dedup actually skip re-embedding and
// skip rewriting bundle.md, instead of always looking "new".
async function hashText(text: string): Promise<string> {
  try {
    const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
    return Array.from(new Uint8Array(buf)).map(b => b.toString(16).padStart(2, '0')).join('').slice(0, 32);
  } catch {
    // Fallback for environments without SubtleCrypto (non-HTTPS, old browsers):
    // a cheap rolling hash — not cryptographic, but stable for identical input.
    let h = 0;
    for (let i = 0; i < text.length; i++) { h = (Math.imul(31, h) + text.charCodeAt(i)) | 0; }
    return 'fnv' + (h >>> 0).toString(16);
  }
}

async function processFile(file: File): Promise<AttachedFile | null> {
  const accepted = ACCEPTED_TYPES.includes(file.type) || file.type.startsWith('image/') || file.type.startsWith('text/') || isSpreadsheetFile(file);
  if (!accepted) return null;
  const id = Math.random().toString(36).slice(2, 10);
  // Return immediately with 'attaching' status
  const base: AttachedFile = { id, name: file.name, size: file.size, type: file.type, content: '', status: 'attaching' };
  try {
    let content = '', extractedText = '';
    if (isSpreadsheetFile(file)) {
      content       = await readAsDataURL(file);
      extractedText = await extractXlsxText(file);
    } else if (file.type.startsWith('text/')) {
      extractedText = await readAsText(file);
      content = extractedText;
    } else if (file.type === 'application/pdf') {
      content       = await readAsDataURL(file);
      extractedText = await extractPdfText(file);
    } else {
      content = await readAsDataURL(file);
    }
    return { ...base, content, extractedText, status: 'attached' };
  } catch (e) {
    return { ...base, status: 'failed', error: (e as Error).message };
  }
}
function buildAttachmentContext(files: AttachedFile[], pasted: PastedText[]): string {
  const parts: string[] = [];
  files.forEach((f, i) => {
    if (f.extractedText) {
      parts.push(`[Attached File ${i+1}: ${f.name}]\n${f.extractedText.slice(0, 8000)}`);
    } else if (f.type.startsWith('image/')) {
      parts.push(`[Attached Image ${i+1}: ${f.name}]`);
    } else {
      parts.push(`[Attached File ${i+1}: ${f.name} (${f.type})]`);
    }
  });
  pasted.forEach((p, i) => {
    parts.push(`[Pasted Context ${i+1}: ${p.label}]\n${p.text.slice(0, 6000)}`);
  });
  if (!parts.length) return '';
  return `\n\n---\n📎 USER-ATTACHED CONTEXT:\n${parts.join('\n\n')}\n---\n\nUse the attached content above when answering.`;
}

interface StreamMeta { type:'meta'; searchPerformed:boolean; resultCount:number; queryType:string; sources:Source[]; kbCompany?: KbCompany | null; }

/** Current Supabase access token, if logged in — attached so the backend can
 *  tie this session's chat history to the user's account. */
async function authHeader(): Promise<Record<string, string>> {
  try {
    const { data } = await supabase.auth.getSession();
    const token = data.session?.access_token;
    return token ? { Authorization: `Bearer ${token}` } : {};
  } catch {
    return {};
  }
}

// ─── Suggestions ──────────────────────────────────────────────────────────────
const SUGGESTIONS = [
  { icon:'📈', label:'Nifty outlook today' },
  { icon:'🏦', label:'Top banking stocks' },
  { icon:'💰', label:'Best SIPs right now' },
  { icon:'🔍', label:'Analyse HDFC Bank' },
  { icon:'📰', label:'Latest market news' },
  { icon:'🌐', label:'RBI rate cut impact' },
  { icon:'🚀', label:'Upcoming IPOs' },
  { icon:'📊', label:'Sector performance' },
];

// ─── Attach-complete notification ─────────────────────────────────────────────
// Fires a native browser notification only when the tab is backgrounded, so
// someone who tabs away while a large file indexes still finds out it's done.
// Silently no-ops if the browser doesn't support Notification or permission
// hasn't been granted — never prompts on its own, only after an explicit
// user gesture (first file attach) requests it once.
let _notifyPermissionAsked = false;
function notifyFileReady(title: string, body: string) {
  if (typeof window === 'undefined' || !('Notification' in window)) return;
  if (document.visibilityState === 'visible') return; // user is already looking at the chip
  if (Notification.permission === 'granted') {
    new Notification(title, { body, icon: '/favicon.ico', tag: 'gg-file-attach' });
  }
}
function ensureNotifyPermission() {
  if (typeof window === 'undefined' || !('Notification' in window)) return;
  if (_notifyPermissionAsked || Notification.permission !== 'default') return;
  _notifyPermissionAsked = true;
  Notification.requestPermission().catch(() => {});
}

// ─── Main Component ───────────────────────────────────────────────────────────
export default function GrowthGradualChat() {
  // Mobile detection
  const [isMobile, setIsMobile] = useState(false);
  useEffect(() => {
    const check = () => setIsMobile(window.innerWidth < 768);
    check();
    window.addEventListener('resize', check);
    return () => window.removeEventListener('resize', check);
  }, []);

  // Conversations (sidebar history)
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId]           = useState<string | null>(null);
  const [messages, setMessages]           = useState<Message[]>([]);
  const [sidebarOpen, setSidebarOpen]     = useState(true);

  // Close sidebar by default on mobile
  useEffect(() => {
    if (isMobile) setSidebarOpen(false);
    else setSidebarOpen(true);
  }, [isMobile]);

  // Welcome mode: null = home, 'chat' = ask anything, 'attach' = file mode, 'news' = market news

  // Chat state
  const [input, setInput]       = useState('');
  const [streaming, setStreaming] = useState(false);
  // The SDK delivers real text deltas. Keep the accumulated target separate from
  // the visible value so large/fast deltas can be eased onto the screen rather than
  // appearing as one sudden block. Refs let the animation consume new deltas without
  // restarting its timer on every provider chunk.
  const [streamDisplayedText, setStreamDisplayedText] = useState('');
  const [streamVisualMessageId, setStreamVisualMessageId] = useState<string | null>(null);
  const [selectedReportTier, setSelectedReportTier] = useState<ReportTier>('brief');
  const [selectedReportStyle, setSelectedReportStyle] = useState<ReportStyle>('market_currents');
  // generateReport reads the style through a ref so its many call sites stay unchanged.
  const selectedReportStyleRef = useRef<ReportStyle>('market_currents');
  selectedReportStyleRef.current = selectedReportStyle;
  const [reportModeEnabled, setReportModeEnabled] = useState(false);
  const [reportTierMenuOpen, setReportTierMenuOpen] = useState(false);
  const reportTierHydratedRef = useRef(false);
  const [searching, setSearching] = useState(false);
  const [statusMsg, setStatusMsg] = useState('');
  const [dragOver, setDragOver]   = useState(false);

  // File attachment state
  const [attachedFiles, setAttachedFiles] = useState<AttachedFile[]>([]);
  const [pastedTexts, setPastedTexts]     = useState<PastedText[]>([]);
  const [attachLoading, setAttachLoading] = useState(false);
  const [ragIndexed, setRagIndexed]       = useState(false);
  const [ragIndexing, setRagIndexing]     = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  // Full-width workspace: measure where the shell starts (below the navbar and
  // ticker, whose heights differ by breakpoint) so it fills the rest of the
  // viewport exactly, with no page scroll.
  const chatShellRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const el = chatShellRef.current;
    if (!el || typeof window === 'undefined') return;
    const update = () => {
      const top = Math.max(0, Math.round(el.getBoundingClientRect().top + window.scrollY));
      el.style.setProperty('--gg-chat-top', `${top}px`);
      // On phones a fixed bottom tab bar covers the composer; reserve its height.
      const bottomNav = document.querySelector('.mobile-bottom-nav');
      const navHeight = bottomNav && getComputedStyle(bottomNav).display !== 'none'
        ? Math.round(bottomNav.getBoundingClientRect().height) : 0;
      el.style.setProperty('--gg-chat-bottom', `${navHeight}px`);
    };
    update();
    window.addEventListener('resize', update);
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(update) : null;
    if (ro && el.parentElement) ro.observe(document.body);
    return () => { window.removeEventListener('resize', update); ro?.disconnect(); };
  }, []);

  const bottomRef      = useRef<HTMLDivElement>(null);
  const shouldAutoScrollRef = useRef(true);
  const inputRef       = useRef<HTMLTextAreaElement>(null);
  const reportTierMenuRef = useRef<HTMLDivElement>(null);
  const abortRef       = useRef<AbortController | null>(null);
  // Message ids whose report job is currently in flight (duplicate-submission guard).
  const reportInFlightRef = useRef<Set<string>>(new Set());
  const historyRef     = useRef<{ role:string; content:string }[]>([]);
  const sdkResponseTargetRef = useRef<string | null>(null);
  const sdkExpectedMessagesRef = useRef(0);
  const streamTargetTextRef = useRef('');
  // "<message id>::<text>" last mirrored from the SDK stream into messages.
  const sdkMirroredRef = useRef('');
  const streamDisplayedTextRef = useRef('');
  const streamVisualMessageIdRef = useRef<string | null>(null);
  const sdkCompletionRef = useRef<{ resolve: (text: string) => void; reject: (error: unknown) => void } | null>(null);
  const initializedRef = useRef(false); // guard against React Strict Mode double-mount

  // Normal chat uses the official Vercel AI SDK hook. Reports continue to use
  // the existing report-job flow because it returns a rich structured report.
  const {
    messages: sdkMessages,
    sendMessage: sdkSendMessage,
    setMessages: sdkSetMessages,
    stop: sdkStop,
  } = useChat({
    transport: growthGradualAiTransport,
    // Batch streamed chunks. Unthrottled, every token republished messages and
    // re-ran the mirror + history effects back to back, which React reports as
    // "Maximum update depth exceeded" on fast streams (AI SDK's documented fix).
    throttle: 50,
    onData: (part) => {
      const streamPart = part as unknown as { type?: string; data?: unknown };
      if (streamPart.type === 'data-chat-status' && streamPart.data && typeof streamPart.data === 'object') {
        const status = String((streamPart.data as { message?: unknown }).message || '').trim();
        if (status) setStatusMsg(status);
        return;
      }
      if (streamPart.type !== 'data-chat-meta' && streamPart.type !== 'chat-meta') return;
      if (!streamPart.data || typeof streamPart.data !== 'object') return;
      const meta = streamPart.data as StreamMeta;
      const targetId = sdkResponseTargetRef.current;
      setSearching(false);
      setStatusMsg(meta.searchPerformed
        ? `Web search · ${meta.sources?.length ?? 0} sources found — generating…`
        : 'Analysing…');
      if (meta.searchPerformed) {
        console.log(
          `[Search] Web search · ${meta.sources?.length ?? 0} sources · type=${meta.queryType}`,
          meta.sources?.map((source) => source.url) ?? [],
        );
      }
      if (targetId) {
        setMessages(prev => prev.map(message => message.id === targetId
          ? { ...message, searchPerformed: meta.searchPerformed, sources: meta.sources, queryType: meta.queryType, kbCompany: meta.kbCompany }
          : message));
      }
    },
    onFinish: ({ message }) => {
      const text = message.parts
        .map(part => part.type === 'text' ? part.text : '')
        .join('');
      // Make sure the final delta is available to the display smoother before
      // the send promise resolves and the request's loading state is cleared.
      streamTargetTextRef.current = text;
      sdkCompletionRef.current?.resolve(text);
    },
    onError: (error) => {
      sdkCompletionRef.current?.reject(error);
    },
  });
  const messagesRef = useRef<Message[]>([]);
  const activeIdRef = useRef<string | null>(null);
  const conversationsRef = useRef<Conversation[]>([]);
  const hydratedHistoryRef = useRef(false);

  useEffect(() => { messagesRef.current = messages; }, [messages]);
  useEffect(() => { activeIdRef.current = activeId; }, [activeId]);
  useEffect(() => { conversationsRef.current = conversations; }, [conversations]);

  // Persist the selected report depth so it behaves like an AI chat/model
  // picker: the user's choice stays active until they change it.
  useEffect(() => {
    try {
      const saved = window.localStorage.getItem('gg-report-tier-v2');
      if (saved === 'brief' || saved === 'detailed' || saved === 'in_depth') {
        setSelectedReportTier(saved);
      }
      const savedStyle = window.localStorage.getItem('gg-report-style-v1');
      if (savedStyle === 'market_currents' || savedStyle === 'feature_article') {
        setSelectedReportStyle(savedStyle);
      }
    } catch { /* localStorage may be unavailable in privacy-restricted browsers */ }
    reportTierHydratedRef.current = true;
  }, []);

  useEffect(() => {
    if (!reportTierHydratedRef.current) return;
    try {
      window.localStorage.setItem('gg-report-tier-v2', selectedReportTier);
    } catch { /* ignore storage failures */ }
  }, [selectedReportTier]);

  useEffect(() => {
    if (!reportTierHydratedRef.current) return;
    try {
      window.localStorage.setItem('gg-report-style-v1', selectedReportStyle);
    } catch { /* ignore storage failures */ }
  }, [selectedReportStyle]);

  // Close the compact report-depth menu when clicking anywhere outside it.
  useEffect(() => {
    if (!reportTierMenuOpen) return;
    const handlePointerDown = (event: MouseEvent) => {
      const target = event.target as Node | null;
      if (target && reportTierMenuRef.current && !reportTierMenuRef.current.contains(target)) {
        setReportTierMenuOpen(false);
      }
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setReportTierMenuOpen(false);
    };
    document.addEventListener('mousedown', handlePointerDown);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('mousedown', handlePointerDown);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [reportTierMenuOpen]);

  // ── File processing helper ─────────────────────────────────────────────────
  const addFiles = useCallback(async (fileList: File[]) => {
    const remaining = MAX_ATTACH - attachedFiles.length;
    if (remaining <= 0) return;
    const toProcess = fileList.slice(0, remaining);
    setAttachLoading(true);
    ensureNotifyPermission();

    // Add placeholder chips immediately with 'attaching' status
    const placeholders: AttachedFile[] = toProcess.map(f => ({
      id: Math.random().toString(36).slice(2, 10),
      name: f.name, size: f.size, type: f.type,
      content: '', status: 'attaching' as const,
    }));
    setAttachedFiles(prev => [...prev, ...placeholders]);

    // Process each file (extract text) and update chip as it completes
    const results = await Promise.all(toProcess.map(async (file, idx) => {
      const placeholder = placeholders[idx];
      try {
        const result = await processFile(file);
        setAttachedFiles(prev => prev.map(f =>
          f.id === placeholder.id
            ? (result ? { ...result, id: placeholder.id } : { ...placeholder, status: 'failed' as const, error: 'Unsupported file type' })
            : f
        ));
        return result ? { ...result, id: placeholder.id } : null;
      } catch (e) {
        setAttachedFiles(prev => prev.map(f =>
          f.id === placeholder.id
            ? { ...placeholder, status: 'failed' as const, error: (e as Error).message }
            : f
        ));
        return null;
      }
    }));

    setAttachLoading(false);

    {
      const ok = results.filter(r => r !== null).length;
      const failed = results.length - ok;
      if (results.length === 1) {
        notifyFileReady(
          ok ? 'File attached' : 'Attach failed',
          ok ? `${toProcess[0].name} is ready to use.` : `${toProcess[0].name} couldn't be read.`
        );
      } else if (results.length > 1) {
        notifyFileReady(
          'Files attached',
          failed > 0 ? `${ok} attached, ${failed} failed.` : `${ok} files are ready to use.`
        );
      }
    }

    const sessionId = getOrCreateSessionId();
    setRagIndexing(true);

    // ── Index attachments directly into rag-service ──
    // Text is already extracted in the browser by processFile(); rag-service
    // indexes it for chat and report retrieval. (The old background upload to the
    // Paperly gateway was removed — those services are no longer deployed.)
    try {
      const toIndex = results.filter((f): f is AttachedFile => f !== null && !!f.extractedText);

      if (toIndex.length > 0) {
        const docs = await Promise.all(toIndex.map(async f => ({
          id: await hashText(f.extractedText!),
          name: f.name,
          text: f.extractedText!,
          source_type: 'file',
          file_type: f.type,
        })));

        // ── Index with one automatic retry (handles HF Space cold starts) ──────
        // HF Spaces sleep after ~15 min idle. Cold start = 60-90s. If the first
        // attempt returns 0 chunks (timeout or cold start), we wait 8s and retry
        // once. This covers the most common reason files are silently ignored.
        const tryIndex = async (): Promise<{ chunks_added?: number; total_chunks?: number; error?: string }> => {
          const r = await fetch('/api/rag/index', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: sessionId, documents: docs }),
          });
          return r.json().catch(() => ({}));
        };

        console.log('[RAG] Indexing', docs.length, 'doc(s) to HF Space...');
        let d = await tryIndex();

        if ((d.chunks_added ?? 0) === 0 && (d.total_chunks ?? 0) === 0) {
          // First attempt got 0 chunks — HF Space may have been cold. Wait and retry.
          console.warn('[RAG] 0 chunks on first attempt — HF Space cold start? Retrying in 8s...');
          setStatusMsg('File server waking up, retrying…');
          await new Promise(res => setTimeout(res, 8_000));
          d = await tryIndex();
        }

        if ((d.chunks_added ?? 0) > 0 || (d.total_chunks ?? 0) > 0) {
          setRagIndexed(true);
          console.log('[RAG] Indexed', d.chunks_added, 'chunks ✅');
        } else {
          // Both attempts failed — tell the user clearly instead of silent failure
          console.error('[RAG] Index failed after retry:', d);
          setAttachedFiles(prev => prev.map(f => ({
            ...f,
            status: 'failed' as const,
            error: 'File server unavailable — your file was attached but AI may not read it. Try re-uploading.',
          })));
        }
      } else {
        console.warn('[RAG] No extracted text available to index');
      }

    } catch (e) {
      console.warn('[RAG] Direct index failed:', e);
      setAttachedFiles(prev => prev.map(f => ({
        ...f,
        status: f.status === 'attaching' ? 'failed' as const : f.status,
        error: f.status === 'attaching' ? 'Upload failed — please try again.' : f.error,
      })));
    } finally {
      setRagIndexing(false);
      setStatusMsg('');
      if (results.some(r => r !== null && !!r.extractedText)) {
        notifyFileReady('Ready to search', 'Your file finished indexing — ask away.');
      }
    }
  }, [attachedFiles.length]);

  // ── Drag-and-drop ─────────────────────────────────────────────────────────
  const handleDragOver  = (e: React.DragEvent) => { e.preventDefault(); setDragOver(true); };
  const handleDragLeave = (e: React.DragEvent) => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setDragOver(false); };
  const handleAreaDrop  = async (e: React.DragEvent) => {
    e.preventDefault(); setDragOver(false);
    const files = Array.from(e.dataTransfer.files);
    if (files.length) await addFiles(files);
  };

  // ── Paste handler ─────────────────────────────────────────────────────────
  // Pasted FILES (images, copied file objects from a file manager, etc.) still
  // become attachments — that's unambiguous. Pasted TEXT, no matter how long,
  // is left alone and allowed to land in the textarea like any normal typed/
  // pasted text: a multi-paragraph prompt shouldn't silently turn into an
  // opaque "Text 1" chip the person then has to click to verify.
  const handlePaste = useCallback(async (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const items = Array.from(e.clipboardData?.items ?? []);
    const fileItems = items.filter(it => it.kind === 'file');
    if (fileItems.length > 0) {
      e.preventDefault();
      const files = fileItems.map(it => it.getAsFile()).filter((f): f is File => f !== null);
      if (files.length) { await addFiles(files); return; }
    }
    // No preventDefault for plain text — browser handles the paste natively.
  }, [addFiles]);

  // Load conversations from localStorage on mount — guarded against Strict Mode double-invoke.
  useEffect(() => {
    if (initializedRef.current) return;
    initializedRef.current = true;
    const saved = loadConversations();
    setConversations(saved);
    conversationsRef.current = saved;
    hydratedHistoryRef.current = true;
  }, []);

  useEffect(() => {
    messagesRef.current = messages;
    // Keep streaming text anchored in place. Smooth scrolling on every chunk
    // restarts the animation repeatedly and makes the response slide around.
    if (shouldAutoScrollRef.current) {
      const scrollContainer = bottomRef.current?.parentElement;
      if (scrollContainer) scrollContainer.scrollTop = scrollContainer.scrollHeight;
    }
    // Persist the current conversation directly from messages. This is the
    // critical path for the history bug: report messages and async report
    // updates may change messages without changing the conversations array.
    if (!hydratedHistoryRef.current || !messages.length) return;
    const currentId = activeIdRef.current;
    const id = currentId || uid();
    if (!currentId) {
      activeIdRef.current = id;
      setActiveId(id);
    }
    const firstUser = messages.find(m => m.role === 'user');
    const title = firstUser?.text?.trim().slice(0, 80) || 'New conversation';
    const existing = conversationsRef.current;
    const next = existing.some(c => c.id === id)
      ? existing.map(c => c.id === id ? { ...c, title, messages, ts: c.ts || Date.now() } : c)
      : [{ id, title, messages, ts: Date.now() }, ...existing];
    conversationsRef.current = next;
    setConversations(next);
    saveConversations(next);
  }, [messages]);

  // Mirror each streamed SDK text delta into the real message model for
  // persistence, while the active bubble renders a smoothed view of that same
  // text. This preserves citations/history and avoids a separate block per delta.
  useEffect(() => {
    const targetId = sdkResponseTargetRef.current;
    if (!targetId || sdkMessages.length < sdkExpectedMessagesRef.current) return;
    const last = sdkMessages[sdkMessages.length - 1];
    if (!last || last.role !== 'assistant') return;
    const streamedText = last.parts
      .map(part => part.type === 'text' ? part.text : '')
      .join('');
    streamTargetTextRef.current = streamedText;
    // Only act on new text. useChat can re-emit an identical messages array;
    // setting state (even to the same value) on every emission re-renders and
    // re-runs this effect, which looped into "Maximum update depth exceeded".
    const mirrorKey = `${targetId}::${streamedText}`;
    if (sdkMirroredRef.current === mirrorKey) return;
    sdkMirroredRef.current = mirrorKey;
    setMessages(prev => (prev.some(message => message.id === targetId && message.text !== streamedText)
      ? prev.map(message => message.id === targetId ? { ...message, text: streamedText } : message)
      : prev));
    if (streamedText) {
      setSearching(false);
      setStatusMsg('');
    }
  }, [sdkMessages]);

  // Smoothly reveal newly received SDK text. This timer is independent of the
  // incoming chunk cadence: new deltas update a ref, so fast chunks cannot keep
  // cancelling the reveal timer. Large backlogs accelerate to catch up quickly.
  // Reduced-motion users see each incoming target immediately.
  useEffect(() => {
    const messageId = streamVisualMessageId;
    if (!messageId) return;
    let cancelled = false;
    let timer: number | undefined;

    const tick = () => {
      if (cancelled || streamVisualMessageIdRef.current !== messageId) return;
      const target = streamTargetTextRef.current;
      const current = streamDisplayedTextRef.current;
      const remaining = target.length - current.length;
      const reduceMotion = typeof window !== 'undefined'
        && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

      if (remaining > 0) {
        let nextText: string;
        if (reduceMotion) {
          nextText = target;
        } else {
          const delayMs = 24;
          const charsPerSecond = Math.min(520, 135 + remaining * 3.2);
          const step = Math.max(1, Math.min(remaining, Math.ceil(charsPerSecond * delayMs / 1000)));
          nextText = target.slice(0, Math.min(target.length, current.length + step));
        }
        streamDisplayedTextRef.current = nextText;
        setStreamDisplayedText(nextText);
      }

      const isCaughtUp = streamDisplayedTextRef.current.length >= streamTargetTextRef.current.length;
      if (isCaughtUp && !streaming) {
        streamVisualMessageIdRef.current = null;
        setStreamVisualMessageId(null);
        return;
      }
      // Poll slowly while waiting for the next provider delta; reveal at a steady
      // cadence while text is queued. No animation loop runs after completion.
      const hasQueuedText = streamDisplayedTextRef.current.length < streamTargetTextRef.current.length;
      timer = window.setTimeout(tick, hasQueuedText ? 24 : 60);
    };

    tick();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [streamVisualMessageId, streaming]);

  // Save whenever the conversation list changes as a secondary durability path.
  useEffect(() => {
    conversationsRef.current = conversations;
    if (!hydratedHistoryRef.current || !conversations.length) return;
    saveConversations(conversations);
  }, [conversations]);

  // Persist one last snapshot when the tab is being closed/refreshed.
  useEffect(() => {
    const onBeforeUnload = () => {
      const msgs = messagesRef.current;
      if (!msgs.length) return;
      const id = activeIdRef.current || uid();
      const base = conversationsRef.current.filter(c => c.id !== id);
      const firstUser = msgs.find(m => m.role === 'user');
      base.unshift({ id, title: firstUser?.text?.trim().slice(0, 80) || 'Conversation', messages: msgs, ts: Date.now() });
      saveConversations(base);
    };
    window.addEventListener('beforeunload', onBeforeUnload);
    return () => window.removeEventListener('beforeunload', onBeforeUnload);
  }, []);

  // New chat
  const startNewChat = useCallback(() => {
    const msgs = messagesRef.current;
    const currentId = activeIdRef.current;
    if (msgs.length && currentId) {
      const updated = conversationsRef.current.some(c => c.id === currentId)
        ? conversationsRef.current.map(c => c.id === currentId ? { ...c, messages: msgs } : c)
        : [{ id: currentId, title: msgs.find(m => m.role === 'user')?.text?.trim().slice(0, 80) || 'Conversation', messages: msgs, ts: Date.now() }, ...conversationsRef.current];
      conversationsRef.current = updated;
      setConversations(updated);
      saveConversations(updated);
    }
    abortRef.current?.abort();
    sdkStop();
    sdkResponseTargetRef.current = null;
    sdkCompletionRef.current?.reject(new Error('Chat stopped.'));
    sdkCompletionRef.current = null;
    setMessages([]);
    setActiveId(null);
    historyRef.current = [];
    setInput('');
    setReportModeEnabled(false);
    setReportTierMenuOpen(false);
    shouldAutoScrollRef.current = true;
    setSelectedReportTier('brief');
    setAttachedFiles([]);
    setPastedTexts([]);
    setRagIndexed(false);
    setRagIndexing(false);
    setTimeout(() => inputRef.current?.focus(), 100);
  }, [sdkStop]);

  // Load a conversation
  const loadConversation = useCallback((conv: Conversation) => {
    abortRef.current?.abort();
    sdkStop();
    sdkResponseTargetRef.current = null;
    sdkCompletionRef.current?.reject(new Error('Chat switched.'));
    sdkCompletionRef.current = null;
    // Clear any stale reportLoading flags — a persisted loading state has no active
    // fetch behind it, so the spinner would show forever if not cleared here.
    shouldAutoScrollRef.current = true;
    setReportModeEnabled(false);
    setReportTierMenuOpen(false);
    const cleanedMessages = conv.messages.map(m =>
      m.reportLoading ? { ...m, reportLoading: false } : m
    );
    messagesRef.current = cleanedMessages;
    activeIdRef.current = conv.id;
    setMessages(cleanedMessages);
    setActiveId(conv.id);
    historyRef.current = conv.messages.map(m => ({ role: m.role, content: m.text }));
    setTimeout(() => bottomRef.current?.scrollIntoView({ behavior:'smooth' }), 50);
  }, [sdkStop]);

  // Delete a conversation
  const deleteConversation = useCallback((id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setConversations(prev => prev.filter(c => c.id !== id));
    if (activeId === id) startNewChat();
  }, [activeId, startNewChat]);

  // Send message

  /** Sends an Action that edits one or more exact sections of the latest report.
   *  Work runs as a backend job (start + status polling) so provider calls and
   *  live research cannot be cut off by a short browser proxy timeout. The UI
   *  merges the returned report only after the complete Action succeeds. */
  const editReportSection = useCallback((reportMsgId: string, instruction: string, reportData: ReportData) => {
    const userMsg: Message = { id: uid(), role: 'user', text: instruction, ts: Date.now() };
    // reportQuestion carries the edit instruction through as the PDF/title
    // fallback when the report itself has no H1/H2 heading.
    const botMsg:  Message = { id: uid(), role: 'assistant', text: '', ts: Date.now(), reportQuestion: instruction };
    setMessages(prev => [...prev, userMsg, botMsg]);
    setStreaming(true);
    setStatusMsg('Queueing report update…');

    void (async () => {
      try {
        // Queue the edit instead of holding one browser request open while
        // live web research and several provider/validation passes execute.
        const started = await fetch('/api/chat/report/edit/start', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            report: reportData.report,
            reportId: reportData.reportId || reportMsgId,
            revision: reportData.revision || 1,
            reportTier: reportData.reportTier || 'detailed',
            editInstruction: instruction,
            title: reportData.title || '',
            charts: reportData.charts || [],
            images: reportData.images || [],
            sources: reportData.sources || [],
            presentation: reportData.presentation || null,
            sessionId: reportData.sessionId || '',
            hasRag: Boolean(reportData.ragStatus?.requested),
          }),
        });
        const startData: any = await started.json().catch(() => ({}));
        if (!started.ok || !startData.jobId) {
          const message = typeof startData?.error === 'string' && startData.error
            ? startData.error
            : `Couldn't start the report edit (HTTP ${started.status}). Your existing report is unchanged.`;
          setMessages(prev => prev.map(m => m.id === botMsg.id ? { ...m, text: message } : m));
          return;
        }

        const jobId = String(startData.jobId);
        const startedAt = Date.now();
        const maxWaitMs = 20 * 60_000;
        let transientStatusErrors = 0;
        let finalData: any = null;

        while (Date.now() - startedAt < maxWaitMs) {
          await new Promise(resolve => setTimeout(resolve, 1800));
          let statusResponse: Response;
          let statusData: any;
          try {
            statusResponse = await fetch(`/api/chat/report/edit/status/${encodeURIComponent(jobId)}`, { cache: 'no-store' });
            statusData = await statusResponse.json().catch(() => ({}));
          } catch (statusError) {
            transientStatusErrors += 1;
            console.warn('[editReportSection] transient status poll error', statusError);
            if (transientStatusErrors >= 30) {
              setStatusMsg('The Action is still running; reconnecting to its status…');
            }
            continue;
          }

          if (!statusResponse.ok) {
            if ((statusResponse.status >= 500 || statusResponse.status === 429) && transientStatusErrors < 30) {
              transientStatusErrors += 1;
              continue;
            }
            throw new Error((typeof statusData?.error === 'string' && statusData.error)
              || `Could not retrieve Action status (HTTP ${statusResponse.status}). Your displayed report has not been changed.`);
          }
          transientStatusErrors = 0;

          if (statusData.status === 'done') {
            finalData = statusData.result || statusData;
            break;
          }
          if (statusData.status === 'failed') {
            const details = Array.isArray(statusData.details) && statusData.details.length
              ? `\n\nDetails:\n- ${statusData.details.map((x: unknown) => String(x)).join('\n- ')}`
              : '';
            throw new Error(`${statusData.error || 'The Action could not be completed safely.'}${details}`);
          }
          if (statusData.status === 'queued') setStatusMsg('Action queued…');
          else setStatusMsg('Retrieving saved OKF/web evidence and applying validated report edits…');
        }

        if (!finalData) {
          throw new Error('The Action is taking longer than expected. Your displayed report has not been changed. Do not submit the same edit again yet; wait briefly and retry the status check.');
        }

        const updatedReport = typeof finalData.report === 'string' ? finalData.report : '';
        if (!updatedReport.trim()) {
          throw new Error('The Action finished without returning a usable report. Your existing report was preserved; please retry.');
        }

        // Commit only the fully successful server result. Multi-section edits
        // are atomic on the backend, so a failed target never updates the
        // report currently shown in the browser.
        const updatedCharts = Array.isArray(finalData.charts) ? finalData.charts : undefined;
        const updatedImages = Array.isArray(finalData.images) ? finalData.images : undefined;
        const updatedSources = Array.isArray(finalData.sources) ? finalData.sources : undefined;
        const savedRevision: ReportRevision = {
          revision: reportData.revision || 1,
          savedAt: Date.now(),
          note: instruction,
          report: reportData.report,
          title: reportData.title,
          charts: reportData.charts || [],
          images: reportData.images || [],
          sources: reportData.sources || [],
          presentation: reportData.presentation,
          keyStats: reportData.keyStats || [],
          summary: reportData.summary,
          theme: reportData.theme,
          qualityReview: reportData.qualityReview,
          publicationStatus: reportData.publicationStatus,
          sessionId: reportData.sessionId,
          ragStatus: reportData.ragStatus,
        };
        const mergedReportData: ReportData = {
          ...reportData,
          reportId: finalData.reportId || reportData.reportId || reportMsgId,
          revision: finalData.revision ?? ((reportData.revision || 1) + 1),
          revisionHistory: [...(reportData.revisionHistory || []), savedRevision].slice(-8),
          report: updatedReport,
          title: finalData.title || reportData.title,
          charts: updatedCharts || reportData.charts,
          images: updatedImages || reportData.images,
          sources: updatedSources || reportData.sources,
          presentation: finalData.presentation ?? reportData.presentation,
          qualityReview: finalData.qualityReview ?? reportData.qualityReview,
          publicationStatus: finalData.publicationStatus ?? 'WARNING',
          auditSummary: finalData.auditSummary,
          sessionId: reportData.sessionId,
          ragStatus: finalData.ragStatus ?? reportData.ragStatus,
          // Action reruns deterministic checks. The previous independent model
          // review applied to the old text and must not be shown as current.
          geminiAudit: undefined,
        };
        setMessages(prev => prev.map(m => {
          if (m.id === reportMsgId && m.reportData) {
            return { ...m, reportData: mergedReportData };
          }
          if (m.id === botMsg.id) {
            const chartNote = finalData.chartUpdated ? ' The chart was updated too.' : '';
            const tableNote = finalData.tableAdded ? ' A supported comparison table was added.' : '';
            const visualWarnings = Array.isArray(finalData.visualWarnings)
              ? finalData.visualWarnings.map((x: unknown) => String(x)).filter(Boolean)
              : (finalData.visualWarning ? [String(finalData.visualWarning)] : []);
            const visualNote = visualWarnings.length ? ` Visual note: ${visualWarnings.join(' ')}` : '';
            const ragNote = finalData.ragStatus?.status === 'used'
              ? ` Reused ${finalData.ragStatus.retrievedChunks || 0} relevant saved OKF evidence chunk(s).`
              : (finalData.ragStatus?.requested ? ' Saved OKF evidence was unavailable for this Action; existing evidence and web checks were used where available.' : '');
            const edited = Array.isArray(finalData.editedSections) && finalData.editedSections.length
              ? finalData.editedSections.join(' and ')
              : (finalData.editedSection || 'requested');
            return {
              ...m,
              text: `Updated the "${edited}" section${Array.isArray(finalData.editedSections) && finalData.editedSections.length > 1 ? 's' : ''}. The rest of the report is unchanged.${ragNote}${chartNote}${tableNote}${visualNote}`,
              reportData: mergedReportData,
            };
          }
          return m;
        }));
      } catch (e) {
        console.error('[editReportSection]', e);
        const message = e instanceof Error && e.message
          ? e.message
          : 'The Action could not be completed. Your existing report was preserved; please retry.';
        setMessages(prev => prev.map(m => m.id === botMsg.id ? { ...m, text: message } : m));
      } finally {
        setStreaming(false);
        setStatusMsg('');
      }
    })();
  }, []);

  const restoreReportRevision = useCallback((reportMsgId: string, saved: ReportRevision, current: ReportData) => {
    const currentSnapshot: ReportRevision = {
      revision: current.revision || 1,
      savedAt: Date.now(),
      note: `Restored content from revision ${saved.revision}`,
      report: current.report,
      title: current.title,
      charts: current.charts || [],
      images: current.images || [],
      sources: current.sources || [],
      presentation: current.presentation,
      keyStats: current.keyStats || [],
      summary: current.summary,
      theme: current.theme,
      qualityReview: current.qualityReview,
      publicationStatus: current.publicationStatus,
      sessionId: current.sessionId,
      ragStatus: current.ragStatus,
    };
    setMessages(prev => {
      const next = prev.map(message => message.id === reportMsgId && message.reportData ? {
        ...message,
        reportData: {
          ...message.reportData,
          ...saved,
          reportId: current.reportId || reportMsgId,
          revision: (current.revision || 1) + 1,
          revisionHistory: [...(current.revisionHistory || []), currentSnapshot].slice(-8),
          auditSummary: undefined,
          sessionId: saved.sessionId ?? current.sessionId,
          ragStatus: saved.ragStatus ?? current.ragStatus,
        },
      } : message);
      return [...next, {
        id: uid(), role: 'assistant' as const,
        text: `Restored report content from revision ${saved.revision}. The restored version is saved as revision ${(current.revision || 1) + 1}.`,
        ts: Date.now(),
      }];
    });
  }, []);

  const send = useCallback(async (text: string) => {
    const q = text.trim();
    if (!q || streaming) return;
    shouldAutoScrollRef.current = true;

    // If a report already exists in this conversation and the new message
    // reads as an edit/elaboration request, update just the section it
    // refers to instead of starting a fresh report or a normal chat reply.
    // The backend queues the Action and returns a job ID; this client polls
    // status until every targeted section is validated and committed atomically.
    const lastReportMsg = [...messages].reverse().find(m => m.reportData?.report);
    if (reportModeEnabled && lastReportMsg?.reportData && _EDIT_INTENT_RE.test(q)) {
      setReportModeEnabled(false);
      setReportTierMenuOpen(false);
      setInput('');
      if (inputRef.current) { inputRef.current.style.height = 'auto'; }
      editReportSection(lastReportMsg.id, q, lastReportMsg.reportData);
      return;
    }

    // Report generation is opt-in via the Report toggle. Without it, every
    // prompt—including prompts containing words like "report" or "analysis"—
    // follows the normal in-chat streaming answer path.
    if (reportModeEnabled) {
      setReportModeEnabled(false);
      setReportTierMenuOpen(false);
      setInput('');
      if (inputRef.current) { inputRef.current.style.height = 'auto'; }

      // Capture prior research metadata before creating the report placeholder so
      // the selected tier can reuse the exact preceding source manifest.
      const priorMessages = messages.filter(m => m.text && m.text.trim());
      const priorSources = [...priorMessages].reverse().find(m => m.role === 'assistant' && (m.sources?.length ?? 0) > 0)?.sources ?? [];
      const conversationContext = priorMessages
        .map(m => `${m.role === 'user' ? 'User' : 'Assistant'}: ${m.text.slice(0, 800)}`)
        .join('\n\n');
      const userMsg: Message = { id: uid(), role: 'user', text: q, ts: Date.now() };
      const botMsg: Message = {
        id: uid(), role: 'assistant', text: '', ts: Date.now(),
        reportEligible: true, reportQuestion: q, reportFiles: attachedFiles,
        sources: priorSources,
      };
      setMessages(prev => [...prev, userMsg, botMsg]);
      // A follow-up such as "give as a report" should transform the most
      // recent researched answer, not trigger a second independent web search.
      // Carry the exact source manifest attached to the latest researched
      // assistant message into /api/chat/report. The backend will reuse it and
      // only search when no prior research context exists.
      historyRef.current = [...historyRef.current, { role: 'user', content: q }];

      // The report-depth selector is shown only while Report mode is enabled.
      // Use the selected tier for this opt-in report request.
      // appendToHistory=true because this flow bypasses the normal streamed
      // chat-answer path.
      // The report-depth picker is authoritative. The wording of the prompt
      // must never silently change the user's selected tier (e.g. selecting
      // Brief and typing a prompt containing the word "detailed").
      const requestTier = selectedReportTier;
      generateReport(
        botMsg.id,
        q,
        attachedFiles,
        conversationContext || undefined,
        true,
        priorSources,
        requestTier,
      );
      return;
    }

    setInput('');
    if (inputRef.current) { inputRef.current.style.height = 'auto'; }

    const userMsg: Message = { id:uid(), role:'user',      text:q,  ts:Date.now() };
    const botMsg:  Message = { id:uid(), role:'assistant', text:'', ts:Date.now() };
    const newMessages = [...messages, userMsg, botMsg];

    const hasAttachments = attachedFiles.some(f => f.status === 'attached') || pastedTexts.length > 0;

    setMessages(newMessages);
    setStreaming(true);
    setSearching(true);
    setStatusMsg(
      ragIndexed ? 'Reading your documents…'
      : hasAttachments ? 'Analysing your attachment…'
      : 'Searching the web for latest data…'
    );
    // When attachments are present, don't carry over prior conversation history.
    // Sending old turns alongside a new file/image causes the model to answer from
    // previous topic context instead of the attachment. Reset to just the current message.
    const previousHistory = [...historyRef.current];
    if (hasAttachments) {
      historyRef.current = [{ role: 'user', content: q }];
    } else {
      historyRef.current = [...historyRef.current, { role: 'user', content: q }];
    }

    let finalText = '';
    const fileCtx = buildAttachmentContext(attachedFiles, pastedTexts);

    // Build image payloads for vision-capable main chat (mirrors buildFilePayload logic)
    const chatFileImages: { name:string; mimeType:string; data:string }[] = [];
    for (const f of attachedFiles) {
      if (f.status === 'attached' && f.type.startsWith('image/') && f.content) {
        const base64 = f.content.split(',')[1];
        if (base64) chatFileImages.push({ name: f.name, mimeType: f.type, data: base64 });
      }
    }

    try {
      // `historyRef` already contains the current user turn. Seed useChat with
      // the prior turns only; sendMessage appends the new prompt itself.
      const priorHistory = hasAttachments ? [] : historyRef.current.slice(0, -1);
      const sdkPriorMessages: UIMessage[] = priorHistory.map((item, index) => ({
        id: `gg-history-${index}-${uid()}`,
        role: item.role === 'user' ? 'user' as const : 'assistant' as const,
        parts: [{ type: 'text' as const, text: item.content }],
      }));
      sdkExpectedMessagesRef.current = sdkPriorMessages.length + 2;
      sdkSetMessages(sdkPriorMessages);
      sdkResponseTargetRef.current = botMsg.id;
      streamTargetTextRef.current = '';
      streamDisplayedTextRef.current = '';
      streamVisualMessageIdRef.current = botMsg.id;
      setStreamDisplayedText('');
      setStreamVisualMessageId(botMsg.id);

      const completion = new Promise<string>((resolve, reject) => {
        sdkCompletionRef.current = { resolve, reject };
      });
      const requestHeaders = await authHeader();
      sdkSendMessage({ text: q }, {
        headers: requestHeaders,
        body: {
          fileContext: fileCtx,
          sessionId: getOrCreateSessionId(),
          hasRag: ragIndexed,
          fileImages: chatFileImages,
        },
      });
      finalText = await completion;
      sdkCompletionRef.current = null;
      sdkResponseTargetRef.current = null;
      setMessages(prev => prev.map(message => message.id === botMsg.id
        ? { ...message, text: finalText }
        : message));

      // After reply, always restore the full history including this new exchange
      // so follow-up messages within the same session continue to work correctly.
      if (hasAttachments) {
        // Rebuild: old turns + new user msg + new assistant reply
        historyRef.current = [
          ...previousHistory,
          { role: 'user', content: q },
          { role: 'assistant', content: finalText },
        ];
      } else {
        historyRef.current = [...historyRef.current, { role: 'assistant', content: finalText }];
      }

      // Normal chat replies stay in the conversation. Opt-in reports are
      // started directly by the Report-mode branch above.
      const botMsgId = botMsg.id;
      const currentFiles = attachedFiles;
      const wantsVisual = /\b(chart|graph|plot|visuali[sz]e|trend\s*line)\b/i.test(q);
      const hasReportableFiles = currentFiles.some(f => f.status === 'attached');
      const isSubstantiveQuery = (() => {
        // Any attached file is inherently substantive — short prompts like
        // "summarize this" or "analyze this file" are exactly when users
        // want a report, so never gate eligibility on message length/chitchat
        // when a file is attached.
        if (hasReportableFiles) return true;
        const lower = q.toLowerCase().trim();
        // Skip very short messages (under 15 chars) — greetings, "hi", "hello", "ok", etc.
        if (q.trim().length < 15) return false;
        // Skip pure greetings / chitchat
        const chitchat = /^(hi|hello|hey|thanks|thank you|ok|okay|sure|great|good|yes|no|bye|test|ping|what'?s up|how are you)/i;
        if (chitchat.test(lower) && q.trim().length < 40) return false;
        return true;
      })();
      // Phrases that indicate the user actually wants a report/research
      // deliverable, not just an answer — "report", "research", "compare",
      // "analyze/analysis" (company/market/investment/detailed/comprehensive
      // all end in this), "deep dive", "due diligence", "valuation",
      // "in-depth". A plain factual or explanatory question ("What is X",
      // "Where is Y", "Explain Z") won't contain any of these.
      setMessages(prev => prev.map(m => m.id === botMsgId
        ? { ...m, reportEligible: false, reportQuestion: q, reportFiles: currentFiles, wantsVisual }
        : m));

      // ── Inline chart generation ────────────────────────────────────────────
      // After any data-heavy finance reply, silently call /api/chat/charts to
      // extract + publish charts from the reply text. Charts are attached to
      // the message and rendered inline above the text.
      //
      // IMPORTANT: if the reply already contains a markdown table that
      // splitTextAndCharts() will turn into an inline chart *inside* the text,
      // skip this call entirely — otherwise the same data gets charted twice
      // (once inline in the text, once again in the "📊 Charts" section above
      // it), which is what produces the duplicated/overlapping chart look.
      const alreadyHasInlineChart = wantsVisual && splitTextAndCharts(finalText).some(p => p.type === 'chart');
      const chartLengthOk = wantsVisual ? finalText.length > 80 : finalText.length > 200;
      if (isSubstantiveQuery && chartLengthOk && !alreadyHasInlineChart) {
        (async () => {
          try {
            const chartRes = await fetch('/api/chat/charts', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ question: q, reply: finalText, wantsVisual }),
            });
            if (!chartRes.ok) return;
            const chartData = await chartRes.json();
            const charts: ChartSpec[] = (chartData.charts ?? []).filter((c: ChartSpec) => {
              const pts = (c.series ?? []).flatMap(s => s.data ?? []);
              return pts.length >= 2 && new Set(pts.map(p => p.value)).size >= 2 && new Set(pts.map(p => p.label)).size >= 2;
            });
            if (charts.length > 0) {
              setMessages(prev => prev.map(m => m.id === botMsgId ? { ...m, inlineCharts: charts } : m));
            }
          } catch { /* silent — charts are non-critical */ }
        })();
      }
      // ── End inline chart generation ────────────────────────────────────────

      // ── Follow-up question generation ──────────────────────────────────────
      // Always show follow-up chips after every bot reply.
      // For greetings/chitchat: show hardcoded starter suggestions instantly.
      // For substantive queries: generate contextual ones via API.
      const GREETING_FOLLOWUPS = [
        { icon: '📈', label: 'Nifty outlook today' },
        { icon: '🏦', label: 'Top banking stocks' },
        { icon: '💰', label: 'Best SIPs right now' },
        { icon: '🚀', label: 'Upcoming IPOs' },
        { icon: '📊', label: 'Sector performance' },
      ];

      if (!isSubstantiveQuery) {
        // Greeting/chitchat — show starter chips immediately, no API call
        setMessages(prev => prev.map(m => m.id === botMsgId
          ? { ...m, followUpQuestions: GREETING_FOLLOWUPS.map(s => `${s.icon} ${s.label}`) }
          : m));
      } else {
        // Substantive query — generate contextual follow-ups via API
        (async () => {
          try {
            const fuRes = await fetch('/api/chat', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                messages: [
                  {
                    role: 'user',
                    content:
                      `Based on this Q&A, suggest exactly 3 short follow-up questions ` +
                      `a user might ask next, staying on the same topic as the Q&A below. ` +
                      `Add a relevant emoji before each question. ` +
                      `Return ONLY a JSON array of 3 strings, no explanation, no markdown.\n\n` +
                      `Q: ${q.slice(0, 200)}\nA: ${finalText.slice(0, 600)}`,
                  },
                ],
                fileContext: '',
                sessionId: '',
                hasRag: false,
                fileImages: [],
                _followUpMode: true,
              }),
            });
            if (!fuRes.ok) return;
            const reader = fuRes.body!.getReader();
            const dec = new TextDecoder();
            let buf = '', acc = '';
            while (true) {
              const { value, done } = await reader.read();
              if (done) break;
              buf += dec.decode(value, { stream: true });
              const lines = buf.split('\n');
              buf = lines.pop() ?? '';
              for (const line of lines) {
                if (!line.startsWith('data: ')) continue;
                const raw = line.slice(6).trim();
                if (raw === '[DONE]') break;
                try {
                  const j = JSON.parse(raw);
                  const t = j.choices?.[0]?.delta?.content;
                  if (t) acc += t;
                } catch { /* ignore */ }
              }
            }
            const match = acc.match(/\[[\s\S]*\]/);
            if (!match) return;
            const questions: string[] = JSON.parse(match[0]);
            if (!Array.isArray(questions) || questions.length === 0) return;
            setMessages(prev => prev.map(m => m.id === botMsgId
              ? { ...m, followUpQuestions: questions.slice(0, 3).map(s => String(s).trim()).filter(Boolean) }
              : m));
          } catch { /* silent fail — follow-ups are non-critical */ }
        })();
      }
      // ── End follow-up generation ───────────────────────────────────────────

      // Persist conversation
      const title = q.length > 46 ? q.slice(0,46)+'…' : q;
      setMessages(prev => {
        const final = prev;
        setConversations(convPrev => {
          if (activeId) {
            return convPrev.map(c => c.id === activeId ? { ...c, messages:final, ts:Date.now() } : c);
          } else {
            // Guard: don't add if a conv with this title was just created (Strict Mode double-fire)
            const alreadyExists = convPrev.some(c => c.title === title && Date.now() - c.ts < 2000);
            if (alreadyExists) return convPrev;
            const newConv: Conversation = { id:uid(), title, messages:final, ts:Date.now() };
            setActiveId(newConv.id);
            return [newConv, ...convPrev];
          }
        });
        return final;
      });
    } catch(e:unknown) {
      sdkCompletionRef.current = null;
      sdkResponseTargetRef.current = null;
      setSearching(false);
      setStatusMsg('');
      // Do not leave the typewriter overlay hiding an error or a cancelled reply.
      streamVisualMessageIdRef.current = null;
      streamTargetTextRef.current = '';
      streamDisplayedTextRef.current = '';
      setStreamVisualMessageId(null);
      setStreamDisplayedText('');
      if ((e as Error)?.name === 'AbortError') return;
      const errMsg = (e as Error)?.message || '';
      const isNetwork = errMsg.includes('fetch') || errMsg.includes('network') || errMsg.includes('Failed');
      setMessages(prev => prev.map(m => m.id === botMsg.id ? {
        ...m,
        text: isNetwork
          ? '*Network error — please check your connection and try again.*'
          : '*Something went wrong. Please try again.*',
      } : m));
    } finally {
      setStreaming(false);
      setSearching(false);
      setStatusMsg('');
    }
  }, [streaming, messages, activeId, attachedFiles, pastedTexts, editReportSection, selectedReportTier, reportModeEnabled]);

  /** Builds a report for a Report-mode query. `conversationContext` contains
   *  prior Q&A from the current thread when available. */
  const generateReport = useCallback((botMsgId: string, question: string, files: AttachedFile[], conversationContext?: string, appendToHistory: boolean = false, priorSources: Source[] = [], reportTier: ReportTier = 'detailed') => {
    // Normalize persisted/legacy tier values such as `Brief` to the backend enum.
    // All report tiers must use the durable start + SSE route; the synchronous
    // route can take minutes and prevents progress from reaching the browser.
    const normalizedTier = String(reportTier || 'detailed').trim().toLowerCase();
    const effectiveReportTier: ReportTier = ['brief', 'detailed', 'in_depth'].includes(normalizedTier)
      ? normalizedTier as ReportTier
      : 'detailed';
    console.info('[report] using background streaming pipeline', { reportTier: effectiveReportTier });
    // Duplicate-submission guard: a double click or a retry while this
    // message's report is still in flight must not start a second job.
    if (reportInFlightRef.current.has(botMsgId)) {
      console.warn('[report] a report for this message is already in progress; ignoring duplicate request');
      return;
    }
    reportInFlightRef.current.add(botMsgId);
    // Stable per message + tier + question, so the backend can hand back the
    // existing job if the browser retries after a dropped connection.
    const effectiveReportStyle = selectedReportStyleRef.current;
    const clientRequestId = `${botMsgId}:${effectiveReportTier}:${effectiveReportStyle}:${question.length}:${Array.from(question).reduce((h, ch) => ((h * 31) + ch.charCodeAt(0)) | 0, 7)}`;
    setMessages(prev => prev.map(m => m.id === botMsgId ? {
      ...m, reportLoading: true, reportProgress: 'Preparing report…', reportProgressPercent: 1,
      reportPreview: undefined, reportPreviewTruncated: false, reportError: undefined,
    } : m));

    const buildFilePayload = async () => {
      // fileImages: FULL-PAGE renders — used ONLY so the backend's Gemini
      // Vision call can read all text/tables/numbers off the page. These are
      // never sent to the PDF renderer and never embedded in the report.
      const fileImages: { name: string; mimeType: string; data: string }[] = [];
      // embeddedFileImages: actual embedded raster images (charts, graphs,
      // photos) pulled OUT of the PDF pages themselves — these are the only
      // images that may end up embedded inline in the generated report.
      // If a file's charts/tables are drawn as vector shapes rather than
      // embedded raster images (common in generated PDF reports), this list
      // will legitimately be empty for that file — no full page is used as
      // a fallback.
      const embeddedFileImages: { name: string; mimeType: string; data: string }[] = [];
      const fileTextParts: string[] = [];

      // Minimum size for an extracted image to be considered a genuine
      // chart/figure/photo rather than a decorative icon, bullet glyph, or
      // logo. Small dimension in points at typical PDF resolution.
      const MIN_EMBED_DIM = 120;
      // Cap on total embedded images sent, matching the backend's [:8] limit.
      const MAX_EMBEDDED_IMAGES = 8;
      const seenImageHashes = new Set<string>();

      const simpleHash = (bytes: Uint8ClampedArray | Uint8Array): string => {
        // Cheap content fingerprint for de-duplication (e.g. the same
        // logo/header image repeated on every page) — not cryptographic,
        // just needs to distinguish "same image again" from "new image".
        let h = 0;
        const step = Math.max(1, Math.floor(bytes.length / 256));
        for (let i = 0; i < bytes.length; i += step) {
          h = (h * 31 + bytes[i]) | 0;
        }
        return `${bytes.length}:${h}`;
      };

      const imgObjToDataUrl = (imgObj: any): string | null => {
        try {
          let canvas = document.createElement('canvas');
          let ctx: CanvasRenderingContext2D | null = null;
          if (typeof ImageBitmap !== 'undefined' && imgObj instanceof ImageBitmap) {
            canvas.width = imgObj.width;
            canvas.height = imgObj.height;
            ctx = canvas.getContext('2d');
            if (!ctx) return null;
            ctx.drawImage(imgObj, 0, 0);
          } else if (imgObj && imgObj.data && imgObj.width && imgObj.height) {
            // Raw pixel buffer — pdf.js gives kind: 1=grayscale, 2=RGB, 3=RGBA
            const { width, height, data, kind } = imgObj;
            canvas.width = width;
            canvas.height = height;
            ctx = canvas.getContext('2d');
            if (!ctx) return null;
            const rgba = new Uint8ClampedArray(width * height * 4);
            if (kind === 3 || data.length === width * height * 4) {
              rgba.set(data);
            } else if (kind === 2 || data.length === width * height * 3) {
              for (let p = 0, q = 0; p < data.length; p += 3, q += 4) {
                rgba[q] = data[p]; rgba[q + 1] = data[p + 1]; rgba[q + 2] = data[p + 2]; rgba[q + 3] = 255;
              }
            } else {
              // Grayscale or unrecognised layout — skip rather than risk a
              // corrupted/garbled image being embedded in the report.
              return null;
            }
            ctx.putImageData(new ImageData(rgba, width, height), 0, 0);
          } else {
            return null;
          }
          return canvas.toDataURL('image/jpeg', 0.85).split(',')[1];
        } catch {
          return null;
        }
      };

      for (const f of files) {
        if (f.extractedText) {
          // Spreadsheets (e.g. multi-sheet fund/scheme performance workbooks)
          // are dense, structured data that the report treats as its primary
          // source — give them a much larger budget than prose file types so
          // a 20+ sheet workbook doesn't get chopped down to its first sheet.
          const textCap = isSpreadsheetFile(f) ? 150_000 : 12_000;
          fileTextParts.push(`[File: ${f.name}]\n${f.extractedText.slice(0, textCap)}`);
        }

        if (f.type === 'application/pdf') {
          try {
            const pdfjsLib = await loadPdfJs();
            const dataUrl = f.content; // base64 data URL
            const base64 = dataUrl.split(',')[1];
            const binary = atob(base64);
            const bytes = new Uint8Array(binary.length);
            for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
            const pdf = await pdfjsLib.getDocument({ data: bytes }).promise;
            const numPages = Math.min(pdf.numPages, 8); // max 8 pages

            for (let pageNum = 1; pageNum <= numPages; pageNum++) {
              const page = await pdf.getPage(pageNum);

              // 1) Full-page render — kept ONLY for the backend's vision-based
              //    text extraction (extract_data_from_images). Never used for
              //    embedding images in the report itself.
              const viewport = page.getViewport({ scale: 1.5 });
              const canvas = document.createElement('canvas');
              canvas.width = viewport.width;
              canvas.height = viewport.height;
              const ctx = canvas.getContext('2d')!;
              await page.render({ canvasContext: ctx, viewport }).promise;
              const imgData = canvas.toDataURL('image/jpeg', 0.85).split(',')[1];
              fileImages.push({ name: `${f.name} page ${pageNum}`, mimeType: 'image/jpeg', data: imgData });

              // 2) Extract only the actual embedded raster images on this
              //    page (real charts/graphs/photos), via pdf.js's operator
              //    list — this is what may get embedded in the report.
              if (embeddedFileImages.length >= MAX_EMBEDDED_IMAGES) continue;
              try {
                const opList = await page.getOperatorList();
                const OPS = pdfjsLib.OPS;
                for (let idx = 0; idx < opList.fnArray.length; idx++) {
                  if (embeddedFileImages.length >= MAX_EMBEDDED_IMAGES) break;
                  const fn = opList.fnArray[idx];
                  if (fn !== OPS.paintImageXObject) continue;
                  const objId = opList.argsArray[idx][0];
                  const imgObj: any = await new Promise((resolve) => {
                    try { page.objs.get(objId, resolve); } catch { resolve(null); }
                  });
                  if (!imgObj) continue;
                  const w = imgObj.width || 0, h = imgObj.height || 0;
                  if (w < MIN_EMBED_DIM || h < MIN_EMBED_DIM) continue; // skip icons/logos
                  const dataStr = imgObjToDataUrl(imgObj);
                  if (!dataStr) continue;
                  const hash = simpleHash(imgObj.data instanceof Uint8ClampedArray || imgObj.data instanceof Uint8Array ? imgObj.data : new Uint8Array(0));
                  if (hash !== '0:0' && seenImageHashes.has(hash)) continue; // skip repeated logos/headers
                  seenImageHashes.add(hash);
                  embeddedFileImages.push({
                    name: `${f.name} — figure (page ${pageNum})`,
                    mimeType: 'image/jpeg',
                    data: dataStr,
                  });
                }
              } catch (e) {
                console.warn('[report] embedded image extraction failed for page', pageNum, e);
              }
            }
          } catch (e) {
            console.warn('[report] pdf.js render failed:', e);
          }
        } else if (f.type.startsWith('image/')) {
          // Direct image attachment — this genuinely is "an image the user
          // provided", not a page render, so it's fair game for both uses.
          const base64 = f.content.split(',')[1];
          if (base64) {
            fileImages.push({ name: f.name, mimeType: f.type, data: base64 });
            embeddedFileImages.push({ name: f.name, mimeType: f.type, data: base64 });
          }
        }
      }

      return { fileImages, embeddedFileImages, fileTextContext: fileTextParts.join('\n\n') };
    };

    // Source-document concepts for the OKF bundle — one per attached file,
    // independent of how much text made it into the LLM prompt (capped above
    // at 12000 chars per file for the prompt; the OKF concept can carry more
    // since it's just markdown, capped again server-side at 4000 chars).
    const sourceDocuments = files
      .filter(f => f.status === 'attached')
      .map(f => ({ name: f.name, text: f.extractedText || '', file_type: f.type }));

    buildFilePayload().then(async ({ fileImages, embeddedFileImages, fileTextContext }) => {
      // Every report tier uses the durable background-job + SSE progress path.
      // Brief still uses its smaller source/page budget, but the browser no
      // longer waits on one long blocking POST or appears frozen during work.
      const useBackgroundReportJob = ['brief', 'detailed', 'in_depth'].includes(effectiveReportTier);
      if (useBackgroundReportJob) {
        console.info('[report] POST /api/chat/report/start', { reportTier: effectiveReportTier });
        const startPayload = {
          question, reportTier: effectiveReportTier, reportStyle: effectiveReportStyle, sources: priorSources, fileContext: fileTextContext, clientRequestId,
          conversationContext: conversationContext || '', fileImages, embeddedFileImages,
          sourceDocuments, sessionId: getOrCreateSessionId(), hasRag: ragIndexed,
        };
        const started = await fetch('/api/chat/report/start', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(startPayload),
        });
        const startContentType = String(started.headers.get('content-type') || '').toLowerCase();
        let startData: any = null;
        if (startContentType.includes('application/json')) {
          startData = await started.json().catch(() => null);
        } else {
          // Do not ever place raw HTML/text returned by a stale app route into
          // the reportError UI. Return a short deployment diagnostic instead.
          const responseText = await started.text().catch(() => '');
          const looksLikeHtml = /<!doctype\s+html|<html[\s>]/i.test(responseText.slice(0, 1000))
            || startContentType.includes('text/html');
          console.error('[report] start endpoint returned a non-JSON response', {
            status: started.status, contentType: startContentType || '(missing)', looksLikeHtml,
            responsePrefix: responseText.slice(0, 120).replace(/\s+/g, ' '),
          });
          return { ok: false, status: started.status || 502, data: { error: looksLikeHtml
            ? 'The report start API returned the website page instead of JSON. Deploy the latest frontend and backend routes, then run the API diagnostic.'
            : `The report start API returned an unexpected content type (HTTP ${started.status}).` } };
        }
        if (!started.ok || !startData?.jobId) {
          const safeError = typeof startData?.error === 'string' && startData.error.trim()
            ? `Report start failed: ${startData.error.slice(0, 600)}`
            : `Could not start the report job (HTTP ${started.status}). Check the report API routes and backend logs.`;
          return { ok: false, status: started.status || 502, data: { error: safeError } };
        }
        const maxWaitMs = effectiveReportTier === 'in_depth' ? 60 * 60_000 : effectiveReportTier === 'brief' ? 20 * 60_000 : 30 * 60_000;
        setStatusMsg(effectiveReportTier === 'in_depth'
          ? 'Deep Research is running — collecting evidence and building the comprehensive report…'
          : effectiveReportTier === 'brief'
            ? 'Brief report is running — finding evidence and assembling the focused report…'
            : 'In-Detail research is running — expanding evidence and building the detailed report…');
        try {
          const streamRes = await fetch(`/api/chat/report/stream/${encodeURIComponent(startData.jobId)}`, {
            cache: 'no-store', signal: AbortSignal.timeout(maxWaitMs),
          });
          const streamContentType = String(streamRes.headers.get('content-type') || '').toLowerCase();
          if (!streamRes.ok || !streamRes.body || !streamContentType.includes('text/event-stream')) {
            // A stale/misrouted Next.js deployment can answer an API path with
            // its HTML app shell. Never surface that entire document as the
            // assistant's red error message; fall through to durable job-status
            // polling because the backend job may still be progressing.
            const responseText = await streamRes.text().catch(() => '');
            const looksLikeHtml = /<!doctype\s+html|<html[\s>]/i.test(responseText.slice(0, 1000))
              || streamContentType.includes('text/html');
            const safeStreamError = looksLikeHtml
              ? `The report streaming route returned a web page instead of a progress stream (HTTP ${streamRes.status}). Checking the existing report job status…`
              : `The report progress stream could not be opened (HTTP ${streamRes.status}). Checking the existing report job status…`;
            console.warn('[report] invalid progress-stream response; switching to status polling', {
              status: streamRes.status,
              contentType: streamContentType || '(missing)',
              looksLikeHtml,
              responsePrefix: responseText.slice(0, 120).replace(/\s+/g, ' '),
            });
            throw new Error(safeStreamError);
          }
          const reader = streamRes.body.getReader();
          const decoder = new TextDecoder();
          let buffer = '';
          let finalResult: any = null;
          let finalError = '';
          let finalFailedStage = '';
          while (true) {
            const { value, done } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const frames = buffer.split('\n\n');
            buffer = frames.pop() || '';
            for (const frame of frames) {
              const dataLine = frame.split(/\r?\n/).find(line => line.startsWith('data: '));
              if (!dataLine) continue;
              const raw = dataLine.slice(6).trim();
              if (!raw || raw === '[DONE]') continue;
              try {
                const event = JSON.parse(raw);
                if (event.type === 'data-report-status' && event.data) {
                  const progressData = event.data as { message?: string; percent?: number; phase?: string };
                  const progressMessage = String(progressData.message || 'Generating report…');
                  const progressPercent = Number.isFinite(Number(progressData.percent)) ? Math.max(0, Math.min(100, Number(progressData.percent))) : undefined;
                  setStatusMsg(progressPercent === undefined ? progressMessage : `${progressMessage} ${progressPercent}%`);
                  setMessages(prev => prev.map(m => m.id === botMsgId ? { ...m, reportProgress: progressMessage, reportProgressPercent: progressPercent } : m));
                }
                if (event.type === 'data-report-preview' && event.data) {
                  const previewData = event.data as { delta?: string; truncated?: boolean };
                  const delta = String(previewData.delta || '');
                  if (delta) setMessages(prev => prev.map(m => m.id === botMsgId ? {
                    ...m, reportPreview: ((m.reportPreview || '') + delta).slice(0, 32000),
                    reportPreviewTruncated: Boolean(previewData.truncated),
                  } : m));
                }
                if (event.type === 'data-report-result') finalResult = event.data;
                if (event.type === 'data-report-error') {
                  finalError = String(event.data?.error || 'Report generation failed.');
                  finalFailedStage = String(event.data?.failedStage || '');
                }
              } catch { /* ignore malformed/partial protocol frames */ }
            }
          }
          if (finalResult && typeof finalResult.report === 'string' && finalResult.report.trim()) {
            setStatusMsg('');
            return { ok: true, status: 200, data: finalResult };
          }
          if (finalError) {
            setStatusMsg('');
            const stageText = reportStageLabel(finalFailedStage);
            return { ok: false, status: 502, data: { error: stageText ? `Failed at the ${stageText}: ${finalError}` : finalError, failedStage: finalFailedStage || undefined } };
          }
          // A cleanly closed stream can still mean a proxy timed out while the
          // durable report job keeps running. Fall through to status polling
          // instead of declaring failure immediately.
          throw new Error('The report stream ended before a complete report was received.');
        } catch (streamErr) {
          console.warn('[report] progress stream interrupted; checking durable job status', streamErr);
          // Keep checking the durable job if an intermediary closes the SSE
          // connection before final validation/PDF creation has completed.
          let fallbackStarted = Date.now();
          let activeJobId: string = startData.jobId;
          let restartedLostJob = false;
          while (Date.now() - fallbackStarted < maxWaitMs) {
            await new Promise(resolve => setTimeout(resolve, 2500));
            try {
              const statusRes = await fetch(`/api/chat/report/status/${encodeURIComponent(activeJobId)}`, { cache: 'no-store' });
              const statusContentType = String(statusRes.headers.get('content-type') || '').toLowerCase();
              // Report jobs live in backend memory, so a backend restart (a deploy,
              // a crash, or uvicorn --reload picking up a file change) loses the
              // running job and its status answers 404 JSON. Start it again once.
              if (statusRes.status === 404 && statusContentType.includes('application/json')) {
                if (restartedLostJob) {
                  setStatusMsg('');
                  return { ok: false, status: 404, data: { error: 'The report server restarted twice while this report was being generated, so the job was lost. Please generate the report again.' } };
                }
                restartedLostJob = true;
                console.warn('[report] backend lost the report job (likely restarted); starting it again', { jobId: activeJobId });
                setStatusMsg('The report server restarted and lost this job — starting the report again…');
                const restarted = await fetch('/api/chat/report/start', {
                  method: 'POST',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({ ...startPayload, clientRequestId: `${clientRequestId}:restart` }),
                });
                const restartedData = await restarted.json().catch(() => null) as { jobId?: string } | null;
                if (!restarted.ok || !restartedData?.jobId) {
                  setStatusMsg('');
                  return { ok: false, status: restarted.status || 502, data: { error: 'The report server restarted and lost this job, and starting it again failed. Please generate the report again.' } };
                }
                activeJobId = restartedData.jobId;
                fallbackStarted = Date.now();
                continue;
              }
              // If the status API path also resolves to a Next.js HTML fallback,
              // stop immediately with an actionable message instead of parsing
              // {} repeatedly for up to 20 minutes.
              if (!statusRes.ok || !statusContentType.includes('application/json')) {
                const fallbackBody = await statusRes.text().catch(() => '');
                const fallbackIsHtml = /<!doctype\s+html|<html[\s>]/i.test(fallbackBody.slice(0, 1000))
                  || statusContentType.includes('text/html');
                const safeMessage = fallbackIsHtml
                  ? 'The report streaming and status API routes returned the website page instead of API responses. Deploy the updated frontend and restart it; the current report job may still be running on the backend.'
                  : `The report status API returned HTTP ${statusRes.status}. Deploy the updated frontend API routes and retry after checking whether the report job completed.`;
                setStatusMsg('');
                return { ok: false, status: statusRes.status || 502, data: { error: safeMessage } };
              }
              const statusData: any = await statusRes.json().catch(() => null);
              if (!statusData || typeof statusData !== 'object') {
                setStatusMsg('');
                return { ok: false, status: 502, data: { error: 'The report status API returned invalid JSON. Restart or redeploy the frontend API routes before retrying.' } };
              }
              if (statusData.progressMessage) {
                const progressMessage = String(statusData.progressMessage);
                const progressPercent = Number.isFinite(Number(statusData.progressPercent)) ? Math.max(0, Math.min(100, Number(statusData.progressPercent))) : undefined;
                setStatusMsg(progressPercent === undefined ? progressMessage : `${progressMessage} ${progressPercent}%`);
                setMessages(prev => prev.map(m => m.id === botMsgId ? { ...m, reportProgress: progressMessage, reportProgressPercent: progressPercent } : m));
              }
              if (statusData.status === 'done') {
                const result = statusData.result || statusData;
                if (typeof result.report === 'string' && result.report.trim()) { setStatusMsg(''); return { ok: true, status: 200, data: result }; }
              }
              if (statusData.status === 'failed') {
                setStatusMsg('');
                const stageText = reportStageLabel(statusData.failedStage);
                const baseError = statusData.error || 'Report generation failed.';
                return { ok: false, status: 502, data: { error: stageText ? `Failed at the ${stageText}: ${baseError}` : baseError, failedStage: statusData.failedStage } };
              }
            } catch { /* transient proxy failure; retry briefly */ }
          }
          setStatusMsg('');
          return { ok: false, status: 504, data: { error: 'The report status could not be confirmed before the waiting limit. The existing job may still be running; check its status before starting a duplicate report.' } };
        }
      }

      return { ok: false, status: 500, data: { error: 'Background report pipeline ended unexpectedly.' } };
    })
      .then(async r => {
        // The background stream returns a normalized {ok,status,data} outcome.
        // Keep the native Response compatibility branch for future direct API
        // callers and graceful rollback during a staged frontend deployment.
        let responseOk = false;
        let responseStatus = 500;
        let data: any = null;

        if (r && typeof r === 'object' && 'data' in (r as any) && 'ok' in (r as any) && 'status' in (r as any)) {
          responseOk = Boolean((r as any).ok);
          responseStatus = Number((r as any).status ?? 500);
          data = (r as any).data;
        } else if (r instanceof Response) {
          responseOk = r.ok;
          responseStatus = r.status;
          try { data = await r.json(); } catch { /* non-JSON body — data stays null */ }
        } else {
          data = r as any;
          responseOk = Boolean((data as any)?.ok);
          responseStatus = Number((data as any)?.status ?? 500);
          data = (data as any)?.data ?? data;
        }

        // A response is only treated as a real report if it came back OK
        // AND actually contains non-empty report text. Previously this
        // branch was unconditional — an upstream timeout/502/error body
        // (e.g. { error: "..." } from the proxy, or a killed connection
        // after Gemini retries ran long) was still written into reportData
        // as report: '', which silently marked the report "done" and
        // enabled Download PDF with nothing to send — that's what produced
        // the backend's "No report content" 400.
        const reportText = typeof data?.report === 'string' ? data.report : '';
        const returnedTier = typeof data?.reportTier === 'string' ? data.reportTier : effectiveReportTier;
        if (responseOk && returnedTier !== effectiveReportTier) {
          responseOk = false;
          responseStatus = 502;
          data = { error: `Report depth mismatch: requested ${effectiveReportTier}, received ${returnedTier}. The report was not accepted.` };
        }
        if (!responseOk || !reportText.trim()) {
          const message = (data && typeof data.error === 'string' && data.error)
            || `Report generation failed (HTTP ${responseStatus}). Please try again.`;
          setMessages(prev => prev.map(m => m.id === botMsgId
            ? { ...m, reportLoading: false, reportError: message, reportPreview: undefined, reportProgress: undefined, reportProgressPercent: undefined }
            : m));
          return;
        }

        // Concise in-chat preview, built from the same generation response —
        // no second LLM call. It fills the report-mode assistant bubble while
        // leaving ordinary chat answers untouched.
        const previewText = (data.summary ?? '').trim();

        setMessages(prev => prev.map(m => m.id === botMsgId ? {
          ...m,
          text: m.text && m.text.trim() ? m.text : previewText,
          reportLoading: false,
          reportError: undefined,
          reportProgress: undefined,
          reportProgressPercent: undefined,
          reportPreview: undefined,
          reportPreviewTruncated: false,
          reportData: {
            reportId: data.reportId || crypto.randomUUID(),
            revision: Number(data.revision) || 1,
            revisionHistory: [],
            report:     reportText,
            reportTier: data.reportTier ?? effectiveReportTier,
            reportStyle: data.reportStyle ?? effectiveReportStyle,
            kicker: typeof data.kicker === 'string' ? data.kicker : undefined,
            pageTarget: data.pageTarget ?? undefined,
            pageBudget: data.pageBudget ?? undefined,
            title:      data.title      ?? '',
            charts:     data.charts     ?? [],
            images:     data.images     ?? [],
            keyStats:   data.keyStats   ?? [],
            summary:    data.summary    ?? '',
            fileImages: data.fileImages ?? [],
            sourceDocuments,
            sources: data.sources ?? [],
            // "html" when the question itself asked for something a static
            // PDF can't do (animation/interactive/motion/etc.), OR when the
            // model's own theme decision (see data.theme.richStyleNeeded)
            // says the requested visual style needs treatment a flat PDF
            // re-theme can't deliver — computed server-side either way, so
            // an absent field here still falls back to "pdf".
            recommendedFormat: data.recommendedFormat ?? 'pdf',
            // Carried through unchanged to /report/pdf and /report/html on
            // every later download, and preserved across section edits —
            // this is what actually makes fontFamily/customCss apply.
            theme: data.theme ?? undefined,
            qualityReview: data.qualityReview ?? undefined,
            geminiAudit: data.geminiAudit ?? undefined,
            publicationStatus: data.publicationStatus ?? undefined,
            publicationOutcome: data.publicationOutcome ?? (data.publicationStatus === 'FAIL' ? 'draft' : undefined),
            failedStage: data.failedStage ?? undefined,
            blockingCategories: Array.isArray(data.blockingCategories) ? data.blockingCategories : undefined,
            failureSummary: Array.isArray(data.failureSummary) ? data.failureSummary : undefined,
            chartPipeline: data.chartPipeline ?? undefined,
            presentation: data.presentation ?? undefined,
            sessionId: data.sessionId ?? getOrCreateSessionId(),
            ragStatus: data.ragStatus ?? { requested: ragIndexed, status: ragIndexed ? 'requested' : 'not_requested' },
          },
        } : m));

        // Keep chat history continuous for report-mode follow-ups. The normal
        // /api/chat flow updates history during streaming; report mode bypasses
        // that flow and therefore records its own assistant preview.
        if (appendToHistory && previewText) {
          historyRef.current = [...historyRef.current, { role: 'assistant', content: previewText }];
        }
      })
      .catch((e) => {
        console.error('[generateReport]', e);
        setMessages(prev => prev.map(m => m.id === botMsgId
          ? { ...m, reportLoading: false, reportError: 'Network error — could not reach server. Please try again.', reportPreview: undefined, reportProgress: undefined, reportProgressPercent: undefined }
          : m));
      })
      .finally(() => { reportInFlightRef.current.delete(botMsgId); });
  }, [ragIndexed]);

  const onKey = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key==='Enter' && !e.shiftKey) { e.preventDefault(); send(input); }
  };

  const isEmpty = messages.length === 0;

  // Group conversations by date
  const grouped = conversations.reduce((acc, c) => {
    const d = fmtDate(c.ts);
    if (!acc[d]) acc[d] = [];
    acc[d].push(c);
    return acc;
  }, {} as Record<string, Conversation[]>);

  return (
    <>
<style>{`
        /* ════════════════════════════════════════════════════════════════════
           GROWTH GRADUAL CHAT — FULLY FLUID RESPONSIVE STYLES
           All dimensions use clamp(), %, svh/svw, or fluid calc() so the
           UI fits every screen from 320 px phone to 4K desktop.
        ════════════════════════════════════════════════════════════════════ */

        /* ── Shell ──────────────────────────────────────────────────────── */
        .chat-shell {
          display: flex;
          /* Navbar (~58px) + ticker (~30px) + page padding (~22px) = ~110px */
          height: calc(100svh - 112px);
          min-height: 0;
          background: #ffffff;
          border-radius: clamp(6px, 1vw, 14px);
          border: 1px solid #e0e5ef;
          box-shadow: 0 2px 20px rgba(15,23,42,.08);
          overflow: hidden;
          font-family: 'DM Sans', sans-serif;
          position: relative;
        }

        /* ── Sidebar ────────────────────────────────────────────────────── */
        .sidebar {
          width: clamp(200px, 22vw, 280px);
          flex-shrink: 0;
          background: #f0f2f7;
          border-right: 1px solid #e2e6f0;
          display: flex;
          flex-direction: column;
          transition: width .25s cubic-bezier(.4,0,.2,1), transform .25s cubic-bezier(.4,0,.2,1);
          overflow: hidden;
        }
        .sidebar--closed { width: 0 !important; }

        .sidebar-hdr {
          padding: clamp(10px,1.5vh,14px) clamp(10px,1.5vw,16px);
          display: flex; align-items: center; gap: 10px;
          border-bottom: 1px solid rgba(26,31,78,.1);
          flex-shrink: 0; background: #fff;
        }
        .sidebar-logo {
          width: clamp(28px,3.5vw,36px); height: clamp(28px,3.5vw,36px);
          border-radius: 9px; background: #ffffff;
          display: flex; align-items: center; justify-content: center;
          flex-shrink: 0; overflow: hidden;
          border: 1px solid #e2e6f0; box-shadow: 0 1px 4px rgba(26,31,78,.08);
        }
        .sidebar-brand {
          color: #0f172a; font-size: clamp(11px,1.1vw,13px);
          font-weight: 800; font-family: 'Playfair Display',serif;
          line-height: 1.2; white-space: nowrap; overflow: hidden;
        }
        .sidebar-brand span {
          display: block; font-size: clamp(7px,.8vw,9px); font-weight: 500;
          color: #94a3b8; font-family: 'DM Sans',sans-serif;
          letter-spacing: .1em; text-transform: uppercase; margin-top: 1px;
        }

        .new-chat-btn {
          margin: clamp(8px,1vh,12px) clamp(8px,1vw,14px);
          display: flex; align-items: center; gap: 8px;
          padding: clamp(7px,1vh,10px) clamp(10px,1.2vw,14px);
          border-radius: 9px; border: 1.5px solid #1a1f4e;
          background: #1a1f4e; color: #fff;
          font-size: clamp(11px,1vw,13px); font-family: 'DM Sans',sans-serif;
          cursor: pointer; transition: background .15s, box-shadow .15s, transform .15s;
          flex-shrink: 0; white-space: nowrap;
          box-shadow: 0 2px 8px rgba(26,31,78,.22); font-weight: 600;
        }
        .new-chat-btn:hover { background: #252b68; box-shadow: 0 4px 14px rgba(26,31,78,.3); transform: translateY(-1px); }

        .conv-list {
          flex: 1; overflow-y: auto; padding: 4px 0 12px;
          scrollbar-width: thin; scrollbar-color: #d0d5e8 transparent;
        }
        .conv-list::-webkit-scrollbar { width: 3px; }
        .conv-list::-webkit-scrollbar-thumb { background: #d0d5e8; border-radius: 4px; }

        .conv-group-label {
          padding: 10px 14px 4px; font-size: 9px; color: #b0b8d4;
          text-transform: uppercase; letter-spacing: .1em;
          font-family: 'DM Sans',sans-serif; white-space: nowrap;
        }
        .conv-item {
          display: flex; align-items: center; gap: 8px;
          padding: clamp(6px,.8vh,9px) 12px;
          cursor: pointer; border-radius: 8px;
          margin: 1px 6px; transition: background .13s; position: relative;
        }
        .conv-item:hover { background: rgba(26,31,78,.07); }
        .conv-item--active { background: rgba(26,31,78,.1); border-left: 2.5px solid #1a6b5a; padding-left: 9.5px; }
        .conv-item__icon { flex-shrink: 0; opacity: .45; }
        .conv-item__title {
          flex: 1; font-size: clamp(11px,1vw,12.5px); color: #4b5680;
          white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
          font-family: 'DM Sans',sans-serif;
        }
        .conv-item--active .conv-item__title { color: #0d4f3c; font-weight: 700; }
        .conv-del {
          opacity: 0; width: 22px; height: 22px; border-radius: 6px;
          border: none; background: rgba(26,31,78,.07); color: #8b93b5;
          cursor: pointer; display: flex; align-items: center; justify-content: center;
          flex-shrink: 0; transition: opacity .15s, background .15s;
        }
        .conv-item:hover .conv-del { opacity: 1; }
        .conv-del:hover { background: rgba(239,68,68,.12); color: #ef4444; }

        .sidebar-footer {
          padding: 10px 14px 14px; border-top: 1px solid rgba(26,31,78,.1);
          font-size: clamp(9px,.9vw,10.5px); color: #8b93b5;
          font-family: 'DM Sans',sans-serif; flex-shrink: 0;
          background: linear-gradient(0deg,#f4f5fa 0%,#f8f9fd 100%);
        }

        /* ── Main chat area ─────────────────────────────────────────────── */
        .chat-main { flex: 1; display: flex; flex-direction: column; min-width: 0; background: #f8f9fb; }

        /* ── Top bar ────────────────────────────────────────────────────── */
        .chat-topbar {
          display: flex; align-items: center; gap: clamp(6px,.8vw,12px);
          padding: clamp(8px,1.2vh,12px) clamp(10px,1.5vw,18px);
          background: #0f172a; border-bottom: 1px solid rgba(255,255,255,0.07);
          flex-shrink: 0;
        }
        .topbar-toggle {
          width: clamp(26px,2.8vw,32px); height: clamp(26px,2.8vw,32px);
          border-radius: 8px; border: 1px solid rgba(255,255,255,0.1);
          background: rgba(255,255,255,0.05); color: rgba(255,255,255,0.5);
          cursor: pointer; display: flex; align-items: center; justify-content: center;
          transition: background .15s, color .15s; flex-shrink: 0;
        }
        .topbar-toggle:hover { background: rgba(255,255,255,0.1); color: #fff; }
        .topbar-logo {
          width: clamp(24px,2.5vw,30px); height: clamp(24px,2.5vw,30px);
          border-radius: 8px; overflow: hidden;
          display: flex; align-items: center; justify-content: center;
          flex-shrink: 0; background: transparent;
        }
        .topbar-info { flex: 1; min-width: 0; overflow: hidden; }
        .topbar-name {
          font-size: clamp(12px,1.3vw,14px); font-weight: 700; color: #fff;
          font-family: 'Playfair Display',serif; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
        }
        .topbar-sub {
          font-size: clamp(9px,.9vw,10.5px); color: rgba(255,255,255,0.4);
          display: flex; align-items: center; gap: 5px;
          white-space: nowrap; overflow: hidden;
        }
        .topbar-dot { width: 6px; height: 6px; border-radius: 50%; background: #22c55e; box-shadow: 0 0 6px rgba(34,197,94,0.7); animation: blink 2s ease-in-out infinite; }
        .topbar-web-badge {
          margin-left: auto; display: flex; align-items: center; gap: 5px;
          padding: 4px clamp(6px,.8vw,12px);
          background: rgba(13,92,69,0.2); border: 1px solid rgba(13,92,69,0.4);
          border-radius: 20px; font-size: clamp(9px,.9vw,11px);
          color: rgba(255,255,255,0.75); font-family: 'DM Sans',sans-serif;
          font-weight: 500; white-space: nowrap; flex-shrink: 0;
        }
        @keyframes blink { 0%,100%{opacity:1;}50%{opacity:.3;} }

        /* ── Messages ───────────────────────────────────────────────────── */
        .chat-msgs {
          flex: 1; overflow-y: auto;
          padding: clamp(12px,2vh,22px) 0 clamp(8px,1vh,14px);
          display: flex; flex-direction: column; gap: 0;
          scrollbar-width: thin; scrollbar-color: #e4e8ef transparent;
          background: #f8f9fb;
        }
        .chat-msgs::-webkit-scrollbar { width: 4px; }
        .chat-msgs::-webkit-scrollbar-thumb { background: #e2e6f0; border-radius: 4px; }

        .msg-row { padding: clamp(3px,.5vh,7px) 0; display: flex; animation: messageAppear .12s ease-out backwards; }
        @keyframes messageAppear { from { opacity: 0; } to { opacity: 1; } }
        .msg-row--user { justify-content: flex-end; }
        .msg-row--bot  { justify-content: flex-start; }

        .msg-inner {
          display: flex; gap: clamp(6px,.8vw,13px); align-items: flex-start;
          max-width: min(820px, 92%);
          padding: 0 clamp(10px,1.5vw,26px);
          width: 100%;
        }
        .msg-row--user .msg-inner { flex-direction: row-reverse; margin-left: auto; }
        .msg-row--bot  .msg-inner { margin-right: auto; }

        .msg-avatar {
          width: clamp(26px,2.8vw,34px); height: clamp(26px,2.8vw,34px);
          border-radius: 10px; flex-shrink: 0;
          display: flex; align-items: center; justify-content: center;
          margin-top: 2px; overflow: hidden;
        }
        .msg-avatar--bot { background: linear-gradient(135deg,#0d4f3c,#1a1f4e); border: 1px solid rgba(13,79,60,.2); box-shadow: 0 2px 6px rgba(13,79,60,.18); }
        .msg-avatar--user { background: linear-gradient(135deg,#e8eaf5,#d4d8ec); border: 1px solid #c4c9e0; }

        .msg-bubble { display: flex; flex-direction: column; gap: 4px; min-width: 0; flex: 1; }
        .msg-text {
          padding: clamp(9px,1.2vh,13px) clamp(11px,1.4vw,17px);
          border-radius: 18px;
          font-size: clamp(12.5px,1.2vw,14px); line-height: 1.68; word-break: break-word;
        }
        .msg-row--user .msg-text {
          background: linear-gradient(135deg,#1a1f4e,#252b68); color: #fff;
          border-bottom-right-radius: 5px; box-shadow: 0 2px 10px rgba(26,31,78,.2);
        }
        .msg-row--bot .msg-text {
          background: #ffffff; color: #1a1f4e;
          border: 1px solid #e2e6f0; border-bottom-left-radius: 5px;
          box-shadow: 0 1px 4px rgba(26,31,78,.05);
        }
        .msg-ts { font-size: clamp(9px,.85vw,11px); color: #b0b8d4; padding: 0 4px; }
        .msg-row--user .msg-ts { text-align: right; }
        .followup-chips { display:flex; flex-wrap:wrap; gap:6px; margin-top:10px; }
        .followup-label { font-size:10px; font-weight:600; color:#8892b0; text-transform:uppercase; letter-spacing:.06em; flex-basis:100%; margin-bottom:3px; }
        .followup-chip {
          display:inline-flex; align-items:center; gap:6px;
          background:#fff; border:1.5px solid #e2e6f0;
          border-radius:50px; padding:6px 13px;
          font-size:clamp(11px,1vw,12.5px); color:#1a1f4e;
          cursor:pointer; font-family:inherit; text-align:left; line-height:1.3;
          transition:background .15s, border-color .15s, box-shadow .15s, transform .1s;
          box-shadow:0 1px 3px rgba(26,31,78,.07);
        }
        .followup-chip:hover { background:#f0f3ff; border-color:#c7d0f0; box-shadow:0 2px 8px rgba(26,31,78,.12); transform:translateY(-1px); }
        .followup-chip:active { transform:translateY(0); }
        .followup-chip-icon { font-size:13px; line-height:1; }

        .msg-text--streaming::after { content:'▍'; display:inline-block; margin-left:2px; color:#2563eb; font-size:.9em; animation:textCaretBlink .85s steps(1,end) infinite; }
        @keyframes textCaretBlink { 50% { opacity:0; } }
        @media (prefers-reduced-motion: reduce) {
          .msg-text--streaming::after { animation:none; opacity:1; }
          .search-dots span, .typing-dot, .typing-dots .typing-dot { animation:none; }
        }

        /* Markdown */
        .msg-text .md-p { margin: 0 0 8px; }
        .msg-text .md-p:last-child { margin-bottom: 0; }
        .msg-text .md-h1,.msg-text .md-h2,.msg-text .md-h3 { font-family: 'Playfair Display',serif; color: #1a1f4e; margin: 11px 0 5px; }
        .msg-text .md-h1{font-size:clamp(14px,1.5vw,17px);} .msg-text .md-h2{font-size:clamp(13px,1.3vw,15px);} .msg-text .md-h3{font-size:clamp(12px,1.2vw,13.5px);}
        .msg-text .md-ul { margin: 4px 0 8px 16px; padding: 0; list-style: disc; }
        .msg-text .md-li { margin: 3px 0; }
        .msg-text .md-code { background: rgba(26,31,78,.06); padding: 1px 6px; border-radius: 4px; font-family: 'DM Mono',monospace; font-size: clamp(11px,1vw,12.5px); }
        .msg-text .md-ref { color: #3b82f6; font-size: 10px; }
        .msg-text .md-table { border-collapse: collapse; margin: 8px 0; width: 100%; font-size: clamp(11px,1vw,12.5px); display: block; overflow-x: auto; }
        .msg-text .md-table thead tr { background: #1a1f4e; }
        .msg-text .md-th { background: linear-gradient(90deg,#0d4f3c,#1a1f4e); color: #fff; font-weight: 600; font-size: clamp(10px,.9vw,11px); text-transform: uppercase; letter-spacing: .04em; border: 1px solid #1a3a30; padding: clamp(4px,.6vh,7px) clamp(6px,.8vw,10px); text-align: left; }
        .msg-text .md-td { border: 1px solid #e2e6f0; padding: clamp(4px,.5vh,6px) clamp(6px,.8vw,10px); }
        .msg-text .md-table tbody tr:nth-child(even) td { background: #f8f9fc; }
        .msg-text .md-table tbody tr { transition: background .12s ease; }
        .msg-text .md-table tbody tr:hover td { background: #f0f3ff; }

        /* Status indicators */
        .searching {
          display: flex; align-items: center; gap: 7px;
          padding: 10px 14px; background: rgba(13,79,60,.07);
          border: 1px solid rgba(13,79,60,.18); border-radius: 12px;
          font-size: clamp(11px,1vw,12.5px); color: #0d4f3c; border-bottom-left-radius: 4px;
        }
        .searching svg { animation: spin 1.2s linear infinite; }
        @keyframes spin { to{transform:rotate(360deg);} }
        .search-dots { display: flex; gap: 3px; }
        .search-dots span { width: 4px; height: 4px; border-radius: 50%; background: #0d4f3c; animation: bd .9s ease-in-out infinite; }
        .search-dots span:nth-child(2){animation-delay:.15s;} .search-dots span:nth-child(3){animation-delay:.3s;}
        @keyframes bd{0%,60%,100%{transform:translateY(0);}30%{transform:translateY(-5px);}}

        .typing-wrap { display: flex; align-items: center; gap: 12px; padding: 6px 0; }
        .typing-dots { display: flex; gap: 4px; padding: 12px 16px; background: #fff; border: 1px solid #e2e6f0; border-radius: 18px; border-bottom-left-radius: 5px; box-shadow: 0 1px 4px rgba(26,31,78,.05); }
        .typing-dot { width: 6px; height: 6px; border-radius: 50%; background: #1a1f4e; animation: bd .9s ease-in-out infinite; }
        .typing-dot:nth-child(2){animation-delay:.15s;} .typing-dot:nth-child(3){animation-delay:.3s;}

        .web-search-chip {
          display: inline-flex; align-items: center; gap: 4px; margin-top: 7px; padding: 3px 8px;
          background: #eff6ff; border: 1px solid #bfdbfe; border-radius: 20px;
          font-size: 11px; color: #1d4ed8; font-family: 'DM Sans',sans-serif; font-weight: 500;
        }

        /* Report */
        .report-wrap { margin-top: 7px; }
        .inline-chart-wrap { margin: 10px 0; background: #f8f9fc; border-radius: 10px; border: 1px solid #e2e6f0; padding: 4px 6px 6px; overflow: hidden; }
        .inline-chart-wrap .chart-wrap { margin: 0; background: transparent; border: none; box-shadow: none; padding: 4px 0 0; }
        .inline-report-chart { margin: 18px 0; }
        .inline-report-image { margin: 18px 0; text-align: center; }
        .inline-report-image img { max-width: 100%; max-height: 360px; border-radius: 10px; border: 1px solid #e2e6f0; box-shadow: 0 1px 4px rgba(26,31,78,.06); }
        .inline-report-image figcaption { margin-top: 6px; font-size: 11px; color: #8b93b5; font-style: italic; font-family: 'DM Sans',sans-serif; }
        .inline-charts-section { margin-bottom: 10px; border: 1px solid #e2e6f0; border-radius: 12px; overflow: hidden; background: #f8f9fc; }
        .inline-charts-label { padding: 7px 12px 4px; font-size: 10px; font-weight: 700; color: #8b93b5; text-transform: uppercase; letter-spacing: .06em; font-family: 'DM Sans',sans-serif; }
        .inline-charts-section .charts-grid { padding: 0 6px 8px; gap: 8px; }
        .inline-charts-section .chart-wrap { background: #fff; box-shadow: 0 1px 4px rgba(26,31,78,.06); }
        .dw-chart-wrap { padding: 6px 6px 4px !important; }
        /* Legacy expanded picker styles retained only for backward-compatible report panels. */
        .composer-report-tier { display:none; width:100%; margin:3px 0 8px; padding:8px 10px; border:1px solid #e2e6f0; border-radius:11px; background:linear-gradient(180deg,#fbfcfe,#f7f8fc); font-family:'DM Sans',sans-serif; }
        .composer-report-tier-title { font-size:9.5px; font-weight:800; color:#5d6682; text-transform:uppercase; letter-spacing:.08em; margin-bottom:6px; }
        .composer-report-tier-options { display:flex; gap:6px; width:100%; }
        .composer-report-tier-option { flex:1 1 0; min-width:0; display:flex; align-items:center; gap:7px; padding:7px 8px; border:1px solid #dde2ee; border-radius:9px; background:#fff; color:#5b6480; cursor:pointer; text-align:left; transition:border-color .15s ease,background .15s ease,box-shadow .15s ease,transform .15s ease; font-family:'DM Sans',sans-serif; }
        .composer-report-tier-option:hover { border-color:#aeb7d1; background:#f8fafc; transform:translateY(-1px); }
        .composer-report-tier-option.is-selected { border-color:#0d4f3c; background:#eef8f4; color:#0d4f3c; box-shadow:0 0 0 1px rgba(13,79,60,.08); }
        .composer-report-tier-dot { width:10px; height:10px; border-radius:50%; border:1.5px solid #b8c0d4; background:#fff; flex:0 0 auto; }
        .composer-report-tier-option.is-selected .composer-report-tier-dot { border-color:#0d4f3c; box-shadow:inset 0 0 0 2.5px #eef8f4; background:#0d4f3c; }
        .composer-report-tier-copy { min-width:0; display:flex; flex-direction:column; gap:1px; }
        .composer-report-tier-label { font-size:10.5px; font-weight:800; line-height:1.1; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
        .composer-report-tier-pages { font-size:8.5px; color:#7d859b; line-height:1.1; }
        .composer-report-tier-option.is-selected .composer-report-tier-pages { color:#4f6e63; }
        .composer-report-tier-option:focus-visible { outline:2px solid #1a1f4e; outline-offset:2px; }
        @media (max-width:640px) { .composer-report-tier-options { flex-direction:column; } .composer-report-tier-option { width:100%; } }

        .report-tier-picker { width:100%; display:flex; flex-direction:column; gap:7px; margin:3px 0 2px; font-family:'DM Sans',sans-serif; }
        .report-tier-picker-head { display:flex; align-items:baseline; justify-content:space-between; gap:10px; flex-wrap:wrap; }
        .report-tier-picker-label { font-size:10px; font-weight:800; color:#26304f; text-transform:uppercase; letter-spacing:.07em; }
        .report-tier-picker-hint { font-size:9.5px; color:#7a8198; }
        .report-tier-options { display:flex; gap:7px; flex-wrap:wrap; width:100%; }
        .report-tier-option { position:relative; min-width:150px; flex:1 1 0; min-height:76px; display:flex; flex-direction:column; align-items:flex-start; gap:5px; padding:9px 30px 9px 10px; border:1px solid #dfe3ef; border-radius:10px; background:#f8f9fc; color:#4b5680; cursor:pointer; text-align:left; transition:border-color .16s ease,background .16s ease,box-shadow .16s ease,transform .16s ease; font-family:'DM Sans',sans-serif; }
        .report-tier-option:hover { border-color:#aeb7d1; background:#f3f5fa; transform:translateY(-1px); }
        .report-tier-option.is-selected { border-color:#0d4f3c; background:#eef8f4; box-shadow:0 0 0 1px rgba(13,79,60,.10),0 3px 10px rgba(13,79,60,.08); color:#0d4f3c; }
        .report-tier-option-top { display:flex; align-items:center; justify-content:space-between; gap:8px; width:100%; }
        .report-tier-option-label { font-size:11.5px; font-weight:800; line-height:1.2; }
        .report-tier-option-pages { font-size:9.5px; font-weight:700; color:#7a8198; line-height:1.2; white-space:nowrap; }
        .report-tier-option.is-selected .report-tier-option-pages { color:#3d6d5e; }
        .report-tier-option-description { font-size:9.5px; line-height:1.35; color:#737b93; }
        .report-tier-option.is-selected .report-tier-option-description { color:#4f6e63; }
        .report-tier-option-radio { position:absolute; top:10px; right:10px; width:13px; height:13px; border:1.5px solid #b7bfd3; border-radius:50%; display:grid; place-items:center; background:#fff; }
        .report-tier-option-radio span { width:6px; height:6px; border-radius:50%; background:transparent; }
        .report-tier-option.is-selected .report-tier-option-radio { border-color:#0d4f3c; }
        .report-tier-option.is-selected .report-tier-option-radio span { background:#0d4f3c; }
        .report-tier-option:focus-visible { outline:2px solid #1a1f4e; outline-offset:2px; }
        @media (max-width:640px) { .report-tier-option { min-width:96px; min-height:72px; } .report-tier-picker-hint { width:100%; } }
        .report-btn { display:flex;align-items:center;gap:5px;background:linear-gradient(135deg,#0d4f3c,#1a1f4e);border:none;border-radius:7px;padding:6px 12px;font-size:clamp(10px,.9vw,11.5px);color:#fff;cursor:pointer;font-family:'DM Sans',sans-serif;transition:box-shadow .18s cubic-bezier(.4,0,.2,1),transform .18s cubic-bezier(.4,0,.2,1);box-shadow:0 2px 8px rgba(13,79,60,.25); }
        .report-btn:hover:not(:disabled) { box-shadow:0 5px 16px rgba(13,79,60,.35); transform:translateY(-1px); }
        .report-btn:active:not(:disabled) { transform:translateY(0); }
        .report-btn:disabled { opacity:.75; cursor:not-allowed; }
        .report-context-toggle { position:relative;display:flex;align-items:center;gap:6px;cursor:pointer;user-select:none;font-family:'DM Sans',sans-serif; }
        .report-context-toggle input { position:absolute;opacity:0;width:0;height:0; }
        .report-context-track { position:relative;width:28px;height:16px;border-radius:999px;background:#d7dbea;transition:background .15s;flex-shrink:0; }
        .report-context-thumb { position:absolute;top:2px;left:2px;width:12px;height:12px;border-radius:50%;background:#fff;box-shadow:0 1px 2px rgba(0,0,0,.25);transition:transform .15s; }
        .report-context-toggle input:checked + .report-context-track { background:#0d4f3c; }
        .report-context-toggle input:checked + .report-context-track .report-context-thumb { transform:translateX(12px); }
        .report-context-toggle input:focus-visible + .report-context-track { outline:2px solid #1a1f4e;outline-offset:2px; }
        .report-context-label { font-size:clamp(10px,.85vw,11px);color:#5a6178; }
        .report-prompt-overlay { position:fixed; inset:0; z-index:10050; display:flex; align-items:center; justify-content:center; padding:18px; background:rgba(15,23,42,.46); backdrop-filter:blur(4px); }
        .report-prompt-modal { width:min(560px,96vw); border-radius:16px; background:#fff; box-shadow:0 24px 70px rgba(15,23,42,.24); overflow:hidden; border:1px solid #dfe5e2; font-family:'DM Sans',sans-serif; }
        .report-prompt-head { display:flex; justify-content:space-between; gap:18px; padding:18px 20px; background:#17384f; color:#fff; }
        .report-prompt-kicker { font-size:8.5px; font-weight:800; text-transform:uppercase; letter-spacing:.12em; color:#a9c6b7; }
        .report-prompt-title { margin-top:3px; font-size:20px; font-weight:800; }
        .report-prompt-subtitle { margin-top:4px; font-size:11px; line-height:1.4; color:rgba(255,255,255,.72); }
        .report-prompt-close { width:30px; height:30px; border:0; border-radius:8px; background:rgba(255,255,255,.10); color:#fff; cursor:pointer; font-size:20px; line-height:1; flex:0 0 auto; }
        .report-prompt-body { padding:18px 20px 20px; }
        .report-prompt-label { display:block; margin-bottom:7px; font-size:10px; font-weight:800; color:#52616b; text-transform:uppercase; letter-spacing:.07em; }
        .report-prompt-textarea { width:100%; box-sizing:border-box; resize:vertical; min-height:118px; border:1px solid #d7e0dc; border-radius:11px; padding:12px 13px; font:13px/1.55 'DM Sans',sans-serif; color:#1d2b33; outline:none; background:#fbfdfc; }
        .report-prompt-section-select { width:100%; box-sizing:border-box; border:1px solid #d7e0dc; border-radius:10px; padding:10px 11px; font:12px/1.35 'DM Sans',sans-serif; color:#1d2b33; background:#fbfdfc; outline:none; }
        .report-prompt-section-select:focus { border-color:#387c58; box-shadow:0 0 0 3px rgba(56,124,88,.10); }
        .report-prompt-textarea:focus { border-color:#387c58; box-shadow:0 0 0 3px rgba(56,124,88,.10); }
        .report-prompt-hint { margin-top:7px; font-size:10px; line-height:1.45; color:#7a8790; }
        .report-prompt-actions { display:flex; justify-content:flex-end; gap:8px; margin-top:15px; }
        .report-prompt-cancel, .report-prompt-submit { border-radius:9px; padding:9px 14px; border:1px solid #d7e0dc; font:700 12px 'DM Sans',sans-serif; cursor:pointer; }
        .report-prompt-cancel { background:#f7faf8; color:#52616b; }
        .report-prompt-submit { color:#fff; border-color:#17384f; background:#17384f; }
        .report-prompt-submit.is-experiment { background:#387c58; border-color:#387c58; }
        .report-prompt-submit:disabled, .report-prompt-cancel:disabled { opacity:.55; cursor:not-allowed; }
        .report-btn--experiment { background:#f0f8f3 !important; color:#21613f !important; border-color:#cfe4d8 !important; }
        .report-btn--action { background:#eef5f8 !important; color:#24536a !important; border-color:#cfdee6 !important; }
        .report-edit-control { min-width:92px; justify-content:center; font-weight:800; border-width:1px !important; box-shadow:none !important; }
        .report-edit-control:hover:not(:disabled) { box-shadow:0 3px 10px rgba(15,23,42,.10) !important; }
        .report-tier-status { display:inline-flex; align-items:center; gap:5px; padding:4px 8px; border:1px solid #e2e6f0; border-radius:999px; background:#f8fafc; color:#6f7890; font:600 10px 'DM Sans',sans-serif; }
        .report-tier-status-label { color:#9aa2b4; text-transform:uppercase; letter-spacing:.06em; font-size:8px; font-weight:800; }
        .report-tier-status strong { color:#0d4f3c; font-size:10px; }
        .report-tier-status span:last-child { color:#8a93a8; font-size:9px; }
        .report-body { margin-top:8px;background:#f8f9fc;border:1px solid #e2e6f0;border-radius:12px;padding:clamp(10px,2vw,18px);max-height:clamp(280px,40vh,560px);overflow-y:auto;animation:fadeUp .2s cubic-bezier(.4,0,.2,1); }
        .report-loading { display:flex;align-items:center;gap:8px;font-size:12px;color:#8b93b5;font-family:'DM Sans',sans-serif; }
        .dots { display:inline-flex;gap:4px; }
        .dots i { width:6px;height:6px;border-radius:50%;background:#8b93b5;animation:bd .9s ease-in-out infinite;display:block; }
        .dots i:nth-child(2){animation-delay:.15s;} .dots i:nth-child(3){animation-delay:.3s;}
        .report-content { font-size:clamp(12px,1.1vw,13.5px);line-height:1.65;color:#1a1f4e;font-family:'DM Sans',sans-serif; }
        .report-content .md-p{margin:0 0 10px;text-align:justify;}
        .report-content .md-h1,.report-content .md-h2,.report-content .md-h3{font-family:'Playfair Display',serif;color:#1a1f4e;margin:14px 0 6px;}
        .report-content .md-h1{font-size:clamp(15px,1.6vw,18px);} .report-content .md-h2{font-size:clamp(13px,1.3vw,15px);} .report-content .md-h3{font-size:clamp(12px,1.1vw,13px);}
        .report-content .md-ul{margin:4px 0 10px 16px;list-style:disc;}
        .report-content .md-li{margin:3px 0;text-align:justify;}
        .report-content .md-table{border-collapse:collapse;margin:10px 0;width:100%;font-size:clamp(11px,.95vw,12.5px);display:block;overflow-x:auto;}
        .report-content .md-th{background:linear-gradient(90deg,#0d4f3c,#1a1f4e);color:#fff;font-weight:600;font-size:clamp(10px,.85vw,11px);text-transform:uppercase;letter-spacing:.04em;border:1px solid #1a3a30;padding:clamp(5px,.7vh,8px) clamp(7px,.9vw,11px);text-align:left;}
        .report-content .md-td{border:1px solid #e2e6f0;padding:clamp(5px,.6vh,7px) clamp(7px,.9vw,11px);}
        .report-content .md-table tbody tr:nth-child(even) td{background:#f8f9fc;}
        .report-sources{margin-top:24px;padding-top:16px;border-top:1px solid #dce1ed;}
        .report-sources h2{font:700 clamp(13px,1.3vw,16px) 'Playfair Display',serif;color:#1a1f4e;margin:0 0 4px;}
        .report-sources h2 span{display:inline-grid;place-items:center;min-width:21px;height:21px;padding:0 6px;border-radius:999px;background:#e8efff;color:#0d4f3c;font:700 11px 'DM Sans',sans-serif;vertical-align:middle;}
        .report-sources>p{margin:0 0 10px;color:#68708c;font-size:11px;}
        .report-sources ol{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:7px;margin:0;padding:0;list-style:none;counter-reset:source;}
        .report-sources li{position:relative;min-width:0;padding:9px 10px 9px 32px;border:1px solid #e2e6f0;border-radius:8px;background:#fff;counter-increment:source;}
        .report-sources li::before{content:counter(source);position:absolute;left:9px;top:10px;width:16px;height:16px;border-radius:50%;display:grid;place-items:center;background:#0d4f3c;color:#fff;font:700 9px 'DM Sans',sans-serif;}
        .report-source-title{font-size:11px;font-weight:700;line-height:1.35;color:#1a1f4e;overflow-wrap:anywhere;}
        .report-source-meta{margin-top:2px;font-size:9.5px;color:#7b849f;overflow-wrap:anywhere;}
        .report-sources a,.report-source-provided{display:block;margin-top:4px;font-size:10px;color:#0d4f3c;text-decoration:none;overflow-wrap:anywhere;}
        .report-sources a:hover{text-decoration:underline;}


        .report-section-shell { margin: 0 0 clamp(12px,1.8vh,22px); }
        .report-section-shell.report-layout-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:10px; }
        .report-section-shell.report-layout-two_column { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:18px; }
        .report-section-shell.report-layout-sidebar_main { display:grid; grid-template-columns:minmax(120px,.28fr) minmax(0,1fr); gap:16px; }
        .report-section-shell.report-density-dense { font-size:.96em; }
        .report-section-shell.report-density-sparse { max-width:90%; }
        .report-section-shell.report-emphasis-critical { border-left:4px solid currentColor; padding-left:10px; }
        .report-section-shell.report-density-sparse { max-width: 88%; }
        .report-section-shell.report-density-dense { margin-bottom: 9px; }
        .report-section-shell.report-layout-two_column { column-count: 2; column-gap: 22px; }
        .report-section-shell.report-layout-two_column > * { break-inside: avoid; }
        .report-section-shell.report-layout-sidebar_main { border-left: 3px solid #cfd8e8; padding-left: 12px; }
        .report-section-shell.report-layout-grid { background: rgba(255,255,255,.45); border: 1px solid #e2e6f0; border-radius: 10px; padding: 10px; }
        .report-section-shell.report-emphasis-high { border-top: 2px solid #aeb8d0; padding-top: 7px; }
        .report-section-shell.report-emphasis-critical { border-left: 4px solid #b91c1c; padding-left: 10px; }
        .report-section-shell.report-type-timeline .md-h1, .report-section-shell.report-type-timeline .md-h2 { letter-spacing: .01em; }
        .report-section-shell.report-type-comparison.report-layout-two_column { column-rule: 1px solid #dfe4ee; }

        /* Charts */
        .charts-grid { display:grid;grid-template-columns:repeat(auto-fit,minmax(min(240px,100%),1fr));gap:clamp(8px,1.2vw,16px);margin-bottom:clamp(10px,1.5vh,20px); }
        .key-stats-row { display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:8px;margin:0 0 clamp(12px,1.5vh,18px); }
        .key-stats--fallback { opacity:.96; }
        .key-stats--scientific { grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); }
        .key-stats--regulatory { grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); }
        .key-stats--comparison { grid-template-columns:repeat(auto-fit,minmax(145px,1fr)); }
        .key-stat-card {
          background:#fff;border:1px solid #e2e6f0;border-radius:10px;
          padding:clamp(8px,1.2vh,14px) clamp(10px,1.4vw,18px);min-width:80px;flex:1;
          transition:box-shadow .2s cubic-bezier(.4,0,.2,1), transform .2s cubic-bezier(.4,0,.2,1), border-color .2s cubic-bezier(.4,0,.2,1);
        }
        .key-stat-card:hover { box-shadow:0 6px 18px rgba(26,31,78,.09); border-color:#d5dbe8; transform:translateY(-1px); }
        .key-stat-label { font-size:clamp(9px,.85vw,10.5px);text-transform:uppercase;letter-spacing:.07em;color:#8b93b5;margin-bottom:4px; }
        .key-stat-value { font-size:clamp(14px,1.5vw,18px);font-weight:700;color:#1a1f4e;line-height:1; }
        .key-stat-change { font-size:clamp(10px,.9vw,11.5px);margin-top:4px;font-weight:600; }
        .key-stat-change.pos { color:#16a34a; } .key-stat-change.neg { color:#dc2626; }
        .chart-wrap {
          background:#fff;border:1px solid #e2e6f0;border-radius:10px;
          padding:clamp(10px,1.3vw,16px) clamp(10px,1.3vw,16px) 8px;
          transition:box-shadow .2s cubic-bezier(.4,0,.2,1);
        }
        .chart-wrap:hover { box-shadow:0 6px 18px rgba(26,31,78,.07); }
        .chart-title { font-size:clamp(10px,.9vw,11.5px);font-weight:600;color:#1a1f4e;font-family:'DM Sans',sans-serif;margin-bottom:8px; }
        .chart-legend { display:flex;flex-wrap:wrap;gap:8px;margin-top:6px; }
        .chart-leg { display:flex;align-items:center;gap:4px;font-size:10px;color:#4b5680;font-family:'DM Sans',sans-serif; }
        .chart-leg i { width:8px;height:8px;border-radius:2px;display:block; }

        /* ── Welcome screen ─────────────────────────────────────────────── */
        .chat-welcome {
          flex: 1; display: flex; flex-direction: column;
          align-items: center; justify-content: center;
          gap: clamp(12px,2vh,22px);
          padding: clamp(16px,3vw,28px) clamp(14px,3vw,28px) clamp(10px,1.5vh,16px);
          text-align: center; background: #f8f9fb;
          overflow-y: auto;
        }
        .welcome-glow { display: none; }
        .welcome-title {
          font-size: clamp(18px,3vw,26px); font-weight: 800;
          background: linear-gradient(135deg,#0d5c45 20%,#1a1f4e 80%);
          -webkit-background-clip: text; -webkit-text-fill-color: transparent;
          font-family: 'Playfair Display',serif; margin: 0; letter-spacing: -.3px;
        }
        .welcome-sub {
          font-size: clamp(11.5px,1.2vw,13px); color: #64748b;
          line-height: 1.6; margin: 2px 0 0; max-width: min(440px, 90%);
        }
        .sugs { display:flex;flex-wrap:wrap;gap:clamp(4px,.5vw,8px);justify-content:center;max-width:min(680px,96%); }
        .sug {
          display:flex;align-items:center;gap:5px;
          padding:clamp(5px,.7vh,8px) clamp(8px,1vw,13px);
          border-radius:20px;border:1.5px solid #d8e4de;
          background:#fff;cursor:pointer;
          font-size:clamp(10.5px,1vw,12px);color:#1a2035;
          transition:all .15s;font-family:'DM Sans',sans-serif;
          box-shadow:0 1px 3px rgba(13,92,69,0.05);
        }
        .sug:hover { border-color:#0d5c45;background:#f0f8f4;transform:translateY(-1px);box-shadow:0 3px 8px rgba(13,92,69,0.1); }
        .sug-icon { font-size:clamp(11px,1.1vw,14px);line-height:1;font-family:'Apple Color Emoji','Segoe UI Emoji','Noto Color Emoji',sans-serif; }

        /* ── Input area ─────────────────────────────────────────────────── */
        .chat-input-area {
          padding: clamp(8px,1.2vh,14px) clamp(10px,2vw,26px) clamp(10px,1.5vh,20px);
          background: #ffffff; border-top: 1px solid rgba(26,31,78,.09);
          flex-shrink: 0; display: flex; flex-direction: column; gap: clamp(5px,.7vh,9px);
          box-shadow: 0 -2px 12px rgba(26,31,78,.04);
        }
        .input-row {
          display: flex; align-items: flex-end; gap: clamp(6px,.8vw,11px);
          background: #f8faf9; border: 1.5px solid #ccddd6; border-radius: 16px;
          padding: clamp(8px,1.1vh,12px) clamp(8px,1vw,12px) clamp(8px,1.1vh,12px) clamp(12px,1.5vw,20px);
          transition: border-color .15s, box-shadow .15s;
          max-width: min(820px, 100%); margin: 0 auto; width: 100%;
        }
        .input-row:focus-within { border-color: #0d4f3c; box-shadow: 0 0 0 3px rgba(13,79,60,.07); }
        .chat-textarea {
          flex: 1; background: none; border: none; outline: none;
          color: #1a1f4e; font-size: clamp(13px,1.2vw,15px); font-family: 'DM Sans',sans-serif;
          line-height: 1.5; resize: none;
          max-height: clamp(80px,15vh,140px); min-height: 22px;
          scrollbar-width: none;
        }
        .chat-textarea::placeholder { color: #b0b8d4; }
        .chat-textarea::-webkit-scrollbar { display: none; }
        .report-mode-btn {
          min-height:34px; display:inline-flex; align-items:center; justify-content:center; gap:6px;
          padding:0 12px; border:1.5px solid #cbd5e1; border-radius:999px;
          background:#f8fafc; color:#64748b; cursor:pointer; white-space:nowrap; flex-shrink:0;
          font-family:'DM Sans',sans-serif; font-size:12px; font-weight:650;
          box-shadow:none;
          transition:background .16s ease,border-color .16s ease,box-shadow .16s ease,color .16s ease;
        }
        /* Inactive Report mode stays neutral: no glow or shadow. */
        .report-mode-btn:hover:not(:disabled):not(.is-active) { background:#f1f5f9; border-color:#94a3b8; box-shadow:none; }
        /* The glow is tied only to the enabled state, which is driven by reportModeEnabled. */
        .report-mode-btn.is-active {
          background:#eaf2ff; color:#1d4ed8; border-color:#2563eb;
          box-shadow:0 0 0 1px rgba(37,99,235,.38),0 0 12px rgba(59,130,246,.48),0 0 24px rgba(59,130,246,.28);
          animation:report-mode-glow 2.2s ease-in-out infinite;
        }
        .report-mode-btn.is-active:hover:not(:disabled) {
          box-shadow:0 0 0 1px rgba(37,99,235,.48),0 0 15px rgba(59,130,246,.56),0 0 28px rgba(59,130,246,.34);
        }
        @keyframes report-mode-glow {
          0%,100% { box-shadow:0 0 0 1px rgba(37,99,235,.30),0 0 8px rgba(59,130,246,.32),0 0 18px rgba(59,130,246,.20); }
          50% { box-shadow:0 0 0 1px rgba(37,99,235,.48),0 0 14px rgba(59,130,246,.54),0 0 28px rgba(59,130,246,.34); }
        }
        .report-mode-btn:disabled { opacity:.45; cursor:not-allowed; }
        .report-mode-btn:focus-visible { outline:2px solid #2563eb; outline-offset:3px; }

        .send-btn {
          width: clamp(34px,3.5vw,40px); height: clamp(34px,3.5vw,40px);
          border-radius: 11px; border: none;
          background: linear-gradient(135deg,#0d4f3c,#0f5c47); color: #fff; cursor: pointer;
          display: flex; align-items: center; justify-content: center;
          flex-shrink: 0; transition: opacity .15s, transform .15s, box-shadow .15s;
          box-shadow: 0 3px 10px rgba(13,79,60,.35);
        }
        .send-btn:disabled{opacity:.22;cursor:not-allowed;box-shadow:none;}
        .send-btn:not(:disabled):hover{transform:scale(1.08);box-shadow:0 5px 16px rgba(13,79,60,.45);}
        .send-btn:not(:disabled):active{transform:scale(.93);}
        .input-hint { font-size: clamp(9px,.85vw,10.5px); color: #b0b8d4; text-align: center; font-family: 'DM Sans',sans-serif; }

        /* ── File chips ─────────────────────────────────────────────────── */
        .chips-row { display:flex;flex-wrap:wrap;gap:6px;padding:clamp(6px,.8vh,12px) clamp(10px,1.5vw,20px) 4px;max-height:90px;overflow-y:auto; }
        .file-chip { display:flex;align-items:center;gap:5px;padding:4px 6px 4px 9px;border-radius:20px;background:#f0f3ff;border:1px solid #c8d0e8;font-family:'DM Sans',sans-serif;flex-shrink:0;max-width:min(220px,42vw); }
        .file-chip--text { background:#f0fdf4;border-color:rgba(34,197,94,0.35); }
        .file-chip--img  { background:#f0fdf4;border-color:rgba(34,197,94,0.35); }
        .chip-dot { width:7px;height:7px;border-radius:50%;flex-shrink:0; }
        .chip-name { font-size:clamp(10.5px,.95vw,12px);color:#1a1f4e;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:clamp(80px,14vw,140px); }
        .chip-size { font-size:10px;color:#8b93b5;font-family:'DM Mono',monospace;flex-shrink:0; }
        .chip-x { width:16px;height:16px;border-radius:50%;border:none;background:transparent;color:#ef4444;cursor:pointer;display:flex;align-items:center;justify-content:center;font-size:14px;line-height:1;font-weight:700;flex-shrink:0;opacity:0.55;transition:opacity .15s,background .15s;padding:0; }
        .chip-x:hover { opacity:1;background:rgba(239,68,68,0.12); }
        @keyframes spin { to { transform:rotate(360deg); } }
        .chip-spinner { width:12px;height:12px;border-radius:50%;flex-shrink:0;border:2px solid #c8d0e8;border-top-color:#1a1f4e;animation:spin .7s linear infinite; }

        /* ── Attach / News buttons ──────────────────────────────────────── */
        .attach-btn {
          width:clamp(28px,3vw,34px);height:clamp(28px,3vw,34px);
          border-radius:9px;border:1.5px solid #ccddd6;background:#f0f8f4;
          color:#0d4f3c;cursor:pointer;flex-shrink:0;
          display:flex;align-items:center;justify-content:center;
          transition:background .15s,color .15s,border-color .15s,box-shadow .15s;
        }
        .attach-btn:hover:not(:disabled) { background:#0d4f3c;color:#fff;border-color:#0d4f3c;box-shadow:0 2px 8px rgba(13,79,60,.25); }
        .attach-btn:disabled { opacity:0.35;cursor:not-allowed; }

        /* Compact AI-chat-style report-depth picker. It sits directly inside
           the composer so the current selection is always visible and can be
           changed before sending a report request. */
        .report-tier-menu-wrap { position:relative; flex:0 0 auto; align-self:flex-end; }
        .report-tier-trigger {
          height:clamp(30px,3vw,36px); min-width:112px; max-width:230px; padding:0 9px;
          display:flex; align-items:center; gap:7px; border:1px solid #d5ddd9; border-radius:10px;
          background:#fff; color:#31445d; cursor:pointer; font-family:'DM Sans',sans-serif;
          transition:border-color .15s, background .15s, box-shadow .15s, transform .15s;
          flex-shrink:0;
        }
        .report-tier-trigger:hover:not(:disabled), .report-tier-trigger.is-open {
          border-color:#9fc0b1; background:#f8fcfa; box-shadow:0 2px 8px rgba(13,79,60,.08);
        }
        .report-tier-trigger:active:not(:disabled) { transform:translateY(1px); }
        .report-tier-trigger:disabled { opacity:.5; cursor:not-allowed; }
        .report-tier-trigger-dot { width:8px; height:8px; border-radius:50%; background:#0d4f3c; flex:0 0 auto; box-shadow:0 0 0 3px #e8f3ee; }
        .report-tier-trigger-text { min-width:0; display:flex; flex-direction:column; align-items:flex-start; line-height:1.05; }
        .report-tier-trigger-kicker { font-size:8px; font-weight:800; color:#8690a5; text-transform:uppercase; letter-spacing:.08em; white-space:nowrap; }
        .report-tier-trigger-text { max-width:100%; overflow:hidden; }
        .report-tier-trigger-value { display:block; max-width:100%; margin-top:2px; font-size:10.5px; font-weight:800; color:#24344e; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
        .report-tier-trigger-sep { color:#a3acbd; font-weight:600; }
        .report-tier-trigger-chevron { margin-left:auto; color:#7c879a; flex:0 0 auto; transition:transform .15s ease; }
        .report-tier-trigger.is-open .report-tier-trigger-chevron { transform:rotate(180deg); }
        .report-tier-menu {
          position:absolute; left:0; bottom:calc(100% + 10px); z-index:80; width:min(340px,86vw);
          max-height:min(70vh,460px); overflow-y:auto; overscroll-behavior:contain;
          padding:10px; border:1px solid #dbe2e8; border-radius:14px; background:rgba(255,255,255,.98);
          box-shadow:0 16px 40px rgba(15,23,42,.18), 0 2px 8px rgba(15,23,42,.08);
          backdrop-filter:blur(12px);
          font-family:'DM Sans',sans-serif;
        }
        .report-tier-menu-head { display:flex; align-items:center; justify-content:space-between; gap:10px; padding:5px 7px 7px; color:#657086; font-size:9px; font-weight:800; text-transform:uppercase; letter-spacing:.08em; }
        .report-tier-menu-head span:last-child { color:#9aa3b2; font-size:8px; font-weight:700; letter-spacing:.04em; text-transform:none; }
        .report-tier-menu-head span:only-child { color:#657086; font-size:9px; font-weight:800; letter-spacing:.08em; text-transform:uppercase; }
        .report-tier-menu-item {
          position:relative; width:100%; display:flex; align-items:flex-start; gap:9px; padding:9px 8px;
          border:1px solid transparent; border-radius:10px; background:transparent; text-align:left; cursor:pointer;
          color:#344056; font-family:'DM Sans',sans-serif; transition:background .15s,border-color .15s,box-shadow .15s;
        }
        .report-tier-menu-item:hover { background:#f5f8f7; border-color:#e2ebe7; }
        .report-tier-menu-item.is-selected { background:#eef8f4; border-color:#cfe4da; box-shadow:inset 3px 0 0 #0d4f3c; }
        .report-tier-menu-radio { width:14px; height:14px; border:1.5px solid #b9c3d0; border-radius:50%; display:grid; place-items:center; background:#fff; margin-top:2px; flex:0 0 auto; }
        .report-tier-menu-item.is-selected .report-tier-menu-radio { border-color:#0d4f3c; }
        .report-tier-menu-radio span { width:6px; height:6px; border-radius:50%; background:transparent; }
        .report-tier-menu-item.is-selected .report-tier-menu-radio span { background:#0d4f3c; }
        .report-tier-menu-copy { min-width:0; display:flex; flex-direction:column; gap:2px; padding-right:18px; }
        .report-tier-menu-label { font-size:12px; font-weight:800; color:#25334c; line-height:1.2; }
        .report-tier-menu-pages { font-size:11px; font-weight:700; color:#0d4f3c; line-height:1.25; }
        .report-tier-menu-description { font-size:11px; line-height:1.4; color:#606c7e; }
        .report-tier-menu-check { position:absolute; right:9px; top:12px; color:#0d4f3c; }
        .report-tier-menu-item:focus-visible { outline:2px solid #1a1f4e; outline-offset:2px; }
        .report-seg { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:4px; padding:3px; border:1px solid #e3e8ee; border-radius:11px; background:#f4f6f8; }
        .report-seg-item { min-width:0; display:flex; flex-direction:column; align-items:center; gap:1px; padding:7px 4px; border:0; border-radius:8px; background:transparent; color:#4a5568; cursor:pointer; font-family:'DM Sans',sans-serif; transition:background .15s, box-shadow .15s, color .15s; }
        .report-seg-item:hover { background:#fff; }
        .report-seg-item.is-selected { background:#fff; color:#0d4f3c; box-shadow:0 1px 3px rgba(15,23,42,.12), 0 0 0 1px #cfe4da; }
        .report-seg-label { font-size:12px; font-weight:800; line-height:1.2; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; max-width:100%; }
        .report-seg-meta { font-size:10px; font-weight:600; color:#7c8799; white-space:nowrap; }
        .report-seg-item.is-selected .report-seg-meta { color:#3d6d5e; }
        .report-tier-menu-note { margin:7px 4px 10px; font-size:11px; line-height:1.4; color:#5f6b7d; }
        .report-style-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:6px; }
        .report-style-card { min-width:0; display:flex; flex-direction:column; align-items:flex-start; gap:3px; padding:9px 10px; border:1px solid #e1e6ec; border-radius:10px; background:#fff; color:#25334c; cursor:pointer; text-align:left; font-family:'DM Sans',sans-serif; transition:border-color .15s, box-shadow .15s, background .15s; }
        .report-style-card:hover { border-color:#b9cfc5; }
        .report-style-card.is-selected { border-color:#0d4f3c; background:#eef8f4; box-shadow:0 0 0 1px rgba(13,79,60,.12); }
        .report-style-desc { font-size:10.5px; color:#6b7686; line-height:1.3; }
        .report-style-swatch { width:100%; height:20px; border-radius:5px; margin-bottom:3px; }
        .report-style-swatch--market_currents { background:linear-gradient(90deg,#0e1f3d 0 62%,#c49a4a 62% 70%,#f4f0e8 70%); }
        .report-style-swatch--feature_article { background:linear-gradient(90deg,#fff 0 55%,#f5a11c 55% 75%,#3b82f6 75%); border:1px solid #e3e6ea; }
        .report-seg-item:focus-visible, .report-style-card:focus-visible, .report-tier-menu-done:focus-visible { outline:2px solid #1a1f4e; outline-offset:2px; }
        .report-tier-menu-done { width:100%; margin-top:10px; padding:8px; border:0; border-radius:9px; background:#0d4f3c; color:#fff; font:700 12px 'DM Sans',sans-serif; cursor:pointer; }
        .report-tier-menu-done:hover { background:#0b4334; }
        body:has(.report-tier-menu) .nfab { opacity:0; pointer-events:none; }
        @media (prefers-reduced-motion: reduce) { .msg-row { animation:none !important; } .report-mode-btn { transition:none; } .report-mode-btn.is-active { animation:none; } }
        @media (max-width:640px) {
          .report-tier-trigger { min-width:42px; width:42px; padding:0; justify-content:center; gap:0; }
          .report-tier-trigger-text { display:none; }
          .report-tier-trigger-chevron { display:none; }
          /* Full-width sheet above the composer; anchoring to the trigger ran off-screen. */
          .report-tier-menu { position:fixed; left:12px; right:12px; width:auto; bottom:calc(var(--gg-chat-bottom, 0px) + 84px); max-height:min(64vh,460px); }
        }

        .news-btn {
          height:clamp(28px,3vw,34px);padding:0 clamp(8px,1vw,13px);
          border-radius:9px;border:1.5px solid #c7ccdc;
          background:#f4f5f9;color:#1a1f4e;cursor:pointer;flex-shrink:0;
          display:flex;align-items:center;gap:5px;
          font-size:clamp(10px,.9vw,11.5px);font-weight:600;
          font-family:'DM Sans',sans-serif;
          transition:background .15s,color .15s,border-color .15s,box-shadow .15s;
        }
        .news-btn:hover { background:#1a1f4e;color:#fff;border-color:#1a1f4e;box-shadow:0 2px 8px rgba(26,31,78,.2); }
        .news-btn--active { background:#1a1f4e;color:#fff;border-color:#1a1f4e; }

        .chat-shell--drag { outline:2.5px dashed #0d4f3c;outline-offset:-2px; }

        /* ══════════════════════════════════════════════════════════════════
           MOBILE SIDEBAR DRAWER
        ══════════════════════════════════════════════════════════════════ */
        .sidebar--mobile {
          position: fixed !important;
          top: 0; left: 0; bottom: 0; z-index: 50;
          height: 100dvh; width: clamp(240px,78vw,300px) !important;
          box-shadow: 4px 0 30px rgba(15,23,42,0.25);
          border-radius: 0 16px 16px 0;
          transition: transform .25s cubic-bezier(.4,0,.2,1) !important;
        }
        .sidebar--mobile.sidebar--closed {
          width: clamp(240px,78vw,300px) !important;
          transform: translateX(-105%) !important;
        }
        .sidebar--mobile:not(.sidebar--closed) {
          transform: translateX(0) !important;
        }

        /* ══════════════════════════════════════════════════════════════════
           BREAKPOINTS
        ══════════════════════════════════════════════════════════════════ */

        /* ── Small phone (≤ 480px) ──────────────────────────────────────── */
        @media (max-width: 480px) {
          .chat-shell { height: calc(100dvh - 108px); border-radius: 8px; }
          .chat-topbar { padding: 7px 10px; gap: 6px; }
          .topbar-web-badge { display: none; }
          .topbar-name { font-size: 12px; }
          .topbar-sub { font-size: 9px; }
          .msg-inner { padding: 0 10px; max-width: 100%; gap: 0; }
          .msg-avatar--bot { display: none; }
          .msg-row--bot .msg-bubble { width: 100%; }
          .msg-text { font-size: 13px; padding: 9px 12px; }
          .chat-welcome { padding: 18px 12px 10px; gap: 12px; justify-content: flex-start; padding-top: 24px; }
          .welcome-title { font-size: 19px; }
          .welcome-sub { font-size: 11.5px; }
          .sugs { justify-content: flex-start; }
          .sug { font-size: 10.5px; padding: 5px 9px; }
          .chat-input-area { padding: 7px 10px 10px; gap: 5px; }
          .input-row { padding: 7px 7px 7px 12px; border-radius: 14px; }
          .chat-textarea { font-size: 15px; }
          .input-hint { display: none; }
          .chips-row { padding: 6px 10px 3px; }
          .key-stat-card { padding: 8px 10px; }
          .key-stat-value { font-size: 14px; }
          .charts-grid { grid-template-columns: 1fr; }
          .report-body { padding: 10px; }
          .report-section-shell.report-layout-two_column { column-count: 1; }
        }

        /* ── Medium phone (481–767px) ───────────────────────────────────── */
        @media (min-width: 481px) and (max-width: 767px) {
          .chat-shell { height: calc(100dvh - 110px); border-radius: 10px; }
          .chat-topbar { padding: 8px 12px; }
          .topbar-web-badge { display: none; }
          .msg-inner { padding: 0 12px; max-width: 100%; }
          .msg-avatar--bot { display: none; }
          .msg-row--bot .msg-bubble { width: 100%; }
          .msg-text { font-size: 13.5px; }
          .chat-welcome { padding: 20px 16px 12px; gap: 14px; }
          .sugs { justify-content: flex-start; }
          .chat-input-area { padding: 8px 12px 12px; }
          .input-hint { display: none; }
          .charts-grid { grid-template-columns: 1fr; }
        }

        /* ── Tablet portrait (768–1023px) ───────────────────────────────── */
        @media (min-width: 768px) and (max-width: 1023px) {
          .chat-shell { height: calc(100dvh - 112px); }
          .sidebar { width: clamp(180px,24vw,230px); }
          .msg-inner { padding: 0 16px; max-width: min(700px, 94%); }
          .topbar-web-badge { padding: 3px 8px; font-size: 9.5px; }
          .chat-input-area { padding: 10px 16px 14px; }
          .charts-grid { grid-template-columns: repeat(auto-fit,minmax(200px,1fr)); }
        }

        /* ── Tablet landscape / small desktop (1024–1279px) ────────────── */
        @media (min-width: 1024px) and (max-width: 1279px) {
          .chat-shell { height: calc(100dvh - 114px); }
          .sidebar { width: clamp(210px,20vw,260px); }
          .msg-inner { max-width: min(760px, 90%); }
        }

        /* ── Large desktop (≥ 1280px) ───────────────────────────────────── */
        @media (min-width: 1280px) {
          .chat-shell { height: calc(100dvh - 116px); }
          .sidebar { width: clamp(240px,18vw,290px); }
          .msg-inner { max-width: min(860px, 88%); }
          .welcome-title { font-size: 28px; }
          .welcome-sub { font-size: 13.5px; }
        }

        /* ── Ultra-wide (≥ 1920px) ──────────────────────────────────────── */
        @media (min-width: 1920px) {
          .chat-shell { max-width: 1600px; margin: 0 auto; }
          .sidebar { width: 300px; }
          .msg-inner { max-width: 920px; }
          .msg-text { font-size: 15px; }
          .welcome-title { font-size: 32px; }
        }

        /* ── Short screens (height < 600px, landscape phones) ───────────── */
        @media (max-height: 600px) {
          .chat-shell { height: calc(100dvh - 88px); }
          .chat-welcome { padding: 10px 14px 8px; gap: 8px; }
          .welcome-title { font-size: 16px; }
          .welcome-sub { display: none; }
          .sugs { max-height: 80px; overflow-y: auto; }
          .chat-input-area { padding: 6px 12px 8px; }
          .sidebar-footer { display: none; }
        }

        /* ── Tall screens (height > 900px) ─────────────────────────────── */
        @media (min-height: 900px) {
          .chat-msgs { padding-top: clamp(24px,3vh,36px); }
          .chat-welcome { gap: 28px; }
        }

        /* ── Premium colour refresh: confident, warm, data-forward ─────── */
        .chat-shell {
          border: 1px solid rgba(181,190,215,.72);
          border-radius: 20px;
          background: linear-gradient(145deg,#fff 0%,#fbfcff 56%,#f8fbff 100%);
          box-shadow: 0 18px 48px rgba(31,43,86,.09), 0 2px 8px rgba(31,43,86,.04);
        }
        .sidebar {
          background: linear-gradient(180deg,#f5f7fc 0%,#f2f5fb 64%,#edf6f3 100%);
          border-right: 1px solid #e0e6f2;
        }
        .sidebar-hdr {
          background: linear-gradient(125deg,#fff 0%,#fbfaff 56%,#f1faf6 100%);
          border-bottom-color: #e3e8f3;
        }
        .sidebar-logo {
          background: linear-gradient(145deg,#e4f8ef,#ede9ff 72%,#fff3d8);
          border-color: rgba(114,126,190,.22);
          box-shadow: 0 4px 12px rgba(58,75,143,.10);
        }
        .sidebar-brand { color: #172744; letter-spacing: -.01em; }
        .sidebar-brand span { color: #7886a1; }
        .new-chat-btn {
          background: linear-gradient(110deg,#087f63 0%,#216f89 51%,#5146a5 100%);
          border-color: transparent;
          box-shadow: 0 7px 18px rgba(38,99,129,.20);
        }
        .new-chat-btn:hover {
          background: linear-gradient(110deg,#079570 0%,#287e9b 51%,#6154bd 100%);
          box-shadow: 0 9px 22px rgba(38,99,129,.24);
        }
        .conv-group-label { color: #8a96b0; letter-spacing: .12em; font-weight: 800; }
        .conv-item { border: 1px solid transparent; transition: background .18s ease,border-color .18s ease,transform .18s ease; }
        .conv-item:hover { background: rgba(104,124,198,.075); border-color: rgba(130,145,196,.14); }
                .conv-item--active {
          background: rgba(14,31,61,.06);
          border: 1px solid rgba(14,31,61,.12);
          border-left: 3px solid #0e1f3d;
          padding-left: 9px;
          box-shadow: none;
        }
        .conv-item--active .conv-item__title { color: #0e1f3d; font-weight: 600; }
        .sidebar-footer {
          background: #f8fafc;
          border-top: 1px solid #e2e8f0;
          color: #64748b;
        }
        .chat-main { background: #f8fafc; }
        .chat-topbar {
          position: relative;
          background: #0e1f3d;
          border-bottom: 1px solid rgba(255,255,255,.10);
          box-shadow: 0 1px 3px rgba(15,23,42,.08);
        }
        .chat-topbar::after { display: none; }
        .chat-topbar > * { position: relative; z-index: 1; }
        .topbar-toggle { background: rgba(255,255,255,.08); border-color: rgba(255,255,255,.15); color: rgba(255,255,255,.85); }
        .topbar-toggle:hover { background: rgba(255,255,255,.16); color: #fff; }
        .topbar-logo { background: #fff; box-shadow: 0 1px 4px rgba(0,0,0,.15); border-radius: 6px; }
        .topbar-name { font-family: 'Playfair Display',Georgia,serif; font-weight: 600; color: #fff; letter-spacing: 0; }
        .topbar-sub { color: rgba(255,255,255,.72); font-size: 11px; }
        .topbar-dot { width: 6px; height: 6px; border-radius: 50%; background: #10b981; box-shadow: 0 0 0 2px rgba(16,185,129,.25); animation: none; }
        .chat-msgs { background: transparent; }
        .chat-welcome {
          position: relative;
          gap: clamp(14px,2vh,22px);
          background: #f8fafc;
          padding-top: clamp(24px,4.5vh,48px);
          padding-bottom: clamp(20px,3vh,32px);
        }
        .chat-welcome::before { display: none; }
        .welcome-kicker {
          display: inline-flex; align-items: center; justify-content: center; gap: 8px;
          color: #c49a4a; font-size: 10px; font-weight: 700;
          letter-spacing: .2em; text-transform: uppercase;
          background: #fff; border: 1px solid #e2e8f0;
          border-radius: 4px; padding: 6px 12px; box-shadow: 0 1px 3px rgba(15,23,42,.04);
          backdrop-filter: none;
        }
        .welcome-kicker-dot { width: 6px; height: 6px; border-radius: 50%; background: #0e1f3d; box-shadow: none; }
        .welcome-kicker-sep { color: #c49a4a; }
        .welcome-intro { max-width: 590px; }
        .welcome-title {
          font-family: 'Playfair Display',Georgia,serif;
          font-size: clamp(26px,3.4vw,38px); line-height: 1.15; font-weight: 700;
          letter-spacing: -.02em;
          color: #0e1f3d;
          background: none; -webkit-text-fill-color: currentColor;
          margin: 0 0 8px;
        }
        .welcome-sub { max-width: 520px; margin: 0 auto; color: #64748b; font-size: clamp(12.5px,1.15vw,14px); line-height: 1.65; }
        .welcome-capabilities { display:flex; flex-wrap:wrap; justify-content:center; gap:8px; max-width:min(720px,100%); margin: 4px 0; }
        .welcome-capability {
          display:inline-flex; align-items:center; gap:6px; border-radius:4px; padding:6px 12px;
          background:#fff; border:1px solid #e2e8f0; color:#334155;
          font-size:11.5px; font-weight:600; box-shadow:0 1px 2px rgba(15,23,42,.03);
        }
        .welcome-capability-icon { width:18px; height:18px; display:inline-flex; align-items:center; justify-content:center; border-radius:4px; background:#f1f5f9; color:#0e1f3d; font-size:12px; }
        .sugs { gap:8px; max-width:min(760px,100%); margin-top: 6px; }
        .sug {
          border:1px solid #e2e8f0; border-radius:6px; background:#fff;
          padding:9px 14px; font-size:12px; font-weight:500; color:#1e293b;
          box-shadow:0 1px 2px rgba(15,23,42,.04); transition:all .15s ease;
        }
        .sug-icon { display:inline-flex; align-items:center; justify-content:center; width:18px; height:18px; border-radius:4px; background:#f8fafc; color:#c49a4a; font-size:12px; }
        .sug:hover { border-color:#c49a4a; background:#fff; color:#0e1f3d; transform:translateY(-1px); box-shadow:0 3px 8px rgba(14,31,61,.08); }
        .msg-row--user .msg-text {
          background: #0e1f3d;
          color: #ffffff;
          border:1px solid #162a4e;
          box-shadow:0 2px 6px rgba(14,31,61,.12);
          border-bottom-right-radius:4px;
          font-size: 14px;
        }
        .msg-row--bot .msg-text {
          background:#fff;
          color:#0f172a; border:1px solid #e2e8f0;
          box-shadow:0 1px 3px rgba(15,23,42,.04);
          border-bottom-left-radius:4px;
          font-size: 14.5px; line-height: 1.65;
        }
        .msg-text .md-h1,.msg-text .md-h2,.msg-text .md-h3 { color:#0e1f3d; font-family:'Playfair Display',Georgia,serif; font-weight:600; }
        .msg-text .md-h2 { padding-bottom:4px; border-bottom:1px solid #e2e8f0; }
        .msg-text .md-th { background:#0e1f3d; color:#fff; font-weight:600; font-size:11px; letter-spacing:.04em; border-color:#162a4e; }
        .msg-text .md-td { border-color:#e2e8f0; }
        .msg-text .md-table tbody tr:nth-child(even) td { background:#f8fafc; }
        .msg-text .md-table tbody tr:hover td { background:#f1f5f9; }
        .key-stat-card {
          border:1px solid #e2e8f0; border-top:3px solid #0e1f3d; border-radius:6px;
          background:#fff;
          box-shadow:0 1px 3px rgba(15,23,42,.04);
        }
        .key-stat-label { color:#64748b; font-size:10px; font-weight:600; text-transform:uppercase; letter-spacing:.06em; }
        .key-stat-value { color:#0e1f3d; font-weight:700; font-family:'JetBrains Mono',monospace; }
        .chart-wrap {
          border:1px solid #e2e8f0; border-radius:8px;
          background:#fff;
          box-shadow:0 1px 3px rgba(15,23,42,.04);
        }
        .chart-wrap:hover { border-color:#cbd5e1; box-shadow:0 3px 8px rgba(15,23,42,.06); }
        .chart-title { color:#0e1f3d; font-weight:700; font-family:'Playfair Display',serif; }
        .chart-legend { gap:10px; }
        .chat-input-area {
          background:#fff;
          border-top:1px solid #e2e8f0; box-shadow:0 -2px 10px rgba(15,23,42,.03);
        }
        .input-row {
          background:#f8fafc;
          border:1px solid #cbd5e1; border-radius:10px;
          box-shadow:none;
        }
        .input-row:focus-within { border-color:#0e1f3d; box-shadow:0 0 0 3px rgba(14,31,61,.08); background:#fff; }
        .chat-textarea { color:#0f172a; font-family:'DM Sans',sans-serif; }
        .send-btn {
          background:#0e1f3d; border:1px solid #162a4e; border-radius:8px; color:#fff;
          box-shadow:0 2px 5px rgba(14,31,61,.15);
        }
        .send-btn:hover:not(:disabled) { background:#162a4e; transform:scale(1.04); box-shadow:0 4px 10px rgba(14,31,61,.22); }
        .report-btn {
          background:#0e1f3d; border:1px solid #162a4e; border-radius:6px; color:#fff;
          padding:8px 13px; font-weight:600;
          box-shadow:0 2px 5px rgba(14,31,61,.15);
        }
        .report-btn:hover:not(:disabled) { background:#162a4e; box-shadow:0 3px 8px rgba(14,31,61,.2); }
        .report-btn--experiment { background:#f8fafc !important; border:1px solid #e2e8f0 !important; color:#0e1f3d !important; }
        .report-btn--action { background:#f1f5f9 !important; border:1px solid #cbd5e1 !important; color:#0e1f3d !important; }
        .report-tier-trigger { border-color:#cbd5e1; background:#fff; border-radius:6px; box-shadow:0 1px 2px rgba(15,23,42,.04); }
        .report-tier-trigger:hover:not(:disabled), .report-tier-trigger.is-open { border-color:#0e1f3d; box-shadow:0 0 0 2px rgba(14,31,61,.08); }
        .report-tier-trigger-dot { background:#0e1f3d; box-shadow:none; border-radius:2px; }
        .report-tier-trigger-kicker { color:#64748b; font-weight:700; }
        .report-tier-trigger-value { color:#0e1f3d; font-weight:700; }
        .report-prompt-modal { border:1px solid #e2e8f0; border-radius:10px; box-shadow:0 16px 40px rgba(15,23,42,.15); }
        .report-prompt-head { background:#0e1f3d; color:#fff; }
        .report-prompt-kicker { color:#c49a4a; }
        .report-sources { border-color:#e0e6f1; background:linear-gradient(145deg,#fff,#f8faff); }
        .followup-chip { border-color:#dfe5f2; color:#2e4669; box-shadow:0 3px 10px rgba(40,62,110,.05); }
        .followup-chip:hover { background:linear-gradient(135deg,#f3fbf8,#f3f1ff); border-color:#bfd2e2; }
        @media (max-width: 680px) {
          .chat-shell { border-radius:16px; box-shadow:0 10px 28px rgba(31,43,86,.08); }
          .chat-welcome { padding-top:26px; }
          .welcome-kicker { font-size:8px; letter-spacing:.10em; padding:7px 10px; }
          .welcome-title { font-size:clamp(24px,7vw,31px); }
          .welcome-capabilities { gap:6px; }
          .welcome-capability { font-size:10px; padding:6px 8px; }
          .sugs { gap:7px; }
          .sug { max-width:100%; }
        }
        @media (max-height: 600px) {
          .welcome-capabilities { display:none; }
          .welcome-kicker { padding:5px 9px; }
          .chat-welcome::before { display:none; }
        }
        @media (prefers-reduced-motion: reduce) {
          .conv-item, .sug, .chart-wrap, .key-stat-card { transition:none !important; }
        }

        /* ════════════════════════════════════════════════════════════════════
           v93 EDITORIAL WORKSPACE — institutional research publication theme.
           Scoped to .chat-shell.gg-premium and appended last, so the earlier
           layers stay intact. Tokens mirror backend/routes/report_design.py.
        ════════════════════════════════════════════════════════════════════ */
        .chat-shell.gg-premium {
          --ed-paper: #FAF9F5; --ed-surface: #FFFFFF; --ed-ink: #2A2A2A; --ed-ink-strong: #1C1C1A; --ed-ink-soft: #4A4843;
          --ed-muted: #6F6B63; --ed-rule: #E1DDD5; --ed-rule-soft: #ECE8E0; --ed-tint: #F3EFE6; --ed-forest: #162721;
          --ed-forest-hover: #24392F; --ed-navy: #0B1B3D; --ed-positive: #1D6B47; --ed-negative: #9A3B2E; --ed-warning: #8A5A12;
          --ed-warning-tint: #FBF3E4; --ed-display: 'Lora', 'Merriweather', Georgia, 'Times New Roman', serif;
          --ed-body: 'Inter', system-ui, -apple-system, 'Segoe UI', Roboto, Arial, sans-serif;
          background: var(--ed-paper) !important; border: 1px solid var(--ed-rule) !important; box-shadow: 0 1px 2px rgba(28,28,26,.04) !important;
          border-radius: 4px !important; font-family: var(--ed-body) !important; color: var(--ed-ink);
        }
        .chat-shell.gg-premium * { font-family: inherit; }
        .chat-shell.gg-premium .ed-overline, .chat-shell.gg-premium .conv-group-label, .chat-shell.gg-premium .welcome-kicker,
        .chat-shell.gg-premium .report-progress-kicker, .chat-shell.gg-premium .report-masthead-kicker, .chat-shell.gg-premium .report-outline-label,
        .chat-shell.gg-premium .followup-label, .chat-shell.gg-premium .report-tier-trigger-kicker, .chat-shell.gg-premium .report-preview-label,
        .chat-shell.gg-premium .report-status-label, .chat-shell.gg-premium .report-alert-label {
          font: 600 10px/1.35 var(--ed-body) !important; letter-spacing: .25em !important; text-transform: uppercase !important; color: var(--ed-muted) !important;
        }

        /* ── sidebar ─────────────────────────────────────────────────────── */
        .chat-shell.gg-premium .sidebar { background: var(--ed-tint) !important; border-right: 1px solid var(--ed-rule) !important; box-shadow: none !important; color: var(--ed-ink) !important; }
        .chat-shell.gg-premium .sidebar::before, .chat-shell.gg-premium .sidebar::after { display: none !important; }
        .chat-shell.gg-premium .sidebar-hdr { border-bottom: 1px solid var(--ed-rule) !important; background: none !important; }
        .chat-shell.gg-premium .sidebar-brand, .chat-shell.gg-premium .sidebar-brand * { color: var(--ed-ink-strong) !important; }
        .chat-shell.gg-premium .sidebar-brand > :first-child { font-family: var(--ed-display) !important; font-weight: 500 !important; }
        .chat-shell.gg-premium .sidebar-logo { background: var(--ed-surface) !important; border: 1px solid var(--ed-rule) !important; box-shadow: none !important; border-radius: 3px !important; }
        .chat-shell.gg-premium .new-chat-btn { background: var(--ed-forest) !important; color: #FFFFFF !important; border: 0 !important; border-radius: 3px !important;
          box-shadow: none !important; font-weight: 600 !important; letter-spacing: .01em; }
        .chat-shell.gg-premium .new-chat-btn:hover { background: var(--ed-forest-hover) !important; transform: none !important; }
        .chat-shell.gg-premium .conv-group-label { padding-top: 14px !important; }
        .chat-shell.gg-premium .conv-item { background: none !important; border: 0 !important; border-left: 2px solid transparent !important; border-radius: 0 !important;
          color: var(--ed-ink-soft) !important; box-shadow: none !important; }
        .chat-shell.gg-premium .conv-item:hover { background: rgba(22,39,33,.05) !important; color: var(--ed-ink-strong) !important; }
        .chat-shell.gg-premium .conv-item--active { border-left-color: var(--ed-forest) !important; background: var(--ed-surface) !important; color: var(--ed-ink-strong) !important; font-weight: 600; }
        .chat-shell.gg-premium .conv-item__title { color: inherit !important; }
        .chat-shell.gg-premium .conv-item__icon { stroke: var(--ed-muted) !important; }
        .chat-shell.gg-premium .conv-del { background: none !important; color: var(--ed-muted) !important; border: 0 !important; }
        .chat-shell.gg-premium .sidebar-footer { background: none !important; border-top: 1px solid var(--ed-rule) !important; color: var(--ed-muted) !important; }
        .chat-shell.gg-premium .sidebar-footer * { color: var(--ed-muted) !important; }

        /* ── top bar ─────────────────────────────────────────────────────── */
        .chat-shell.gg-premium .chat-main { background: var(--ed-paper) !important; }
        .chat-shell.gg-premium .chat-topbar { background: var(--ed-surface) !important; border-bottom: 1px solid var(--ed-rule) !important; box-shadow: none !important; color: var(--ed-ink) !important; }
        .chat-shell.gg-premium .chat-topbar::before, .chat-shell.gg-premium .chat-topbar::after { display: none !important; }
        .chat-shell.gg-premium .topbar-name { font-family: var(--ed-display) !important; font-weight: 500 !important; color: var(--ed-ink-strong) !important; letter-spacing: 0 !important; }
        .chat-shell.gg-premium .topbar-sub, .chat-shell.gg-premium .topbar-sub * { color: var(--ed-muted) !important; }
        .chat-shell.gg-premium .topbar-dot { background: var(--ed-positive) !important; box-shadow: none !important; }
        .chat-shell.gg-premium .topbar-toggle { background: var(--ed-surface) !important; border: 1px solid var(--ed-rule) !important; color: var(--ed-ink) !important; border-radius: 3px !important; box-shadow: none !important; }
        .chat-shell.gg-premium .topbar-toggle svg { stroke: var(--ed-ink) !important; }
        .chat-shell.gg-premium .topbar-logo { background: var(--ed-surface) !important; border: 1px solid var(--ed-rule) !important; border-radius: 3px !important; box-shadow: none !important; }
        .chat-shell.gg-premium .topbar-web-badge { background: var(--ed-tint) !important; color: var(--ed-ink-soft) !important; border: 0 !important; }

        /* ── welcome ─────────────────────────────────────────────────────── */
        .chat-shell.gg-premium .welcome-glow { display: none !important; }
        .chat-shell.gg-premium .chat-welcome { background: var(--ed-paper) !important; }
        .chat-shell.gg-premium .chat-welcome::before, .chat-shell.gg-premium .chat-welcome::after { display: none !important; }
        .chat-shell.gg-premium .welcome-kicker { display: inline-flex !important; flex-wrap: wrap; justify-content: center; align-items: center; gap: 6px 10px !important; background: none !important; border: 0 !important;
          padding: 0 !important; box-shadow: none !important; backdrop-filter: none !important; text-align: center; }
        .chat-shell.gg-premium .welcome-kicker-dot { width: 24px !important; height: 2px !important; border-radius: 0 !important; background: var(--ed-forest) !important; box-shadow: none !important; animation: none !important; }
        .chat-shell.gg-premium .welcome-kicker-sep { color: var(--ed-rule) !important; }
        .chat-shell.gg-premium .welcome-title { font-family: var(--ed-display) !important; font-weight: 400 !important; color: var(--ed-ink-strong) !important;
          background: none !important; -webkit-text-fill-color: currentColor !important; font-size: clamp(30px, 2.2rem + 1vw, 46px) !important; letter-spacing: -.015em !important; line-height: 1.12 !important; }
        .chat-shell.gg-premium .welcome-sub { color: var(--ed-ink-soft) !important; font-size: clamp(14px, .9rem + .2vw, 16px) !important; max-width: 56ch; line-height: 1.6 !important; }
        .chat-shell.gg-premium .welcome-capabilities { gap: 0 !important; border-top: 1px solid var(--ed-rule); border-bottom: 1px solid var(--ed-rule); padding: 10px 0 !important; }
        .chat-shell.gg-premium .welcome-capability { background: none !important; border: 0 !important; box-shadow: none !important; color: var(--ed-ink-soft) !important; padding: 2px 16px !important; border-radius: 0 !important; font-size: 12.5px !important; }
        .chat-shell.gg-premium .welcome-capability + .welcome-capability { border-left: 1px solid var(--ed-rule) !important; }
        .chat-shell.gg-premium .welcome-capability-icon { background: none !important; color: var(--ed-forest) !important; box-shadow: none !important; border: 0 !important; }
        .chat-shell.gg-premium .sugs { gap: 0 !important; border-top: 1px solid var(--ed-rule); }
        .chat-shell.gg-premium .sug { background: none !important; border: 0 !important; border-bottom: 1px solid var(--ed-rule) !important; border-radius: 0 !important;
          box-shadow: none !important; color: var(--ed-ink) !important; text-align: left; transition: background .15s ease, padding-left .15s ease !important; }
        .chat-shell.gg-premium .sug:hover { background: var(--ed-surface) !important; transform: none !important; padding-left: 18px !important; }
        .chat-shell.gg-premium .sug-icon { background: none !important; color: var(--ed-forest) !important; box-shadow: none !important; border: 0 !important; }

        /* ── messages ────────────────────────────────────────────────────── */
        .chat-shell.gg-premium .msg-avatar { border-radius: 3px !important; box-shadow: none !important; border: 1px solid var(--ed-rule) !important; background: var(--ed-surface) !important; }
        .chat-shell.gg-premium .msg-avatar--user { background: var(--ed-forest) !important; color: #FFFFFF !important; border-color: var(--ed-forest) !important; }
        .chat-shell.gg-premium .chat-msgs { background: var(--ed-paper) !important; }
        .chat-shell.gg-premium .chat-msgs::before, .chat-shell.gg-premium .chat-msgs::after { display: none !important; }
        .chat-shell.gg-premium .msg-row--user .msg-bubble { background: var(--ed-forest) !important; border: 0 !important; border-radius: 3px !important; box-shadow: none !important; }
        .chat-shell.gg-premium .msg-row--user .msg-text { background: none !important; color: #FFFFFF !important; border: 0 !important; border-radius: 0 !important; box-shadow: none !important; }
        .chat-shell.gg-premium .msg-row--user .msg-ts { color: rgba(255,255,255,.62) !important; }
        .chat-shell.gg-premium .msg-row--bot .msg-bubble { background: var(--ed-surface) !important; border: 0 !important; border-left: 2px solid var(--ed-rule) !important; border-radius: 0 !important; box-shadow: none !important; }
        .chat-shell.gg-premium .msg-row--bot .msg-text { background: none !important; color: var(--ed-ink) !important; border: 0 !important;
          border-radius: 0 !important; box-shadow: none !important; font-size: 14.5px !important; line-height: 1.68 !important; }
        .chat-shell.gg-premium .msg-text .md-h1, .chat-shell.gg-premium .msg-text .md-h2, .chat-shell.gg-premium .msg-text .md-h3 { font-family: var(--ed-display) !important; font-weight: 500 !important; color: var(--ed-ink-strong) !important; }
        .chat-shell.gg-premium .msg-text strong { color: var(--ed-ink-strong); }
        .chat-shell.gg-premium .msg-ts { color: var(--ed-muted) !important; font-size: 10.5px !important; }
        .chat-shell.gg-premium .followup-chip { background: var(--ed-surface) !important; border: 1px solid var(--ed-rule) !important; border-radius: 3px !important; color: var(--ed-ink) !important; box-shadow: none !important; }
        .chat-shell.gg-premium .followup-chip:hover { border-color: var(--ed-forest) !important; color: var(--ed-forest) !important; transform: none !important; }
        .chat-shell.gg-premium .followup-chip-icon { color: var(--ed-forest) !important; background: none !important; }
        .chat-shell.gg-premium .chat-source-links { color: var(--ed-muted) !important; }

        /* ── composer ────────────────────────────────────────────────────── */
        .chat-shell.gg-premium .chat-input-area { background: linear-gradient(to bottom, rgba(250,249,245,0), var(--ed-paper) 28%) !important; border-top: 1px solid var(--ed-rule) !important; }
        .chat-shell.gg-premium .input-row { background: var(--ed-surface) !important; border: 1px solid var(--ed-rule) !important; border-radius: 4px !important; box-shadow: 0 1px 2px rgba(28,28,26,.04) !important; }
        .chat-shell.gg-premium .input-row:focus-within { border-color: var(--ed-forest) !important; box-shadow: 0 0 0 3px rgba(22,39,33,.08) !important; }
        .chat-shell.gg-premium .chat-textarea { color: var(--ed-ink) !important; font-size: 15px !important; }
        .chat-shell.gg-premium .chat-textarea::placeholder { color: #9A958C !important; }
        .chat-shell.gg-premium .attach-btn { background: none !important; border: 1px solid var(--ed-rule) !important; border-radius: 3px !important; color: var(--ed-ink-soft) !important; box-shadow: none !important; }
        .chat-shell.gg-premium .attach-btn:hover { border-color: var(--ed-forest) !important; color: var(--ed-forest) !important; }
        .chat-shell.gg-premium .send-btn { background: var(--ed-forest) !important; border-radius: 3px !important; box-shadow: none !important; color: #FFFFFF !important; }
        .chat-shell.gg-premium .send-btn:disabled { background: #CFCAC0 !important; opacity: 1 !important; }
        .chat-shell.gg-premium .send-btn:not(:disabled):hover { background: var(--ed-forest-hover) !important; transform: none !important; }
        .chat-shell.gg-premium .report-mode-btn { background: var(--ed-surface) !important; border: 1px solid var(--ed-rule) !important; border-radius: 3px !important; color: var(--ed-ink-soft) !important; box-shadow: none !important; }
        .chat-shell.gg-premium .report-mode-btn.is-active { background: var(--ed-tint) !important; border-color: var(--ed-forest) !important; color: var(--ed-forest) !important; box-shadow: inset 2px 0 0 var(--ed-forest) !important; }
        .chat-shell.gg-premium .input-hint { color: var(--ed-muted) !important; }
        .chat-shell.gg-premium .file-chip { background: var(--ed-tint) !important; border: 0 !important; border-radius: 3px !important; color: var(--ed-ink) !important; }

        /* depth selector (Brief / In-Detail / Deep Research) */
        .chat-shell.gg-premium .report-tier-trigger { background: var(--ed-surface) !important; border: 1px solid var(--ed-rule) !important; border-radius: 3px !important; box-shadow: none !important; color: var(--ed-ink) !important; }
        .chat-shell.gg-premium .report-tier-trigger.is-open, .chat-shell.gg-premium .report-tier-trigger:hover { border-color: var(--ed-forest) !important; }
        .chat-shell.gg-premium .report-tier-trigger-dot { background: var(--ed-forest) !important; box-shadow: none !important; border-radius: 0 !important; width: 2px !important; height: 18px !important; }
        .chat-shell.gg-premium .report-tier-trigger-value { color: var(--ed-ink-strong) !important; font-weight: 600 !important; }
        .chat-shell.gg-premium .report-tier-menu { background: var(--ed-surface) !important; border: 1px solid var(--ed-rule) !important; border-radius: 4px !important; box-shadow: 0 12px 32px rgba(28,28,26,.12) !important; }
        .chat-shell.gg-premium .report-tier-menu-head { border-bottom: 1px solid var(--ed-rule) !important; color: var(--ed-muted) !important; }
        .chat-shell.gg-premium .report-tier-menu-item { border-radius: 0 !important; border-left: 2px solid transparent !important; background: none !important; }
        .chat-shell.gg-premium .report-tier-menu-item:hover { background: var(--ed-paper) !important; }
        .chat-shell.gg-premium .report-tier-menu-item.is-selected { border-left-color: var(--ed-forest) !important; background: var(--ed-tint) !important; }
        .chat-shell.gg-premium .report-tier-menu-label { font-family: var(--ed-display) !important; font-weight: 500 !important; color: var(--ed-ink-strong) !important; }
        .chat-shell.gg-premium .report-tier-menu-pages { color: var(--ed-forest) !important; font-weight: 600 !important; }
        .chat-shell.gg-premium .report-tier-menu-description { color: var(--ed-ink-soft) !important; }
        .chat-shell.gg-premium .report-tier-menu-radio { border-color: var(--ed-rule) !important; }
        .chat-shell.gg-premium .report-tier-menu-item.is-selected .report-tier-menu-radio { border-color: var(--ed-forest) !important; }
        .chat-shell.gg-premium .report-tier-menu-item.is-selected .report-tier-menu-radio span { background: var(--ed-forest) !important; }
        .chat-shell.gg-premium .report-tier-menu-check { color: var(--ed-forest) !important; }

        /* ── report: progress, preview, alerts, status ───────────────────── */
        .chat-shell.gg-premium .report-wrap { gap: 10px; }
        .chat-shell.gg-premium .report-progress { width: 100%; display: grid; gap: 8px; padding: 14px 16px; background: var(--ed-surface); border-left: 2px solid var(--ed-forest); }
        .chat-shell.gg-premium .report-progress-head { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px; }
        .chat-shell.gg-premium .report-progress-kicker { display: inline-flex; align-items: center; gap: 10px; }
        .chat-shell.gg-premium .report-progress-pct { font: 600 13px var(--ed-body); color: var(--ed-ink-strong); font-variant-numeric: tabular-nums; }
        .chat-shell.gg-premium .report-progress-stage { font: 400 14px/1.5 var(--ed-body); color: var(--ed-ink); min-width: 0; overflow-wrap: anywhere; }
        .chat-shell.gg-premium .report-progress-track { height: 2px; background: var(--ed-rule); overflow: hidden; }
        .chat-shell.gg-premium .report-progress-fill { height: 100%; background: var(--ed-forest); transition: width 240ms ease-out; }
        .chat-shell.gg-premium .dots i { background: var(--ed-forest) !important; }
        .chat-shell.gg-premium .report-preview-panel { width: 100%; background: var(--ed-surface); border-top: 1px solid var(--ed-rule); }
        .chat-shell.gg-premium .report-preview-head { display: flex; flex-wrap: wrap; gap: 4px 14px; align-items: baseline; padding: 10px 16px; border-bottom: 1px solid var(--ed-rule); color: var(--ed-muted); font-size: 12px; }
        .chat-shell.gg-premium .report-preview-body { max-height: min(56vh, 520px); overflow-y: auto; padding: 14px 18px !important; }
        .chat-shell.gg-premium .report-alert { width: 100%; display: grid; gap: 3px; padding: 10px 14px; border-left: 2px solid var(--ed-negative); background: #F8ECE9; color: #5E231B; font-size: 13px; line-height: 1.5; }
        .chat-shell.gg-premium .report-alert--error .report-alert-label { color: var(--ed-negative) !important; }
        .chat-shell.gg-premium .report-status-banner { width: 100%; display: block; padding: 12px 16px; border-left: 2px solid var(--ed-warning); background: var(--ed-warning-tint); color: #4F3609; font-size: 13px; line-height: 1.55; }
        .chat-shell.gg-premium .report-status-banner .report-status-label { display: block; margin-bottom: 4px; color: var(--ed-warning) !important; }
        .chat-shell.gg-premium .report-status-banner strong { color: #3E2A06; }
        .chat-shell.gg-premium .report-status-findings { margin: 8px 0 0; padding: 8px 0 0 18px; border-top: 1px solid rgba(138,90,18,.18); display: grid; gap: 4px; }
        .chat-shell.gg-premium .report-tier-status { background: var(--ed-tint) !important; border: 0 !important; border-left: 2px solid var(--ed-forest) !important; border-radius: 0 !important; color: var(--ed-ink) !important; }
        .chat-shell.gg-premium .report-context-track { background: var(--ed-rule) !important; }
        .chat-shell.gg-premium .report-context-toggle input:checked + .report-context-track { background: var(--ed-forest) !important; }

        /* toolbar */
        .chat-shell.gg-premium .report-btn { background: var(--ed-surface) !important; color: var(--ed-ink) !important; border: 1px solid var(--ed-rule) !important; border-radius: 3px !important;
          box-shadow: none !important; font-weight: 600 !important; font-size: 12.5px !important; letter-spacing: .01em; padding: 7px 12px !important; transform: none !important; }
        .chat-shell.gg-premium .report-btn:hover:not(:disabled) { border-color: var(--ed-forest) !important; color: var(--ed-forest) !important; }
        .chat-shell.gg-premium .report-btn svg { stroke: currentColor !important; }
        .chat-shell.gg-premium .report-btn--primary { background: var(--ed-forest) !important; color: #FFFFFF !important; border-color: var(--ed-forest) !important; margin-left: auto; }
        .chat-shell.gg-premium .report-btn--primary:hover:not(:disabled) { background: var(--ed-forest-hover) !important; color: #FFFFFF !important; }
        .chat-shell.gg-premium .report-btn--primary:disabled { opacity: .75; cursor: progress; }
        .chat-shell.gg-premium .report-btn--experiment, .chat-shell.gg-premium .report-btn--action { background: var(--ed-surface) !important; color: var(--ed-ink) !important; }
        .chat-shell.gg-premium .report-rev { font: 600 10px var(--ed-body); letter-spacing: .2em; text-transform: uppercase; color: var(--ed-muted); padding: 0 4px; }

        /* ── report document ─────────────────────────────────────────────── */
        .chat-shell.gg-premium .report-body { background: var(--ed-surface) !important; border: 0 !important; border-top: 1px solid var(--ed-forest) !important; border-radius: 0 !important; box-shadow: 0 1px 2px rgba(28,28,26,.05) !important;
          padding: clamp(18px, 3vw, 44px) clamp(16px, 4vw, 56px) clamp(24px, 3vw, 48px) !important; max-height: min(80vh, 1040px) !important; counter-reset: ed-figure; scroll-behavior: smooth; }
        .chat-shell.gg-premium .report-masthead { display: grid; gap: 12px; padding-bottom: 22px; margin-bottom: 8px; border-bottom: 1px solid var(--ed-rule); }
        .chat-shell.gg-premium .report-masthead-kicker { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; }
        .chat-shell.gg-premium .report-masthead-kicker::before { content: ""; width: 24px; height: 2px; background: var(--ed-forest); flex: 0 0 auto; }
        .chat-shell.gg-premium .report-masthead-flag { padding: 2px 8px; background: var(--ed-warning-tint); color: var(--ed-warning); letter-spacing: .2em; }
        .chat-shell.gg-premium .report-masthead-title { margin: 0; font: 400 clamp(26px, 1.4rem + 1.6vw, 40px)/1.14 var(--ed-display); color: var(--ed-ink-strong); letter-spacing: -.012em; text-wrap: balance; max-width: 26ch; }
        .chat-shell.gg-premium .report-masthead-summary { margin: 0; max-width: 64ch; font: 400 clamp(15px, .9rem + .3vw, 18px)/1.55 var(--ed-display); color: var(--ed-ink-soft); }
        .chat-shell.gg-premium .report-masthead-meta { display: flex; flex-wrap: wrap; gap: 6px 28px; margin: 4px 0 0; padding-top: 12px; border-top: 1px solid var(--ed-rule-soft); }
        .chat-shell.gg-premium .report-masthead-meta div { display: grid; gap: 2px; min-width: 0; }
        .chat-shell.gg-premium .report-masthead-meta dt { font: 600 9.5px var(--ed-body); letter-spacing: .22em; text-transform: uppercase; color: var(--ed-muted); }
        .chat-shell.gg-premium .report-masthead-meta dd { margin: 0; font: 500 14px var(--ed-body); color: var(--ed-ink-strong); font-variant-numeric: tabular-nums; }
        .chat-shell.gg-premium .report-outline { display: grid; gap: 8px; padding-top: 12px; border-top: 1px solid var(--ed-rule-soft); }
        .chat-shell.gg-premium .report-outline ol { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 220px), 1fr)); gap: 0 24px; }
        .chat-shell.gg-premium .report-outline li { border-bottom: 1px solid var(--ed-rule-soft); min-width: 0; }
        .chat-shell.gg-premium .report-outline button { width: 100%; display: flex; gap: 10px; align-items: baseline; padding: 7px 0; background: none; border: 0; cursor: pointer; text-align: left;
          font: 500 13px/1.4 var(--ed-body); color: var(--ed-ink); }
        .chat-shell.gg-premium .report-outline button:hover { color: var(--ed-forest); }
        .chat-shell.gg-premium .report-outline-num { font: 600 11px var(--ed-body); color: var(--ed-muted); font-variant-numeric: tabular-nums; }

        .chat-shell.gg-premium .report-content { font: 400 15px/1.72 var(--ed-body) !important; color: var(--ed-ink) !important; max-width: 100%; }
        .chat-shell.gg-premium .report-content section { scroll-margin-top: 12px; }
        .chat-shell.gg-premium .report-content .md-p, .chat-shell.gg-premium .report-content .md-li { text-align: left !important; hyphens: manual !important; }
        .chat-shell.gg-premium .report-content .md-p { margin: 0 0 14px !important; max-width: 72ch; }
        .chat-shell.gg-premium .report-content .md-h1 { font: 400 clamp(22px, 1.2rem + 1vw, 30px)/1.2 var(--ed-display) !important; color: var(--ed-ink-strong) !important; margin: 34px 0 12px !important; }
        .chat-shell.gg-premium .report-content .md-h2 { font: 400 clamp(20px, 1.1rem + .8vw, 26px)/1.22 var(--ed-display) !important; color: var(--ed-ink-strong) !important;
          margin: 36px 0 12px !important; padding-top: 24px !important; border-top: 1px solid var(--ed-rule) !important; }
        .chat-shell.gg-premium .report-content .md-h3 { font: 600 15.5px/1.4 var(--ed-body) !important; color: var(--ed-ink-strong) !important; margin: 22px 0 8px !important; }
        .chat-shell.gg-premium .report-content > :first-child .md-h1:first-child, .chat-shell.gg-premium .report-content > :first-child .md-h2:first-child { margin-top: 10px !important; border-top: 0 !important; padding-top: 0 !important; }
        .chat-shell.gg-premium .report-content .md-ul { margin: 0 0 14px 1.2em !important; }
        .chat-shell.gg-premium .report-content .md-li { margin: 0 0 7px !important; padding-left: 3px; }
        .chat-shell.gg-premium .report-content .md-li::marker { color: var(--ed-forest); }
        .chat-shell.gg-premium .report-content .md-ref, .chat-shell.gg-premium .msg-text .md-ref { font: 600 .72em var(--ed-body) !important; color: var(--ed-forest) !important; vertical-align: .2em; text-decoration: none !important; background: none !important; }
        .chat-shell.gg-premium .report-content .md-code { background: var(--ed-tint) !important; color: var(--ed-ink) !important; }
        /* tables: wrap, safe horizontal overflow, very soft zebra */
        .chat-shell.gg-premium .report-content .md-table, .chat-shell.gg-premium .chart-wrap .md-table { display: block; max-width: 100%; overflow-x: auto; border-collapse: collapse !important; margin: 18px 0 !important;
          font: 400 13.5px/1.45 var(--ed-body) !important; font-variant-numeric: tabular-nums lining-nums; border-top: 1px solid var(--ed-forest); }
        .chat-shell.gg-premium .report-content .md-th, .chat-shell.gg-premium .chart-wrap .md-th { background: var(--ed-surface) !important; color: var(--ed-muted) !important; border: 0 !important; border-bottom: 1px solid var(--ed-rule) !important;
          font: 600 10px/1.35 var(--ed-body) !important; letter-spacing: .14em !important; text-transform: uppercase !important; padding: 10px 12px !important; vertical-align: bottom; min-width: 7ch; }
        .chat-shell.gg-premium .report-content .md-td, .chat-shell.gg-premium .chart-wrap .md-td { border: 0 !important; border-bottom: 1px solid var(--ed-rule) !important; padding: 10px 12px !important; color: var(--ed-ink) !important;
          vertical-align: top; background: var(--ed-surface); overflow-wrap: break-word; min-width: 7ch; }
        .chat-shell.gg-premium .report-content .md-table tbody tr:nth-child(even) td, .chat-shell.gg-premium .chart-wrap .md-table tbody tr:nth-child(even) td { background: var(--ed-paper) !important; }
        .chat-shell.gg-premium .report-content .md-td:first-child { color: var(--ed-ink-strong) !important; font-weight: 500; min-width: 13ch; }

        /* metric boxes */
        .chat-shell.gg-premium .key-stats-row { display: grid !important; grid-template-columns: repeat(auto-fit, minmax(min(100%, 140px), 1fr)) !important; gap: 12px !important; margin: 18px 0 26px !important; }
        .chat-shell.gg-premium .key-stat-card { display: flex !important; flex-direction: column; gap: 6px; min-width: 0; padding: 16px 16px 14px !important; background: var(--ed-tint) !important;
          border: 0 !important; border-left: 2px solid var(--ed-forest) !important; border-radius: 0 !important; box-shadow: none !important; }
        .chat-shell.gg-premium .key-stat-card::before, .chat-shell.gg-premium .key-stat-card::after { display: none !important; }
        .chat-shell.gg-premium .key-stat-label { font: 600 10px/1.35 var(--ed-body) !important; letter-spacing: .2em !important; text-transform: uppercase !important; color: var(--ed-muted) !important; overflow-wrap: anywhere; }
        .chat-shell.gg-premium .key-stat-value { display: flex; flex-wrap: wrap; align-items: baseline; column-gap: .22em; font: 500 clamp(22px, 1.2rem + .7vw, 28px)/1.12 var(--ed-body) !important;
          color: var(--ed-ink-strong) !important; font-variant-numeric: tabular-nums lining-nums; letter-spacing: -.015em; overflow-wrap: anywhere; background: none !important; -webkit-text-fill-color: currentColor !important; }
        .chat-shell.gg-premium .key-stat-number { white-space: nowrap; }
        .chat-shell.gg-premium .key-stat-prefix, .chat-shell.gg-premium .key-stat-unit { font-size: .56em; font-weight: 500; letter-spacing: 0; color: var(--ed-ink-soft); }
        .chat-shell.gg-premium .key-stat-prefix { align-self: flex-start; margin-top: .2em; }
        .chat-shell.gg-premium .key-stat-change { font: 600 12px var(--ed-body) !important; color: var(--ed-ink-soft) !important; font-variant-numeric: tabular-nums; background: none !important; }
        .chat-shell.gg-premium .key-stat-change.pos { color: var(--ed-positive) !important; }
        .chat-shell.gg-premium .key-stat-change.neg { color: var(--ed-negative) !important; }

        /* charts — editorial frames, figure numbering, legends and sources */
        .chat-shell.gg-premium .inline-report-chart, .chat-shell.gg-premium .inline-chart-wrap { margin: 22px 0 !important; background: none !important; border: 0 !important; box-shadow: none !important; padding: 0 !important; }
        .chat-shell.gg-premium .charts-grid { display: grid !important; grid-template-columns: repeat(auto-fit, minmax(min(100%, 320px), 1fr)) !important; gap: 16px !important; }
        .chat-shell.gg-premium .inline-charts-label { font: 600 10px var(--ed-body) !important; letter-spacing: .25em; text-transform: uppercase; color: var(--ed-muted) !important; }
        .chat-shell.gg-premium .ed-chart { margin: 0; padding: 16px 18px 12px; background: var(--ed-surface); border-top: 1px solid var(--ed-rule); min-width: 0; break-inside: avoid; }
        .chat-shell.gg-premium .report-content .ed-chart { counter-increment: ed-figure; padding-inline: 0; }
        .chat-shell.gg-premium .ed-chart-head { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 4px 16px; margin-bottom: 10px; }
        .chat-shell.gg-premium .ed-chart-title { font: 500 16.5px/1.35 var(--ed-display); color: var(--ed-ink-strong); min-width: 0; flex: 1 1 260px; text-wrap: balance; }
        .chat-shell.gg-premium .report-content .ed-chart-title::before { content: "Figure " counter(ed-figure); display: block; margin-bottom: 3px; font: 600 10px/1.3 var(--ed-body); letter-spacing: .25em; text-transform: uppercase; color: var(--ed-muted); }
        .chat-shell.gg-premium .ed-chart-unit { font: 600 10px var(--ed-body); letter-spacing: .18em; text-transform: uppercase; color: var(--ed-muted); white-space: nowrap; }
        .chat-shell.gg-premium .ed-chart-plot { min-width: 0; }
        .chat-shell.gg-premium .ed-chart-plot svg { display: block; width: 100%; height: auto; overflow: visible; }
        .chat-shell.gg-premium .ed-chart-pie { display: flex; justify-content: center; }
        .chat-shell.gg-premium .ed-chart-pie svg { width: min(100%, 240px) !important; }
        .chat-shell.gg-premium .ed-chart-legend { list-style: none; margin: 12px 0 0; padding: 10px 0 0; border-top: 1px solid var(--ed-rule-soft); display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 200px), 1fr)); gap: 6px 20px; }
        .chat-shell.gg-premium .ed-chart-legend li { display: flex; align-items: center; gap: 8px; min-width: 0; font-size: 12.5px; color: var(--ed-ink); }
        .chat-shell.gg-premium .ed-chart-legend i { width: 10px; height: 10px; border-radius: 2px; flex: 0 0 auto; }
        .chat-shell.gg-premium .ed-chart-legend span { min-width: 0; overflow-wrap: anywhere; }
        .chat-shell.gg-premium .ed-chart-legend b { margin-left: auto; padding-left: 8px; font-weight: 600; font-variant-numeric: tabular-nums; color: var(--ed-ink-strong); white-space: nowrap; }
        .chat-shell.gg-premium .ed-chart-caption { margin: 10px 0 0; font: italic 400 13px/1.5 var(--ed-display); color: var(--ed-ink-soft); }
        .chat-shell.gg-premium .ed-chart-source { margin: 10px 0 0; padding-top: 8px; border-top: 1px solid var(--ed-rule-soft); font-size: 11.5px; color: var(--ed-muted); display: flex; flex-wrap: wrap; gap: 6px; }
        .chat-shell.gg-premium .ed-chart-source span { font-weight: 600; color: var(--ed-ink-soft); }
        .chat-shell.gg-premium .chart-wrap { background: var(--ed-surface) !important; border: 0 !important; border-top: 1px solid var(--ed-rule) !important; border-radius: 0 !important; box-shadow: none !important; }
        .chat-shell.gg-premium .chart-title { font: 500 16px/1.35 var(--ed-display) !important; color: var(--ed-ink-strong) !important; text-transform: none !important; letter-spacing: 0 !important; }
        .chat-shell.gg-premium .inline-report-image { margin: 24px 0 !important; background: none !important; border: 0 !important; padding: 0 !important; }
        .chat-shell.gg-premium .inline-report-image img { display: block; max-width: 100%; max-height: 440px; width: auto; margin: 0 auto; border-radius: 0 !important; object-fit: contain; }
        .chat-shell.gg-premium .inline-report-image figcaption { font: italic 400 13px/1.5 var(--ed-display) !important; color: var(--ed-ink-soft) !important; text-align: left; margin-top: 8px !important; }
        .chat-shell.gg-premium .report-unplanned-visual { border: 0 !important; }

        /* sources */
        .chat-shell.gg-premium .report-sources { margin-top: 44px !important; padding: 22px 0 0 !important; background: none !important; border: 0 !important; border-top: 1px solid var(--ed-forest) !important; border-radius: 0 !important; box-shadow: none !important; }
        .chat-shell.gg-premium .report-sources h2 { font: 400 22px/1.2 var(--ed-display) !important; color: var(--ed-ink-strong) !important; display: flex; align-items: baseline; gap: 12px; }
        .chat-shell.gg-premium .report-sources h2 span { font: 600 11px var(--ed-body); letter-spacing: .2em; color: var(--ed-muted); background: none !important; padding: 0 !important; }
        .chat-shell.gg-premium .report-sources p { color: var(--ed-muted) !important; font-size: 12.5px !important; }
        .chat-shell.gg-premium .report-sources ol { margin: 12px 0 0 !important; padding-left: 1.6em !important; display: block !important; }
        .chat-shell.gg-premium .report-sources li { padding: 10px 0 !important; border: 0 !important; border-bottom: 1px solid var(--ed-rule) !important; background: none !important; border-radius: 0 !important; box-shadow: none !important; }
        .chat-shell.gg-premium .report-sources li::marker { color: var(--ed-muted); font-variant-numeric: tabular-nums; font-size: 12px; }
        .chat-shell.gg-premium .report-sources li::before, .chat-shell.gg-premium .report-sources li::after { content: none !important; display: none !important; }
        .chat-shell.gg-premium .report-sources ol { list-style: decimal outside !important; counter-reset: none !important; }
        .chat-shell.gg-premium .report-sources li { display: list-item !important; padding-left: 4px !important; }
        .chat-shell.gg-premium .report-source-title { font-weight: 500 !important; color: var(--ed-ink-strong) !important; }
        .chat-shell.gg-premium .report-source-meta { color: var(--ed-muted) !important; font-size: 12px !important; }
        .chat-shell.gg-premium .report-sources a { color: var(--ed-forest) !important; font-size: 12.5px; text-decoration: none; }
        .chat-shell.gg-premium .report-sources a:hover { text-decoration: underline; }

        /* modal */
        .chat-shell.gg-premium ~ .report-prompt-overlay .report-prompt-modal, .report-prompt-modal { font-family: 'Inter', system-ui, sans-serif; }

        @media (max-width: 640px) {
          .chat-shell.gg-premium .report-body { padding: 18px 14px 24px !important; max-height: none !important; }
          .chat-shell.gg-premium .report-btn--primary { margin-left: 0; }
          .chat-shell.gg-premium .welcome-capability + .welcome-capability { border-left: 0 !important; }
          .chat-shell.gg-premium .welcome-kicker { letter-spacing: .18em !important; }
          .chat-shell.gg-premium .welcome-kicker-dot { display: none !important; }
          .chat-shell.gg-premium .ed-chart { padding: 12px 0 10px; }
        }
        @media (prefers-reduced-motion: reduce) {
          .chat-shell.gg-premium * { animation-duration: .01ms !important; transition-duration: .01ms !important; }
        }

        /* ── Full-width workspace (v95): the chat fills the page edge to edge
           below the navbar + ticker instead of sitting in a boxed card.
           The page container around it drops its max-width and padding. ── */
        .main-container:has(.chat-shell.gg-full) {
          max-width: none !important; padding: 0 !important; margin: 0 !important;
        }
        .chat-shell.gg-premium.gg-full {
          height: calc(100dvh - var(--gg-chat-top, 92px) - var(--gg-chat-bottom, 0px)) !important;
          max-width: none !important; width: 100% !important; margin: 0 !important;
          border: 0 !important; border-top: 1px solid var(--ed-rule) !important;
          border-radius: 0 !important; box-shadow: none !important;
        }
        .chat-shell.gg-premium.gg-full .chat-main { background: var(--ed-paper); }
        @media (min-width: 1920px) {
          .chat-shell.gg-premium.gg-full .msg-inner { max-width: 960px; }
        }
      `}</style>


      {/* Hidden file input */}
      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept=".pdf,.doc,.docx,.txt,.csv,.md,.xlsx,.xls,.jpg,.jpeg,.png,.webp,.gif"
        style={{ display:'none' }}
        onChange={async e => {
          const files = Array.from(e.target.files ?? []);
          if (files.length) await addFiles(files);
          e.target.value = '';
        }}
      />

      <div
        ref={chatShellRef}
        className={`chat-shell gg-premium gg-full${dragOver ? ' chat-shell--drag' : ''}`}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleAreaDrop}
      >
        {dragOver && (
          <div style={{ position:'absolute', inset:0, zIndex:50, background:'rgba(26,31,78,0.07)',
            display:'flex', alignItems:'center', justifyContent:'center',
            backdropFilter:'blur(4px)', borderRadius:16, pointerEvents:'none' }}>
            <div style={{ background:'#fff', border:'2px dashed #1a1f4e', borderRadius:16,
              padding:'32px 48px', textAlign:'center', boxShadow:'0 8px 32px rgba(26,31,78,.15)' }}>
              <div style={{ fontSize:36, marginBottom:10 }}>📎</div>
              <p style={{ fontSize:16, fontWeight:700, color:'#1a1f4e', margin:0 }}>Drop files to attach</p>
              <p style={{ fontSize:12, color:'#8b93b5', margin:'4px 0 0' }}>PDF · DOCX · TXT · Images</p>
            </div>
          </div>
        )}

        {/* ── Sidebar ────────────────────────────────────────────────────── */}
        {/* Mobile sidebar overlay backdrop */}
        {isMobile && sidebarOpen && (
          <div
            onClick={() => setSidebarOpen(false)}
            style={{
              position: 'fixed', inset: 0, zIndex: 49,
              background: 'rgba(15,23,42,0.45)', backdropFilter: 'blur(2px)',
            }}
          />
        )}

        <aside className={`sidebar${sidebarOpen ? '' : ' sidebar--closed'}${isMobile ? ' sidebar--mobile' : ''}`}>
          {/* Sidebar header */}
          <div className="sidebar-hdr">
            <div className="sidebar-logo">
              <Image src="/growth-gradual-logo.jpg" alt="Growth Gradual" width={32} height={32} style={{ objectFit:'cover', objectPosition:'3% 50%', width:32, height:32 }}/>
            </div>
            <div className="sidebar-brand">
              Growth Gradual
              <span>Research Intelligence</span>
            </div>
            {/* Mobile close button */}
            {isMobile && (
              <button
                onClick={() => setSidebarOpen(false)}
                style={{
                  marginLeft:'auto', borderRadius:8,
                  border:'1px solid #e2e6f0', background:'#f5f5f8',
                  color:'#8b93b5', cursor:'pointer', flexShrink:0,
                  display:'flex', alignItems:'center', justifyContent:'center',
                }}
                aria-label="Close sidebar"
              >
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
                  <path d="M18 6L6 18M6 6l12 12"/>
                </svg>
              </button>
            )}
          </div>

          {/* New chat button */}
          <button className="new-chat-btn" onClick={() => { startNewChat(); if (isMobile) setSidebarOpen(false); }}>
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><path d="M12 5v14M5 12h14"/></svg>
            New chat
          </button>

          {/* Conversation list */}
          <div className="conv-list">
            {conversations.length === 0 ? (
              <div style={{ padding:'16px 14px', fontSize:11, color:'#b0b8d4', fontFamily:'DM Sans,sans-serif', textAlign:'center', lineHeight:1.6 }}>
                No conversations yet.<br/>Start chatting below.
              </div>
            ) : (
              Object.entries(grouped).map(([date, convs]) => (
                <div key={date}>
                  <div className="conv-group-label">{date}</div>
                  {convs.map(conv => (
                    <div
                      key={conv.id}
                      className={`conv-item${activeId === conv.id ? ' conv-item--active' : ''}`}
                      onClick={() => { loadConversation(conv); if (isMobile) setSidebarOpen(false); }}
                    >
                      <svg className="conv-item__icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#8b93b5" strokeWidth="2" strokeLinecap="round">
                        <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
                      </svg>
                      <span className="conv-item__title">{conv.title}</span>
                      <button className="conv-del" onClick={(e) => deleteConversation(conv.id, e)} aria-label="Delete">
                        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><path d="M18 6L6 18M6 6l12 12"/></svg>
                      </button>
                    </div>
                  ))}
                </div>
              ))
            )}
          </div>

          <div className="sidebar-footer">
            <div style={{ fontWeight: 600, color: 'inherit' }}>Growth Gradual LLP</div>
            <div style={{ fontSize: '9px', opacity: 0.75, marginTop: '2px' }}>Institutional Research · ARN-351151</div>
          </div>
        </aside>

        {/* ── Main area ──────────────────────────────────────────────────── */}
        <div className="chat-main">

          {/* Top bar */}
          <div className="chat-topbar">
            <button className="topbar-toggle" onClick={() => setSidebarOpen(o => !o)} aria-label="Toggle sidebar">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><path d="M3 12h18M3 6h18M3 18h18"/></svg>
            </button>
            <div className="topbar-logo">
              <Image src="/growth-gradual-logo.jpg" alt="Growth Gradual" width={30} height={30} style={{ objectFit:'cover', objectPosition:'3% 50%', width:30, height:30 }}/>
            </div>
            <div className="topbar-info">
              <div className="topbar-name">Growth Gradual</div>
              <div className="topbar-sub">
                <span className="topbar-dot"/>
                Research Intelligence · Indian Markets
              </div>
            </div>
          </div>

          {/* Messages */}
          <div className="chat-msgs" onScroll={(event) => {
            const el = event.currentTarget;
            shouldAutoScrollRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 140;
          }}>
            {isEmpty ? (
              <div className="chat-welcome">
                <div className="welcome-kicker">
                  <span className="welcome-kicker-dot" aria-hidden="true" />
                  RESEARCH INTELLIGENCE <span className="welcome-kicker-sep">/</span> INDIAN MARKETS
                </div>
                {/* Headline */}
                <div className="welcome-intro">
                  <p className="welcome-title">Where should we begin?</p>
                  <p className="welcome-sub">A sharper view of Indian markets, built from live research, source-linked evidence and thoughtful analysis.</p>
                </div>
                <div className="welcome-capabilities" aria-label="Research capabilities">
                  <span className="welcome-capability"><span className="welcome-capability-icon welcome-capability-icon--mint">↗</span> Live research</span>
                  <span className="welcome-capability"><span className="welcome-capability-icon welcome-capability-icon--violet"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M10 13a5 5 0 0 0 7.07 0l3-3a5 5 0 0 0-7.07-7.07l-1.5 1.5"/><path d="M14 11a5 5 0 0 0-7.07 0l-3 3a5 5 0 0 0 7.07 7.07l1.5-1.5"/></svg></span> Evidence-linked insights</span>
                  <span className="welcome-capability"><span className="welcome-capability-icon welcome-capability-icon--gold">▥</span> Visual reports</span>
                </div>

                {/* Quick suggestion chips */}
                <div className="sugs">
                  {SUGGESTIONS.map(s => (
                    <button key={s.label} className="sug" onClick={() => send(s.label)}>
                      <span className="sug-icon">{s.icon}</span>
                      <span>{s.label}</span>
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              messages.map((msg, i) => {
                const isLast = i === messages.length - 1;
                const isUser = msg.role === 'user';
                const isVisuallyStreaming = !isUser && streamVisualMessageId === msg.id;
                const renderedText = isVisuallyStreaming ? streamDisplayedText : msg.text;

                // Status / loading state (searching, indexing, generating). Once
                // actual text has arrived, render its progressive visible value.
                if (isLast && streaming && !isUser && (!renderedText || searching || statusMsg)) return (
                  <div key={msg.id} className="msg-row msg-row--bot">
                    <div className="msg-inner">
                      <div className="msg-avatar msg-avatar--bot">
                        <Image src="/growth-gradual-logo.jpg" alt="" width={28} height={28} style={{ objectFit:'cover', objectPosition:'3% 50%', width:28, height:28 }}/>
                      </div>
                      <div className="msg-bubble">
                        <div className="searching">
                          {statusMsg?.startsWith('Web search') ? (
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#1d4ed8" strokeWidth="2.5" strokeLinecap="round"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
                          ) : (
                            <div className="chip-spinner" style={{ width:12, height:12, borderWidth:2 }}/>
                          )}
                          {statusMsg || 'Thinking…'}
                          <div className="search-dots"><span/><span/><span/></div>
                        </div>
                      </div>
                    </div>
                  </div>
                );

                return (
                  <div key={msg.id} className={`msg-row msg-row--${isUser ? 'user' : 'bot'}`}>
                    <div className="msg-inner">
                      {/* Avatar */}
                      <div className={`msg-avatar msg-avatar--${isUser ? 'user' : 'bot'}`}>
                        {isUser ? (
                          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#4b5680" strokeWidth="2" strokeLinecap="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
                        ) : (
                          <Image src="/growth-gradual-logo.jpg" alt="" width={28} height={28} style={{ objectFit:'cover', objectPosition:'3% 50%', width:28, height:28 }}/>
                        )}
                      </div>
                      {/* Bubble */}
                      <div className="msg-bubble">

                        {isUser ? (
                          <div
                            className="msg-text"
                            dangerouslySetInnerHTML={{ __html: msg.text.replace(/\n/g,'<br/>') }}
                          />
                        ) : (() => {
                          // Only swap a markdown table for a rendered chart when the
                          // user's question actually asked for one (chart/graph/plot/
                          // visualize). Otherwise a "give me a table" request would
                          // silently lose its table and show a bar chart instead —
                          // respect what was asked for; the table itself renders fine
                          // via renderMd()'s <table class="md-table"> handling.
                          const parts = msg.wantsVisual ? splitTextAndCharts(renderedText) : [];
                          const hasCharts = parts.some(p => p.type === 'chart');
                          const hasInlineCharts = (msg.inlineCharts ?? []).length > 0;
                          return (
                            <>
                              {/* Datawrapper/SVG charts from /api/chat/charts */}
                              {hasInlineCharts && (
                                <div className="inline-charts-section">
                                  <div className="inline-charts-label">📊 Charts</div>
                                  <div className="charts-grid">
                                    {(msg.inlineCharts ?? []).map((c, ci) => <ChartBlock key={ci} spec={c}/>)}
                                  </div>
                                </div>
                              )}
                              {/* Text with embedded table-extracted charts */}
                              {!hasCharts ? (
                                <div
                                  className={`msg-text ${(streaming && isLast) || isVisuallyStreaming ? 'msg-text--streaming' : ''}`}
                                  dangerouslySetInnerHTML={{ __html: renderMd(renderedText) }}
                                />
                              ) : (
                                <div className={`msg-text ${(streaming && isLast) || isVisuallyStreaming ? 'msg-text--streaming' : ''}`}>
                                  {parts.map((p, pi) =>
                                    p.type === 'text'
                                      ? <div key={pi} dangerouslySetInnerHTML={{ __html: renderMd(p.content) }}/>
                                      : <div key={pi} className="inline-chart-wrap"><ChartBlock spec={p.spec}/></div>
                                  )}
                                </div>
                              )}
                            </>
                          );
                        })()}

                        {!isUser && (msg.reportEligible || msg.reportLoading || msg.reportData || msg.reportError) && (
                          <ReportPanel
                            msg={msg}
                            question={msg.reportQuestion ?? msg.text}
                            hasPriorContext={i > 1}
                            initialTier={selectedReportTier}
                            onExperiment={async (prompt, tier) => {
                              const userMsg: Message = { id: uid(), role: 'user', text: prompt, ts: Date.now() };
                              const botMsg: Message = { id: uid(), role: 'assistant', text: '', ts: Date.now(), reportEligible: true, reportQuestion: prompt, reportFiles: msg.reportFiles ?? [], sources: [] };
                              setMessages(prev => [...prev, userMsg, botMsg]);
                              historyRef.current = [...historyRef.current, { role: 'user', content: prompt }];
                              generateReport(botMsg.id, prompt, msg.reportFiles ?? [], undefined, true, [], tier);
                            }}
                            onAction={(instruction) => {
                              if (msg.reportData) editReportSection(msg.id, instruction, msg.reportData);
                            }}
                            onRestore={(revision) => {
                              if (msg.reportData) restoreReportRevision(msg.id, revision, msg.reportData);
                            }}
                            onGenerate={(includeContext, tier) => {
                              // Build conversation context from all prior messages in this thread —
                              // only when the toggle is on (it's hidden/defaults false for the
                              // very first response, since there's nothing prior to include).
                              const conversationContext = includeContext
                                ? messages
                                    .slice(0, i) // all messages before this one
                                    .filter(m => m.text && m.text.trim())
                                    .map(m => `${m.role === 'user' ? 'User' : 'Assistant'}: ${m.text.slice(0, 800)}`)
                                    .join('\n\n')
                                : '';
                              generateReport(
                                msg.id,
                                msg.reportQuestion ?? msg.text,
                                msg.reportFiles ?? [],
                                conversationContext || undefined,
                                !msg.text.trim(),
                                msg.sources ?? [],
                                tier,
                              );
                            }}
                          />
                        )}
                        {/* Source-of-truth download — shown whenever this answer used the
                            screener fundamentals KB (companies/ratios/financials tables) */}
                        {!isUser && msg.kbCompany && (
                          <div style={{ marginTop: 8 }}>
                            <a
                              href={msg.kbCompany.downloadUrl}
                              target="_blank"
                              rel="noopener noreferrer"
                              download
                              style={{
                                display: 'inline-flex', alignItems: 'center', gap: 6,
                                fontSize: 11.5, color: '#1a1f4e', fontFamily: 'DM Sans,sans-serif',
                                textDecoration: 'none', padding: '5px 10px', borderRadius: 8,
                                border: '1px solid #e2e5f0', background: '#f8f9fc',
                              }}
                            >
                              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                              Download source data — {msg.kbCompany.ticker} fundamentals (.xlsx)
                            </a>
                          </div>
                        )}
                        {/* Retrieved/web sources for normal chat: visible links rather than console-only metadata. */}
                        {!isUser && !streaming && !msg.reportData && (msg.sources?.length ?? 0) > 0 && (
                          <details className="chat-source-links" style={{ marginTop: 8, maxWidth: '100%', fontSize: 11.5, color: '#536176' }}>
                            <summary style={{ cursor: 'pointer', fontWeight: 650, color: '#256b58', padding: '3px 0' }}>
                              Sources ({msg.sources!.length})
                            </summary>
                            <ol style={{ margin: '5px 0 0', paddingLeft: 19, display: 'grid', gap: 5 }}>
                              {msg.sources!.filter(source => /^https?:\/\//i.test(String(source.url || ''))).slice(0, 10).map((source, sourceIndex) => (
                                <li key={`${source.url}-${sourceIndex}`}>
                                  <a href={source.url} target="_blank" rel="noopener noreferrer" style={{ color: '#285f9e', textDecoration: 'underline', textUnderlineOffset: 2 }}>
                                    {source.title || source.url}
                                  </a>
                                  {source.snippet ? <span style={{ display: 'block', color: '#667085', marginTop: 2 }}>{source.snippet.slice(0, 220)}</span> : null}
                                </li>
                              ))}
                            </ol>
                          </details>
                        )}
                        {/* Follow-up question chips */}
                        {!isUser && isLast && !streaming && msg.followUpQuestions && msg.followUpQuestions.length > 0 && (
                          <div className="followup-chips">
                            <span className="followup-label">You might also ask</span>
                            {msg.followUpQuestions.map((q, qi) => {
                              // Split leading emoji from text (handles emoji followed by space)
                              const emojiMatch = q.match(/^(\p{Emoji_Presentation}|\p{Extended_Pictographic})\s*/u);
                              const icon = emojiMatch ? emojiMatch[0].trim() : '💬';
                              const label = emojiMatch ? q.slice(emojiMatch[0].length) : q;
                              return (
                                <button key={qi} className="followup-chip" onClick={() => send(label)}>
                                  <span className="followup-chip-icon">{icon}</span>
                                  <span>{label}</span>
                                </button>
                              );
                            })}
                          </div>
                        )}
                        {/* Web search chip hidden — sources used internally only */}
                        <span className="msg-ts">{fmtTime(msg.ts)}</span>
                      </div>
                    </div>
                  </div>
                );
              })
            )}
            <div ref={bottomRef}/>
          </div>

          {/* Input */}
          <div className="chat-input-area">

            {/* File + text chips */}
            {(attachedFiles.length > 0 || pastedTexts.length > 0 || attachLoading) && (
              <div className="chips-row" style={{ position:'relative' }}>
                {attachedFiles.length > 0 && (
                  <div style={{
                    position:'absolute', top:-28, left:'50%', transform:'translateX(-50%)',
                    background:'#1a1f4e', color:'#fff', borderRadius:20,
                    fontSize:11, fontWeight:600, padding:'3px 10px',
                    display:'flex', alignItems:'center', gap:5, whiteSpace:'nowrap',
                    boxShadow:'0 2px 8px rgba(26,31,78,.25)',
                    pointerEvents:'none',
                  }}>
                    📎 {attachedFiles.filter(f => f.status==='attached').length} of {attachedFiles.length} file{attachedFiles.length>1?'s':''} attached
                  </div>
                )}
                {attachedFiles.map(f => (
                  <div key={f.id}
                    className={`file-chip${f.type.startsWith('image/') ? ' file-chip--img' : ''}`}
                    title={f.status === 'failed' ? (f.error || 'Failed to read file') : f.name}
                    style={{
                      borderColor: f.status === 'failed' ? '#fca5a5' : f.status === 'attaching' ? '#c4b5fd' : undefined,
                      background:  f.status === 'failed' ? '#fef2f2' : f.status === 'attaching' ? '#f5f3ff' : undefined,
                    }}
                  >
                    {/* Image thumbnail OR icon dot */}
                    {f.type.startsWith('image/') && f.content && f.status === 'attached'
                      ? <img src={f.content} alt="" style={{ width:22, height:22, borderRadius:5, objectFit:'cover', flexShrink:0 }} />
                      : f.status === 'attaching'
                        ? <div className="chip-spinner"/>
                        : <span className="chip-dot" style={{ background: f.status === 'failed' ? '#ef4444' : f.type === 'application/pdf' ? '#ef4444' : '#3b82f6' }} />
                    }

                    {/* Label: Loading… → ✓ name */}
                    <span className="chip-name">
                      {f.status === 'attaching'
                        ? `Loading ${f.name.length > 12 ? f.name.slice(0,10)+'…' : f.name}`
                        : f.status === 'failed'
                          ? `✗ ${f.name.length > 14 ? f.name.slice(0,12)+'…' : f.name}`
                          : `✓ ${f.name.length > 16 ? f.name.slice(0,14)+'…' : f.name}`
                      }
                    </span>

                    {/* Size — only when attached */}
                    {f.status === 'attached' && (
                      <span className="chip-size">{(f.size/1024).toFixed(0)}K</span>
                    )}

                    {f.status !== 'attaching' && (
                      <button className="chip-x" onClick={() => setAttachedFiles(prev => prev.filter(x => x.id !== f.id))} title={`Remove ${f.name}`}>×</button>
                    )}
                  </div>
                ))}
                {pastedTexts.map(p => (
                  <div key={p.id} className="file-chip file-chip--text" title={p.text.slice(0,80)}>
                    <span style={{ fontSize:11 }}>📋</span>
                    <span className="chip-name">{p.label}</span>
                    <span className="chip-size">{wordCount(p.text)}w</span>
                    <button className="chip-x" onClick={() => setPastedTexts(prev => prev.filter(x => x.id !== p.id))}>×</button>
                  </div>
                ))}
              </div>
            )}

            <div className="input-row">
              {/* Attach button */}
              <button
                className="attach-btn"
                onClick={() => fileInputRef.current?.click()}
                disabled={attachLoading || attachedFiles.length >= MAX_ATTACH}
                title="Attach files — PDF, DOCX, TXT, images"
              >
                {attachLoading
                  ? <div className="chip-spinner" style={{ width:15,height:15 }}/>
                  : <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><path d="M21.44 11.05l-9.19 9.19a6 6 0 01-8.49-8.49l9.19-9.19a4 4 0 015.66 5.66l-9.2 9.19a2 2 0 01-2.83-2.83l8.49-8.48"/></svg>
                }
              </button>

              {/* Report depth is only shown when the user has enabled Report mode. */}
              {reportModeEnabled && (
              <div className="report-tier-menu-wrap" ref={reportTierMenuRef}>
                <button
                  type="button"
                  className={`report-tier-trigger ${reportTierMenuOpen ? 'is-open' : ''}`}
                  onClick={() => setReportTierMenuOpen(prev => !prev)}
                  disabled={streaming}
                  aria-haspopup="menu"
                  aria-expanded={reportTierMenuOpen}
                  title="Choose report depth and style"
                >
                  <span className="report-tier-trigger-dot" aria-hidden="true" />
                  <span className="report-tier-trigger-text">
                    <span className="report-tier-trigger-kicker">Report</span>
                    <span className="report-tier-trigger-value">
                      {REPORT_TIER_OPTIONS.find(o => o.value === selectedReportTier)?.label ?? 'In-Detail'}
                      <span className="report-tier-trigger-sep" aria-hidden="true"> · </span>
                      {REPORT_STYLE_OPTIONS.find(o => o.value === selectedReportStyle)?.label ?? 'Market Currents'}
                    </span>
                  </span>
                  <svg className="report-tier-trigger-chevron" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>
                </button>

                {reportTierMenuOpen && (
                  <div className="report-tier-menu" role="dialog" aria-label="Report settings">
                    <div className="report-tier-menu-head"><span>Depth</span></div>
                    <div className="report-seg" role="radiogroup" aria-label="Report depth">
                      {REPORT_TIER_OPTIONS.map(option => (
                        <button
                          key={option.value}
                          type="button"
                          role="radio"
                          aria-checked={selectedReportTier === option.value}
                          className={`report-seg-item ${selectedReportTier === option.value ? 'is-selected' : ''}`}
                          onClick={() => setSelectedReportTier(option.value)}
                        >
                          <span className="report-seg-label">{option.label}</span>
                          <span className="report-seg-meta">{option.scope}</span>
                        </button>
                      ))}
                    </div>
                    <p className="report-tier-menu-note">{REPORT_TIER_OPTIONS.find(o => o.value === selectedReportTier)?.description}</p>
                    <div className="report-tier-menu-head"><span>Style</span></div>
                    <div className="report-style-grid" role="radiogroup" aria-label="Report style">
                      {REPORT_STYLE_OPTIONS.map(option => (
                        <button
                          key={option.value}
                          type="button"
                          role="radio"
                          aria-checked={selectedReportStyle === option.value}
                          className={`report-style-card ${selectedReportStyle === option.value ? 'is-selected' : ''}`}
                          onClick={() => setSelectedReportStyle(option.value)}
                        >
                          <span className={`report-style-swatch report-style-swatch--${option.value}`} aria-hidden="true" />
                          <span className="report-seg-label">{option.label}</span>
                          <span className="report-style-desc">{option.summary}</span>
                        </button>
                      ))}
                    </div>
                    <button type="button" className="report-tier-menu-done" onClick={() => setReportTierMenuOpen(false)}>Done</button>
                  </div>
                )}
              </div>
              )}

              <textarea
                ref={inputRef}
                className="chat-textarea"
                placeholder={attachedFiles.length > 0 || pastedTexts.length > 0
                  ? 'Ask anything about your attached files…'
                  : 'Ask about Nifty, stocks, MFs, RBI, IPOs…'}
                value={input}
                rows={1}
                onChange={e => {
                  setInput(e.target.value);
                  e.target.style.height = 'auto';
                  e.target.style.height = Math.min(e.target.scrollHeight, 130) + 'px';
                }}
                onKeyDown={onKey}
                onPaste={handlePaste}
                disabled={streaming}
              />

              <button
                type="button"
                className={`report-mode-btn ${reportModeEnabled ? 'is-active' : ''}`}
                onClick={() => {
                  setReportModeEnabled(enabled => !enabled);
                  setReportTierMenuOpen(false);
                }}
                disabled={streaming}
                aria-pressed={reportModeEnabled}
                title={reportModeEnabled
                  ? 'Report mode is enabled. The next query will generate a research report.'
                  : 'Enable Report mode for the next query. Otherwise, answers appear in chat.'}
              >
                <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M12 5.2a3.1 3.1 0 0 0-5.9 1.2A3.5 3.5 0 0 0 4.5 12a3.5 3.5 0 0 0 1.6 5.6A3.1 3.1 0 0 0 12 19.1Z"/>
                  <path d="M12 5.2a3.1 3.1 0 0 1 5.9 1.2 3.5 3.5 0 0 1 1.6 5.6 3.5 3.5 0 0 1-1.6 5.6 3.1 3.1 0 0 1-5.9 1.5Z"/>
                  <path d="M8.5 9.3c1 .1 1.7.8 1.7 1.8M8.3 15c.8-.6 1.5-.8 2.2-.5M15.5 9.3c-1 .1-1.7.8-1.7 1.8M15.7 15c-.8-.6-1.5-.8-2.2-.5M12 5.2v13.9"/>
                </svg>
                <span>Report</span>
              </button>

              <button className="send-btn" onClick={() => send(input)} disabled={!input.trim() || streaming || attachedFiles.some(f => f.status === 'attaching') || ragIndexing} aria-label="Send">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M22 2L11 13M22 2L15 22l-4-9-9-4 20-7z"/>
                </svg>
              </button>
            </div>
            <p className="input-hint">
              Growth Gradual · Enter to send · Shift+Enter for newline
              {reportModeEnabled ? ' · Report enabled: next query generates a report' : ''}
              {attachedFiles.length > 0 || pastedTexts.length > 0
                ? ` · ${attachedFiles.length + pastedTexts.length} item${attachedFiles.length + pastedTexts.length !== 1 ? 's' : ''} attached`
                : ''}
            </p>
          </div>

        </div>
      </div>
    </>
  );
}
