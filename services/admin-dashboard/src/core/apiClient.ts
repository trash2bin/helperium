// apiClient.ts — fetch wrapper with auth, error parsing, and logging

const w = window as any;

function getToken(): string | null {
  return localStorage.getItem('admin_token');
}

// Global log (before Alpine boots)
if (!w.__apiLog) w.__apiLog = [];
const globalLog = w.__apiLog as any[];
const MAX_LOG = 50;
let logId = 0;

function logEntry(
  method: string, path: string, status: number,
  resBody: unknown, reqBody: string | null, durationMs: number,
): void {
  const entry = {
    id: ++logId, method, path, status, reqBody,
    resBody: typeof resBody === 'string' ? resBody : JSON.stringify(resBody, null, 2),
    durationMs, ts: new Date().toISOString(),
  };
  globalLog.push(entry);
  if (globalLog.length > MAX_LOG) globalLog.shift();

  // Push to Alpine store if loaded
  if (typeof Alpine !== 'undefined') {
    try {
      const logger = w.Alpine.store('apiLogger');
      if (logger?._push) logger._push(entry);
    } catch { /* Alpine not yet ready */ }
  }
}

async function request(path: string, options?: RequestInit): Promise<unknown> {
  const opts = options ?? {};
  const method = (opts.method || 'GET').toUpperCase();
  const reqBody = opts.body ? String(opts.body) : null;
  const start = performance.now();

  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  const token = getToken();
  if (token) headers['Authorization'] = 'Bearer ' + token;

  let res: Response;
  try {
    res = await fetch(path, {
      ...opts,
      headers: { ...headers, ...((opts.headers as Record<string, string>) ?? {}) },
    });
  } catch (netErr: unknown) {
    const dur = Math.round(performance.now() - start);
    const msg = netErr instanceof Error ? netErr.message : String(netErr);
    logEntry(method, path, 0, msg, reqBody, dur);
    throw new Error('Network error: ' + msg);
  }

  if (res.status === 401) {
    const dur = Math.round(performance.now() - start);
    logEntry(method, path, 401, 'Unauthorized', reqBody, dur);
    throw new Error('Unauthorized');
  }

  const contentType = res.headers.get('content-type') || '';
  let body: unknown;

  if (contentType.includes('application/json')) {
    try { body = await res.json(); } catch {
      const text = await res.text();
      body = text ? { error: text } : {};
    }
  } else {
    const text = await res.text();
    body = text ? { error: text } : {};
  }

  const dur = Math.round(performance.now() - start);

  if (!res.ok) {
    const b = body as Record<string, unknown>;
    let msg: string = String(b.message || b.error || res.statusText);
    if (Array.isArray(b.detail) && b.detail[0]) {
      const d = b.detail[0] as Record<string, unknown>;
      msg = String(d.msg || msg);
      if (d.input !== undefined) msg += ' (got: ' + JSON.stringify(d.input) + ')';
    } else if (typeof b.detail === 'string') {
      msg = b.detail;
    }
    logEntry(method, path, res.status, body, reqBody, dur);
    throw new Error(msg);
  }

  logEntry(method, path, res.status, body, reqBody, dur);
  return body;
}

const client = {
  get: (path: string) => request(path),
  put: (path: string, data: unknown) => request(path, { method: 'PUT', body: JSON.stringify(data) }),
  post: (path: string, data?: unknown) => {
    const opts: RequestInit = { method: 'POST' };
    if (data !== undefined) opts.body = JSON.stringify(data);
    return request(path, opts);
  },
  del: (path: string) => request(path, { method: 'DELETE' }),
};

w.apiClient = client;

export {};
