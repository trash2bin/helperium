// audit.ts — Audit Log
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const w = window as any;

AppRegistry.register('audit', {
  state: {
    auditEntries: [], auditLimit: 100, auditLoading: false, auditError: '',
  },

  methods() {
    const api = () => w.Alpine.store('api');
    const notify = () => w.Alpine.store('notify');

    return {
      async loadAudit(this: any) {
        this.auditLoading = true; this.auditError = '';
        try {
          const resp = await api().get('/api/audit?limit=' + this.auditLimit);
          this.auditEntries = resp.entries || [];
        } catch (e: unknown) {
          this.auditError = (e instanceof Error ? e.message : 'Failed to load audit log') || '';
          notify().error(this.auditError);
        } finally { this.auditLoading = false; }
      },

      auditTimestamp(this: any, ts: any) {
        if (!ts) return '';
        try { return new Date(ts).toLocaleString(); } catch { return ts; }
      },

      auditActionLabel(this: any, action: any) {
        if (!action) return '';
        const labels: Record<string, string> = {
          'tenant.create': '🆕 ' + __('audit.tenantCreate'),
          'tenant.delete': '🗑️ ' + __('audit.tenantDelete'),
          'tenant.introspect': '🔍 ' + __('audit.tenantIntrospect'),
          'tenant.upload': '📤 ' + __('audit.tenantUpload'),
          'config.update': '⚙️ ' + __('audit.configUpdate'),
          'tool.approve': '✅ ' + __('audit.toolApprove'),
          'rag.config.update': '📄 ' + __('audit.ragConfigUpdate'),
          'rag.doc.import': '📥 ' + __('audit.ragDocImport'),
          'rag.doc.upload': '📤 ' + __('audit.ragDocUpload'),
          'rag.doc.delete': '🗑️ ' + __('audit.ragDocDelete'),
          'agent.create': '🤖 ' + __('audit.agentCreate'),
          'agent.update': '🤖 ' + __('audit.agentUpdate'),
          'agent.delete': '🗑️ ' + __('audit.agentDelete'),
          'agent.abuse.update': '🛡️ ' + __('audit.agentAbuseUpdate'),
          'llm-provider.add': '🧠 ' + __('audit.llmProviderAdd'),
          'llm-provider.update': '🧠 ' + __('audit.llmProviderUpdate'),
          'llm-provider.delete': '🗑️ ' + __('audit.llmProviderDelete'),
          'llm-provider.toggle': '🔁 ' + __('audit.llmProviderToggle'),
          'voice-config.update': '🎤 ' + __('audit.voiceConfigUpdate'),
          'abuse-settings.update': '🛡️ ' + __('audit.abuseSettingsUpdate'),
          'abuse-preset.set': '🚨 ' + __('audit.abusePresetSet'),
          'abuse-config.reload': '🔄 ' + __('audit.abuseConfigReload'),
          'db.test': '🔌 ' + __('audit.dbTest'),
        };
        return labels[action] || action;
      },
    };
  },
});

export {};
