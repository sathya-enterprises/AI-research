// Moved verbatim from GrowthGradualChat.tsx (split for maintainability; no behaviour change).
import type { AttachedFile } from './attachments';
export interface Source { title: string; url: string; snippet: string; }
export interface ReportSource { id?: string; title: string; url?: string; publisher?: string; kind?: string; page_fetch_status?: string; evidence_origin?: string; claim_evidence_matched?: boolean; }
export interface ChartDataPoint { label: string; value: number; [key: string]: unknown; }
export interface ChartSeries { name: string; data: ChartDataPoint[]; color?: string; }
/** Route a Tavily/third-party image URL through our server-side proxy to bypass hotlink protection. */
export function proxyImg(url: string): string {
  if (!url) return url;
  if (url.startsWith('/') || url.startsWith('data:')) return url;
  return `/api/image-proxy?url=${encodeURIComponent(url)}`;
}

export interface DatawrapperInfo { id: string; embedUrl: string; publicUrl: string; }
export interface ChartSpec { type: string; title: string; series?: ChartSeries[]; unit?: string; columns?: string[]; rows?: string[] | string[][]; values?: unknown[][]; datawrapper?: DatawrapperInfo; [key: string]: unknown; }
export interface WebImage { url: string; caption?: string; sourceUrl?: string; sourceURL?: string; sourceTitle?: string; publisher?: string; sourceId?: string; sourceIds?: string[]; assetUrl?: string; }
// Mirrors backend/routes/report.py's _sanitize_theme output shape — only
// ever set when the user's request explicitly asked for a visual style
// (see report.py SYSTEM_PROMPT's THEME section). fontFamily/customCss are not
// fully represented in the flat PDF export, which is the only report export exposed.
export type ReportTier = 'detailed' | 'in_depth';

export const REPORT_TIER_OPTIONS: { value: ReportTier; label: string; scope: string; description: string }[] = [
  { value: 'detailed', label: 'In-Detail', scope: '25–30 pages', description: 'Core findings plus deeper comparisons, history and risks, with charts.' },
  { value: 'in_depth', label: 'Deep Research', scope: '70–100 pages', description: 'Widest source base and granular, chart-heavy analysis; length follows the evidence.' },
];

export type ReportStyle = 'market_currents' | 'feature_article';

export const REPORT_STYLE_OPTIONS: { value: ReportStyle; label: string; summary: string; description: string }[] = [
  { value: 'market_currents', label: 'Market Currents', summary: 'Branded house report', description: 'Growth Gradual house style: branded navy cover, numbered sections, KPI cards, Desk View notes and ARN footer. Monthly market reviews use the fixed 10-section format.' },
  { value: 'feature_article', label: 'Feature Article', summary: 'Long-read editorial', description: 'Long-read editorial: kicker, headline, standfirst, narrative sections and infographic charts.' },
];

