package config_test

import (
	"fmt"
	"strings"
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

// TestLegacyFindListOps_Rejected — v4: legacy op="find"/op="list" endpoints
// are no longer converted to strategies; Normalize leaves them untouched and
// Validate rejects them as unsupported. Старые конфиги с find/list не загрузятся.
func TestLegacyFindListOps_Rejected(t *testing.T) {
	tests := []struct {
		name string
		op   string
		ver  int
	}{
		{"find v2", "find", 2},
		{"list v2", "list", 2},
		{"find v3", "find", 3},
		{"list v3", "list", 3},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			path := writeTempConfig(t, fmt.Sprintf(`{
				"version": %d,
				"data_source": {"driver": "sqlite", "dsn": ":memory:"},
				"entities": [{"name": "student", "table": "students", "id_column": "id",
					"fields": [{"name": "id", "column": "id", "type": "string", "nullable": false}]}],
				"endpoints": [{"method": "GET", "path": "/students", "op": %q, "entity": "student"}]
			}`, tt.ver, tt.op))

			cfg, err := config.Load(path)
			if err == nil {
				t.Fatalf("Load(legacy op=%q) = nil error, want validation error", tt.op)
			}
			if !strings.Contains(err.Error(), `op: unsupported "`+tt.op+`"`) {
				t.Errorf("error = %q, want op: unsupported %q", err.Error(), tt.op)
			}
			if cfg != nil {
				t.Errorf("cfg = %+v, want nil on validation error", cfg)
			}
		})
	}
}

// TestLegacyFindListOps_NotConverted — Normalize() не должен конвертировать
// op=find/list в strategy (v4: legacy unsupported).
func TestLegacyFindListOps_NotConverted(t *testing.T) {
	cfg := &config.Config{
		Version: 3,
		Endpoints: []config.Endpoint{
			{Method: config.MethodGET, Path: "/a", Op: "find"},
			{Method: config.MethodGET, Path: "/b", Op: "list"},
		},
	}
	cfg.Normalize()
	if cfg.Version != config.CurrentConfigVersion {
		t.Errorf("Version = %d, want %d", cfg.Version, config.CurrentConfigVersion)
	}
	if cfg.Endpoints[0].Op != "find" || cfg.Endpoints[0].Strategy != "" {
		t.Errorf("endpoints[0] converted: op=%q strategy=%q, want find + empty", cfg.Endpoints[0].Op, cfg.Endpoints[0].Strategy)
	}
	if cfg.Endpoints[1].Op != "list" || cfg.Endpoints[1].Strategy != "" {
		t.Errorf("endpoints[1] converted: op=%q strategy=%q, want list + empty", cfg.Endpoints[1].Op, cfg.Endpoints[1].Strategy)
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
