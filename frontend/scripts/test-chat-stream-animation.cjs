const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const source = fs.readFileSync(
  path.join(__dirname, '..', 'src', 'components', 'GrowthGradualChat.tsx'),
  'utf8',
);

test('SDK text deltas feed a separate progressive display target', () => {
  assert.match(source, /streamTargetTextRef\.current = streamedText/);
  assert.match(source, /streamTargetTextRef\.current = text/);
  assert.match(source, /const streamTargetTextRef = useRef\(''\)/);
});

test('active assistant bubble renders the visible prefix, not the full buffered text', () => {
  assert.match(source, /const renderedText = isVisuallyStreaming \? streamDisplayedText : msg\.text/);
  assert.match(source, /renderMd\(renderedText\)/);
  assert.match(source, /splitTextAndCharts\(renderedText\)/);
});

test('stream caret remains visible during catch-up after the network finishes', () => {
  assert.match(source, /\|\| isVisuallyStreaming \? 'msg-text--streaming'/);
  assert.match(source, /if \(isCaughtUp && !streaming\)/);
});

test('cancel/error paths clear the progressive overlay', () => {
  assert.match(source, /streamVisualMessageIdRef\.current = null/);
  assert.match(source, /Do not leave the typewriter overlay hiding an error or a cancelled reply/);
});

test('reduced-motion preference bypasses paced reveal', () => {
  assert.match(source, /prefers-reduced-motion: reduce/);
  assert.match(source, /if \(reduceMotion\) \{/);
});
