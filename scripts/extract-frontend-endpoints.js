#!/usr/bin/env node
// extract-frontend-endpoints.js — парсит JS-файлы и выводит METHOD /api/path
// для всех API-вызовов с учётом строковой конкатенации '+' и template literals.
//
// Поддерживаемые паттерны:
//   1. api('/api/...', {method:'PUT'})        — legacy app.js
//   2. Alpine.store('api').get('/api/...')    — domain files
//   3. fetch('/api/...', {method:'POST'})     — raw fetch (multipart)
//
// Query-параметры (после ?) изымаются перед сравнением — они не часть роута.
//
// Использование: node extract-frontend-endpoints.js file1.js file2.js ...
'use strict';

const fs = require('fs');
const files = process.argv.slice(2);
if (!files.length) {
  files.push('admin-dashboard/internal/server/static/app.js');
}

// Strips query string after ?
function stripQuery(p) {
  const idx = p.indexOf('?');
  return idx >= 0 ? p.slice(0, idx) : p;
}

function extractEndpoints(src) {
  const endpoints = [];

  function parsePathTokens(firstArg) {
    const tokens = firstArg.match(/(?:['"`][^'"`]+['"`])|(?:[^'"`\s\+]+)|(?:\+)/g) || [];
    let path = '';
    for (const tok of tokens) {
      if (tok === '+') continue;
      if ((tok.startsWith("'") && tok.endsWith("'")) ||
          (tok.startsWith('"') && tok.endsWith('"')) ||
          (tok.startsWith('`') && tok.endsWith('`'))) {
        let inner = tok.slice(1, -1);
        inner = inner.replace(/\$\{[^}]+\}/g, '*');
        path += inner;
      } else {
        path += '*';
      }
    }
    path = path.replace(/\/\*+/g, '/*');
    path = path.replace(/\/\*\//g, '/*/');
    path = path.replace(/\/\//g, '/');
    path = stripQuery(path);
    return path;
  }

  // --- Pattern 1: api(...) — legacy app.js ---
  const apiRe = /api\s*\(/g;
  let m;
  while ((m = apiRe.exec(src))) {
    const start = m.index + m[0].length;
    let depth = 1;
    let i = start;
    while (depth > 0 && i < src.length) {
      if (src[i] === '(') depth++;
      else if (src[i] === ')') depth--;
      i++;
    }
    const rawBody = src.slice(start, i - 1).replace(/\n/g, ' ');

    let method = 'GET';
    const methodM = /method\s*:\s*['"]([A-Z]+)['"]/.exec(rawBody);
    if (methodM) method = methodM[1];

    const commaIdx = rawBody.indexOf(',');
    let firstArg = commaIdx >= 0 ? rawBody.slice(0, commaIdx) : rawBody;

    const path = stripQuery(parsePathTokens(firstArg));
    if (path === '*' || path === '' || !path.startsWith('/api/')) continue;
    endpoints.push({ method, path });
  }

  // --- Pattern 2: Alpine.store('api').method('/api/...') — domain files ---
  const alpineRe = /Alpine\.store\(['"]api['"]\)\.(get|put|post|del)\s*\(/g;
  while ((m = alpineRe.exec(src))) {
    const method = m[1].toUpperCase() === 'DEL' ? 'DELETE' : m[1].toUpperCase();
    const start = m.index + m[0].length;
    let depth = 1;
    let i = start;
    while (depth > 0 && i < src.length) {
      if (src[i] === '(') depth++;
      else if (src[i] === ')') depth--;
      i++;
    }
    const rawBody = src.slice(start, i - 1).replace(/\n/g, ' ');

    const commaIdx = rawBody.indexOf(',');
    let firstArg = commaIdx >= 0 ? rawBody.slice(0, commaIdx) : rawBody;

    const path = stripQuery(parsePathTokens(firstArg));
    if (path === '*' || path === '' || !path.startsWith('/api/')) continue;
    endpoints.push({ method, path });
  }

  // --- Pattern 3: raw fetch('/api/...') ---
  const fetchRe = /fetch\s*\(['"]([^'"]*\/api\/[^'"]+)['"]/g;
  while ((m = fetchRe.exec(src))) {
    let path = stripQuery(m[1]);
    let method = 'GET';
    const before = src.slice(Math.max(0, m.index - 300), m.index);
    const methodMBack = /method\s*:\s*['"]([A-Z]+)['"]/.exec(before);
    if (methodMBack) method = methodMBack[1];
    if (method === 'GET') {
      const afterFetch = src.slice(m.index, m.index + 300);
      const methodMFwd = /method\s*:\s*['"]([A-Z]+)['"]/.exec(afterFetch);
      if (methodMFwd) method = methodMFwd[1];
    }

    if (!path.startsWith('/api/')) continue;
    endpoints.push({ method, path });
  }

  return endpoints;
}

const allEndpoints = [];
for (const file of files) {
  const src = fs.readFileSync(file, 'utf8');
  allEndpoints.push(...extractEndpoints(src));
}

const dedup = {};
for (const ep of allEndpoints) {
  const key = ep.method + ' ' + ep.path;
  if (!dedup[key]) dedup[key] = 0;
  dedup[key]++;
}

for (const [key, count] of Object.entries(dedup).sort()) {
  console.log(key);
}
