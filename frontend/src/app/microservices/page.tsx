'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import type { CSSProperties } from 'react';

type Service = {
  id: string; name: string; role?: string; status: string; mode?: string;
  probeMs?: number; model?: string; embeddingDimension?: number; rerankerEnabled?: boolean;
  configured?: boolean; table?: string; readAvailable?: boolean; writeAvailable?: boolean;
  storageMode?: string; groqKeysConfigured?: number; geminiKeysConfigured?: number;
  exaKeysConfigured?: number; serpapiKeysConfigured?: number; serperKeysConfigured?: number;
  provider?: string; primaryPath?: string; fallbackPath?: string; reason?: string;
};
type Event = {
  timestamp: string; traceId?: string; service: string; stage: string; status: string;
  durationMs?: number | null; details?: Record<string, unknown>;
};
type Overview = {
  generatedAt?: number; traceId?: string; services?: Service[]; legacyServices?: Service[];
  recentEvents?: Event[]; ragRecentEvents?: Event[]; rag?: {
    reachable?: boolean; ready?: boolean; probeMs?: number; sessionCountInProcess?: number;
    chunkCountInProcess?: number; restoreModel?: string;
    vectorStorage?: { ready?: boolean; configured?: boolean; reason?: string };
    reranker?: { enabled?: boolean; model?: string };
  }; notes?: string[];
};

const panel: CSSProperties = {
  background: '#fff', border: '1px solid #e3eae6', borderRadius: 14,
  boxShadow: '0 2px 10px rgba(20,55,40,0.035)',
};
const labelStyle: CSSProperties = {
  color: '#718078', fontSize: 10, letterSpacing: 1.1, fontWeight: 800,
  textTransform: 'uppercase', fontFamily: 'Arial, sans-serif',
};

function statusColor(status: string) {
  if (['healthy', 'connected', 'ok'].includes(status)) return { dot: '#16845d', bg: '#e9f6ef', text: '#176447' };
  if (['degraded', 'partial', 'no_match'].includes(status)) return { dot: '#c78a21', bg: '#fff5df', text: '#8a5d12' };
  return { dot: '#c34a42', bg: '#fcefed', text: '#9e342d' };
}
function formatTime(value?: string | number) {
  if (!value) return '—';
  const date = typeof value === 'number' ? new Date(value * 1000) : new Date(value);
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}
function pretty(value: unknown) {
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  return String(value);
}

