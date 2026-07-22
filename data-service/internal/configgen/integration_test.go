//go:build integration

package configgen

import (
	"context"
	"encoding/json"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/trash2bin/helperium/data-service/internal/datasource"
	"github.com/trash2bin/helperium/helperium-go/config"
)

// autopartsDSN возвращает DSN для реальной autoparts PostgreSQL базы.
// Требует запущенного контейнера: docker compose up -d db
func autopartsDSN(t *testing.T) string {
	t.Helper()
	dsn := os.Getenv("AUTOPARTS_DSN")
	if dsn == "" {
		dsn = "postgres://autoparts:autoparts_secret_2024@127.0.0.1:5434/autoparts?sslmode=disable"
	}
	return dsn
}

// connectAutoparts подключается к реальной autoparts PostgreSQL базе.
func connectAutoparts(t *testing.T) datasource.Conn {
	t.Helper()
	adapter := datasource.PostgresAdapter{}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	conn, err := adapter.Connect(ctx, autopartsDSN(t))
	if err != nil {
		t.Fatalf("connect to autoparts: %v\n\nУбедитесь, что контейнер запущен:\n  docker compose up -d db", err)
	}
	t.Cleanup(func() { conn.Close() })
	return conn
}

// TestAutoparts_Introspect проверяет, что Introspect корректно читает
// схему реальной autoparts PostgreSQL базы.
func TestAutoparts_Introspect(t *testing.T) {
	conn := connectAutoparts(t)
	adapter := datasource.PostgresAdapter{}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	schema, err := adapter.Introspect(ctx, conn)
	if err != nil {
		t.Fatalf("introspect: %v", err)
	}

	t.Logf("driver=%s tables=%d", schema.Driver, len(schema.Tables))

	// Ожидаем 17 таблиц (7 catalog_*, 6 auth_*, 4 django_*)
	if len(schema.Tables) < 7 {
		t.Fatalf("expected at least 7 tables, got %d", len(schema.Tables))
	}

	// Индексируем по имени
	byName := make(map[string]datasource.Table)
	for _, tbl := range schema.Tables {
		byName[tbl.Name] = tbl
	}

	// catalog_product должен существовать и иметь FK
	product, ok := byName["public.catalog_product"]
	if !ok {
		t.Fatal("expected table 'public.catalog_product'")
	}
	if len(product.PrimaryKey) == 0 {
		t.Error("catalog_product should have a primary key")
	}
	t.Logf("catalog_product: %d columns, %d FKs, PK=%v",
		len(product.Columns), len(product.ForeignKeys), product.PrimaryKey)

	// catalog_product должен иметь FK на catalog_brand и catalog_category
	fkTargets := make(map[string]string) // local_col → ref_table
	for _, fk := range product.ForeignKeys {
		for _, col := range fk.Columns {
			fkTargets[col] = fk.ReferencedTable
		}
	}
	if ref, ok := fkTargets["brand_id"]; !ok || !strings.Contains(ref, "catalog_brand") {
		t.Errorf("expected FK brand_id → catalog_brand, got %q", ref)
	}
	if ref, ok := fkTargets["category_id"]; !ok || !strings.Contains(ref, "catalog_category") {
		t.Errorf("expected FK category_id → catalog_category, got %q", ref)
	}

	// catalog_cartitem должен иметь FK на cart и product
	cartitem, ok := byName["public.catalog_cartitem"]
	if !ok {
		t.Fatal("expected table 'public.catalog_cartitem'")
	}
	cartitemFKs := make(map[string]string)
	for _, fk := range cartitem.ForeignKeys {
		for _, col := range fk.Columns {
			cartitemFKs[col] = fk.ReferencedTable
		}
	}
	if ref, ok := cartitemFKs["cart_id"]; !ok || !strings.Contains(ref, "catalog_cart") {
		t.Errorf("expected FK cart_id → catalog_cart, got %q", ref)
	}
	if ref, ok := cartitemFKs["product_id"]; !ok || !strings.Contains(ref, "catalog_product") {
		t.Errorf("expected FK product_id → catalog_product, got %q", ref)
	}

	// catalog_category self-referencing FK (parent_id)
	category, ok := byName["public.catalog_category"]
	if !ok {
		t.Fatal("expected table 'public.catalog_category'")
	}
	for _, fk := range category.ForeignKeys {
		for _, col := range fk.Columns {
			if col == "parent_id" && !strings.Contains(fk.ReferencedTable, "catalog_category") {
				t.Errorf("expected FK parent_id → catalog_category, got %q", fk.ReferencedTable)
			}
		}
	}

	// Bool колонки в catalog_product
	for _, col := range product.Columns {
		if col.Name == "is_available" && col.Type != datasource.TypeBool {
			t.Errorf("expected is_available to be bool, got %s", col.Type)
		}
		if col.Name == "is_popular" && col.Type != datasource.TypeBool {
			t.Errorf("expected is_popular to be bool, got %s", col.Type)
		}
	}

	// Datetime колонки
	for _, col := range product.Columns {
		if col.Name == "created_at" && col.Type != datasource.TypeDatetime {
			t.Errorf("expected created_at to be datetime, got %s", col.Type)
		}
	}
}

