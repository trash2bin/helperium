// apiLogger.ts — Alpine.store('apiLogger') — HTTP request debug panel

const w = window as any;

interface LogEntry {
  id: number; method: string; path: string; status: number;
  reqBody: string | null; resBody: string; durationMs: number; ts: string;
}

interface LogToast {
  id: number; text: string; class: string; entryId: number;
}

document.addEventListener('alpine:init', () => {
  let toastCounter = 0;

  w.Alpine.store('apiLogger', {
    entries: [] as LogEntry[],
    apiToasts: [] as LogToast[],
    showPanel: false,
    selectedEntry: null as LogEntry | null,

    _push(entry: LogEntry): void {
      this.entries = [entry, ...this.entries].slice(0, 50);
      if (entry.method !== 'GET') this._showApiToast(entry);
    },

    _showApiToast(entry: LogEntry): void {
      const id = ++toastCounter;
      const isOk = entry.status >= 200 && entry.status < 300;
      const icon = isOk ? '\u2713' : '\u2717';
      this.apiToasts.push({
        id, text: `${icon} [${entry.status}] ${entry.method} ${entry.path}`,
        class: isOk ? 'api-toast-ok' : 'api-toast-err', entryId: entry.id,
      });
      setTimeout(() => { this.apiToasts = this.apiToasts.filter((t: LogToast) => t.id !== id); }, 4000);
    },

    dismissToast(id: number): void {
      this.apiToasts = this.apiToasts.filter((t: LogToast) => t.id !== id);
    },

    togglePanel(): void { this.showPanel = !this.showPanel; },

    selectEntry(id: number): void {
      const found = this.entries.find((e: LogEntry) => e.id === id);
      if (found) this.selectedEntry = found;
    },

    closeDetail(): void { this.selectedEntry = null; },

    _catchUp(): void {
      const globalLog = w.__apiLog as LogEntry[] | undefined;
      if (!globalLog) return;
      for (let i = globalLog.length - 1; i >= 0; i--) {
        const e = globalLog[i];
        if (!e) continue;
        if (!this.entries.some((x: LogEntry) => x.id === e.id)) {
          this.entries = [e, ...this.entries].slice(0, 50);
        }
      }
    },
  });

  w.Alpine.store('apiLogger')._catchUp();
});

export {};
