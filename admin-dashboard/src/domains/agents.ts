// agents.ts — Agent CRUD + voice overrides
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const w = window as any;

function reorderArray(arr: string[], idx: number, dir: number): string[] {
  const target = idx + dir;
  if (target < 0 || target >= arr.length) return [...arr];
  const result = [...arr];
  [result[idx], result[target]] = [result[target]!, result[idx]!];
  return result;
}

function toggleInArray(arr: string[], name: string): string[] {
  const idx = arr.indexOf(name);
  return idx >= 0 ? arr.filter(n => n !== name) : [...arr, name];
}

AppRegistry.register('agents', {
  state: {
    agents: [], availableTenants: [],
    showNewAgentForm: false,
    newAgent: { name: '', description: '', tenant_ids_selected: [] as string[], provider_priority: [] as string[], system_prompt: '' },
    editingAgent: false,
    editAgentData: {
      name: '', description: '', tenant_ids: [] as string[], provider_priority: [] as string[], system_prompt: '',
      voice_config_enabled: true, voice_input_disabled: false, voice_output_disabled: false,
      voice_stt_provider: '', voice_tts_provider: '',
      _sttProviders: [] as any[], _ttsProviders: [] as any[],
    },
    llmProviderStoreList: [] as any[],
    creatingAgent: false, savingAgent: false, agentCreateResult: null,
  },

  methods() {
    const api = () => w.Alpine.store('api');
    const notify = () => w.Alpine.store('notify');
    const events = () => w.Alpine.store('events');

    return {
      openNewAgentModal(this: any) {
        this.showNewAgentForm = true; this.editingAgent = false;
        this.newAgent = { name: '', description: '', tenant_ids_selected: [], provider_priority: [], system_prompt: '' };
        this.agentCreateResult = null;
        this.loadLlmProviderStoreList(); this.loadAgents();
      },

      async loadLlmProviderStoreList(this: any) {
        try { const r = await api().get('/api/llm-providers'); this.llmProviderStoreList = r.providers || []; }
        catch { this.llmProviderStoreList = []; }
      },

      moveProviderPriority(this: any, idx: number, dir: number) { this.newAgent.provider_priority = reorderArray(this.newAgent.provider_priority, idx, dir); },
      moveEditProviderPriority(this: any, idx: number, dir: number) { this.editAgentData.provider_priority = reorderArray(this.editAgentData.provider_priority, idx, dir); },
      toggleProviderPriority(this: any, name: string) { this.newAgent.provider_priority = toggleInArray(this.newAgent.provider_priority, name); },
      toggleEditProviderPriority(this: any, name: string) { this.editAgentData.provider_priority = toggleInArray(this.editAgentData.provider_priority, name); },

      async loadAgents(this: any) {
        try { const r = await api().get('/api/agents'); this.agents = r.agents || []; } catch { this.agents = []; }
        try { const t = await api().get('/api/tenants'); this.availableTenants = t.tenants || []; } catch { /* ignore */ }
      },

      async createAgent(this: any) {
        if (!this.newAgent.name || !/^[a-z][a-z0-9_-]*$/.test(this.newAgent.name)) {
          this.agentCreateResult = { error: __('agent.namePatternHint') };
          notify().error(__('agent.namePatternHint'));
          return;
        }
        this.creatingAgent = true; this.agentCreateResult = null;
        const body = {
          name: this.newAgent.name, description: this.newAgent.description,
          tenant_ids: this.newAgent.tenant_ids_selected || [],
          provider_priority: this.newAgent.provider_priority || [],
          system_prompt: this.newAgent.system_prompt || null,
        };
        try {
          const result = await api().post('/api/agents', body);
          this.agentCreateResult = result; this.showNewAgentForm = false;
          this.newAgent = { name: '', description: '', tenant_ids_selected: [], provider_priority: [], system_prompt: '' };
          notify().success('Agent "' + body.name + '" created');
          events().emit('agents:created', body.name);
          await this.loadAgents();
        } catch (e: unknown) {
          const msg = e instanceof Error ? e.message : String(e);
          this.agentCreateResult = { error: msg }; notify().error(msg);
        } finally { this.creatingAgent = false; }
      },

      editAgent(this: any, agent: any) {
        const vc = agent.voice_config || {};
        this.editAgentData = {
          name: agent.name, description: agent.description || '',
          tenant_ids: [...(agent.tenant_ids || [])], provider_priority: [...(agent.provider_priority || [])],
          system_prompt: agent.system_prompt || '',
          voice_config_enabled: vc.enabled != null ? vc.enabled : true,
          voice_input_disabled: vc.voice_input_disabled || false,
          voice_output_disabled: vc.voice_output_disabled || false,
          voice_stt_provider: vc.stt_provider || '', voice_tts_provider: vc.tts_provider || '',
          _sttProviders: [], _ttsProviders: [],
        };
        this.editingAgent = true;
        api().get('/api/voice-config').then((vcResp: any) => {
          this.editAgentData._sttProviders = vcResp.stt_providers || [];
          this.editAgentData._ttsProviders = vcResp.tts_providers || [];
        }).catch(() => { this.editAgentData._sttProviders = []; this.editAgentData._ttsProviders = []; });
      },

      async updateAgent(this: any) {
        this.savingAgent = true;
        const vc: any = {};
        if (this.editAgentData.voice_config_enabled !== undefined) vc.enabled = this.editAgentData.voice_config_enabled;
        if (this.editAgentData.voice_input_disabled) vc.voice_input_disabled = true;
        if (this.editAgentData.voice_output_disabled) vc.voice_output_disabled = true;
        if (this.editAgentData.voice_stt_provider) vc.stt_provider = this.editAgentData.voice_stt_provider;
        if (this.editAgentData.voice_tts_provider) vc.tts_provider = this.editAgentData.voice_tts_provider;
        const body = {
          description: this.editAgentData.description, tenant_ids: this.editAgentData.tenant_ids,
          provider_priority: this.editAgentData.provider_priority || [],
          system_prompt: this.editAgentData.system_prompt || null,
          voice_config: Object.keys(vc).length > 0 ? vc : null,
        };
        try {
          await api().put('/api/agents/' + encodeURIComponent(this.editAgentData.name), body);
          this.editingAgent = false;
          notify().success('Agent "' + this.editAgentData.name + '" updated');
          events().emit('agents:updated', this.editAgentData.name);
          await this.loadAgents();
        } catch (e: unknown) { notify().error(e instanceof Error ? e.message : String(e)); }
        finally { this.savingAgent = false; }
      },

      async deleteAgent(this: any, name: string) {
        if (!confirm(__('confirm.deleteAgent') + ' "' + name + '"?')) return;
        try {
          await api().del('/api/agents/' + encodeURIComponent(name));
          notify().success('Agent "' + name + '" deleted');
          events().emit('agents:deleted', name);
          await this.loadAgents();
        } catch (e: unknown) { notify().error(e instanceof Error ? e.message : String(e)); }
      },
    };
  },
});

export {};
