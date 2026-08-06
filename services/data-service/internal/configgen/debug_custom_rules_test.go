package configgen

import (
	"testing"

	"github.com/trash2bin/helperium/data-service/internal/datasource"
	"github.com/trash2bin/helperium/helperium-go/config"
)

func TestDebugCustomFilterableRules(t *testing.T) {
	cfg := Generate(&datasource.Schema{
		Driver: "postgres",
		Tables: []datasource.Table{
			{
				Name:       "products",
				PrimaryKey: []string{"id"},
				Columns: []datasource.Column{
					{Name: "id", Type: "int", Nullable: false},
					{Name: "name", Type: "string", Nullable: false},
					{Name: "price", Type: "float", Nullable: false},
					{Name: "rating", Type: "float", Nullable: true},
					{Name: "discount", Type: "float", Nullable: true},
				},
			},
		},
	}, &config.Config{
		DataSource: config.DataSourceConfig{Driver: "postgres", DSN: "test.db"},
		FilterableRules: []config.FieldRule{
			{AllowNames: []string{"rating", "discount"}, Reason: "Custom metrics"},
		},
	})

	for _, tool := range cfg.MCPTools {
		if tool.Name == "filter_products" {
			t.Logf("filter_products params (%d):", len(tool.Params))
			for _, p := range tool.Params {
				t.Logf("  %s", p.Name)
			}
		}
	}
}
