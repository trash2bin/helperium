// Package server_test — бенчмарки search strategies через HTTP.
//
// Замеряет производительность strategy handlers через httptest.Server.
// Использует in-memory SQLite с 1000 продуктов.
//
// Запуск:
//
//	go test -bench=. -benchmem ./data-service/internal/server/tests/ -run=^$ -count=1 -benchtime=1x
package server_test

import (
	"database/sql"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"

	_ "modernc.org/sqlite"

	"github.com/trash2bin/helperium/helperium-go/config"
)

// setupStrategyBenchmark creates in-memory SQLite with 1000 products
// and registers search/filter strategy endpoints.
func setupStrategyBenchmark(b *testing.B) *httptest.Server {
	b.Helper()

	// 1. Create in-memory SQLite with products
	db, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		b.Fatalf("sql.Open: %v", err)
	}
	b.Cleanup(func() { _ = db.Close() })

	_, err = db.Exec(`CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT, category TEXT, price REAL)`)
	if err != nil {
		b.Fatalf("create table: %v", err)
	}

	for i := 0; i < 1000; i++ {
		categories := []string{"Electronics", "Clothing", "Food", "Books"}
		_, err = db.Exec(
			`INSERT INTO products (name, category, price) VALUES (?, ?, ?)`,
			fmt.Sprintf("Product %d", i+1),
			categories[i%4],
			float64((i+1)*10),
		)
		if err != nil {
			b.Fatalf("insert row %d: %v", i, err)
		}
	}

	// 2. Config with strategy endpoints
	t := true
	f := false
	cfg := &config.Config{
		DataSource: config.DataSourceConfig{
			Driver:   "sqlite",
			DSN:      ":memory:",
			ReadOnly: &f,
		},
		Entities: []config.Entity{
			{
				Name:     "products",
				Table:    "products",
				IDColumn: "id",
				Fields: []config.EntityField{
					{Name: "id", Column: "id", Type: config.FieldTypeInt, PrimaryKey: &t},
					{Name: "name", Column: "name", Type: config.FieldTypeString},
					{Name: "category", Column: "category", Type: config.FieldTypeString},
					{Name: "price", Column: "price", Type: config.FieldTypeFloat},
				},
			},
		},
		Endpoints: []config.Endpoint{
			{
				Entity:   "products",
				Path:     "/products/search",
				Method:   "GET",
				Strategy: "search",
				Op:       "strategy",
			},
			{
				Entity:   "products",
				Path:     "/products/filter",
				Method:   "GET",
				Strategy: "filter",
				Op:       "strategy",
			},
		},
	}
	cfg.Normalize()

	// 3. Build router using existing buildTestRouter helper
	return buildTestRouter(b, cfg, db)
}

// BenchmarkSearchStrategy_SelectAndCount measures the current 2-query strategy:
// SELECT COUNT(*) + SELECT ... LIMIT
func BenchmarkSearchStrategy_SelectAndCount(b *testing.B) {
	ts := setupStrategyBenchmark(b)
	b.ResetTimer()

	for i := 0; i < b.N; i++ {
		resp, err := http.Get(ts.URL + "/products/search?pattern=Product&limit=10")
		if err != nil {
			b.Fatal(err)
		}
		resp.Body.Close()
		if resp.StatusCode != 200 {
			b.Errorf("status=%d", resp.StatusCode)
		}
	}
}

// BenchmarkFilterStrategy_SelectAndCount measures filter-only strategy
// (fewer conditions, no text search).
func BenchmarkFilterStrategy_SelectAndCount(b *testing.B) {
	ts := setupStrategyBenchmark(b)
	b.ResetTimer()

	for i := 0; i < b.N; i++ {
		resp, err := http.Get(ts.URL + "/products/filter?category=Electronics&limit=10")
		if err != nil {
			b.Fatal(err)
		}
		resp.Body.Close()
		if resp.StatusCode != 200 {
			b.Errorf("status=%d", resp.StatusCode)
		}
	}
}

// BenchmarkSearchStrategy_Pagination queries with offset to exercise
// the full SELECT + COUNT path with non-trivial offset.
func BenchmarkSearchStrategy_Pagination(b *testing.B) {
	ts := setupStrategyBenchmark(b)
	b.ResetTimer()

	for i := 0; i < b.N; i++ {
		offset := (i * 10) % 990
		resp, err := http.Get(fmt.Sprintf("%s/products/search?pattern=Product&limit=10&offset=%d", ts.URL, offset))
		if err != nil {
			b.Fatal(err)
		}
		resp.Body.Close()
		if resp.StatusCode != 200 {
			b.Errorf("status=%d", resp.StatusCode)
		}
	}
}
