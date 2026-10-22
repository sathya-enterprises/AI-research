/**
 * Lightweight structured logger for Growth Gradual Next.js API routes.
 * All output goes to stdout/stderr — visible in `npm run dev` terminal.
 *
 * Format:  HH:MM:SS [GG-Frontend] LEVEL  [scope] — message
 */

type Level = 'INFO' | 'WARN' | 'ERROR' | 'DEBUG';

function timestamp(): string {
  return new Date().toTimeString().slice(0, 8); // HH:MM:SS
}

function stringify(a: unknown): string {
  if (a instanceof Error) return `${a.name}: ${a.message}`;
  if (typeof a === 'object' && a !== null) {
    try { return JSON.stringify(a); } catch { return String(a); }
  }
  return String(a);
}

/**
 * printf-style placeholders (%s %d %i %f %.Nf %o %j %%), matching how the
 * routes already call the logger. Previously the placeholders were printed
 * literally and the values appended ("✓ %s: %d visible … Moneycontrol 12").
 */
export function formatLogMessage(msg: string, args: unknown[]): string {
  let used = 0;
  const out = String(msg).replace(/%(%|s|d|i|f|o|O|j|\.(\d+)f)/g, (token, kind: string, digits?: string) => {
    if (kind === '%') return '%';
    if (used >= args.length) return token;
    const value = args[used++];
    if (kind === 'd' || kind === 'i') {
      const n = Number(value);
      return Number.isFinite(n) ? String(Math.trunc(n)) : stringify(value);
    }
    if (kind === 'f') return String(Number(value));
    if (digits !== undefined) {
      const n = Number(value);
      return Number.isFinite(n) ? n.toFixed(Number(digits)) : stringify(value);
    }
    return stringify(value);
  });
  const rest = args.slice(used);
  return rest.length ? `${out} ${rest.map(stringify).join(' ')}` : out;
}

function write(level: Level, scope: string, msg: string, ...args: unknown[]) {
  const prefix = `${timestamp()} [GG-Frontend] ${level.padEnd(5)}  [${scope}]`;
  const formatted = args.length ? formatLogMessage(msg, args) : msg;
  const line = `${prefix} — ${formatted}`;

  if (level === 'ERROR') {
    console.error(line);
  } else if (level === 'WARN') {
    console.warn(line);
  } else {
    console.log(line);
  }
}

export function createLogger(scope: string) {
  return {
    info:  (msg: string, ...args: unknown[]) => write('INFO',  scope, msg, ...args),
    warn:  (msg: string, ...args: unknown[]) => write('WARN',  scope, msg, ...args),
    error: (msg: string, ...args: unknown[]) => write('ERROR', scope, msg, ...args),
    debug: (msg: string, ...args: unknown[]) => {
      if (process.env.LOG_LEVEL === 'debug') write('DEBUG', scope, msg, ...args);
    },
  };
}

/** Log an incoming API request and return a helper that logs the response. */
export function logRequest(
  logger: ReturnType<typeof createLogger>,
  method: string,
  path: string,
) {
  const t0 = performance.now();
  logger.info(`→ ${method} ${path}`);
  return function logResponse(status: number, note?: string) {
    const ms = (performance.now() - t0).toFixed(0);
    const suffix = note ? `  ${note}` : '';
    logger.info(`← ${method} ${path}  ${status}  ${ms}ms${suffix}`);
  };
}