export default function MicroservicesPage() {
  const [data, setData] = useState<Overview | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  const [lastChecked, setLastChecked] = useState('');
  const [traceFilter, setTraceFilter] = useState('');
  const [autoRefresh, setAutoRefresh] = useState(true);

  const refresh = useCallback(async (quiet = false) => {
    if (!quiet) setRefreshing(true);
    try {
      const response = await fetch('/api/diagnostics/overview', {
        cache: 'no-store', signal: AbortSignal.timeout(16000),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result?.error || `Diagnostics returned HTTP ${response.status}`);
      setData(result as Overview);
      setError('');
      setLastChecked(new Date().toISOString());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Diagnostics request failed.');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => {
    if (!autoRefresh) return;
    const timer = window.setInterval(() => { void refresh(true); }, 15000);
    return () => window.clearInterval(timer);
  }, [autoRefresh, refresh]);

  const events = useMemo(() => {
    const all = [...(data?.recentEvents ?? []), ...(data?.ragRecentEvents ?? [])];
    const unique = new Map<string, Event>();
    for (const event of all) {
      const key = `${event.timestamp}|${event.traceId}|${event.service}|${event.stage}`;
      unique.set(key, event);
    }
    const list = [...unique.values()].sort((a, b) => Date.parse(b.timestamp) - Date.parse(a.timestamp));
    const filter = traceFilter.trim().toLowerCase();
    return filter ? list.filter(e => (e.traceId ?? '').toLowerCase().includes(filter)) : list;
  }, [data, traceFilter]);

  return (
    <main style={{ minHeight: 'calc(100vh - 70px)', background: '#f5f8f6', padding: '30px 22px 72px', color: '#18362a' }}>
      <div style={{ maxWidth: 1440, margin: '0 auto' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 16, flexWrap: 'wrap', marginBottom: 24 }}>
          <div>
            <div style={{ ...labelStyle, color: '#237455', marginBottom: 8 }}>Growth Gradual · Operations</div>
            <h1 style={{ fontSize: 29, lineHeight: 1.15, margin: 0, color: '#123d2c', fontWeight: 750 }}>Microservices Monitor</h1>
            <p style={{ color: '#718078', fontSize: 13, margin: '9px 0 0', maxWidth: 700, lineHeight: 1.55 }}>
              Follow the journey of an uploaded file or chat question through browser extraction, FastAPI orchestration, RAG retrieval, vector storage and streamed AI output.
            </p>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
            <label style={{ display: 'flex', alignItems: 'center', gap: 7, color: '#64756c', fontSize: 11, fontWeight: 600 }}>
              <input type="checkbox" checked={autoRefresh} onChange={e => setAutoRefresh(e.target.checked)} /> Auto-refresh · 15s
            </label>
            <button onClick={() => void refresh()} disabled={refreshing} style={{ border: '1px solid #cbdcd2', background: '#fff', color: '#145b43', borderRadius: 9, padding: '9px 13px', fontWeight: 700, cursor: refreshing ? 'wait' : 'pointer' }}>
              {refreshing ? 'Checking…' : '↻ Refresh status'}
            </button>
          </div>
        </div>

        {error && <div role="alert" style={{ ...panel, padding: 14, marginBottom: 18, borderColor: '#edc7c1', background: '#fff7f5', color: '#9e342d', fontSize: 13 }}>
          <strong>Diagnostics unavailable.</strong> {error} Check that Next.js can reach the FastAPI backend and that the backend has been restarted with the observability update.
        </div>}

        <section style={{ ...panel, padding: 18, marginBottom: 20 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, marginBottom: 15, flexWrap: 'wrap' }}>
            <div><div style={labelStyle}>Live dependency map</div><h2 style={{ fontSize: 17, margin: '5px 0 0', color: '#214b37' }}>What happens to each input?</h2></div>
            <div style={{ fontSize: 10, color: '#8b9991' }}>Last checked: {lastChecked ? formatTime(lastChecked) : '—'}</div>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(205px, 1fr))', gap: 9 }}>
            {[
              ['01 · Browser', 'User prompt / uploaded file', 'Chat text is sent to Next.js. PDFs, spreadsheets and text files are extracted in the browser.'],
              ['02 · Next.js', '/api/chat/ai-sdk · /api/rag/index', 'Proxy routes forward the chat stream and extracted document text to FastAPI.'],
              ['03 · FastAPI', '/api/chat · orchestrator', 'The orchestrator decides whether to call document_search, web search or other tools.'],
              ['04 · RAG service', 'Chunk → embed → retrieve → rerank', 'SentenceTransformer creates 384-dimensional vectors; pgvector finds candidates and an optional cross-encoder reranks them.'],
              ['05 · Supabase', 'Postgres pgvector + Storage', 'Vector chunks are searched in rag_chunks; original extracted text/OKF bundle is archived in Storage.'],
              ['06 · LLM → UI', 'Groq → Gemini fallback → SSE', 'Retrieved context is added to the generation prompt and the answer is streamed back to the chat UI.'],
            ].map(([step, title, description], index) => (
              <div key={step} style={{ background: '#f8fbf9', border: '1px solid #e6eee9', borderRadius: 10, padding: 12, position: 'relative' }}>
                <div style={{ ...labelStyle, color: '#398061', fontSize: 9 }}>{step}</div>
                <div style={{ color: '#214b37', fontWeight: 750, fontSize: 12, marginTop: 7 }}>{title}</div>
                <div style={{ color: '#738178', fontSize: 11, lineHeight: 1.5, marginTop: 6 }}>{description}</div>
                {index < 5 && <div aria-hidden="true" style={{ position: 'absolute', right: -8, top: '45%', zIndex: 1, background: '#e4efe8', color: '#4d8a6a', width: 16, height: 16, borderRadius: 20, textAlign: 'center', lineHeight: '16px', fontSize: 11 }}>›</div>}
              </div>
            ))}
          </div>
          <div style={{ marginTop: 12, color: '#8a9890', fontSize: 10, lineHeight: 1.5 }}>
            Chat retrieval is initiated by the FastAPI orchestrator's <code>document_search</code> tool; it is not a separate direct RAG branch in the chat route.
          </div>
        </section>

        <section style={{ marginBottom: 22 }}>
          <div style={{ ...labelStyle, marginBottom: 10 }}>Active services and dependencies</div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(235px, 1fr))', gap: 12 }}>
            {(data?.services ?? []).map(service => {
              const color = statusColor(service.status);
              return <article key={service.id} style={{ ...panel, padding: 15 }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
                  <div style={{ color: '#244b38', fontWeight: 750, fontSize: 13 }}>{service.name}</div>
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, color: color.text, background: color.bg, borderRadius: 20, padding: '4px 8px', fontSize: 9, fontWeight: 800, textTransform: 'uppercase' }}><span style={{ width: 6, height: 6, borderRadius: 10, background: color.dot }} />{service.status.replaceAll('_', ' ')}</span>
                </div>
                <div style={{ color: '#78877e', fontSize: 11, lineHeight: 1.45, minHeight: 32, marginTop: 8 }}>{service.role}</div>
                <div style={{ borderTop: '1px solid #edf1ee', marginTop: 10, paddingTop: 9, color: '#617268', fontSize: 10, lineHeight: 1.7 }}>
                  {service.id === 'rag_service' && <>{service.model || 'Embedding model'} · {service.embeddingDimension ?? '—'} dimensions<br />Reranker: {service.rerankerEnabled ? 'enabled' : 'not loaded'} · Probe: {service.probeMs ?? '—'} ms<br />Loaded sessions: {data?.rag?.sessionCountInProcess ?? '—'} · cached chunks: {data?.rag?.chunkCountInProcess ?? '—'}</>}
                  {service.id === 'supabase_pgvector' && <>Table: {service.table || 'rag_chunks'}<br />Configured: {pretty(service.configured)}</>}
                  {service.id === 'supabase_storage' && <>Read: {pretty(service.readAvailable)} · Write: {pretty(service.writeAvailable)}<br />Mode: {service.storageMode || '—'}</>}
                  {service.id === 'llm_providers' && <>Groq keys configured: {service.groqKeysConfigured ?? 0}<br />Gemini keys configured: {service.geminiKeysConfigured ?? 0} · {service.primaryPath} → {service.fallbackPath}</>}
                  {service.id === 'web_search' && <>Provider: {service.provider || '—'}<br />Exa: {service.exaKeysConfigured ?? 0} · SerpAPI: {service.serpapiKeysConfigured ?? 0} · Serper: {service.serperKeysConfigured ?? 0}</>}
                  {service.id === 'api_backend' && <>Request orchestration and streamed responses<br />FastAPI · active route</>}
                  {service.id === 'frontend' && <>File extraction and stream rendering<br />Next.js · browser</>}
                </div>
              </article>;
            })}
            {loading && !data && <div style={{ ...panel, padding: 18, color: '#7b8a81', fontSize: 12 }}>Checking service status…</div>}
          </div>
        </section>

        <section style={{ ...panel, padding: 18, marginBottom: 20 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap', marginBottom: 12 }}>
            <div><div style={labelStyle}>Request timeline</div><h2 style={{ fontSize: 17, margin: '5px 0 0', color: '#214b37' }}>What actually ran</h2></div>
            <input value={traceFilter} onChange={e => setTraceFilter(e.target.value)} placeholder="Filter by request / trace ID" style={{ border: '1px solid #dce7e0', borderRadius: 8, padding: '8px 10px', minWidth: 220, fontSize: 11, outlineColor: '#26805a' }} />
          </div>
          <div style={{ color: '#87948c', fontSize: 10, marginBottom: 10 }}>Backend event buffer: latest 160 · RAG service: latest 100 · newest first. A shared trace ID connects hops for the same request.</div>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 780, fontSize: 11 }}>
              <thead><tr style={{ color: '#829087', textAlign: 'left', borderBottom: '1px solid #e8eee9' }}>
                {['Time', 'Trace ID', 'Service', 'Stage', 'Status', 'Duration', 'Safe diagnostics'].map(v => <th key={v} style={{ padding: '9px 8px', fontSize: 9, letterSpacing: .7, textTransform: 'uppercase' }}>{v}</th>)}
              </tr></thead>
              <tbody>
                {events.map((event, index) => {
                  const c = statusColor(event.status);
                  return <tr key={`${event.timestamp}-${event.service}-${event.stage}-${index}`} style={{ borderBottom: '1px solid #f0f3f1', verticalAlign: 'top' }}>
                    <td style={{ padding: '10px 8px', color: '#65766c', whiteSpace: 'nowrap' }}>{formatTime(event.timestamp)}</td>
                    <td style={{ padding: '10px 8px', color: '#387455', fontFamily: 'monospace', whiteSpace: 'nowrap' }}>{event.traceId ? `${event.traceId.slice(0, 12)}…` : '—'}</td>
                    <td style={{ padding: '10px 8px', color: '#365b47', whiteSpace: 'nowrap' }}>{event.service}</td>
                    <td style={{ padding: '10px 8px', color: '#365b47' }}>{event.stage}</td>
                    <td style={{ padding: '10px 8px' }}><span style={{ background: c.bg, color: c.text, borderRadius: 20, padding: '3px 7px', fontSize: 9, fontWeight: 800 }}>{event.status}</span></td>
                    <td style={{ padding: '10px 8px', color: '#65766c', whiteSpace: 'nowrap' }}>{event.durationMs == null ? '—' : `${Math.round(event.durationMs)} ms`}</td>
                    <td style={{ padding: '10px 8px', color: '#7b8981', maxWidth: 360, lineHeight: 1.4, wordBreak: 'break-word' }}>{event.details && Object.keys(event.details).length ? JSON.stringify(event.details) : '—'}</td>
                  </tr>;
                })}
                {!events.length && <tr><td colSpan={7} style={{ padding: 18, color: '#839087', textAlign: 'center' }}>{loading ? 'Waiting for events…' : 'No events yet. Upload a file or send a chat question, then refresh this view.'}</td></tr>}
              </tbody>
            </table>
          </div>
        </section>

        <section style={{ ...panel, padding: 18, marginBottom: 20 }}>
          <div style={labelStyle}>Legacy / inactive services</div>
          <h2 style={{ fontSize: 17, margin: '5px 0 12px', color: '#214b37' }}>Not on the current Growth Gradual chat path</h2>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 9 }}>
            {(data?.legacyServices ?? []).map(service => <div key={service.id} style={{ border: '1px solid #edf0ee', background: '#fafbfa', borderRadius: 9, padding: 11 }}>
              <div style={{ fontWeight: 750, color: '#53665a', fontSize: 11 }}>{service.name}</div>
              <div style={{ color: '#98a39c', fontSize: 10, lineHeight: 1.5, marginTop: 5 }}>{service.reason}</div>
            </div>)}
          </div>
        </section>

        <section style={{ ...panel, padding: 15, color: '#75837a', fontSize: 10, lineHeight: 1.6 }}>
          <strong style={{ color: '#4b6455' }}>Diagnostic limitations</strong>
          <ul style={{ paddingLeft: 17, margin: '6px 0 0' }}>
            {(data?.notes ?? [
              'Only operational metadata is recorded. Prompts, document contents, credentials and provider URLs are not stored.',
              'Recent events are held in bounded in-memory buffers and disappear when a service restarts.',
              'Configured provider key counts are not proof that a live provider request will succeed.',
            ]).map((note, i) => <li key={i}>{note}</li>)}
          </ul>
        </section>
      </div>
    </main>
  );
}