// TestAutoparts_Generate проверяет полный пайплайн: Introspect → Generate
// на реальной autoparts PostgreSQL базе.
func TestAutoparts_Generate(t *testing.T) {
	conn := connectAutoparts(t)
	adapter := datasource.PostgresAdapter{}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	schema, err := adapter.Introspect(ctx, conn)
	if err != nil {
		t.Fatalf("introspect: %v", err)
	}

	cfg := Generate(schema, &config.Config{
		DataSource: config.DataSourceConfig{
			Driver: "postgres",
			DSN:    autopartsDSN(t),
		},
	})

	// Write config to temp file for inspection
	data, err := json.MarshalIndent(cfg, "", "  ")
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	tmpFile := t.TempDir() + "/autoparts-generated.json"
	if err := os.WriteFile(tmpFile, data, 0644); err != nil {
		t.Fatalf("write: %v", err)
	}
	t.Logf("config written to %s (%d bytes)", tmpFile, len(data))

	// === Entities ===
	t.Run("entities", func(t *testing.T) {
		// After skip rules: 7 catalog_* tables (auth_*, django_* skipped)
		if len(cfg.Entities) != 7 {
			t.Errorf("expected 7 entities (catalog_*), got %d", len(cfg.Entities))
			for _, e := range cfg.Entities {
				t.Logf("  entity: %s (table=%s)", e.Name, e.Table)
			}
		}

		// Verify expected entities exist
		entityNames := make(map[string]bool)
		for _, e := range cfg.Entities {
			entityNames[e.Name] = true
		}
		for _, expected := range []string{
			"catalog_brand", "catalog_cart", "catalog_cartitem",
			"catalog_category", "catalog_order", "catalog_product",
			"catalog_sitesettings",
		} {
			if !entityNames[expected] {
				t.Errorf("expected entity %q", expected)
			}
		}
	})

	// === FK Relations ===
	t.Run("relations", func(t *testing.T) {
		var product *config.Entity
		for i, e := range cfg.Entities {
			if e.Name == "catalog_product" {
				product = &cfg.Entities[i]
				break
			}
		}
		if product == nil {
			t.Fatal("expected catalog_product entity")
		}

		// Should have 2 relations: brand_id → catalog_brand, category_id → catalog_category
		if len(product.Relations) != 2 {
			t.Errorf("expected 2 relations on catalog_product, got %d", len(product.Relations))
			for _, r := range product.Relations {
				t.Logf("  relation: %s → %s (kind=%s)", r.LocalFK, r.Table, r.Kind)
			}
		}

		relMap := make(map[string]config.Relation)
		for _, r := range product.Relations {
			relMap[r.LocalFK] = r
		}
		if r, ok := relMap["brand_id"]; ok {
			if !strings.Contains(r.Table, "catalog_brand") {
				t.Errorf("brand_id relation should reference catalog_brand, got %q", r.Table)
			}
		} else {
			t.Error("missing brand_id relation")
		}

		// cartitem relations
		var cartitem *config.Entity
		for i, e := range cfg.Entities {
			if e.Name == "catalog_cartitem" {
				cartitem = &cfg.Entities[i]
				break
			}
		}
		if cartitem == nil {
			t.Fatal("expected catalog_cartitem entity")
		}
		if len(cartitem.Relations) != 2 {
			t.Errorf("expected 2 relations on catalog_cartitem, got %d", len(cartitem.Relations))
		}
	})

	// === Bool Filters ===
	t.Run("bool_filters", func(t *testing.T) {
		// find_catalog_product should have bool filter params
		var findEp *config.Endpoint
		for i, ep := range cfg.Endpoints {
			if ep.Op == config.OpFind && ep.Entity == "catalog_product" {
				findEp = &cfg.Endpoints[i]
				break
			}
		}
		if findEp == nil {
			t.Fatal("expected find endpoint for catalog_product")
		}

		boolParams := make([]string, 0)
		for _, p := range findEp.Params {
			if p.Type == config.ParamTypeBool {
				boolParams = append(boolParams, p.Name)
			}
		}
		expectedBools := []string{"is_available", "is_popular", "is_new", "is_bestseller", "is_promo", "is_active"}
		for _, name := range expectedBools {
			found := false
			for _, bp := range boolParams {
				if bp == name {
					found = true
					break
				}
			}
			if !found {
				t.Errorf("expected bool filter param %q, bool params: %v", name, boolParams)
			}
		}
		t.Logf("bool filter params: %v", boolParams)
	})

	// === Datetime Filters ===
	t.Run("datetime_filters", func(t *testing.T) {
		var findEp *config.Endpoint
		for i, ep := range cfg.Endpoints {
			if ep.Op == config.OpFind && ep.Entity == "catalog_product" {
				findEp = &cfg.Endpoints[i]
				break
			}
		}
		if findEp == nil {
			t.Fatal("expected find endpoint for catalog_product")
		}

		var hasCreatedAt bool
		for _, p := range findEp.Params {
			if p.Name == "created_at" && p.Type == config.ParamTypeString {
				hasCreatedAt = true
				if !strings.Contains(p.Description, "ISO-8601") {
					t.Errorf("created_at description should mention ISO-8601, got %q", p.Description)
				}
			}
		}
		if !hasCreatedAt {
			t.Error("expected created_at string filter param (datetime)")
		}
	})

	// === Count Endpoints ===
	t.Run("count_endpoints", func(t *testing.T) {
		countEndpoints := 0
		for _, ep := range cfg.Endpoints {
			if ep.Op == config.OpCount {
				countEndpoints++
			}
		}
		if countEndpoints != 7 {
			t.Errorf("expected 7 count endpoints, got %d", countEndpoints)
		}
	})

	// === MCP Tools ===
	t.Run("mcp_tools", func(t *testing.T) {
		// Should have find, get, list, count, distinct for each entity
		toolNames := make(map[string]bool)
		for _, tool := range cfg.MCPTools {
			toolNames[tool.Name] = true
		}

		// Key tools
		for _, expected := range []string{
			"search_catalog_product",
			"get_catalog_product",
			"search_catalog_brand",
			"get_catalog_brand",
			"count_catalog_product",
			"distinct_catalog_product",
		} {
			if !toolNames[expected] {
				t.Errorf("expected MCP tool %q", expected)
			}
		}

		// No double underscores in tool names
		for _, tool := range cfg.MCPTools {
			if strings.Contains(tool.Name, "__") {
				t.Errorf("tool name has double underscore: %s", tool.Name)
			}
		}

		t.Logf("total MCP tools: %d", len(cfg.MCPTools))
	})

	// === Custom Queries (Navigation) ===
	t.Run("custom_queries", func(t *testing.T) {
		// Should have navigation queries from FKs
		if len(cfg.CustomQueries) == 0 {
			t.Error("expected custom queries from FK relations, got 0")
		}

		// Check for catalog_product → catalog_brand navigation
		hasProductByBrand := false
		for queryID := range cfg.CustomQueries {
			if strings.Contains(queryID, "catalog_product") && strings.Contains(queryID, "catalog_brand") {
				hasProductByBrand = true
				break
			}
		}
		if !hasProductByBrand {
			t.Error("expected custom query for catalog_product_by_catalog_brand")
		}

		t.Logf("custom queries: %d", len(cfg.CustomQueries))
		for id, cq := range cfg.CustomQueries {
			t.Logf("  %s: %s", id, cq.Description)
		}
	})

	// === No double underscores in any endpoint path ===
	t.Run("clean_paths", func(t *testing.T) {
		for _, ep := range cfg.Endpoints {
			if strings.Contains(ep.Path, "//") {
				t.Errorf("endpoint has double slash: %s", ep.Path)
			}
		}
	})
}

// TestAutoparts_ToolCount проверяет, что генерируется разумное количество тулов
// (не 90+, не 0).
func TestAutoparts_ToolCount(t *testing.T) {
	conn := connectAutoparts(t)
	adapter := datasource.PostgresAdapter{}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	schema, err := adapter.Introspect(ctx, conn)
	if err != nil {
		t.Fatalf("introspect: %v", err)
	}

	cfg := Generate(schema, &config.Config{
		DataSource: config.DataSourceConfig{
			Driver: "postgres",
			DSN:    autopartsDSN(t),
		},
	})

	t.Logf("entities=%d endpoints=%d tools=%d custom_queries=%d",
		len(cfg.Entities), len(cfg.Endpoints), len(cfg.MCPTools), len(cfg.CustomQueries))

	// Sanity bounds
	if len(cfg.Entities) < 5 || len(cfg.Entities) > 20 {
		t.Errorf("unexpected entity count: %d", len(cfg.Entities))
	}
	if len(cfg.MCPTools) < 10 || len(cfg.MCPTools) > 60 {
		t.Errorf("unexpected tool count: %d", len(cfg.MCPTools))
	}
}
