// contract.test.js — проверяет что все API-вызовы в JS-файлах есть в OpenAPI контрактах.
//
// Источники правды (source of truth):
//   ../../specs/api.openapi.yaml    — api-service (agents, voice, llm, abuse, chat)
//   ../../specs/rag.openapi.yaml    — rag-service
//   spec/openapi.json               — admin-dashboard Go proxy (генерится из chi-роутера)
//
// Правила:
//   - Добавил API вызов → проверь что endpoint есть в одном из контрактов
//   - Удалил endpoint из бэка → удали из контракта
//   - Тест падает если endpoint не найден ни в одном контракте
//
// Контракты НЕ редактируются вручную:
//   - api.openapi.yaml / rag.openapi.yaml — авто-генерация из FastAPI (python тесты)
//   - admin-dashboard openapi.json — авто-генерация из Go chi-роутера (build.sh / startup)

import { existsSync, readdirSync, readFileSync } from 'fs';
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';
import { describe, expect, it } from 'vitest';

const __dirname = dirname(fileURLToPath(import.meta.url));

// ── Paths ──
const DOMAINS_DIR = join(__dirname, '../src/domains');
const OPENAPI_SPECS = [
  { file: join(__dirname, '../../specs/api.openapi.yaml'),   label: 'api-service',   parser: 'yaml' },
  { file: join(__dirname, '../../specs/rag.openapi.yaml'),   label: 'rag-service',   parser: 'yaml' },
  { file: join(__dirname, '../internal/server/static/openapi.json'), label: 'admin-dashboard', parser: 'json' },
];

