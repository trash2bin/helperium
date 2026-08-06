// emergency.ts — Emergency presets
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const w = window as any;

AppRegistry.register('emergency', {
  state: {
    emergencyStatus: { rps: null, burst: null, token_budget: null, max_messages: null, min_interval_ms: null },
    emergencyActive: false, emergencyCurrentPreset: 'normal',
    emergencyApplying: false, emergencyTimer: null, emergencyConflicting: false,
  },

  methods() {
    const api = () => w.Alpine.store('api');
    const notify = () => w.Alpine.store('notify');
    const events = () => w.Alpine.store('events');

    return {
      async loadEmergencyStatus(this: any) {
        try {
          const resp = await api().get('/api/emergency-status');
          this.emergencyStatus = resp;
          this.emergencyCurrentPreset = resp.current_preset || 'normal';
          this.emergencyActive = resp.current_preset === 'lockdown';
        } catch (e: unknown) { notify().error(e instanceof Error ? e.message : String(e)); }
      },

      async applyEmergencyPreset(this: any, preset: string) {
        this.emergencyApplying = true;
        try {
          await api().post('/api/abuse-preset/' + preset);
          this.emergencyCurrentPreset = preset;
          this.emergencyActive = preset === 'lockdown';
          notify().success('Emergency preset: ' + preset);
          events().emit('emergency:preset-changed', preset);
        } catch (e: unknown) { notify().error(e instanceof Error ? e.message : String(e)); }
        finally { this.emergencyApplying = false; }
      },

      toggleEmergencyMode(this: any) {
        const preset = this.emergencyCurrentPreset === 'lockdown' ? 'normal' : 'lockdown';
        this.applyEmergencyPreset(preset);
      },
    };
  },
});

export {};
