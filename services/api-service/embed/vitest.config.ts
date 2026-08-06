import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    // Default: node (fast, no jsdom overhead)
    // Files that need DOM add: // @vitest-environment jsdom
    environment: 'node',
    globals: true,
    include: ['tests/**/*.test.ts'],
    coverage: {
      provider: 'v8',
      include: ['src/**/*.ts'],
      exclude: ['src/env.d.ts', 'src/index.ts'],
    },
  },
});
