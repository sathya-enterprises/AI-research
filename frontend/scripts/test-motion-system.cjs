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

test('News FAB is restrained, has no misleading status dot, and follows the selected theme', () => {
  const fab = read('src/components/NewsFAB.tsx');
  const css = read('src/app/globals.css');
  assert.doesNotMatch(fab, /nfab-pulse|nfab-blink|nfab__ring|nfab__dot/);
  assert.match(fab, /width: 48px/);
  assert.match(fab, /transition: transform 240ms cubic-bezier\(\.2,\.7,\.2,1\)/);
  assert.match(fab, /html\[data-theme="dark"\] \.nfab-panel/);
  assert.match(css, /html\[data-theme="dark"\] \.nfab-panel__body \.gg-article-card/);
  assert.match(css, /html\[data-theme="dark"\] \.nfab-panel__body \.feed-meta-badge/);
});

test('premium report mode and live status markers do not continuously glow or blink', () => {
  const chat = read('src/components/GrowthGradualChat.tsx');
  assert.match(chat, /\.chat-shell\.gg-premium \.report-mode-btn\.is-active\s*\{\s*animation:\s*none\s*!important;/);
  const stock = read('src/components/StockPanel.tsx');
  assert.match(stock, /animation:\s*'none'/);
  const feed = read('src/components/FeedPage.tsx');
  assert.doesNotMatch(feed, /animation:'pulse 1\.2s infinite'/);
});

test('chat identities stay visible and selected conversations use a calm neutral state', () => {
  const chat = read('src/components/GrowthGradualChat.tsx');
  assert.match(chat, /assistant-robot-icon/);
  assert.match(chat, /user-avatar-icon/);
  assert.match(chat, /msg-avatar--bot\s*\{[\s\S]*?display:\s*inline-flex\s*!important/s);
  assert.match(chat, /conv-item--active,[\s\S]*?background:\s*#E7EAE8\s*!important/s);
  assert.doesNotMatch(chat, /\.msg-avatar--bot\s*\{\s*display:\s*none\s*!important/);
});

test('collapsing the desktop sidebar preserves a centered conversation reading lane', () => {
  const chat = read('src/components/GrowthGradualChat.tsx');
  assert.match(chat, /\.chat-msgs > \.msg-row\s*\{[^}]*width:\s*min\(100%, 1120px\)[^}]*margin-inline:\s*auto/s);
  assert.match(chat, /\.sidebar--closed\s*\{[^}]*flex-basis:\s*0\s*!important/s);
  assert.match(chat, /aria-expanded=\{sidebarOpen\}/);
  assert.match(chat, /aria-controls="growth-gradual-chat-sidebar"/);
});


test('editorial restraint pass keeps short user messages compact and removes decorative glow', () => {
  const chat = read('src/components/GrowthGradualChat.tsx');
  const refinement = chat.split('v100: editorial restraint pass')[1];
  assert.ok(refinement, 'missing final editorial restraint layer');
  assert.match(refinement, /\.msg-row--user \.msg-inner\s*\{[^}]*width:\s*fit-content\s*!important/s);
  assert.match(refinement, /\.msg-row--user \.msg-text\s*\{[^}]*background:\s*#145A43\s*!important/s);
  assert.match(refinement, /\.input-row:focus-within\s*\{[^}]*box-shadow:\s*0 0 0 2px/s);
  assert.match(refinement, /\.chat-welcome::before\s*\{\s*display:\s*none\s*!important/s);
  assert.match(refinement, /\.conv-item--active,[\s\S]*?background:\s*#E7E9E7\s*!important/s);
});


test('chat route hides the duplicate global header while preserving sidebar account and theme controls', () => {
  const navbar = read('src/components/Navbar.tsx');
  const chat = read('src/components/GrowthGradualChat.tsx');
  const globals = read('src/app/globals.css');
  assert.match(navbar, /pathname === '\/' \? 'gg-global-navbar gg-global-navbar--chat'/);
  assert.match(globals, /\.gg-global-navbar--chat\s*\{\s*display:\s*none\s*!important;/);
  assert.match(chat, /className="sidebar-appearance-toggle"/);
  assert.match(chat, /className="sidebar-user-button"/);
  assert.match(chat, /await signOut\(\)/);
});

test('light/dark preference persists and provides dark tokens for chat and shared pages', () => {
  const chat = read('src/components/GrowthGradualChat.tsx');
  const init = read('src/components/ThemeInitializer.tsx');
  const globals = read('src/app/globals.css');
  assert.match(chat, /localStorage\.setItem\('gg-theme', nextTheme\)/);
  assert.match(chat, /document\.documentElement\.dataset\.theme = nextTheme/);
  assert.match(init, /localStorage\.getItem\('gg-theme'\)/);
  assert.match(globals, /html\[data-theme="dark"\]\s*\{/);
  assert.match(chat, /html\[data-theme="dark"\] \.chat-shell\.gg-premium\.gg-full/);
});
