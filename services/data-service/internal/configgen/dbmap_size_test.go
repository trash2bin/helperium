package configgen

import (
	"encoding/json"
	"testing"

	"github.com/trash2bin/helperium/data-service/internal/datasource"
	"github.com/trash2bin/helperium/helperium-go/config"
)

// Фаза 2.5 fix: db_map (компактная версия) — cheat-sheet, не манифест.
// Схема уже авто-инжектится в system prompt. 7 entities (product широкий,
// остальные узкие — как autoparts) → < 3.5KB (живой 8.5KB → компактный).
func TestSchemaForLLMCompact_Size(t *testing.T) {
	tPK := true
	productCols := []datasource.Column{{Name: "id", Type: "int"}}
	productFields := []config.EntityField{{Name: "id", Column: "id", Type: config.FieldTypeInt, PrimaryKey: &tPK}}
	wide := []struct{ name, typ string }{
		{"article", "string"}, {"name", "string"}, {"slug", "string"}, {"oem_number", "string"},
		{"description", "string"}, {"short_description", "string"}, {"dimensions", "string"},
		{"image", "string"}, {"label", "string"}, {"supplier", "string"}, {"country_of_origin", "string"},
		{"seo_title", "string"}, {"seo_description", "string"},
		{"is_available", "bool"}, {"is_popular", "bool"}, {"is_new", "bool"}, {"is_bestseller", "bool"},
		{"is_promo", "bool"}, {"is_active", "bool"},
		{"price", "float"}, {"old_price", "float"}, {"quantity", "int"}, {"weight_kg", "float"},
		{"warranty_months", "int"}, {"views_count", "int"}, {"ordering", "int"},
		{"brand_id", "int"}, {"category_id", "int"},
	}
	ftOf := func(t string) config.FieldType {
		switch t {
		case "int":
			return config.FieldTypeInt
		case "float":
			return config.FieldTypeFloat
		case "bool":
			return config.FieldTypeBool
		}
		return config.FieldTypeString
	}
	for _, c := range wide {
		productCols = append(productCols, datasource.Column{Name: c.name, Type: c.typ})
		productFields = append(productFields, config.EntityField{Name: c.name, Column: c.name, Type: ftOf(c.typ)})
	}
	mkNarrow := func(name string, flds ...string) (datasource.Table, config.Entity) {
		cols := []datasource.Column{{Name: "id", Type: "int"}}
		fields := []config.EntityField{{Name: "id", Column: "id", Type: config.FieldTypeInt, PrimaryKey: &tPK}}
		for _, f := range flds {
			cols = append(cols, datasource.Column{Name: f, Type: "string"})
			fields = append(fields, config.EntityField{Name: f, Column: f, Type: config.FieldTypeString})
		}
		return datasource.Table{Name: name, PrimaryKey: []string{"id"}, Columns: cols},
			config.Entity{Name: name, Table: name, IDColumn: "id", Fields: fields}
	}
	tables := []datasource.Table{
		{Name: "catalog_product", PrimaryKey: []string{"id"}, Columns: productCols,
			ForeignKeys: []datasource.ForeignKey{
				{Columns: []string{"brand_id"}, ReferencedTable: "catalog_brand", ReferencedColumns: []string{"id"}},
				{Columns: []string{"category_id"}, ReferencedTable: "catalog_category", ReferencedColumns: []string{"id"}},
			}},
	}
	entities := []config.Entity{{Name: "catalog_product", Table: "catalog_product", IDColumn: "id", Fields: productFields}}
	narrow := []struct {
		name string
		flds []string
	}{
		{"catalog_brand", []string{"name", "country", "description"}},
		{"catalog_category", []string{"name", "parent_id", "description"}},
		{"catalog_order", []string{"number", "status", "payment_method", "delivery_method", "total", "customer_name"}},
		{"catalog_cart", []string{"session_key", "created_at"}},
		{"catalog_cartitem", []string{"cart_id", "product_id", "quantity"}},
		{"catalog_sitesettings", []string{"key", "value"}},
	}
	for _, n := range narrow {
		tbl, ent := mkNarrow(n.name, n.flds...)
		tables = append(tables, tbl)
		entities = append(entities, ent)
	}
	schema := &datasource.Schema{Driver: "sqlite", Tables: tables}
	cfg := Generate(schema, &config.Config{DataSource: config.DataSourceConfig{Driver: "sqlite", DSN: "test.db"}, Entities: entities})

	compact := GenerateSchemaForLLMCompact(schema, cfg)
	cb, _ := json.Marshal(compact)
	if len(cb) > 3500 {
		t.Errorf("compact db_map too large: %d bytes (want < 3500). Full was 8545 for autoparts.", len(cb))
	}

	full := GenerateSchemaForLLM(schema, cfg)
	fb, _ := json.Marshal(full)
	if len(fb) < len(cb) {
		t.Errorf("full db_map (%d) must be larger than compact (%d)", len(fb), len(cb))
	}
}
