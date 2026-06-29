// Package tools provides MCP tool registration and invocation.
//
// Tools are auto-generated from config endpoints with optional overrides
// from explicit mcp_tools in the config file.
package tools

import (
	"context"
	"encoding/json"
	"fmt"
	"regexp"
	"strings"

	"github.com/agent-tutor/agent-tutor-go/config"
	"github.com/agent-tutor/mcp-gateway/internal/httpclient"
	"github.com/agent-tutor/mcp-gateway/internal/ragclient"
	"github.com/mark3labs/mcp-go/mcp"
	"github.com/mark3labs/mcp-go/server"
)

// Registry manages auto-generated + explicit MCP tools.
type Registry struct {
	cfg       *config.Config
	client    *httpclient.Client
	ragClient *ragclient.Client
	toolDefs  []toolDef
}

// toolDef — внутреннее описание одного MCP-инструмента.
type toolDef struct {
	Name        string
	Endpoint    string
	Description string
	Params      []config.EndpointParam
}

// NewRegistry creates a registry and auto-builds tool definitions from config.
// ragClient will be auto-initialised from the RAG_SERVICE_URL environment variable.
// If RAG is not available (nil client or health check fails), RAG tools are
// still registered but return a friendly error message at call time.
func NewRegistry(cfg *config.Config) *Registry {
	r := &Registry{
		cfg:       cfg,
		client:    httpclient.New(),
		ragClient: ragclient.New(),
	}
	r.buildTools()
	return r
}

// RagEnabled reports whether RAG tools are available.
func (r *Registry) RagEnabled() bool {
	return r.ragClient != nil && r.ragClient.IsAvailable()
}

// RagDisabledReason returns a human-readable explanation when RAG is unavailable.
func (r *Registry) RagDisabledReason() string {
	if r.ragClient == nil {
		return "RAG_SERVICE_URL not set"
	}
	if !r.ragClient.IsAvailable() {
		return fmt.Sprintf("RAG health check failed at %s", r.ragClient.BaseURL())
	}
	return ""
}

// toolDefCount returns total tool count including RAG tools.
func (r *Registry) toolDefCount() int {
	n := len(r.toolDefs)
	if r.RagEnabled() {
		n += 3 // search_documents, list_documents, get_rag_context
	}
	return n
}

// buildTools generates tool definitions from endpoints, overridden by explicit mcp_tools.
func (r *Registry) buildTools() {
	// Step 1: auto-generate from endpoints
	auto := make(map[string]toolDef)
	for _, ep := range r.cfg.Endpoints {
		td := endpointToToolDef(ep, r.cfg.Entities, r.cfg.CustomQueries)
		if td.Name != "" {
			auto[td.Name] = td
		}
	}

	// Step 2: apply explicit mcp_tools overrides
	for _, mt := range r.cfg.MCPTools {
		td := toolDef{
			Name:        mt.Name,
			Endpoint:    mt.Endpoint,
			Description: mt.Description,
			Params:      mt.Params,
		}
		auto[mt.Name] = td
	}

	// Step 3: collect into slice (deterministic order — stable iteration)
	r.toolDefs = make([]toolDef, 0, len(auto))
	seen := make(map[string]bool, len(auto))
	for _, td := range auto {
		if seen[td.Name] {
			continue
		}
		seen[td.Name] = true
		r.toolDefs = append(r.toolDefs, td)
	}
}

// RegisterAll registers all tools on the MCP server.
func (r *Registry) RegisterAll(mcpServer *server.MCPServer) {
	for _, td := range r.toolDefs {
		registerOne(mcpServer, td, r.client)
	}
	r.registerRagTools(mcpServer)
}

