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
    savingDisplayNames: false,
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
    newAgent: { name: '', description: '', tenant_ids_selected: [], provider_priority: [] },
    editingAgent: false,
    editAgentData: { name: '', description: '', tenant_ids: [], provider_priority: [] },
    llmProviderStoreList: [],
    creatingAgent: false,
    savingAgent: false,
    agentCreateResult: null,

    // ── Anti-Abuse ──
    abuseTab: 'global', // 'global' | 'agent'
    abuseGlobal: { rps: null, burst: null, max_message_length: null, min_interval_ms: null, max_messages_per_session: null, token_budget: null, block_empty_user_agent: null, blocked_user_agents: [], _ua_text: '', history_turns: null, history_content_chars: null, max_iterations: null, max_empty_rounds: null, max_turn_tokens: null, session_ttl_hours: null },
    abuseAgent: null,
    abuseAgentName: '',
    abuseAgentOverrides: {},
    abuseSaving: false,
    abuseSaveMsg: '',
    abuseReloading: false,
    abuseReloadMsg: '',
    abuseAgentList: [],

    // ── Emergency Panel ──
    emergencyStatus: { rps: null, burst: null, token_budget: null, max_messages: null, min_interval_ms: null },
    emergencyActive: false,
    emergencyCurrentPreset: 'normal',
    emergencyApplying: false,
    emergencyTimer: null,
    emergencyConflicting: false,

    // ── LLM Provider Fallback ──
    llmConfig: null,
    llmError: '',

    // ═══════════════════════════════════���═══════
    //  I18N
    // ═══════════════════════════════════════════
    __(key) {
      if (typeof window.__ === 'function') {
        return window.__(key);
      }
      return key;
    },

    // ═══════════════════════════════════════════
    //  LLM FALLBACK METHODS
    // ════════════════════════��══════════════════
    async loadLlmConfig() {
      this.llmError = '';
      try {
        this.llmConfig = await this.api('/api/llm-config');
        await this.loadLlmProviderList();
      } catch (e) {
        this.llmError = e.message || this.__('llm.loadError');
        this.llmConfig = null;
      }
    },

    get hasFallbackProvider() {
      return this.llmConfig && this.llmConfig.num_models > 0;
    },

    // ── Provider CRUD ──
    llmTab: 'list',
    llmNew: { name: '', model: '', api_key: '', api_base: '', enabled: true, provider: '' },
    llmEdit: null,
    llmEditName: '',
    llmProviderList: null,
    llmSaving: false,
    llmSaveMsg: '',
    llmDeleteConfirm: '',

    async loadLlmProviderList() {
      try {
        const res = await this.api('/api/llm-provider-list');
        this.llmProviderList = res.providers || [];
      } catch {
        this.llmProviderList = [];
      }
    },

    async loadLlmProviders() {
      await this.loadLlmConfig();
    },

    async addLlmProvider() {
      this.llmSaving = true;
      this.llmSaveMsg = '';
      try {
        const body = {
          name: this.llmNew.name,
          model: this.llmNew.model,
          provider: this.llmNew.provider || undefined,
          api_key: this.llmNew.api_key || undefined,
          api_base: this.llmNew.api_base || undefined,
          enabled: this.llmNew.enabled,
        };
        await this.api('/api/llm-providers', { method: 'POST', body: JSON.stringify(body) });
        this.llmNew = { name: '', model: '', api_key: '', api_base: '', enabled: true, provider: '' };
        this.llmSaveMsg = this.__('msg.saved');
        setTimeout(() => { this.llmSaveMsg = ''; }, 3000);
        await this.loadLlmConfig();
      } catch (e) {
        this.llmSaveMsg = this.__('msg.failed') + ': ' + e.message;
      } finally {
        this.llmSaving = false;
      }
    },

    startEditLlmProvider(name) {
      const p = this.llmConfig?.providers?.find(x => x.name === name);
      if (!p) return;
      this.llmEditName = name;
      this.llmEdit = {
        model: p.model,
        api_key: '',
        api_base: p.api_base || '',
        enabled: p.enabled,
        has_api_key: p.has_api_key,
        api_key_masked: p.api_key_masked,
      };
      this.llmTab = 'edit';
    },

    async saveLlmProvider() {
      if (!this.llmEditName) return;
      this.llmSaving = true;
      this.llmSaveMsg = '';
      try {
        const body = { model: this.llmEdit.model, api_base: this.llmEdit.api_base || undefined, enabled: this.llmEdit.enabled };
        if (this.llmEdit.api_key && this.llmEdit.api_key.trim()) {
          body.api_key = this.llmEdit.api_key.trim();
        } else if (this.llmEdit.api_key === '') {
          body.api_key = '';
        }
        await this.api('/api/llm-providers/' + this.llmEditName, { method: 'PUT', body: JSON.stringify(body) });
        this.llmSaveMsg = this.__('msg.saved');
        setTimeout(() => { this.llmSaveMsg = ''; }, 3000);
        await this.loadLlmConfig();
      } catch (e) {
        this.llmSaveMsg = this.__('msg.failed') + ': ' + e.message;
      } finally {
        this.llmSaving = false;
      }
    },

    async deleteLlmProvider(name) {
      if (!confirm(this.__('llm.deleteConfirmMsg') + ' "' + name + '"?')) return;
      try {
        await this.api('/api/llm-providers/' + name, { method: 'DELETE' });
        await this.loadLlmConfig();
      } catch (e) {
        this.llmError = e.message;
      }
    },

    async toggleLlmProvider(name) {
      try {
        await this.api('/api/llm-providers/' + name + '/toggle', { method: 'POST' });
        await this.loadLlmConfig();
      } catch (e) {
        this.llmError = e.message;
      }
    },

    cancelLlmEdit() {
      this.llmTab = 'list';
      this.llmEdit = null;
      this.llmEditName = '';
    },

    // ═══════════════════════════════════════════
    //  ANTI-ABUSE METHODS
    // ═══════════════════════════════════════════
    async loadAbuseSettings() {
      this.error = '';
      try {
        const cfg = await this.api('/api/abuse-settings');
        cfg._ua_text = (cfg.blocked_user_agents || []).join('\n');
        this.abuseGlobal = cfg;
        // Also load emergency status
        await this.loadEmergencyStatus();
      } catch (e) {
        // error already set
      }
    },

    async saveAbuseGlobal() {
      this.abuseSaving = true;
      this.error = '';
      this.abuseSaveMsg = '';
      try {
        await this.api('/api/abuse-settings', {
          method: 'PUT',
          body: JSON.stringify(this.abuseGlobal),
        });
        this.abuseSaveMsg = this.__('abuse.saveMsgGlobal');
        setTimeout(() => { this.abuseSaveMsg = ''; }, 3000);
      } catch (e) {
        // error already set
      } finally {
        this.abuseSaving = false;
      }
    },

    async reloadAbuseOnApi() {
      this.abuseReloading = true;
      this.abuseReloadMsg = '';
      try {
        await this.api('/api/admin/abuse-config/reload', { method: 'POST' });
        this.abuseReloadMsg = '✅ Config reloaded on api-service';
        setTimeout(() => { this.abuseReloadMsg = ''; }, 3000);
      } catch (e) {
        this.abuseReloadMsg = '❌ ' + (e.message || 'Reload failed');
      } finally {
        this.abuseReloading = false;
      }
    },

    async selectAbuseAgent(name) {
      this.abuseAgentName = name;
      this.error = '';
      if (!name) {
        this.abuseAgentOverrides = {};
        return;
      }
      try {
        const resp = await this.api('/api/agents/' + name + '/abuse');
        this.abuseAgentOverrides = resp.abuse_config || {};
      } catch (e) {
        this.abuseAgentOverrides = {};
      }
    },

    async saveAbuseAgent() {
      this.abuseSaving = true;
      this.error = '';
      this.abuseSaveMsg = '';
      try {
        await this.api('/api/agents/' + this.abuseAgentName + '/abuse', {
          method: 'PUT',
          body: JSON.stringify(this.abuseAgentOverrides),
        });
        this.abuseSaveMsg = this.__('abuse.saveMsgAgent');
        setTimeout(() => { this.abuseSaveMsg = ''; }, 3000);
      } catch (e) {
        // error already set
      } finally {
        this.abuseSaving = false;
      }
    },

    // ═══════════════════════════════════════════
    //  EMERGENCY METHODS
    // ═══════════════════════════════════════════
    async loadEmergencyStatus() {
      try {
        const status = await this.api('/api/emergency-status');
        this.emergencyStatus = status;
        this.emergencyActive = status.emergency_mode;
        this.emergencyCurrentPreset = status.emergency_preset || 'normal';
      } catch(e) {
        // api() sets this.error
      }
    },

    async applyEmergencyPreset(preset) {
      this.emergencyApplying = true;
      this.error = '';
      try {
        const cfg = await this.api('/api/abuse-preset/' + preset, { method: 'POST' });
        this.emergencyActive = cfg.emergency_mode;
        this.emergencyCurrentPreset = cfg.emergency_preset || preset;
        // Reload global settings so the form reflects new values
        if (this.abuseGlobal) {
          // Update the form fields from the response
          Object.assign(this.abuseGlobal, cfg);
          this.abuseGlobal._ua_text = (cfg.blocked_user_agents || []).join('\n');
        }
        await this.loadEmergencyStatus();
      } catch(e) {
        // error already set
      } finally {
        this.emergencyApplying = false;
      }
    },

    get emergencyPresetClass() {
      if (this.emergencyCurrentPreset === 'lockdown') return 'preset-lockdown';
      if (this.emergencyCurrentPreset === 'cautious') return 'preset-cautious';
      return 'preset-normal';
    },

    get emergencyPresetLabel() {
      if (this.emergencyCurrentPreset === 'lockdown') return this.__('emergency.labelLockdown');
      if (this.emergencyCurrentPreset === 'cautious') return this.__('emergency.labelCautious');
      return this.__('emergency.labelNormal');
    },

    get emergencyPresetDescription() {
      if (this.emergencyCurrentPreset === 'lockdown') return this.__('emergency.descLockdown');
      if (this.emergencyCurrentPreset === 'cautious') return this.__('emergency.descCautious');
      return this.__('emergency.descNormal');
    },

    async toggleEmergencyMode() {
      // Toggle between normal and lockdown
      if (this.emergencyCurrentPreset === 'lockdown') {
        await this.applyEmergencyPreset('normal');
      } else {
        await this.applyEmergencyPreset('lockdown');
      }
    },

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
        this.error = this.__('error.enterToken');
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
          this.error = this.__('error.unauthorizedCheck');
          throw new Error(this.__('error.unauthorized'));
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
          if (!this.error) this.error = e.message || this.__('error.network');
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
      if (!confirm(this.__('confirm.deleteTenant') + ' "' + id + '"?')) return;
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
      this.saveIndicatorText = this.__('msg.saving');
      this.saveConfig().then(() => {
        this.saveIndicatorText = this.__('msg.saved');
        this.configDirty = false;
        setTimeout(() => { this.saveIndicator = ''; }, 2000);
      }).catch(() => {
        this.saveIndicatorText = this.__('msg.failed');
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
        alert(this.__('msg.introspected'));
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
      if (!this.selectedTenant) return;
      try {
        this.pendingTools = await this.api('/api/tenants/' + this.selectedTenant + '/tools/pending');
      } catch (e) {
        this.pendingTools = null;
      }
    },

    async approveTool(toolName) {
      this.error = '';
      if (!this.selectedTenant) return;
      try {
        await this.api(`/api/tenants/${this.selectedTenant}/tools/${toolName}/approve`, { method: 'POST' });
        await this.refreshPendingTools();
      } catch (e) {
        // error already set
      }
    },

    async saveToolDisplayNames() {
      if (!this.selectedTenant || !this.manifest?.mcp_tools) return;
      this.savingDisplayNames = true;
      this.error = '';
      try {
        // Make sure config.mcp_tools exists
        if (!this.config.mcp_tools) {
          this.config.mcp_tools = [];
        }
        // Merge display_name from manifest into config
        for (const manifestTool of this.manifest.mcp_tools) {
          const existing = this.config.mcp_tools.find(t => t.name === manifestTool.name);
          if (existing) {
            existing.display_name = manifestTool.display_name || '';
          } else {
            // Need full tool definition — fetch from manifest
            // Minimal tool def
            this.config.mcp_tools.push({
              name: manifestTool.name,
              endpoint: manifestTool.endpoint,
              description: manifestTool.description,
              params: manifestTool.params || [],
              display_name: manifestTool.display_name || '',
            });
          }
        }
        // Also add any tools that are in config but not in manifest (keeps existing)
        // Save
        await this.saveConfig();
        alert(this.__('tool.displayNamesSaved'));
      } catch (e) {
        // error already set
      } finally {
        this.savingDisplayNames = false;
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
    ragSettings: null,
    ragStats: null,
    ragSettingsLoading: false,
    ragSettingsSaving: false,
    ragSettingsSaveMsg: '',
    ragStatsLoading: false,
    ragTab: 'docs', // 'docs' | 'settings'

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

    async loadRagSettings() {
      this.ragSettingsLoading = true;
      this.ragSettingsSaveMsg = '';
      try {
        this.ragSettings = await this.api('/api/rag/config');
      } catch (e) {
        this.error = e.message;
      } finally {
        this.ragSettingsLoading = false;
      }
    },

    async loadRagStats() {
      this.ragStatsLoading = true;
      try {
        this.ragStats = await this.api('/api/rag/stats');
      } catch (e) {
        this.error = e.message;
      } finally {
        this.ragStatsLoading = false;
      }
    },

    async saveRagSettings() {
      this.ragSettingsSaving = true;
      this.ragSettingsSaveMsg = '';
      try {
        await this.api('/api/rag/config', {
          method: 'PUT',
          body: JSON.stringify(this.ragSettings),
        });
        this.ragSettingsSaveMsg = 'saved';
        setTimeout(() => { this.ragSettingsSaveMsg = ''; }, 3000);
      } catch (e) {
        this.ragSettingsSaveMsg = 'error';
      } finally {
        this.ragSettingsSaving = false;
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
      if (!confirm(this.__('confirm.deleteDocument') + ' "' + (doc.title || docId) + '"?')) return;
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
      this.newAgent = { name: '', description: '', tenant_ids_selected: [], provider_priority: [] };
      this.agentCreateResult = null;
      this.loadLlmProviderStoreList();
      this.loadAgents();
    },

    async loadLlmProviderStoreList() {
      try {
        const resp = await this.api('/api/llm-providers');
        this.llmProviderStoreList = (resp.providers || []).filter(p => p.has_api_key);
      } catch (e) {
        this.llmProviderStoreList = [];
      }
    },

    // ── Provider priority reorder ──
    moveProviderPriority(idx, dir) {
      const arr = [...this.newAgent.provider_priority];
      const target = idx + dir;
      if (target < 0 || target >= arr.length) return;
      [arr[idx], arr[target]] = [arr[target], arr[idx]];
      this.newAgent.provider_priority = arr;
    },
    moveEditProviderPriority(idx, dir) {
      const arr = [...this.editAgentData.provider_priority];
      const target = idx + dir;
      if (target < 0 || target >= arr.length) return;
      [arr[idx], arr[target]] = [arr[target], arr[idx]];
      this.editAgentData.provider_priority = arr;
    },
    toggleProviderPriority(name) {
      const idx = this.newAgent.provider_priority.indexOf(name);
      if (idx >= 0) {
        this.newAgent.provider_priority = this.newAgent.provider_priority.filter(n => n !== name);
      } else {
        this.newAgent.provider_priority = [...this.newAgent.provider_priority, name];
      }
    },
    toggleEditProviderPriority(name) {
      const idx = this.editAgentData.provider_priority.indexOf(name);
      if (idx >= 0) {
        this.editAgentData.provider_priority = this.editAgentData.provider_priority.filter(n => n !== name);
      } else {
        this.editAgentData.provider_priority = [...this.editAgentData.provider_priority, name];
      }
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
          provider_priority: this.newAgent.provider_priority || [],
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
        provider_priority: [...(agent.provider_priority || [])],
      };
      this.loadLlmProviderStoreList();
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
            provider_priority: this.editAgentData.provider_priority || [],
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
      if (!confirm(this.__('confirm.deleteAgent') + ' "' + name + '"?')) return;
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
