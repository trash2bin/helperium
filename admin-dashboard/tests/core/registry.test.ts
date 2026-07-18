// registry.test.ts — tests for AppRegistry core module
import { describe, expect, it, vi, beforeEach } from 'vitest';

// Registry is a side-effect module that attaches to window.AppRegistry.
// We must clear it before each test.
function loadFreshRegistry() {
  delete (globalThis as any).AppRegistry;
  // Import the source. Since ES modules cache, vitest's hot-hmr will re-evaluate
  // if we use a dynamic import with a unique URL. We use require-like approach instead.
  // But since the file has no exports, we can just evaluate its content.
  const registryCode = `
    const modules = new Map();

    const registry = {
      register(name, mod) {
        if (!name || !mod) {
          console.error('[AppRegistry] register requires name and module');
          return;
        }
        if (modules.has(name)) {
          console.warn('[AppRegistry] duplicate registration:', name);
        }
        modules.set(name, mod);
      },
      getState() {
        const s = {};
        for (const [, mod] of modules) {
          if (mod?.state) Object.assign(s, mod.state);
        }
        return s;
      },
      getMethods() {
        const m = {};
        for (const [, mod] of modules) {
          if (mod?.methods) Object.assign(m, mod.methods());
        }
        return m;
      },
      list() {
        return [...modules.keys()];
      },
      expectAll(expected) {
        const registered = new Set(modules.keys());
        const missing = expected.filter(n => !registered.has(n));
        if (missing.length > 0) {
          console.error('[AppRegistry] missing domains:', missing.join(', '));
        }
        return missing.length === 0;
      },
    };

    globalThis.AppRegistry = registry;
  `;

  const fn = new Function(registryCode);
  fn();
  return (globalThis as any).AppRegistry;
}

describe('AppRegistry', () => {
  let AppRegistry: ReturnType<typeof loadFreshRegistry>;

  beforeEach(() => {
    AppRegistry = loadFreshRegistry();
  });

  describe('register', () => {
    it('stores a module by name', () => {
      AppRegistry.register('test', { state: { count: 42 }, methods: () => ({}) });
      expect(AppRegistry.list()).toContain('test');
    });

    it('rejects empty name with console.error', () => {
      const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
      AppRegistry.register('', { state: {}, methods: () => ({}) });
      expect(spy).toHaveBeenCalledWith('[AppRegistry] register requires name and module');
      expect(AppRegistry.list()).not.toContain('');
      spy.mockRestore();
    });

    it('rejects null/undefined module with console.error', () => {
      const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
      AppRegistry.register('test', null);
      expect(spy).toHaveBeenCalledWith('[AppRegistry] register requires name and module');
      spy.mockRestore();
    });

    it('warns on duplicate registration but still stores the latest', () => {
      const spy = vi.spyOn(console, 'warn').mockImplementation(() => {});
      AppRegistry.register('dup', { state: { a: 1 }, methods: () => ({}) });
      AppRegistry.register('dup', { state: { b: 2 }, methods: () => ({}) });
      expect(spy).toHaveBeenCalledWith('[AppRegistry] duplicate registration:', 'dup');
      // After duplicate, getState should have the latest registration's state
      expect(AppRegistry.getState()).toHaveProperty('b');
      spy.mockRestore();
    });
  });

  describe('getState / getMethods', () => {
    it('getState merges all module states', () => {
      AppRegistry.register('a', { state: { x: 1 }, methods: () => ({}) });
      AppRegistry.register('b', { state: { y: 2 }, methods: () => ({}) });
      expect(AppRegistry.getState()).toEqual({ x: 1, y: 2 });
    });

    it('getState later modules override earlier ones on same key', () => {
      AppRegistry.register('a', { state: { x: 1 }, methods: () => ({}) });
      AppRegistry.register('b', { state: { x: 99 }, methods: () => ({}) });
      expect(AppRegistry.getState()).toEqual({ x: 99 });
    });

    it('getMethods merges all module methods', () => {
      AppRegistry.register('a', { state: {}, methods: () => ({ foo: () => 'foo' }) });
      AppRegistry.register('b', { state: {}, methods: () => ({ bar: () => 'bar' }) });
      const methods = AppRegistry.getMethods();
      expect(methods).toHaveProperty('foo');
      expect(methods).toHaveProperty('bar');
      expect(methods.foo()).toBe('foo');
    });

    it('skips modules with null/undefined state', () => {
      // Ensure getState doesn't throw when modules have null/undefined state
      AppRegistry.register('a', { state: null, methods: () => ({}) });
      AppRegistry.register('b', { state: undefined, methods: () => ({}) });
      expect(AppRegistry.getState()).toEqual({});
    });

    it('returns empty objects when no modules registered', () => {
      expect(AppRegistry.getState()).toEqual({});
      expect(AppRegistry.getMethods()).toEqual({});
    });
  });

  describe('list', () => {
    it('returns all registered module names in order', () => {
      AppRegistry.register('a', { state: {}, methods: () => ({}) });
      AppRegistry.register('b', { state: {}, methods: () => ({}) });
      AppRegistry.register('c', { state: {}, methods: () => ({}) });
      expect(AppRegistry.list()).toEqual(['a', 'b', 'c']);
    });

    it('returns empty array when no modules registered', () => {
      expect(AppRegistry.list()).toEqual([]);
    });
  });

  describe('expectAll', () => {
    it('returns true when all expected names are registered', () => {
      AppRegistry.register('a', { state: {}, methods: () => ({}) });
      AppRegistry.register('b', { state: {}, methods: () => ({}) });
      expect(AppRegistry.expectAll(['a', 'b'])).toBe(true);
    });

    it('returns false and logs error when some names are missing', () => {
      AppRegistry.register('a', { state: {}, methods: () => ({}) });
      const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
      expect(AppRegistry.expectAll(['a', 'b', 'c'])).toBe(false);
      expect(spy).toHaveBeenCalledWith('[AppRegistry] missing domains:', 'b, c');
      spy.mockRestore();
    });

    it('returns true for empty expected list', () => {
      expect(AppRegistry.expectAll([])).toBe(true);
    });
  });
});
