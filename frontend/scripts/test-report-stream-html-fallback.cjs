const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');
const chat = fs.readFileSync(path.join(root, 'src/components/GrowthGradualChat.tsx'), 'utf8');
const streamRoute = fs.readFileSync(path.join(root, 'src/app/api/chat/report/stream/[jobId]/route.ts'), 'utf8');
const startRoute = fs.readFileSync(path.join(root, 'src/app/api/chat/report/start/route.ts'), 'utf8');
const statusRoute = fs.readFileSync(path.join(root, 'src/app/api/chat/report/status/[jobId]/route.ts'), 'utf8');
const healthRoute = fs.readFileSync(path.join(root, 'src/app/api/health/route.ts'), 'utf8');
const smoke = fs.readFileSync(path.join(__dirname, 'api-route-smoke-test.mjs'), 'utf8');

test('report client validates SSE content type before parsing events', () => {
  assert.match(chat, /streamContentType\.includes\('text\/event-stream'\)/);
  assert.match(chat, /invalid progress-stream response; switching to status polling/);
});

test('failed stream response is never copied verbatim into the assistant error', () => {
  assert.match(chat, /responsePrefix: responseText\.slice\(0, 120\)/);
  assert.doesNotMatch(chat, /data:\s*\{\s*error:\s*errBody\s*\}/);
  assert.match(chat, /throw new Error\(safeStreamError\)/);
});

test('invalid or HTML status response fails quickly with a safe actionable error', () => {
  assert.match(chat, /statusContentType\.includes\('application\/json'\)/);
  assert.match(chat, /website page instead of API responses/);
});

test('server stream proxy refuses unexpected non-SSE content types', () => {
  assert.match(streamRoute, /upstreamContentType\.includes\('text\/event-stream'\)/);
  assert.match(streamRoute, /expectedContentType:\s*'text\/event-stream'/);
  assert.match(streamRoute, /NextResponse\.json\(/);
});

test('API smoke check detects when the report stream route is missing from the deployed build', () => {
  assert.match(smoke, /report stream route registration/);
  assert.match(smoke, /stream\/invalid/);
});


test('report start proxy does not wrap upstream HTML into assistant-visible JSON errors', () => {
  assert.match(startRoute, /contentType\.includes\('application\/json'\)/);
  assert.match(startRoute, /returned a web page instead of JSON/);
  assert.match(startRoute, /expectedContentType:\s*'application\/json'/);
  assert.doesNotMatch(startRoute, /payload\s*=\s*\{\s*error:\s*text/);
});

test('report status proxy also rejects non-JSON upstream pages', () => {
  assert.match(statusRoute, /contentType\.includes\('application\/json'\)/);
  assert.match(statusRoute, /returned a web page instead of JSON/);
  assert.doesNotMatch(statusRoute, /payload\s*=\s*\{\s*error:\s*text/);
});

test('stream errors with non-JSON upstream bodies never proxy the body verbatim', () => {
  assert.match(streamRoute, /if \(contentType\.includes\('application\/json'\)\)/);
  assert.match(streamRoute, /configured report stream endpoint returned a web page/);
  assert.doesNotMatch(streamRoute, /payload\s*=\s*\{\s*error:\s*text/);
});

test('frontend start response checks JSON content type before parsing', () => {
  assert.match(chat, /startContentType\.includes\('application\/json'\)/);
  assert.match(chat, /The report start API returned the website page instead of JSON/);
});

test('deployment diagnostics verify start/status/stream routes on frontend and backend', () => {
  assert.match(smoke, /report start proxy returns JSON/);
  assert.match(smoke, /report status proxy returns JSON/);
  assert.match(smoke, /api\/chat\/report\/stream\/\{job_id\}/);
  assert.match(healthRoute, /v39-report-stream-api-guard-20261019/);
});
