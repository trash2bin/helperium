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
	"github.com/trash2bin/helperium/helperium-go/config"
)

// TestStatsHandler_TenantFilter — HIGH: /stats не учитывал tenant-фильтр.
// В multi-tenant конфиге (AuthStrategyHeader + row_filters) StatsHandler
// возвращал глобальные счётчики по ВСЕМ тенантам вместо своего.
func TestStatsHandler_TenantFilter(t *testing.T) {
	db, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close() //nolint:errcheck
	db.SetMaxOpenConns(1)

	_, err = db.ExecContext(context.Background(), `
		CREATE TABLE customers (
			id INTEGER PRIMARY KEY,
			name TEXT NOT NULL,
			tenant_id TEXT NOT NULL
		);
		INSERT INTO customers (id, name, tenant_id) VALUES
			(1, 'John Doe', 'tenant-a'),
			(2, 'Jane Smith', 'tenant-a'),
			(3, 'Bob Jones', 'tenant-b');
	`)
	if err != nil {
		t.Fatal(err)
	}

	adapter := &testAdapter{db: db}

	customerEntity := runtime.Entity{
		Name:     "customer",
		Table:    "customers",
		IDColumn: "id",
		Fields: []runtime.EntityField{
			{Name: "id", Column: "id", Type: "int", PrimaryKey: true},
			{Name: "name", Column: "name", Type: "string"},
			{Name: "tenant_id", Column: "tenant_id", Type: "string"},
		},
	}
	resolver, err := runtime.NewEntityResolver([]runtime.Entity{customerEntity})
	if err != nil {
		t.Fatal(err)
	}
	builder := runtime.NewBuilder(adapter)

	ctx := &handlers.Context{
		DB:       adapter,
		Adapter:  adapter,
		Builder:  builder,
		Resolver: resolver,
		Auth: &config.AuthConfig{
			Strategy: config.AuthStrategyHeader,
			RowFilters: []config.RowFilter{
				{Entity: "customer", Where: `"tenant_id" = :tenant_id`},
			},
		},
		TenantIDFunc: func(_ *http.Request) string { return "tenant-a" },
		URLParam:     func(_ *http.Request, _ string) string { return "" },
	}

	cfg := &config.Config{
		Stats: &config.StatsConfig{
			Counters: []config.Counter{
				{Name: "customers_total", Entity: "customer"},
			},
		},
	}

	h := handlers.StatsHandler(ctx, cfg)
	req := httptest.NewRequest(http.MethodGet, "/stats", nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)

	t.Logf("stats response body: %s", w.Body.String())

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	var body map[string]int
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
		t.Fatalf("failed to parse response: %v, body: %s", err, w.Body.String())
	}

	// tenant-a имеет 2 записи. До фикса /stats возвращал 3 (все тенанты).
	if got := body["customers_total"]; got != 2 {
		t.Errorf("BUG CONFIRMED: customers_total = %d, want 2 (tenant-a only). "+
			"StatsHandler ignores tenant filter — cross-tenant leak.", got)
	}
}

// TestStatsHandler_TenantFilter_WithRawFilter — counter.Filter + tenant-фильтр
// комбинируются: WHERE (Filter) AND tenantWhere.
func TestStatsHandler_TenantFilter_WithRawFilter(t *testing.T) {
	db, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close() //nolint:errcheck
	db.SetMaxOpenConns(1)

	_, err = db.ExecContext(context.Background(), `
		CREATE TABLE customers (
			id INTEGER PRIMARY KEY,
			name TEXT NOT NULL,
			active INTEGER NOT NULL,
			tenant_id TEXT NOT NULL
		);
		INSERT INTO customers (id, name, active, tenant_id) VALUES
			(1, 'John Doe', 1, 'tenant-a'),
			(2, 'Jane Smith', 0, 'tenant-a'),
			(3, 'Bob Jones', 1, 'tenant-b');
	`)
	if err != nil {
		t.Fatal(err)
	}

	adapter := &testAdapter{db: db}

	customerEntity := runtime.Entity{
		Name:     "customer",
		Table:    "customers",
		IDColumn: "id",
		Fields: []runtime.EntityField{
			{Name: "id", Column: "id", Type: "int", PrimaryKey: true},
			{Name: "name", Column: "name", Type: "string"},
			{Name: "active", Column: "active", Type: "int"},
			{Name: "tenant_id", Column: "tenant_id", Type: "string"},
		},
	}
	resolver, err := runtime.NewEntityResolver([]runtime.Entity{customerEntity})
	if err != nil {
		t.Fatal(err)
	}
	builder := runtime.NewBuilder(adapter)

	ctx := &handlers.Context{
		DB:       adapter,
		Adapter:  adapter,
		Builder:  builder,
		Resolver: resolver,
		Auth: &config.AuthConfig{
			Strategy: config.AuthStrategyHeader,
			RowFilters: []config.RowFilter{
				{Entity: "customer", Where: `"tenant_id" = :tenant_id`},
			},
		},
		TenantIDFunc: func(_ *http.Request) string { return "tenant-a" },
		URLParam:     func(_ *http.Request, _ string) string { return "" },
	}

	cfg := &config.Config{
		Stats: &config.StatsConfig{
			Counters: []config.Counter{
				{Name: "active_customers", Entity: "customer", Filter: `"active" = 1`},
			},
		},
	}

	h := handlers.StatsHandler(ctx, cfg)
	req := httptest.NewRequest(http.MethodGet, "/stats", nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)

	t.Logf("stats response body: %s", w.Body.String())

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	var body map[string]int
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
		t.Fatalf("failed to parse response: %v, body: %s", err, w.Body.String())
	}

	// tenant-a + active=1 → только John Doe. До фикса — 2 (John + Bob, оба active).
	if got := body["active_customers"]; got != 1 {
		t.Errorf("BUG CONFIRMED: active_customers = %d, want 1. "+
			"Filter+tenant combo wrong (got %d)", got, got)
	}
}

