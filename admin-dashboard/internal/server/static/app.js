function dashboard() {
  return {
    // ── Auth ──
    tokenSet: !!localStorage.getItem('admin_token'),
    tokenInput: '',

    // ── Navigation ──
    page: 'dashboard',
    loading: false,
    error: '',

    // ── Dashboard ──
    dashboard: {},

    // ── Tenants ──
    tenants: [],
    selectedTenant: '',
    showNewTenantForm: false,
    newTenant: { tenant_id: '', driver: 'sqlite3', dsn: '' },
    newTenantUploadFile: null,
    creating: false,
    createResult: null,

    // ── Config ──
    config: {},
    manifest: null,
    configDirty: false,
    saveIndicator: '',          // 'readonly' | 'config' | ''
    saveIndicatorText: '',
    saving: false,
    introspecting: false,

    // ── Tools ──
    pendingTools: null,

    // ── RAG ──
    ragHealth: {},
    ragHealthData: null,
    ragDocs: [],
    ragDocsCount: 0,
    ragImport: { title: '', discipline_id: '' },
    ragUploadFile: null,
    ragImporting: false,
    ragImportResult: null,

    // ── Data Service URL (for sidebar footer) ──
    dataService: '',

    // ── Agents ──
    agents: [],
    availableTenants: [],
    showNewAgentForm: false,
    newAgent: { name: '', description: '', tenant_ids_selected: [] },
    editingAgent: false,
    editAgentData: { name: '', description: '', tenant_ids: [] },
    creatingAgent: false,
    savingAgent: false,
    agentCreateResult: null,

    // ═══════════════════════════════════════════
    //  INIT
    // ═══════════════════════════════════════════
    init() {
      if (!this.tokenSet) return;
      this.refreshDashboard();
      this.loadTenants();
      this.refreshRag();
    },

    // ═══════════════════════════════════════════
    //  AUTH
    // ═══════════════════════════════════════════
    login() {
      const token = this.tokenInput.trim();
      if (!token) {
        this.error = 'Введите токен доступа';
        return;
      }
      localStorage.setItem('admin_token', token);
      this.tokenSet = true;
      this.error = '';
      this.init();
    },

    logout() {
      localStorage.removeItem('admin_token');
      location.reload();
    },

    // ═══════════════════════════════════════════
    //  API HELPER
    // ═══════════════════════════════════════════
    async api(url, options = {}) {
      const headers = { 'Content-Type': 'application/json' };
      const token = localStorage.getItem('admin_token');
      if (token) {
        headers['Authorization'] = 'Bearer ' + token;
      }

      try {
        const res = await fetch(url, { ...options, headers });

        if (res.status === 401) {
          this.error = 'Ошибка авторизации. Проверьте ADMIN_TOKEN.';
          throw new Error('Unauthorized');
        }

        // Try to parse JSON body; fall back to text for empty/error responses
        let body;
        const contentType = res.headers.get('content-type') || '';
        if (contentType.includes('application/json')) {
          body = await res.json();
        } else {
          const text = await res.text();
          body = text ? { error: text } : {};
        }

        if (!res.ok) {
          const msg = body.message || body.error || res.statusText;
          this.error = msg;
          throw new Error(msg);
        }

        return body;
      } catch (e) {
        if (e.message !== 'Unauthorized' && e.message !== 'AbortError') {
          // Don't double-set error if already set by 401/!ok handling
          if (!this.error) this.error = e.message || 'Network error';
        }
        throw e;
      }
    },

    // ═══════════════════════════════════════════
    //  DASHBOARD
    // ═══════════════════════════════════════════
    async refreshDashboard() {
      this.loading = true;
      this.error = '';
      try {
        this.dashboard = await this.api('/api/dashboard');
        this.dataService = this.dashboard.data_service || '';
      } catch (e) {
        // error already set in api()
      } finally {
        this.loading = false;
      }
    },

    // ═══════════════════════════════════════════
    //  TENANTS
    // ═══════════════════════════════════════════
    async loadTenants() {
      this.error = '';
      try {
        const resp = await this.api('/api/tenants');
        this.tenants = resp.tenants || [];
      } catch (e) {
        // error already set
      }
    },

    selectTenant(id) {
      this.selectedTenant = id;
      this.page = 'config';
      this.refreshConfig();
      this.refreshPendingTools();
      this.loadManifest();
    },

    async createTenantWithUpload() {
      this.creating = true;
      this.error = '';
      this.createResult = null;
      try {
        if (this.newTenant.driver === 'sqlite3' && this.newTenantUploadFile) {
          // SQLite: upload file via multipart, then data-service saves and uses path as DSN
          const fd = new FormData();
          fd.append('file', this.newTenantUploadFile);
          fd.append('tenant_id', this.newTenant.tenant_id);
          fd.append('driver', 'sqlite3');

          const token = localStorage.getItem('admin_token');
          const headers = {};
          if (token) headers['Authorization'] = 'Bearer ' + token;

          const res = await fetch('/api/tenants/upload-sqlite', {
            method: 'POST',
            headers,
            body: fd,
          });
          this.createResult = await res.json();
          if (!res.ok) {
            this.createResult = { error: this.createResult.message || this.createResult.error || res.statusText };
          }
        } else {
          // PostgreSQL: JSON with DSN
          this.createResult = await this.api('/api/tenants', {
            method: 'POST',
            body: JSON.stringify(this.newTenant),
          });
        }
        this.showNewTenantForm = false;
        this.newTenant = { tenant_id: '', driver: 'sqlite3', dsn: '' };
        this.newTenantUploadFile = null;
        await this.loadTenants();
        await this.refreshDashboard();
      } catch (e) {
        this.createResult = { error: e.message };
      } finally {
        this.creating = false;
      }
    },

    async deleteTenant(id) {
      if (!confirm(`Удалить тенант "${id}"?`)) return;
      this.error = '';
      try {
        await this.api(`/api/tenants/${id}`, { method: 'DELETE' });
        await this.loadTenants();
        if (this.selectedTenant === id) {
          this.selectedTenant = '';
          this.config = {};
          this.pendingTools = null;
        }
      } catch (e) {
        // error already set
      }
    },

    // ═══════════════════════════════════════════
    //  CONFIG
    // ═══════════════════════════════════════════
    async refreshConfig() {
      this.loading = true;
      this.error = '';
      try {
        this.config = await this.api('/api/tenants/' + this.selectedTenant + '/config');
      } catch (e) {
        this.config = {};
      } finally {
        this.loading = false;
      }
    },

    toggleReadOnly(val) {
      if (!this.config.data_source) {
        this.config.data_source = { read_only: true };
      }
      this.config.data_source.read_only = val;
      this.configDirty = true;
      this.autoSaveConfig('readonly');
    },

    autoSaveConfig(label) {
      this.saveIndicator = label;
      this.saveIndicatorText = '⏳ Saving...';
      this.saveConfig().then(() => {
        this.saveIndicatorText = '✅ Saved';
        this.configDirty = false;
        setTimeout(() => { this.saveIndicator = ''; }, 2000);
      }).catch(() => {
        this.saveIndicatorText = '❌ Failed';
        setTimeout(() => { this.saveIndicator = ''; }, 3000);
      });
    },

    async saveConfig() {
      this.saving = true;
      this.error = '';
      try {
        const result = await this.api('/api/tenants/' + this.selectedTenant + '/config', {
          method: 'PUT',
          body: JSON.stringify(this.config),
        });
        this.error = '';
        return result;
      } catch (e) {
        throw e;
      } finally {
        this.saving = false;
      }
    },

    async introspectTenant() {
      this.introspecting = true;
      this.error = '';
      try {
        this.config = await this.api('/api/tenants/' + this.selectedTenant + '/introspect', {
          method: 'POST',
        });
        alert('Схема пересканирована!');
      } catch (e) {
        // error already set
      } finally {
        this.introspecting = false;
      }
    },

    // ═══════════════════════════════════════════
    //  TOOLS
    // ═══════════════════════════════════════════
    async refreshPendingTools() {
      this.error = '';
      try {
        this.pendingTools = await this.api('/api/tools/pending');
      } catch (e) {
        this.pendingTools = null;
      }
    },

    async approveTool(toolName) {
      this.error = '';
      try {
        await this.api(`/api/tools/${toolName}/approve`, { method: 'POST' });
        await this.refreshPendingTools();
      } catch (e) {
        // error already set
      }
    },

    async loadManifest() {
      if (!this.selectedTenant) return;
      this.error = '';
      try {
        this.manifest = await this.api('/api/tenants/' + this.selectedTenant + '/manifest');
      } catch (e) {
        this.manifest = null;
      }
    },

    findEndpoint(endpointPath) {
      if (!this.manifest?.endpoints) return null;
      return this.manifest.endpoints.find(function(ep) {
        return ep.path === endpointPath;
      });
    },

    // ═══════════════════════════════════════════
    //  RAG
    // ═══════════════════════════════════════════
    async refreshRag() {
      this.error = '';
      try {
        this.ragHealth = await this.api('/api/rag/health');
      } catch (e) {
        this.ragHealth = { status: 'error', error: e.message };
      }
      try {
        const docsResp = await this.api('/api/rag/documents/list', {
          method: 'POST',
          body: JSON.stringify({ limit: 100 }),
        });
        this.ragDocs = docsResp.documents || [];
        this.ragDocsCount = docsResp.count ?? this.ragDocs.length;
      } catch (e) {
        this.ragDocs = [];
        this.ragDocsCount = 0;
      }
    },

    ragDropFile(event) {
      const file = event.dataTransfer?.files?.[0];
      if (file) this.ragUploadFile = file;
    },

    async uploadRagDoc() {
      if (!this.ragUploadFile) return;
      this.ragImporting = true;
      this.error = '';
      this.ragImportResult = null;
      try {
        const fd = new FormData();
        fd.append('file', this.ragUploadFile);
        if (this.ragImport.title) fd.append('title', this.ragImport.title);
        if (this.ragImport.discipline_id) fd.append('discipline_id', this.ragImport.discipline_id);

        const token = localStorage.getItem('admin_token');
        const headers = {};
        if (token) headers['Authorization'] = 'Bearer ' + token;

        const res = await fetch('/api/rag/documents/upload', {
          method: 'POST',
          headers,
          body: fd,
        });
        const result = await res.json();
        if (!res.ok) {
          this.ragImportResult = { error: result.message || result.error || res.statusText };
        } else {
          this.ragImportResult = result;
          this.ragUploadFile = null;
          this.ragImport = { title: '', discipline_id: '' };
          await this.refreshRag();
        }
      } catch (e) {
        this.ragImportResult = { error: e.message };
      } finally {
        this.ragImporting = false;
      }
    },

    async deleteRagDoc(doc) {
      const docId = doc.id || doc.document_id;
      const docPath = doc.source_path || doc.path;
      if (!confirm(`Удалить документ "${doc.title || docId}"?`)) return;
      this.error = '';
      try {
        const body = docId ? { document_id: docId } : { path: docPath };
        await this.api('/api/rag/documents/delete', {
          method: 'POST',
          body: JSON.stringify(body),
        });
        await this.refreshRag();
      } catch (e) {
        // error already set
      }
    },

    // ═══════════════════════════════════════════
    //  AGENTS
    // ═══════════════════════════════════════════
    openNewAgentModal() {
      this.showNewAgentForm = true;
      this.editingAgent = false;
      this.newAgent = { name: '', description: '', tenant_ids_selected: [] };
      this.agentCreateResult = null;
      this.loadAgents();
    },

    async loadAgents() {
      this.error = '';
      try {
        const resp = await this.api('/api/agents');
        this.agents = resp.agents || [];
      } catch (e) {
        this.agents = [];
      }
      // Also load tenants list for the creation form
      try {
        const tResp = await this.api('/api/tenants');
        this.availableTenants = tResp.tenants || [];
      } catch (e) {
        // ignore
      }
    },

    async createAgent() {
      this.creatingAgent = true;
      this.error = '';
      this.agentCreateResult = null;
      try {
        const body = {
          name: this.newAgent.name,
          description: this.newAgent.description,
          tenant_ids: this.newAgent.tenant_ids_selected || [],
        };
        this.agentCreateResult = await this.api('/api/agents', {
          method: 'POST',
          body: JSON.stringify(body),
        });
        this.showNewAgentForm = false;
        this.newAgent = { name: '', description: '', tenant_ids_selected: [] };
        await this.loadAgents();
      } catch (e) {
        this.agentCreateResult = { error: e.message };
      } finally {
        this.creatingAgent = false;
      }
    },

    editAgent(agent) {
      this.editAgentData = {
        name: agent.name,
        description: agent.description || '',
        tenant_ids: [...(agent.tenant_ids || [])],
      };
      this.editingAgent = true;
    },

    async updateAgent() {
      this.savingAgent = true;
      this.error = '';
      try {
        await this.api('/api/agents/' + this.editAgentData.name, {
          method: 'PUT',
          body: JSON.stringify({
            description: this.editAgentData.description,
            tenant_ids: this.editAgentData.tenant_ids,
          }),
        });
        this.editingAgent = false;
        await this.loadAgents();
      } catch (e) {
        // error already set
      } finally {
        this.savingAgent = false;
      }
    },

    async deleteAgent(name) {
      if (!confirm(`Удалить агента "${name}"?`)) return;
      this.error = '';
      try {
        await this.api('/api/agents/' + name, { method: 'DELETE' });
        await this.loadAgents();
      } catch (e) {
        // error already set
      }
    },
  };
}
