// types.ts — shared types for admin-dashboard

// ── API response ──

export interface ApiResponse<T = unknown> {
  data?: T;
  error?: string;
  message?: string;
}

// ── Domain module shape ──

export interface DomainModule<S extends Record<string, unknown>, M extends Record<string, unknown>> {
  state: S;
  methods: () => M;
}

// ── Tenant ──

export interface TenantInfo {
  id: string;
}

// ── Dashboard ──

export interface DashboardData {
  tenants?: TenantInfo[];
  tenant_count?: number;
  data_service?: string;
  status?: string;
  role?: string;
}

// ── Config ──

export interface DataSourceConfig {
  driver?: string;
  dsn?: string;
  read_only?: boolean;
  pool_size?: number;
}

export interface EntityConfig {
  name: string;
  table_name?: string;
  fields?: FieldConfig[];
}

export interface FieldConfig {
  name?: string;
  type?: string;
  description?: string;
}

export interface EndpointConfig {
  entity?: string;
  path?: string;
  method?: string;
  op?: string;
  description?: string;
  enabled?: boolean;
}

export interface McpToolConfig {
  name: string;
  endpoint?: string;
  description?: string;
  params?: unknown[];
  display_name?: string;
}

export interface TenantConfig {
  version?: number;
  data_source?: DataSourceConfig;
  entities?: EntityConfig[];
  endpoints?: EndpointConfig[];
  mcp_tools?: McpToolConfig[];
  custom_queries?: Record<string, unknown>;
  skip_rules?: SkipRule[];
  display_prefixes?: string[];
  custom_plurals?: Record<string, string>;
  approved_tools?: string[];
  disabled_default_rules?: string[];
  stats?: { counters?: { entity?: string }[] };
}

export interface SkipRule {
  prefix?: string;
  suffix?: string;
  contains?: string;
  reason?: string;
}

// ── Computed summary ──

export interface ComputedSummary {
  driver: string;
  readonly: boolean;
  poolSize: string | number;
  entities: number;
  endpoints: number;
  mcpTools: number;
  customQueries: number;
  skipRules: number;
  displayPrefixes: string;
  customPlurals: number;
  approvedTools: number;
}

// ── Pending tools ──

export interface PendingTools {
  tools: ToolInfo[];
  mode: string;
}

export interface ToolInfo {
  name: string;
  description?: string;
  path?: string;
  method?: string;
  approved?: boolean;
}

export interface ManifestData {
  endpoints?: ManifestEndpoint[];
  mcp_tools?: McpToolConfig[];
  data_source?: DataSourceConfig;
}

export interface ManifestEndpoint {
  path?: string;
  method?: string;
}

// ── RAG ──

export interface RagHealth {
  status?: string;
  error?: string;
  embedding?: { model?: string };
}

export interface RagDoc {
  id?: string;
  document_id?: string;
  title?: string;
  filename?: string;
  source_path?: string;
  path?: string;
  chunks_count?: number;
}

export interface RagSettings {
  embedding_provider: string;
  embedding_model: string;
  embedding_api_key: string;
  embedding_api_base: string;
  embedding_dimensions: number;
  chunker_type: string;
  chunk_size: number;
  chunk_overlap: number;
  reranker_enabled: boolean;
  reranker_k1: number;
  reranker_b: number;
  cache_enabled: boolean;
  cache_ttl: number;
  cache_maxsize: number;
}

export interface RagStats {
  document_count?: number;
  chunk_count?: number;
  chroma_size_mb?: number;
}

// ── Agents ──

export interface AgentInfo {
  name: string;
  description?: string;
  tenant_ids?: string[];
  provider_priority?: string[];
  system_prompt?: string;
  voice_config?: VoiceConfig;
}

export interface VoiceConfig {
  enabled?: boolean;
  voice_input_disabled?: boolean;
  voice_output_disabled?: boolean;
  stt_provider?: string;
  tts_provider?: string;
}

// ── LLM ──

export interface LlmConfig {
  providers?: LlmProvider[];
  fallback_enabled?: boolean;
  num_models?: number;
}

export interface LlmProvider {
  name: string;
  model: string;
  provider?: string;
  api_key?: string;
  api_base?: string;
  enabled: boolean;
  has_api_key?: boolean;
  api_key_masked?: string;
}

export interface LlmProviderListResponse {
  providers?: LlmProviderListItem[];
}

export interface LlmProviderListItem {
  name?: string;
  provider?: string;
}

// ── Abuse ──

export interface AbuseGlobalSettings {
  rps: number | null;
  burst: number | null;
  max_message_length: number | null;
  min_interval_ms: number | null;
  max_messages_per_session: number | null;
  token_budget: number | null;
  block_empty_user_agent: boolean | null;
  blocked_user_agents: string[];
  _ua_text: string;
  history_turns: number | null;
  history_content_chars: number | null;
  max_iterations: number | null;
  max_empty_rounds: number | null;
  max_turn_tokens: number | null;
  session_ttl_hours: number | null;
}

export interface AbuseAgentSettings {
  // mirrors AbuseGlobalSettings
  [key: string]: unknown;
}

// ── Emergency ──

export interface EmergencyStatus {
  rps?: number | null;
  burst?: number | null;
  token_budget?: number | null;
  max_messages?: number | null;
  min_interval_ms?: number | null;
  current_preset?: string;
}

// ── Audit ──

export interface AuditEntry {
  ts?: string;
  action?: string;
  details?: string;
}

// ── Voice ──

export interface VoiceConfigData {
  enabled: boolean;
  stt_providers: VoiceProviderEntry[];
  tts_providers: VoiceProviderEntry[];
  stt_fallback_enabled: boolean;
  tts_fallback_enabled: boolean;
  max_voice_message_size: number;
  min_voice_interval_seconds: number;
  max_voice_duration_seconds: number;
}

export interface VoiceProviderEntry {
  name: string;
  provider: string;
  model: string;
  voice?: string;
  api_key?: string;
  api_base?: string;
  enabled: boolean;
}

// ── API Log ──

export interface ApiLogEntry {
  id: number;
  method: string;
  path: string;
  status: number;
  reqBody: string | null;
  resBody: string;
  durationMs: number;
  ts: string;
}

export interface ApiToast {
  id: number;
  text: string;
  class: string;
  entryId: number;
}
