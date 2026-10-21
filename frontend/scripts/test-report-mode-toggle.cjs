const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const componentPath = path.join(__dirname, '..', 'src', 'components', 'GrowthGradualChat.tsx');
const source = fs.readFileSync(componentPath, 'utf8');

test('Report toggle label and mode state are wired together', () => {
  assert.match(source, /<span>Report<\/span>/);
  assert.match(source, /className=\{`report-mode-btn \$\{reportModeEnabled \? 'is-active' : ''\}`\}/);
  assert.match(source, /aria-pressed=\{reportModeEnabled\}/);
  assert.match(source, /setReportModeEnabled\(enabled => !enabled\)/);
  assert.doesNotMatch(source, /<span>Think<\/span>/);
});

test('inactive Report toggle has no glow, shadow, or active animation', () => {
  assert.match(source, /\.report-mode-btn \{[\s\S]*?box-shadow:none;/);
  assert.match(source, /\.report-mode-btn:hover:not\(:disabled\):not\(\.is-active\) \{[^}]*box-shadow:none;/);
  assert.match(source, /\.report-mode-btn\.is-active \{[\s\S]*?box-shadow:[^;]*rgba\(59,130,246/);
  assert.match(source, /animation:report-mode-glow 2\.2s ease-in-out infinite/);
  assert.match(source, /\.report-mode-btn\.is-active \{ animation:none; \}/);
});

test('report-mode hint and accessible titles describe report generation', () => {
  assert.match(source, /Report mode is enabled\. The next query will generate a research report\./);
  assert.match(source, /Report enabled: next query generates a report/);
  assert.match(source, /Report depth is only shown when the user has enabled Report mode/);
});