// registerRagTools registers static RAG tools (search_documents, list_documents,
// get_rag_context). Tools are always registered — if RAG is unavailable the
// handlers return a descriptive error.
func (r *Registry) registerRagTools(mcpServer *server.MCPServer) {
	// search_documents — семантический поиск по документам
	searchTool := mcp.NewTool(
		"search_documents",
		mcp.WithDescription("Поиск наиболее релевантных фрагментов загруженных документов (лекций, методичек) по текстовому запросу. Возвращает массив фрагментов с оценкой релевантности."),
		mcp.WithString("query", mcp.Required(), mcp.Description("Поисковый запрос — вопрос или ключевые слова")),
		mcp.WithString("discipline_id", mcp.Description("ID дисциплины для фильтрации (опционально)")),
		mcp.WithNumber("limit", mcp.Description("Максимум результатов (1-20, по умолчанию 5)")),
	)
	mcpServer.AddTool(searchTool, r.makeRagHandler("search"))

	// list_documents — список документов в RAG
	listTool := mcp.NewTool(
		"list_documents",
		mcp.WithDescription("Список документов, загруженных в базу знаний (лекции, методички, учебные материалы). Можно фильтровать по дисциплине."),
		mcp.WithString("discipline_id", mcp.Description("ID дисциплины для фильтрации (опционально)")),
	)
	mcpServer.AddTool(listTool, r.makeRagHandler("list"))

	// get_rag_context — готовый контекст для LLM
	contextTool := mcp.NewTool(
		"get_rag_context",
		mcp.WithDescription("Формирует готовую строку контекста из релевантных фрагментов документов для подстановки в ответ модели. Возвращает контекст и список источников."),
		mcp.WithString("query", mcp.Required(), mcp.Description("Вопрос пользователя для поиска релевантных фрагментов")),
		mcp.WithString("discipline_id", mcp.Description("ID дисциплины для фильтрации (опционально)")),
		mcp.WithNumber("limit", mcp.Description("Максимум фрагментов (1-20, по умолчанию 5)")),
	)
	mcpServer.AddTool(contextTool, r.makeRagHandler("context"))
}

// makeRagHandler creates a handler that delegates to RAG service via HTTP.
func (r *Registry) makeRagHandler(kind string) server.ToolHandlerFunc {
	return func(ctx context.Context, request mcp.CallToolRequest) (*mcp.CallToolResult, error) {
		if !r.RagEnabled() {
			return mcp.NewToolResultError(fmt.Sprintf("RAG недоступен: %s. Проверьте RAG_SERVICE_URL и запущен ли rag-сервис.", r.RagDisabledReason())), nil
		}

		args := request.Params.Arguments
		query, _ := args["query"].(string)
		disciplineID, _ := args["discipline_id"].(string)

		var limit int
		switch v := args["limit"].(type) {
		case float64:
			limit = int(v)
		case int:
			limit = v
		}

		switch kind {
		case "search":
			results, err := r.ragClient.SearchDocuments(query, disciplineID, limit)
			if err != nil {
				return mcp.NewToolResultError(fmt.Sprintf("Ошибка поиска: %v", err)), nil
			}
			data, err := json.MarshalIndent(results, "", "  ")
			if err != nil {
				return mcp.NewToolResultError(fmt.Sprintf("Ошибка форматирования: %v", err)), nil
			}
			return mcp.NewToolResultText(string(data)), nil

		case "list":
			docs, err := r.ragClient.ListDocuments(disciplineID, 0)
			if err != nil {
				return mcp.NewToolResultError(fmt.Sprintf("Ошибка получения списка: %v", err)), nil
			}
			data, err := json.MarshalIndent(docs, "", "  ")
			if err != nil {
				return mcp.NewToolResultError(fmt.Sprintf("Ошибка форматирования: %v", err)), nil
			}
			return mcp.NewToolResultText(string(data)), nil

		case "context":
			ctxResp, err := r.ragClient.GetRagContext(query, disciplineID, limit)
			if err != nil {
				return mcp.NewToolResultError(fmt.Sprintf("Ошибка сборки контекста: %v", err)), nil
			}
			data, err := json.MarshalIndent(ctxResp, "", "  ")
			if err != nil {
				return mcp.NewToolResultError(fmt.Sprintf("Ошибка форматирования: %v", err)), nil
			}
			return mcp.NewToolResultText(string(data)), nil

		default:
			return mcp.NewToolResultError(fmt.Sprintf("Неизвестная RAG-операция: %s", kind)), nil
		}
	}
}

