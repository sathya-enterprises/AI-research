#!/usr/bin/env node
'use strict';

/**
 * Capture a real Growth Gradual export from the EXISTING attached browser tab
 * without opening Chrome's native Save As dialog.
 *
 * The real UI button is clicked and its same-origin API request runs normally.
 * Only the final `HTMLAnchorElement.click()` that would initiate a browser file
 * save is intercepted; the generated Blob is read and its exact bytes are
 * written to a temporary QA directory. Authentication storage is never read,
 * printed, cleared, or changed.
 *
 * Usage from frontend/:
 *   node scripts/capture-report-export.cjs --cdp http://127.0.0.1:63593 --format pdf
 *   node scripts/capture-report-export.cjs --cdp http://127.0.0.1:63593 --format docx --report-index 1
 *   node scripts/capture-report-export.cjs --cdp http://127.0.0.1:63593 --format html --out .test-exports
 */
const fs = require('node:fs');
const path = require('node:path');
const WebSocket = require('ws');

function parseArgs(argv) {
  const args = {
    cdp: process.env.GG_CDP_URL || 'http://127.0.0.1:9222',
    format: 'pdf', reportIndex: 0, out: '.test-exports', timeoutMs: 180_000,
  };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === '--cdp') args.cdp = argv[++i];
    else if (arg === '--format') args.format = String(argv[++i] || '').toLowerCase();
    else if (arg === '--report-index') args.reportIndex = Number.parseInt(argv[++i], 10);
    else if (arg === '--out') args.out = argv[++i];
    else if (arg === '--timeout-ms') args.timeoutMs = Number.parseInt(argv[++i], 10);
    else if (arg === '--help' || arg === '-h') args.help = true;
    else throw new Error(`Unknown argument: ${arg}`);
  }
  if (!['pdf', 'docx', 'html'].includes(args.format)) throw new Error('--format must be pdf, docx, or html');
  if (!Number.isInteger(args.reportIndex) || args.reportIndex < 0) throw new Error('--report-index must be a non-negative integer');
  if (!Number.isFinite(args.timeoutMs) || args.timeoutMs < 5_000 || args.timeoutMs > 240_000) throw new Error('--timeout-ms must be between 5000 and 240000');
  return args;
}

