import { vi } from 'vitest';

// Mock Alpine globally for modules that reference it (store.ts, eventBus.ts, notify.ts, etc.)
// The mock needs to handle both getter and setter calls:
//   Alpine.store('name')        → get store
//   Alpine.store('name', val)   → set store
//   Alpine.magic('__', fn)      → register magic helper
const alpineStore: Record<string, unknown> = {};

(globalThis as any).Alpine = {
  magic: vi.fn(),
  store: vi.fn((name: string, val?: unknown) => {
    if (val !== undefined) {
      alpineStore[name] = val;
      return;
    }
    if (!(name in alpineStore)) {
      alpineStore[name] = {};
    }
    return alpineStore[name];
  }),
  version: '3.14.8',
};

// Mock XMLHttpRequest for i18n.ts synchronous XHR loading
class MockXHR {
  status = 200;
  responseText = JSON.stringify({
    locale: 'ru',
    translations: {
      ru: {
        'nav.dashboard': '📊 Дашборд',
        'nav.tenants': '🏪 Тенанты',
      },
    },
  });
  open = vi.fn();
  send = vi.fn();
  setRequestHeader = vi.fn();
  onreadystatechange: (() => void) | null = null;
  readyState = 4;
}

(globalThis as any).XMLHttpRequest = MockXHR as unknown as typeof XMLHttpRequest;
