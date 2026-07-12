package config_test

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/trash2bin/helperium/helperium-go/config"
)

// writeTempConfig пишет конфиг во временный файл и возвращает его путь.
func writeTempConfig(t *testing.T, data string) string {
	t.Helper()
	dir := t.TempDir()
	path := filepath.Join(dir, "config.json")
	if err := os.WriteFile(path, []byte(data), 0644); err != nil {
		t.Fatalf("write temp config: %v", err)
	}
	return path
}

// withConfigSchemaNoop — убирает CONFIG_SCHEMA (тесты сами знают где она).
func withSchemaPath(t *testing.T) {
	t.Helper()
	// Берём схему из specs/ относительно helperium-go/
	wd, err := os.Getwd()
	if err != nil {
		t.Fatalf("os.Getwd: %v", err)
	}
	candidates := []string{
		filepath.Join(wd, "..", "..", "specs", "config.schema.json"), // helperium-go/config → repo/specs
		filepath.Join(wd, "..", "..", "specs", "config.schema.json"),
	}
	for _, c := range candidates {
		if _, err := os.Stat(c); err == nil {
			abs, _ := filepath.Abs(c)
			t.Setenv("CONFIG_SCHEMA", abs)
			return
		}
	}
	t.Fatalf("config.schema.json not found; tried %v", candidates)
}

func TestMCPLoad_ValidConfig(t *testing.T) {
	withSchemaPath(t)
	path := writeTempConfig(t, `{
		"version": 1,
		"data_source": { "driver": "sqlite", "dsn": ":memory:" },
		"entities": [
			{
				"name": "student", "table": "students", "id_column": "id",
				"fields": [
					{ "name": "id", "column": "id", "type": "string", "nullable": false, "primary_key": true },
					{ "name": "full_name", "column": "name", "type": "string", "nullable": false }
				]
			}
		],
		"endpoints": [
			{ "method": "GET", "path": "/students/{id}", "op": "get_by_id", "entity": "student" }
		]
	}`)
	cfg, err := config.Load(path)
	if err != nil {
		t.Fatalf("Load() returned error: %v", err)
	}
	if cfg.Version != 1 {
		t.Errorf("Version = %d, want 1", cfg.Version)
	}
	if cfg.DataSource.Driver != config.DriverSQLite {
		t.Errorf("DataSource.Driver = %q, want %q", cfg.DataSource.Driver, config.DriverSQLite)
	}
	if len(cfg.Entities) != 1 {
		t.Errorf("len(Entities) = %d, want 1", len(cfg.Entities))
	}
	if len(cfg.Endpoints) != 1 {
		t.Errorf("len(Endpoints) = %d, want 1", len(cfg.Endpoints))
	}
}

func TestMCPLoad_InvalidPath(t *testing.T) {
	withSchemaPath(t)
	_, err := config.Load("/nonexistent/path/config.json")
	if err == nil {
		t.Fatal("Load() expected error for nonexistent path, got nil")
	}
}

func TestMCPLoad_InvalidJSON(t *testing.T) {
	withSchemaPath(t)
	path := writeTempConfig(t, `{invalid json}`)
	_, err := config.Load(path)
	if err == nil {
		t.Fatal("Load() expected error for invalid JSON, got nil")
	}
}

func TestMCPLoad_EmptyEntitiesAndEndpoints(t *testing.T) {
	withSchemaPath(t)
	path := writeTempConfig(t, `{
		"version": 1,
		"data_source": { "driver": "postgres", "dsn": "host=localhost" },
		"entities": [],
		"endpoints": []
	}`)
	cfg, err := config.Load(path)
	if err != nil {
		t.Fatalf("Load() returned error: %v", err)
	}
	if len(cfg.Entities) != 0 {
		t.Errorf("len(Entities) = %d, want 0", len(cfg.Entities))
	}
	if len(cfg.Endpoints) != 0 {
		t.Errorf("len(Endpoints) = %d, want 0", len(cfg.Endpoints))
	}
	if cfg.DataSource.Driver != config.DriverPostgres {
		t.Errorf("DataSource.Driver = %q, want %q", cfg.DataSource.Driver, config.DriverPostgres)
	}
}

