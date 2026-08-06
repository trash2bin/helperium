// notify.ts — Alpine.store('notify') — toast notifications

interface NotifyItem {
  id: number;
  text: string;
  type: 'success' | 'error' | 'warn';
}

const w = window as any;

document.addEventListener('alpine:init', () => {
  let counter = 0;
  const items: NotifyItem[] = [];

  w.Alpine.store('notify', {
    get items(): NotifyItem[] { return items; },

    success(msg: string): void {
      const id = ++counter;
      items.push({ id, text: msg, type: 'success' });
      setTimeout(() => { const idx = items.findIndex(i => i.id === id); if (idx >= 0) items.splice(idx, 1); }, 3000);
    },

    error(msg: string): void {
      const id = ++counter;
      items.push({ id, text: msg, type: 'error' });
      setTimeout(() => { const idx = items.findIndex(i => i.id === id); if (idx >= 0) items.splice(idx, 1); }, 5000);
    },

    warn(msg: string): void {
      const id = ++counter;
      items.push({ id, text: msg, type: 'warn' });
      setTimeout(() => { const idx = items.findIndex(i => i.id === id); if (idx >= 0) items.splice(idx, 1); }, 4000);
    },

    clear(): void { items.length = 0; },
  });
});

export {};
