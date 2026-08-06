import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    environment: 'jsdom',
    include: ['**/*.test.{js,ts}'],
    setupFiles: ['./setup.ts'],
  },
});
