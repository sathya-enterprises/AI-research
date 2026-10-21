import fs from 'node:fs';
import crypto from 'node:crypto';
import puppeteer from '../../frontend/node_modules/puppeteer-core/lib/puppeteer/puppeteer-core.js';

const payload = JSON.parse(fs.readFileSync('artifacts/composition-qa/report-1.json', 'utf8'));
const browser = await puppeteer.connect({ browserURL: process.env.CDP_URL || 'http://127.0.0.1:55456' });
const pages = await browser.pages();
const page = pages.find(p => (p.url() || '').includes('localhost:3000')) || pages[0];
if (!page) throw new Error('No existing CDP page is available');
await page.goto('http://localhost:3000', { waitUntil: 'domcontentloaded' });
const result = await page.evaluate(async (body) => {
  const response = await fetch('/api/chat/report/pdf', { method: 'POST', headers: {'content-type': 'application/json'}, body: JSON.stringify(body) });
  const bytes = new Uint8Array(await response.arrayBuffer());
  const digest = async (value) => Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', value))).map(x => x.toString(16).padStart(2, '0')).join('');
  const backendSha = response.headers.get('x-pdf-sha256');
  const proxySha = await digest(bytes);
  const objectUrl = URL.createObjectURL(new Blob([bytes], {type: 'application/pdf'}));
  const iframe = document.createElement('iframe');
  iframe.src = objectUrl; iframe.style.display = 'none'; document.body.appendChild(iframe);
  await new Promise(resolve => setTimeout(resolve, 300));
  const viewerBytes = new Uint8Array(await (await fetch(objectUrl)).arrayBuffer());
  const viewerSha = await digest(viewerBytes);
  const anchor = document.createElement('a'); anchor.href = objectUrl; anchor.download = 'browser-verified.pdf'; anchor.click();
  return { status: response.status, byteLength: bytes.length, backendSha, proxySha, viewerSha, viewerMatchesProxy: viewerSha === proxySha, objectUrlPrefix: objectUrl.slice(0, 5) };
}, payload);
console.log(JSON.stringify(result, null, 2));
await browser.close();
