// store.ts — Alpine.store('api') + Alpine.store('ui')

const w = window as any;

document.addEventListener('alpine:init', () => {
  const api = w.apiClient as {
    get(path: string): Promise<unknown>;
    put(path: string, data: unknown): Promise<unknown>;
    post(path: string, data?: unknown): Promise<unknown>;
    del(path: string): Promise<unknown>;
  };

  w.Alpine.store('api', {
    get: (path: string) => api.get(path),
    put: (path: string, data: unknown) => api.put(path, data),
    post: (path: string, data?: unknown) => api.post(path, data),
    del: (path: string) => api.del(path),
  });

  w.Alpine.store('ui', {
    tokenSet: !!localStorage.getItem('admin_token'),
    role: localStorage.getItem('admin_role') || '',
    tokenInput: '',
    page: 'dashboard',
    error: '',
    loading: false,
    dataService: '',

    isViewer(): boolean { return this.role === 'viewer'; },
    isAdmin(): boolean { return this.role === 'admin'; },

    login(): void {
      const token = this.tokenInput.trim();
      if (!token) return;
      localStorage.setItem('admin_token', token);
      this.tokenSet = true;
      this.error = '';

      api.get('/api/dashboard').then((data: any) => {
        this.role = String(data.role || 'admin');
        localStorage.setItem('admin_role', this.role);
        document.documentElement.dataset.role = this.role;
      }).catch(() => {
        this.role = 'admin';
        localStorage.setItem('admin_role', 'admin');
        document.documentElement.dataset.role = 'admin';
      });
    },

    logout(): void {
      localStorage.removeItem('admin_token');
      localStorage.removeItem('admin_role');
      delete document.documentElement.dataset.role;
      location.reload();
    },

    navigate(pageName: string): void {
      this.page = pageName;
    },
  });
});

export {};