func TestMCPLoad_EntityWithAllFieldTypes(t *testing.T) {
	withSchemaPath(t)
	path := writeTempConfig(t, `{
		"version": 1,
		"data_source": { "driver": "sqlite", "dsn": ":memory:" },
		"entities": [
			{
				"name": "product", "table": "products", "id_column": "id",
				"fields": [
					{ "name": "id", "column": "id", "type": "string", "nullable": false },
					{ "name": "name", "column": "name", "type": "string" },
					{ "name": "price", "column": "price", "type": "float", "nullable": true },
					{ "name": "active", "column": "is_active", "type": "bool" },
					{ "name": "metadata", "column": "meta", "type": "json" }
				]
			}
		],
		"endpoints": [
			{ "method": "GET", "path": "/products/{id}", "op": "get_by_id", "entity": "product" }
		]
	}`)
	cfg, err := config.Load(path)
	if err != nil {
		t.Fatalf("Load() returned error: %v", err)
	}
	entity := cfg.Entities[0]
	if len(entity.Fields) != 5 {
		t.Fatalf("len(Fields) = %d, want 5", len(entity.Fields))
	}
	fields := make(map[string]string)
	for _, f := range entity.Fields {
		fields[f.Name] = string(f.Type)
	}
	tests := []struct {
		name     string
		wantType string
	}{
		{"id", "string"},
		{"name", "string"},
		{"price", "float"},
		{"active", "bool"},
		{"metadata", "json"},
	}
	for _, tt := range tests {
		got, ok := fields[tt.name]
		if !ok {
			t.Errorf("field %q not found", tt.name)
			continue
		}
		if got != tt.wantType {
			t.Errorf("field %q type = %q, want %q", tt.name, got, tt.wantType)
		}
	}
}

func TestMCPLoad_MCPToolOverrideOrderPreserved(t *testing.T) {
	withSchemaPath(t)
	path := writeTempConfig(t, `{
		"version": 1,
		"data_source": { "driver": "sqlite", "dsn": ":memory:" },
		"mcp_tools": [
			{ "name": "first_tool", "endpoint": "/a", "description": "First" },
			{ "name": "second_tool", "endpoint": "/b", "description": "Second" },
			{ "name": "third_tool", "endpoint": "/c", "description": "Third" }
		]
	}`)
	cfg, err := config.Load(path)
	if err != nil {
		t.Fatalf("Load() returned error: %v", err)
	}
	if len(cfg.MCPTools) != 3 {
		t.Fatalf("len(MCPTools) = %d, want 3", len(cfg.MCPTools))
	}
	names := []string{cfg.MCPTools[0].Name, cfg.MCPTools[1].Name, cfg.MCPTools[2].Name}
	expected := []string{"first_tool", "second_tool", "third_tool"}
	for i, n := range names {
		if n != expected[i] {
			t.Errorf("MCPTools[%d].Name = %q, want %q", i, n, expected[i])
		}
	}
}

func TestMCPLoad_TrailingCommas(t *testing.T) {
	withSchemaPath(t)
	path := writeTempConfig(t, `{
		"version": 1,
		"data_source": { "driver": "sqlite", "dsn": ":memory:", }
	}`)
	_, err := config.Load(path)
	if err == nil {
		t.Fatal("Load() expected error for trailing comma, got nil")
	}
}

func TestMCPLoad_UnicodeFieldDescriptions(t *testing.T) {
	withSchemaPath(t)
	path := writeTempConfig(t, `{
		"version": 1,
		"data_source": { "driver": "sqlite", "dsn": ":memory:" },
		"entities": [
			{
				"name": "student", "table": "students", "id_column": "id",
				"fields": [
					{ "name": "id", "column": "id", "type": "string", "nullable": false, "primary_key": true },
					{ "name": "full_name", "column": "name", "type": "string", "nullable": false, "description": "Полное ФИО" },
					{ "name": "course", "column": "course", "type": "int", "nullable": true }
				]
			}
		],
		"endpoints": [
			{ "method": "GET", "path": "/students/{id}", "op": "get_by_id", "entity": "student" }
		]
	}`)
	cfg, err := config.Load(path)
	if err != nil {
		t.Fatalf("Load() returned error: %v", err)
	}
	if cfg.Entities[0].Fields[1].Description != "Полное ФИО" {
		t.Errorf("Description = %q, want %q", cfg.Entities[0].Fields[1].Description, "Полное ФИО")
	}
}
