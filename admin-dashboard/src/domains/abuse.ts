// abuse.ts — Anti-Abuse settings
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const w = window as any;

AppRegistry.register('abuse', {
  state: {
    abuseTab: 'global',
    abuseGlobal: {
      rps: null, burst: null, max_message_length: null, min_interval_ms: null,
      max_messages_per_session: null, token_budget: null, block_empty_user_agent: null,
      blocked_user_agents: [], _ua_text: '',
      history_turns: null, history_content_chars: null, max_iterations: null,
      max_empty_rounds: null, max_turn_tokens: null, session_ttl_hours: null,
    },
    abuseAgent: null, abuseAgentName: '', abuseAgentOverrides: {},
    abuseSaving: false, abuseSaveMsg: '', abuseReloading: false, abuseReloadMsg: '',
    abuseAgentList: [],
  },

  methods() {
    const api = () => w.Alpine.store('api');
    const notify = () => w.Alpine.store('notify');

    return {
      async loadAbuseSettings(this: any) {
        try { this.abuseGlobal = await api().get('/api/abuse-settings'); }
        catch (e: unknown) { notify().error(e instanceof Error ? e.message : String(e)); }
      },

      async saveAbuseGlobal(this: any) {
        this.abuseSaving = true; this.abuseSaveMsg = '';
        try { await api().put('/api/abuse-settings', this.abuseGlobal); notify().success('Abuse settings saved'); }
        catch (e: unknown) { notify().error(e instanceof Error ? e.message : String(e)); }
        finally { this.abuseSaving = false; }
      },

      async selectAbuseAgent(this: any, name: string) {
        this.abuseAgentName = name;
        try { this.abuseAgent = await api().get('/api/agents/' + name + '/abuse'); }
        catch (e: unknown) { this.abuseAgent = null; notify().error(e instanceof Error ? e.message : String(e)); }
      },

      async saveAbuseAgent(this: any) {
        this.abuseSaving = true;
        try { await api().put('/api/agents/' + this.abuseAgentName + '/abuse', this.abuseAgent); notify().success('Agent abuse settings saved'); }
        catch (e: unknown) { notify().error(e instanceof Error ? e.message : String(e)); }
        finally { this.abuseSaving = false; }
      },
    };
  },
});

export {};