// GetToolDefs returns tool definitions (for debug/inspection).
func (r *Registry) GetToolDefs() []toolDef {
	return r.toolDefs
}

// GetToolNames returns all tool names including RAG tools.
func (r *Registry) GetToolNames() []string {
	names := make([]string, 0, len(r.toolDefs)+3)
	for _, td := range r.toolDefs {
		names = append(names, td.Name)
	}
	if r.RagEnabled() {
		names = append(names, "search_documents", "list_documents", "get_rag_context")
	} else {
		names = append(names, "search_documents", "list_documents", "get_rag_context")
	}
	return names
}

// registerOne registers a single tool on the MCP server.
func registerOne(mcpServer *server.MCPServer, td toolDef, client *httpclient.Client) {
	desc := td.Description
	if desc == "" {
		desc = fmt.Sprintf("Call %s endpoint", td.Endpoint)
	}

	opts := []mcp.ToolOption{mcp.WithDescription(desc)}

	for _, p := range td.Params {
		propOpts := []mcp.PropertyOption{mcp.Description(p.Description)}
		if p.Required != nil && *p.Required {
			propOpts = append(propOpts, mcp.Required())
		}
		switch p.Type {
		case config.ParamTypeInt, config.ParamTypeFloat:
			opts = append(opts, mcp.WithNumber(p.Name, propOpts...))
		case config.ParamTypeBool:
			opts = append(opts, mcp.WithBoolean(p.Name, propOpts...))
		default:
			opts = append(opts, mcp.WithString(p.Name, propOpts...))
		}
	}

	tool := mcp.NewTool(td.Name, opts...)
	mcpServer.AddTool(tool, makeHandler(td, client))
}

// makeHandler creates a handler that delegates to data-service via HTTP.
func makeHandler(td toolDef, client *httpclient.Client) server.ToolHandlerFunc {
	return func(ctx context.Context, request mcp.CallToolRequest) (*mcp.CallToolResult, error) {
		args := make(map[string]any)
		if request.Params.Arguments != nil {
			for k, v := range request.Params.Arguments {
				args[k] = v
			}
		}

		result, err := client.Call(td.Endpoint, args)
		if err != nil {
			return mcp.NewToolResultError(fmt.Sprintf("error calling %s: %v", td.Endpoint, err)), nil
		}
		if result == nil {
			return mcp.NewToolResultText("null"), nil
		}

		data, err := json.MarshalIndent(result, "", "  ")
		if err != nil {
			return mcp.NewToolResultError(fmt.Sprintf("error formatting result: %v", err)), nil
		}
		return mcp.NewToolResultText(string(data)), nil
	}
}

// endpointToToolDef converts an endpoint config entry into a tool definition.
func endpointToToolDef(ep config.Endpoint, entities []config.Entity, customQueries map[string]config.CustomQuery) toolDef {
	name := deriveToolName(ep)
	if name == "" {
		return toolDef{}
	}

	desc := ep.Description
	if desc == "" {
		desc = buildDefaultDesc(ep, entities)
	}

	// Params: explicit > derived from path/entity
	params := ep.Params
	if len(params) == 0 {
		params = deriveParams(ep, entities)
	}

	return toolDef{
		Name:        name,
		Endpoint:    ep.Path,
		Description: desc,
		Params:      params,
	}
}

// deriveToolName generates a unique tool name from endpoint metadata.
func deriveToolName(ep config.Endpoint) string {
	switch ep.Op {
	case config.OpBuiltinHealth:
		return "health"
	case config.OpBuiltinStats:
		return "stats"
	}

	entityName := ep.Entity
	if entityName == "" {
		parts := strings.Split(strings.Trim(ep.Path, "/"), "/")
		if len(parts) > 0 {
			entityName = parts[0]
		}
	}
	if entityName == "" {
		return ""
	}

	switch ep.Op {
	case config.OpGetByID:
		return "get_" + entityName
	case config.OpFind:
		return "find_" + entityName
	case config.OpList:
		return "list_" + entityName
	case config.OpCustomQuery:
		if ep.QueryID != "" {
			return ep.QueryID
		}
		path := strings.Trim(ep.Path, "/")
		return strings.ReplaceAll(path, "/", "_")
	default:
		return ""
	}
}

