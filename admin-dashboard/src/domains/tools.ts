// tools.ts — MCP tools approval & manifest
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const w = window as any;

AppRegistry.register('tools', {
  state: {
    pendingTools: { tools: [], mode: 'read_only' },
    manifest: null,
  },

  methods() {
    const api = () => w.Alpine.store('api');
    const notify = () => w.Alpine.store('notify');

    return {
      async loadPendingTools(this: any, tenantId: string) {
        if (!tenantId) return;
        try {
          this.pendingTools = await api().get('/api/tenants/' + tenantId + '/tools/pending');
        } catch (e: unknown) {
          this.pendingTools = null;
          notify().error(e instanceof Error ? e.message : String(e));
        }
      },

      async loadManifest(this: any, tenantId: string) {
        if (!tenantId) return;
        try { this.manifest = await api().get('/api/tenants/' + tenantId + '/manifest'); }
        catch { this.manifest = null; }
      },

      async approveTool(this: any, tenantId: string, toolName: string) {
        if (!tenantId || !toolName) return;
        try {
          await api().post('/api/tenants/' + tenantId + '/tools/' + toolName + '/approve');
          notify().success('Tool approved: ' + toolName);
          await this.loadPendingTools(tenantId);
        } catch (e: unknown) { notify().error(e instanceof Error ? e.message : String(e)); }
      },

      findEndpoint(this: any, endpointPath: string) {
        const eps = this.manifest?.endpoints;
        if (!eps) return null;
        for (const ep of eps) { if (ep.path === endpointPath) return ep; }
        return null;
      },

      refreshPendingTools(this: any) {
        this.loadPendingTools(this.selectedTenant);
        this.loadManifest(this.selectedTenant);
      },

      async saveToolDisplayNames(this: any, tenantId: string, config: any, saveConfigFn: any) {
        if (!tenantId || !this.manifest?.mcp_tools) return;
        if (!config) config = {};
        if (!config.mcp_tools) config.mcp_tools = [];
        for (const mt of this.manifest.mcp_tools) {
          const found = config.mcp_tools.find((t: any) => t.name === mt.name);
          if (found) found.display_name = mt.display_name || '';
          else config.mcp_tools.push({ name: mt.name, endpoint: mt.endpoint, description: mt.description, params: mt.params || [], display_name: mt.display_name || '' });
        }
        try { await saveConfigFn(config); notify().success('Display names saved'); }
        catch (e: unknown) { notify().error(e instanceof Error ? e.message : String(e)); }
      },
    };
  },
});

export {};
