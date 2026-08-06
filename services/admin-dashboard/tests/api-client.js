/**
 * Extracted api() function from admin-dashboard Alpine.js component.
 *
 * Differences from the original (app.js:400):
 * - No `this.__()` i18n — error messages are plain English strings
 * - No `this.error` assignment — returns errors instead
 * - Accepts explicit `getToken` function instead of reading localStorage
 * - Accepts explicit `onUnauthorized` callback for 401 handling
 *
 * Usage:
 *   import { apiCall } from './api-client.js';
 *   const data = await apiCall('/api/agents');
 */

/**
 * @param {string} url
 * @param {object} [options]
 * @param {object} [options.headers] - extra headers
 * @param {string} [options.method]
 * @param {string} [options.body]
 * @param {() => string|null} [getToken] - returns Bearer token or null
 * @param {(msg: string) => void} [onUnauthorized] - called when 401
 * @returns {Promise<object>}
 */
export async function apiCall(url, options = {}, getToken, onUnauthorized) {
  const headers = { 'Content-Type': 'application/json' };
  const token = typeof getToken === 'function' ? getToken() : null;
  if (token) {
    headers['Authorization'] = 'Bearer ' + token;
  }
  // Merge extra headers from options
  if (options.headers) {
    Object.assign(headers, options.headers);
  }

  try {
    const res = await fetch(url, {
      method: options.method || 'GET',
      body: options.body,
      headers,
    });

    if (res.status === 401) {
      const msg = 'Unauthorized';
      if (typeof onUnauthorized === 'function') {
        onUnauthorized(msg);
      }
      throw new Error(msg);
    }

    // Try to parse JSON body; fall back to text for empty/error responses
    let body;
    const contentType = res.headers.get('content-type') || '';
    if (contentType.includes('application/json')) {
      try {
        body = await res.json();
      } catch (_jsonErr) {
        // Empty body with JSON content-type (e.g. 204 No Content)
        // Fall through to text fallback
        const text = await res.text();
        body = text ? { error: text } : {};
      }
    } else {
      const text = await res.text();
      body = text ? { error: text } : {};
    }

    if (!res.ok) {
      // Extract human-readable error from Pydantic 422 / FastAPI errors
      let msg = body.message || body.error || res.statusText;
      if (body.detail && Array.isArray(body.detail)) {
        // Pydantic validation error: show first meaningful message
        const d = body.detail[0];
        msg = d.msg || msg;
        if (d.input !== undefined) {
          msg += ` (got: ${JSON.stringify(d.input)})`;
        }
      } else if (body.detail && typeof body.detail === 'string') {
        msg = body.detail;
      }
      throw new Error(msg);
    }

    return body;
  } catch (e) {
    if (e.message !== 'Unauthorized' && e.message !== 'AbortError') {
      // Wrap network errors with a friendly message
      if (e instanceof TypeError && e.message.includes('fetch')) {
        throw new Error('Network error');
      }
    }
    throw e;
  }
}
