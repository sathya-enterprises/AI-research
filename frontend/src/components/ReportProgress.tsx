'use client';

import { useEffect, useState } from 'react';

/**
 * Live progress for a report job: a timeline of the pipeline's real stages.
 *
 * Every stage, message and percentage comes from the backend's progress events
 * (routes/report.py `_report_progress`). Nothing is simulated: a stage is marked
 * done only when the backend has moved past it, and the detail line is the
 * backend's own message ("Writing sections 3-4 of 9…").
 */

type StageId = 'understand' | 'search' | 'read' | 'evidence' | 'write' | 'check' | 'audit';

const STAGES: { id: StageId; label: string; phases: string[]; startsAt: number }[] = [
  { id: 'understand', label: 'Understanding your question', phases: ['queued', 'starting', 'prepare', 'intent'], startsAt: 0 },
  { id: 'search', label: 'Finding sources', phases: ['search'], startsAt: 14 },
  { id: 'read', label: 'Reading sources', phases: ['source_enrichment', 'site_crawl'], startsAt: 30 },
  { id: 'evidence', label: 'Checking the evidence', phases: ['evidence_ready', 'chart_data_gap', 'analysis'], startsAt: 49 },
  { id: 'write', label: 'Writing sections', phases: ['planning', 'parallel_analysis', 'writing'], startsAt: 61 },
  { id: 'check', label: 'Checking the draft', phases: ['validating_draft', 'expansion', 'pagination', 'quality_checks'], startsAt: 76 },
  { id: 'audit', label: 'Final audit', phases: ['independent_audit', 'finalizing', 'complete', 'draft_ready'], startsAt: 95 },
];

/** Index of the active stage: from the phase name, else from the percentage (older backends). */
export function activeStageIndex(phase: string | undefined, percent: number | undefined): number {
  const byPhase = STAGES.findIndex(s => s.phases.includes(String(phase || '')));
  if (byPhase >= 0) return byPhase;
  const p = Number.isFinite(percent) ? Number(percent) : 0;
  let index = 0;
  STAGES.forEach((s, i) => { if (p >= s.startsAt) index = i; });
  return index;
}

function formatElapsed(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return m ? `${m} min ${String(s).padStart(2, '0')} s` : `${s} s`;
}

export default function ReportProgress({
  title, phase, message, percent, startedAt,
}: {
  title: string;
  phase?: string;
  message?: string;
  percent?: number;
  startedAt?: number;
}) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  const active = activeStageIndex(phase, percent);
  const pct = Number.isFinite(percent) ? Math.max(0, Math.min(100, Number(percent))) : undefined;

  return (
    <section className="gg-progress" role="status" aria-live="polite" aria-label={`${title} in progress`}>
      <header className="gg-progress-head">
        <span className="gg-progress-title">{title}</span>
        <span className="gg-progress-meta">
          {startedAt ? formatElapsed(now - startedAt) : null}
          {pct !== undefined ? <b>{pct}%</b> : null}
        </span>
      </header>
      {pct !== undefined && (
        <div className="gg-progress-track" role="progressbar" aria-label="Report progress"
             aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}>
          <div className="gg-progress-fill" style={{ width: `${Math.max(2, pct)}%` }} />
        </div>
      )}
      <ol className="gg-progress-steps">
        {STAGES.map((stage, i) => {
          const state = i < active ? 'done' : i === active ? 'active' : 'pending';
          return (
            <li key={stage.id} className={`gg-step gg-step--${state}`} aria-current={state === 'active' ? 'step' : undefined}>
              <span className="gg-step-dot" aria-hidden="true">
                {state === 'done' ? (
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="3.5"
                       strokeLinecap="round" strokeLinejoin="round"><polyline points="5 12 10 17 19 8" /></svg>
                ) : null}
              </span>
              <span className="gg-step-text">
                <span className="gg-step-label">{stage.label}</span>
                {state === 'active' && message ? <span className="gg-step-detail" key={message}>{message}</span> : null}
              </span>
            </li>
          );
        })}
      </ol>
      <p className="gg-progress-note">Keep this tab open — the report appears here when it is ready. You can switch to other tabs meanwhile.</p>
    </section>
  );
}
