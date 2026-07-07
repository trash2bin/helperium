// Package tools provides MCP tool registration and invocation.
//
// Tools are auto-generated from config endpoints with optional overrides
// from explicit mcp_tools in the config file.
package tools

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"regexp"
	"sort"
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
	tenantID  string // "" for single-tenant (no prefix), "tenant-a" for composite mode
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
	return newRegistry(cfg, "")
}

// NewPrefixedRegistry creates a registry with a tenant prefix for composite multi-tenant mode.
// Tools will be registered as "{tenantID}__{toolName}" instead of "{toolName}".
func NewPrefixedRegistry(cfg *config.Config, tenantID string) *Registry {
	return newRegistry(cfg, tenantID)
}

func newRegistry(cfg *config.Config, tenantID string) *Registry {
	r := &Registry{
		cfg:       cfg,
		client:    httpclient.New(),
		ragClient: ragclient.New(),
		tenantID:  tenantID,
	}
	if cfg != nil {
		r.buildTools()
	}
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
	auto := make(map[string]toolDef)

	// Always auto-generate builtin tools (health, stats)
	for _, ep := range r.cfg.Endpoints {
		if ep.Op == config.OpBuiltinHealth || ep.Op == config.OpBuiltinStats {
			td := endpointToToolDef(ep, r.cfg.Entities, r.cfg.CustomQueries)
			if td.Name != "" {
				auto[td.Name] = td
			}
		}
	}

	// Prefer explicit mcp_tools from config (data-service manifest) if available.
	// They carry richer descriptions + params. Skip auto-generation for data tools
	// to avoid duplicates with the explicit ones.
	if len(r.cfg.MCPTools) > 0 {
		for _, mt := range r.cfg.MCPTools {
			auto[mt.Name] = toolDef{
				Name:        mt.Name,
				Endpoint:    mt.Endpoint,
				Description: mt.Description,
				Params:      mt.Params,
			}
		}
	} else {
		// Fallback: auto-generate from endpoints (legacy, no manifest mcp_tools)
		for _, ep := range r.cfg.Endpoints {
			if ep.Op == config.OpBuiltinHealth || ep.Op == config.OpBuiltinStats {
				continue
			}
			td := endpointToToolDef(ep, r.cfg.Entities, r.cfg.CustomQueries)
			if td.Name != "" {
				auto[td.Name] = td
			}
		}
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
	sort.Slice(r.toolDefs, func(i, j int) bool {
		return r.toolDefs[i].Name < r.toolDefs[j].Name
	})
}

// RegisterAll registers all tools on the MCP server.
// In composite mode (tenantID != ""), tool names are prefixed with "{tenantID}__".
func (r *Registry) RegisterAll(mcpServer *server.MCPServer) {
	for _, td := range r.toolDefs {
		name := td.Name
		if r.tenantID != "" {
			name = r.tenantID + "__" + name
		}
		registerOne(mcpServer, td, r.client, name, r.tenantID)
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
// name is the tool name (may be prefixed in composite mode).
// tenantID is the tenant for this tool ("" for single-tenant mode).
func registerOne(mcpServer *server.MCPServer, td toolDef, client *httpclient.Client, name string, tenantID string) {
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

	tool := mcp.NewTool(name, opts...)
	mcpServer.AddTool(tool, makeHandler(td, client, tenantID))
}

// makeHandler creates a handler that delegates to data-service via HTTP.
// In composite mode (tenantID != ""), the tenant is hard-coded into the closure.
// In single-tenant mode (tenantID == ""), tenantID is read from request context.
func makeHandler(td toolDef, client *httpclient.Client, tenantID string) server.ToolHandlerFunc {
	return func(ctx context.Context, request mcp.CallToolRequest) (*mcp.CallToolResult, error) {
		// 1. Resolve tenantID: from closure (composite) or from context (legacy)
		actualTenantID := tenantID
		if actualTenantID == "" {
			actualTenantID, _ = ctx.Value(httpclient.TenantIDKey).(string)
		}
		slog.Info("Tool call", "tool", td.Name, "tenantID", actualTenantID)

		// 2. Fetch current manifest for this tenant to resolve the endpoint
		cfg, err := client.FetchConfigWithTenant(actualTenantID)
		if err != nil {
			return mcp.NewToolResultError(fmt.Sprintf("failed to resolve tenant config: %v", err)), nil
		}

		// 3. Dynamic endpoint resolution: find the path that corresponds to this tool name
		endpoint := td.Endpoint
		for _, ep := range cfg.Endpoints {
			if deriveToolName(ep) == td.Name {
				endpoint = ep.Path
				break
			}
		}

		args := make(map[string]any)
		if request.Params.Arguments != nil {
			for k, v := range request.Params.Arguments {
				args[k] = v
			}
		}

		// 4. Inject tenantID into context for httpclient
		ctx = context.WithValue(ctx, httpclient.TenantIDKey, actualTenantID)

		result, err := client.Call(ctx, endpoint, args)
		if err != nil {
			slog.Error("Data-service call failed", "endpoint", endpoint, "error", err)
			return mcp.NewToolResultError(fmt.Sprintf("error calling %s: %v", endpoint, err)), nil
		}
		if result == nil {
			slog.Warn("Data-service returned null result", "endpoint", endpoint)
			return mcp.NewToolResultText("No data found"), nil
		}

		data, err := json.Marshal(result)
		if err != nil {
			slog.Error("Error formatting result", "error", err)
			return mcp.NewToolResultError(fmt.Sprintf("error formatting result: %v", err)), nil
		}

		resText := string(data)
		slog.Info("Sending tool result to agent", "tool", td.Name, "content", resText)
		return mcp.NewToolResultText(resText), nil
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

// deriveToolName generates a valid MCP tool name from endpoint metadata.
// Sanitises path-derived names: removes { and } which are illegal in
// Mistral function names.
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
		// Sanitise: strip { and } — Mistral rejects these in function names.
		path := strings.Trim(ep.Path, "/")
		path = strings.ReplaceAll(path, "{", "")
		path = strings.ReplaceAll(path, "}", "")
		return strings.ReplaceAll(path, "/", "_")
	default:
		return ""
	}
}

// buildDefaultDesc generates an LLM-friendly conversational description.
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
		desc := fmt.Sprintf(
			"Возвращает данные о %s по его уникальному идентификатору. "+
				"Используйте, когда уже знаете ID нужной записи (например, из результатов find_%s или list_%s).",
			entityName, entityName, entityName)
		if entityDesc != "" {
			desc += fmt.Sprintf(" %s: %s", entityName, entityDesc)
		}
		return desc
	case config.OpFind:
		desc := fmt.Sprintf(
			"Позволяет найти %s по текстовому запросу.",
			entityName)
		if ep.SearchField != "" {
			desc += fmt.Sprintf(" Поиск производится по полю '%s'.", ep.SearchField)
		}
		desc += " Если параметр поиска не указан, возвращает полный список всех записей."
		if entityDesc != "" {
			desc += fmt.Sprintf(" %s: %s", entityName, entityDesc)
		}
		return desc
	case config.OpList:
		return fmt.Sprintf("Возвращает полный список всех %s.", entityName)
	case config.OpCustomQuery:
		if entityName != "" {
			return fmt.Sprintf("Выполняет пользовательский запрос: %s", entityName)
		}
		return "Выполняет пользовательский запрос"
	default:
		return fmt.Sprintf("Выполняет запрос %s", ep.Path)
	}
}

// deriveParams extracts parameters from endpoint path and entity fields,
// with LLM-friendly parameter descriptions.
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

	// 3. Build param for each path param — с понятной подсказкой для LLM
	for _, pp := range pathParams {
		fieldType := config.FieldTypeString
		fieldDesc := ""
		for _, f := range entityFields {
			if f.Name == pp || f.Column == pp {
				fieldType = f.Type
				if f.Description != "" {
					fieldDesc = f.Description
				} else {
					fieldDesc = fmt.Sprintf("Уникальный идентификатор %s", ep.Entity)
				}
				break
			}
		}
		if fieldDesc == "" {
			fieldDesc = fmt.Sprintf("Уникальный идентификатор %s", ep.Entity)
		}
		ptype := fieldTypeToParamType(fieldType)
		required := true
		params = append(params, config.EndpointParam{
			Name:        pp,
			In:          config.ParamInPath,
			Type:        ptype,
			Required:    &required,
			Description: fieldDesc,
		})
	}

	// 4. For find/list: add search query param — с явной подсказкой, что он опционален
	if ep.Op == config.OpFind || ep.Op == config.OpList {
		qp := ep.QueryParam
		if qp == "" {
			qp = ep.SearchField
		}
		if qp != "" {
			required := false
			desc := fmt.Sprintf("Текстовый запрос для поиска %s по имени. Если не указан, возвращаются все записи.", ep.Entity)
			if ep.SearchField != "" {
				desc = fmt.Sprintf("Текстовый запрос для поиска %s по полю '%s'. Если не указан, возвращаются все записи.",
					ep.Entity, ep.SearchField)
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
			paramName := m[1]
			out = append(out, paramName)
		}
	}
	return out
}

// isWriteMethod возвращает true для мутирующих HTTP-методов.
func isWriteMethod(method config.HTTPMethod) bool {
	switch method {
	case config.MethodPOST, config.MethodPUT, config.MethodPATCH, config.MethodDELETE:
		return true
	}
	return false
}

// isWriteTool проверяет, соответствует ли MCPTool write-методу.
func isWriteTool(mt config.MCPTool, endpoints []config.Endpoint) bool {
	for _, ep := range endpoints {
		if ep.Path == mt.Endpoint {
			return isWriteMethod(ep.Method)
		}
	}
	return false
}
