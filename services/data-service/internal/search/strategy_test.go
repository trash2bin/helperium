package search

import (
	"testing"

	"github.com/trash2bin/helperium/helperium-go/config"
)

// ═══════════════════════════════════════════════════════════════════════════════
// Contract tests: each strategy must satisfy the Strategy interface correctly
// ═══════════════════════════════════════════════════════════════════════════════

func TestGrepStrategy_Contract(t *testing.T) {
	s := NewGrepStrategy("id", "name")

	if got := s.Name(); got != "grep" {
		t.Errorf("Name() = %q, want %q", got, "grep")
	}

	toolName := s.ToolName(sampleEntity)
	if toolName != "grep_products" {
		t.Errorf("ToolName() = %q, want %q", toolName, "grep_products")
	}

	desc := s.ToolDescription(sampleEntity)
	if desc == "" {
		t.Error("ToolDescription() is empty")
	}

	params := s.ToolParams(sampleEntity)
	if len(params) == 0 {
		t.Fatal("ToolParams() returned empty")
	}

	// pattern must be required
	hasRequired := false
	for _, p := range params {
		if p.Name == "pattern" {
			hasRequired = true
			if p.Required == nil || !*p.Required {
				t.Error("pattern param must have Required=true")
			}
			break
		}
	}
	if !hasRequired {
		t.Error("pattern param not found in ToolParams")
	}

	if s.EntityIDCol() != "id" {
		t.Errorf("EntityIDCol() = %q, want %q", s.EntityIDCol(), "id")
	}
	if s.EntityNameCol() != "name" {
		t.Errorf("EntityNameCol() = %q, want %q", s.EntityNameCol(), "name")
	}
}

func TestGrepStrategy_EmptyPattern_Error(t *testing.T) {
	s := NewGrepStrategy("id", "name")
	// Empty query → no pattern param
	r := makeRequest(map[string]string{})
	_, err := s.ParseRequest(r, sampleEntity, testAdapter{})
	if err == nil {
		t.Error("expected error for empty pattern, got nil")
	}
}

func TestFilterStrategy_Contract(t *testing.T) {
	s := NewFilterStrategy("id", "name")

	if got := s.Name(); got != "filter" {
		t.Errorf("Name() = %q, want %q", got, "filter")
	}

	toolName := s.ToolName(sampleEntity)
	if toolName != "filter_products" {
		t.Errorf("ToolName() = %q, want %q", toolName, "filter_products")
	}

	desc := s.ToolDescription(sampleEntity)
	if desc == "" {
		t.Error("ToolDescription() is empty")
	}

	params := s.ToolParams(sampleEntity)
	if len(params) == 0 {
		t.Fatal("ToolParams() returned empty")
	}

	// Must have field params beyond just "limit"
	hasFieldParam := false
	for _, p := range params {
		if p.Name != "limit" {
			hasFieldParam = true
			break
		}
	}
	if !hasFieldParam {
		t.Error("ToolParams() must contain field filter params beyond limit")
	}

	if s.EntityIDCol() != "id" {
		t.Errorf("EntityIDCol() = %q, want %q", s.EntityIDCol(), "id")
	}
	if s.EntityNameCol() != "name" {
		t.Errorf("EntityNameCol() = %q, want %q", s.EntityNameCol(), "name")
	}
}

func TestFilterStrategy_EmptyRequest_Error(t *testing.T) {
	s := NewFilterStrategy("id", "name")
	r := makeRequest(map[string]string{}) // no filter params
	_, err := s.ParseRequest(r, sampleEntity, testAdapter{})
	if err == nil {
		t.Error("expected error for empty filter request, got nil")
	}
}

func TestSchemaStrategy_Contract(t *testing.T) {
	s := NewSchemaStrategy("id", "name")

	if got := s.Name(); got != "schema" {
		t.Errorf("Name() = %q, want %q", got, "schema")
	}

	toolName := s.ToolName(sampleEntity)
	if toolName != "schema_products" {
		t.Errorf("ToolName() = %q, want %q", toolName, "schema_products")
	}

	desc := s.ToolDescription(sampleEntity)
	if desc == "" {
		t.Error("ToolDescription() is empty")
	}

	// schema has no params
	params := s.ToolParams(sampleEntity)
	if params != nil {
		t.Errorf("ToolParams() = %v, want nil", params)
	}

	// ParseRequest always returns (nil, nil) for schema
	plan, err := s.ParseRequest(nil, sampleEntity, nil)
	if err != nil {
		t.Errorf("ParseRequest() error = %v, want nil", err)
	}
	if plan != nil {
		t.Errorf("ParseRequest() plan = %v, want nil", plan)
	}

	if s.EntityIDCol() != "id" {
		t.Errorf("EntityIDCol() = %q, want %q", s.EntityIDCol(), "id")
	}
	if s.EntityNameCol() != "name" {
		t.Errorf("EntityNameCol() = %q, want %q", s.EntityNameCol(), "name")
	}
}

func TestSchemaStrategy_FieldInfo(t *testing.T) {
	s := NewSchemaStrategy("id", "name")

	// Entity with PK, tenant_id, excluded field
	entity := config.Entity{
		Name:  "test",
		Table: "test",
		Fields: []config.EntityField{
			{Name: "id", Column: "id", Type: config.FieldTypeInt, PrimaryKey: boolPtr(true)},
			{Name: "tenant_id", Column: "tenant_id", Type: config.FieldTypeString},
			{Name: "name", Column: "name", Type: config.FieldTypeString},
			{Name: "pii_field", Column: "pii", Type: config.FieldTypeString, ExcludeFromSearch: true},
			{Name: "price", Column: "price", Type: config.FieldTypeInt},
		},
	}

	fields := s.FieldInfo(entity)
	if len(fields) != 2 {
		t.Fatalf("FieldInfo() returned %d fields, want 2 (name, price)", len(fields))
	}
	if fields[0].Name != "name" || fields[1].Name != "price" {
		t.Errorf("FieldInfo() = %v, want [{name} {price}]", fields)
	}
}

func TestAllStrategies_HaveUniqueNames(t *testing.T) {
	strategies := []Strategy{
		NewGrepStrategy("id", "name"),
		NewFilterStrategy("id", "name"),
		NewSchemaStrategy("id", "name"),
	}

	seen := make(map[string]bool)
	for _, s := range strategies {
		name := s.Name()
		if name == "" {
			t.Error("a strategy has empty Name()")
		}
		if seen[name] {
			t.Errorf("duplicate strategy name: %q", name)
		}
		seen[name] = true
	}

	if len(seen) != 3 {
		t.Errorf("expected 3 unique strategy names, got %d: %v", len(seen), seen)
	}
}
