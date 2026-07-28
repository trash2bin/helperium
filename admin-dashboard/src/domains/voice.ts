// voice.ts — Voice Config: STT providers
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const w = window as any;

AppRegistry.register('voice', {
  state: {
    voiceConfig: {
      enabled: true, stt_providers: [] as any[],
      stt_fallback_enabled: true,
      max_voice_message_size: 10485760, min_voice_interval_seconds: 10, max_voice_duration_seconds: 120,
    },
    voiceError: '', voiceSaveMsg: '', voiceSaveSuccess: false, voiceSaving: false,
    showAddSttProvider: false,
    editingSttIndex: -1,
    voiceSttForm: { name: '', provider: 'litellm', model: 'whisper-1', api_key: '', api_base: '', enabled: true },
  },

  methods() {
    const api = () => w.Alpine.store('api');
    const notify = () => w.Alpine.store('notify');
    const events = () => w.Alpine.store('events');

    return {
      async loadVoiceConfig(this: any) {
        this.voiceError = '';
        try {
          this.voiceConfig = await api().get('/api/voice-config');
          events().emit('voice:loaded', this.voiceConfig);
        } catch (e: unknown) {
          const msg = e instanceof Error ? e.message : 'Failed to load voice config';
          this.voiceError = String(msg); notify().error(msg);
        }
      },

      async saveVoiceConfig(this: any) {
        this.voiceSaving = true; this.voiceSaveMsg = ''; this.voiceSaveSuccess = false;
        try {
          await api().put('/api/voice-config', this.voiceConfig);
          this.voiceSaveMsg = __('msg.saved'); this.voiceSaveSuccess = true;
          notify().success(__('msg.saved'));
          setTimeout(() => { this.voiceSaveMsg = ''; }, 3000);
          await this.loadVoiceConfig(); events().emit('voice:saved', this.voiceConfig);
        } catch (e: unknown) {
          const msg = e instanceof Error ? e.message : __('msg.failed');
          this.voiceSaveMsg = String(msg); this.voiceSaveSuccess = false; notify().error(msg);
        } finally { this.voiceSaving = false; }
      },

      cancelVoiceEdit(this: any) {
        this.showAddSttProvider = false;
        this.editingSttIndex = -1;
        this.voiceSttForm = { name: '', provider: 'litellm', model: 'whisper-1', api_key: '', api_base: '', enabled: true };
      },

      editSttProvider(this: any, idx: number) {
        const prov = this.voiceConfig?.stt_providers?.[idx];
        if (!prov) return;
        this.editingSttIndex = idx; this.showAddSttProvider = true;
        this.voiceSttForm = { name: prov.name || '', provider: prov.provider, model: prov.model, api_key: prov.api_key || '', api_base: prov.api_base || '', enabled: prov.enabled !== false };
      },

      deleteSttProvider(this: any, idx: number) {
        if (!confirm(__('voice.deleteConfirmMsg') + '?')) return;
        this.voiceConfig.stt_providers.splice(idx, 1);
        this.saveVoiceConfig();
      },

      saveSttProvider(this: any) {
        if (!this.voiceConfig.stt_providers) this.voiceConfig.stt_providers = [];
        const body = { name: this.voiceSttForm.name, provider: this.voiceSttForm.provider, model: this.voiceSttForm.model, api_key: this.voiceSttForm.api_key || '', api_base: this.voiceSttForm.api_base || '', enabled: this.voiceSttForm.enabled };
        if (this.editingSttIndex >= 0 && this.editingSttIndex < this.voiceConfig.stt_providers.length) this.voiceConfig.stt_providers[this.editingSttIndex] = body;
        else this.voiceConfig.stt_providers.push(body);
        this.cancelVoiceEdit(); this.saveVoiceConfig();
      },

    };
  },
});

export {};
