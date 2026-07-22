// Package tools provides MCP tool registration and invocation.
//
// Tools are auto-generated from config endpoints with optional overrides
// from explicit mcp_tools in the config file.
//
// HTTP routes called (through httpclient.Client and ragclient.Client):
//   data-service:GET /mcp/manifest  (via FetchConfigWithTenant, on every tool call)
//   data-service:GET /{endpoint}    (via client.Call, the actual data query)
//   rag:POST /search                (via ragClient.SearchDocuments)
//   rag:POST /documents/list        (via ragClient.ListDocuments)
//   rag:POST /context               (via ragClient.GetRagContext)
package tools

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"regexp"
	"sort"
	"strings"
	"time"

	"github.com/trash2bin/helperium/helperium-go/config"
	"github.com/trash2bin/helperium/mcp-gateway/internal/httpclient"
	"github.com/trash2bin/helperium/mcp-gateway/internal/ragclient"
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
	DisplayName string
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
				DisplayName: mt.DisplayName,
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
		mcp.WithDescription("Searches for relevant fragments in uploaded documents by text query. Returns an array of fragments with relevance score."),
		mcp.WithString("query", mcp.Required(), mcp.Description("Search query — question or keywords")),
		mcp.WithString("discipline_id", mcp.Description("Discipline ID for filtering (optional)")),
		mcp.WithNumber("limit", mcp.Description("Max results (1-20, default 5)")),
	)
	mcpServer.AddTool(searchTool, MakeAuditHandler("search_documents", r.tenantID, r.makeRagHandler("search")))

	// list_documents — список документов в RAG
	listTool := mcp.NewTool(
		"list_documents",
		mcp.WithDescription("Lists documents uploaded to the knowledge base. Can be filtered by discipline."),
		mcp.WithString("discipline_id", mcp.Description("Discipline ID for filtering (optional)")),
	)
	mcpServer.AddTool(listTool, MakeAuditHandler("list_documents", r.tenantID, r.makeRagHandler("list")))

	// get_rag_context — готовый контекст для LLM
	contextTool := mcp.NewTool(
		"get_rag_context",
		mcp.WithDescription("Builds a ready context string from relevant document fragments for the model response. Returns context and source list."),
		mcp.WithString("query", mcp.Required(), mcp.Description("User question for searching relevant fragments")),
		mcp.WithString("discipline_id", mcp.Description("Discipline ID for filtering (optional)")),
		mcp.WithNumber("limit", mcp.Description("Max fragments (1-20, default 5)")),
	)
	mcpServer.AddTool(contextTool, MakeAuditHandler("get_rag_context", r.tenantID, r.makeRagHandler("context")))
}



