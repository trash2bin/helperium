// Package openapi генерирует OpenAPI 3.1.0 спецификацию admin-dashboard.
//
// Спека строится из map[string]any и сериализуется в JSON — без external зависимостей.
// Используется для:
//   - GET /openapi.json (runtime)
//   - static/openapi.json (build-time копия для контрактных тестов фронта)
//
// Все эндпоинты, которые идут через прокси, аннотируются:
//   x-proxy-to: "data-service" | "api-service" | "rag-service"
//   x-route-group: "local" | "data-service" | "api-service" | "rag-service"
package openapi

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

const version = "1.0.0"

// GenerateSpec создаёт полную OpenAPI 3.1.0 спецификацию admin-dashboard.
func GenerateSpec() map[string]any {
	return map[string]any{
		"openapi": "3.1.0",
		"info": map[string]any{
			"title":       "Admin Dashboard API",
			"description": "Прокси-API для администрирования платформы Helperium. Большинство эндпоинтов — pass-through к upstream сервисам (data-service, api-service, rag-service).\n\nАутентификация: Bearer токен через Authorization header (ADMIN_TOKEN или VIEWER_TOKEN). Viewer-level доступ только на GET /api/*.",
			"version":     version,
		},
		"servers": []map[string]any{
			{"url": "http://localhost:8085", "description": "admin-dashboard (dev)"},
		},
		"paths":      buildPaths(),
		"components": buildComponents(),
		"security": []map[string]any{
			{"BearerAuth": []string{}},
		},
	}
}

// WriteSpecToFile сериализует spec в JSON и пишет в указанный путь.
func WriteSpecToFile(spec map[string]any, filePath string) error {
	data, err := json.MarshalIndent(spec, "", "  ")
	if err != nil {
		return fmt.Errorf("marshal spec: %w", err)
	}
	dir := filepath.Dir(filePath)
	if err := os.MkdirAll(dir, 0755); err != nil {
		return fmt.Errorf("mkdir: %w", err)
	}
	if err := os.WriteFile(filePath, data, 0644); err != nil {
		return fmt.Errorf("write spec: %w", err)
	}
	return nil
}

// ── Paths ──────────────────────────────────────────────────────────────────

