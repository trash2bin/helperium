package handlers

import (
	"context"
	"database/sql"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	_ "modernc.org/sqlite"

	"github.com/trash2bin/helperium/data-service/internal/query"
	"github.com/trash2bin/helperium/data-service/internal/runtime"
	"github.com/trash2bin/helperium/data-service/internal/search"
	"github.com/trash2bin/helperium/helperium-go/config"
)

// TestNewStrategyHandler_GrepRawWhere_TenantCountCorrect — C1 regression.
// grep (RawWhere-план) + tenant-фильтр: раньше count строился от
// обёрнутого sqlStr через countQueryWithArgs, который резал по
// LastIndex(" LIMIT ") и отрезал ") AS _t WHERE tenant_id = ?" →
// невалидный SQL → runCountQuery возвращал -1 → total=-1.
//
// До фикса: result.Total == -1 (countSQL: SELECT COUNT(*) FROM (SELECT ... LIKE ?)
// с незакрытой скобкой). После фикса: total = 1 (только tenant-a).
func TestNewStrategyHandler_GrepRawWhere_TenantCountCorrect(t *testing.T) {
	db, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close() //nolint:errcheck

	_, err = db.ExecContext(context.Background(), `
		CREATE TABLE products (
			id INTEGER PRIMARY KEY,
			name TEXT NOT NULL,
			tenant_id TEXT NOT NULL
		);
		INSERT INTO products VALUES (1, 'TenantA Widget', 'tenant-a');
		INSERT INTO products VALUES (2, 'TenantB Widget', 'tenant-b');
	`)
	if err != nil {
		t.Fatal(err)
	}

	adapter := &testStrategyAdapter{db: db}

	runtimeEntity := runtime.Entity{
		Name:     "product",
		Table:    "products",
		IDColumn: "id",
		Fields: []runtime.EntityField{
			{Name: "id", Column: "id", Type: "int", PrimaryKey: true},
			{Name: "name", Column: "name", Type: "string"},
			{Name: "tenant_id", Column: "tenant_id", Type: "string"},
		},
	}

	resolver, err := runtime.NewEntityResolver([]runtime.Entity{runtimeEntity})
	if err != nil {
		t.Fatal(err)
	}

	builder := runtime.NewBuilder(adapter)

	tPK := true
	cfgEntity := config.Entity{
		Name:     "product",
		Table:    "products",
		IDColumn: "id",
		Fields: []config.EntityField{
			{Name: "id", Column: "id", Type: config.FieldTypeInt, PrimaryKey: &tPK},
			{Name: "name", Column: "name", Type: config.FieldTypeString},
			{Name: "tenant_id", Column: "tenant_id", Type: config.FieldTypeString},
		},
	}

	// Grep-стратегия → RawWhere-план (multi-token AND LIKE).
	strategy := search.NewGrepStrategy("id", "name")

	ctx := &Context{
		DB:       adapter,
		Adapter:  adapter,
		Builder:  builder,
		Resolver: resolver,
		Auth: &config.AuthConfig{
			Strategy: config.AuthStrategyHeader,
			RowFilters: []config.RowFilter{
				{Entity: "product", Where: `"tenant_id" = :tenant_id`},
			},
		},
		TenantIDFunc: func(r *http.Request) string { return "tenant-a" },
		URLParam:     func(r *http.Request, name string) string { return "" },
	}

	h := NewStrategyHandler(ctx, strategy, "product", cfgEntity)

	// grep по "Widget" — матчит и tenant-a (id=1), и tenant-b (id=3).
	req := httptest.NewRequest(http.MethodGet, "/products/grep?pattern=Widget", nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)

	t.Logf("grep tenant-a response body: %s", w.Body.String())

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	var result query.SearchResult
	if err := json.Unmarshal(w.Body.Bytes(), &result); err != nil {
		t.Fatalf("failed to parse response: %v, body: %s", err, w.Body.String())
	}

	if result.Total == -1 {
		t.Error("BUG CONFIRMED (C1): Total=-1 — countSQL invalid after tenant wrapper " +
			"(countQueryWithArgs cut off ') AS _t WHERE tenant_id = ?').")
	}

	if result.Total != 1 {
		t.Errorf("tenant-a: expected Total=1 (only tenant-a row matches Widget), got %d", result.Total)
	}
	if result.Returned != 1 {
		t.Errorf("tenant-a: expected 1 returned row, got %d", result.Returned)
	}
	if len(result.Preview) != 1 {
		t.Errorf("tenant-a: expected 1 preview row, got %d", len(result.Preview))
	}
}

