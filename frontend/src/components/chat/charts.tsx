'use client';

// Moved verbatim from GrowthGradualChat.tsx (split for maintainability; no behaviour change).
import { useState, useRef, useEffect } from 'react';
import type { ChartSeries, ChartSpec } from './types';
import { normalizeInlineChartType, isValidChart } from './markdown';
export const ED_PALETTE = ['#17845A', '#3461A8', '#C27C26', '#A64B63', '#0897AC', '#7E62B5'];
export const ED_NEGATIVE = '#9A3B2E';
export const ED_INK = '#2A2A2A';
export const ED_MUTED = '#6F6B63';
export const ED_GRID = '#ECE8E0';
export const ED_RULE = '#E1DDD5';
export const ED_FONT = "Inter, system-ui, -apple-system, 'Segoe UI', Arial, sans-serif";

export function edDecimals(v: number): number {
  if (!Number.isFinite(v) || Number.isInteger(v)) return 0;
  const text = String(v);
  if (text.includes('e')) return 2;
  return Math.min(3, (text.split('.')[1] ?? '').length);
}

/** Format a plotted value exactly as precise as the source value (no forced decimals). */
export function edFormatValue(v: number, unit?: string): string {
  const s = v.toLocaleString('en-IN', { minimumFractionDigits: 0, maximumFractionDigits: edDecimals(v) });
  const u = String(unit ?? '').trim();
  if (u === '%') return `${s}%`;
  return s;
}

