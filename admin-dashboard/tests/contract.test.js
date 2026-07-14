/**
 * КОНТРАКТНЫЙ ТЕСТ
 *
 * Единственный source of truth: api-registry.json
 *
 * - Если добавил this.api() в app.js — добавь endpoint в api-registry.json
 * - Если удалил endpoint из бэка — удали из api-registry.json и app.js
 * - Тест просто сверяет что все this.api() вызовы есть в registry
 *
 * Никакого OpenAPI парсинга. Никакой магии.
 */

import { describe, it, expect } from 'vitest';
import { readFileSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const APP_JS_PATH = join(__dirname, '../internal/server/static/app.js');
const REGISTRY_PATH = join(__dirname, 'api-registry.json');

const REGISTRY = JSON.parse(readFileSync(REGISTRY_PATH, 'utf-8'));

// ── Парсинг app.js ──

function extractMethod(line, nextLines) {
  // Check current line first
  const check = (s) =>
    s.includes("method: 'POST'") || s.includes('method: "POST"') ? 'POST' :
    s.includes("method: 'PUT'") || s.includes('method: "PUT"') ? 'PUT' :
    s.includes("method: 'DELETE'") || s.includes('method: "DELETE"') ? 'DELETE' : null;

  let m = check(line);
  if (m) return m;

  // Multi-line: check next few lines
  if (nextLines) {
    for (const nl of nextLines) {
      m = check(nl);
      if (m) return m;
    }
  }
  return 'GET';
}

function extractFrontendCalls() {
  const appJs = readFileSync(APP_JS_PATH, 'utf-8');
  const lines = appJs.split('\n');
  const calls = [];

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const m = line.match(/this\.api\(\s*['"]([^'"]+)['"]/);
    if (!m) continue;

    let path = m[1];
    const fullLine = line;

    // Dynamic path: '/api/agents/' + name → /api/agents/{id}
    if (fullLine.includes(path + "' +") || fullLine.includes(path + '" +') ||
        fullLine.includes(path + "'+") || fullLine.includes(path + '"+') ||
        fullLine.includes(path + ' +')) {
      path = path.replace(/\/+$/, '') + '/{id}';
    }

    const nextFewLines = lines.slice(i + 1, i + 5);
    const method = extractMethod(fullLine, nextFewLines);

    let funcName = 'unknown';
    for (let j = i - 1; j >= Math.max(0, i - 20); j--) {
      const fn = lines[j].match(/async\s+(\w+)/);
      if (fn) { funcName = fn[1]; break; }
    }

    calls.push({ path, method, funcName, line: i + 1 });
  }

  // De-duplicate by (path, method, funcName)
  const seen = new Set();
  return calls.filter(c => {
    const key = `${c.method} ${c.path}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

const calls = extractFrontendCalls();

// ── Helpers ──

function normalize(p) {
  return p.replace(/\/+$/, '').replace(/\{name\}/g, '{id}').replace(/\{agent\}/g, '{id}').replace(/\{preset\}/g, '{id}').replace(/\/api$/, '');
}

function matchRegistry(path, method) {
  const norm = normalize(path);
  return REGISTRY.find(r => normalize(r.path) === norm && r.method === method);
}

// ── Hardcoded API paths check ──

function findHardcodedApiPaths() {
  const appJs = readFileSync(APP_JS_PATH, 'utf-8');
  const lines = appJs.split('\n');
  const found = [];

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    // Skip this.api() lines — validated separately
    if (line.includes('this.api(')) continue;
    // Skip comments
    if (line.trim().startsWith('//') || line.trim().startsWith('*')) continue;
    if (!line.trim()) continue;

    // Find any quoted string containing /api/
    const m = line.match(/['"](\/api\/[^'"]+)['"]/);
    if (!m) continue;

    const path = m[1];

    // Whitelist — эти используют raw fetch() для multipart/form-data
    const WHITELIST = [
      '/api/tenants/upload-sqlite',
      '/api/rag/documents/upload',
      '/api/health',
    ];
    if (WHITELIST.includes(path)) continue;
    if (path.startsWith('//api.')) continue;

    found.push({ path, line: i + 1, text: line.trim().slice(0, 80) });
  }
  return found;
}

// ── Tests ──

describe('CONTRACT: app.js vs api-registry.json', () => {
  it(`api-registry.json содержит ${REGISTRY.length} endpoints`, () => {
    expect(REGISTRY.length).toBeGreaterThan(0);
  });

  it(`app.js содержит ${calls.length} this.api() вызовов (уникальных путь+метод)`, () => {
    expect(calls.length).toBeGreaterThan(0);
  });

  // Каждый this.api() из app.js должен быть в registry
  describe('Every this.api() call must be in api-registry.json', () => {
    const errors = [];

    calls.forEach(({ path, method, funcName, line }) => {
      it(`${method} ${path} (${funcName}:${line})`, () => {
        const match = matchRegistry(path, method);
        if (!match) {
          // Ищем похожие для отладки
          const norm = normalize(path);
          const similar = REGISTRY
            .filter(r => normalize(r.path).split('/').slice(0, 4).join('/') === norm.split('/').slice(0, 4).join('/'))
            .map(r => `  ${r.method} ${r.path} — ${r.desc}`);

          throw new Error(
            `\n❌ ${method} ${path} (${funcName}:${line})\n` +
            `   NOT in api-registry.json!\n` +
            `   Add it to admin-dashboard/tests/api-registry.json\n` +
            (similar.length ? `\nSimilar registry entries:\n${similar.join('\n')}` : '')
          );
        }
      });
    });
  });

  // Регистрируем ошибки одной пачкой в конце
  it('ALL app.js calls exist in registry (summary)', () => {
    const missing = calls.filter(c => !matchRegistry(c.path, c.method));
    if (missing.length > 0) {
      const msg = missing.map(c =>
        `  ${c.method} ${c.path} (${c.funcName}:${c.line})`
      ).join('\n');
      throw new Error(`\n❌ ${missing.length} app.js calls missing from api-registry.json:\n${msg}\n\nFix: add them to admin-dashboard/tests/api-registry.json`);
    }
  });

  // ── Hardcoded API paths check ──
  describe('No hardcoded API paths outside this.api()', () => {
    const hardcoded = findHardcodedApiPaths();

    it(`found ${hardcoded.length} suspicious hardcoded API paths in app.js`, () => {
      if (hardcoded.length > 0) {
        const msg = hardcoded.map(h =>
          `  "${h.path}" at line ${h.line}: ${h.text}`
        ).join('\n');
        throw new Error(
          `\n❌ ${hardcoded.length} API paths hardcoded in app.js (not in this.api()):\n${msg}\n\n` +
          `All API calls must use this.api() so the contract test can validate them.\n` +
          `If it's a valid endpoint, move it to this.api().`
        );
      }
    });
  });
});
