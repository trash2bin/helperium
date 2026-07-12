package handlers

import (
	"net/http"

	"github.com/trash2bin/helperium/helperium-go/config"
	"github.com/trash2bin/helperium/data-service/internal/configgen"
)

// MCPManifestHandler возвращает манифест MCP-инструментов,
// сформированный из конфига data-service — единственный source of truth.
//
// MCPTools генерируются runtime из эндпоинтов через configgen.GenerateMCPTools,
// чтобы не зависеть от того, есть ли mcp_tools в дисковом config.json.
//
// mcp-gateway вызывает этот эндпоинт при старте вместо того,
// чтобы парсить config.json самостоятельно.
//
// Результат кэшируется — генерируем tools только один раз при старте сервиса.
func MCPManifestHandler(cfg *config.Config) http.HandlerFunc {
	// Предварительная генерация MCPTools — только один раз при старте
	tools := cfg.MCPTools
	if len(tools) == 0 {
		tools = configgen.GenerateMCPTools(cfg.Endpoints)
	}
	// Определяем read-only режим
	readOnly := cfg.DataSource.ReadOnly != nil && *cfg.DataSource.ReadOnly

	manifest := map[string]any{
		"endpoints":      cfg.Endpoints,
		"entities":       cfg.Entities,
		"custom_queries": cfg.CustomQueries,
		"mcp_tools":      tools,
		"read_only":      readOnly,
		"data_source": map[string]any{
			"read_only": readOnly,
		},
	}

	return func(w http.ResponseWriter, r *http.Request) {
		RespondJSON(w, http.StatusOK, manifest)
	}
}