// buildPaths собирает все пути API.
func buildPaths() map[string]any {
	paths := make(map[string]any)

	// ── System / no auth ──
	addGet(paths, "/health", "health_check", "Health check", "System",
		withResponse("ok", "#/components/schemas/HealthResponse"),
		withNoAuth())

	addGet(paths, "/openapi.json", "openapi_spec", "OpenAPI спецификация", "System",
		withResponse("ok", "#/components/schemas/OpenAPISpec"),
		withNoAuth())

	// ── Local handlers ──
	addGet(paths, "/api/health", "api_health", "API health check", "Local",
		withResponse("ok", "#/components/schemas/HealthResponse"),
		withNoAuth())

	addGet(paths, "/api/dashboard", "dashboard", "Dashboard summary (tenant count, data-service status)", "Local",
		withResponse("ok", "#/components/schemas/DashboardResponse"))

	addPost(paths, "/api/db/test", "db_test", "Test DB connection by DSN", "Local",
		withRequestBody("#/components/schemas/DbTestRequest"),
		withResponse("ok", "#/components/schemas/DbTestResponse"))

	addGet(paths, "/api/audit", "audit_list", "Audit log entries", "Local",
		withQueryParam("limit", "integer", "Max entries (1-1000, default 50)", false),
		withResponse("ok", "#/components/schemas/AuditListResponse"))

	addGet(paths, "/api/abuse-settings", "abuse_settings_get", "Get global abuse config", "Local",
		withResponse("ok", "#/components/schemas/AbuseConfig"))

	addPut(paths, "/api/abuse-settings", "abuse_settings_put", "Update global abuse config", "Local",
		withRequestBody("#/components/schemas/AbuseConfig"),
		withResponse("ok", "#/components/schemas/AbuseConfig"))

	addPost(paths, "/api/abuse-preset/{preset}", "abuse_preset_apply", "Apply emergency preset (normal/cautious/lockdown)", "Local",
		withPathParam("preset", "string", "Preset name: normal, cautious, lockdown"),
		withResponse("ok", "#/components/schemas/AbuseConfig"))

	addPost(paths, "/api/admin/abuse-config/reload", "abuse_config_reload", "Reload abuse config on api-service", "Local",
		withResponse("ok", "#/components/schemas/ReloadResponse"))

	addGet(paths, "/api/emergency-status", "emergency_status", "Get current emergency status", "Local",
		withResponse("ok", "#/components/schemas/EmergencyStatus"))

	addGet(paths, "/api/agents/{name}/abuse", "agent_abuse_get", "Get per-agent abuse overrides", "Local",
		withPathParam("name", "string", "Agent name"),
		withResponse("ok", "#/components/schemas/AgentAbuseResponse"))

	addPut(paths, "/api/agents/{name}/abuse", "agent_abuse_put", "Update per-agent abuse overrides", "Local",
		withPathParam("name", "string", "Agent name"),
		withRequestBody("#/components/schemas/AgentAbuseOverride"),
		withResponse("ok", "#/components/schemas/AgentAbuseResponse"))

	// ── Data-Service proxy ──
	addGet(paths, "/api/tenants", "tenants_list", "List all tenants", "Data-Service",
		withProxyTo("data-service"),
		withResponse("ok", "#/components/schemas/TenantListResponse"))

	addPost(paths, "/api/tenants", "tenants_create", "Create a new tenant (+ start introspection)", "Data-Service",
		withProxyTo("data-service"),
		withRequestBody("#/components/schemas/CreateTenantRequest"),
		withResponse("created", "#/components/schemas/TenantCreateResponse"))

	addGet(paths, "/api/tenants/{id}", "tenants_get", "Get tenant info", "Data-Service",
		withProxyTo("data-service"),
		withPathParam("id", "string", "Tenant ID"),
		withResponse("ok", "#/components/schemas/TenantResponse"))

	addDelete(paths, "/api/tenants/{id}", "tenants_delete", "Delete a tenant", "Data-Service",
		withProxyTo("data-service"),
		withPathParam("id", "string", "Tenant ID"),
		withResponse("ok", "#/components/schemas/StatusResponse"))

	addPost(paths, "/api/tenants/upload-sqlite", "tenants_upload_sqlite", "Upload SQLite file and register tenant", "Data-Service",
		withProxyTo("data-service"),
		withMultipartRequestBody([]string{"file", "tenant_id", "driver"}),
		withResponse("created", "#/components/schemas/TenantCreateResponse"))

	addGet(paths, "/api/tenants/{id}/config", "tenants_config_get", "Get tenant config", "Data-Service",
		withProxyTo("data-service"),
		withPathParam("id", "string", "Tenant ID"),
		withResponse("ok", "#/components/schemas/ConfigObject"))

	addPut(paths, "/api/tenants/{id}/config", "tenants_config_put", "Update tenant config", "Data-Service",
		withProxyTo("data-service"),
		withPathParam("id", "string", "Tenant ID"),
		withRequestBody("application/json"),
		withResponse("ok", "#/components/schemas/ConfigObject"))

	addPost(paths, "/api/tenants/{id}/introspect", "tenants_introspect", "Run introspection (rewrite config from DB schema)", "Data-Service",
		withProxyTo("data-service"),
		withPathParam("id", "string", "Tenant ID"),
		withResponse("ok", "#/components/schemas/RewriteResponse"))

	addGet(paths, "/api/tenants/{id}/tools/pending", "tenants_tools_pending", "List pending tools for approval", "Data-Service",
		withProxyTo("data-service"),
		withPathParam("id", "string", "Tenant ID"),
		withResponse("ok", "#/components/schemas/PendingToolsResponse"))

	addPost(paths, "/api/tenants/{id}/tools/{toolName}/approve", "tenants_tools_approve", "Approve a write tool", "Data-Service",
		withProxyTo("data-service"),
		withPathParam("id", "string", "Tenant ID"),
		withPathParam("toolName", "string", "Tool name to approve"),
		withResponse("ok", "#/components/schemas/StatusResponse"))

	addGet(paths, "/api/tenants/{id}/manifest", "tenants_manifest", "Get MCP manifest for tenant", "Data-Service",
		withProxyTo("data-service"),
		withPathParam("id", "string", "Tenant ID"),
		withResponse("ok", "#/components/schemas/ManifestResponse"))

	// ── API-Service proxy ──
	addGet(paths, "/api/agents", "agents_list", "List all agents", "API-Service",
		withProxyTo("api-service"),
		withResponse("ok", "#/components/schemas/AgentListResponse"))

	addPost(paths, "/api/agents", "agents_create", "Create a new agent", "API-Service",
		withProxyTo("api-service"),
		withRequestBody("#/components/schemas/CreateAgentRequest"),
		withResponse("created", "#/components/schemas/AgentResponse"))

	addGet(paths, "/api/agents/{name}", "agents_get", "Get agent by name", "API-Service",
		withProxyTo("api-service"),
		withPathParam("name", "string", "Agent name"),
		withResponse("ok", "#/components/schemas/AgentResponse"))

	addPut(paths, "/api/agents/{name}", "agents_update", "Update agent", "API-Service",
		withProxyTo("api-service"),
		withPathParam("name", "string", "Agent name"),
		withRequestBody("#/components/schemas/UpdateAgentRequest"),
		withResponse("ok", "#/components/schemas/AgentResponse"))

	addDelete(paths, "/api/agents/{name}", "agents_delete", "Delete agent", "API-Service",
		withProxyTo("api-service"),
		withPathParam("name", "string", "Agent name"),
		withResponse("no_content", "#/components/schemas/StatusResponse"))

	addGet(paths, "/api/llm-config", "llm_config_get", "Get LLM fallback configuration", "API-Service",
		withProxyTo("api-service"),
		withResponse("ok", "#/components/schemas/LlmConfig"))

	addGet(paths, "/api/llm-providers", "llm_providers_list", "List all LLM providers (detailed)", "API-Service",
		withProxyTo("api-service"),
		withResponse("ok", "#/components/schemas/LlmProviderList"))

	addPost(paths, "/api/llm-providers", "llm_providers_add", "Add a new LLM provider", "API-Service",
		withProxyTo("api-service"),
		withRequestBody("#/components/schemas/LlmProviderAddRequest"),
		withResponse("created", "#/components/schemas/LlmProvider"))

	addGet(paths, "/api/llm-providers/{name}", "llm_providers_get", "Get LLM provider by name", "API-Service",
		withProxyTo("api-service"),
		withPathParam("name", "string", "Provider name"),
		withResponse("ok", "#/components/schemas/LlmProvider"))

	addPut(paths, "/api/llm-providers/{name}", "llm_providers_update", "Update LLM provider", "API-Service",
		withProxyTo("api-service"),
		withPathParam("name", "string", "Provider name"),
		withRequestBody("#/components/schemas/LlmProviderUpdateRequest"),
		withResponse("ok", "#/components/schemas/LlmProvider"))

	addDelete(paths, "/api/llm-providers/{name}", "llm_providers_delete", "Delete LLM provider", "API-Service",
		withProxyTo("api-service"),
		withPathParam("name", "string", "Provider name"),
		withResponse("ok", "#/components/schemas/StatusResponse"))

	addPost(paths, "/api/llm-providers/{name}/toggle", "llm_providers_toggle", "Toggle LLM provider on/off", "API-Service",
		withProxyTo("api-service"),
		withPathParam("name", "string", "Provider name"),
		withResponse("ok", "#/components/schemas/LlmProvider"))

	addGet(paths, "/api/llm-provider-list", "llm_provider_list", "List available providers from LiteLLM", "API-Service",
		withProxyTo("api-service"),
		withResponse("ok", "#/components/schemas/ProviderListResponse"))

	addGet(paths, "/api/voice-config", "voice_config_get", "Get STT/TTS voice configuration", "API-Service",
		withProxyTo("api-service"),
		withResponse("ok", "#/components/schemas/VoiceConfig"))

	addPut(paths, "/api/voice-config", "voice_config_put", "Update STT/TTS voice configuration", "API-Service",
		withProxyTo("api-service"),
		withRequestBody("#/components/schemas/VoiceConfig"),
		withResponse("ok", "#/components/schemas/VoiceConfig"))

	addSsePost(paths, "/api/chat/voice", "chat_voice", "Voice chat (multipart audio → SSE stream)", "API-Service",
		withProxyTo("api-service"),
		withMultipartRequestBody([]string{"audio", "session_id", "agent", "lang"}))

	// ── RAG proxy ──
	addGet(paths, "/api/rag/health", "rag_health", "RAG service health check", "RAG",
		withProxyTo("rag-service"),
		withResponse("ok", "#/components/schemas/RagHealthResponse"))

	addGet(paths, "/api/rag/config", "rag_config_get", "Get RAG configuration", "RAG",
		withProxyTo("rag-service"),
		withResponse("ok", "#/components/schemas/RagConfig"))

	addPut(paths, "/api/rag/config", "rag_config_put", "Update RAG configuration", "RAG",
		withProxyTo("rag-service"),
		withRequestBody("#/components/schemas/RagConfig"),
		withResponse("ok", "#/components/schemas/RagConfig"))

	addGet(paths, "/api/rag/stats", "rag_stats", "Get RAG statistics", "RAG",
		withProxyTo("rag-service"),
		withResponse("ok", "#/components/schemas/RagStats"))

	addPost(paths, "/api/rag/documents/list", "rag_documents_list", "List RAG documents", "RAG",
		withProxyTo("rag-service"),
		withRequestBody("#/components/schemas/RagDocListRequest"),
		withResponse("ok", "#/components/schemas/RagDocList"))

	addPost(paths, "/api/rag/documents/import", "rag_documents_import", "Import document from file path", "RAG",
		withProxyTo("rag-service"),
		withRequestBody("#/components/schemas/RagDocImportRequest"),
		withResponse("ok", "#/components/schemas/RagDocImportResponse"))

	addPost(paths, "/api/rag/documents/upload", "rag_documents_upload", "Upload document (multipart)", "RAG",
		withProxyTo("rag-service"),
		withMultipartRequestBody([]string{"file", "title", "discipline_id"}),
		withResponse("ok", "#/components/schemas/RagDocImportResponse"))

	addPost(paths, "/api/rag/documents/delete", "rag_documents_delete", "Delete RAG document", "RAG",
		withProxyTo("rag-service"),
		withRequestBody("#/components/schemas/RagDocDeleteRequest"),
		withResponse("ok", "#/components/schemas/StatusResponse"))

	return paths
}

