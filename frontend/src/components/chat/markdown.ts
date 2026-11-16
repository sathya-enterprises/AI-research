// Moved verbatim from GrowthGradualChat.tsx (split for maintainability; no behaviour change).
import type { ChartSeries, ChartSpec } from './types';
export function normalizeReportMarkdownSource(value: string): string {
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
export function recoverChartTitlePlaceholders(value: string, charts: Array<{ title?: string }> | undefined): string {
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

export function renderMd(text: string): string {
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
export function extractInlineCharts(text: string): { spec: ChartSpec; raw: string }[] {
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
export function splitTextAndCharts(text: string): Array<{ type: 'text'; content: string } | { type: 'chart'; spec: ChartSpec }> {
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
export function normalizeInlineChartType(value: unknown): string {
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

export function isValidChart(spec: ChartSpec): boolean {
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
