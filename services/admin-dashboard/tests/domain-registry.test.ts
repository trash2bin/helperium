// domain-registry.test.ts — every domain module must be wired into the app.
//
// Regression for commit 50bd165: reports.ts existed but was never imported in
// index.ts, so the Жалобы page rendered nothing. Static per-file scans (i18n,
// contract) cannot catch a missing import, so this test derives the expected
// set from the filesystem and asserts both wiring surfaces.
import { readdirSync, readFileSync } from 'fs';
import { dirname, resolve } from 'path';
import { fileURLToPath } from 'url';
import { describe, expect, it } from 'vitest';

const __dirname = dirname(fileURLToPath(import.meta.url));
const domainsDir = resolve(__dirname, '../src/domains');

const domainNames = readdirSync(domainsDir)
  .filter(f => f.endsWith('.ts'))
  .map(f => f.replace(/\.ts$/, ''))
  .sort();

describe('domain registry — wiring completeness', () => {
  const indexSrc = readFileSync(resolve(__dirname, '../src/index.ts'), 'utf8');

  it('index.ts imports every domain module', () => {
    const missing = domainNames.filter(
      name => !indexSrc.includes(`import './domains/${name}.js';`),
    );
    expect(missing, `domain modules not imported in index.ts: ${missing.join(', ')}`).toEqual([]);
  });

  it('EXPECTED_DOMAINS covers every domain module', () => {
    const match = indexSrc.match(/EXPECTED_DOMAINS\s*=\s*\[([^\]]*)\]/);
    expect(match, 'EXPECTED_DOMAINS array not found in index.ts').toBeTruthy();
    const declared = (match![1].match(/'([^']+)'/g) ?? []).map(s => s.replace(/'/g, ''));
    const missing = domainNames.filter(name => !declared.includes(name));
    expect(missing, `EXPECTED_DOMAINS misses: ${missing.join(', ')}`).toEqual([]);
    const unknown = declared.filter(name => !domainNames.includes(name));
    expect(unknown, `EXPECTED_DOMAINS lists unknown domains: ${unknown.join(', ')}`).toEqual([]);
  });
});
