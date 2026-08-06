package handlers_test

// Тесты из внешнего аудита (P0-1): tenant isolation — fail-open.
//
// tenantFilter (row_filter.go) возвращает ("", nil) если:
//   - auth == nil (стратегия не настроена)
//   - auth.Strategy != header
//   - нет RowFilter для entity
//   - tenant_id пуст
//
// Во всех случаях SQL выполняется БЕЗ WHERE tenant_id → тенант видит чужие
// строки. Это fail-open. Тесты ниже фиксируют текущее поведение:
// те, что помечены «ДОЛЖЕН ПАДАТЬ», документируют уязвимость и станут
// регрессией, когда изоляцию сделают fail-closed.
//
// Используем COUNT-эндпоинт: он не требует filter-параметров и возвращает
// {"count": N} по всем строкам — идеален для детекции утечки.
// (filter-стратегия 400-ит без параметров, поэтому count надёжнее.)

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

type countResponse struct {
	Count int `json:"count"`
}

func makeTenantDB(t *testing.T) (*sql.DB, *testAdapter) {
	t.Helper()
	db, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { db.Close() }) //nolint:errcheck
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
	return db, &testAdapter{db: db}
}

func customerEntity() runtime.Entity {
	return runtime.Entity{
		Name:     "customer",
		Table:    "customers",
		IDColumn: "id",
		Fields: []runtime.EntityField{
			{Name: "id", Column: "id", Type: "int", PrimaryKey: true},
			{Name: "name", Column: "name", Type: "string"},
			{Name: "tenant_id", Column: "tenant_id", Type: "string"},
		},
	}
}

func newTenantCtx(adapter *testAdapter, builder *runtime.Builder, resolver *runtime.EntityResolver,
	rowFilters []config.RowFilter) *handlers.Context {
	return &handlers.Context{
		DB:       adapter,
		Adapter:  adapter,
		Builder:  builder,
		Resolver: resolver,
		Auth: &config.AuthConfig{
			Strategy:     config.AuthStrategyHeader,
			TenantHeader: "X-Tenant-ID",
			RowFilters:   rowFilters,
		},
		TenantIDFunc: func(r *http.Request) string { return r.Header.Get("X-Tenant-ID") },
		URLParam:     func(_ *http.Request, _ string) string { return "" },
	}
}

func doCount(t *testing.T, ctx *handlers.Context, entity string, tenantID string) (int, int) {
	t.Helper()
	req := httptest.NewRequest(http.MethodGet, "/"+entity+"/count", nil)
	if tenantID != "" {
		req.Header.Set("X-Tenant-ID", tenantID)
	}
	w := httptest.NewRecorder()
	handlers.CountHandler(ctx, entity).ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		return w.Code, -1
	}
	var body countResponse
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
		t.Fatalf("bad json: %v body=%s", err, w.Body.String())
	}
	return w.Code, body.Count
}

// ═══════════════════════════════════════════════════════════════════════
// Контрольный случай: row_filter ЕСТЬ + X-Tenant-ID: tenant-a
// → count = 2 (только tenant-a). Должен ПРОХОДИТЬ.
// ═══════════════════════════════════════════════════════════════════════
func TestTenantFilter_WithRowFilter_ReturnsOwnRows(t *testing.T) {
	db, adapter := makeTenantDB(t)
	_ = db

	resolver, err := runtime.NewEntityResolver([]runtime.Entity{customerEntity()})
	if err != nil {
		t.Fatal(err)
	}
	builder := runtime.NewBuilder(adapter)

	ctx := newTenantCtx(adapter, builder, resolver, []config.RowFilter{
		{Entity: "customer", Where: `"tenant_id" = :tenant_id`},
	})

	code, count := doCount(t, ctx, "customer", "tenant-a")
	if code != http.StatusOK {
		t.Fatalf("expected 200, got %d", code)
	}
	if count != 2 {
		t.Errorf("control case: count = %d, want 2 (tenant-a only)", count)
	}
}

// ═══════════════════════════════════════════════════════════════════════
// P0-1a: Пустой X-Tenant-ID при настроенном auth → НЕ должен отдавать данные
// ═══════════════════════════════════════════════════════════════════════
// Сейчас: tenantFilter("") → ("", nil) → SQL без WHERE → count = 3 (все).
// → ДОЛЖЕН ПАДАТЬ (fail-open). Сквозной HTTP-тест через /count.
func TestTenantFilter_EmptyTenantID_DeniesAccess(t *testing.T) {
	db, adapter := makeTenantDB(t)
	_ = db

	resolver, err := runtime.NewEntityResolver([]runtime.Entity{customerEntity()})
	if err != nil {
		t.Fatal(err)
	}
	builder := runtime.NewBuilder(adapter)

	ctx := newTenantCtx(adapter, builder, resolver, []config.RowFilter{
		{Entity: "customer", Where: `"tenant_id" = :tenant_id`},
	})

	code, count := doCount(t, ctx, "customer", "") // БЕЗ X-Tenant-ID
	if code != http.StatusOK {
		// Не-200 — уже fail-closed (ок)
		return
	}
	if count > 0 {
		t.Errorf("BUG P0-1: empty X-Tenant-ID → count=%d (all tenants). "+
			"Expected 401/403 — fail-closed.", count)
	}
}

// ═══════════════════════════════════════════════════════════════════════
// P0-1b: row_filter для entity НЕ настроен → запрос должен быть запрещён
// ═══════════════════════════════════════════════════════════════════════
// RowFilters есть только для customer, а запрос идёт за entity "order"
// (не покрыта). Сейчас tenantFilter вернёт ("", nil) → SQL без WHERE →
// count = 3 (все строки order, включая чужие тенанты).
// → ДОЛЖЕН ПАДАТЬ (fail-open). Сквозной HTTP-тест через /count.
func TestTenantFilter_MissingRowFilter_DeniesAccess(t *testing.T) {
	db, adapter := makeTenantDB(t)
	_ = db
	if _, err := db.ExecContext(context.Background(), `
		CREATE TABLE orders (
			id INTEGER PRIMARY KEY,
			customer_id INTEGER,
			tenant_id TEXT NOT NULL
		);
		INSERT INTO orders (id, customer_id, tenant_id) VALUES
			(1, 1, 'tenant-a'),
			(2, 2, 'tenant-a'),
			(3, 3, 'tenant-b');
	`); err != nil {
		t.Fatal(err)
	}

	orderEntity := runtime.Entity{
		Name:     "order",
		Table:    "orders",
		IDColumn: "id",
		Fields: []runtime.EntityField{
			{Name: "id", Column: "id", Type: "int", PrimaryKey: true},
			{Name: "customer_id", Column: "customer_id", Type: "int"},
			{Name: "tenant_id", Column: "tenant_id", Type: "string"},
		},
	}
	resolver, err := runtime.NewEntityResolver([]runtime.Entity{orderEntity})
	if err != nil {
		t.Fatal(err)
	}
	builder := runtime.NewBuilder(adapter)

	// RowFilters ТОЛЬКО для customer — order не покрыт.
	ctx := newTenantCtx(adapter, builder, resolver, []config.RowFilter{
		{Entity: "customer", Where: `"tenant_id" = :tenant_id`},
	})

	code, count := doCount(t, ctx, "order", "tenant-a")
	if code != http.StatusOK {
		// Не-200 — уже fail-closed (ок)
		return
	}
	if count > 0 {
		t.Errorf("BUG P0-1: entity %q WITHOUT row_filters → count=%d. "+
			"Fail-closed expected: 403/500 (no filter = deny).", "order", count)
	}
}
