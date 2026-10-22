'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const C = require('../src/lib/server/article-cleanup.cjs');

test('publisher dates with IST and other forms never become "Invalid Date"', () => {
  const cases = [
    'Oct 21, 2026, 04:18:00 PM IST',
    'Updated: 21 Oct 2026, 4:18 pm IST',
    'October 21, 2026 16:18 IST',
    '2026-10-21T16:18:00+05:30',
    '21-10-2026 16:18',
    '1792496880',
  ];
  for (const raw of cases) {
    const formatted = C.formatArticleDate(raw);
    assert.ok(formatted, `should parse: ${raw}`);
    assert.doesNotMatch(formatted, /invalid/i);
  }
  assert.equal(C.isoArticleDate('Oct 21, 2026, 04:18:00 PM IST'), '2026-10-21T10:48:00.000Z');
  assert.equal(C.isoArticleDate('2026-10-21T16:18:00+05:30'), '2026-10-21T10:48:00.000Z');
  assert.equal(C.formatArticleDate('not a date'), '');
  assert.equal(C.formatArticleDate(''), '');
});

test('publisher suffix is stripped from titles', () => {
  assert.equal(
    C.stripPublisherSuffix("India's infrastructure output jumps 7.9% in September on strong coal, steel growth - The Economic Times"),
    "India's infrastructure output jumps 7.9% in September on strong coal, steel growth",
  );
  assert.equal(C.stripPublisherSuffix('Sensex rallies 600 points | Mint'), 'Sensex rallies 600 points');
  assert.equal(C.stripPublisherSuffix('RBI holds repo rate - Investing.com India'), 'RBI holds repo rate');
  assert.equal(C.stripPublisherSuffix('Nifty ends higher — Acme Markets Daily', ['Acme Markets Daily']), 'Nifty ends higher');
  assert.equal(
    C.stripPublisherSuffix("Premji Invest, Norwest and Lok Capital back Vegafin's majority stake in Bizloan with up to ₹1,000 crore in commitment | Company Business News"),
    "Premji Invest, Norwest and Lok Capital back Vegafin's majority stake in Bizloan with up to ₹1,000 crore in commitment",
  );
  assert.equal(C.stripPublisherSuffix('Nifty hits record high | Stock Market News'), 'Nifty hits record high');
  assert.equal(C.stripPublisherSuffix('RBI policy: what changes for borrowers | Mint'), 'RBI policy: what changes for borrowers');
  // A pipe followed by real headline text is kept
  assert.equal(C.stripPublisherSuffix('Gold vs equity | which one wins over 10 years'), 'Gold vs equity | which one wins over 10 years');
  // A dash inside the headline is kept
  assert.equal(C.stripPublisherSuffix('Q2 results - what to expect from TCS'), 'Q2 results - what to expect from TCS');
});

test('related stock-quote and "also read" lines are dropped, prose kept', () => {
  assert.ok(C.isRelatedLinkLine('HCL Tech share price'));
  assert.ok(C.isRelatedLinkLine('Sensex top gainers and losers'));
  assert.ok(C.isRelatedLinkLine('Bajaj Finance share price'));
  assert.ok(C.isRelatedLinkLine('Also Read: RBI keeps repo rate unchanged'));
  assert.ok(!C.isRelatedLinkLine("India's infrastructure output jumped 7.9% in September on a year-on-year basis, government data showed."));
  assert.ok(!C.isRelatedLinkLine('Shares of Infosys rose 3.3% after the company raised its revenue guidance and announced a buyback of shares at a premium to the share price'));
  const cleaned = C.cleanParagraphs(['Lede paragraph about markets today and why.', 'HCL Tech share price', '§ Heading', 'Lede paragraph about markets today and why.', 'Second paragraph.']);
  assert.deepEqual(cleaned, ['Lede paragraph about markets today and why.', '§ Heading', 'Second paragraph.']);
});

test('link-dominated short blocks are detected', () => {
  assert.ok(C.isMostlyLinkText('HCL Tech share price', 'HCL Tech share price'));
  assert.ok(!C.isMostlyLinkText('Infosys rose 3% after results, said analysts at HDFC Securities.', 'HDFC Securities'));
});

test('same photo at different sizes is de-duplicated, distinct photos kept', () => {
  const imgs = [
    'https://img.etimg.com/thumb/msid-135091232,width-1200,height-900,imgsize-123,resizemode-75/photo.jpg',
    'https://img.etimg.com/thumb/msid-135091232,width-600,height-450/photo.jpg',
    'https://img.etimg.com/thumb/msid-135090001,width-600,height-450/other.jpg',
    'https://images.livemint.com/img/2026/10/21/1600x900/markets_1729500000000.jpg',
    'https://images.livemint.com/img/2026/10/21/600x338/markets_1729500000000.jpg',
    'https://cdn.example.com/news/photo-800x450.jpg',
    'https://cdn.example.com/news/photo-1200x675.jpg',
    'https://cdn.example.com/news/photo.jpg?w=640',
  ];
  const out = C.dedupeImages(imgs);
  assert.deepEqual(out, [imgs[0], imgs[2], imgs[3], imgs[5]]);
});
