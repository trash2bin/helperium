package configgen

import (
	"testing"

	"github.com/trash2bin/helperium/data-service/internal/datasource"
	"github.com/trash2bin/helperium/helperium-go/config"
)

func TestReview_FilterNumericFKGap(t *testing.T) {
	schema := &datasource.Schema{
		Driver: "sqlite",
		Tables: []datasource.Table{
			{Name: "orders", PrimaryKey: []string{"id"},
				Columns: []datasource.Column{
					{Name: "id", Type: "int"},
					{Name: "product_id", Type: "int"},   // FK
					{Name: "customer_id", Type: "int"},  // FK
					{Name: "price", Type: "float"},
					{Name: "status", Type: "string"},
				}},
		},
	}

	cfg := Generate(schema, &config.Config{
		DataSource: config.DataSourceConfig{Driver: "sqlite", DSN: "test.db"},
	})

	for _, tool := range cfg.MCPTools {
		if tool.Name == "filter_orders" {
			for _, p := range tool.Params {
				t.Logf("param: %s (type=%s, array_of=%s)", p.Name, p.Type, p.ArrayOf)
			}
			var hasGt, hasLt, hasIn bool
			for _, p := range tool.Params {
				if p.Name == "product_id__gt" || p.Name == "customer_id__gt" {
					hasGt = true
				}
				if p.Name == "product_id__lt" || p.Name == "customer_id__lt" {
					hasLt = true
				}
				if p.Name == "product_id__in" || p.Name == "customer_id__in" {
					hasIn = true
				}
			}
			t.Logf("FK numeric: gt=%v lt=%v in=%v", hasGt, hasLt, hasIn)
			// exact match должен быть
			foundExact := false
			for _, p := range tool.Params {
				if p.Name == "product_id" {
					foundExact = true
				}
			}
			if !foundExact {
				t.Error("product_id exact param missing")
			}
		}
	}
}
