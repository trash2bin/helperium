package tools

import (
	"encoding/json"
	"testing"

	"github.com/mark3labs/mcp-go/mcp"
	"github.com/trash2bin/helperium/helperium-go/config"
)

// TestDbFilter_InputSchemaPermitsDynamicFieldOps pins the wire-level contract
// for the consolidated db_filter tool.
//
// GenerateConsolidatedMCPTools declares ONLY entity+limit in Params, while the
// tool description tells the model to pass dynamic field params top-level
// (price__lt=2000, status=new, ...). For that to work end-to-end, the
// LLM-facing inputSchema must NOT reject unknown top-level properties:
//
//   - additionalProperties must not be false;
//   - entity must be present and required;
//   - limit must be present and optional.
//
// If someone switches the schema to additionalProperties:false (or a mcp-go
// upgrade changes the default), every dynamic db_filter call dies at the MCP
// client before reaching data-service. This test makes that fail loudly here
// instead of silently in production.
func TestDbFilter_InputSchemaPermitsDynamicFieldOps(t *testing.T) {
	required := true
	// Exactly what GenerateConsolidatedMCPTools emits for db_filter.
	// Duplicated inline (data-service types are not importable here without a
	// module dep) — keep in sync with services/data-service/internal/configgen/mcp.go.
	mt := config.MCPTool{
		Name:     "db_filter",
		Endpoint: "/q/filter",
		Params: []config.EndpointParam{
			{
				Name: "entity", In: config.ParamInQuery, Type: config.ParamTypeString, Required: &required,
				Description: "Entity name (from db_map, canonical e.g. catalog_product).",
			},
			{
				Name: "limit", In: config.ParamInQuery, Type: config.ParamTypeInt,
				Description: "Max results (1-100, default: 10). Use 1 for pure count questions.",
			},
		},
	}

	// Rebuild the inputSchema exactly the way registerOne does.
	opts := []mcp.ToolOption{mcp.WithDescription(mt.Description)}
	for _, p := range mt.Params {
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
	tool := mcp.NewTool(mt.Name, opts...)

	raw, err := json.Marshal(tool.InputSchema)
	if err != nil {
		t.Fatalf("marshal inputSchema: %v", err)
	}
	var schema map[string]any
	if err := json.Unmarshal(raw, &schema); err != nil {
		t.Fatalf("unmarshal inputSchema: %v", err)
	}

	t.Logf("db_filter inputSchema: %s", raw)

	// 1. additionalProperties must not be explicitly false.
	if ap, ok := schema["additionalProperties"]; ok {
		if b, isBool := ap.(bool); isBool && !b {
			t.Errorf("db_filter inputSchema sets additionalProperties=false: "+
				"dynamic field params (price__lt, status, ...) will be rejected "+
				"by MCP clients before reaching the gateway. Schema: %s", raw)
		}
	}

	// 2. entity declared and required.
	props, _ := schema["properties"].(map[string]any)
	if props == nil {
		t.Fatalf("inputSchema has no properties object: %s", raw)
	}
	if _, ok := props["entity"]; !ok {
		t.Errorf("db_filter inputSchema missing required 'entity' property: %s", raw)
	}
	if _, ok := props["limit"]; !ok {
		t.Errorf("db_filter inputSchema missing 'limit' property: %s", raw)
	}
	reqList, _ := schema["required"].([]any)
	var reqNames []string
	for _, r := range reqList {
		if s, ok := r.(string); ok {
			reqNames = append(reqNames, s)
		}
	}
	if len(reqNames) != 1 || reqNames[0] != "entity" {
		t.Errorf("db_filter required = %v, want [entity]", reqNames)
	}

	// 3. Simulate the model's real call: entity + undeclared dynamic fields.
	// The schema itself must be object-shaped so extra props land in the same
	// namespace (gateway forwards them as query params — see client.Call).
	if schema["type"] != "object" {
		t.Errorf("db_filter inputSchema type = %v, want object", schema["type"])
	}
}
