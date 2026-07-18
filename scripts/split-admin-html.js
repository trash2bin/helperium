#!/usr/bin/env node
/**
 * Разбивает index.html на skeleton + pages/*.html
 *
 * Анализирует файл, находит все <div x-show="page === 'xxx'">,
 * вырезает их в отдельные файлы, остальное — skeleton.
 *
 * Запуск: node scripts/split-admin-html.js
 */

const fs = require('fs');
const path = require('path');

const htmlPath = process.argv[2] || 'admin-dashboard/internal/server/static/index.html';
const pagesDir = path.join(path.dirname(htmlPath), 'pages');

const html = fs.readFileSync(htmlPath, 'utf8');
const lines = html.split('\n');

// ── 1. Find all page sections ──
// Pattern: <!-- PageName -->\n<div x-show="page === 'xxx'" ...> ... </div>\n<!-- NextPage -->
const pageStartRe = /^\s*<div\s+x-show="page\s*===\s*'(\w+)'/;
const pageEndRe = /^\s*<\/div>/;

const pages = {}; // { name: { startLine, endLine, content: [] } }
let currentPage = null;
let depth = 0;

for (let i = 0; i < lines.length; i++) {
  const l = lines[i];

  // Check for page start
  const m = l.match(pageStartRe);
  if (m) {
    currentPage = { name: m[1], startLine: i, content: [l] };
    depth = 1;
    continue;
  }

  if (currentPage) {
    currentPage.content.push(l);

    // Track div nesting
    const openCount = (l.match(/<div[^>]*>/g) || []).length;
    const closeCount = (l.match(/<\/div>/g) || []).length;
    depth += openCount - closeCount;

    if (depth <= 0) {
      // End of this page
      currentPage.endLine = i;
      pages[currentPage.name] = currentPage;
      currentPage = null;
      depth = 0;
    }
  }
}

console.log(`Found ${Object.keys(pages).length} pages:`);
Object.entries(pages).forEach(([name, p]) => {
  console.log(`  ${name}: lines ${p.startLine}-${p.endLine} (${p.content.length} lines)`);
});

// ── 2. Write page files ──
fs.mkdirSync(pagesDir, { recursive: true });
const pageNames = Object.keys(pages).sort((a, b) => pages[a].startLine - pages[b].startLine);

pageNames.forEach(name => {
  const p = pages[name];
  const content = p.content.join('\n');
  fs.writeFileSync(path.join(pagesDir, `${name}.html`), content + '\n');
  console.log(`  wrote pages/${name}.html (${content.split('\n').length} lines)`);
});

// ── 3. Build skeleton ──
// Take all lines NOT in any page section
const pageLines = new Set();
pageNames.forEach(name => {
  const p = pages[name];
  for (let i = p.startLine; i <= p.endLine; i++) {
    pageLines.add(i);
  }
});

const skeletonLines = [];
let lastMarkerLine = -1;

for (let i = 0; i < lines.length; i++) {
  if (pageLines.has(i)) {
    // Check if we need to insert a marker
    if (lastMarkerLine !== i - 1) {
      // Find which page ends here and insert marker right before the next non-page line
      // We'll handle markers below
    }
    continue;
  }

  // Before writing this line, check if we just finished a page
  // and need to insert a marker
  let inserted = false;
  for (const name of pageNames) {
    const p = pages[name];
    if (p.endLine === i - 1) {
      skeletonLines.push(`<!--PAGE:${name}-->`);
      inserted = true;
    }
  }
  skeletonLines.push(lines[i]);
  lastMarkerLine = i;
}

// Also add markers for pages at very end
for (const name of pageNames) {
  const p = pages[name];
  if (p.endLine >= lines.length - 1) {
    skeletonLines.push(`<!--PAGE:${name}-->`);
  }
}

const skeleton = skeletonLines.join('\n');
fs.writeFileSync(htmlPath, skeleton);
console.log(`\nWrote skeleton index.html (${skeletonLines.length} lines, was ${lines.length})`);

// ── 4. Report ──
console.log('\nDone! Summary:');
console.log(`  skeleton: index.html (${skeletonLines.length} lines)`);
pageNames.forEach(name => {
  const f = path.join(pagesDir, `${name}.html`);
  const size = fs.statSync(f).size;
  console.log(`  pages/${name}.html: ${size} bytes`);
});
