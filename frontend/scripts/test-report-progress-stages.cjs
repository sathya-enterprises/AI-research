const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const component = fs.readFileSync(path.join(__dirname, '..', 'src', 'components', 'ReportProgress.tsx'), 'utf8');
const { readChatSource } = require('./read-chat-source.cjs');
const chat = readChatSource();
const backendReport = path.join(__dirname, '..', '..', 'backend', 'routes', 'report.py');

test('every progress phase the backend reports maps to a timeline stage', { skip: !fs.existsSync(backendReport) }, () => {
  const source = fs.readFileSync(backendReport, 'utf8');
  const phases = new Set([...source.matchAll(/_report_progress\(\s*"([a-z_]+)"/g)].map(m => m[1]));
  assert.ok(phases.size >= 10, `expected the backend's progress phases, found ${phases.size}`);
  const mapped = new Set([...component.matchAll(/'([a-z_]+)'/g)].map(m => m[1]));
  const unmapped = [...phases].filter(p => p !== 'failed' && !mapped.has(p));
  assert.deepEqual(unmapped, [], `backend phases missing from ReportProgress STAGES: ${unmapped.join(', ')}`);
});

test('the progress shown is the backend\'s own (phase passed through from stream and status polling)', () => {
  assert.match(chat, /reportPhase: progressPhase \?\? m\.reportPhase/);
  assert.equal((chat.match(/const progressPhase = /g) || []).length, 2, 'stream and status polling both pass the phase');
  assert.match(chat, /<ReportProgress[\s\S]*?phase=\{msg\.reportPhase\}[\s\S]*?message=\{msg\.reportProgress\}/);
});
