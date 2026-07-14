import { describe, it, expect, vi, beforeEach } from 'vitest';
import { apiCall } from './api-client.js';

/**
 * Helper: create a mock Response object.
 * Note: vitest running in node mode uses a minimal fetch polyfill,
 * so we patch globalThis.fetch directly.
 */
function mockResponse(status, body, headers = {}) {
  const defaultHeaders = { 'content-type': 'application/json', ...headers };
  // 204 No Content must have null body (Node fetch spec)
  const responseBody = status === 204 ? null : (
    typeof body === 'string' ? body : JSON.stringify(body)
  );
  return new Response(responseBody, { status, headers: defaultHeaders });
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe('apiCall', () => {
  // ── Success cases ──

  it('200 with JSON body returns parsed object', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      mockResponse(200, { agents: [{ name: 'test' }] })
    );

    const result = await apiCall('/api/agents');
    expect(result).toEqual({ agents: [{ name: 'test' }] });
  });

  it('200 with empty JSON object returns it', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      mockResponse(200, {})
    );

    const result = await apiCall('/api/health');
    expect(result).toEqual({});
  });

  // ── No Content ──

  it('204 No Content returns empty object (no json parse attempt)', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      mockResponse(204, '', { 'content-type': '' })
    );

    const result = await apiCall('/api/agents/test', { method: 'DELETE' });
    expect(result).toEqual({});
  });

  it('204 with stray content-type returns empty object', async () => {
    // Regression: some servers return content-type even on 204
    globalThis.fetch = vi.fn().mockResolvedValue(
      mockResponse(204, '', { 'content-type': 'application/json' })
    );

    // Should NOT throw JSON.parse error on empty body
    const result = await apiCall('/api/agents/x', { method: 'DELETE' });
    expect(result).toEqual({});
  });

  // ── Pydantic 422 with detail[] ──

  it('422 with Pydantic detail array gives human-readable error', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      mockResponse(422, {
        detail: [{
          type: 'string_pattern_mismatch',
          loc: ['body', 'name'],
          msg: "String should match pattern '^[a-z][a-z0-9_-]*$'",
          input: 'BadName',
          ctx: { pattern: '^[a-z][a-z0-9_-]*$' },
        }],
      })
    );

    await expect(apiCall('/api/agents', {
      method: 'POST',
      body: JSON.stringify({ name: 'BadName' }),
    })).rejects.toThrow(
      /String should match pattern.*got: "BadName"/
    );
  });

  it('422 with Pydantic detail without input still works', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      mockResponse(422, {
        detail: [{
          type: 'missing',
          loc: ['body', 'name'],
          msg: 'Field required',
        }],
      })
    );

    await expect(apiCall('/api/agents', { method: 'POST' })).rejects.toThrow(
      'Field required'
    );
  });

  // ── 422 with string detail ──

  it('422 with string detail extracts the message', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      mockResponse(422, {
        detail: 'Agent name already exists',
      })
    );

    await expect(apiCall('/api/agents', {
      method: 'POST',
      body: JSON.stringify({ name: 'duplicate' }),
    })).rejects.toThrow('Agent name already exists');
  });

  // ── 404 etc with error/message fields ──

  it('404 with error field shows that error', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      mockResponse(404, { error: 'Agent not found' })
    );

    await expect(apiCall('/api/agents/missing')).rejects.toThrow('Agent not found');
  });

  it('500 with JSON body and wrong content-type parses as error', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      status: 500,
      ok: false,
      headers: new Map([['content-type', 'text/plain']]),
      json: () => Promise.reject(new Error('not json')),
      text: () => Promise.resolve('Internal Server Error'),
    });

    await expect(apiCall('/api/broken')).rejects.toThrow('Internal Server Error');
  });

  it('500 with no body returns error with status code text', async () => {
    // Node undici does not populate statusText, so we just check it throws
    globalThis.fetch = vi.fn().mockResolvedValue({
      status: 500,
      ok: false,
      headers: new Map([['content-type', 'text/plain']]),
      json: () => Promise.reject(new Error('not json')),
      text: () => Promise.resolve(''),
    });

    await expect(apiCall('/api/broken')).rejects.toThrow();
  });

  // ── 401 Unauthorized ──

  it('401 throws Unauthorized and calls onUnauthorized callback', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      mockResponse(401, { detail: 'Not authenticated' })
    );

    const onUnauthorized = vi.fn();
    await expect(apiCall('/api/agents', {}, null, onUnauthorized)).rejects.toThrow(
      'Unauthorized'
    );
    expect(onUnauthorized).toHaveBeenCalledWith('Unauthorized');
  });

  // ── Auth token ──

  it('passes Bearer token from getToken function', async () => {
    let capturedHeaders;
    globalThis.fetch = vi.fn().mockImplementation((url, opts) => {
      capturedHeaders = opts.headers;
      return Promise.resolve(mockResponse(200, { ok: true }));
    });

    await apiCall('/api/agents', {}, () => 'secret123');
    expect(capturedHeaders['Authorization']).toBe('Bearer secret123');
  });

  it('does not add Authorization when getToken returns null', async () => {
    let capturedHeaders;
    globalThis.fetch = vi.fn().mockImplementation((url, opts) => {
      capturedHeaders = opts.headers;
      return Promise.resolve(mockResponse(200, { ok: true }));
    });

    await apiCall('/api/health', {}, () => null);
    expect(capturedHeaders['Authorization']).toBeUndefined();
  });

  // ── Network errors ──

  it('network error (TypeError) becomes "Network error"', async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(
      new TypeError('fetch failed')
    );

    await expect(apiCall('/api/agents')).rejects.toThrow('Network error');
  });

  it('AbortError propagates as-is (not swallowed)', async () => {
    const abortErr = new DOMException('The user aborted a request.', 'AbortError');
    globalThis.fetch = vi.fn().mockRejectedValue(abortErr);

    await expect(apiCall('/api/agents')).rejects.toThrow('The user aborted');
  });

  // ── 409 Conflict ──

  it('409 with message field shows the message', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      mockResponse(409, { message: 'Agent "dup" already exists' })
    );

    await expect(apiCall('/api/agents', {
      method: 'POST',
      body: JSON.stringify({ name: 'dup' }),
    })).rejects.toThrow('Agent "dup" already exists');
  });
});