// ── Components ─────────────────────────────────────────────────────────────

func buildComponents() map[string]any {
	return map[string]any{
		"schemas":         buildSchemas(),
		"securitySchemes": buildSecuritySchemes(),
	}
}

func buildSchemas() map[string]any {
	return map[string]any{
		// ── System ──
		"ErrorResponse": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"error":   map[string]any{"type": "string", "description": "Код ошибки"},
				"message": map[string]any{"type": "string", "description": "Описание ошибки"},
			},
		},
		"HealthResponse": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"status": map[string]any{"type": "string", "example": "ok"},
			},
		},
		"OpenAPISpec": map[string]any{
			"type":       "object",
			"properties": map[string]any{
				"openapi": map[string]any{"type": "string"},
				"info":    map[string]any{"type": "object"},
				"paths":   map[string]any{"type": "object"},
			},
			"description": "OpenAPI 3.1.0 спецификация admin-dashboard",
		},
		"StatusResponse": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"status":  map[string]any{"type": "string"},
				"message": map[string]any{"type": "string"},
			},
		},
		"ReloadResponse": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"status":  map[string]any{"type": "string", "example": "reload_triggered"},
				"message": map[string]any{"type": "string"},
			},
		},

		// ── Dashboard & DB ──
		"DashboardResponse": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"tenants":      map[string]any{"type": "array", "items": map[string]any{"$ref": "#/components/schemas/TenantInfo"}},
				"tenant_count": map[string]any{"type": "integer"},
				"data_service": map[string]any{"type": "string"},
				"role":         map[string]any{"type": "string", "description": "admin | viewer"},
			},
		},
		"TenantInfo": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"id": map[string]any{"type": "string"},
			},
		},
		"DbTestRequest": map[string]any{
			"type":     "object",
			"required": []string{"driver", "dsn"},
			"properties": map[string]any{
				"driver": map[string]any{"type": "string", "description": "sqlite | postgres"},
				"dsn":    map[string]any{"type": "string", "description": "Data source name / connection string"},
			},
		},
		"DbTestResponse": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"status":  map[string]any{"type": "string"},
				"message": map[string]any{"type": "string"},
				"driver":  map[string]any{"type": "string"},
				"dsn":     map[string]any{"type": "string"},
			},
		},

		// ── Tenant CRUD ──
		"CreateTenantRequest": map[string]any{
			"type":     "object",
			"required": []string{"tenant_id"},
			"properties": map[string]any{
				"tenant_id": map[string]any{"type": "string", "description": "Уникальный ID тенанта"},
				"driver":    map[string]any{"type": "string", "description": "sqlite | sqlite3 | postgres", "default": "sqlite"},
				"dsn":       map[string]any{"type": "string", "description": "Connection string или путь к SQLite"},
			},
		},
		"TenantCreateResponse": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"id":     map[string]any{"type": "string"},
				"config": map[string]any{"type": "object"},
			},
		},
		"TenantListResponse": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"tenants": map[string]any{
					"type":  "array",
					"items": map[string]any{"$ref": "#/components/schemas/TenantInfo"},
				},
			},
		},
		"TenantResponse": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"id":         map[string]any{"type": "string"},
				"driver":     map[string]any{"type": "string"},
				"entities":   map[string]any{"type": "integer"},
				"endpoints":  map[string]any{"type": "integer"},
				"healthy":    map[string]any{"type": "boolean"},
				"error":      map[string]any{"type": "string"},
				"created_at": map[string]any{"type": "string", "format": "date-time"},
			},
		},

		// ── Config ──
		"ConfigObject": map[string]any{
			"type":       "object",
			"properties": map[string]any{
				"version":   map[string]any{"type": "integer"},
				"data_source": map[string]any{
					"type": "object",
					"properties": map[string]any{
						"driver":    map[string]any{"type": "string"},
						"dsn":       map[string]any{"type": "string"},
						"read_only": map[string]any{"type": "boolean"},
					},
				},
				"entities":  map[string]any{"type": "array", "items": map[string]any{"type": "object"}},
				"endpoints": map[string]any{"type": "array", "items": map[string]any{"type": "object"}},
				"mcp_tools": map[string]any{"type": "array", "items": map[string]any{"type": "object"}},
			},
		},
		"RewriteResponse": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"status":    map[string]any{"type": "string"},
				"path":      map[string]any{"type": "string"},
				"entities":  map[string]any{"type": "integer"},
				"endpoints": map[string]any{"type": "integer"},
				"note":      map[string]any{"type": "string"},
			},
		},

		// ── Tools / Manifest ──
		"PendingToolsResponse": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"tools": map[string]any{
					"type": "array",
					"items": map[string]any{
						"type": "object",
						"properties": map[string]any{
							"name":      map[string]any{"type": "string"},
							"approved":  map[string]any{"type": "boolean"},
							"method":    map[string]any{"type": "string"},
							"path":      map[string]any{"type": "string"},
						},
					},
				},
				"mode":      map[string]any{"type": "string", "description": "read_only | read_write"},
			},
		},
		"ManifestResponse": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"endpoints": map[string]any{"type": "array", "items": map[string]any{"type": "object"}},
				"mcp_tools": map[string]any{"type": "array", "items": map[string]any{"type": "object"}},
			},
		},

		// ── Audit ──
		"AuditListResponse": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"entries": map[string]any{
					"type": "array",
					"items": map[string]any{
						"type": "object",
						"properties": map[string]any{
							"timestamp": map[string]any{"type": "string", "format": "date-time"},
							"actor_role": map[string]any{"type": "string"},
							"action":    map[string]any{"type": "string"},
							"resource":  map[string]any{"type": "string"},
							"details":   map[string]any{"type": "string"},
						},
					},
				},
				"count": map[string]any{"type": "integer"},
			},
		},

		// ── Abuse / Emergency ──
		"AbuseConfig": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"rps":                      map[string]any{"type": "number", "description": "Requests per second"},
				"burst":                    map[string]any{"type": "integer"},
				"max_message_length":       map[string]any{"type": "integer"},
				"min_interval_ms":          map[string]any{"type": "integer"},
				"max_messages_per_session": map[string]any{"type": "integer"},
				"block_empty_user_agent":   map[string]any{"type": "boolean"},
				"blocked_user_agents":      map[string]any{"type": "array", "items": map[string]any{"type": "string"}},
				"emergency_mode":           map[string]any{"type": "boolean"},
				"token_budget":             map[string]any{"type": "integer"},
				"emergency_preset":         map[string]any{"type": "string"},
			},
		},
		"EmergencyStatus": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"emergency_mode":   map[string]any{"type": "boolean"},
				"emergency_preset": map[string]any{"type": "string"},
				"rps":              map[string]any{"type": "number"},
				"burst":            map[string]any{"type": "integer"},
				"token_budget":     map[string]any{"type": "integer"},
				"max_messages":     map[string]any{"type": "integer"},
				"min_interval_ms":  map[string]any{"type": "integer"},
				"active":           map[string]any{"type": "boolean"},
			},
		},
		"AgentAbuseOverride": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"rps":                      map[string]any{"type": "number"},
				"burst":                    map[string]any{"type": "integer"},
				"max_message_length":       map[string]any{"type": "integer"},
				"min_interval_ms":          map[string]any{"type": "integer"},
				"max_messages_per_session": map[string]any{"type": "integer"},
				"block_empty_user_agent":   map[string]any{"type": "boolean"},
				"blocked_user_agents":      map[string]any{"type": "array", "items": map[string]any{"type": "string"}},
			},
		},
		"AgentAbuseResponse": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"agent":       map[string]any{"type": "object"},
				"abuse_config": map[string]any{"$ref": "#/components/schemas/AgentAbuseOverride"},
			},
		},

		// ── Agents ──
		"AgentListResponse": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"agents": map[string]any{
					"type": "array",
					"items": map[string]any{"$ref": "#/components/schemas/AgentResponse"},
				},
			},
		},
		"CreateAgentRequest": map[string]any{
			"type":     "object",
			"required": []string{"name"},
			"properties": map[string]any{
				"name":             map[string]any{"type": "string", "pattern": "^[a-z][a-z0-9_-]*$"},
				"description":      map[string]any{"type": "string"},
				"tenant_ids":       map[string]any{"type": "array", "items": map[string]any{"type": "string"}},
				"provider_priority": map[string]any{"type": "array", "items": map[string]any{"type": "string"}},
				"system_prompt":    map[string]any{"type": "string"},
			},
		},
		"UpdateAgentRequest": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"description":      map[string]any{"type": "string"},
				"tenant_ids":       map[string]any{"type": "array", "items": map[string]any{"type": "string"}},
				"provider_priority": map[string]any{"type": "array", "items": map[string]any{"type": "string"}},
				"system_prompt":    map[string]any{"type": "string"},
				"voice_config":     map[string]any{"type": "object"},
			},
		},
		"AgentResponse": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"name":              map[string]any{"type": "string"},
				"description":       map[string]any{"type": "string"},
				"tenant_ids":        map[string]any{"type": "array", "items": map[string]any{"type": "string"}},
				"provider_priority": map[string]any{"type": "array", "items": map[string]any{"type": "string"}},
				"system_prompt":     map[string]any{"type": "string"},
				"voice_config":      map[string]any{"type": "object"},
				"created_at":        map[string]any{"type": "string", "format": "date-time"},
				"updated_at":        map[string]any{"type": "string", "format": "date-time"},
			},
		},

		// ── LLM ──
		"LlmConfig": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"providers": map[string]any{
					"type": "array",
					"items": map[string]any{
						"type": "object",
						"properties": map[string]any{
							"name":    map[string]any{"type": "string"},
							"model":   map[string]any{"type": "string"},
							"enabled": map[string]any{"type": "boolean"},
						},
					},
				},
				"fallback_enabled": map[string]any{"type": "boolean"},
				"num_models":       map[string]any{"type": "integer"},
			},
		},
		"LlmProviderList": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"providers": map[string]any{
					"type": "array",
					"items": map[string]any{"$ref": "#/components/schemas/LlmProvider"},
				},
			},
		},
		"LlmProviderAddRequest": map[string]any{
			"type":     "object",
			"required": []string{"name", "model"},
			"properties": map[string]any{
				"name":      map[string]any{"type": "string"},
				"model":     map[string]any{"type": "string"},
				"provider":  map[string]any{"type": "string"},
				"api_key":   map[string]any{"type": "string"},
				"api_base":  map[string]any{"type": "string"},
				"enabled":   map[string]any{"type": "boolean", "default": true},
			},
		},
		"LlmProviderUpdateRequest": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"model":    map[string]any{"type": "string"},
				"provider": map[string]any{"type": "string"},
				"api_key":  map[string]any{"type": "string"},
				"api_base": map[string]any{"type": "string"},
				"enabled":  map[string]any{"type": "boolean"},
			},
		},
		"LlmProvider": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"name":           map[string]any{"type": "string"},
				"model":          map[string]any{"type": "string"},
				"provider":       map[string]any{"type": "string"},
				"api_base":       map[string]any{"type": "string"},
				"enabled":        map[string]any{"type": "boolean"},
				"has_api_key":    map[string]any{"type": "boolean"},
				"api_key_masked": map[string]any{"type": "string"},
			},
		},
		"ProviderListResponse": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"providers": map[string]any{
					"type": "array",
					"items": map[string]any{
						"type": "object",
						"properties": map[string]any{
							"name":     map[string]any{"type": "string"},
							"provider": map[string]any{"type": "string"},
						},
					},
				},
			},
		},

		// ── Voice ──
		"VoiceConfig": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"enabled":                   map[string]any{"type": "boolean"},
				"stt_providers":              map[string]any{"type": "array", "items": map[string]any{"type": "object"}},
				"tts_providers":              map[string]any{"type": "array", "items": map[string]any{"type": "object"}},
				"stt_fallback_enabled":       map[string]any{"type": "boolean"},
				"tts_fallback_enabled":       map[string]any{"type": "boolean"},
				"max_voice_message_size":     map[string]any{"type": "integer"},
				"min_voice_interval_seconds": map[string]any{"type": "integer"},
				"max_voice_duration_seconds": map[string]any{"type": "integer"},
			},
		},

		// ── RAG ──
		"RagHealthResponse": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"status":    map[string]any{"type": "string"},
				"available": map[string]any{"type": "boolean"},
				"warning":   map[string]any{"type": "string"},
				"embedding": map[string]any{"type": "object"},
			},
		},
		"RagConfig": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"embedding_provider": map[string]any{"type": "string"},
				"embedding_model":    map[string]any{"type": "string"},
				"chunk_size":         map[string]any{"type": "integer"},
				"chunk_overlap":      map[string]any{"type": "integer"},
				"cache_enabled":      map[string]any{"type": "boolean"},
				"cache_ttl":          map[string]any{"type": "integer"},
			},
		},
		"RagStats": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"document_count": map[string]any{"type": "integer"},
				"chunk_count":    map[string]any{"type": "integer"},
				"chroma_size_mb": map[string]any{"type": "number"},
			},
		},
		"RagDocListRequest": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"discipline_id": map[string]any{"type": "string"},
			},
		},
		"RagDocList": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"documents": map[string]any{
					"type": "array",
					"items": map[string]any{
						"type": "object",
						"properties": map[string]any{
							"id":           map[string]any{"type": "string"},
							"title":        map[string]any{"type": "string"},
							"filename":     map[string]any{"type": "string"},
							"chunks_count": map[string]any{"type": "integer"},
						},
					},
				},
			},
		},
		"RagDocImportRequest": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"path":          map[string]any{"type": "string"},
				"discipline_id": map[string]any{"type": "string"},
				"title":         map[string]any{"type": "string"},
			},
		},
		"RagDocImportResponse": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"status":  map[string]any{"type": "string"},
				"message": map[string]any{"type": "string"},
				"doc_id":  map[string]any{"type": "string"},
			},
		},
		"RagDocDeleteRequest": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"path": map[string]any{"type": "string"},
				"id":   map[string]any{"type": "string"},
			},
		},
	}
}

