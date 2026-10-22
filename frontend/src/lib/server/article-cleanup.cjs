'use strict';
/**
 * Pure helpers that clean article-reader output (dates, titles, related-link
 * lines, duplicate images). Kept dependency-free CommonJS so the Next.js
 * route can require() it and `node --test` can test it directly.
 */

const MONTHS = {
  jan: 0, january: 0, feb: 1, february: 1, mar: 2, march: 2, apr: 3, april: 3, may: 4,
  jun: 5, june: 5, jul: 6, july: 6, aug: 7, august: 7, sep: 8, sept: 8, september: 8,
  oct: 9, october: 9, nov: 10, november: 10, dec: 11, december: 11,
};

// Offsets (minutes) for zone abbreviations that Date() does not understand.
const ZONES = { ist: 330, gmt: 0, utc: 0, bst: 60, cet: 60, cest: 120, est: -300, edt: -240, pst: -480, pdt: -420, sgt: 480, hkt: 480, jst: 540 };

/**
 * Parse the date strings publishers actually emit. Returns a valid Date or null.
 * Handles ISO 8601, epoch seconds/ms, and forms such as
 *   "Oct 21, 2026, 04:18:00 PM IST", "Updated: 21 Oct 2026, 4:18 pm IST",
 *   "October 21, 2026 16:18 IST", "21-10-2026 16:18".
 * `new Date("... IST")` yields an Invalid Date (rendered as "Invalid Date")
 * rather than throwing, which is what the old try/catch missed.
 */