// makeRagHandler creates a handler that delegates to RAG service via HTTP.
func (r *Registry) makeRagHandler(kind string) server.ToolHandlerFunc {
	return func(ctx context.Context, request mcp.CallToolRequest) (*mcp.CallToolResult, error) {
		if !r.RagEnabled() {
			return mcp.NewToolResultError(fmt.Sprintf("RAG unavailable: %s. Check RAG_SERVICE_URL and ensure rag-service is running.", r.RagDisabledReason())), nil
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
				return mcp.NewToolResultError(fmt.Sprintf("Search error: %v", err)), nil
			}
			data, err := json.MarshalIndent(results, "", "  ")
			if err != nil {
				return mcp.NewToolResultError(fmt.Sprintf("Formatting error: %v", err)), nil
			}
			return mcp.NewToolResultText(string(data)), nil

		case "list":
			docs, err := r.ragClient.ListDocuments(disciplineID, 0)
			if err != nil {
				return mcp.NewToolResultError(fmt.Sprintf("List retrieval error: %v", err)), nil
			}
			data, err := json.MarshalIndent(docs, "", "  ")
			if err != nil {
				return mcp.NewToolResultError(fmt.Sprintf("Formatting error: %v", err)), nil
			}
			return mcp.NewToolResultText(string(data)), nil

		case "context":
			ctxResp, err := r.ragClient.GetRagContext(query, disciplineID, limit)
			if err != nil {
				return mcp.NewToolResultError(fmt.Sprintf("Context build error: %v", err)), nil
			}
			data, err := json.MarshalIndent(ctxResp, "", "  ")
			if err != nil {
				return mcp.NewToolResultError(fmt.Sprintf("Formatting error: %v", err)), nil
			}
			return mcp.NewToolResultText(string(data)), nil

		default:
			return mcp.NewToolResultError(fmt.Sprintf("Unknown RAG operation: %s", kind)), nil
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
	handler := makeHandler(td, client, tenantID)
	// Wrap with audit logging for every tool call
	mcpServer.AddTool(tool, MakeAuditHandler(name, tenantID, handler))
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

		// 2. The endpoint path is already resolved at tool registration time.
		//    (td.Endpoint is set when the tool definition is built from config.)
		//    No need to re-fetch the manifest for endpoint resolution.
		endpoint := td.Endpoint

		args := make(map[string]any)
		if request.Params.Arguments != nil {
			for k, v := range request.Params.Arguments {
				args[k] = v
			}
		}

		// 4. Validate tool arguments before forwarding to data-service.
		//    Prevents DoS/OOM via negative limits, excessive values, or long strings.
		if errs := validateArgs(args, td.Params); len(errs) > 0 {
			msgs := make([]string, len(errs))
			for i, e := range errs {
				msgs[i] = e.Error()
			}
			return mcp.NewToolResultError(fmt.Sprintf("argument validation failed: %s", strings.Join(msgs, "; "))), nil
		}

		// 5. Inject tenantID into context for httpclient
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

		resText := truncateResult(string(data), MaxResultSize)
		slog.Info("Sending tool result to agent", "tool", td.Name, "size", len(string(data)), "truncated", len(resText) < len(string(data)))
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
		DisplayName: "",
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
			"Returns data about %s by its unique identifier. "+
				"Use when you already know the record ID (e.g. from find_%s or list_%s).",
			entityName, entityName, entityName)
		if entityDesc != "" {
			desc += fmt.Sprintf(" %s: %s", entityName, entityDesc)
		}
		return desc
	case config.OpFind:
		desc := fmt.Sprintf(
			"Searches for %s by text query.",
			entityName)
		if ep.SearchField != "" {
			desc += fmt.Sprintf(" Search is done on the '%s' field.", ep.SearchField)
		}
		desc += " If no search parameter is provided, returns full list of all records."
		if entityDesc != "" {
			desc += fmt.Sprintf(" %s: %s", entityName, entityDesc)
		}
		return desc
	case config.OpList:
		return fmt.Sprintf("Returns full list of all %s.", entityName)
	case config.OpCustomQuery:
		if entityName != "" {
			return fmt.Sprintf("Executes custom query: %s", entityName)
		}
		return "Executes custom query"
	default:
		return fmt.Sprintf("Executes query %s", ep.Path)
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
					fieldDesc = fmt.Sprintf("Unique identifier for %s", ep.Entity)
				}
				break
			}
		}
		if fieldDesc == "" {
			fieldDesc = fmt.Sprintf("Unique identifier for %s", ep.Entity)
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
			desc := fmt.Sprintf("Text query to search %s by name. If omitted, returns all records.", ep.Entity)
			if ep.SearchField != "" {
				desc = fmt.Sprintf("Text query to search %s by field '%s'. If omitted, returns all records.",
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

// ── Constants for validation and result size limits ──

const (
	// MaxNumericParamValue is the maximum allowed value for numeric params.
	// Prevents DoS/OOM via limit=-1 or limit=9999999.
	MaxNumericParamValue = 10000

	// MaxStringParamLength is the maximum allowed length for string params.
	// Prevents huge string injection via MCP tool arguments.
	MaxStringParamLength = 1000

	// MaxResultSize is the maximum size of a tool result (in characters)
	// returned to the LLM. Prevents token explosion from large DB results.
	MaxResultSize = 50000
)

// validateArgs validates tool arguments against parameter definitions.
// Returns a list of validation errors (nil/nil if valid).
// Unknown params (not in definition) are silently ignored.
func validateArgs(args map[string]any, params []config.EndpointParam) []error {
	if len(params) == 0 {
		return nil
	}

	// Build a lookup by param name for O(1) access
	paramDefs := make(map[string]config.EndpointParam, len(params))
	for _, p := range params {
		paramDefs[p.Name] = p
	}

	var errs []error

	// 1. Check required fields
	for _, p := range params {
		if p.Required != nil && *p.Required {
			if _, ok := args[p.Name]; !ok {
				errs = append(errs, fmt.Errorf("param %q is required but not provided", p.Name))
			}
		}
	}

	if len(args) == 0 {
		return errs
	}

	for k, v := range args {
		def, ok := paramDefs[k]
		if !ok {
			continue // unknown param, ignore
		}

		switch def.Type {
		case config.ParamTypeInt, config.ParamTypeFloat:
			var val float64
			switch n := v.(type) {
			case float64:
				val = n
			case int:
				val = float64(n)
			case int64:
				val = float64(n)
			default:
				// Non-numeric passed for a numeric param
				errs = append(errs, fmt.Errorf("param %q: expected numeric type, got %T", k, v))
				continue
			}
			if val < 0 {
				errs = append(errs, fmt.Errorf("param %q: negative value %.0f is not allowed", k, val))
			}
			if val > MaxNumericParamValue {
				errs = append(errs, fmt.Errorf("param %q: value %.0f exceeds maximum allowed value %d", k, val, MaxNumericParamValue))
			}

		case config.ParamTypeString:
			s, ok := v.(string)
			if !ok {
				continue // not a string, skip validation
			}
			if len(s) > MaxStringParamLength {
				errs = append(errs, fmt.Errorf("param %q: string length %d exceeds maximum allowed length %d", k, len(s), MaxStringParamLength))
			}

		case config.ParamTypeBool:
			// No validation needed for booleans

		default:
			// Unknown param type, skip
		}
	}

	return errs
}

// truncateResult truncates a result string to at most maxLen characters.
// If truncated, appends a note that the result was truncated.
// Returns the original string unchanged if within limit.
func truncateResult(s string, maxLen int) string {
	if len(s) <= maxLen {
		return s
	}
	truncated := s[:maxLen]
	truncated += fmt.Sprintf("\n\n[Truncated: result was too large; showing first %d of %d characters]", maxLen, len(s))
	return truncated
}

// ── Audit Logging ──

// MakeAuditHandler wraps a ToolHandlerFunc with structured audit logging.
// Every tool call is logged with: tool name, tenant ID, truncated args,
// duration (ms), result size, and error (if any).
// Args are truncated to prevent PII leaks in logs:
// - each value is truncated to 200 chars
// - total serialised args string is truncated to 500 chars
func MakeAuditHandler(toolName, tenantID string, inner server.ToolHandlerFunc) server.ToolHandlerFunc {
	return func(ctx context.Context, request mcp.CallToolRequest) (*mcp.CallToolResult, error) {
		start := time.Now()

		result, err := inner(ctx, request)

		elapsed := time.Since(start)
		argsStr := truncateArgs(request.Params.Arguments)
		resultSize := 0
		if result != nil {
			for _, c := range result.Content {
				switch v := c.(type) {
				case mcp.TextContent:
					resultSize += len(v.Text)
				}
			}
		}

		attrs := []slog.Attr{
			slog.String("tool", toolName),
			slog.String("tenant", tenantID),
			slog.String("args", argsStr),
			slog.Int64("duration_ms", elapsed.Milliseconds()),
			slog.Int("result_size", resultSize),
		}

		if err != nil {
			attrs = append(attrs, slog.String("error", err.Error()))
			slog.LogAttrs(ctx, slog.LevelWarn, "tool_call", attrs...)
		} else {
			slog.LogAttrs(ctx, slog.LevelInfo, "tool_call", attrs...)
		}

		return result, err
	}
}

// truncateArgs serialises args to a short string for audit logging.
// Truncation strategy:
//   - each value is truncated to 200 chars
//   - total string is truncated to 500 chars
func truncateArgs(args map[string]any) string {
	if len(args) == 0 {
		return "{}"
	}
	var parts []string
	for k, v := range args {
		s := fmt.Sprintf("%v", v)
		if len(s) > 200 {
			s = s[:200] + "..."
		}
		parts = append(parts, fmt.Sprintf("%s=%s", k, s))
	}
	result := strings.Join(parts, ", ")
	if len(result) > 500 {
		result = result[:500] + "..."
	}
	return result
}

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
