package handlers

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/trash2bin/helperium/helperium-go/config"
)

func boolPtr2(b bool) *bool { return &b }

// TestMCPManifest_CustomFilterableRulesReachManifest — Задача 1 (HIGH):
// кастомные FilterableRules (добавленные через PUT /admin/config) должны
// доходить до MCP-манифеста, а не заменяться дефолтными в runtime.
//
// Поле note не входит в дефолтные filterable-поля; с кастомным
// правилом (AllowNames: ["note"]) у filter-тула должен появиться
// параметр note.
func TestMCPManifest_CustomFilterableRulesReachManifest(t *testing.T) {
	entity := config.Entity{
		Name:     "products",
		Table:    "products",
		IDColumn: "id",
		Fields: []config.EntityField{
			{Name: "id", Type: config.FieldTypeInt, PrimaryKey: boolPtr2(true), Nullable: boolPtr2(false)},
			{Name: "name", Type: config.FieldTypeString, Nullable: boolPtr2(false)},
			{Name: "note", Type: config.FieldTypeString, Nullable: boolPtr2(true)},
		},
	}

	cfg := &config.Config{
		Entities: []config.Entity{entity},
		Endpoints: []config.Endpoint{
			{
				Method:   config.MethodGET,
				Path:     "/products/filter",
				Op:       config.OpStrategy,
				Strategy: "filter",
				Entity:   "products",
			},
		},
		FilterableRules: []config.FieldRule{
			{AllowNames: []string{"note"}, Reason: "User rule: note filterable"},
		},
	}

	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/mcp/manifest", nil)
	MCPManifestHandler(cfg)(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("status %d, body %s", rec.Code, rec.Body.String())
	}

	var manifest struct {
		MCPTools []struct {
			Name   string `json:"name"`
			Params []struct {
				Name string `json:"name"`
			} `json:"params"`
		} `json:"mcp_tools"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &manifest); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}

	var filterTool *struct {
		Name   string `json:"name"`
		Params []struct {
			Name string `json:"name"`
		} `json:"params"`
	}
	for i := range manifest.MCPTools {
		if manifest.MCPTools[i].Name == "filter_products" {
			filterTool = &manifest.MCPTools[i]
			break
		}
	}
	if filterTool == nil {
		t.Fatalf("filter_products tool not found in manifest: %+v", manifest.MCPTools)
	}

	found := false
	for _, p := range filterTool.Params {
		if p.Name == "note" {
			found = true
			break
		}
	}
	if !found {
		names := make([]string, 0, len(filterTool.Params))
		for _, p := range filterTool.Params {
			names = append(names, p.Name)
		}
		t.Errorf("note not in filter_products params (custom FilterableRules lost); got %v", names)
	}
}