func buildSecuritySchemes() map[string]any {
	return map[string]any{
		"BearerAuth": map[string]any{
			"type":         "http",
			"scheme":       "bearer",
			"bearerFormat": "JWT",
			"description":  "ADMIN_TOKEN или VIEWER_TOKEN из .env. Reader/viewer — только GET на /api/*.",
		},
	}
}

// ── Helpers ────────────────────────────────────────────────────────────────

type pathOption func(map[string]any)

func withResponse(httpStatus, schemaRef string) pathOption {
	return func(op map[string]any) {
		statusCode := mapStatus(httpStatus)
		content := map[string]any{
			"application/json": map[string]any{
				"schema": simpleRef(schemaRef),
			},
		}
		resp := map[string]any{
			"description": describeStatus(httpStatus),
			"content":     content,
		}
		if httpStatus == "no_content" {
			resp = map[string]any{"description": "No Content"}
			delete(resp, "content")
		}
		op["responses"] = map[string]any{
			statusCode: resp,
		}
		// Добавляем стандартные ошибки для не-204
		if httpStatus != "no_content" {
			addDefaultErrors(op)
		}
	}
}

func withRequestBody(schemaRef string) pathOption {
	return func(op map[string]any) {
		contentType := "application/json"
		if schemaRef == "application/json" {
			op["requestBody"] = map[string]any{
				"required": true,
				"content": map[string]any{
					contentType: map[string]any{
						"schema": map[string]any{"type": "object"},
					},
				},
			}
			return
		}
		op["requestBody"] = map[string]any{
			"required": true,
			"content": map[string]any{
				contentType: map[string]any{
					"schema": simpleRef(schemaRef),
				},
			},
		}
	}
}

