// Moved verbatim from GrowthGradualChat.tsx (split for maintainability; no behaviour change).
import type { ChartDataPoint, ChartSpec, ReportData, Message } from './types';
export function requestedReportChartIndex(question: string, charts: ChartSpec[]): number | undefined {
  const numbered = question.match(/\b(?:chart|figure|graph|plot|visualization|visualisation)\s*(?:number|no\.?|#)?\s*(\d+)\b/i);
  if (numbered) {
    const index = Number(numbered[1]) - 1;
    return index >= 0 && index < charts.length ? index : undefined;
  }
  const queryWords = new Set((question.toLowerCase().match(/[a-z0-9]{3,}/g) || [])
    .filter(word => !['about', 'chart', 'does', 'explain', 'figure', 'graph', 'report', 'show', 'that', 'the', 'this', 'what', 'with'].includes(word)));
  let best: { index: number; score: number } | undefined;
  charts.forEach((chart, index) => {
    const chartText = [chart.title, chart.type, chart.xLabel, chart.yLabel].map(value => String(value || '')).join(' ');
    const titleWords = new Set((chartText.toLowerCase().match(/[a-z0-9]{3,}/g) || [])
      .filter(word => !['about', 'chart', 'does', 'explain', 'figure', 'graph', 'report', 'show', 'that', 'the', 'this', 'what', 'with'].includes(word)));
    const overlap = [...queryWords].filter(word => titleWords.has(word)).length;
    const typeWords = new Set((String(chart.type || '').toLowerCase().match(/[a-z0-9]{3,}/g) || []));
    if (typeWords.has('heatmap')) typeWords.add('matrix');
    const score = Math.max(overlap / Math.max(1, titleWords.size), [...queryWords].some(word => typeWords.has(word)) ? 0.5 : 0);
    if (overlap && (!best || score > best.score)) best = { index, score };
  });
  return best && best.score >= 0.25 ? best.index : undefined;
}

export function isReportReferenceQuestion(question: string, messages: Message[]): boolean {
  const text = question.trim();
  if (/\b(?:report|document|chart|graph|figure|table|section|chapter|appendix|visualization|visualisation|finding|key takeaway|executive summary|uploaded file|attached file)\b|\b(?:whole|entire|overall)\s+(?:report|document|analysis|file)\b|\b(?:summari[sz]e|overview|walk me through|inch by inch|section by section|chart by chart)\b/i.test(text)) return true;
  const words = text.match(/[\w'-]+/g) || [];
  if (words.length <= 12 && /\b(?:it|this|that|these|those|above|previous)\b/i.test(text)) return true;
  return words.length <= 2 && /^\s*(?:why|how|what|which)\b/i.test(text)
    && messages.some(message => message.role === 'assistant' && Boolean(message.text.trim()));
}

/** Send only readable report material and chart values to ordinary follow-up chat. */
export function buildReportChatContext(
  reportData: ReportData | undefined,
  originalQuestion: string,
  chatQuestion: string,
): Record<string, unknown> | undefined {
  if (!reportData?.report) return undefined;
  const sourceCharts = (reportData.charts || []).slice(0, 80);
  const requestedIndex = requestedReportChartIndex(chatQuestion, sourceCharts);
  let pointBudget = 2_500;
  const charts = sourceCharts.map((chart, chartIndex) => {
    if (requestedIndex !== undefined && chartIndex !== requestedIndex) {
      return {
        title: String(chart.title || '').slice(0, 220),
        type: String(chart.type || '').slice(0, 40),
        unit: String(chart.unit || '').slice(0, 80),
        xLabel: String(chart.xLabel || '').slice(0, 100),
        yLabel: String(chart.yLabel || '').slice(0, 100),
        series: [],
      };
    }
    const series = (chart.series || []).slice(0, 12).map(item => {
      const sourcePoints = item.data || [];
      let selectedPoints: ChartDataPoint[];
      if (requestedIndex !== undefined) {
        selectedPoints = sourcePoints.slice(0, Math.max(0, pointBudget));
      } else {
        selectedPoints = sourcePoints.length <= 2
          ? sourcePoints
          : [sourcePoints[0], sourcePoints[sourcePoints.length - 1]];
        selectedPoints = selectedPoints.slice(0, Math.max(0, pointBudget));
      }
      pointBudget -= selectedPoints.length;
      return {
        name: String(item.name || 'Series').slice(0, 100),
        data: selectedPoints.map(point => ({
          label: String(point.label ?? '').slice(0, 120),
          value: typeof point.value === 'number' || typeof point.value === 'string'
            ? point.value
            : String(point.value ?? ''),
        })),
      };
    });
    return {
      title: String(chart.title || '').slice(0, 220),
      type: String(chart.type || '').slice(0, 40),
      unit: String(chart.unit || '').slice(0, 80),
      xLabel: String(chart.xLabel || '').slice(0, 100),
      yLabel: String(chart.yLabel || '').slice(0, 100),
      series,
      columns: Array.isArray(chart.columns) ? chart.columns.slice(0, 14).map(String) : undefined,
      rows: Array.isArray(chart.rows) ? chart.rows.slice(0, 24).map(row =>
        Array.isArray(row) ? row.slice(0, 14).map(cell => String(cell ?? '').slice(0, 120)) : String(row ?? '').slice(0, 120),
      ) : undefined,
      values: Array.isArray(chart.values) ? chart.values.slice(0, 24).map(row =>
        Array.isArray(row) ? row.slice(0, 14).map(cell => cell == null ? null : cell) : [],
      ) : undefined,
    };
  });
  const report = String(reportData.report || '');
  return {
    title: String(reportData.title || '').slice(0, 300),
    question: String(originalQuestion || '').slice(0, 500),
    summary: String(reportData.summary || '').slice(0, 6_000),
    report: report.slice(0, 250_000),
    reportTruncated: report.length > 250_000,
    keyStats: (reportData.keyStats || []).slice(0, 24).map(stat => ({
      label: String(stat.label || '').slice(0, 120),
      value: String(stat.value || '').slice(0, 120),
      change: String(stat.change || '').slice(0, 100),
    })),
    charts,
  };
}

// Human-readable names for the backend's failedStage values.
export const REPORT_STAGE_LABELS: Record<string, string> = {
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
  sections_missing: 'section writing (some sections could not be written)',
  writer_unavailable: 'report writer (no AI model was available)',
  report_integrity: 'report integrity check',
};
export const reportStageLabel = (stage?: string | null) => (stage ? (REPORT_STAGE_LABELS[stage] ?? stage.replace(/_/g, ' ')) : '');
export const isDraftReport = (rd?: { publicationOutcome?: string; publicationStatus?: string } | null) =>
  !!rd && (rd.publicationOutcome === 'draft' || (rd.publicationStatus === 'FAIL' && rd.publicationOutcome !== 'published'));
/** Split a display value such as "₹2,41,47,183 crore" or "2.2%" into prefix / figure / unit.
 *  Presentation only — the figure text is kept exactly as supplied (no rounding). */
export function splitMetricValue(value: string): { prefix: string; number: string; unit: string } | null {
  const m = /^(\s*[^\d\-+−.,]*?)([-+−]?\d[\d,]*(?:\.\d+)?\s*%?)(.*)$/.exec(String(value ?? ''));
  if (!m) return null;
  return { prefix: m[1].trim(), number: m[2].replace(/\s+%/, '%').trim(), unit: m[3].trim() };
}
/** Stable DOM id for a report section heading, used by the in-report navigation. */
export function reportSectionDomId(msgId: string, heading: string): string {
  return `rpt-${msgId}-${String(heading).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 60)}`;
}
/** Level-1/2 headings of the generated report, in order (drives "In this report"). */
export function reportOutline(markdown: string): string[] {
  const out: string[] = [];
  for (const m of String(markdown ?? '').matchAll(/^(#{1,2})\s+(.+)$/gm)) {
    const h = m[2].replace(/[*_`]/g, '').trim();
    if (h && !out.includes(h)) out.push(h);
  }
  return out;
}
