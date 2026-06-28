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

	"github.com/agent-tutor/mcp-gateway/internal/config"
	"github.com/agent-tutor/mcp-gateway/internal/httpclient"
	"github.com/mark3labs/mcp-go/mcp"
	"github.com/mark3labs/mcp-go/server"
)

// Registry manages auto-generated + explicit MCP tools.
type Registry struct {
	cfg      *config.Config
	client   *httpclient.Client
	toolDefs []toolDef
}

// toolDef — внутреннее описание одного MCP-инструмента.
type toolDef struct {
	Name        string
	Endpoint    string
	Description string
	Params      []config.EndpointParam
}

// NewRegistry creates a registry and auto-builds tool definitions from config.
func NewRegistry(cfg *config.Config) *Registry {
	r := &Registry{
		cfg:    cfg,
		client: httpclient.New(),
	}
	r.buildTools()
	return r
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
}

// GetToolDefs returns tool definitions (for debug/inspection).
func (r *Registry) GetToolDefs() []toolDef {
	return r.toolDefs
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
		case "int", "float":
			opts = append(opts, mcp.WithNumber(p.Name, propOpts...))
		case "bool":
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
	case "builtin_health":
		return "health"
	case "builtin_stats":
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
	case "get_by_id":
		return "get_" + entityName
	case "find":
		return "find_" + entityName
	case "list":
		return "list_" + entityName
	case "custom_query":
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
	case "get_by_id":
		desc := "Get " + entityName + " by ID"
		if entityDesc != "" {
			desc += " (" + entityDesc + ")"
		}
		return desc
	case "find":
		desc := "Find " + entityName
		if ep.SearchField != "" {
			desc += " by " + ep.SearchField
		}
		if entityDesc != "" {
			desc += " (" + entityDesc + ")"
		}
		return desc
	case "list":
		return "List all " + entityName
	case "custom_query":
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
		fieldType := "string"
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
			In:          "path",
			Type:        ptype,
			Required:    &required,
			Description: pp,
		})
	}

	// 4. For find/list: add search query param
	if ep.Op == "find" || ep.Op == "list" {
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
				In:          "query",
				Type:        "string",
				Required:    &required,
				Description: desc,
			})
		}
	}

	return params
}

// fieldTypeToParamType maps entity field types to MCP parameter types.
func fieldTypeToParamType(ft string) string {
	switch ft {
	case "int", "float":
		return ft
	case "bool":
		return "bool"
	default:
		return "string"
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