// TestNewStrategyHandler_GrepRawWhere_NoTenantStillWorks — grep без tenant-фильтра
// не должен сломаться после рефакторинга count.
func TestNewStrategyHandler_GrepRawWhere_NoTenantStillWorks(t *testing.T) {
	db, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close() //nolint:errcheck

	_, err = db.ExecContext(context.Background(), `
		CREATE TABLE products (
			id INTEGER PRIMARY KEY,
			name TEXT NOT NULL,
			tenant_id TEXT NOT NULL
		);
		INSERT INTO products VALUES (1, 'TenantA Widget', 'tenant-a');
		INSERT INTO products VALUES (2, 'TenantB Widget', 'tenant-b');
	`)
	if err != nil {
		t.Fatal(err)
	}

	adapter := &testStrategyAdapter{db: db}

	runtimeEntity := runtime.Entity{
		Name:     "product",
		Table:    "products",
		IDColumn: "id",
		Fields: []runtime.EntityField{
			{Name: "id", Column: "id", Type: "int", PrimaryKey: true},
			{Name: "name", Column: "name", Type: "string"},
			{Name: "tenant_id", Column: "tenant_id", Type: "string"},
		},
	}
	resolver, err := runtime.NewEntityResolver([]runtime.Entity{runtimeEntity})
	if err != nil {
		t.Fatal(err)
	}
	builder := runtime.NewBuilder(adapter)

	tPK := true
	cfgEntity := config.Entity{
		Name:     "product",
		Table:    "products",
		IDColumn: "id",
		Fields: []config.EntityField{
			{Name: "id", Column: "id", Type: config.FieldTypeInt, PrimaryKey: &tPK},
			{Name: "name", Column: "name", Type: config.FieldTypeString},
			{Name: "tenant_id", Column: "tenant_id", Type: config.FieldTypeString},
		},
	}

	strategy := search.NewGrepStrategy("id", "name")

	// Без Auth → tenantWhere == "" → RawWhere без обёртки.
	ctx := &Context{
		DB:           adapter,
		Adapter:      adapter,
		Builder:      builder,
		Resolver:     resolver,
		TenantIDFunc: func(r *http.Request) string { return "tenant-a" },
		URLParam:     func(r *http.Request, name string) string { return "" },
	}

	h := NewStrategyHandler(ctx, strategy, "product", cfgEntity)

	req := httptest.NewRequest(http.MethodGet, "/products/grep?pattern=Widget", nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)

	t.Logf("grep no-tenant response body: %s", w.Body.String())

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	var result query.SearchResult
	if err := json.Unmarshal(w.Body.Bytes(), &result); err != nil {
		t.Fatalf("failed to parse response: %v, body: %s", err, w.Body.String())
	}

	if result.Total != 2 {
		t.Errorf("no-tenant: expected Total=2 (both rows match Widget), got %d", result.Total)
	}
}

// TestCountHandler_TenantID_NotFilterable — HIGH-15 regression.
// CountHandler принимал tenant_id как фильтр → можно было посчитать
// записи чужого тенанта (tenant isolation breach).
func TestCountHandler_TenantID_NotFilterable(t *testing.T) {
	db, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close() //nolint:errcheck

	_, err = db.ExecContext(context.Background(), `
		CREATE TABLE products (
			id INTEGER PRIMARY KEY,
			name TEXT NOT NULL,
			tenant_id TEXT NOT NULL
		);
		INSERT INTO products VALUES (1, 'TenantA Widget', 'tenant-a');
		INSERT INTO products VALUES (2, 'TenantB Widget', 'tenant-b');
	`)
	if err != nil {
		t.Fatal(err)
	}

	adapter := &testStrategyAdapter{db: db}

	runtimeEntity := runtime.Entity{
		Name:     "product",
		Table:    "products",
		IDColumn: "id",
		Fields: []runtime.EntityField{
			{Name: "id", Column: "id", Type: "int", PrimaryKey: true},
			{Name: "name", Column: "name", Type: "string"},
			{Name: "tenant_id", Column: "tenant_id", Type: "string"},
		},
	}
	resolver, err := runtime.NewEntityResolver([]runtime.Entity{runtimeEntity})
	if err != nil {
		t.Fatal(err)
	}
	builder := runtime.NewBuilder(adapter)

	ctx := &Context{
		DB:           adapter,
		Adapter:      adapter,
		Builder:      builder,
		Resolver:     resolver,
		TenantIDFunc: func(r *http.Request) string { return "tenant-a" },
		URLParam:     func(r *http.Request, name string) string { return "" },
	}

	h := CountHandler(ctx, "product")

	// Пытаемся посчитать записи ЧУЖОГО тенанта через tenant_id-фильтр.
	// До фикса: WHERE "tenant_id" = 'tenant-b' → count=1 (утечка).
	// После фикса: tenant_id игнорируется как фильтр → count=2 (все записи,
	// т.к. Auth==nil — это допустимое поведение: без auth-конфига tenant_id
	// не должен использоваться как пользовательский фильтр).
	req := httptest.NewRequest(http.MethodGet, "/products/count?tenant_id=tenant-b", nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)

	t.Logf("count with tenant_id filter response body: %s", w.Body.String())

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	var body struct {
		Count int `json:"count"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
		t.Fatalf("failed to parse response: %v, body: %s", err, w.Body.String())
	}

	if body.Count == 1 {
		t.Error("BUG CONFIRMED (HIGH-15): tenant_id accepted as filter — " +
			"count of another tenant's rows leaked. Expected tenant_id to be skipped.")
	}

	// tenant_id — системный параметр: он не должен влиять на count.
	// Без auth-фильтров (Auth==nil) ожидаем общий count=2.
	if body.Count != 2 {
		t.Errorf("expected Count=2 (tenant_id ignored as filter), got %d", body.Count)
	}
}
