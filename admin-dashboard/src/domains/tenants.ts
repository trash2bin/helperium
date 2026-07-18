// tenants.ts — tenant CRUD
// Contracts: admin-endpoints.json (Go proxy)

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const w = window as any;

AppRegistry.register('tenants', {
  state: {
    tenants: [],
    selectedTenant: '',
    showNewTenantForm: false,
    newTenant: { tenant_id: '', driver: 'sqlite3', dsn: '' },
    newTenantUploadFile: null,
    creating: false,
    createResult: null,
  },

  methods() {
    return {
      async loadTenants(this: any) {
        try {
          const resp = await w.Alpine.store('api').get('/api/tenants');
          this.tenants = resp.tenants || [];
        } catch { /* error handled in apiClient */ }
      },

      selectTenant(this: any, id: string) {
        this.selectedTenant = id;
        w.Alpine.store('ui').page = 'config';
        w.Alpine.store('events').emit('tenant:selected', { id });
      },

      async deleteTenant(this: any, id: string) {
        if (!confirm(__('confirm.deleteTenant') + ' "' + id + '"?')) return;
        try {
          await w.Alpine.store('api').del('/api/tenants/' + id);
          await this.loadTenants();
          if (this.selectedTenant === id) {
            this.selectedTenant = '';
            this.config = {};
            this.pendingTools = null;
          }
          w.Alpine.store('notify').success('Tenant "' + id + '" deleted');
          w.Alpine.store('events').emit('tenant:deleted', { id });
        } catch (e: unknown) {
          w.Alpine.store('notify').error(e instanceof Error ? e.message : String(e));
        }
      },

      async createTenantWithUpload(this: any) {
        this.creating = true;
        this.createResult = null;
        try {
          if (this.newTenant.driver === 'sqlite3' && this.newTenantUploadFile) {
            const fd = new FormData();
            fd.append('file', this.newTenantUploadFile);
            fd.append('tenant_id', this.newTenant.tenant_id);
            fd.append('driver', 'sqlite3');

            const token = localStorage.getItem('admin_token');
            const headers: Record<string, string> = {};
            if (token) headers['Authorization'] = 'Bearer ' + token;

            const res = await fetch('/api/tenants/upload-sqlite', { method: 'POST', headers, body: fd });
            this.createResult = await res.json();
            if (!res.ok) {
              const cr = this.createResult as Record<string, unknown>;
              this.createResult = { error: cr.message || cr.error || res.statusText };
            }
          } else {
            this.createResult = await w.Alpine.store('api').post('/api/tenants', this.newTenant);
          }
          this.showNewTenantForm = false;
          this.newTenant = { tenant_id: '', driver: 'sqlite3', dsn: '' };
          this.newTenantUploadFile = null;
          await this.loadTenants();
          await this.refreshDashboard();
        } catch (e: unknown) {
          this.createResult = { error: e instanceof Error ? e.message : String(e) };
        } finally {
          this.creating = false;
        }
      },
    };
  },
});

export {};
