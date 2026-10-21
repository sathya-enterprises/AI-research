'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { extractFeedItemBlocks, extractNextFeedUrl, googleNewsDateWindowUrls } = require('../src/lib/server/feed-pagination.cjs');

test('extracts RSS items and Atom entries, including a namespaced Atom entry', () => {
  const xml = `<?xml version="1.0"?><rss><channel>
    <item><title>RSS article</title></item>
    <item><title>Second RSS article</title></item>
  </channel></rss>
  <feed xmlns:atom="http://www.w3.org/2005/Atom">
    <entry><title>Atom article</title></entry>
    <atom:entry><atom:title>Namespaced Atom article</atom:title></atom:entry>
  </feed>`;
  const blocks = extractFeedItemBlocks(xml, 10);
  assert.equal(blocks.length, 4);
  assert.match(blocks[0], /RSS article/);
  assert.match(blocks[2], /Atom article/);
  assert.match(blocks[3], /Namespaced Atom article/);
});

test('feed item cap is honored', () => {
  const xml = '<rss>' + Array.from({ length: 5 }, (_, i) => `<item><title>${i}</title></item>`).join('') + '</rss>';
  assert.equal(extractFeedItemBlocks(xml, 2).length, 2);
  assert.equal(extractFeedItemBlocks(xml, 0).length, 0);
});

test('follows only same-host HTTP(S) rel=next links', () => {
  const current = 'https://publisher.example/feed?page=1';
  assert.equal(extractNextFeedUrl('<feed><link rel="next" href="?page=2" /></feed>', current), 'https://publisher.example/feed?page=2');
  assert.equal(extractNextFeedUrl('<feed><link rel="next" href="https://evil.example/feed?page=2" /></feed>', current), null);
  assert.equal(extractNextFeedUrl('<feed><link rel="next" href="javascript:alert(1)" /></feed>', current), null);
  assert.equal(extractNextFeedUrl('<feed><link rel="next" href="http://localhost/admin" /></feed>', current), null);
  assert.equal(extractNextFeedUrl('<feed><link rel="alternate" href="?page=2" /></feed>', current), null);
});

test('Google News archive pagination uses disjoint date windows and leaves other hosts alone', () => {
  const fixedNow = new Date('2026-10-16T10:00:00Z');
  const url = 'https://news.google.com/rss/search?q=UPI+payments&hl=en-IN&gl=IN&ceid=IN%3Aen';
  const pages = googleNewsDateWindowUrls(url, 2, 7, fixedNow);
  assert.equal(pages.length, 2);
  assert.match(new URL(pages[0]).searchParams.get('q'), /after:2026-10-09 before:2026-10-16/);
  assert.match(new URL(pages[1]).searchParams.get('q'), /after:2026-10-02 before:2026-10-09/);
  assert.equal(new Set(pages).size, 2);
  assert.deepEqual(googleNewsDateWindowUrls('https://publisher.example/rss', 2, 7, fixedNow), []);
});