func withMultipartRequestBody(fields []string) pathOption {
	return func(op map[string]any) {
		props := make(map[string]any)
		required := make([]string, 0)
		for _, f := range fields {
			props[f] = map[string]any{
				"type": "string",
			}
			if f == "file" || f == "audio" || f == "tenant_id" || f == "name" {
				required = append(required, f)
			}
		}
		op["requestBody"] = map[string]any{
			"required": true,
			"content": map[string]any{
				"multipart/form-data": map[string]any{
					"schema": map[string]any{
						"type":       "object",
						"properties": props,
						"required":   required,
					},
				},
			},
		}
	}
}

func withPathParam(name, ptype, description string) pathOption {
	return func(op map[string]any) {
		param := map[string]any{
			"name":        name,
			"in":          "path",
			"required":    true,
			"schema":      map[string]any{"type": ptype},
			"description": description,
		}
		existing, _ := op["parameters"].([]map[string]any)
		op["parameters"] = append(existing, param)
	}
}

func withQueryParam(name, ptype, description string, required bool) pathOption {
	return func(op map[string]any) {
		param := map[string]any{
			"name":        name,
			"in":          "query",
			"required":    required,
			"schema":      map[string]any{"type": ptype},
			"description": description,
		}
		existing, _ := op["parameters"].([]map[string]any)
		op["parameters"] = append(existing, param)
	}
}