// buildDefaultDesc generates a human-readable description for auto-generated tools.
func buildDefaultDesc(ep config.Endpoint, entities []config.Entity) string {
	entityName := ep.Entity
	if entityName == "" {
		parts := strings.Split(strings.Trim(ep.Path, "/"), "/")
		if len(parts) > 0 {
			entityName = parts[0]
		}
	}

	entityDesc := ""
	for _, e := range entities {
		if e.Name == entityName {
			entityDesc = e.Description
			break
		}
	}

	switch ep.Op {
	case config.OpGetByID:
		desc := "Get " + entityName + " by ID"
		if entityDesc != "" {
			desc += " (" + entityDesc + ")"
		}
		return desc
	case config.OpFind:
		desc := "Find " + entityName
		if ep.SearchField != "" {
			desc += " by " + ep.SearchField
		}
		if entityDesc != "" {
			desc += " (" + entityDesc + ")"
		}
		return desc
	case config.OpList:
		return "List all " + entityName
	case config.OpCustomQuery:
		return "Execute custom query: " + entityName
	default:
		return "Call " + ep.Path
	}
}

// deriveParams extracts parameters from endpoint path and entity fields.
func deriveParams(ep config.Endpoint, entities []config.Entity) []config.EndpointParam {
	params := make([]config.EndpointParam, 0)

	// 1. Extract path params from URL pattern
	pathParams := extractPathParams(ep.Path)

	// 2. Find entity fields for type info
	var entityFields []config.EntityField
	for _, e := range entities {
		if e.Name == ep.Entity {
			entityFields = e.Fields
			break
		}
	}

	// 3. Build param for each path param
	for _, pp := range pathParams {
		fieldType := config.FieldTypeString
		for _, f := range entityFields {
			if f.Name == pp || f.Column == pp {
				fieldType = f.Type
				break
			}
		}
		ptype := fieldTypeToParamType(fieldType)
		required := true
		params = append(params, config.EndpointParam{
			Name:        pp,
			In:          config.ParamInPath,
			Type:        ptype,
			Required:    &required,
			Description: pp,
		})
	}

	// 4. For find/list: add search query param
	if ep.Op == config.OpFind || ep.Op == config.OpList {
		qp := ep.QueryParam
		if qp == "" {
			qp = ep.SearchField
		}
		if qp != "" {
			required := false
			desc := "Search query"
			if ep.SearchField != "" {
				desc = "Search by " + ep.SearchField
			}
			params = append(params, config.EndpointParam{
				Name:        qp,
				In:          config.ParamInQuery,
				Type:        config.ParamTypeString,
				Required:    &required,
				Description: desc,
			})
		}
	}

	return params
}

// fieldTypeToParamType maps entity field types to MCP parameter types.
func fieldTypeToParamType(ft config.FieldType) config.ParamType {
	switch ft {
	case config.FieldTypeInt:
		return config.ParamTypeInt
	case config.FieldTypeFloat:
		return config.ParamTypeFloat
	case config.FieldTypeBool:
		return config.ParamTypeBool
	default:
		return config.ParamTypeString
	}
}

// pathParamRe matches {param_name} in URL path patterns.
var pathParamRe = regexp.MustCompile(`\{(\w+)\}`)

// extractPathParams extracts {param_name} from URL path patterns.
func extractPathParams(path string) []string {
	matches := pathParamRe.FindAllStringSubmatch(path, -1)
	out := make([]string, 0, len(matches))
	for _, m := range matches {
		if len(m) > 1 {
			out = append(out, m[1])
		}
	}
	return out
}