export function edNiceScale(minV: number, maxV: number, includeZero: boolean): { lo: number; hi: number; ticks: number[] } {
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

export function edTick(t: number, top: number): string {
  if (Math.abs(t) < 1e-12) return '0';
  if (top >= 1_000_000) return `${+(t / 1_000_000).toFixed(2)}M`;
  if (top >= 10_000) return `${+(t / 1_000).toFixed(2)}k`;
  return t.toLocaleString('en-IN', { maximumFractionDigits: 2 });
}

/** Word-wrap a category label; ellipsize only what cannot fit in maxLines. */
export function edWrap(label: string, maxChars: number, maxLines = 2): string[] {
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

export function edSourceText(spec: ChartSpec): string {
  const raw = (spec as { source?: unknown; sources?: unknown; sourceIds?: unknown }).source
    ?? (spec as { sources?: unknown }).sources ?? (spec as { sourceIds?: unknown }).sourceIds;
  const list = Array.isArray(raw) ? raw.map(String) : raw ? [String(raw)] : [];
  return list.map((x) => x.trim()).filter(Boolean).map((x) => (/^S\d+$/i.test(x) ? `[${x.toUpperCase()}]` : x)).join(' · ');
}

export function edUnitLabel(spec: ChartSpec): string {
  const unit = String(spec.unit ?? '').trim();
  if (!unit) return '';
  return unit === '%' ? 'Per cent' : unit;
}

/** Measured plot width so SVG text renders at its true pixel size on every screen. */
export function useEdWidth(fallback = 640): [React.RefObject<HTMLDivElement | null>, number] {
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

export function EdChartFrame({ spec, legend, children }: { spec: ChartSpec; legend?: { name: string; color: string; value?: string }[]; children: React.ReactNode }) {
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

export function EdBarChart({ spec, horizontal = false }: { spec: ChartSpec; horizontal?: boolean }) {
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
                      <rect className="ed-bar ed-bar--h" x={Math.min(x0, x1)} y={y + 1} width={Math.max(1, Math.abs(x1 - x0))} height={Math.max(2, barH - 2)} rx={2} fill={colorFor(si, v)}>
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
                    <rect className="ed-bar ed-bar--v" x={bx + 1} y={Math.min(y0, y1)} width={Math.max(2, barW - 2)} height={Math.max(1, Math.abs(y1 - y0))} rx={2} fill={colorFor(si, v)}>
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

export function EdLineChart({ spec }: { spec: ChartSpec }) {
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
                <polyline key={k} className="ed-line" pathLength={1} points={seg.map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ')} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
              ) : null)}
              {pts.map((p, i) => p ? (
                <g key={i}>
                  <circle className="ed-dot" cx={p.x} cy={p.y} r={4} fill="#FFFFFF" stroke={color} strokeWidth={2}><title>{`${p.l}${series.length > 1 ? ` · ${s.name}` : ''}: ${edFormatValue(p.v, spec.unit)}`}</title></circle>
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
export function edFoldSliceTail<T extends { label?: unknown; value?: unknown }>(data: T[], limit = 10): Array<T | { label: string; value: number }> {
  if (data.length <= limit + 1) return data;
  const keep = new Set(data.map((d, i) => [Math.abs(Number(d.value)), i] as const).sort((a, b) => b[0] - a[0]).slice(0, limit).map(([, i]) => i));
  const tail = data.filter((_, i) => !keep.has(i));
  return [...data.filter((_, i) => keep.has(i)), { label: `Other (${tail.length})`, value: tail.reduce((acc, d) => acc + Number(d.value), 0) }];
}

export function EdPieChart({ spec, donut = false }: { spec: ChartSpec; donut?: boolean }) {
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
            <path key={i} className="ed-slice" d={s.path} fill={s.color} stroke="#FFFFFF" strokeWidth={2}><title>{`${s.label}: ${edFormatValue(s.value, spec.unit)} (${s.pct.toFixed(1)}%)`}</title></path>
          ))}
        </svg>
      </div>
    </EdChartFrame>
  );
}

// ─── Table fallback (used only if Datawrapper isn't configured/available) ────
export function TableChart({ spec }: { spec: ChartSpec }) {
  const columns = spec.columns ?? [];
  const rows = spec.rows ?? [];
  const isLabeledMatrix = typeof rows[0] === 'string' && Array.isArray(spec.values);
  const tableRows: string[][] = rows.map((row, ri) => {
    if (typeof row === 'string') {
      const matrixRow = spec.values?.[ri];
      return [row, ...(Array.isArray(matrixRow) ? matrixRow.map((cell) => String(cell ?? '—')) : [])];
    }
    return row.map((cell) => String(cell ?? '—'));
  });
  const tableColumns = isLabeledMatrix ? [String(spec.yLabel || 'Row'), ...columns] : columns;
  if (tableColumns.length < 2 || tableRows.length < 1) return null;
  return (
    <div className="chart-wrap">
      <div className="chart-title">{spec.title}</div>
      <div style={{ overflowX: 'auto' }}>
        <table className="md-table">
          <thead>
            <tr>{tableColumns.map((c,i) => <th key={i} className="md-th">{c}</th>)}</tr>
          </thead>
          <tbody>
            {tableRows.map((r,ri) => (
              <tr key={ri}>{r.map((cell,ci) => <td key={ci} className="md-td">{cell}</td>)}</tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function StructuredChartPreview({ spec }: { spec: ChartSpec }) {
  const type = normalizeInlineChartType(spec.type);
  const title = spec.title || `${type.replace(/_/g, ' ')} chart`;
  const chartSeries = spec.series ?? [];
  const scatter = type === 'scatter' || type === 'bubble';
  const rows = scatter
    ? (() => {
      const yByLabel = new Map((chartSeries[1]?.data ?? []).map((point) => [point.label, point]));
      const sizeByLabel = new Map((chartSeries[2]?.data ?? []).map((point) => [point.label, point]));
      return (chartSeries[0]?.data ?? []).flatMap((point) => {
        const label = String(point.label ?? '');
        const yPoint = yByLabel.get(label);
        if (!yPoint) return [];
        const row = [label, String(point.value ?? '—'), String(yPoint.value ?? '—')];
        if (type === 'bubble') row.push(String(sizeByLabel.get(label)?.value ?? '—'));
        return [row];
      });
    })()
    : chartSeries.flatMap((s) => (s.data ?? []).map((p) => {
    if (type === 'candlestick' || type === 'ohlc') return [p.label, p.open, p.high, p.low, p.close].map((v) => String(v ?? '—'));
    if (type === 'boxplot') return [p.label, p.min, p.q1, p.median, p.q3, p.max].map((v) => String(v ?? '—'));
    if (type === 'bullet') return [p.label, p.value, p.target].map((v) => String(v ?? '—'));
    if (type === 'population_pyramid') return [p.label, p.left ?? p.value, p.right ?? p.value].map((v) => String(v ?? '—'));
    return [p.label, p.value].map((v) => String(v ?? '—'));
  }));
  const cols = scatter
    ? ['Observation', String(spec.xLabel || chartSeries[0]?.name || 'X'), String(spec.yLabel || chartSeries[1]?.name || 'Y'),
      ...(type === 'bubble' ? [String(spec.sizeLabel || chartSeries[2]?.name || 'Size')] : [])]
    : type === 'candlestick' || type === 'ohlc' ? ['Period', 'Open', 'High', 'Low', 'Close']
    : type === 'boxplot' ? ['Group', 'Min', 'Q1', 'Median', 'Q3', 'Max']
    : type === 'bullet' ? ['Metric', 'Actual', 'Target']
    : type === 'population_pyramid' ? ['Cohort', 'Left group', 'Right group']
    : ['Label', chartSeries[0]?.name ?? 'Value'];
  const structured = type === 'heatmap' && Array.isArray(spec.rows) && Array.isArray(spec.columns) && Array.isArray(spec.values)
    ? (spec.rows as unknown as string[]).flatMap((r, i) => (spec.columns as string[]).map((c, j) => [String(r), String(c), String((spec.values as unknown[][])[i]?.[j] ?? '—')]))
    : rows;
  const structuredCols = type === 'heatmap' ? ['Row', 'Column', 'Value'] : cols;
  return (
    <div className="chart-wrap">
      <div className="chart-title">{title}</div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        <span style={{ background: 'var(--gg-surface)', color: 'var(--gg-forest)', padding: '3px 7px', borderRadius: 6, textTransform: 'uppercase', fontSize: 9, fontWeight: 800, letterSpacing: '.08em' }}>{type.replace(/_/g, ' ')}</span>
        <span style={{ color: 'var(--gg-muted)', fontSize: 10 }}>Detailed visual available in the generated report</span>
      </div>
      {['sankey', 'chord', 'network', 'tree', 'flowchart'].includes(type) && Array.isArray(spec.nodes) && Array.isArray(spec.links) ? (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 8 }}>
          {(spec.nodes as Array<string | {label?: string; id?: string}>).slice(0, 16).map((n, i) => <span key={i} style={{ border: '1px solid var(--gg-line)', borderRadius: 8, padding: '5px 8px', background: 'var(--gg-surface)', fontSize: 11 }}>{typeof n === 'string' ? n : (n.label ?? n.id ?? `Node ${i+1}`)}</span>)}
        </div>
      ) : null}
      {structured.length ? <div style={{ overflow: 'auto', maxHeight: 360 }}><table className="md-table"><thead><tr>{structuredCols.map((c, i) => <th key={i} className="md-th">{c}</th>)}</tr></thead><tbody>{structured.map((row, ri) => <tr key={ri}>{row.map((cell, ci) => <td key={ci} className="md-td">{cell}</td>)}</tr>)}</tbody></table></div> : (
        <div style={{ color: 'var(--gg-muted)', fontSize: 12 }}>This chart uses a specialized structure (such as sets, events, or nodes/links) and is rendered in the full report.</div>
      )}
    </div>
  );
}

export function ChartBlock({ spec }: { spec: ChartSpec }) {
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
          style={{ fontSize: 10, color: 'var(--gg-forest)', fontFamily: 'DM Sans,sans-serif', textDecoration: 'none' }}>
          Open in Datawrapper ↗
        </a>
      </div>
    </div>
  );
}

// ─── Presentation helpers ─────────────────────────────────────────────────────