func withProxyTo(service string) pathOption {
	return func(op map[string]any) {
		op["x-proxy-to"] = service
		op["x-route-group"] = service
	}
}

func withNoAuth() pathOption {
	return func(op map[string]any) {
		op["x-auth-required"] = false
		op["security"] = []map[string]any{}
	}
}

// addGet добавляет GET path+operation в paths.
func addGet(paths map[string]any, path, operationID, summary, tag string, opts ...pathOption) {
	addMethod(paths, "get", path, operationID, summary, tag, opts...)
}

// addPost добавляет POST path+operation в paths.
func addPost(paths map[string]any, path, operationID, summary, tag string, opts ...pathOption) {
	addMethod(paths, "post", path, operationID, summary, tag, opts...)
}

// addPut добавляет PUT path+operation в paths.
func addPut(paths map[string]any, path, operationID, summary, tag string, opts ...pathOption) {
	addMethod(paths, "put", path, operationID, summary, tag, opts...)
}

// addDelete добавляет DELETE path+operation в paths.
func addDelete(paths map[string]any, path, operationID, summary, tag string, opts ...pathOption) {
	addMethod(paths, "delete", path, operationID, summary, tag, opts...)
}

// addSsePost добавляет POST path с x-streaming: true.
func addSsePost(paths map[string]any, path, operationID, summary, tag string, opts ...pathOption) {
	addMethod(paths, "post", path, operationID, summary, tag, append(opts, func(op map[string]any) {
		op["x-streaming"] = true
		op["responses"] = map[string]any{
			"200": map[string]any{
				"description": "SSE stream (text/event-stream)",
				"content": map[string]any{
					"text/event-stream": map[string]any{
						"schema": map[string]any{
							"type": "string",
							"description": "Server-Sent Events поток с событиями: token, tool_call, tool_result, final, done, error, audio",
						},
					},
				},
			},
		}
	})...)
}

