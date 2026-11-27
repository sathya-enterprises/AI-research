'use client';

import { useState, useRef, useEffect, useCallback } from 'react';
import { useChat } from '@ai-sdk/react';
import type { UIMessage } from 'ai';
import Image from 'next/image';
import { supabase } from '@/lib/supabaseClient';
import { loadPdfJs } from '@/lib/pdfjs-browser';

// ─── Types ────────────────────────────────────────────────────────────────────
import { REPORT_TIER_OPTIONS, REPORT_STYLE_OPTIONS } from './chat/types';
import type { Source, ChartSpec, ReportTier, ReportStyle, ReportRevision, ReportData, Message, Conversation, StreamMeta } from './chat/types';
import { isReportReferenceQuestion, buildReportChatContext, reportStageLabel } from './chat/reportContext';
import { isReportEditRequest, reportSectionTitles, uid, fmtTime, fmtDate, growthGradualAiTransport, getOrCreateSessionId, loadConversations, saveConversations } from './chat/storage';
import { renderMd, splitTextAndCharts } from './chat/markdown';
import { ChartBlock } from './chat/charts';
import { ReportPanel } from './chat/ReportPanel';
import { type AttachedFile, type AttachmentImagePayload, type PastedText, attachmentMimeType, isWordDocumentFile, isImageAttachmentFile, MAX_ATTACH, MAX_EXTRACTED_TEXT_CHARS, wordCount, hashText, processFile, buildAttachmentContext } from './chat/attachments';
import { SUGGESTIONS, SugIcon } from './chat/suggestions';
import { CHAT_CSS } from './chat/chatStyles';

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
  const [selectedReportTier, setSelectedReportTier] = useState<ReportTier>('detailed');
  const [selectedReportStyle, setSelectedReportStyle] = useState<ReportStyle>('market_currents');
  // generateReport reads the style through a ref so its many call sites stay unchanged.
  const selectedReportStyleRef = useRef<ReportStyle>('market_currents');
  selectedReportStyleRef.current = selectedReportStyle;
  // "Use only attached files": the report's evidence is limited to the attachments
  // (no web research). The depth (In-Detail / Deep Research) is chosen separately.
  const [attachmentOnly, setAttachmentOnly] = useState(true);
  const attachmentOnlyRef = useRef(true);
  attachmentOnlyRef.current = attachmentOnly;
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
  const hasCurrentAttachments = attachedFiles.some(file => file.status === 'attached' || file.status === 'attaching') || pastedTexts.length > 0;
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
      if (saved === 'detailed' || saved === 'in_depth') {
        setSelectedReportTier(saved);
      } else if (saved === 'brief') {
        // The Brief tier was removed; migrate the saved picker to In-Detail.
        setSelectedReportTier('detailed');
        window.localStorage.setItem('gg-report-tier-v2', 'detailed');
      }
      const savedScope = window.localStorage.getItem('gg-attachment-only-v1');
      if (savedScope === 'true' || savedScope === 'false') setAttachmentOnly(savedScope === 'true');
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

  useEffect(() => {
    if (!reportTierHydratedRef.current) return;
    try {
      window.localStorage.setItem('gg-attachment-only-v1', String(attachmentOnly));
    } catch { /* ignore storage failures */ }
  }, [attachmentOnly]);

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

    // One trace ties browser extraction metrics to the subsequent RAG indexing request.
    const attachmentTraceId = crypto.randomUUID();

    // Process each file (extract text) and update chip as it completes.
    // Only file type, byte count, extracted character count and timings leave the browser;
    // original content and filenames are never sent to the diagnostics event buffer.
    const results = await Promise.all(toProcess.map(async (file, idx) => {
      const placeholder = placeholders[idx];
      const extractionStarted = performance.now();
      try {
        const result = await processFile(file);
        void fetch('/api/diagnostics/client-event', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-Request-ID': attachmentTraceId },
          body: JSON.stringify({
            stage: 'browser_file_extraction', status: result?.status === 'failed' ? 'failed' : (result?.extractedText ? 'ok' : 'partial'),
            file_type: (file.name.split('.').pop() || file.type || 'unknown').slice(0, 24),
            input_bytes: file.size, extracted_chars: result?.extractedText?.length ?? 0,
            duration_ms: Math.round(performance.now() - extractionStarted),
          }),
        }).catch(() => {});
        setAttachedFiles(prev => prev.map(f =>
          f.id === placeholder.id
            ? (result ? { ...result, id: placeholder.id } : { ...placeholder, status: 'failed' as const, error: 'Unsupported file type' })
            : f
        ));
        return result ? { ...result, id: placeholder.id } : null;
      } catch (e) {
        void fetch('/api/diagnostics/client-event', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-Request-ID': attachmentTraceId },
          body: JSON.stringify({
            stage: 'browser_file_extraction', status: 'failed',
            file_type: (file.name.split('.').pop() || file.type || 'unknown').slice(0, 24),
            input_bytes: file.size, extracted_chars: 0,
            duration_ms: Math.round(performance.now() - extractionStarted),
          }),
        }).catch(() => {});
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
    const attachmentIds = new Set(results.filter((file): file is AttachedFile => file !== null).map(file => file.id));
    let indexedSuccessfully = false;
    try {
      const readyFiles = results.filter((file): file is AttachedFile => file !== null);
      const textFiles = readyFiles.filter(file => !!file.extractedText);
      const documents = await Promise.all(textFiles.map(async file => ({
        id: await hashText(file.extractedText!),
        name: file.name,
        text: file.extractedText!,
        source_type: 'file',
        file_type: file.type,
      })));
      const images: AttachmentImagePayload[] = [];
      const binaryFiles: { name: string; mimeType: string; data: string }[] = [];
      for (const file of readyFiles) {
        images.push(...(file.indexImages ?? []));
        if (isImageAttachmentFile(file) && file.content.includes(',')) {
          images.push({
            name: file.name,
            documentName: file.name,
            mimeType: attachmentMimeType(file) || 'image/jpeg',
            data: file.content.split(',', 2)[1],
          });
        }
        if (isWordDocumentFile(file) && file.content.includes(',')) {
          binaryFiles.push({
            name: file.name,
            mimeType: attachmentMimeType(file),
            data: file.content.split(',', 2)[1],
          });
        }
      }

      if (documents.length || images.length || binaryFiles.length) {
        const tryIndex = async (): Promise<Record<string, any> & { ok: boolean; httpStatus: number }> => {
          try {
            const response = await fetch('/api/rag/index', {
              method: 'POST',
              // signed-in uploads carry the user's token so the session is tied to their account
              headers: { 'Content-Type': 'application/json', 'X-Request-ID': attachmentTraceId, ...(await authHeader()) },
              body: JSON.stringify({ session_id: sessionId, documents, images, binaryFiles }),
            });
            const data = await response.json().catch(() => ({}));
            return { ...data, ok: response.ok, httpStatus: response.status };
          } catch (error) {
            return { ok: false, httpStatus: 0, error: (error as Error)?.message || 'Network error' };
          }
        };

        console.info('[RAG] Indexing attachment content', {
          textDocuments: documents.length, imageDocuments: images.length, binaryDocuments: binaryFiles.length,
        });
        let result = await tryIndex();
        if (!result.ok && [0, 502, 503, 504].includes(result.httpStatus)) {
          console.warn('[RAG] RAG service did not respond successfully; retrying after a short wake-up delay');
          setStatusMsg('File index is waking up, retrying…');
          await new Promise(resolve => setTimeout(resolve, 8_000));
          result = await tryIndex();
        }

        const vectorStatus = String(result.vector_persistence?.status || '');
        indexedSuccessfully = result.ok && result.index_status === 'indexed'
          && ['persisted', 'unchanged'].includes(vectorStatus);
        const ocrByName = new Map<string, string>();
        for (const extracted of Array.isArray(result.extracted_documents) ? result.extracted_documents : []) {
          if (extracted?.name && extracted?.text) ocrByName.set(String(extracted.name).toLowerCase(), String(extracted.text));
        }
        const errorByName = new Map<string, string>();
        for (const extractionError of Array.isArray(result.extraction_errors) ? result.extraction_errors : []) {
          if (extractionError?.name && extractionError?.error) errorByName.set(String(extractionError.name).toLowerCase(), String(extractionError.error));
        }
        const indexedNames = new Set<string>();
        for (const indexedDocument of Array.isArray(result.indexed_documents) ? result.indexed_documents : []) {
          if (indexedDocument?.name && ['indexed', 'already_indexed'].includes(String(indexedDocument.status))) {
            indexedNames.add(String(indexedDocument.name).toLowerCase());
          }
        }
        const updatedResults = readyFiles.map(file => {
          const ocrText = ocrByName.get(file.name.toLowerCase());
          const textParts = [file.extractedText, ocrText].filter((text, index, parts) => !!text && parts.indexOf(text) === index);
          const extractionError = errorByName.get(file.name.toLowerCase());
          const fileIndexed = indexedNames.size > 0
            ? indexedNames.has(file.name.toLowerCase())
            : indexedSuccessfully && !extractionError;
          return {
            ...file,
            extractedText: textParts.join('\n\n'),
            indexStatus: fileIndexed ? 'indexed' as const : 'failed' as const,
            indexError: extractionError || (!indexedSuccessfully
              ? String(result.error || 'The attachment was not confirmed in the vector database.')
              : undefined),
          };
        });
        setAttachedFiles(previous => previous.map(file => {
          const updated = updatedResults.find(item => item.id === file.id);
          return updated ? updated : file;
        }));

        if (indexedSuccessfully) {
          setRagIndexed(true);
          console.info('[RAG] Attachment indexing confirmed', {
            chunksAdded: result.chunks_added ?? 0,
            totalChunks: result.total_chunks ?? 0,
            vectorStore: result.vector_persistence?.store || result.vector_store || 'supabase_pgvector',
            extractionWarnings: errorByName.size,
          });
          notifyFileReady(
            'Files indexed',
            errorByName.size
              ? 'Readable file data was indexed; one or more attachments need extraction attention.'
              : 'Readable file data is stored and ready to search.',
          );
        } else {
          const detail = String(result.error || 'The RAG service did not confirm vector database persistence.');
          console.error('[RAG] Attachment indexing was not confirmed', {
            httpStatus: result.httpStatus,
            indexStatus: result.index_status,
            vectorStatus,
            extractionWarnings: errorByName.size,
          });
          setStatusMsg('The file is attached, but indexing did not complete.');
          setAttachedFiles(previous => previous.map(file => attachmentIds.has(file.id)
            ? { ...file, indexStatus: 'failed' as const, indexError: errorByName.get(file.name.toLowerCase()) || detail }
            : file));
          notifyFileReady('Indexing needs attention', 'The file is attached, but its text was not confirmed in the search database.');
        }
      } else {
        const detail = 'No readable text or image content was available to index.';
        console.warn('[RAG] No extracted text or OCR-capable content was available');
        setAttachedFiles(previous => previous.map(file => attachmentIds.has(file.id)
          ? { ...file, indexStatus: 'failed' as const, indexError: detail }
          : file));
      }
    } catch (error) {
      console.warn('[RAG] Direct index failed:', error);
      setAttachedFiles(previous => previous.map(file => attachmentIds.has(file.id)
        ? { ...file, indexStatus: 'failed' as const, indexError: 'Indexing request failed. The attachment is still available for report generation.' }
        : file));
    } finally {
      setRagIndexing(false);
      setStatusMsg('');
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
    setSelectedReportTier('detailed');
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
          headers: { 'Content-Type': 'application/json', ...(await authHeader()) },
          body: JSON.stringify({
            report: reportData.report,
            reportId: reportData.reportId || reportMsgId,
            revision: reportData.revision || 1,
            reportTier: reportData.reportTier || 'detailed',
            reportStyle: reportData.reportStyle,
            styleVariant: reportData.styleVariant,
            // the same inputs as the PDF export, so "page 30" in an edit means page 30 of the PDF the user sees
            keyStats: reportData.keyStats || [],
            summary: reportData.summary || '',
            theme: reportData.theme ?? null,
            kicker: reportData.kicker ?? '',
            publicationOutcome: reportData.publicationOutcome ?? undefined,
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
          publicationOutcome: reportData.publicationOutcome,
          failedStage: reportData.failedStage,
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
          // The revised text has its own outcome; never keep the previous version's draft/published flag.
          publicationOutcome: finalData.publicationOutcome ?? (finalData.publicationStatus === 'FAIL' ? 'draft' : undefined),
          failedStage: finalData.failedStage ?? undefined,
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
      publicationOutcome: current.publicationOutcome,
      failedStage: current.failedStage,
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
    // Whether or not the Report toggle is on: an edit of the report in this conversation must never start a
    // full new report run ("make the outlook section shorter" used to rerun the whole report).
    if (lastReportMsg?.reportData && isReportEditRequest(q, reportSectionTitles(lastReportMsg.reportData.report))) {
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
      // In-Detail and typing a prompt containing the words "deep dive").
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
    const reportFileIds = new Set((lastReportMsg?.reportFiles || []).map(file => file.id));
    const hasNewAttachment = pastedTexts.length > 0
      || attachedFiles.some(file => file.status === 'attached' && !reportFileIds.has(file.id));
    const reportContext = hasNewAttachment || !isReportReferenceQuestion(q, messages)
      ? undefined
      : buildReportChatContext(lastReportMsg?.reportData, lastReportMsg?.reportQuestion || '', q);

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

    // Build image payloads for vision-capable main chat, including image-only
    // pages extracted from PDFs so chart pages are visible to the vision reader.
    const chatFileImages: { name:string; mimeType:string; data:string }[] = [];
    for (const f of attachedFiles) {
      if (f.status === 'attached' && f.type.startsWith('image/') && f.content) {
        const base64 = f.content.split(',')[1];
        if (base64) chatFileImages.push({ name: f.name, mimeType: f.type, data: base64 });
      }
      if (f.status === 'attached' && f.indexImages?.length) {
        for (const image of f.indexImages) {
          if (chatFileImages.length >= 8) break;
          chatFileImages.push({ name: image.name, mimeType: image.mimeType, data: image.data });
        }
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
          fileContext: reportContext ? '' : fileCtx,
          sessionId: getOrCreateSessionId(),
          hasRag: ragIndexed,
          fileImages: reportContext ? [] : chatFileImages,
          reportContext,
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
    // Normalize persisted/legacy tier values to the backend enum; the removed
    // `brief` tier (e.g. from older saved conversations) falls back to detailed.
    // All report tiers must use the durable start + SSE route; the synchronous
    // route can take minutes and prevents progress from reaching the browser.
    const normalizedTier = String(reportTier || 'detailed').trim().toLowerCase();
    const hasAttachedData = files.some(file => file.status === 'attached');
    const effectiveReportTier: ReportTier = ['detailed', 'in_depth'].includes(normalizedTier)
      ? normalizedTier as ReportTier
      : 'detailed';
    const useOnlyAttachments = hasAttachedData && attachmentOnlyRef.current;
    console.info('[report] using background streaming pipeline', {
      reportTier: effectiveReportTier, attachmentOnly: useOnlyAttachments,
    });
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
      reportPhase: 'starting', reportStartedAt: Date.now(),
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
          // Keep the complete browser extraction budget for each attachment;
          // this is the report's primary source and the same text goes to RAG.
          const textCap = MAX_EXTRACTED_TEXT_CHARS;
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
    // including extracted text even when the original prompt payload is capped.
    const sourceDocuments = files
      .filter(f => f.status === 'attached')
      .map(f => ({ name: f.name, text: f.extractedText || '', file_type: f.type }));

    buildFilePayload().then(async ({ fileImages, embeddedFileImages, fileTextContext }) => {
      // Every report tier uses the durable background-job + SSE progress path,
      // so the browser never waits on one long blocking POST or appears frozen.
      const useBackgroundReportJob = ['detailed', 'in_depth'].includes(effectiveReportTier);
      if (useBackgroundReportJob) {
        console.info('[report] POST /api/chat/report/start', { reportTier: effectiveReportTier });
        const startPayload = {
          question, reportTier: effectiveReportTier, reportStyle: effectiveReportStyle, sources: priorSources, fileContext: fileTextContext, clientRequestId,
          conversationContext: conversationContext || '', fileImages, embeddedFileImages,
          sourceDocuments, sessionId: getOrCreateSessionId(), hasRag: ragIndexed,
          attachmentOnly: useOnlyAttachments,
        };
        const started = await fetch('/api/chat/report/start', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', ...(await authHeader()) },
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
        const maxWaitMs = effectiveReportTier === 'in_depth' ? 60 * 60_000 : 30 * 60_000;
        setStatusMsg(useOnlyAttachments
          ? `${effectiveReportTier === 'in_depth' ? 'Deep Research' : 'In-Detail'} report from your attached files only is running…`
          : effectiveReportTier === 'in_depth'
          ? 'Deep Research is running — collecting evidence and building the comprehensive report…'
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
                  const progressPhase = progressData.phase ? String(progressData.phase) : undefined;
                  setMessages(prev => prev.map(m => m.id === botMsgId ? { ...m, reportProgress: progressMessage, reportProgressPercent: progressPercent, reportPhase: progressPhase ?? m.reportPhase } : m));
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
                  headers: { 'Content-Type': 'application/json', ...(await authHeader()) },
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
                const progressPhase = statusData.phase ? String(statusData.phase) : undefined;
                setMessages(prev => prev.map(m => m.id === botMsgId ? { ...m, reportProgress: progressMessage, reportProgressPercent: progressPercent, reportPhase: progressPhase ?? m.reportPhase } : m));
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
            styleVariant: typeof data.styleVariant === 'number' ? data.styleVariant : undefined,
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
            // PDF is the only report output format.
            recommendedFormat: 'pdf',
            // Carried through unchanged to /report/pdf on
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
<style>{CHAT_CSS}</style>


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
              <p style={{ fontSize:16, fontWeight:700, color:'var(--gg-ink)', margin:0 }}>Drop files to attach</p>
              <p style={{ fontSize:12, color:'var(--gg-muted)', margin:'4px 0 0' }}>PDF · DOCX · TXT · Images</p>
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
            {/* The brand is in the site header; the sidebar is the user's research list. */}
            <div style={{ fontFamily:'var(--gg-font-ui)', fontSize:14, fontWeight:600, color:'var(--gg-ink)' }}>
              Your research
            </div>
            {/* Mobile close button */}
            {isMobile && (
              <button
                onClick={() => setSidebarOpen(false)}
                style={{
                  marginLeft:'auto', borderRadius:8,
                  border:'1px solid var(--gg-line)', background:'var(--gg-surface)',
                  color:'var(--gg-muted)', cursor:'pointer', flexShrink:0,
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
              <div style={{ padding:'12px 14px', fontSize:13, color:'var(--gg-muted)', fontFamily:'var(--gg-font-ui)', lineHeight:1.55 }}>
                Your questions and reports will be kept here.
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
            <div className="topbar-info" style={{ minWidth:0 }}>
              <div className="topbar-name" style={{ overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>
                {conversations.find(c => c.id === activeId)?.title || 'New research'}
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
                {/* Headline */}
                <div className="welcome-intro">
                  <h1 className="welcome-title">What do you want to research?</h1>
                  <p className="welcome-sub">Answers from live market data and sources you can check — every number linked to where it came from.</p>
                </div>
                {/* Quick suggestion chips */}
                <div className="sugs">
                  {SUGGESTIONS.map(s => (
                    <button key={s.label} type="button" className="sug" onClick={() => send(s.label)}>
                      <span className="sug-icon"><SugIcon name={s.icon} /></span>
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
                        <Image src="/growth-gradual-mark.png" alt="" width={28} height={28} style={{ objectFit:'contain', width:28, height:28 }}/>
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
                          <Image src="/growth-gradual-mark.png" alt="" width={28} height={28} style={{ objectFit:'contain', width:28, height:28 }}/>
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
                                fontSize: 11.5, color: 'var(--gg-ink)', fontFamily: 'DM Sans,sans-serif',
                                textDecoration: 'none', padding: '5px 10px', borderRadius: 8,
                                border: '1px solid var(--gg-line)', background: 'var(--gg-surface)',
                              }}
                            >
                              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                              Download source data — {msg.kbCompany.ticker} fundamentals (.xlsx)
                            </a>
                          </div>
                        )}
                        {/* Retrieved/web sources for normal chat: visible links rather than console-only metadata. */}
                        {!isUser && !streaming && !msg.reportData && (msg.sources?.length ?? 0) > 0 && (
                          <details className="chat-source-links" style={{ marginTop: 8, maxWidth: '100%', fontSize: 11.5, color: 'var(--gg-muted)' }}>
                            <summary style={{ cursor: 'pointer', fontWeight: 650, color: 'var(--gg-forest)', padding: '3px 0' }}>
                              Sources ({msg.sources!.length})
                            </summary>
                            <ol style={{ margin: '5px 0 0', paddingLeft: 19, display: 'grid', gap: 5 }}>
                              {msg.sources!.filter(source => /^https?:\/\//i.test(String(source.url || ''))).slice(0, 10).map((source, sourceIndex) => (
                                <li key={`${source.url}-${sourceIndex}`}>
                                  <a href={source.url} target="_blank" rel="noopener noreferrer" style={{ color: 'var(--gg-ink-2)', textDecoration: 'underline', textUnderlineOffset: 2 }}>
                                    {source.title || source.url}
                                  </a>
                                  {source.snippet ? <span style={{ display: 'block', color: 'var(--gg-muted)', marginTop: 2 }}>{source.snippet.slice(0, 220)}</span> : null}
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
                    background:'var(--gg-ink)', color:'#fff', borderRadius:20,
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
                    title={f.indexError
                      ? `${f.name}: ${f.indexError}`
                      : f.indexStatus === 'indexed'
                        ? `${f.name}: indexed in the vector database`
                        : f.status === 'failed' ? (f.error || 'Failed to read file') : `${f.name}: attached; indexing pending`}
                    style={{
                      borderColor: f.status === 'failed' ? '#fca5a5' : f.indexError ? '#fbbf24' : f.status === 'attaching' ? '#c4b5fd' : undefined,
                      background:  f.status === 'failed' ? '#fef2f2' : f.indexError ? '#fffbeb' : f.status === 'attaching' ? '#f5f3ff' : undefined,
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
                          : f.indexError
                            ? `⚠ ${f.name.length > 14 ? f.name.slice(0,12)+'…' : f.name}`
                          : f.indexStatus === 'indexed'
                            ? `✓ ${f.name.length > 14 ? f.name.slice(0,12)+'…' : f.name}`
                            : `· ${f.name.length > 16 ? f.name.slice(0,14)+'…' : f.name}`
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
                  title="Choose report depth, scope and style"
                >
                  <span className="report-tier-trigger-dot" aria-hidden="true" />
                  <span className="report-tier-trigger-text">
                    <span className="report-tier-trigger-kicker">Report</span>
                    <span className="report-tier-trigger-value">
                      {REPORT_TIER_OPTIONS.find(o => o.value === selectedReportTier)?.label ?? 'In-Detail'}
                      {hasCurrentAttachments && attachmentOnly && (
                        <><span className="report-tier-trigger-sep" aria-hidden="true"> · </span>Attached files only</>
                      )}
                      <span className="report-tier-trigger-sep" aria-hidden="true"> · </span>
                      {REPORT_STYLE_OPTIONS.find(o => o.value === selectedReportStyle)?.label ?? 'Market Currents'}
                    </span>
                  </span>
                  <svg className="report-tier-trigger-chevron" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>
                </button>

                {reportTierMenuOpen && (
                  <div className="report-tier-menu" role="dialog" aria-label="Report settings">
                    {hasCurrentAttachments && (
                      <>
                        <div className="report-tier-menu-head"><span>Sources</span></div>
                        <label className="report-context-toggle report-scope-toggle" title="When on, the report uses only your attached files; when off, it adds web research">
                          <input
                            type="checkbox"
                            checked={attachmentOnly}
                            onChange={e => setAttachmentOnly(e.target.checked)}
                          />
                          <span className="report-context-track"><span className="report-context-thumb"/></span>
                          <span className="report-context-label">Use only attached files</span>
                        </label>
                        <p className="report-tier-menu-note">{attachmentOnly
                          ? 'Every figure comes from your attached files. No web research is added.'
                          : 'Your attached files are combined with web research.'}</p>
                      </>
                    )}
                    <>
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
                    </>
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
