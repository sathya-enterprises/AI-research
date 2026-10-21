/**
 * Canonical backend URL handling for all Next.js proxy routes.
 * `BACKEND_URL` is a host/base URL, not a route: callers append `/api/...`.
 * A terminal `/api` is removed to avoid accidental `/api/api/...` paths.
 */
const DEFAULT_BACKEND_URL = 'http://127.0.0.1:10000';

export function getBackendBaseUrl(): string {
  let raw = (process.env.BACKEND_URL || DEFAULT_BACKEND_URL).trim();
  // An empty port such as BACKEND_URL=http://127.0.0.1: may be normalized to port 80.
  // Prefer an explicit BACKEND_PORT, then the local FastAPI default. PORT often belongs to Next.js.
  const emptyPort = raw.match(/^(https?:\/\/(?:\[[^\]]+\]|[^/:?#]+)):\s*(\/.*)?$/i);
  if (emptyPort) {
    const separatePort = Number(process.env.BACKEND_PORT || 10000);
    if (Number.isInteger(separatePort) && separatePort > 0 && separatePort <= 65535) {
      raw = `${emptyPort[1]}:${separatePort}${emptyPort[2] || ''}`;
    }
  }
  let parsed: URL;
  try {
    parsed = new URL(raw);
  } catch {
    throw new Error('Invalid BACKEND_URL. Set it to a backend origin such as http://127.0.0.1:10000.');
  }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
    throw new Error('Invalid BACKEND_URL protocol. Only HTTP and HTTPS are supported.');
  }
  parsed.search = '';
  parsed.hash = '';
  let pathname = parsed.pathname.replace(/\/+$/, '');
  if (/\/api$/i.test(pathname)) pathname = pathname.slice(0, -4);
  parsed.pathname = pathname;
  return parsed.toString().replace(/\/$/, '');
}

export function getBackendDiagnosticTarget(): string {
  try {
    const url = new URL(getBackendBaseUrl());
    // Do not expose credentials, query parameters, or fragments in diagnostics.
    return `${url.origin}${url.pathname}`;
  } catch {
    return 'invalid BACKEND_URL';
  }
}
