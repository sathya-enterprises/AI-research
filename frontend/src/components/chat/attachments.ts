// Moved verbatim from GrowthGradualChat.tsx (split for maintainability; no behaviour change).
import { loadPdfJs } from '@/lib/pdfjs-browser';
export interface AttachedFile {
  id: string;
  name: string;
  size: number;
  type: string;
  content: string; // base64 data URL or raw text
  extractedText?: string;
  indexImages?: AttachmentImagePayload[];
  status: 'attaching' | 'attached' | 'failed';
  indexStatus?: 'indexed' | 'failed';
  error?: string;
  indexError?: string;
}
export interface AttachmentImagePayload { name: string; documentName?: string; mimeType: string; data: string; }
export interface PastedText {
  id: string;
  label: string;
  text: string;
}
export const ACCEPTED_TYPES = [
  'application/pdf',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  'application/msword',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', // .xlsx
  'application/vnd.ms-excel', // .xls
  'text/plain', 'text/csv', 'text/markdown',
  'image/jpeg', 'image/png', 'image/webp', 'image/gif',
];
export function attachmentExtension(f: { name: string }): string {
  return f.name.split('.').pop()?.toLowerCase() ?? '';
}
export function attachmentMimeType(f: { name: string; type: string }): string {
  const byExtension: Record<string, string> = {
    pdf: 'application/pdf', doc: 'application/msword',
    docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    xls: 'application/vnd.ms-excel',
    xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    csv: 'text/csv', txt: 'text/plain', md: 'text/markdown',
    jpg: 'image/jpeg', jpeg: 'image/jpeg', png: 'image/png',
    webp: 'image/webp', gif: 'image/gif',
  };
  return byExtension[attachmentExtension(f)] ?? (f.type === 'application/octet-stream' ? '' : (f.type ?? ''));
}
export function isSpreadsheetFile(f: { name: string; type: string }): boolean {
  if (/\.(csv|txt|md)$/i.test(f.name)) return false;
  return /\.(xlsx|xls)$/i.test(f.name)
    || f.type === 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    || f.type === 'application/vnd.ms-excel';
}
export function isPdfAttachmentFile(f: { name: string; type: string }): boolean {
  return attachmentMimeType(f) === 'application/pdf' || /\.pdf$/i.test(f.name);
}
export function isWordDocumentFile(f: { name: string; type: string }): boolean {
  return /\.docx?$/i.test(f.name)
    || /wordprocessingml\.document|application\/msword/i.test(attachmentMimeType(f));
}
export function isImageAttachmentFile(f: { name: string; type: string }): boolean {
  return attachmentMimeType(f).startsWith('image/') || /\.(jpe?g|png|webp|gif)$/i.test(f.name);
}
export function isTextAttachmentFile(f: { name: string; type: string }): boolean {
  return attachmentMimeType(f).startsWith('text/') || /\.(csv|txt|md)$/i.test(f.name);
}
export const MAX_ATTACH = 10;
export const MAX_EXTRACTED_TEXT_CHARS = 1_000_000;
export function wordCount(s: string) { return s.trim().split(/\s+/).filter(Boolean).length; }
export function readAsDataURL(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader(); r.onload = () => resolve(r.result as string);
    r.onerror = () => reject(new Error('Read failed')); r.readAsDataURL(file);
  });
}
export function readAsText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader(); r.onload = () => resolve(r.result as string);
    r.onerror = () => reject(new Error('Read failed')); r.readAsText(file);
  });
}

export async function extractPdfText(file: File): Promise<{ text: string; indexImages: AttachmentImagePayload[] }> {
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
    const maxPages = 500;
    const numPages = Math.min(pdf.numPages, maxPages);
    const textParts: string[] = [];
    const indexImages: AttachmentImagePayload[] = [];
    let extractedChars = 0;
    for (let p = 1; p <= numPages; p++) {
      const page    = await pdf.getPage(p);
      const content = await page.getTextContent();
      const pageText = content.items
        .map((item: unknown) => (item as { str?: string }).str ?? '')
        .join(' ');
      if (pageText.trim()) {
        const pageBlock = `[Page ${p}]\n${pageText.trim()}`;
        const remaining = MAX_EXTRACTED_TEXT_CHARS - extractedChars;
        if (remaining <= 0) break;
        textParts.push(pageBlock.slice(0, remaining));
        extractedChars += Math.min(pageBlock.length, remaining);
      } else if (indexImages.length < 8) {
        const page = await pdf.getPage(p);
        const viewport = page.getViewport({ scale: 1.2 });
        const canvas = document.createElement('canvas');
        canvas.width = Math.ceil(viewport.width);
        canvas.height = Math.ceil(viewport.height);
        const ctx = canvas.getContext('2d');
        if (ctx) {
          await page.render({ canvasContext: ctx, viewport } as any).promise;
          indexImages.push({
            name: `${file.name} page ${p}`,
            documentName: file.name,
            mimeType: 'image/jpeg',
            data: canvas.toDataURL('image/jpeg', 0.84).split(',')[1],
          });
        }
      }
    }
    if (pdf.numPages > maxPages) textParts.push(`[Extraction note: processed ${maxPages} of ${pdf.numPages} PDF pages.]`);
    if (extractedChars >= MAX_EXTRACTED_TEXT_CHARS) textParts.push('[Extraction note: PDF text was capped at 1,000,000 characters.]');
    return { text: textParts.join('\n\n'), indexImages };
  } catch (e) {
    console.warn('[processFile] pdf.js extraction failed:', e);
    return { text: '', indexImages: [] };
  }
}

