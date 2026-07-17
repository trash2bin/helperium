// Audit Log domain — config change history
// window.Audit = { state, methods }

window.Audit = {
  state: {
    auditEntries: [],
    auditLimit: 100,
    auditLoading: false,
    auditError: '',
  },

  methods: function () {
    return {
      // ── Load ──
      loadAudit: async function () {
        this.auditLoading = true;
        this.auditError = '';
        try {
          var resp = await Alpine.store('api').get('/api/audit?limit=' + this.auditLimit);
          this.auditEntries = resp.entries || [];
        } catch (e) {
          this.auditError = e.message || 'Failed to load audit log';
          Alpine.store('notify').error(this.auditError);
        } finally {
          this.auditLoading = false;
        }
      },

      // ── Format helpers ──
      auditTimestamp: function (ts) {
        if (!ts) return '';
        try {
          var d = new Date(ts);
          return d.toLocaleString();
        } catch (e) {
          return ts;
        }
      },

      auditActionLabel: function (action) {
        // Map internal action names to i18n-friendly labels
        var labels = {
          'tenant.create': '🆕 ' + this.__('audit.tenantCreate'),
          'tenant.delete': '🗑️ ' + this.__('audit.tenantDelete'),
          'tenant.introspect': '🔍 ' + this.__('audit.tenantIntrospect'),
          'tenant.upload': '📤 ' + this.__('audit.tenantUpload'),
          'config.update': '⚙️ ' + this.__('audit.configUpdate'),
          'tool.approve': '✅ ' + this.__('audit.toolApprove'),
          'rag.config.update': '📄 ' + this.__('audit.ragConfigUpdate'),
          'rag.doc.import': '📥 ' + this.__('audit.ragDocImport'),
          'rag.doc.upload': '📤 ' + this.__('audit.ragDocUpload'),
          'rag.doc.delete': '🗑️ ' + this.__('audit.ragDocDelete'),
          'agent.create': '🤖 ' + this.__('audit.agentCreate'),
          'agent.update': '🤖 ' + this.__('audit.agentUpdate'),
          'agent.delete': '🗑️ ' + this.__('audit.agentDelete'),
          'agent.abuse.update': '🛡️ ' + this.__('audit.agentAbuseUpdate'),
          'llm-provider.add': '🧠 ' + this.__('audit.llmProviderAdd'),
          'llm-provider.update': '🧠 ' + this.__('audit.llmProviderUpdate'),
          'llm-provider.delete': '🗑️ ' + this.__('audit.llmProviderDelete'),
          'llm-provider.toggle': '🔁 ' + this.__('audit.llmProviderToggle'),
          'voice-config.update': '🎤 ' + this.__('audit.voiceConfigUpdate'),
          'abuse-settings.update': '🛡️ ' + this.__('audit.abuseSettingsUpdate'),
          'abuse-preset.set': '🚨 ' + this.__('audit.abusePresetSet'),
          'abuse-config.reload': '🔄 ' + this.__('audit.abuseConfigReload'),
          'db.test': '🔌 ' + this.__('audit.dbTest'),
        };
        return labels[action] || action;
      },
    };
  },
};
