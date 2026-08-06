package handlers_test

import (
	"context"
	"database/sql"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	_ "modernc.org/sqlite"

	"github.com/trash2bin/helperium/data-service/internal/runtime"
	"github.com/trash2bin/helperium/data-service/internal/runtime/handlers"
)

// TestDistinctHandler_TypedValues — Задача 2 (MEDIUM):
// distinct по числовой колонке отдаёт числа, по bool — bool, а не строки.
func TestDistinctHandler_TypedValues(t *testing.T) {
	db, _ := sql.Open("sqlite", ":memory:")
	defer db.Close() //nolint:errcheck
	db.SetMaxOpenConns(1)

	_, _ = db.ExecContext(context.Background(), `
		CREATE TABLE products (
			id INTEGER PRIMARY KEY,
			name TEXT NOT NULL,
			price REAL NOT NULL,
			in_stock BOOLEAN NOT NULL
		);
		INSERT INTO products (id, name, price, in_stock) VALUES
			(1, 'A', 95.5, 1),
			(2, 'B', 10.0, 0),
			(3, 'C', 95.5, 1);
	`)

	adapter := &testAdapter{db: db}

	productEntity := runtime.Entity{
		Name:     "product",
		Table:    "products",
		IDColumn: "id",
		Fields: []runtime.EntityField{
			{Name: "id", Column: "id", Type: "int", PrimaryKey: true},
			{Name: "name", Column: "name", Type: "string"},
			{Name: "price", Column: "price", Type: "float"},
			{Name: "in_stock", Column: "in_stock", Type: "bool"},
		},
	}
	resolver, _ := runtime.NewEntityResolver([]runtime.Entity{productEntity})
	builder := runtime.NewBuilder(adapter)

	ctx := &handlers.Context{
		DB:           adapter,
		Adapter:      adapter,
		Builder:      builder,
		Resolver:     resolver,
		URLParam:     func(_ *http.Request, _ string) string { return "" },
		TenantIDFunc: func(_ *http.Request) string { return "" },
	}

	t.Run("float column returns numbers", func(t *testing.T) {
		req := httptest.NewRequest(http.MethodGet, "/products/distinct?column=price", nil)
		rec := httptest.NewRecorder()
		handlers.DistinctHandler(ctx, "product")(rec, req)

		if rec.Code != http.StatusOK {
			t.Fatalf("status = %d, body %s", rec.Code, rec.Body.String())
		}
		var body struct {
			Values []json.RawMessage `json:"values"`
		}
		if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
			t.Fatalf("unmarshal: %v", err)
		}
		// Должно быть 2 уникальных числа: 95.5 и 10.
		if len(body.Values) != 2 {
			t.Fatalf("values = %d, want 2 (%s)", len(body.Values), rec.Body.String())
		}
		for _, raw := range body.Values {
			var f float64
			if err := json.Unmarshal(raw, &f); err != nil {
				t.Errorf("value %s is not a JSON number: %v", raw, err)
			}
		}
	})

	t.Run("bool column returns booleans", func(t *testing.T) {
		req := httptest.NewRequest(http.MethodGet, "/products/distinct?column=in_stock", nil)
		rec := httptest.NewRecorder()
		handlers.DistinctHandler(ctx, "product")(rec, req)

		if rec.Code != http.StatusOK {
			t.Fatalf("status = %d, body %s", rec.Code, rec.Body.String())
		}
		var body struct {
			Values []json.RawMessage `json:"values"`
		}
		if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
			t.Fatalf("unmarshal: %v", err)
		}
		if len(body.Values) != 2 {
			t.Fatalf("values = %d, want 2 (%s)", len(body.Values), rec.Body.String())
		}
		for _, raw := range body.Values {
			var b bool
			if err := json.Unmarshal(raw, &b); err != nil {
				t.Errorf("value %s is not a JSON bool: %v", raw, err)
			}
		}
	})

	t.Run("string column stays strings", func(t *testing.T) {
		req := httptest.NewRequest(http.MethodGet, "/products/distinct?column=name", nil)
		rec := httptest.NewRecorder()
		handlers.DistinctHandler(ctx, "product")(rec, req)

		if rec.Code != http.StatusOK {
			t.Fatalf("status = %d, body %s", rec.Code, rec.Body.String())
		}
		var body struct {
			Values []string `json:"values"`
		}
		if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
			t.Fatalf("unmarshal: %v", err)
		}
		if len(body.Values) != 3 {
			t.Fatalf("values = %d, want 3 (%s)", len(body.Values), rec.Body.String())
		}
	})
}