// addMethod — общая функция для добавления метода к path.
func addMethod(paths map[string]any, method, path, operationID, summary, tag string, opts ...pathOption) {
	operation := map[string]any{
		"summary":     summary,
		"operationId": operationID,
		"tags":        []string{tag},
		"parameters":  make([]map[string]any, 0),
	}

	// Route group из tag, если не переопределён x-route-group
	if _, exists := operation["x-route-group"]; !exists {
		operation["x-route-group"] = tag
	}

	// Default: auth required (кроме /health, /api/health, /openapi.json etc)
	if !strings.HasPrefix(path, "/health") && path != "/api/health" && path != "/openapi.json" {
		operation["x-auth-required"] = true
	}

	// Применяем опции
	for _, opt := range opts {
		opt(operation)
	}

	// Убираем пустой parameters (если ни один параметр не добавлен, а был создан пустой слайс)
	if params, ok := operation["parameters"].([]map[string]any); ok && len(params) == 0 {
		delete(operation, "parameters")
	}

	// Инициализируем path, если его нет
	if _, ok := paths[path]; !ok {
		paths[path] = make(map[string]any)
	}
	paths[path].(map[string]any)[method] = operation
}

func addDefaultErrors(op map[string]any) {
	respMap, _ := op["responses"].(map[string]any)
	if respMap == nil {
		respMap = make(map[string]any)
	}
	// Добавляем 401 и 500 если их нет
	if _, ok := respMap["401"]; !ok {
		respMap["401"] = map[string]any{
			"description": "Unauthorized — неверный или отсутствующий Bearer токен",
			"content": map[string]any{
				"application/json": map[string]any{
					"schema": simpleRef("#/components/schemas/ErrorResponse"),
				},
			},
		}
	}
	if _, ok := respMap["500"]; !ok {
		respMap["500"] = map[string]any{
			"description": "Внутренняя ошибка сервера",
			"content": map[string]any{
				"application/json": map[string]any{
					"schema": simpleRef("#/components/schemas/ErrorResponse"),
				},
			},
		}
	}
	op["responses"] = respMap
}

// ── Utils ──────────────────────────────────────────────────────────────────

func simpleRef(ref string) map[string]any {
	return map[string]any{"$ref": ref}
}

func mapStatus(s string) string {
	switch s {
	case "ok":
		return "200"
	case "created":
		return "201"
	case "no_content":
		return "204"
	default:
		return "200"
	}
}

func describeStatus(s string) string {
	switch s {
	case "ok":
		return "Успешный ответ"
	case "created":
		return "Создано"
	case "no_content":
		return "Нет содержимого"
	default:
		return "Ответ"
	}
}
