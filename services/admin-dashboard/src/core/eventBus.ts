// eventBus.ts — Alpine.store('events') — cross-domain pub/sub

const w = window as any;

document.addEventListener('alpine:init', () => {
  w.Alpine.store('events', {
    _listeners: {} as Record<string, ((data: unknown) => void)[]>,

    on(event: string, fn: (data: unknown) => void): void {
      if (!this._listeners[event]) this._listeners[event] = [];
      this._listeners[event]!.push(fn);
    },

    off(event: string, fn?: (data: unknown) => void): void {
      if (!this._listeners[event]) return;
      if (!fn) { delete this._listeners[event]; return; }
      this._listeners[event] = this._listeners[event]!.filter((f: (data: unknown) => void) => f !== fn);
    },

    emit(event: string, data?: unknown): void {
      const fns = this._listeners[event];
      if (!fns) return;
      for (const fn of fns) {
        try { fn(data); } catch (e) {
          console.error('[eventBus] Error in handler for "' + event + '":', e);
        }
      }
    },
  });
});

export {};
