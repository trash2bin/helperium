package handlers

import (
	"net/http"

	"github.com/agent-tutor/agent-tutor-go/config"
	"github.com/agent-tutor/data-service/internal/configgen"
)

// MCPManifestHandler возвращает манифест MCP-инструментов,
// сформированный из конфига data-service — единственный source of truth.
//
// MCPTools генерируются runtime из эндпоинтов через configgen.GenerateMCPTools,
// чтобы не зависеть от того, есть ли mcp_tools в дисковом config.json.
//
// mcp-gateway вызывает этот эндпоинт при старте вместо того,
// чтобы парсить config.json самостоятельно.
func MCPManifestHandler(cfg *config.Config) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		// Runtime-генерация MCPTools — всегда актуальны, не зависят от дискового config.json.
		tools := cfg.MCPTools
		if len(tools) == 0 {
			tools = configgen.GenerateMCPTools(cfg.Endpoints)
		}

		RespondJSON(w, http.StatusOK, map[string]any{
			"endpoints":      cfg.Endpoints,
			"entities":       cfg.Entities,
			"custom_queries": cfg.CustomQueries,
			"mcp_tools":      tools,
		})
	}
}
