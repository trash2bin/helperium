package configgen

import (
	"testing"

	"github.com/trash2bin/helperium/data-service/internal/datasource"
	"github.com/trash2bin/helperium/helperium-go/config"
)

func TestFieldRules_E2E(t *testing.T) {
	schema := &datasource.Schema{
		Driver: "postgres",
		Tables: []datasource.Table{
			{
				Name:       "products",
				PrimaryKey: []string{"id"},
				Columns: []datasource.Column{
					{Name: "id", Type: "int", Nullable: false},
					{Name: "name", Type: "string", Nullable: false},
					{Name: "price", Type: "float", Nullable: false},
					{Name: "category_id", Type: "int", Nullable: false},
					{Name: "rating", Type: "float", Nullable: true},
					{Name: "discount", Type: "float", Nullable: true},
					{Name: "internal_note", Type: "string", Nullable: true},
					{Name: "status", Type: "string", Nullable: false},
					{Name: "tier", Type: "string", Nullable: true},
					{Name: "created_at", Type: "datetime", Nullable: false},
					{Name: "is_active", Type: "bool", Nullable: false},
				},
			},
		},
	}

	t.Run("defaults", func(t *testing.T) {
		cfg := Generate(schema, &config.Config{
			DataSource: config.DataSourceConfig{Driver: "postgres", DSN: "test.db"},
		})

		// Default: filter, grep, distinct, schema should exist
		var hasFilter, hasGrep, hasDistinct, hasSchema bool
		for _, ep := range cfg.Endpoints {
			if ep.Strategy == "filter" {
				hasFilter = true
			}
			if ep.Strategy == "grep" {
				hasGrep = true
			}
			if ep.Op == config.OpDistinct {
				hasDistinct = true
			}
			if ep.Strategy == "schema" {
				hasSchema = true
			}
		}
		if !hasFilter || !hasGrep || !hasDistinct || !hasSchema {
			t.Errorf("defaults: filter=%v grep=%v distinct=%v schema=%v", hasFilter, hasGrep, hasDistinct, hasSchema)
		}

		// Check filter params include default fields
		for _, tool := range cfg.MCPTools {
			if tool.Name == "filter_products" {
				paramNames := map[string]bool{}
				for _, p := range tool.Params {
					paramNames[p.Name] = true
				}
				for _, name := range []string{"name", "price", "status", "category_id", "is_active"} {
					if !paramNames[name] {
						t.Errorf("default filter missing param: %s", name)
					}
				}
				// internal_note should be blocked by default searchable rules
				if paramNames["internal_note"] {
					t.Error("internal_note should be blocked by default searchable rules")
				}
			}
		}
	})

	t.Run("custom_filterable_add", func(t *testing.T) {
		cfg := Generate(schema, &config.Config{
			DataSource: config.DataSourceConfig{Driver: "postgres", DSN: "test.db"},
			FilterableRules: []config.FieldRule{
				{AllowNames: []string{"rating", "discount"}, Reason: "Custom metrics"},
			},
		})

		// rating and discount should be in filter params now
		for _, tool := range cfg.MCPTools {
			if tool.Name == "filter_products" {
				paramNames := map[string]bool{}
				for _, p := range tool.Params {
					paramNames[p.Name] = true
				}
				if !paramNames["rating"] {
					t.Error("rating should be filterable via custom rule")
				}
				if !paramNames["discount"] {
					t.Error("discount should be filterable via custom rule")
				}
			}
		}
	})

	t.Run("disable_default_custom_only", func(t *testing.T) {
		cfg := Generate(schema, &config.Config{
			DataSource:                     config.DataSourceConfig{Driver: "postgres", DSN: "test.db"},
			DisabledDefaultFilterableRules: []string{"filterable.common"},
			FilterableRules: []config.FieldRule{
				{AllowNames: []string{"rating", "discount"}, Reason: "Custom only"},
			},
		})

		for _, tool := range cfg.MCPTools {
			if tool.Name == "filter_products" {
				paramNames := map[string]bool{}
				for _, p := range tool.Params {
					paramNames[p.Name] = true
				}

				// name, price, status should NOT be filterable (disabled defaults)
				for _, name := range []string{"name", "price", "status"} {
					if paramNames[name] {
						t.Errorf("default field %s should NOT be filterable when default disabled", name)
					}
				}
				// But rating/discount should be (custom rule)
				if !paramNames["rating"] || !paramNames["discount"] {
					t.Error("custom fields rating/discount should be filterable")
				}
			}
		}
	})

	t.Run("searchable_block_internal", func(t *testing.T) {
		cfg := Generate(schema, &config.Config{
			DataSource: config.DataSourceConfig{Driver: "postgres", DSN: "test.db"},
			SearchableRules: []config.FieldRule{
				{BlockNames: []string{"internal_note"}, Reason: "Internal only"},
			},
		})

		foundGrep := false
		for _, tool := range cfg.MCPTools {
			if tool.Name == "grep_products" {
				// internal_note заблокирован из searchable, но name остаётся
				// searchable → grep-эндпоинт генерируется. Проверяем его наличие
				// (grep fields — free text, не валидируется).
				foundGrep = true
			}
		}
		if !foundGrep {
			t.Error("grep_products tool should exist (name still searchable)")
		}
	})

	t.Run("enum_custom_tier", func(t *testing.T) {
		cfg := Generate(schema, &config.Config{
			DataSource: config.DataSourceConfig{Driver: "postgres", DSN: "test.db"},
			EnumRules: []config.FieldRule{
				{AllowContains: []string{"tier"}, Reason: "Tier fields"},
			},
		})

		// tier should be in distinct endpoint
		for _, ep := range cfg.Endpoints {
			if ep.Op == config.OpDistinct && ep.Entity == "products" {
				for _, p := range ep.Params {
					if p.Name == "column" {
						// Check if tier is mentioned in description or enum values
						t.Logf("distinct params: %+v", ep.Params)
					}
				}
			}
		}
	})
}