// TestStatsHandler_TenantFilter_NoLeakSQL проверяет, что SQL-запрос содержит
// tenant-условие (для диагностики; основной assert — в двух тестах выше).
func TestStatsHandler_TenantFilter_NoLeakSQL(t *testing.T) {
	db, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close() //nolint:errcheck
	db.SetMaxOpenConns(1)

	_, err = db.ExecContext(context.Background(), `
		CREATE TABLE customers (
			id INTEGER PRIMARY KEY,
			name TEXT NOT NULL,
			tenant_id TEXT NOT NULL
		);
		INSERT INTO customers (id, name, tenant_id) VALUES
			(1, 'John Doe', 'tenant-a'),
			(2, 'Jane Smith', 'tenant-a');
	`)
	if err != nil {
		t.Fatal(err)
	}

	adapter := &testAdapter{db: db}

	customerEntity := runtime.Entity{
		Name:     "customer",
		Table:    "customers",
		IDColumn: "id",
		Fields: []runtime.EntityField{
			{Name: "id", Column: "id", Type: "int", PrimaryKey: true},
			{Name: "name", Column: "name", Type: "string"},
			{Name: "tenant_id", Column: "tenant_id", Type: "string"},
		},
	}
	resolver, err := runtime.NewEntityResolver([]runtime.Entity{customerEntity})
	if err != nil {
		t.Fatal(err)
	}
	builder := runtime.NewBuilder(adapter)

	ctx := &handlers.Context{
		DB:           adapter,
		Adapter:      adapter,
		Builder:      builder,
		Resolver:     resolver,
		TenantIDFunc: func(_ *http.Request) string { return "" },
		URLParam:     func(_ *http.Request, _ string) string { return "" },
	}

	cfg := &config.Config{
		Stats: &config.StatsConfig{
			Counters: []config.Counter{
				{Name: "customers_total", Entity: "customer"},
			},
		},
	}

	// Пустой tenantID + auth==nil → tenant-фильтр не применяется, SQL без WHERE.
	h := handlers.StatsHandler(ctx, cfg)
	req := httptest.NewRequest(http.MethodGet, "/stats", nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	// Без auth — запрос должен пройти без tenant-условия (валидный SQL).
	var body map[string]int
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
		t.Fatalf("failed to parse response: %v, body: %s", err, w.Body.String())
	}
	if got := body["customers_total"]; got != 2 {
		t.Errorf("no-auth stats: customers_total = %d, want 2", got)
	}

	// Убеждаемся, что лог/SQL-путь не падает при counter.Filter без tenant.
	cfg2 := &config.Config{
		Stats: &config.StatsConfig{
			Counters: []config.Counter{
				{Name: "active_customers", Entity: "customer", Filter: `"active" = 1`},
			},
		},
	}
	w2 := httptest.NewRecorder()
	handlers.StatsHandler(ctx, cfg2).ServeHTTP(w2, req)
	if w2.Code != http.StatusOK {
		t.Fatalf("no-auth + Filter: expected 200, got %d: %s", w2.Code, w2.Body.String())
	}
}
