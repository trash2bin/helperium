// registry.ts — AppRegistry: explicit domain registration
// Each domain calls AppRegistry.register(name, { state, methods })
// index.ts calls AppRegistry.getState() and AppRegistry.getMethods()
// to merge everything into the dashboard() component.

interface RegistryModule {
  state: Record<string, unknown>;
  methods: () => Record<string, unknown>;
}

const modules = new Map<string, RegistryModule>();

const registry = {
  register<S extends Record<string, unknown>, M extends Record<string, unknown>>(
    name: string,
    mod: { state: S; methods: () => M }
  ): void {
    if (!name || !mod) {
      console.error('[AppRegistry] register requires name and module');
      return;
    }
    if (modules.has(name)) {
      console.warn('[AppRegistry] duplicate registration:', name);
    }
    modules.set(name, mod as unknown as RegistryModule);
  },

  getState(): Record<string, unknown> {
    const s: Record<string, unknown> = {};
    for (const [, mod] of modules) {
      if (mod?.state) Object.assign(s, mod.state);
    }
    return s;
  },

  getMethods(): Record<string, unknown> {
    const m: Record<string, unknown> = {};
    for (const [, mod] of modules) {
      if (mod?.methods) Object.assign(m, mod.methods());
    }
    return m;
  },

  list(): string[] {
    return [...modules.keys()];
  },

  expectAll(expected: string[]): boolean {
    const registered = new Set(modules.keys());
    const missing = expected.filter(n => !registered.has(n));
    if (missing.length > 0) {
      console.error('[AppRegistry] missing domains:', missing.join(', '));
    }
    return missing.length === 0;
  },
};

// eslint-disable-next-line @typescript-eslint/no-explicit-any
(window as any).AppRegistry = registry;

export {};
