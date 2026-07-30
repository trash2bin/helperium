package config_test

import (
	"fmt"
	"testing"

	"github.com/trash2bin/helperium/helperium-go/config"
)

// TestNormalize_V1toV2 verifies that a v1 config is upgraded to v2 after Normalize().
func TestNormalize_V1toV2(t *testing.T) {
	// Must have DataSource.Driver + DSN (required by Validate after Normalize).
	path := writeTempConfig(t, `{
		"version": 1,
		"data_source": {
			"driver": "sqlite",
			"dsn": ":memory:"
		},
		"entities": [
			{
				"name": "user",
				"table": "users",
				"id_column": "id",
				"fields": [
					{ "name": "id", "column": "id", "type": "int", "nullable": false }
				]
			}
		],
		"endpoints": [
			{ "method": "GET", "path": "/users/{id}", "op": "get_by_id", "entity": "user" }
		]
	}`)

	cfg, err := config.Load(path)
	if err != nil {
		t.Fatalf("Load() returned error: %v", err)
	}

	if cfg.Version != config.CurrentConfigVersion {
		t.Errorf("Version = %d, want %d", cfg.Version, config.CurrentConfigVersion)
	}

	if cfg.Meta == nil {
		t.Fatal("Meta is nil after Normalize")
	}
	if cfg.Meta.ConfigVersion != config.CurrentConfigVersion {
		t.Errorf("Meta.ConfigVersion = %d, want %d",
			cfg.Meta.ConfigVersion, config.CurrentConfigVersion)
	}
}

// TestNormalize_V2toV3 verifies that v2 configs with legacy find/list endpoints
// are migrated to op=strategy, strategy=schema.
func TestNormalize_V2toV3(t *testing.T) {
	tests := []struct {
		name      string
		inputOp   string
		wantOp    config.Op
		wantStrat string
	}{
		{"find becomes strategy", "find", config.OpStrategy, "schema"},
		{"list becomes strategy", "list", config.OpStrategy, "schema"},
		{"strategy unchanged", "strategy", config.OpStrategy, "grep"},
		{"get_by_id untouched", "get_by_id", config.OpGetByID, "grep"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			path := writeTempConfig(t, fmt.Sprintf(`{
				"version": 2,
				"data_source": {"driver": "sqlite", "dsn": ":memory:"},
				"entities": [{"name": "student", "table": "students", "id_column": "id",
					"fields": [{"name": "id", "column": "id", "type": "string", "nullable": false}]}],
				"endpoints": [{"method": "GET", "path": "/students", "op": %q, "entity": "student", "strategy": "grep"}]
			}`, tt.inputOp))

			cfg, err := config.Load(path)
			if err != nil {
				t.Fatalf("Load: %v", err)
			}
			if cfg.Endpoints[0].Op != tt.wantOp {
				t.Errorf("Op = %v, want %v", cfg.Endpoints[0].Op, tt.wantOp)
			}
			if cfg.Endpoints[0].Strategy != tt.wantStrat {
				t.Errorf("Strategy = %q, want %q", cfg.Endpoints[0].Strategy, tt.wantStrat)
			}
		})
	}
}

