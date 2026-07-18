// types.test.ts — type-level contracts and runtime checks
import { readFileSync } from 'fs';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';
import { describe, expect, it } from 'vitest';

const __dirname = dirname(fileURLToPath(import.meta.url));
import type {
  // Response
  ApiResponse,
  // Domain
  DomainModule,
  // Tenant
  TenantInfo,
  // Dashboard
  DashboardData,
  // Config
  DataSourceConfig,
  EntityConfig,
  FieldConfig,
  EndpointConfig,
  McpToolConfig,
  TenantConfig,
  SkipRule,
  ComputedSummary,
  // Tools
  PendingTools,
  ToolInfo,
  ManifestData,
  ManifestEndpoint,
  // RAG
  RagHealth,
  RagDoc,
  RagSettings,
  RagStats,
  // Agents
  AgentInfo,
  VoiceConfig,
  // LLM
  LlmConfig,
  LlmProvider,
  LlmProviderListResponse,
  LlmProviderListItem,
  // Abuse
  AbuseGlobalSettings,
  // Emergency
  EmergencyStatus,
  // Audit
  AuditEntry,
  // Voice
  VoiceConfigData,
  VoiceProviderEntry,
  // API Log
  ApiLogEntry,
  ApiToast,
} from '../src/types.js';

describe('Types — type-level contracts', () => {
  // ── ApiResponse ──
  it('ApiResponse can hold data or error', () => {
    const ok: ApiResponse<{ id: string }> = { data: { id: 'abc' } };
    const err: ApiResponse = { error: 'not found' };
    expect(ok.data!.id).toBe('abc');
    expect(err.error).toBe('not found');
  });

  // ── DomainModule ──
  it('DomainModule holds state and methods', () => {
    const mod: DomainModule<{ count: number }, { inc: () => void }> = {
      state: { count: 0 },
      methods: () => ({ inc: () => { } }),
    };
    expect(mod.state.count).toBe(0);
    expect(typeof mod.methods().inc).toBe('function');
  });

  // ── TenantInfo ──
  it('TenantInfo has string id', () => {
    const t: TenantInfo = { id: 'shop' };
    expect(t.id).toBe('shop');
  });

  // ── DashboardData ──
  it('DashboardData can hold dashboard fields', () => {
    const d: DashboardData = {
      tenants: [{ id: 'a' }, { id: 'b' }],
      tenant_count: 2,
      data_service: 'ok',
      status: 'healthy',
      role: 'admin',
    };
    expect(d.tenants).toHaveLength(2);
    expect(d.tenant_count).toBe(2);
    expect(d.role).toBe('admin');
  });

  // ── DataSourceConfig ──
  it('DataSourceConfig configures DB connection', () => {
    const ds: DataSourceConfig = {
      driver: 'sqlite',
      dsn: '/data/db.sqlite',
      read_only: true,
      pool_size: 5,
    };
    expect(ds.driver).toBe('sqlite');
    expect(ds.read_only).toBe(true);
  });

  // ── EntityConfig ──
  it('EntityConfig describes a table', () => {
    const e: EntityConfig = {
      name: 'students',
      table_name: 'students',
      fields: [{ name: 'id', type: 'INTEGER' }],
    };
    expect(e.name).toBe('students');
    expect(e.fields).toHaveLength(1);
  });

  // ── EndpointConfig ──
  it('EndpointConfig describes an API endpoint', () => {
    const ep: EndpointConfig = {
      entity: 'students',
      path: '/students/{id}',
      method: 'GET',
      op: 'get_by_id',
      enabled: true,
    };
    expect(ep.op).toBe('get_by_id');
  });

  // ── McpToolConfig ──
  it('McpToolConfig describes an MCP tool', () => {
    const tool: McpToolConfig = {
      name: 'find_students',
      description: 'Search students by name',
      endpoint: '/students',
      display_name: 'Поиск студентов',
    };
    expect(tool.display_name).toBe('Поиск студентов');
  });

  // ── TenantConfig ──
  it('TenantConfig holds full tenant config', () => {
    const cfg: TenantConfig = {
      version: 1,
      data_source: { driver: 'postgres', dsn: 'pg://...', read_only: true },
      entities: [{ name: 'products' }],
      endpoints: [{ path: '/products', method: 'GET' }],
      mcp_tools: [{ name: 'find_products' }],
      custom_queries: { top_products: 'SELECT * FROM products LIMIT 10' },
      approved_tools: ['create_product'],
      disabled_default_rules: ['sqlite_'],
    };
    expect(cfg.version).toBe(1);
    expect(cfg.approved_tools).toContain('create_product');
  });

  // ── SkipRule ──
  it('SkipRule has prefix/suffix/contains', () => {
    const rule: SkipRule = { prefix: 'django_', reason: 'Django metadata' };
    expect(rule.prefix).toBe('django_');
    expect(rule.suffix).toBeUndefined();
  });

  // ── ComputedSummary ──
  it('ComputedSummary has all summary fields', () => {
    const s: ComputedSummary = {
      driver: 'sqlite',
      readonly: true,
      poolSize: 5,
      entities: 10,
      endpoints: 20,
      mcpTools: 15,
      customQueries: 3,
      skipRules: 2,
      displayPrefixes: 'catalog_',
      customPlurals: 0,
      approvedTools: 1,
    };
    expect(s.entities).toBe(10);
    expect(s.poolSize).toBe(5);
  });

  // ── PendingTools / ToolInfo ──
  it('PendingTools holds tools list and mode', () => {
    const pt: PendingTools = {
      tools: [{ name: 'create_product', approved: false }],
      mode: 'read_only',
    };
    expect(pt.mode).toBe('read_only');
    expect(pt.tools[0].approved).toBe(false);
  });

  // ── ManifestData / ManifestEndpoint ──
  it('ManifestData holds endpoints and mcp_tools', () => {
    const m: ManifestData = {
      endpoints: [{ path: '/students', method: 'GET' }],
      mcp_tools: [{ name: 'find_students' }],
    };
    expect(m.endpoints).toHaveLength(1);
    expect(m.mcp_tools).toHaveLength(1);
  });

  // ── RAG ──
  it('RagHealth carries embeddings model info', () => {
    const h: RagHealth = { status: 'ok', embedding: { model: 'paraphrase-multilingual' } };
    expect(h.embedding!.model).toContain('paraphrase');
  });

  it('RagDoc carries document metadata', () => {
    const doc: RagDoc = { id: 'doc1', title: 'Lecture 1', filename: 'lec1.pdf', chunks_count: 42 };
    expect(doc.chunks_count).toBe(42);
  });

  it('RagSettings has all configurable fields', () => {
    const s: RagSettings = {
      embedding_provider: 'sentence-transformers',
      embedding_model: 'all-MiniLM-L6-v2',
      embedding_api_key: '',
      embedding_api_base: '',
      embedding_dimensions: 384,
      chunker_type: 'recursive',
      chunk_size: 768,
      chunk_overlap: 160,
      reranker_enabled: false,
      reranker_k1: 1.5,
      reranker_b: 0.75,
      cache_enabled: true,
      cache_ttl: 300,
      cache_maxsize: 256,
    };
    expect(s.embedding_dimensions).toBe(384);
    expect(s.cache_enabled).toBe(true);
  });

  // ── AgentInfo / VoiceConfig ──
  it('AgentInfo carries agent configuration', () => {
    const agent: AgentInfo = {
      name: 'shop-agent',
      description: 'Retail assistant',
      tenant_ids: ['shop'],
      provider_priority: ['ollama'],
      system_prompt: 'You are a shop assistant.',
    };
    expect(agent.tenant_ids).toContain('shop');
  });

  it('VoiceConfig can be partial', () => {
    const vc: VoiceConfig = { enabled: true, stt_provider: 'whisper' };
    expect(vc.enabled).toBe(true);
  });

  // ── LLM ──
  it('LlmConfig holds provider list and fallback', () => {
    const cfg: LlmConfig = {
      providers: [{ name: 'ollama', model: 'qwen2.5', enabled: true }],
      fallback_enabled: true,
      num_models: 1,
    };
    expect(cfg.fallback_enabled).toBe(true);
    expect(cfg.providers).toHaveLength(1);
  });

  it('LlmProvider can have api_key details', () => {
    const p: LlmProvider = {
      name: 'mistral',
      model: 'mistral-medium',
      enabled: true,
      has_api_key: true,
      api_key_masked: '***...key',
    };
    expect(p.has_api_key).toBe(true);
  });

  it('LlmProviderListItem has name and provider', () => {
    const item: LlmProviderListItem = { name: 'ollama', provider: 'ollama' };
    expect(item.provider).toBe('ollama');
  });

  // ── Abuse ──
  it('AbuseGlobalSettings has all fields', () => {
    const s: AbuseGlobalSettings = {
      rps: 10,
      burst: 20,
      max_message_length: 2000,
      min_interval_ms: 1000,
      max_messages_per_session: 50,
      token_budget: 100000,
      block_empty_user_agent: true,
      blocked_user_agents: ['curl'],
      _ua_text: 'curl\nwget',
      history_turns: 10,
      history_content_chars: 4000,
      max_iterations: 10,
      max_empty_rounds: 3,
      max_turn_tokens: 2000,
      session_ttl_hours: 24,
    };
    expect(s.blocked_user_agents).toContain('curl');
    expect(s.session_ttl_hours).toBe(24);
  });

  // ── Emergency ──
  it('EmergencyStatus has current preset', () => {
    const s: EmergencyStatus = {
      rps: 5,
      burst: 10,
      token_budget: null,
      max_messages: null,
      min_interval_ms: 2000,
      current_preset: 'cautious',
    };
    expect(s.current_preset).toBe('cautious');
  });

  // ── Audit ──
  it('AuditEntry has timestamp and action', () => {
    const e: AuditEntry = { ts: '2024-01-01T00:00:00Z', action: 'tenant.create', details: 'Created tenant shop' };
    expect(e.action).toBe('tenant.create');
  });

  // ── Voice ──
  it('VoiceConfigData has STT/TTS provider lists', () => {
    const v: VoiceConfigData = {
      enabled: true,
      stt_providers: [{ name: 'whisper', provider: 'litellm', model: 'whisper-1', enabled: true }],
      tts_providers: [],
      stt_fallback_enabled: true,
      tts_fallback_enabled: false,
      max_voice_message_size: 10485760,
      min_voice_interval_seconds: 10,
      max_voice_duration_seconds: 120,
    };
    expect(v.stt_providers).toHaveLength(1);
    expect(v.tts_fallback_enabled).toBe(false);
  });

  // ── ApiLog ──
  it('ApiLogEntry has request/response fields', () => {
    const log: ApiLogEntry = {
      id: 1,
      method: 'GET',
      path: '/api/agents',
      status: 200,
      reqBody: null,
      resBody: '{"agents":[]}',
      durationMs: 42,
      ts: '2024-01-01T00:00:00Z',
    };
    expect(log.status).toBe(200);
    expect(log.durationMs).toBe(42);
  });

  it('ApiToast carries notification data', () => {
    const t: ApiToast = { id: 1, text: '✓ [200] GET /api/agents', class: 'api-toast-ok', entryId: 1 };
    expect(t.class).toBe('api-toast-ok');
  });
});

describe('Types — runtime structure checks', () => {
  it('types.ts file contains all expected export declarations', () => {
    const content = readFileSync(resolve(__dirname, '../src/types.ts'), 'utf8');
    const expected = [
      'ApiResponse', 'DomainModule',
      'TenantInfo', 'DashboardData',
      'DataSourceConfig', 'EntityConfig', 'FieldConfig', 'EndpointConfig', 'McpToolConfig', 'TenantConfig', 'SkipRule', 'ComputedSummary',
      'PendingTools', 'ToolInfo', 'ManifestData', 'ManifestEndpoint',
      'RagHealth', 'RagDoc', 'RagSettings', 'RagStats',
      'AgentInfo', 'VoiceConfig',
      'LlmConfig', 'LlmProvider', 'LlmProviderListResponse', 'LlmProviderListItem',
      'AbuseGlobalSettings',
      'EmergencyStatus',
      'AuditEntry',
      'VoiceConfigData', 'VoiceProviderEntry',
      'ApiLogEntry', 'ApiToast',
    ];
    for (const name of expected) {
      expect(content).toContain('export interface ' + name);
    }
  });
});
