// index.ts — admin-dashboard entry point
// Architecture:
//   - Core modules wire Alpine stores and utilities (loaded first)
//   - Domain modules register state+methods via AppRegistry (loaded second)
//   - dashboard() Alpine component merges everything and exposes it
//
// Build: esbuild --bundle --format=iife → dist/app.js

// ── Core (order matters: registry before anything that uses it) ──
import './core/registry.js';
import './core/apiClient.js';
import './i18n.js';
import './core/eventBus.js';
import './core/notify.js';
import './core/apiLogger.js';
import './core/store.js';

// ── Domains (import = side-effect: AppRegistry.register()) ──
import './domains/auth.js';
import './domains/tenants.js';
import './domains/config.js';
import './domains/tools.js';
import './domains/rag.js';
import './domains/agents.js';
import './domains/abuse.js';
import './domains/emergency.js';
import './domains/llm.js';
import './domains/voice.js';
import './domains/audit.js';

import type { DashboardData } from './types.js';

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyRecord = Record<string, any>;

const EXPECTED_DOMAINS = [
  'auth', 'tenants', 'config', 'tools', 'rag', 'agents',
  'abuse', 'emergency', 'llm', 'voice', 'audit',
];

// ── dashboard() — Alpine component factory ──
function dashboard(): AnyRecord {
  return {
    // ── State: merged from all domains ──
    ...AppRegistry.getState(),

    // ── Emergency computed getters ──
    get emergencyPresetLabel(): string {
      const self = this as AnyRecord;
      if (self.emergencyCurrentPreset === 'lockdown') return self.__('emergency.labelLockdown');
      if (self.emergencyCurrentPreset === 'cautious') return self.__('emergency.labelCautious');
      return self.__('emergency.labelNormal');
    },

    get emergencyPresetDescription(): string {
      const self = this as AnyRecord;
      if (self.emergencyCurrentPreset === 'lockdown') return self.__('emergency.descLockdown');
      if (self.emergencyCurrentPreset === 'cautious') return self.__('emergency.descCautious');
      return self.__('emergency.descNormal');
    },

    get emergencyPresetClass(): string {
      const self = this as AnyRecord;
      if (self.emergencyCurrentPreset === 'lockdown') return 'emergency-lockdown';
      if (self.emergencyCurrentPreset === 'cautious') return 'emergency-cautious';
      return 'emergency-normal';
    },

    // ── Dashboard own data ──
    dashboard: {} as DashboardData,

    // ── UI state (direct properties — Alpine needs them on the x-data scope) ──
    page: 'dashboard',
    error: '',
    loading: false,
    dataService: '',

    // ── Aliases ──
    get tokenSet(): boolean {
      return (window as any).Alpine.store('ui').tokenSet;
    },
    get tokenInput(): string {
      return (window as any).Alpine.store('ui').tokenInput;
    },
    set tokenInput(v: string) {
      (window as any).Alpine.store('ui').tokenInput = v;
    },

    // ── I18N ──
    __(key: string): string {
      return (window as any).__(key);
    },

    // ── INIT ──
    init(): void {
      const self = this as any;
      if (!self.tokenSet) return;

      AppRegistry.expectAll(EXPECTED_DOMAINS);

      // Sync from store
      const ui = (window as any).Alpine.store('ui');
      self.page = ui.page;
      self.dataService = ui.dataService || '';

      // Inject methods from domains
      const methods = AppRegistry.getMethods();
      for (const [k, v] of Object.entries(methods)) {
        self[k] = v;
      }

      // Cross-domain subscriptions
      const events = (window as any).Alpine.store('events');

      events.on('tenant:selected', (data: unknown) => {
        const d = data as { id: string };
        self.refreshConfig(d.id);
        self.loadPendingTools(d.id);
        self.loadManifest(d.id);
      });

      events.on('config:saved', () => {
        self.loadPendingTools(self.selectedTenant);
        self.loadManifest(self.selectedTenant);
      });

      events.on('llm:providers-changed', () => {
        if (typeof self.loadLlmProviderStoreList === 'function') self.loadLlmProviderStoreList();
        if (typeof self.loadLlmConfig === 'function') self.loadLlmConfig();
      });

      events.on('agents:updated', () => {
        if (typeof self.loadLlmProviderStoreList === 'function') self.loadLlmProviderStoreList();
      });

      // Initial load
      self.refreshDashboard();
      self.loadTenants();
      self.loadEmergencyStatus();
      self.refreshRag();
    },

    // ── Dashboard (not a full domain — small inline) ──
    async refreshDashboard(this: AnyRecord): Promise<void> {
      try {
        const data = await (window as any).Alpine.store('api').get('/api/dashboard') as DashboardData;
        this.dashboard = data;
        this.dataService = (data as AnyRecord).data_service || '';
      } catch {
        // error handled in apiClient
      }
    },

    // ── login / logout delegates ──
    login(this: AnyRecord): void {
      (window as any).Alpine.store('ui').login();
      // After login, re-init
      const self = this;
      setTimeout(() => { self.init(); }, 100);
    },

    logout(this: AnyRecord): void {
      (window as any).Alpine.store('ui').logout();
    },
  };
}

// Expose globally for Alpine x-data="dashboard()"
(window as any).dashboard = dashboard;
