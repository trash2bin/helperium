package handlers_test

import (
	"context"
	"database/sql"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	_ "modernc.org/sqlite"

	"github.com/trash2bin/helperium/data-service/internal/runtime"
	"github.com/trash2bin/helperium/data-service/internal/runtime/handlers"
	"github.com/trash2bin/helperium/helperium-go/config"
)

// TestStatsHandler_Success — несколько счётчиков, возвращает корректные значения
func TestStatsHandler_Success(t *testing.T) {
	db, _ := sql.Open("sqlite", ":memory:")
	defer db.Close() //nolint:errcheck
	db.SetMaxOpenConns(1)

	_, _ = db.ExecContext(context.Background(), `
		CREATE TABLE customers (
			id INTEGER PRIMARY KEY,
			name TEXT NOT NULL,
			email TEXT NOT NULL
		);
		INSERT INTO customers (id, name, email) VALUES
			(1, 'John Doe', 'john@example.com'),
			(2, 'Jane Smith', 'jane@example.com');
	`)

	adapter := &testAdapter{db: db}

	customerEntity := runtime.Entity{
		Name:     "customer",
		Table:    "customers",
		IDColumn: "id",
		Fields: []runtime.EntityField{
			{Name: "id", Column: "id", Type: "int", PrimaryKey: true},
			{Name: "name", Column: "name", Type: "string"},
			{Name: "email", Column: "email", Type: "string"},
		},
	}
	resolver, _ := runtime.NewEntityResolver([]runtime.Entity{customerEntity})
	builder := runtime.NewBuilder(adapter)

	ctx := &handlers.Context{
		DB:           adapter,
		Adapter:      adapter,
		Builder:      builder,
		Resolver:     resolver,
		URLParam:     func(_ *http.Request, _ string) string { return "" },
		TenantIDFunc: func(_ *http.Request) string { return "" },
	}

	cfg := &config.Config{
		Stats: &config.StatsConfig{
			Counters: []config.Counter{
				{Name: "total_customers", Entity: "customer"},
			},
		},
	}

	h := handlers.StatsHandler(ctx, cfg)

	req := httptest.NewRequest(http.MethodGet, "/stats", nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	if !strings.Contains(w.Body.String(), "total_customers") {
		t.Errorf("response missing total_customers: %s", w.Body.String())
	}
	if !strings.Contains(w.Body.String(), `"total_customers":2`) {
		t.Errorf("expected total_customers:2, got: %s", w.Body.String())
	}
}

// TestStatsHandler_EmptyConfig — нет счётчиков → пустой JSON
func TestStatsHandler_EmptyConfig(t *testing.T) {
	db, _ := sql.Open("sqlite", ":memory:")
	defer db.Close() //nolint:errcheck

	adapter := &testAdapter{db: db}
	resolver, _ := runtime.NewEntityResolver([]runtime.Entity{})
	builder := runtime.NewBuilder(adapter)

	ctx := &handlers.Context{
		DB:           adapter,
		Adapter:      adapter,
		Builder:      builder,
		Resolver:     resolver,
		URLParam:     func(_ *http.Request, _ string) string { return "" },
		TenantIDFunc: func(_ *http.Request) string { return "" },
	}

	// Stats is nil
	h := handlers.StatsHandler(ctx, &config.Config{})

	req := httptest.NewRequest(http.MethodGet, "/stats", nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	if w.Body.String() != "{}\n" {
		t.Errorf("expected empty JSON object, got: %s", w.Body.String())
	}
}

// TestStatsHandler_EmptyCounters — Stats.Counters пуст → пустой JSON
func TestStatsHandler_EmptyCounters(t *testing.T) {
	db, _ := sql.Open("sqlite", ":memory:")
	defer db.Close() //nolint:errcheck

	adapter := &testAdapter{db: db}
	resolver, _ := runtime.NewEntityResolver([]runtime.Entity{})
	builder := runtime.NewBuilder(adapter)

	ctx := &handlers.Context{
		DB:           adapter,
		Adapter:      adapter,
		Builder:      builder,
		Resolver:     resolver,
		URLParam:     func(_ *http.Request, _ string) string { return "" },
		TenantIDFunc: func(_ *http.Request) string { return "" },
	}

	h := handlers.StatsHandler(ctx, &config.Config{Stats: &config.StatsConfig{}})

	req := httptest.NewRequest(http.MethodGet, "/stats", nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// TestStatsHandler_UnknownEntity — счётчик для несуществующей сущности → skip (не падает)
func TestStatsHandler_UnknownEntity(t *testing.T) {
	db, _ := sql.Open("sqlite", ":memory:")
	defer db.Close() //nolint:errcheck

	adapter := &testAdapter{db: db}
	resolver, _ := runtime.NewEntityResolver([]runtime.Entity{})
	builder := runtime.NewBuilder(adapter)

	ctx := &handlers.Context{
		DB:           adapter,
		Adapter:      adapter,
		Builder:      builder,
		Resolver:     resolver,
		URLParam:     func(_ *http.Request, _ string) string { return "" },
		TenantIDFunc: func(_ *http.Request) string { return "" },
	}

	cfg := &config.Config{
		Stats: &config.StatsConfig{
			Counters: []config.Counter{
				{Name: "ghosts", Entity: "nonexistent_entity"},
			},
		},
	}

	h := handlers.StatsHandler(ctx, cfg)

	req := httptest.NewRequest(http.MethodGet, "/stats", nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	// Несуществующая сущность просто пропускается — результат пустой
	body := w.Body.String()
	if !strings.Contains(body, "{}") {
		t.Errorf("expected empty result for unknown entity: %s", body)
	}
}

// TestStatsHandler_DBError — ошибка БД во время COUNT → fail-soft: counter
// пропускается, /stats отдаёт 200 с остальными счётчиками (раньше: 500 на весь эндпоинт).
func TestStatsHandler_DBError(t *testing.T) {
	db, _ := sql.Open("sqlite", ":memory:")
	defer db.Close() //nolint:errcheck

	adapter := &errorAdapter{
		db: &testAdapter{db: db},
		errFunc: func(_ context.Context, _ string, _ ...any) (*sql.Rows, error) {
			return nil, fmt.Errorf("database error")
		},
	}

	customerEntity := runtime.Entity{
		Name:     "customer",
		Table:    "customers",
		IDColumn: "id",
		Fields: []runtime.EntityField{
			{Name: "id", Column: "id", Type: "int", PrimaryKey: true},
			{Name: "name", Column: "name", Type: "string"},
		},
	}
	resolver, _ := runtime.NewEntityResolver([]runtime.Entity{customerEntity})
	builder := runtime.NewBuilder(adapter)

	// errorAdapter не реализует PingContext — а StatsHandler не вызывает Ping,
	// он вызывает QueryContext. Поэтому errorAdapter с errFunc перехватит QueryContext.
	ctx := &handlers.Context{
		DB:           adapter,
		Adapter:      adapter,
		Builder:      builder,
		Resolver:     resolver,
		URLParam:     func(_ *http.Request, _ string) string { return "" },
		TenantIDFunc: func(_ *http.Request) string { return "" },
	}

	cfg := &config.Config{
		Stats: &config.StatsConfig{
			Counters: []config.Counter{
				{Name: "total_customers", Entity: "customer"},
			},
		},
	}

	h := handlers.StatsHandler(ctx, cfg)

	req := httptest.NewRequest(http.MethodGet, "/stats", nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)

	// Fail-soft: битый counter пропускается, ответ 200 без его ключа.
	if w.Code != http.StatusOK {
		t.Errorf("expected 200 (fail-soft), got %d: %s", w.Code, w.Body.String())
	}
	if strings.Contains(w.Body.String(), "total_customers") {
		t.Errorf("failed counter should be skipped: %s", w.Body.String())
	}
}

// TestStatsHandler_RowFilterUnknownColumn_SkipsBadCounter — RowFilter на
// несуществующую колонку: один counter падает, остальные считаются (200).
// Баг C6: раньше весь /stats валился 500.
func TestStatsHandler_RowFilterUnknownColumn_SkipsBadCounter(t *testing.T) {
	db, _ := sql.Open("sqlite", ":memory:")
	defer db.Close() //nolint:errcheck
	// :memory: — своя БД на коннект: фиксируем один коннект.
	db.SetMaxOpenConns(1)

	_, _ = db.ExecContext(context.Background(), `
		CREATE TABLE customers (
			id INTEGER PRIMARY KEY,
			name TEXT NOT NULL
		);
		INSERT INTO customers (id, name) VALUES (1, 'John'), (2, 'Jane');
		CREATE TABLE audit_log (
			id INTEGER PRIMARY KEY,
			msg TEXT NOT NULL,
			tenant_id TEXT NOT NULL
		);
		INSERT INTO audit_log (id, msg, tenant_id) VALUES
			(1, 'a', 'tenant-a'), (2, 'b', 'tenant-a'), (3, 'c', 'tenant-a');
	`)

	adapter := &testAdapter{db: db}

	customerEntity := runtime.Entity{
		Name:     "customer",
		Table:    "customers",
		IDColumn: "id",
		Fields: []runtime.EntityField{
			{Name: "id", Column: "id", Type: "int", PrimaryKey: true},
			{Name: "name", Column: "name", Type: "string"},
		},
	}
	auditEntity := runtime.Entity{
		Name:     "audit_log",
		Table:    "audit_log",
		IDColumn: "id",
		Fields: []runtime.EntityField{
			{Name: "id", Column: "id", Type: "int", PrimaryKey: true},
			{Name: "msg", Column: "msg", Type: "string"},
			{Name: "tenant_id", Column: "tenant_id", Type: "string"},
		},
	}
	resolver, _ := runtime.NewEntityResolver([]runtime.Entity{customerEntity, auditEntity})
	builder := runtime.NewBuilder(adapter)

	ctx := &handlers.Context{
		DB:       adapter,
		Adapter:  adapter,
		Builder:  builder,
		Resolver: resolver,
		URLParam: func(_ *http.Request, _ string) string { return "" },
		TenantIDFunc: func(_ *http.Request) string { return "tenant-a" },
		// RowFilter ссылается на tenant_id, которой НЕТ в таблице customers.
		// audit_log — БЕЗ RowFilter (healthy counter).
		Auth: &config.AuthConfig{
			Strategy: config.AuthStrategyHeader,
			RowFilters: []config.RowFilter{
				{Entity: "customer", Where: `"tenant_id" = :tenant_id`}, // битый: tenant_id нет в customers
				{Entity: "audit_log", Where: `"tenant_id" = :tenant_id`}, // healthy: есть
			},
		},
	}

	cfg := &config.Config{
		Stats: &config.StatsConfig{
			Counters: []config.Counter{
				{Name: "customers_with_tenant", Entity: "customer"}, // битый (нет tenant_id) → 0 или skip
				{Name: "audit_total", Entity: "audit_log"},         // healthy → 3
			},
		},
	}

	h := handlers.StatsHandler(ctx, cfg)
	req := httptest.NewRequest(http.MethodGet, "/stats", nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	body := w.Body.String()
	// На sqlite несуществующая колонка в WHERE даёт 0 (не ошибку) — bad counter
	// вернёт 0, а НЕ 500 на весь /stats (главное: эндпоинт жив, healthy counter ок).
	if !strings.Contains(body, `"audit_total":3`) {
		t.Errorf("healthy counter should be counted: %s", body)
	}
}
