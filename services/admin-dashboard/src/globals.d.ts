// Global type declarations for admin-dashboard
// Alpine.js is loaded from CDN — types are only for IDE hints

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyRecord = Record<string, any>;

interface AlpineStore {
  // Alpine JS 3.x: Alpine.store(name) — get, Alpine.store(name, value) — create/set
  (name: string): any;
  (name: string, value: Record<string, unknown>): void;
}

interface AlpineGlobal {
  store: AlpineStore;
  magic(name: string, fn: () => unknown): void;
  start(): void;
}

declare const Alpine: AlpineGlobal;

// i18n — loaded synchronously by /i18n.js before Alpine boots
declare function __(key: string, ...args: string[]): string;
declare function __setLocale(locale: string): void;
declare function __getLocale(): string;

// AppRegistry — domain registration system
declare const AppRegistry: {
  register(name: string, mod: { state: Record<string, unknown>; methods: () => Record<string, unknown> }): void;
  getState(): Record<string, unknown>;
  getMethods(): Record<string, unknown>;
  list(): string[];
  expectAll(expected: string[]): boolean;
};

// apiClient — fetch wrapper
declare const apiClient: {
  get(path: string): Promise<unknown>;
  put(path: string, data: unknown): Promise<unknown>;
  post(path: string, data?: unknown): Promise<unknown>;
  del(path: string): Promise<unknown>;
};

// dashboard — Alpine component factory, set by src/index.ts
declare function dashboard(): Record<string, unknown>;
