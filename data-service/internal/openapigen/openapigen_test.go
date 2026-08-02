package openapigen

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/trash2bin/helperium/helperium-go/config"
)

func boolPtr3(b bool) *bool { return &b }

// TestGenerate_CustomQuerySQLNotInOpenAPI — Задача 5 (MEDIUM):
// SQL custom-запроса не должен попадать в OpenAPI-документ
// (раскрытие структуры клиентской БД в публичном /openapi.json).
func TestGenerate_CustomQuerySQLNotInOpenAPI(t *testing.T) {
	cfg := &config.Config{
		Endpoints: []config.Endpoint{
			{
				Method:  config.MethodGET,
				Path:    "/products/by_brand/{brand_id}",
				Op:      config.OpCustomQuery,
				QueryID: "products_by_brand",
			},
		},
		CustomQueries: map[string]config.CustomQuery{
			"products_by_brand": {
				SQL:           "SELECT t.* FROM secret_products t WHERE t.brand_id = ?",
				Params:        []string{"brand_id"},
				MaxRows:       1000,
				Description:   "All products linked to a brand",
				ResultMapping: map[string]config.ResultMappingField{},
			},
		},
	}

	spec := Generate(cfg, "http://localhost", "Test", "1.0", false)
	data, err := json.Marshal(spec)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	raw := string(data)

	if strings.Contains(raw, "secret_products") {
		t.Errorf("OpenAPI doc leaks SQL table name (secret_products)")
	}
	if strings.Contains(raw, "SELECT t.*") {
		t.Errorf("OpenAPI doc leaks SQL text")
	}
}

// TestGenerate_ResponseSchemaExcludesPKFromRequired — Задача 4 (MEDIUM):
// response-схема не должна требовать PK-поле (id) — это read-only GET-ответ,
// а не запрос на создание.
func TestGenerate_ResponseSchemaExcludesPKFromRequired(t *testing.T) {
	entity := config.Entity{
		Name:     "products",
		Table:    "products",
		IDColumn: "id",
		Fields: []config.EntityField{
			{Name: "id", Type: config.FieldTypeInt, PrimaryKey: boolPtr3(true), Nullable: boolPtr3(false)},
			{Name: "name", Type: config.FieldTypeString, Nullable: boolPtr3(false)},
			{Name: "note", Type: config.FieldTypeString, Nullable: boolPtr3(true)},
		},
	}
	cfg := &config.Config{
		Entities: []config.Entity{entity},
		Endpoints: []config.Endpoint{
			{
				Method: config.MethodGET,
				Path:   "/products/{id}",
				Op:     config.OpGetByID,
				Entity: "products",
			},
		},
	}

	spec := Generate(cfg, "http://localhost", "Test", "1.0", false)
	data, err := json.Marshal(spec)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}

	var parsed struct {
		Components struct {
			Schemas map[string]struct {
				Required []string `json:"required"`
			} `json:"schemas"`
		} `json:"components"`
	}
	if err := json.Unmarshal(data, &parsed); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}

	schema, ok := parsed.Components.Schemas["products"]
	if !ok {
		t.Fatalf("products schema not found: %+v", parsed.Components.Schemas)
	}
	for _, req := range schema.Required {
		if req == "id" {
			t.Errorf("PK field 'id' should not be required in response schema; required=%v", schema.Required)
		}
	}
	// name (non-nullable, non-PK) — остаётся required, это семантически верно
	// для полной записи ответа.
	foundName := false
	for _, req := range schema.Required {
		if req == "name" {
			foundName = true
		}
	}
	if !foundName {
		t.Errorf("non-nullable non-PK field 'name' should remain required; required=%v", schema.Required)
	}
}

// TestQueryParams_StrategyEndpoints — L9: query-параметры для strategy-эндпоинтов
// (grep/filter) и distinct должны присутствовать в OpenAPI (раньше пусто).
func TestQueryParams_StrategyEndpoints(t *testing.T) {
	// Прямой unit-вызов queryParams (в том же пакете).
	grepParams := queryParams(config.Endpoint{Strategy: "grep", Op: config.OpStrategy})
	foundPattern := false
	for _, p := range grepParams {
		if p["name"] == "pattern" {
			foundPattern = true
			if p["required"] != true {
				t.Errorf("pattern should be required")
			}
		}
	}
	if !foundPattern {
		t.Errorf("queryParams(grep) should include pattern, got %v", grepParams)
	}

	filterParams := queryParams(config.Endpoint{Strategy: "filter", Op: config.OpStrategy})
	foundFieldOp := false
	for _, p := range filterParams {
		if p["name"] == "field__op" {
			foundFieldOp = true
		}
	}
	if !foundFieldOp {
		t.Errorf("queryParams(filter) should include field__op, got %v", filterParams)
	}

	distinctParams := queryParams(config.Endpoint{Op: config.OpDistinct})
	foundColumn := false
	for _, p := range distinctParams {
		if p["name"] == "column" {
			foundColumn = true
		}
	}
	if !foundColumn {
		t.Errorf("queryParams(distinct) should include column, got %v", distinctParams)
	}

	// e2e: параметры реально попадают в сгенерированный OpenAPI paths.
	specCfg := &config.Config{
		Entities: []config.Entity{{Name: "products", Table: "products", IDColumn: "id"}},
		Endpoints: []config.Endpoint{
			{Method: config.MethodGET, Path: "/products/grep", Op: config.OpStrategy, Strategy: "grep", Entity: "products", Description: "grep products"},
		},
	}
	spec := Generate(specCfg, "http://localhost", "Test", "1.0", false)
	data, err := json.Marshal(spec)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	if !strings.Contains(string(data), `"pattern"`) {
		t.Errorf("generated OpenAPI should contain pattern query param for grep endpoint")
	}
}
