'use client';

// Moved verbatim from GrowthGradualChat.tsx (split for maintainability; no behaviour change).
import { useState, useEffect } from 'react';
import ReportProgress from '@/components/ReportProgress';
import { proxyImg, REPORT_TIER_OPTIONS } from './types';
import type { ReportTier, ReportRevision, ReportData, Message } from './types';
import { reportStageLabel, isDraftReport, splitMetricValue, reportSectionDomId, reportOutline } from './reportContext';
import { fmtDate } from './storage';
import { normalizeReportMarkdownSource, recoverChartTitlePlaceholders, renderMd } from './markdown';
import { ChartBlock } from './charts';
export type PresentationSection = {
  title?: string;
  section_type?: string;
  layout?: string;
  density?: string;
  emphasis?: string;
  order?: number;
  blocks?: Array<{ kind?: string }>;
};

export type ReportPresentation = {
  domain?: string;
  cover?: { enabled?: boolean; treatment?: string };
  executive_summary?: { placement?: string };
  sections?: PresentationSection[];
  default_layout?: string;
  default_density?: string;
};

export function getReportPresentation(rd?: ReportData): ReportPresentation | null {
  const p = rd?.presentation;
  return p && typeof p === 'object' ? p as ReportPresentation : null;
}

export function normalizeSectionTitle(value: unknown): string {
  return String(value ?? '')
    .replace(/^\s*\d+[.)]\s*/, '')
    .trim()
    .toLowerCase();
}

export function presentationSectionFor(title: string, presentation: ReportPresentation | null): PresentationSection | null {
  const wanted = normalizeSectionTitle(title);
  if (!wanted || !presentation?.sections?.length) return null;
  return presentation.sections.find((s) => normalizeSectionTitle(s.title) === wanted) ?? null;
}

export function presentationClass(section: PresentationSection | null, presentation: ReportPresentation | null): string {
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
export function getReportSectionOptions(reportData?: ReportData): string[] {
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
export function ReportPromptModal({
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
    ? 'Example: Compare EV adoption in India, China and Europe in an In-Detail report.'
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
export function ReportPanel({ msg, question, hasPriorContext, onGenerate, onExperiment, onAction, onRestore, initialTier = 'detailed' }: { msg: Message; question: string; hasPriorContext: boolean; onGenerate: (includeContext: boolean, tier: ReportTier) => void; onExperiment: (prompt: string, tier: ReportTier) => void; onAction: (instruction: string) => void; onRestore: (revision: ReportRevision) => void; initialTier?: ReportTier }) {
  const [open, setOpen]               = useState(false);
  const [pdfLoading, setPdfLoading] = useState(false);
  const [artifactError, setArtifactError] = useState<string | null>(null);
  const [promptMode, setPromptMode] = useState<'experiment' | 'action' | null>(null);
  const [promptSubmitting, setPromptSubmitting] = useState(false);
  // Default ON to match the previous always-include behaviour; only ever
  // shown when there's actually prior conversation to include (i.e. not the
  // very first response in the thread).
  const [includeContext, setIncludeContext] = useState(true);
  const attachmentReport = (msg.reportFiles ?? []).some(file => file.status === 'attached');
  // Derive the depth from current report state. Attachment analysis stays at
  // the data-focused tier without synchronizing props through an effect.
  const selectedTier: ReportTier = attachmentReport
    ? 'detailed'
    : (msg.reportData?.reportTier ?? initialTier);

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
            <ReportProgress
              title={`${attachmentReport ? 'Attachment analysis' : (REPORT_TIER_OPTIONS.find(o => o.value === selectedTier)?.label ?? 'Research')} report`}
              phase={msg.reportPhase}
              message={msg.reportProgress}
              percent={msg.reportProgressPercent}
              startedAt={msg.reportStartedAt}
            />
          )}
          {loading && !!msg.reportPreview?.trim() && (
            <div className="report-preview-panel gg-rise">
              <div className="report-preview-head">
                <span className="report-preview-label">Draft — read while it is checked</span>
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
              <span className="report-tier-status-label">{attachmentReport ? 'Report scope' : 'Report depth'}</span>
              <strong>{attachmentReport ? 'Attachment analysis' : (REPORT_TIER_OPTIONS.find(o => o.value === selectedTier)?.label ?? 'In-Detail')}</strong>
              <span>{attachmentReport ? 'Planned from the attached data' : (REPORT_TIER_OPTIONS.find(o => o.value === selectedTier)?.scope ?? 'Expanded analysis')}</span>
            </div>
          )}
          {!loading && !done && !!error && eligible && (
            <button className="report-btn" onClick={() => onGenerate(hasPriorContext ? includeContext : false, attachmentReport ? 'detailed' : selectedTier)}>
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
                Growth Gradual · {attachmentReport ? 'Attachment analysis' : (REPORT_TIER_OPTIONS.find(o => o.value === (rd.reportTier ?? selectedTier))?.label ?? 'Research')} report
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