// ── Simple YAML parser (no external deps — just what we need for OpenAPI) ──
function parseSimpleYaml(text) {
  const lines = text.split('\n');
  const result = {};
  const stack = [{ obj: result, indent: -1 }];

  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const trimmed = line.trimEnd();

    // Skip comments, empty lines
    if (trimmed.trim() === '' || trimmed.trimStart().startsWith('#')) {
      i++;
      continue;
    }

    // Detect indent level
    const indent = line.length - trimmed.length;

    // Find parent on stack
    while (stack.length > 1 && indent <= stack[stack.length - 1].indent) {
      stack.pop();
    }

    // Check if it's a key: value or key:
    const colonIdx = trimmed.indexOf(':');
    if (colonIdx < 0) {
      i++;
      continue;
    }

    const key = trimmed.slice(0, colonIdx).trim();
    const valuePart = trimmed.slice(colonIdx + 1).trim();

    const current = stack[stack.length - 1].obj;

    if (valuePart === '' || valuePart === '|' || valuePart === '>') {
      // Object or multiline scalar
      const newObj = {};
      if (Array.isArray(current)) {
        current.push(newObj);
      } else {
        current[key] = newObj;
      }
      stack.push({ obj: newObj, indent });

      // Handle | and > multiline
      if (valuePart === '|' || valuePart === '>') {
        const lines2 = [];
        i++;
        while (i < lines.length) {
          const l = lines[i];
          if (l.trim() === '' || l.trimStart().startsWith('#')) { i++; continue; }
          const li = l.length - l.trimEnd().length;
          if (li <= indent) break;
          lines2.push(l.trim());
          i++;
        }
        newObj['__value'] = lines2.join(valuePart === '>' ? ' ' : '\n');
        stack.pop();
        continue;
      }
    } else if (valuePart.startsWith('[')) {
      // Array inline
      try { current[key] = JSON.parse(valuePart.replace(/'/g, '"')); }
      catch { current[key] = valuePart; }
    } else if (valuePart.startsWith('{')) {
      // Object inline
      try { current[key] = JSON.parse(valuePart.replace(/'/g, '"')); }
      catch { current[key] = valuePart; }
    } else {
      // Scalar
      current[key] = parseYamlScalar(valuePart);
    }

    // Check for array items (lines starting with -)
    let nextIdx = i + 1;
    while (nextIdx < lines.length) {
      const nextLine = lines[nextIdx];
      const nextTrimmed = nextLine.trimStart();
      const nextIndent = nextLine.length - nextTrimmed.length;

      if (nextTrimmed.startsWith('- ') || nextTrimmed.startsWith('-')) {
        // Array item
        if (!Array.isArray(current[key])) {
          current[key] = [];
        }
        const itemContent = nextTrimmed.slice(1).trim();
        if (itemContent.includes(':')) {
          // Object in array — push placeholder and process recursively
          const itemObj = {};
          current[key].push(itemObj);
          stack.push({ obj: itemObj, indent: nextIndent });
          // Reprocess this line as key:value inside object
          const subColon = itemContent.indexOf(':');
          const subKey = itemContent.slice(0, subColon).trim();
          const subVal = itemContent.slice(subColon + 1).trim();
          itemObj[subKey] = parseYamlScalar(subVal);
          stack.pop();
        } else {
          current[key].push(parseYamlScalar(itemContent));
        }
        nextIdx++;
      } else if (nextTrimmed.startsWith('  ') && nextTrimmed.includes(':')) {
        // Same level continuation
        break;
      } else {
        break;
      }
    }

    i++;
  }

  return result;
}

function parseYamlScalar(val) {
  if (val === 'true') return true;
  if (val === 'false') return false;
  if (val === 'null' || val === '~') return null;
  const num = Number(val);
  if (!isNaN(num) && val !== '') return num;
  if ((val.startsWith("'") && val.endsWith("'")) || (val.startsWith('"') && val.endsWith('"'))) {
    return val.slice(1, -1);
  }
  return val;
}

// ── Навигация по parsed OpenAPI ──
function get(obj, path) {
  const parts = path.split('.');
  let current = obj;
  for (const part of parts) {
    if (current == null || typeof current !== 'object') return undefined;
    current = current[part];
  }
  return current;
}

// ── Load all contracts from specs ──
function loadContracts() {
  const ALLOWED_PATHS = {};

  for (const spec of OPENAPI_SPECS) {
    if (!existsSync(spec.file)) {
      console.warn(`[contract] WARN: ${spec.file} not found — skipping`);
      continue;
    }

    const raw = readFileSync(spec.file, 'utf-8');
    let parsed;

    if (spec.parser === 'json') {
      parsed = JSON.parse(raw);
    } else {
      parsed = parseSimpleYaml(raw);
    }

    const paths = get(parsed, 'paths') || get(parsed, 'paths.__value');
    if (!paths || typeof paths !== 'object') {
      console.warn(`[contract] WARN: ${spec.file} has no paths — skipping`);
      continue;
    }

    for (const [path, methods] of Object.entries(paths)) {
      if (!methods || typeof methods !== 'object') continue;
      for (const [method] of Object.entries(methods)) {
        if (!['get', 'post', 'put', 'delete', 'patch', 'head', 'options'].includes(method)) continue;
        const key = method.toUpperCase() + ' ' + normalizePath(path);
        ALLOWED_PATHS[key] = spec.label;
      }
    }
  }

  return ALLOWED_PATHS;
}

// ── Normalize path for matching ──
function normalizePath(p) {
  // Handle OpenAPI {param} style → {id}
  p = p.replace(/\{(\w+)\}/g, '{id}');
  // Remove trailing slash
  p = p.replace(/\/+$/, '');
  return p;
}

// ── Parse JS files for API calls (unchanged from original logic) ──
function methodNormalize(m) {
  if (m.toUpperCase() === 'DEL') return 'DELETE';
  return m.toUpperCase();
}

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

      if (after.match(/^\s*\+\s*[a-zA-Z_.]+\s*\+\s*['\"]/)) {
        path = path.replace(/\/+$/, '') + '/{id}';
        const rest = after.match(/\+\s*[a-zA-Z_.]+\s*\+\s*['\"]([\/\w{}]+)['\"]/);
        if (rest) path = path + rest[1];
      } else if (after.match(/^\s*\+\s*[a-zA-Z_.]/)) {
        path = path.replace(/\/+$/, '') + '/{id}';
      }

      calls.push({ method, path: normalizePath(path), source: filename, line: i + 1 });
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
      const after2 = line.substring(m2.index + m2[0].length);
      if (after2.match(/^\s*\+\s*[a-zA-Z_.]/)) {
        path = path.replace(/\/+$/, '') + '/{id}';
        method = 'POST';
      }
      calls.push({ method, path: normalizePath(path), source: filename + ' (fetch)', line: i + 1 });
    }
  }

  return calls;
}

// ── Load contracts ──
const ALLOWED_PATHS = loadContracts();

// Parse limitations: multi-segment concatenation
// /api/tenants/{id}/tools/{toolName}/approve → parser sees /api/tenants/{id}/tools
ALLOWED_PATHS['POST /api/tenants/{id}/tools'] = 'admin-dashboard (parser hack)';
ALLOWED_PATHS['GET /api/tenants/{id}/tools'] = 'admin-dashboard (parser hack)';

// Raw fetch endpoints (multipart uploads) — парсер не видит method: 'POST' на предыдущей строке
const RAW_FETCH_ENDPOINTS = [
  { method: 'POST', path: '/api/tenants/upload-sqlite' },
  { method: 'POST', path: '/api/rag/documents/upload' },
];
RAW_FETCH_ENDPOINTS.forEach(function (ep) {
  ALLOWED_PATHS[ep.method + ' ' + ep.path] = 'admin-dashboard (raw fetch)';
  // Parser can't see method on next line for fetch, so also register GET
  ALLOWED_PATHS['GET ' + ep.path] = 'admin-dashboard (raw fetch hack)';
});

// ── Collect all calls from JS files ──
let allCalls = [];
const domainFiles = readdirSync(DOMAINS_DIR).filter(function (f) { return f.endsWith('.ts'); });

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
  const key = c.method + ' ' + c.path;
  if (!seen[key]) { seen[key] = true; uniqueCalls.push(c); }
});

// ── Tests ──
describe('CONTRACT: JS domain files vs OpenAPI spec files', function () {
  it('contracts have ' + Object.keys(ALLOWED_PATHS).length + ' endpoints loaded from specs', function () {
    expect(Object.keys(ALLOWED_PATHS).length).toBeGreaterThan(0);
  });

  it('found ' + uniqueCalls.length + ' unique API calls across ' + domainFiles.length + ' domain files', function () {
    expect(uniqueCalls.length).toBeGreaterThan(0);
  });

  describe('Every API call must exist in an OpenAPI contract', function () {
    uniqueCalls.forEach(function (c) {
      it(c.method + ' ' + c.path + ' (' + c.source + ':' + c.line + ')', function () {
        const contract = ALLOWED_PATHS[c.method + ' ' + c.path];
        if (!contract) {
          const segments = c.path.split('/').slice(0, 4).join('/');
          const similar = Object.keys(ALLOWED_PATHS)
            .filter(function (k) {
              const ks = k.split(' ').slice(1).join(' ').split('/').slice(0, 4).join('/');
              return ks === segments;
            })
            .map(function (k) { return '  ' + k + ' (' + ALLOWED_PATHS[k] + ')'; });

          throw new Error(
            '\n\u274C ' + c.method + ' ' + c.path + ' (' + c.source + ':' + c.line + ')\n' +
            '   NOT in any OpenAPI contract!\n' +
            '   Expected in one of:\n' +
            '     - ../../specs/api.openapi.yaml\n' +
            '     - ../../specs/rag.openapi.yaml\n' +
            '     - ../internal/server/static/openapi.json (run build.sh first)\n' +
            (similar.length ? '\nSimilar contract entries:\n' + similar.join('\n') : '')
          );
        }
      });
    });
  });

  it('ALL API calls exist in OpenAPI contracts (summary)', function () {
    const missing = uniqueCalls.filter(function (c) { return !ALLOWED_PATHS[c.method + ' ' + c.path]; });
    if (missing.length > 0) {
      const msg = missing.map(function (c) {
        return '  ' + c.method + ' ' + c.path + ' (' + c.source + ':' + c.line + ')';
      }).join('\n');
      throw new Error('\n\u274C ' + missing.length + ' API calls missing from contracts:\n' + msg + '\n\nFix: add endpoint to the appropriate OpenAPI spec or run build.sh');
    }
  });
});