function parseArticleDate(raw) {
  if (raw === null || raw === undefined) return null;
  if (typeof raw === 'number' && Number.isFinite(raw)) {
    const d = new Date(raw < 1e12 ? raw * 1000 : raw);
    return Number.isNaN(d.getTime()) ? null : d;
  }
  let text = String(raw).replace(/\s+/g, ' ').trim();
  if (!text) return null;
  if (/^\d{10}(\d{3})?$/.test(text)) return parseArticleDate(Number(text));
  text = text.replace(/^(updated|published|posted|last updated|first published)\s*(on|at)?\s*:?\s*/i, '').trim();

  if (/^\d{4}-\d{2}-\d{2}/.test(text)) {
    const iso = new Date(text);
    if (!Number.isNaN(iso.getTime())) return iso;
  }

  let offsetMinutes = null;
  const zone = text.match(/\b([A-Z]{2,4})\b\s*$/i);
  if (zone && ZONES[zone[1].toLowerCase()] !== undefined) {
    offsetMinutes = ZONES[zone[1].toLowerCase()];
    text = text.slice(0, zone.index).trim();
  }
  const numericOffset = text.match(/([+-])(\d{2}):?(\d{2})\s*$/);
  if (numericOffset) {
    offsetMinutes = (numericOffset[1] === '-' ? -1 : 1) * (Number(numericOffset[2]) * 60 + Number(numericOffset[3]));
    text = text.slice(0, numericOffset.index).trim();
  }
  text = text.replace(/,/g, ' ').replace(/\s+/g, ' ').trim();

  let y; let mo; let d;
  let m = text.match(/^([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?\s+(\d{4})(.*)$/);           // Oct 21 2026
  if (m && MONTHS[m[1].toLowerCase()] !== undefined) { mo = MONTHS[m[1].toLowerCase()]; d = +m[2]; y = +m[3]; }
  if (y === undefined) {
    m = text.match(/^(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\.?\s+(\d{4})(.*)$/);               // 21 Oct 2026
    if (m && MONTHS[m[2].toLowerCase()] !== undefined) { d = +m[1]; mo = MONTHS[m[2].toLowerCase()]; y = +m[3]; }
  }
  if (y === undefined) {
    m = text.match(/^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})(.*)$/);                                      // 21-10-2026 (Indian D-M-Y)
    if (m) { d = +m[1]; mo = +m[2] - 1; y = +m[3]; }
  }
  if (y === undefined) {
    const loose = new Date(text);
    if (Number.isNaN(loose.getTime())) return null;
    if (offsetMinutes === null) return loose;
    // Re-interpret the wall-clock time in the stated zone.
    return new Date(Date.UTC(loose.getFullYear(), loose.getMonth(), loose.getDate(), loose.getHours(), loose.getMinutes(), loose.getSeconds()) - offsetMinutes * 60000);
  }
  const rest = (m && m[4]) || '';
  let hh = 0; let mm = 0; let ss = 0;
  const t = rest.match(/(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(am|pm)?/i);
  if (t) {
    hh = +t[1]; mm = +t[2]; ss = t[3] ? +t[3] : 0;
    if (t[4]) {
      const pm = t[4].toLowerCase() === 'pm';
      if (pm && hh < 12) hh += 12;
      if (!pm && hh === 12) hh = 0;
    }
  }
  if (mo < 0 || mo > 11 || d < 1 || d > 31 || hh > 23 || mm > 59) return null;
  const utc = Date.UTC(y, mo, d, hh, mm, ss) - (offsetMinutes === null ? 330 : offsetMinutes) * 60000; // default IST for Indian publishers
  const out = new Date(utc);
  return Number.isNaN(out.getTime()) ? null : out;
}

/** Human date in IST, or '' when the input cannot be parsed (never "Invalid Date"). */
function formatArticleDate(raw) {
  const date = parseArticleDate(raw);
  if (!date) return '';
  return date.toLocaleString('en-IN', {
    day: 'numeric', month: 'long', year: 'numeric', hour: '2-digit', minute: '2-digit',
    timeZone: 'Asia/Kolkata',
  }) + ' IST';
}

/** ISO string for storage, or ''. */
function isoArticleDate(raw) {
  const date = parseArticleDate(raw);
  return date ? date.toISOString() : '';
}

const PUBLISHER_SUFFIX_RE = /\s+(?:[-|–—:]|·)\s+((?:the\s+)?(?:economic times|et\s?\w*|economictimes|business standard|mint|livemint|moneycontrol|financial express|the financial express|hindu ?businessline|businessline|the hindu|ndtv ?profit|ndtv|cnbc ?tv18|cnbctv18\.com|zee business|reuters|bloomberg|investing\.com(?: india)?|investing\.com|times of india|the times of india|hindustan times|india today|business today|forbes india|value research|trendlyne|tickertape|groww|upstox|angel one|5paisa|news18|firstpost|the print|theprint|deccan herald|indian express|the indian express|outlook business|fortune india|yahoo finance|marketwatch|cnbc|ft\.com|financial times)(?:\.com|\.in)?)\s*$/i;

const SECTION_SUFFIX_RE = /\s+\|\s+(?:[A-Z][\w&'.-]*\s+){0,4}(?:news|updates?|today|live|markets?|business|economy|money)\s*$/i;

/**
 * Remove a trailing " - The Economic Times" / " | Mint" style publisher suffix.
 * `siteNames` (og:site_name, feed source name, host) are matched too.
 */
function stripPublisherSuffix(title, siteNames) {
  let out = String(title || '').replace(/\s+/g, ' ').trim();
  if (!out) return out;
  for (let pass = 0; pass < 2; pass += 1) {
    const before = out;
    out = out.replace(PUBLISHER_SUFFIX_RE, '').trim();
    // Section labels after a pipe: "| Company Business News", "| Stock Market News",
    // "| Economy News", "| Mint Markets" (pipes only — a dash may be part of the headline).
    out = out.replace(SECTION_SUFFIX_RE, '').trim();
    for (const name of siteNames || []) {
      const n = String(name || '').trim();
      if (n.length < 3) continue;
      const escaped = n.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      out = out.replace(new RegExp(`\\s+(?:[-|–—:]|\\u00b7)\\s+${escaped}\\s*$`, 'i'), '').trim();
    }
    if (out === before) break;
  }
  return out.length >= 8 ? out : String(title || '').trim();
}

// Short navigation lines publishers interleave with the story body.
const RELATED_LINE_RE = /^(?:also read|read also|read more|recommended|related|must read|check out|click here|watch|listen|trending|top stories|more from|you may also like|catch all the|download|subscribe)\b|\b(?:share price|stock price(?: today)?|top gainers and losers|gainers and losers|live updates?|live blog|price today|latest news|stock quote|q[1-4] results live)\s*$/i;

/** True for a short line that is a related-article/stock-quote link, not prose. */
function isRelatedLinkLine(text) {
  const t = String(text || '').replace(/\s+/g, ' ').trim();
  if (!t) return true;
  if (t.length > 110) return false;
  if (/[.!?…"”]$/.test(t) && t.split(' ').length > 8) return false;
  return RELATED_LINE_RE.test(t);
}

/** True when anchor text covers most of a short block (a bare link / link list). */
function isMostlyLinkText(text, linkText) {
  const total = String(text || '').replace(/\s+/g, ' ').trim().length;
  const links = String(linkText || '').replace(/\s+/g, ' ').trim().length;
  if (!total || total > 160) return false;
  return links / total >= 0.85;
}

/**
 * Identity of an image regardless of size/crop variant, so the same photo
 * served at two sizes (ET "msid-123,width-1200" vs "msid-123,width-600",
 * "photo-800x450.jpg" vs "photo-1200x675.jpg", ?w=640 vs ?w=1200) counts once.
 */
function imageIdentityKey(url) {
  const raw = String(url || '').trim();
  if (!raw) return '';
  let u;
  try { u = new URL(raw); } catch { return raw.toLowerCase(); }
  const host = u.hostname.replace(/^www\./, '').replace(/^(?:img|static|images?|cdn|media)\d*\./, '');
  let path = decodeURIComponent(u.pathname).toLowerCase();
  const msid = path.match(/msid-(\d+)/);
  if (msid) return `${host}#msid-${msid[1]}`;
  path = path
    .replace(/\/(?:thumb|thumbs|resize|resized|crop|w_\d+[^/]*|c_[^/]+|tr:[^/]+|fit-in|\d+x\d+|filters:[^/]+)(?=\/)/g, '')
    .replace(/[-_](?:\d{2,4}x\d{2,4}|w\d{2,4}|h\d{2,4}|small|medium|large|thumb|thumbnail|original|scaled)(?=\.[a-z0-9]+$)/g, '')
    .replace(/,(?:width|height|resizemode|quality|imgsize)-[^,/]*/g, '')
    .replace(/\.(?:jpe?g|png|webp|avif)$/, '');
  return `${host}${path}`;
}

/** Keep the first occurrence of each distinct image (order preserved). */
function dedupeImages(urls) {
  const seen = new Set();
  const out = [];
  for (const url of urls || []) {
    const key = imageIdentityKey(url);
    if (!key || seen.has(key)) continue;
    seen.add(key);
    out.push(url);
  }
  return out;
}

/** Drop related-link lines and exact/near-exact repeated paragraphs. */
function cleanParagraphs(paragraphs) {
  const out = [];
  const seen = new Set();
  for (const para of paragraphs || []) {
    const text = String(para || '').trim();
    if (!text) continue;
    const body = text.startsWith('§ ') ? text.slice(2) : text;
    if (!text.startsWith('§ ') && isRelatedLinkLine(body)) continue;
    const key = body.toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
    if (!key || seen.has(key)) continue;
    seen.add(key);
    out.push(text);
  }
  return out;
}

module.exports = {
  parseArticleDate,
  formatArticleDate,
  isoArticleDate,
  stripPublisherSuffix,
  isRelatedLinkLine,
  isMostlyLinkText,
  imageIdentityKey,
  dedupeImages,
  cleanParagraphs,
};