function safeFilename(name, format) {
  const fallback = `growth-gradual-report.${format}`;
  const cleaned = path.basename(String(name || fallback)).replace(/[<>:"/\\|?*\x00-\x1F]/g, '_');
  return cleaned || fallback;
}

function validateBytes(format, bytes) {
  if (!bytes || bytes.length < 8) throw new Error('Export produced an empty or implausibly small file.');
  if (format === 'pdf' && bytes.subarray(0, 5).toString('ascii') !== '%PDF-') throw new Error('The generated artifact is not a PDF (missing %PDF signature).');
  if (format === 'docx' && !(bytes[0] === 0x50 && bytes[1] === 0x4b)) throw new Error('The generated artifact is not a DOCX/OOXML ZIP (missing PK signature).');
  if (format === 'html') {
    const start = bytes.subarray(0, 500).toString('utf8').toLowerCase();
    if (!start.includes('<html') && !start.includes('<!doctype html')) throw new Error('The generated artifact does not appear to be an HTML document.');
  }
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  if (options.help) {
    process.stdout.write('Usage: node scripts/capture-report-export.cjs --cdp <URL> --format <pdf|docx|html> [--report-index N] [--out .test-exports] [--timeout-ms 180000]\n');
    return;
  }

  const projectRoot = path.resolve(__dirname, '..');
  const outputDir = path.resolve(projectRoot, options.out);
  const relativeOutput = path.relative(projectRoot, outputDir);
  if (!relativeOutput || relativeOutput.startsWith('..') || path.isAbsolute(relativeOutput)) {
    throw new Error('--out must be a subdirectory of the frontend project, such as .test-exports');
  }
  if (!['.test-exports', 'test-exports'].includes(relativeOutput.split(path.sep)[0])) {
    throw new Error('For safety, output must be under frontend/.test-exports/ or frontend/test-exports/.');
  }
  fs.mkdirSync(outputDir, { recursive: true });

  let socket;
  let nextId = 0;
  const pending = new Map();
  const call = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++nextId;
    const timer = setTimeout(() => {
      pending.delete(id);
      reject(new Error(`Browser command timed out: ${method}`));
    }, Math.min(options.timeoutMs + 10_000, 250_000));
    pending.set(id, { resolve, reject, timer, method });
    socket.send(JSON.stringify({ id, method, params }));
  });

  try {
    const cdpBase = new URL(options.cdp);
    const listUrl = new URL('/json/list', cdpBase).toString();
    const targetResponse = await fetch(listUrl, { signal: AbortSignal.timeout(5_000) });
    if (!targetResponse.ok) throw new Error(`Could not inspect browser targets (HTTP ${targetResponse.status}).`);
    const targets = await targetResponse.json();
    const target = targets.find(item => {
      try {
        const url = new URL(item.url || '');
        return url.origin === 'http://127.0.0.1:3000' && url.pathname === '/' && Boolean(item.webSocketDebuggerUrl);
      } catch { return false; }
    });
    if (!target) throw new Error('Could not find the existing Growth Gradual tab at http://127.0.0.1:3000/. Keep the current browser session open and supply its actual CDP URL.');

    socket = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('Timed out connecting to the existing browser tab.')), 5_000);
      socket.once('open', () => { clearTimeout(timer); resolve(); });
      socket.once('error', error => { clearTimeout(timer); reject(error); });
    });
    socket.on('message', raw => {
      let message;
      try { message = JSON.parse(raw.toString()); } catch { return; }
      if (!message.id || !pending.has(message.id)) return;
      const waiter = pending.get(message.id);
      pending.delete(message.id);
      clearTimeout(waiter.timer);
      if (message.error) waiter.reject(new Error(`${waiter.method}: ${message.error.message || 'browser command failed'}`));
      else waiter.resolve(message.result || {});
    });

    const extension = options.format;
    const expression = `
      (async () => {
        const format = ${JSON.stringify(extension)};
        const reportIndex = ${options.reportIndex};
        const timeoutMs = ${options.timeoutMs};
        const labels = { pdf: 'Download PDF', docx: 'Download Editable Word', html: 'Download HTML' };
        const reports = Array.from(document.querySelectorAll('.report-wrap'));
        if (!reports.length) throw new Error('No rendered report card found in the current conversation.');
        if (reportIndex >= reports.length) throw new Error('Report index is out of range (' + reports.length + ' reports visible).');
        const report = reports[reportIndex];
        const button = Array.from(report.querySelectorAll('button')).find(el => (el.innerText || '').trim() === labels[format]);
        if (!button) throw new Error('Button "' + labels[format] + '" was not found in the selected report card.');
        if (button.disabled) throw new Error('Export button is disabled; wait for the report to finish loading.');

        const originalClick = HTMLAnchorElement.prototype.click;
        let captured = null;
        HTMLAnchorElement.prototype.click = function (...args) {
          const href = String(this.href || '');
          const filename = String(this.download || '');
          if (this instanceof HTMLAnchorElement && this.hasAttribute('download') && href.startsWith('blob:') && filename.toLowerCase().endsWith('.' + format)) {
            captured = { href, filename };
            return undefined;
          }
          return originalClick.apply(this, args);
        };

        try {
          // This is the real UI button. Its same-origin export request executes;
          // only the final native browser-save click is intercepted.
          button.click();
          const started = Date.now();
          while (!captured && Date.now() - started < timeoutMs) {
            await new Promise(resolve => setTimeout(resolve, 200));
          }
          if (!captured) {
            const visibleError = report.innerText.match(/(?:PDF|Word document|HTML) generation failed[^\\n]*|Could not download[^\\n]*|generated (?:PDF|Word document|HTML) was empty[^\\n]*/i);
            throw new Error(visibleError ? visibleError[0] : 'No export Blob appeared before the bounded timeout. Inspect the UI error and Network response; no second click was made.');
          }
          const response = await fetch(captured.href);
          if (!response.ok) throw new Error('Could not read the generated export Blob (HTTP ' + response.status + ').');
          const blob = await response.blob();
          if (!blob.size) throw new Error('The export Blob was empty.');
          const bytes = new Uint8Array(await blob.arrayBuffer());
          let binary = '';
          const chunkSize = 0x8000;
          for (let i = 0; i < bytes.length; i += chunkSize) {
            binary += String.fromCharCode(...bytes.subarray(i, Math.min(i + chunkSize, bytes.length)));
          }
          return { filename: captured.filename, mimeType: blob.type, size: blob.size, base64: btoa(binary), reportTitle: (report.querySelector('h1,h2,h3')?.innerText || '').trim().slice(0, 120), visibleReportCount: reports.length };
        } finally {
          HTMLAnchorElement.prototype.click = originalClick;
        }
      })()`;

    const evaluated = await call('Runtime.evaluate', {
      expression, awaitPromise: true, returnByValue: true, userGesture: true,
    });
    if (evaluated.exceptionDetails) {
      const detail = evaluated.exceptionDetails.exception?.description || evaluated.exceptionDetails.text || 'Export capture failed in the browser.';
      throw new Error(detail.slice(0, 600));
    }
    const captured = evaluated.result?.value;
    if (!captured?.base64) throw new Error('The browser returned no export bytes.');

    const bytes = Buffer.from(captured.base64, 'base64');
    if (bytes.length !== captured.size) throw new Error(`Byte-size mismatch: expected ${captured.size}, decoded ${bytes.length}.`);
    validateBytes(options.format, bytes);

    const downloadFilename = safeFilename(captured.filename, options.format);
    let filename = downloadFilename;
    let outputPath = path.join(outputDir, filename);
    if (fs.existsSync(outputPath)) {
      const ext = path.extname(downloadFilename);
      const stem = path.basename(downloadFilename, ext);
      filename = `${stem}-qa-${new Date().toISOString().replace(/[:.]/g, '-')}${ext}`;
      outputPath = path.join(outputDir, filename);
    }
    fs.writeFileSync(outputPath, bytes, { flag: 'wx' });
    process.stdout.write(JSON.stringify({
      status: 'PASS', format: options.format, filename, downloadFilename, sizeBytes: bytes.length,
      mimeType: captured.mimeType || null, output: path.relative(projectRoot, outputPath),
      reportIndex: options.reportIndex, reportTitle: captured.reportTitle,
      visibleReportCount: captured.visibleReportCount, saveAsDialogAvoided: true,
      originalBrowserSessionPreserved: true,
    }, null, 2) + '\n');
  } finally {
    for (const waiter of pending.values()) clearTimeout(waiter.timer);
    pending.clear();
    if (socket && socket.readyState === WebSocket.OPEN) socket.close();
  }
}

main().catch(error => {
  const message = error instanceof Error ? error.message : 'Unknown export capture failure';
  process.stderr.write(JSON.stringify({ status: 'FAIL', error: message }) + '\n');
  process.exitCode = 1;
});
