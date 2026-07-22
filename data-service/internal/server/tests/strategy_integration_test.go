// Package server_test — HTTP integration tests for search strategy endpoints.
//
// Tests the full pipeline: HTTP -> chi router -> strategy handler -> SQLite.
// Uses in-memory SQLite with a products table seeded with 1000 rows.
package server_test

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"

	_ "modernc.org/sqlite"

	"github.com/trash2bin/helperium/helperium-go/config"
	"github.com/trash2bin/helperium/data-service/internal/datasource"
	"github.com/trash2bin/helperium/data-service/internal/server"
)

// setupStrategyIntegration creates an in-memory SQLite DB with a products table,
// seeds 1000 rows, builds a config with strategy endpoints, and returns an
// httptest.Server wrapped with tenant middleware.
func setupStrategyIntegration(t *testing.T) *httptest.Server {
	t.Helper()

	sqlDB, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatalf("open in-memory db: %v", err)
	}

	// Create schema
	_, err = sqlDB.Exec(`
		CREATE TABLE products (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			name TEXT NOT NULL,
			category TEXT NOT NULL,
			price REAL NOT NULL
		)
	`)
	if err != nil {
		t.Fatalf("create table: %v", err)
	}

	// Seed 1000 rows across 4 categories
	categories := []string{"Electronics", "Clothing", "Food", "Books"}
	for i := 1; i <= 1000; i++ {
		cat := categories[i%4]
		price := float64(10 + (i % 991))
		name := fmt.Sprintf("Product %d", i)
		if i <= 4 {
			name = fmt.Sprintf("%s Basic %d", cat, i)
		}
		_, err := sqlDB.Exec(
			"INSERT INTO products (name, category, price) VALUES (?, ?, ?)",
			name, cat, price,
		)
		if err != nil {
			t.Fatalf("seed row %d: %v", i, err)
		}
	}

	t.Cleanup(func() { _ = sqlDB.Close() })

	// Build config with strategy endpoints
	cfg := &config.Config{
		DataSource: config.DataSourceConfig{
			Driver:   "sqlite",
			ReadOnly: boolPtr(false),
		},
		Entities: []config.Entity{
			{
				Name:     "products",
				Table:    "products",
				IDColumn: "id",
				Fields: []config.EntityField{
					{Name: "id", Column: "id", Type: config.FieldTypeInt, Nullable: boolPtr(false), PrimaryKey: boolPtr(true)},
					{Name: "name", Column: "name", Type: config.FieldTypeString, Nullable: boolPtr(false)},
					{Name: "category", Column: "category", Type: config.FieldTypeString, Nullable: boolPtr(false)},
					{Name: "price", Column: "price", Type: config.FieldTypeFloat, Nullable: boolPtr(false)},
				},
			},
		},
		Endpoints: []config.Endpoint{
			{Method: "GET", Path: "/health", Op: "builtin_health"},
			// search strategy: one combined grep+filter tool
			{Method: "GET", Path: "/products/search", Op: "", Entity: "products", Strategy: "search"},
			// filter strategy: field-based filters only
			{Method: "GET", Path: "/products/filter", Op: "", Entity: "products", Strategy: "filter"},
		},
	}

	adapter := &testSQLite{db: sqlDB}
	store := server.NewTenantStore(datasource.NewDefaultRegistry(), "")

	router, err := server.NewRouterFromConfig(store, cfg, adapter, nil)
	if err != nil {
		t.Fatalf("NewRouterFromConfig: %v", err)
	}

	// Register tenant instance with the in-memory DB
	inst := &server.TenantInstance{
		ID:         "default",
		Config:     cfg,
		AdapterSub: adapter,
		Router:     router,
	}
	if err := store.RegisterTenantInstance(inst); err != nil {
		t.Fatalf("RegisterTenantInstance: %v", err)
	}

	// Wrap with tenant middleware
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("X-Tenant-ID") == "" && r.URL.Query().Get("tenant") == "" {
			r.Header.Set("X-Tenant-ID", "default")
		}
		server.TenantIDMiddleware("X-Tenant-ID")(store).ServeHTTP(w, r)
	})
	ts := httptest.NewServer(handler)
	t.Cleanup(func() { ts.Close() })
	return ts
}

func assertStatus(t *testing.T, got, want int) {
	t.Helper()
	if got != want {
		t.Errorf("status = %d, want %d", got, want)
	}
}

