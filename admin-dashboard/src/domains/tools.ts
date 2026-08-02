// tools.ts — MCP tools manifest
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const w = window as any;

AppRegistry.register('tools', {
  state: {
    manifest: null,
  },

  methods() {
    const api = () => w.Alpine.store('api');
    const notify = () => w.Alpine.store('notify');

    return {
      async loadManifest(this: any, tenantId: string) {
        if (!tenantId) return;
        try { this.manifest = await api().get('/api/tenants/' + tenantId + '/manifest'); }
        catch { this.manifest = null; }
      },

      findEndpoint(this: any, endpointPath: string) {
        const eps = this.manifest?.endpoints;
        if (!eps) return null;
        for (const ep of eps) { if (ep.path === endpointPath) return ep; }
        return null;
      },

      refreshManifest(this: any) {
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
