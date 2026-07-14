#!/usr/bin/env node
// extract-frontend-endpoints.js — парсит app.js и выводит METHOD /api/path
// для всех вызовов api() с учётом строковой конкатенации '+' и template literals.
//
// Алгоритм (v4 — финальный, правильный):
//   Для каждого api()-вызова:
//     1. METHOD из {method:'...'} или GET.
//     2. Вычленить фрагменты пути между первым строковым аргументом и запятой (вторым аргументом).
//     3. Каждый не-строковой токен заменять на '*', строковые добавлять как есть.
//     4. Вывести METHOD + итоговый путь.
//
// Пример: api('/api/tenants/' + this.id + '/config', {method:'PUT'})
//   → фрагменты: '/api/tenants/'  '+'  this.id  '+'  '/config'  ','
//   → путь: /api/tenants/*/config  METHOD: PUT
//
// Пример: api('/api/llm-config')
//   → путь: /api/llm-config  METHOD: GET
'use strict';

const fs = require('fs');
const app = fs.readFileSync(process.argv[2] || 'admin-dashboard/internal/server/static/app.js', 'utf8');

function extractEndpoints(src) {
  const endpoints = [];
  const apiRe = /api\s*\(/g;
  let m;
  while ((m = apiRe.exec(src))) {
    const start = m.index + m[0].length;
    // Найти парную закрывающую скобку
    let depth = 1;
    let i = start;
    while (depth > 0 && i < src.length) {
      if (src[i] === '(') depth++;
      else if (src[i] === ')') depth--;
      i++;
    }
    const rawBody = src.slice(start, i - 1).replace(/\n/g, ' ');

    // 1) METHOD
    let method = 'GET';
    const methodM = /method\s*:\s*['"]([A-Z]+)['"]/.exec(rawBody);
    if (methodM) method = methodM[1];

    // 2) Путь — разбираем первый аргумент api() до запятой (или до конца первого аргумента)
    //
    // Берём всё от начала до первой запятой, которая НЕ внутри объекта/строки.
    // Но проще: берём первый аргумент — первый токен до запятой или до { (второй аргумент).
    // Если rawBody = '/api/tenants/' + this.id + '/config', {method:'PUT'}
    // то первый аргумент = '/api/tenants/' + this.id + '/config'
    const commaIdx = rawBody.indexOf(',');
    // Ищем где заканчивается первый аргумент:
    //   • запятая перед {method:...} (или перед body:)
    //   • если нет запятой — один аргумент, весь rawBody это путь
    let firstArg = commaIdx >= 0 ? rawBody.slice(0, commaIdx) : rawBody;

    // Парсим firstArg на токены:
    //   string_literal, template_literal, '+', variable/expression
    // Токены разделены пробелами и '+'.
    // Упрощённый парсер: разбиваем по '+' и пробелам, для каждого токена:
    //   если это строка / template literal в кавычках → добавляем содержимое
    //   иначе → добавляем '*'
    const tokens = firstArg.match(/(?:['"`][^'"`]+['"`])|(?:[^'"`\s\+]+)|(?:\+)/g) || [];
    let path = '';
    for (const tok of tokens) {
      if (tok === '+') continue;
      // Проверяем: строковой литерал / template literal?
      if ((tok.startsWith("'") && tok.endsWith("'")) ||
          (tok.startsWith('"') && tok.endsWith('"')) ||
          (tok.startsWith('`') && tok.endsWith('`'))) {
        // Содержимое без кавычек
        let inner = tok.slice(1, -1);
        // Заменить ${...} на *
        inner = inner.replace(/\$\{[^}]+\}/g, '*');
        path += inner;
      } else {
        // Переменная / выражение → '*'
        path += '*';
      }
    }

    // Финальная чистка
    path = path.replace(/\/\*+/g, '/*');         // двойные звёзды → одна
    path = path.replace(/\/\*\//g, '/*/');       // чистка
    path = path.replace(/\/\/+/g, '/');          // двойные слеши

    // Отфильтровать мусор (определение функции api(url, options), пустой вызов api())
    if (path === '*' || path === '' || !path.startsWith('/api/')) continue;

    endpoints.push({ method, path });
  }
  return endpoints;
}

const eps = extractEndpoints(app);
const dedup = {};
for (const ep of eps) {
  const key = ep.method + ' ' + ep.path;
  if (!dedup[key]) dedup[key] = 0;
  dedup[key]++;
}

for (const [key, count] of Object.entries(dedup).sort()) {
  console.log(key);
}
