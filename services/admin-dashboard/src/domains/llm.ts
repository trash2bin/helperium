// llm.ts — LLM Provider configuration
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const w = window as any;

AppRegistry.register('llm', {
  state: {
    llmConfig: { providers: [], fallback_enabled: false },
    llmError: '', llmTab: 'list',
    llmNew: { name: '', model: '', api_key: '', api_base: '', enabled: true, provider: '' },
    llmEdit: { model: '', api_key: '', api_base: '', has_api_key: false, api_key_masked: '', enabled: true },
    llmEditName: '', llmProviderList: [] as any[],
    llmSaving: false, llmSaveMsg: '', llmDeleteConfirm: false,
  },

  methods() {
    const api = () => w.Alpine.store('api');
    const notify = () => w.Alpine.store('notify');
    const events = () => w.Alpine.store('events');

    return {
      async loadLlmConfig(this: any) {
        this.llmError = '';
        try { this.llmConfig = await api().get('/api/llm-config'); await this.loadLlmProviderList(); }
        catch (e: unknown) { this.llmError = (e instanceof Error ? e.message : String(e)) || __('llm.loadError'); }
      },

      async loadLlmProviderList(this: any) {
        try { const r = await api().get('/api/llm-provider-list'); this.llmProviderList = r.providers || []; }
        catch { this.llmProviderList = []; }
      },

      async addLlmProvider(this: any) {
        this.llmSaving = true;
        try {
          const body = { name: this.llmNew.name, model: this.llmNew.model, provider: this.llmNew.provider || undefined, api_key: this.llmNew.api_key || undefined, api_base: this.llmNew.api_base || undefined, enabled: this.llmNew.enabled };
          await api().post('/api/llm-providers', body);
          this.llmNew = { name: '', model: '', api_key: '', api_base: '', enabled: true, provider: '' };
          notify().success(__('msg.saved')); await this.loadLlmConfig(); events().emit('llm:providers-changed');
        } catch (e: unknown) { notify().error(e instanceof Error ? e.message : String(e)); }
        finally { this.llmSaving = false; }
      },

      startEditLlmProvider(this: any, name: string) {
        const p = this.llmConfig?.providers?.find((x: any) => x.name === name);
        if (!p) return;
        this.llmEditName = name;
        this.llmEdit = { model: p.model, api_key: '', api_base: p.api_base || '', enabled: p.enabled, has_api_key: p.has_api_key || false, api_key_masked: p.api_key_masked || '' };
        this.llmTab = 'edit';
      },

      async saveLlmProvider(this: any) {
        if (!this.llmEditName) return; this.llmSaving = true;
        try {
          const body: any = { model: this.llmEdit.model, api_base: this.llmEdit.api_base || undefined, enabled: this.llmEdit.enabled };
          if (this.llmEdit.api_key && this.llmEdit.api_key.trim()) body.api_key = this.llmEdit.api_key.trim();
          else if (this.llmEdit.api_key === '') body.api_key = '';
          await api().put('/api/llm-providers/' + this.llmEditName, body);
          notify().success(__('msg.saved')); await this.loadLlmConfig(); events().emit('llm:providers-changed');
        } catch (e: unknown) { notify().error(e instanceof Error ? e.message : String(e)); }
        finally { this.llmSaving = false; }
      },

      async deleteLlmProvider(this: any, name: string) {
        if (!confirm(__('llm.deleteConfirmMsg') + ' "' + name + '"?')) return;
        try { await api().del('/api/llm-providers/' + name); await this.loadLlmConfig(); events().emit('llm:providers-changed'); }
        catch (e: unknown) { notify().error(e instanceof Error ? e.message : String(e)); }
      },

      async toggleLlmProvider(this: any, name: string) {
        try { await api().post('/api/llm-providers/' + name + '/toggle'); await this.loadLlmConfig(); events().emit('llm:providers-changed'); }
        catch (e: unknown) { notify().error(e instanceof Error ? e.message : String(e)); }
      },

      cancelLlmEdit(this: any) {
        this.llmTab = 'list';
        this.llmEdit = { model: '', api_key: '', api_base: '', has_api_key: false, api_key_masked: '', enabled: true };
        this.llmEditName = '';
      },
    };
  },
});

export {};