func jsonGet(t *testing.T, url string) (int, map[string]any) {
	t.Helper()
	resp, err := http.Get(url)
	if err != nil {
		t.Fatalf("GET %s: %v", url, err)
	}
	defer resp.Body.Close()

	var result map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		t.Fatalf("decode %s: %v", url, err)
	}
	return resp.StatusCode, result
}

// ── SearchStrategy tests ───────────────────────────────────────────────────────

func TestStrategy_SearchReturns200(t *testing.T) {
	ts := setupStrategyIntegration(t)
	status, result := jsonGet(t, ts.URL+"/products/search?pattern=Product&limit=10")
	assertStatus(t, status, 200)

	if result["preview"] == nil {
		t.Error("expected non-nil preview for search with pattern")
	}
	total, ok := result["total"].(float64)
	if !ok {
		t.Errorf("expected total field as number, got %T=%v", result["total"], result["total"])
	} else if int(total) < 1 {
		t.Errorf("expected total > 0, got %d", int(total))
	}
}

func TestStrategy_SearchEmptyPattern(t *testing.T) {
	ts := setupStrategyIntegration(t)
	status, result := jsonGet(t, ts.URL+"/products/search?pattern=&limit=10")
	assertStatus(t, status, 400)

	if result["error"] == nil {
		t.Error("expected error for empty pattern")
	}
}

func TestStrategy_SearchCombined(t *testing.T) {
	ts := setupStrategyIntegration(t)
	// names with 'Product' in Electronics category
	status, result := jsonGet(t, ts.URL+"/products/search?pattern=Product&category=Electronics&limit=10")
	assertStatus(t, status, 200)

	if result["preview"] == nil {
		t.Error("expected non-nil preview for search with pattern+filter")
	}
	total, ok := result["total"].(float64)
	if !ok {
		t.Errorf("expected total field as number, got %T=%v", result["total"], result["total"])
	} else if int(total) >= 250 {
		t.Errorf("expected category=Electronics filter to constrain results (total < 250), got total=%d", int(total))
	} else if int(total) < 1 {
		t.Errorf("expected total > 0, got %d", int(total))
	}
}

// ── FilterStrategy tests ───────────────────────────────────────────────────────

func TestStrategy_FilterReturns200(t *testing.T) {
	s := setupStrategyIntegration(t)
	status, result := jsonGet(t, s.URL+"/products/filter?category=Electronics&limit=10")
	assertStatus(t, status, 200)

	if result["preview"] == nil {
		t.Error("expected non-nil preview for filter by category")
	}
	total, ok := result["total"].(float64)
	if !ok {
		t.Errorf("expected total field as number, got %T=%v", result["total"], result["total"])
	} else if int(total) < 1 {
		t.Errorf("expected total > 0, got %d", int(total))
	}
}

func TestStrategy_FilterCombined(t *testing.T) {
	s := setupStrategyIntegration(t)
	status, result := jsonGet(t, s.URL+"/products/filter?category=Electronics&price__gte=50&limit=5")
	assertStatus(t, status, 200)

	if result["preview"] == nil {
		t.Error("expected non-nil preview for combined filter")
	}
	// Verify price filter works: total should be less than unfiltered 250
	total, ok := result["total"].(float64)
	if !ok {
		t.Errorf("expected total field as number, got %T=%v", result["total"], result["total"])
	} else if int(total) < 1 {
		t.Errorf("expected filtered total > 0, got %d", int(total))
	}
}

func TestStrategy_FilterNoResults(t *testing.T) {
	s := setupStrategyIntegration(t)
	status, result := jsonGet(t, s.URL+"/products/filter?category=Electronics&price__gte=99999&limit=5")
	assertStatus(t, status, 200)

	total, ok := result["total"].(float64)
	if !ok {
		t.Errorf("expected total field as number, got %T=%v", result["total"], result["total"])
	} else if int(total) != 0 {
		t.Errorf("expected total=0 for no matches, got %d", int(total))
	}
	returned, ok := result["returned"].(float64)
	if !ok {
		t.Errorf("expected returned field as number, got %T=%v", result["returned"], result["returned"])
	} else if int(returned) != 0 {
		t.Errorf("expected returned=0 for no matches, got %d", int(returned))
	}
}

func TestStrategy_EmptyFilterRejected(t *testing.T) {
	s := setupStrategyIntegration(t)
	status, result := jsonGet(t, s.URL+"/products/filter")
	assertStatus(t, status, 400)

	if result["error"] == nil {
		t.Error("expected error for empty filter request")
	}
}
