const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const read = (p) => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');

test('shared motion tokens and page entry motion are defined', () => {
  const css = read('src/app/globals.css');
  for (const token of ['--gg-motion-press: 110ms', '--gg-motion-fast: 140ms', '--gg-motion-ui: 180ms', '--gg-motion-enter: 220ms', '--gg-motion-page: 260ms']) {
    assert.ok(css.includes(token), `missing motion token ${token}`);
  }
  assert.match(css, /\.feed-page-grid,\s*\.article-page-grid\s*\{\s*animation:\s*ggRouteEnter/);
  assert.match(css, /\.gg-account-menu\s*\{[^}]*animation:\s*ggPopoverEnter/s);
});

test('reduced motion disables route, card and ticker movement without hiding content', () => {
  const css = read('src/app/globals.css');
  assert.match(css, /@media\s*\(prefers-reduced-motion:\s*reduce\)[\s\S]*?\.ticker-track\s*\{\s*animation:\s*none\s*!important;\s*transform:\s*none\s*!important;/);
  assert.match(css, /\.feed-page-grid,\s*\.article-page-grid,\s*\.gg-account-menu,[\s\S]*?animation:\s*none\s*!important;/);
});

test('News FAB has no perpetual decorative pulse and uses restrained easing', () => {
  const fab = read('src/components/NewsFAB.tsx');
  assert.doesNotMatch(fab, /nfab-pulse|nfab-blink|nfab__ring/);
  assert.match(fab, /transition: transform 240ms cubic-bezier\(\.2,\.7,\.2,1\)/);
  assert.match(fab, /The FAB uses a static status dot/);
});

test('premium report mode and live status markers do not continuously glow or blink', () => {
  const chat = read('src/components/GrowthGradualChat.tsx');
  assert.match(chat, /\.chat-shell\.gg-premium \.report-mode-btn\.is-active\s*\{\s*animation:\s*none\s*!important;/);
  const stock = read('src/components/StockPanel.tsx');
  assert.match(stock, /animation:\s*'none'/);
  const feed = read('src/components/FeedPage.tsx');
  assert.doesNotMatch(feed, /animation:'pulse 1\.2s infinite'/);
});

test('motion audit documents all main route families and reduced-motion acceptance', () => {
  const audit = fs.readFileSync(path.join(__dirname, '..', '..', 'MOTION-AUDIT.md'), 'utf8');
  for (const route of ['Chat', 'Feed and category pages', 'Stocks/markets', 'Article reader', 'Search/dashboard', 'Navigation/account menu', 'News FAB']) {
    assert.ok(audit.includes(route), `missing animation inventory for ${route}`);
  }
  assert.match(audit, /Reduced-motion acceptance checklist/);
});

test('collapsing the desktop sidebar preserves a centered conversation reading lane', () => {
  const chat = read('src/components/GrowthGradualChat.tsx');
  assert.match(chat, /\.chat-msgs > \.msg-row\s*\{[^}]*width:\s*min\(100%, 1120px\)[^}]*margin-inline:\s*auto/s);
  assert.match(chat, /\.sidebar--closed\s*\{[^}]*flex-basis:\s*0\s*!important/s);
  assert.match(chat, /aria-expanded=\{sidebarOpen\}/);
  assert.match(chat, /aria-controls="growth-gradual-chat-sidebar"/);
});