/** Parses an uploaded .xlsx/.xls workbook into plain text: one block per
 *  sheet, each row rendered as CSV. This is what feeds `extractedText`,
 *  which in turn becomes the report generator's PRIMARY SOURCE file content
 *  (see buildFilePayload / fileContext on the backend) — so multi-sheet
 *  data like fund/scheme performance tables survives intact rather than
 *  being dropped because the file wasn't a PDF or plain text.
 *  Fully blank rows are skipped. Extraction limits are explicit and any cap is
 *  recorded in the indexed text so large workbooks are not silently truncated. */
export async function extractXlsxText(file: File): Promise<string> {
  try {
    const XLSX = await import('xlsx');
    const dataUrl = await readAsDataURL(file);
    const base64  = dataUrl.split(',')[1];
    const binary  = atob(base64);
    const bytes   = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    const wb = XLSX.read(bytes, { type: 'array' });

    const MAX_SHEETS = 100;
    const MAX_ROWS_PER_SHEET = 20_000;
    const MAX_TOTAL_CHARS = MAX_EXTRACTED_TEXT_CHARS;

    const parts: string[] = [];
    const truncationNotes: string[] = [];
    let totalChars = 0;
    const sheetNames = wb.SheetNames.slice(0, MAX_SHEETS);
    if (wb.SheetNames.length > MAX_SHEETS) truncationNotes.push(`Only the first ${MAX_SHEETS} of ${wb.SheetNames.length} sheets were extracted.`);
    for (const sheetName of sheetNames) {
      if (totalChars >= MAX_TOTAL_CHARS) break;
      const ws = wb.Sheets[sheetName];
      const ref = ws['!ref'];
      if (!ref) continue;
      const range = XLSX.utils.decode_range(ref);
      const rowLimit = Math.min(range.e.r, range.s.r + MAX_ROWS_PER_SHEET - 1);
      const limitedRows = XLSX.utils.sheet_to_json(ws, {
        header: 1,
        blankrows: false,
        defval: '',
        raw: false,
        range: { s: range.s, e: { r: rowLimit, c: range.e.c } },
      }) as unknown[][];
      const csv = limitedRows.map(row => row.map(value => {
        const cell = String(value ?? '');
        return /[",\r\n]/.test(cell) ? `"${cell.replace(/"/g, '""')}"` : cell;
      }).join(',')).join('\n');
      if (range.e.r > rowLimit) truncationNotes.push(`${sheetName}: extracted ${MAX_ROWS_PER_SHEET} of ${range.e.r - range.s.r + 1} rows.`);
      const rows = csv.split('\n').filter(r => r.trim().length > 0);
      if (!rows.length) continue;
      const block = `[Sheet: ${sheetName}]\n${rows.join('\n')}`;
      const remaining = MAX_TOTAL_CHARS - totalChars;
      if (block.length > remaining) {
        parts.push(block.slice(0, remaining));
        totalChars += remaining;
        truncationNotes.push(`Workbook text reached the ${MAX_TOTAL_CHARS.toLocaleString()} character extraction limit.`);
        break;
      }
      totalChars += block.length;
      parts.push(block);
    }
    const content = parts.join('\n\n');
    return truncationNotes.length
      ? `${content}\n\n[Extraction notes: ${truncationNotes.join(' ')}]`
      : content;
  } catch (e) {
    console.warn('[processFile] xlsx extraction failed:', e);
    return '';
  }
}

// Stable content hash — SHA-256 of the extracted text, so re-uploading the
// exact same file (same content) always yields the same doc ID, letting the
// backend's `sess.has_doc(doc_id)` dedup actually skip re-embedding and
// skip rewriting bundle.md, instead of always looking "new".
export async function hashText(text: string): Promise<string> {
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

export async function processFile(file: File): Promise<AttachedFile | null> {
  const accepted = ACCEPTED_TYPES.includes(file.type) || file.type.startsWith('image/') || file.type.startsWith('text/')
    || isSpreadsheetFile(file) || isPdfAttachmentFile(file) || isWordDocumentFile(file)
    || isTextAttachmentFile(file) || isImageAttachmentFile(file);
  if (!accepted) return null;
  const id = Math.random().toString(36).slice(2, 10);
  // Return immediately with 'attaching' status
  const base: AttachedFile = { id, name: file.name, size: file.size, type: attachmentMimeType(file), content: '', status: 'attaching' };
  try {
    let content = '', extractedText = '';
    let indexImages: AttachmentImagePayload[] = [];
    if (isSpreadsheetFile(file)) {
      content       = await readAsDataURL(file);
      extractedText = await extractXlsxText(file);
    } else if (isTextAttachmentFile(file)) {
      const text = await readAsText(file);
      extractedText = text.length > MAX_EXTRACTED_TEXT_CHARS
        ? `${text.slice(0, MAX_EXTRACTED_TEXT_CHARS)}\n\n[Extraction note: text was capped at 1,000,000 characters.]`
        : text;
      content = extractedText;
    } else if (isPdfAttachmentFile(file)) {
      content       = await readAsDataURL(file);
      const pdf = await extractPdfText(file);
      extractedText = pdf.text;
      indexImages = pdf.indexImages;
    } else {
      content = await readAsDataURL(file);
    }
    return { ...base, content, extractedText, indexImages, status: 'attached' };
  } catch (e) {
    return { ...base, status: 'failed', error: (e as Error).message };
  }
}
export function compactPdfTextForChat(text: string, maxChars: number): string {
  if (text.length <= maxChars) return text;
  const markers = [...text.matchAll(/\[Page\s+(\d+)\]\s*/gi)];
  if (!markers.length) return `${text.slice(0, maxChars - 45)}\n[PDF text shortened for chat context.]`;
  const prefix = text.slice(0, markers[0].index).trim();
  const pages = markers.map((marker, index) => ({
    number: marker[1],
    text: text.slice((marker.index ?? 0) + marker[0].length, markers[index + 1]?.index ?? text.length).trim(),
  }));
  const available = maxChars - Math.min(prefix.length, 500) - pages.length * 16 - 40;
  if (available < pages.length * 40) return `${text.slice(0, maxChars - 45)}\n[Later PDF pages omitted.]`;
  const perPage = Math.max(40, Math.floor(available / pages.length));
  const parts = prefix ? [prefix.slice(0, 500)] : [];
  for (const page of pages) {
    let pageText = page.text;
    if (pageText.length > perPage) {
      const marker = ' [middle omitted] ';
      const usable = Math.max(20, perPage - marker.length);
      const head = Math.floor(usable * 0.68);
      pageText = `${pageText.slice(0, head).trimEnd()}${marker}${pageText.slice(-Math.max(1, usable - head)).trimStart()}`;
    }
    parts.push(`[Page ${page.number}] ${pageText}`);
  }
  const compacted = parts.join('\n\n');
  return compacted.length <= maxChars ? compacted : `${compacted.slice(0, maxChars - 45)}\n[PDF page excerpts shortened.]`;
}
export function buildAttachmentContext(files: AttachedFile[], pasted: PastedText[]): string {
  const parts: string[] = [];
  let remainingChars = 80_000;
  files.forEach((f, i) => {
    if (f.extractedText) {
      const perFileCap = Math.min(remainingChars, isPdfAttachmentFile(f) ? remainingChars : 8_000);
      if (perFileCap <= 0) return;
      const text = isPdfAttachmentFile(f)
        ? compactPdfTextForChat(f.extractedText, perFileCap)
        : f.extractedText.slice(0, perFileCap);
      parts.push(`[Attached File ${i+1}: ${f.name}]\n${text}`);
      remainingChars -= text.length;
    } else if (f.type.startsWith('image/')) {
      parts.push(`[Attached Image ${i+1}: ${f.name}]`);
    } else {
      parts.push(`[Attached File ${i+1}: ${f.name} (${f.type})]`);
    }
  });
  pasted.forEach((p, i) => {
    if (remainingChars <= 0) return;
    const text = p.text.slice(0, Math.min(6_000, remainingChars));
    parts.push(`[Pasted Context ${i+1}: ${p.label}]\n${text}`);
    remainingChars -= text.length;
  });
  if (!parts.length) return '';
  return `\n\n---\n📎 USER-ATTACHED CONTEXT:\n${parts.join('\n\n')}\n---\n\nUse the attached content above when answering.`;
}
