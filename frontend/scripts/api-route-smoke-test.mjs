const frontend = (process.env.FRONTEND_URL || 'http://127.0.0.1:3000').replace(/\/$/, '');
const backend = (process.env.BACKEND_URL || 'http://127.0.0.1:10000').replace(/\/$/, '').replace(/\/api$/i, '');
const checks = [];

async function check(name, url, method, evaluate, options = {}) {
  try {
    const res = await fetch(url, { ...options, method, signal: AbortSignal.timeout(8000) });
    const text = await res.text();
    let payload = null;
    try { payload = JSON.parse(text); } catch {}
    const contentType = String(res.headers.get('content-type') || '').toLowerCase();
    const result = evaluate(res.status, payload, text, contentType);
    checks.push({ name, status: res.status, pass: result.pass, message: result.message });
  } catch (error) {
    checks.push({ name, status: 'ERR', pass: false, message: error instanceof Error ? error.message : String(error) });
  }
}

await check('Backend /health', `${backend}/health`, 'GET', (status) => ({
  pass: status === 200,
  message: status === 200 ? 'FastAPI backend responds.' : 'Expected HTTP 200 from the backend.'
}));

await check('Backend report route registered', `${backend}/openapi.json`, 'GET', (status, payload) => {
  const paths = payload?.paths || {};
  const required = [
    ['/api/chat/report', 'post'],
    ['/api/chat/report/start', 'post'],
    ['/api/chat/report/stream/{job_id}', 'get'],
    ['/api/chat/report/status/{job_id}', 'get'],
  ];
  const missing = required.filter(([path, method]) => !paths[path]?.[method]).map(([path, method]) => `${method.toUpperCase()} ${path}`);
  return { pass: status === 200 && missing.length === 0, message: missing.length === 0 ? 'Synchronous and background report routes are registered in FastAPI.' : `Backend OpenAPI is missing report job route(s): ${missing.join(', ')}. The running backend is stale or BACKEND_URL points at the wrong service.` };
});

await check('Active frontend identity / backend reachability', `${frontend}/api/health`, 'GET', (status, payload) => {
  const currentBuild = status === 200 && payload?.ok === true && payload?.sourcePackage === 'frontend'
    && payload?.build === 'v39-report-stream-api-guard-20261019';
  return {
    pass: currentBuild && payload?.backendReachable === true,
    message: status !== 200 ? 'Frontend /api/health is not registered: wrong frontend directory or stale Next.js route build.'
      : payload?.sourcePackage !== 'frontend' ? `Wrong frontend copy is serving requests: ${payload?.sourcePackage || 'unknown'}. Use the top-level frontend folder.`
      : payload?.build !== 'v39-report-stream-api-guard-20261019' ? `Frontend is active but has stale build marker ${payload?.build || 'unknown'}. Deploy the latest frontend before testing report streams.`
      : payload?.backendReachable ? `Correct frontend build is active and backend health returned ${payload.backendStatus}.`
      : `Correct frontend is active, but backend is unreachable at ${payload?.backendTarget}: ${payload?.backendError || 'health check failed'}.`
  };
});

await check('Frontend /api/market route', `${frontend}/api/market?healthcheck=1`, 'GET', (status, payload) => ({
  pass: status === 200 && payload?.route === '/api/market',
  message: status === 200 ? 'Market route is registered.' : 'Market API returned 404 or an unexpected response; the active Next.js route tree is stale or wrong.'
}));

await check('Frontend /api/scrape route', `${frontend}/api/scrape`, 'OPTIONS', (status) => ({
  pass: status !== 404,
  message: status === 404 ? 'Scrape route is not registered in the active Next.js app.' : `Scrape route responded with HTTP ${status} (expected a non-404 route response).`
}));

await check('Frontend DOCX export route registration (GET should be 405)', `${frontend}/api/chat/report/docx`, 'GET', (status) => ({
  pass: status === 405,
  message: status === 405 ? 'Editable Word export proxy is registered and accepts POST.' : status === 404 ? 'DOCX export route is not registered in the active frontend.' : `Unexpected DOCX route status ${status}.`
}));

await check('Frontend report route registration (GET should be 405)', `${frontend}/api/chat/report`, 'GET', (status, payload) => ({
  pass: status === 405,
  message: status === 405 ? 'Report proxy route is registered and accepts POST.' : status === 404 ? 'Report route is not registered by the active frontend.' : `Unexpected GET status ${status}; inspect the route response ${payload?.error || ''}`.trim()
}));

await check('Frontend report stream route registration (invalid ID should be 400 JSON)', `${frontend}/api/chat/report/stream/invalid`, 'GET', (status, payload) => ({
  pass: status === 400 && payload?.error === 'Invalid report job ID.',
  message: status === 400 && payload?.error === 'Invalid report job ID.'
    ? 'Report streaming route is registered and its request guard is active.'
    : status === 404 ? 'Report streaming route is missing from the active frontend build. Deploy the latest frontend before generating reports.'
      : `Unexpected stream-route response: HTTP ${status}, content=${String(payload?.error || '').slice(0, 100)}`
}));

await check('Frontend report start proxy returns JSON (invalid tier is safe)', `${frontend}/api/chat/report/start`, 'POST', (status, payload, _text, contentType) => ({
  pass: status === 400 && contentType.includes('application/json') && /invalid report tier/i.test(String(payload?.error || '')),
  message: status === 400 && contentType.includes('application/json')
    ? 'Report start route is registered and returns structured JSON errors.'
    : `Report start route mismatch: HTTP ${status}, content-type=${contentType || 'missing'}. Do not submit a real job from this smoke test.`
}), { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ reportTier: 'invalid' }) });

await check('Frontend report status proxy returns JSON (unknown job is safe)', `${frontend}/api/chat/report/status/00000000000000000000000000000000`, 'GET', (status, payload, _text, contentType) => ({
  pass: status === 404 && contentType.includes('application/json') && Boolean(payload?.error),
  message: status === 404 && contentType.includes('application/json')
    ? 'Report status route is registered and backend not-found errors remain JSON.'
    : `Report status route mismatch: HTTP ${status}, content-type=${contentType || 'missing'}.`
}));

console.log('\nGrowth Gradual local API route smoke test\n');
for (const item of checks) console.log(`${item.pass ? 'PASS' : 'FAIL'}  ${item.name} [${item.status}] — ${item.message}`);
const failures = checks.filter(item => !item.pass).length;
console.log(`\n${checks.length - failures}/${checks.length} checks passed.`);
if (failures) process.exitCode = 1;