export interface ReportTheme { primaryColor?: string; accentColor?: string; toneNote?: string; fontFamily?: string; customCss?: string; richStyleNeeded?: boolean; }
export interface ReportPublicationAudit { status?: 'clean' | 'review_required'; flags?: string[]; controlMarkerCount?: number; danglingChartRefs?: number[]; danglingImageRefs?: number[]; unresolvedCitationIds?: string[]; duplicateTableCount?: number; suspiciousEntityTableValues?: { column?: string; repeatedValue?: string; rowsWithValue?: number; rowsWithAvailableValue?: number; reason?: string }[]; chartTableVerificationConflicts?: { chartTitle?: string; entity?: string; period?: string; reason?: string }[]; chartEvidenceFailures?: { chartTitle?: string; label?: string; value?: string; sourceIds?: string[]; reason?: string }[]; imagesMissingPublisherPage?: number[]; note?: string; }
export interface GeminiAuditFinding { severity?: string; category?: string; location?: string; issue?: string; evidence?: string; recommended_fix?: string; }
export interface GeminiReportAudit { status?: 'PASS' | 'WARNING' | 'FAIL' | 'unavailable' | 'not_run'; summary?: string; blocking?: boolean; reason?: string; criticalIssues?: string[]; findings?: GeminiAuditFinding[]; checks?: Record<string, string>; provider?: string; model?: string; }
export interface ReportQualityReview { publicationAudit?: ReportPublicationAudit; chartDensity?: { profile?: { minimum?: number | null; maximum?: number | null; label?: string }; withinTarget?: number | null; measuredCharts?: number; applicableCharts?: number; exceptionsOrMisses?: { title?: string; status?: string; count?: number; minimum?: number | null; maximum?: number | null }[] }; }
export interface ReportPageBudget { minimum?: number | null; target?: number | null; maximum?: number | null; renderedPages?: number | null; measured?: boolean; minimumReached?: boolean | null; warning?: string | null; }
export interface ReportRagStatus { requested?: boolean; status?: string; retrievedChunks?: number; sourceCount?: number; sourceIds?: string[]; restoreStatus?: string; rerankerRan?: boolean; message?: string; }
export interface ReportRevision { revision: number; savedAt: number; note: string; report: string; title?: string; charts: ChartSpec[]; images?: WebImage[]; sources?: ReportSource[]; presentation?: Record<string, unknown>; keyStats?: {label:string;value:string;change?:string}[]; summary?: string; theme?: ReportTheme; qualityReview?: ReportQualityReview; publicationStatus?: string; publicationOutcome?: string; failedStage?: string | null; sessionId?: string; ragStatus?: ReportRagStatus; }
export interface ReportData { reportId?: string; revision?: number; revisionHistory?: ReportRevision[]; report: string; reportTier?: ReportTier; reportStyle?: ReportStyle; kicker?: string; pageTarget?: {min:number | null; target:number | null; max:number}; pageBudget?: ReportPageBudget; title?: string; charts: ChartSpec[]; images?: WebImage[]; presentation?: Record<string, unknown>; keyStats: {label:string;value:string;change?:string}[]; summary: string; fileImages?: {name:string;mimeType:string;data:string}[]; sourceDocuments?: {name:string;text:string;file_type?:string}[]; sources?: ReportSource[]; recommendedFormat?: 'pdf'; theme?: ReportTheme; qualityReview?: ReportQualityReview; geminiAudit?: GeminiReportAudit; publicationStatus?: 'PASS' | 'WARNING' | 'FAIL' | string; auditSummary?: {status?: string; newBlockingFindings?: number; remainingBlockingFindings?: number; publisherPagesVerified?: number}; sessionId?: string; ragStatus?: ReportRagStatus; publicationOutcome?: 'published' | 'draft' | 'structural_failure' | string; failedStage?: string | null; blockingCategories?: string[]; failureSummary?: ReportFailureItem[]; chartPipeline?: { stages?: { stage: string; count: number; removed?: { title?: string; reason?: string }[] }[]; finalCount?: number }; }
export interface ReportFailureItem { stage?: string; issue?: string; claim?: string; sources?: string[]; values?: string[]; }

export interface KbCompany { id: number; ticker: string; name: string; downloadUrl: string; }
export interface Message {
  id: string; role: 'user' | 'assistant'; text: string; ts: number;
  sources?: Source[]; searchPerformed?: boolean; queryType?: string;
  kbCompany?: KbCompany | null;
  reportData?: ReportData; reportLoading?: boolean; reportError?: string;
  reportProgress?: string; reportProgressPercent?: number; reportPreview?: string; reportPreviewTruncated?: boolean;
  reportPhase?: string; reportStartedAt?: number;
  inlineCharts?: ChartSpec[]; wantsVisual?: boolean;
  // Report request context is retained for retrying a failed opt-in report.
  reportEligible?: boolean; reportQuestion?: string; reportFiles?: AttachedFile[];
  followUpQuestions?: string[];
}
export interface Conversation {
  id: string; title: string; messages: Message[]; ts: number;
}

// Matches a follow-up message that asks to edit/elaborate part of an
// already-generated report (e.g. "expand the risk factors section", "make
// the executive summary shorter"). When this fires AND a report already
// exists in the conversation, `send()` routes to editReportSection() instead
// of a fresh report/chat reply — see backend/routes/report.py:edit_report_section
// for how the single matching section is located and replaced.
export interface StreamMeta { type:'meta'; searchPerformed:boolean; resultCount:number; queryType:string; sources:Source[]; kbCompany?: KbCompany | null; }

/** Current Supabase access token, if logged in — attached so the backend can
 *  tie this session's chat history to the user's account. */
