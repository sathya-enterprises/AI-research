'use strict';

/**
 * Small dependency-free helpers used by the JavaScript market-news scraper.
 * Kept in CommonJS so these exact functions can be tested with Node's built-in
 * test runner without starting Next.js or making network requests.
 */
function extractFeedItemBlocks(xml, limit = 100) {
  const source = String(xml || '');
  const safeLimit = Math.max(0, Math.min(1000, Number.isFinite(Number(limit)) ? Math.trunc(Number(limit)) : 100));
  if (safeLimit === 0) return [];
  const blocks = [];
  const expression = /<((?:[a-z_][\w.-]*:)?(?:item|entry))\b[\s\S]*?<\/\1\s*>/gi;
  for (const match of source.matchAll(expression)) {
    blocks.push(match[0]);
    if (blocks.length >= safeLimit) break;
  }
  return blocks;
}

function isSafeSameHost(candidate, current) {
  try {
    const next = new URL(candidate);
    const base = new URL(current);
    const blocked = new Set(['localhost', '127.0.0.1', '::1', '0.0.0.0']);
    return ['http:', 'https:'].includes(next.protocol)
      && next.hostname.toLowerCase() === base.hostname.toLowerCase()
      && !next.username && !next.password
      && !blocked.has(next.hostname.toLowerCase());
  } catch {
    return false;
  }
}

function extractNextFeedUrl(xml, currentUrl) {
  const source = String(xml || '');
  const links = [...source.matchAll(/<(?:[a-z_][\w.-]*:)?link\b([^>]*)\/?\s*>/gi)];
  for (const match of links) {
    const attrs = match[1] || '';
    const rel = attrs.match(/\brel=["']([^"']+)["']/i)?.[1] || '';
    const href = attrs.match(/\bhref=["']([^"']+)["']/i)?.[1] || '';
    if (!/\bnext\b/i.test(rel) || !href) continue;
    try {
      const absolute = new URL(href, currentUrl);
      if (!isSafeSameHost(absolute.toString(), currentUrl)) continue;
      absolute.hash = '';
      return absolute.toString();
    } catch {
      // Ignore malformed pagination links and continue checking other links.
    }
  }
  return null;
}

function googleNewsDateWindowUrls(url, maxWindows = 2, windowDays = 7, now = new Date()) {
  const count = Math.max(0, Math.min(6, Number.isFinite(Number(maxWindows)) ? Math.trunc(Number(maxWindows)) : 2));
  const days = Math.max(1, Math.min(30, Number.isFinite(Number(windowDays)) ? Math.trunc(Number(windowDays)) : 7));
  if (!count) return [];
  try {
    const parsed = new URL(url);
    if (parsed.hostname.toLowerCase() !== 'news.google.com' || !parsed.pathname.startsWith('/rss/search')) return [];
    const query = parsed.searchParams.get('q');
    if (!query) return [];
    const base = query.replace(/\s+(?:after|before):\d{4}-\d{2}-\d{2}/gi, '').trim();
    if (!base) return [];
    const today = new Date(now);
    if (Number.isNaN(today.getTime())) return [];
    const dayIso = offsetDays => {
      const date = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate() - offsetDays));
      return date.toISOString().slice(0, 10);
    };
    const pages = [];
    for (let i = 0; i < count; i++) {
      const after = dayIso((i + 1) * days);
      const before = dayIso(i * days);
      const page = new URL(parsed.toString());
      page.searchParams.set('q', `${base} after:${after} before:${before}`);
      pages.push(page.toString());
    }
    return pages;
  } catch {
    return [];
  }
}

module.exports = { extractFeedItemBlocks, extractNextFeedUrl, googleNewsDateWindowUrls };