// TestNormalize_VersionFromZero upgrades a config with no version field (implicit 0).
func TestNormalize_VersionFromZero(t *testing.T) {
	path := writeTempConfig(t, `{
		"data_source": {
			"driver": "postgres",
			"dsn": "host=localhost dbname=test"
		},
		"entities": [
			{
				"name": "product",
				"table": "products",
				"id_column": "id",
				"fields": [
					{ "name": "id", "column": "id", "type": "int", "nullable": false }
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

	if cfg.Version != config.CurrentConfigVersion {
		t.Errorf("Version = %d, want %d after zero-to-current migration",
			cfg.Version, config.CurrentConfigVersion)
	}
}

// TestNormalize_PreservesExistingMeta verifies that a v2 config with existing
// Meta data is not overwritten.
func TestNormalize_PreservesExistingMeta(t *testing.T) {
	path := writeTempConfig(t, `{
		"version": 2,
		"meta": {
			"config_version": 2,
			"generated_at": "2026-07-11T12:00:00Z",
			"generator_version": "1.0.0"
		},
		"data_source": {
			"driver": "sqlite",
			"dsn": ":memory:"
		},
		"entities": [
			{
				"name": "order",
				"table": "orders",
				"id_column": "id",
				"fields": [
					{ "name": "id", "column": "id", "type": "int", "nullable": false }
				]
			}
		],
		"endpoints": [
			{ "method": "GET", "path": "/orders/{id}", "op": "get_by_id", "entity": "order" }
		]
	}`)

	cfg, err := config.Load(path)
	if err != nil {
		t.Fatalf("Load() returned error: %v", err)
	}

	if cfg.Meta.GeneratedAt != "2026-07-11T12:00:00Z" {
		t.Errorf("Meta.GeneratedAt = %q, want 2026-07-11T12:00:00Z", cfg.Meta.GeneratedAt)
	}
	if cfg.Meta.GeneratorVersion != "1.0.0" {
		t.Errorf("Meta.GeneratorVersion = %q, want 1.0.0", cfg.Meta.GeneratorVersion)
	}
}

// TestApprovedTool_UnmarshalJSON_String checks that legacy "/path" string format
// is correctly parsed into ApprovedTool.
func TestApprovedTool_UnmarshalJSON_String(t *testing.T) {
	raw := []byte(`"/students"`)
	var at config.ApprovedTool
	if err := at.UnmarshalJSON(raw); err != nil {
		t.Fatalf("UnmarshalJSON('/students'): %v", err)
	}
	if at.Endpoint != "/students" {
		t.Errorf("Endpoint = %q, want %q", at.Endpoint, "/students")
	}
	if at.Methods != nil {
		t.Errorf("Methods = %v, want nil (all methods)", at.Methods)
	}
}

// TestApprovedTool_UnmarshalJSON_Object checks that the expanded format
// {endpoint: "...", methods: [...]} is correctly parsed.
func TestApprovedTool_UnmarshalJSON_Object(t *testing.T) {
	raw := []byte(`{"endpoint":"/students","methods":["POST"]}`)
	var at config.ApprovedTool
	if err := at.UnmarshalJSON(raw); err != nil {
		t.Fatalf("UnmarshalJSON(object): %v", err)
	}
	if at.Endpoint != "/students" {
		t.Errorf("Endpoint = %q, want %q", at.Endpoint, "/students")
	}
	if len(at.Methods) != 1 {
		t.Fatalf("len(Methods) = %d, want 1", len(at.Methods))
	}
	if string(at.Methods[0]) != "POST" {
		t.Errorf("Methods[0] = %q, want %q", at.Methods[0], "POST")
	}
}

// TestApprovedTool_UnmarshalJSON_LegacyArray loads a full config where
// approved_tools is a []string (legacy format). config.Load should parse it
// via ApprovedTool.UnmarshalJSON and produce valid []ApprovedTool entries.
func TestApprovedTool_UnmarshalJSON_LegacyArray(t *testing.T) {
	path := writeTempConfig(t, `{
		"version": 2,
		"data_source": { "driver": "sqlite", "dsn": ":memory:" },
		"endpoints": [
			{ "method": "GET", "path": "/a", "op": "builtin_health" },
			{ "method": "GET", "path": "/b", "op": "builtin_health" }
		],
		"approved_tools": ["/a", "/b"]
	}`)

	cfg, err := config.Load(path)
	if err != nil {
		t.Fatalf("Load() returned error: %v", err)
	}

	if len(cfg.ApprovedTools) != 2 {
		t.Fatalf("len(ApprovedTools) = %d, want 2", len(cfg.ApprovedTools))
	}

	// Check first tool
	if cfg.ApprovedTools[0].Endpoint != "/a" {
		t.Errorf("ApprovedTools[0].Endpoint = %q, want %q",
			cfg.ApprovedTools[0].Endpoint, "/a")
	}
	// Legacy format — Methods should be nil (all methods)
	if cfg.ApprovedTools[0].Methods != nil {
		t.Errorf("ApprovedTools[0].Methods = %v, want nil (legacy=all)", cfg.ApprovedTools[0].Methods)
	}

	// Check second tool
	if cfg.ApprovedTools[1].Endpoint != "/b" {
		t.Errorf("ApprovedTools[1].Endpoint = %q, want %q",
			cfg.ApprovedTools[1].Endpoint, "/b")
	}
}

// TestValidate_V2Config verifies that a valid v2 config passes Validate().
func TestValidate_V2Config(t *testing.T) {
	raw := []byte(`{
		"version": 2,
		"data_source": {
			"driver": "sqlite",
			"dsn": ":memory:"
		},
		"entities": [
			{
				"name": "student",
				"table": "students",
				"id_column": "id",
				"fields": [
					{ "name": "id", "column": "id", "type": "string", "nullable": false, "primary_key": true },
					{ "name": "name", "column": "name", "type": "string", "nullable": false }
				],
				"relations": [
					{
						"field": "course",
						"kind": "many_to_one",
						"table": "courses",
						"local_fk": "course_id"
					}
				]
			}
		],
		"endpoints": [
			{ "method": "GET", "path": "/students/{id}", "op": "get_by_id", "entity": "student" }
		],
		"approved_tools": [
			{ "endpoint": "/students", "methods": ["POST"] }
		]
	}`)

	if err := config.Validate(raw); err != nil {
		t.Errorf("Validate(v2 config): %v", err)
	}
}

// TestValidate_V2Config_InvalidFields verifies that v2 validation catches
// bad relations (missing junction_table for many_to_many).
func TestValidate_V2Config_InvalidFields(t *testing.T) {
	raw := []byte(`{
		"version": 2,
		"data_source": {
			"driver": "sqlite",
			"dsn": ":memory:"
		},
		"entities": [
			{
				"name": "student",
				"table": "students",
				"id_column": "id",
				"fields": [
					{ "name": "id", "column": "id", "type": "string" }
				],
				"relations": [
					{
						"field": "courses",
						"kind": "many_to_many",
						"table": "courses",
						"local_fk": "student_id",
						"target_fk": "course_id"
					}
				]
			}
		],
		"endpoints": [
			{ "method": "GET", "path": "/students/{id}", "op": "get_by_id", "entity": "student" }
		]
	}`)

	err := config.Validate(raw)
	if err == nil {
		t.Fatal("expected validation error for many_to_many without junction_table, got nil")
	}
}

// TestNormalize_NormalizeTwiceIsIdempotent verifies calling Normalize() twice
// produces the same result as calling it once.
func TestNormalize_NormalizeTwiceIsIdempotent(t *testing.T) {
	path := writeTempConfig(t, `{
		"version": 1,
		"data_source": { "driver": "sqlite", "dsn": ":memory:" }
	}`)

	cfg, err := config.Load(path)
	if err != nil {
		t.Fatalf("Load() returned error: %v", err)
	}

	versionAfterFirstNormalize := cfg.Version

	// Call Normalize again on the already-loaded config
	cfg.Normalize()

	if cfg.Version != versionAfterFirstNormalize {
		t.Errorf("Version changed after second Normalize: %d → %d",
			versionAfterFirstNormalize, cfg.Version)
	}
}

// TestApprovedTool_UnmarshalJSON_EmptyString checks that an empty string
// parses with an empty Endpoint (not panic).
func TestApprovedTool_UnmarshalJSON_EmptyString(t *testing.T) {
	raw := []byte(`""`)
	var at config.ApprovedTool
	if err := at.UnmarshalJSON(raw); err != nil {
		t.Fatalf("UnmarshalJSON(empty string): %v", err)
	}
	// Empty string → empty Endpoint, not nil
	if at.Endpoint != "" {
		t.Errorf("Endpoint = %q, want empty string", at.Endpoint)
	}
}
