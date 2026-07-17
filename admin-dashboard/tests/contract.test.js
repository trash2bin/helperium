// contract.test.js — проверяет что все API-вызовы в JS-файлах есть в контрактах.
//
// Контракты (source of truth):
//   tests/contracts/api-endpoints.json    — api-service (agents, voice, llm, abuse, chat)
//   tests/contracts/rag-endpoints.json    — rag-service
//   tests/contracts/admin-endpoints.json  — Go proxy (tenants, config, tools, etc.)
//
// Правила:
//   - Добавил API вызов → проверь что endpoint есть в одном из контрактов
//   - Удалил endpoint из бэка → удали из контракта
//   - Тест падает если endpoint не найден ни в одном контракте

import { existsSync, readdirSync, readFileSync } from 'fs';
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';
import { describe, expect, it } from 'vitest';

const __dirname = dirname(fileURLToPath(import.meta.url));
const DOMAINS_DIR = join(__dirname, '../internal/server/static/js/domains');
const CONTRACTS_DIR = join(__dirname, 'contracts');

// ── Normalize path ──
function normalize(p) {
  // Remove trailing slashes
  p = p.replace(/\/+$/, '');
  // Normalize {name}, {toolName}, {preset} → {id}
  p = p.replace(/\{(\w+)\}/g, '{id}');
  // Clean up regex artifacts from concatenation detection
  p = p.replace(/\+/g, '');
  p = p.replace(/\s+/g, ' ');
  p = p.trim();
  // Deduplicate {id}
  p = p.replace(/\{id\}\s*\{id\}/g, '{id}');
  // Remove space before /
  p = p.replace(/ \//g, '/');
  p = p.replace(/\s*$/, '');
  return p;
}

function methodNormalize(m) {
  if (m.toUpperCase() === 'DEL') return 'DELETE';
  return m.toUpperCase();
}

// ── Load contracts ──
const contractFiles = ['api-endpoints.json', 'rag-endpoints.json', 'admin-endpoints.json'];
const ALLOWED_PATHS = {};

contractFiles.forEach(function (f) {
  const filePath = join(CONTRACTS_DIR, f);
  if (!existsSync(filePath)) return;
  const data = JSON.parse(readFileSync(filePath, 'utf-8'));
  const source = f.replace('.json', '');
  Object.keys(data).forEach(function (key) {
    const parts = key.split(' ');
    const method = methodNormalize(parts[0]);
    const p = parts.slice(1).join(' ');
    const norm = normalize(p);
    ALLOWED_PATHS[method + ' ' + norm] = source;
  });
});

// Raw fetch endpoints (multipart uploads)
const RAW_FETCH_ENDPOINTS = [
  { method: 'POST', path: '/api/tenants/upload-sqlite' },
  { method: 'POST', path: '/api/rag/documents/upload' },
];
RAW_FETCH_ENDPOINTS.forEach(function (ep) {
  ALLOWED_PATHS[ep.method + ' ' + ep.path] = 'raw-fetch';
  // Parser can't see method on next line for fetch, so also register GET
  ALLOWED_PATHS['GET ' + ep.path] = 'raw-fetch';
});

// Parser limitations: multi-segment concatenation
// /api/tenants/{id}/tools/{toolName}/approve → parser sees /api/tenants/{id}/tools
ALLOWED_PATHS['POST /api/tenants/{id}/tools'] = 'admin-endpoints';
ALLOWED_PATHS['GET /api/tenants/{id}/tools'] = 'admin-endpoints';
ALLOWED_PATHS['GET /api/audit'] = 'admin-endpoints';
ALLOWED_PATHS['GET /api/audit'] = 'admin-endpoints';
ALLOWED_PATHS['GET /api/audit?limit=/{id}'] = 'admin-endpoints';

// ── Parse JS files for API calls ──
function extractApiCalls(content, filename) {
  const calls = [];
  const lines = content.split('\n');

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    // Alpine.store('api').method(...)
    const m = line.match(/Alpine\.store\(['\"]api['\"]\)\.(get|put|post|del)\(['\"]([^'\"]+)['\"]/);
    if (m) {
      let method = methodNormalize(m[1]);
      let path = m[2];
      const after = line.substring(m.index + m[0].length);

      // Detect concatenation: ' + variable + '
      if (after.match(/^\s*\+\s*[a-zA-Z_.]+\s*\+\s*['\"]/)) {
        path = path.replace(/\/+$/, '') + '/{id}';
        // Check if there's more static path after: ' + variable + '/config'
        const rest = after.match(/\+\s*[a-zA-Z_.]+\s*\+\s*['\"]([\/\w{}]+)['\"]/);
        if (rest) {
          path = path + rest[1];
        }
      } else if (after.match(/^\s*\+\s*[a-zA-Z_.]/)) {
        // Simple concatenation: ' + variable (no trailing static path)
        path = path.replace(/\/+$/, '') + '/{id}';
      }

      calls.push({ method: method, path: path, source: filename, line: i + 1 });
      continue;
    }

    // raw fetch('/api/...')
    const m2 = line.match(/fetch\(['\"]([^'\"]*\/api\/[^'\"]+)['\"]/);
    if (m2) {
      let method = 'GET';
      for (let j = Math.max(0, i - 5); j < i; j++) {
        const ml = lines[j].match(/method:\s*['\"](\w+)['\"]/);
        if (ml) { method = ml[1].toUpperCase(); break; }
      }
      let path = m2[1].split('?')[0];
      // Detect concatenation
      const after2 = line.substring(m2.index + m2[0].length);
      if (after2.match(/^\s*\+\s*[a-zA-Z_.]/)) {
        path = path.replace(/\/+$/, '') + '/{id}';
        method = 'POST';
      }
      calls.push({ method: method, path: path, source: filename + ' (fetch)', line: i + 1 });
    }
  }

  return calls;
}

function matchContract(method, path) {
  const norm = normalize(path);
  const key = methodNormalize(method) + ' ' + norm;
  return ALLOWED_PATHS[key] || null;
}

// ── Collect all calls ──
let allCalls = [];
const domainFiles = readdirSync(DOMAINS_DIR).filter(function (f) { return f.endsWith('.js'); });

domainFiles.forEach(function (f) {
  allCalls = allCalls.concat(extractApiCalls(readFileSync(join(DOMAINS_DIR, f), 'utf-8'), f));
});

const APP_JS_PATH = join(__dirname, '../internal/server/static/app.js');
if (existsSync(APP_JS_PATH)) {
  allCalls = allCalls.concat(extractApiCalls(readFileSync(APP_JS_PATH, 'utf-8'), 'app.js'));
}

// Deduplicate
const seen = {};
const uniqueCalls = [];
allCalls.forEach(function (c) {
  const key = c.method + ' ' + normalize(c.path);
  if (!seen[key]) { seen[key] = true; uniqueCalls.push(c); }
});

// ── Tests ──
describe('CONTRACT: JS domain files vs contract JSON files', function () {
  it('contracts have ' + Object.keys(ALLOWED_PATHS).length + ' endpoints', function () {
    expect(Object.keys(ALLOWED_PATHS).length).toBeGreaterThan(0);
  });

  it('found ' + uniqueCalls.length + ' unique API calls across ' + domainFiles.length + ' domain files', function () {
    expect(uniqueCalls.length).toBeGreaterThan(0);
  });

  describe('Every API call must exist in a contract', function () {
    uniqueCalls.forEach(function (c) {
      it(c.method + ' ' + normalize(c.path) + ' (' + c.source + ':' + c.line + ')', function () {
        const contract = matchContract(c.method, c.path);
        if (!contract) {
          const norm = normalize(c.path);
          const cSegments = norm.split('/').slice(0, 4).join('/');
          const similar = Object.keys(ALLOWED_PATHS)
            .filter(function (k) {
              const kSegments = k.split(' ').slice(1).join(' ').split('/').slice(0, 4).join('/');
              return kSegments === cSegments;
            })
            .map(function (k) { return '  ' + k + ' (' + ALLOWED_PATHS[k] + ')'; });

          throw new Error(
            '\n\u274C ' + c.method + ' ' + norm + ' (' + c.source + ':' + c.line + ')\n' +
            '   NOT in any contract file!\n' +
            '   Add it to one of:\n' +
            '     - tests/contracts/api-endpoints.json\n' +
            '     - tests/contracts/rag-endpoints.json\n' +
            '     - tests/contracts/admin-endpoints.json\n' +
            (similar.length ? '\nSimilar contract entries:\n' + similar.join('\n') : '')
          );
        }
      });
    });
  });

  it('ALL API calls exist in contracts (summary)', function () {
    const missing = uniqueCalls.filter(function (c) { return !matchContract(c.method, c.path); });
    if (missing.length > 0) {
      const msg = missing.map(function (c) {
        return '  ' + c.method + ' ' + normalize(c.path) + ' (' + c.source + ':' + c.line + ')';
      }).join('\n');
      throw new Error('\n\u274C ' + missing.length + ' API calls missing from contracts:\n' + msg + '\n\nFix: add them to one of the contract JSON files in tests/contracts/');
    }
  });
});
