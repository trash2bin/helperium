// config.ts — tenant config view/edit/rewrite
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const w = window as any;

const defaultSkipRules: { prefix: string; reason: string }[] = [
  { prefix: 'sqlite_', reason: 'SQLite: internal schema tables' },
  { prefix: 'pg_', reason: 'PostgreSQL: system catalogs' },
  { prefix: 'pg_catalog', reason: 'PostgreSQL: system catalog schema' },
  { prefix: 'information_schema', reason: 'SQL standard: system views' },
  { prefix: 'auth_', reason: 'Django: built-in auth tables' },
  { prefix: 'django_', reason: 'Django: framework metadata' },
  { prefix: 'session', reason: 'Django: session storage' },
  { prefix: 'documents', reason: 'Helperium RAG: internal chunks' },
  { prefix: 'migrations', reason: 'Laravel: migration tracking' },
  { prefix: 'jobs', reason: 'Laravel: queue jobs' },
  { prefix: 'failed_jobs', reason: 'Laravel: queue failures' },
  { prefix: 'schema_migrations', reason: 'Rails: migration versions' },
  { prefix: 'ar_internal_metadata', reason: 'Rails: internal metadata' },
];

AppRegistry.register('config', {
  state: {
    config: {},
    computed: {},
    configDirty: false,
    configgenTab: 'skip',
    savingDisplayNames: false,
    saveIndicator: '',
    saveIndicatorText: '',
    saving: false,
    introspecting: false,
    _configgenTimer: null,
    defaultSkipRules,
    disabledDefaultRules: [],
    _configSeq: 0,
    selectedTenant: '',
  },

  methods() {
    const computeSummary = (cfg: any) => ({
      driver: cfg.data_source?.driver || '\u2014',
      readonly: cfg.data_source ? cfg.data_source.read_only !== false : true,
      poolSize: cfg.data_source?.pool_size || '\u2014',
      entities: cfg.entities?.length || 0,
      endpoints: cfg.endpoints?.length || 0,
      mcpTools: cfg.mcp_tools?.length || 0,
      customQueries: cfg.custom_queries ? Object.keys(cfg.custom_queries).length : 0,
      skipRules: cfg.skip_rules?.length || 0,
      displayPrefixes: cfg.display_prefixes?.join(', ') || '\u2014',
      customPlurals: cfg.custom_plurals ? Object.keys(cfg.custom_plurals).length : 0,
      approvedTools: cfg.approved_tools?.length || 0,
    });

    return {
      async refreshConfig(this: any, tenantId?: string) {
        const id = tenantId || this.selectedTenant;
        if (!id) return;
        const seq = ++this._configSeq;
        this.config = {};
        this.computed = {};
        try {
          const data = await w.Alpine.store('api').get('/api/tenants/' + id + '/config');
          if (seq !== this._configSeq) return;
          this.config = data;
          this.computed = computeSummary(data);
          this.disabledDefaultRules = data.disabled_default_rules || [];
        } catch (e: unknown) {
          if (seq !== this._configSeq) return;
          w.Alpine.store('notify').error(e instanceof Error ? e.message : String(e));
        }
      },

      _configgenChanged(this: any) {
        this.configDirty = true;
        if (this._configgenTimer) clearTimeout(this._configgenTimer);
        this._configgenTimer = setTimeout(async () => {
          this._cleanConfiggen();
          try {
            await this.saveConfig();
            this.computed = computeSummary(this.config);
            this.configDirty = false;
          } catch (err: unknown) {
            w.Alpine.store('notify').error(err instanceof Error ? err.message : String(err));
            this.configDirty = false;
          }
        }, 800);
      },

      _cleanConfiggen(this: any) {
        const cfg = this.config;
        if (cfg.skip_rules) {
          cfg.skip_rules = cfg.skip_rules.filter((r: any) => r?.prefix || r?.suffix || r?.contains);
          if (cfg.skip_rules.length === 0) delete cfg.skip_rules;
        }
        if (cfg.display_prefixes) {
          cfg.display_prefixes = cfg.display_prefixes.filter((p: string) => p?.trim());
        }
        if (cfg.custom_plurals) {
          const cleaned: Record<string, string> = {};
          let has = false;
          for (const [k, v] of Object.entries(cfg.custom_plurals)) {
            if (k && v) { cleaned[k] = v as string; has = true; }
          }
          if (has) cfg.custom_plurals = cleaned; else delete cfg.custom_plurals;
        }
      },

      addSkipRule(this: any) {
        if (!this.config.skip_rules) this.config.skip_rules = [];
        this.config.skip_rules.push({ prefix: '', suffix: '', contains: '', reason: '' });
        this._configgenChanged();
      },

      removeSkipRule(this: any, index: number) {
        this.config.skip_rules?.splice(index, 1);
        this._configgenChanged();
      },

      addCustomPlural(this: any) {
        if (!this.config.custom_plurals) this.config.custom_plurals = {};
        this.config.custom_plurals['new_' + Date.now()] = '';
        this._configgenChanged();
      },

      removeCustomPlural(this: any, key: string) {
        if (this.config.custom_plurals) { delete this.config.custom_plurals[key]; this._configgenChanged(); }
      },

      hideEntity(this: any, index: number) {
        const entity = this.config.entities?.[index];
        if (!entity) return;
        const name = entity.name;
        const tableName = entity.table_name || name;
        if (!this.config.skip_rules) this.config.skip_rules = [];
        this.config.skip_rules.push({ prefix: '', suffix: '', contains: tableName, reason: '\u0421\u043a\u0440\u044b\u0442\u0430 \u0438\u0437 UI' });
        if (this.config.endpoints) this.config.endpoints = this.config.endpoints.filter((ep: any) => ep.entity !== name);
        if (this.config.mcp_tools) this.config.mcp_tools = this.config.mcp_tools.filter((tool: any) => !tool.name.endsWith('_' + name));
        if (this.config.stats?.counters) this.config.stats.counters = this.config.stats.counters.filter((c: any) => c.entity !== name);
        this.config.entities!.splice(index, 1);
        this._configgenChanged();
      },

      addDisplayPrefix(this: any) {
        if (!this.config.display_prefixes) this.config.display_prefixes = [];
        this.config.display_prefixes.push('');
        this._configgenChanged();
      },

      removeDisplayPrefix(this: any, index: number) {
        this.config.display_prefixes?.splice(index, 1);
        this._configgenChanged();
      },

      autoSaveConfig(this: any, label: string) {
        this.saveIndicator = label;
        this.saveIndicatorText = __('msg.saving');
        this.saveConfig()
          .then(() => { this.saveIndicatorText = __('msg.saved'); return this.refreshConfig(); })
          .then(() => { this.configDirty = false; setTimeout(() => { this.saveIndicator = ''; }, 2000); })
          .catch(() => { this.saveIndicatorText = __('msg.failed'); setTimeout(() => { this.saveIndicator = ''; }, 3000); });
      },

      async saveConfig(this: any, tenantId?: string) {
        const id = tenantId || this.selectedTenant;
        if (!id) return;
        this.saving = true;
        try {
          const result = await w.Alpine.store('api').put('/api/tenants/' + id + '/config', this.config);
          w.Alpine.store('events').emit('config:saved', { id });
          return result;
        } finally { this.saving = false; }
      },

      async introspectTenant(this: any, tenantId?: string) {
        const id = tenantId || this.selectedTenant;
        if (!id) return;
        this.introspecting = true;
        try {
          await w.Alpine.store('api').post('/api/tenants/' + id + '/introspect');
          await this.refreshConfig(id);
          w.Alpine.store('notify').success(__('msg.introspected'));
        } catch (e: unknown) {
          w.Alpine.store('notify').error(e instanceof Error ? e.message : String(e));
        } finally { this.introspecting = false; }
      },

      _stripPrefix(this: any, name: string): string {
        if (!name) return '';
        const prefixes = this.config.display_prefixes?.filter(Boolean) || [];
        const effective = prefixes.length > 0 ? prefixes : ['catalog_', 'auth_', 'django_'];
        for (const prefix of effective) {
          if (name.startsWith(prefix)) {
            const result = name.slice(prefix.length);
            return result ? result.charAt(0).toUpperCase() + result.slice(1) : name;
          }
        }
        return name.charAt(0).toUpperCase() + name.slice(1);
      },

      toggleDefaultRule(this: any, prefix: string) {
        const idx = this.disabledDefaultRules.indexOf(prefix);
        if (idx >= 0) this.disabledDefaultRules.splice(idx, 1);
        else this.disabledDefaultRules.push(prefix);
        this.config.disabled_default_rules = this.disabledDefaultRules;
        this._configgenChanged();
      },

      isDefaultRuleDisabled(this: any, prefix: string) { return this.disabledDefaultRules.indexOf(prefix) >= 0; },

      toggleReadOnly(this: any, readonly: boolean) {
        if (!this.config.data_source) this.config.data_source = {};
        this.config.data_source.read_only = readonly;
        this.autoSaveConfig('readonly');
      },
    };
  },
});

export {};
