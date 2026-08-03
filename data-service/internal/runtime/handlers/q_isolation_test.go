package handlers

import (
	"context"
	"database/sql"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	_ "modernc.org/sqlite"

	"github.com/trash2bin/helperium/data-service/internal/runtime"
	"github.com/trash2bin/helperium/helperium-go/config"
)

// ── Фаза 2 ревизия: tenant-изоляция db_get / db_related ─────────────────

// setupTenantDB создаёт sqlite-БД с таблицей products(id, name, tenant_id)
// и двумя тенантами. Возвращает адаптер + конфиги.
func setupTenantDB(t *testing.T, tenantID string) (*sql.DB, *Context, config.Entity) {
	t.Helper()
	db, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { db.Close() }) //nolint:errcheck

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
	builder := runtime.NewBuilder(adapter)

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

	ctx := &Context{
		DB:       adapter,
		Adapter:  adapter,
		Builder:  builder,
		Resolver: resolver,
		URLParam: func(r *http.Request, name string) string { return "" },
		Auth: &config.AuthConfig{
			Strategy: config.AuthStrategyHeader,
			RowFilters: []config.RowFilter{
				{Entity: "product", Where: "tenant_id = :tenant_id"},
			},
		},
		TenantIDFunc: func(r *http.Request) string { return tenantID },
	}

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
	return db, ctx, cfgEntity
}

// TestDBGet_OtherTenantID_Denied — db_get чужого id (другой tenant) → 404,
// НЕ данные. Tenant-изоляция: AND tenant_id = ? в WHERE.
func TestDBGet_OtherTenantID_Denied(t *testing.T) {
	_, ctx, _ := setupTenantDB(t, "tenant-a")

	// db_get идёт через GetByIDHandler (id из query-параметра).
	// Эмулируем GET /q/get?entity=product&id=2 (id=2 — запись tenant-b).
	h := QGetHandler(ctx, func(n string) bool { return true },
		func(n string) http.HandlerFunc {
			// подменяем URLParam на чтение query id (как в /q/get)
			ctx.URLParam = func(r *http.Request, name string) string {
				if name == "id" {
					return r.URL.Query().Get("id")
				}
				return ""
			}
			return GetByIDHandler(ctx, n)
		})

	req := httptest.NewRequest(http.MethodGet, "/q/get?entity=product&id=2", nil)
	w := httptest.NewRecorder()
	h(w, req)

	// id=2 принадлежит tenant-b; tenant-a с RowFilter tenant_id=tenant-a не видит её.
	if w.Code != http.StatusNotFound {
		t.Errorf("db_get of other-tenant id must be 404 (tenant isolation), got %d: %s",
			w.Code, w.Body.String())
	}
	if strings.Contains(w.Body.String(), "TenantB Widget") {
		t.Errorf("tenant-a db_get leaked tenant-b record: %s", w.Body.String())
	}
}

// TestDBGet_OwnTenantID_OK — db_get своего id → 200 + данные.
func TestDBGet_OwnTenantID_OK(t *testing.T) {
	_, ctx, _ := setupTenantDB(t, "tenant-a")

	ctx.URLParam = func(r *http.Request, name string) string {
		if name == "id" {
			return r.URL.Query().Get("id")
		}
		return ""
	}
	h := QGetHandler(ctx, func(n string) bool { return true },
		func(n string) http.HandlerFunc { return GetByIDHandler(ctx, n) })

	req := httptest.NewRequest(http.MethodGet, "/q/get?entity=product&id=1", nil)
	w := httptest.NewRecorder()
	h(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("db_get own id should be 200, got %d: %s", w.Code, w.Body.String())
	}
	if !strings.Contains(w.Body.String(), "TenantA Widget") {
		t.Errorf("db_get own id should return own record: %s", w.Body.String())
	}
}

// TestDBGet_NoTenantID_Denied — header-auth без X-Tenant-ID → 400 (fail-closed).
func TestDBGet_NoTenantID_Denied(t *testing.T) {
	_, ctx, _ := setupTenantDB(t, "") // пустой tenantID

	ctx.URLParam = func(r *http.Request, name string) string {
		if name == "id" {
			return r.URL.Query().Get("id")
		}
		return ""
	}
	h := QGetHandler(ctx, func(n string) bool { return true },
		func(n string) http.HandlerFunc { return GetByIDHandler(ctx, n) })

	req := httptest.NewRequest(http.MethodGet, "/q/get?entity=product&id=1", nil)
	w := httptest.NewRecorder()
	h(w, req)

	// Fail-closed: пустой X-Tenant-ID при header-auth → 400 (не данные).
	if w.Code != http.StatusBadRequest {
		t.Errorf("db_get without tenant id must be 400 (fail-closed), got %d", w.Code)
	}
}

// TestDBRelated_PGPlaceholdersAndTenantStrip — db_related:
// 1. PG-плейсхолдеры не коллизируют (id=$1, tenant=$2, limit=$3);
// 2. tenant_id НЕ течёт в JSON-ответ (SELECT без tenant_id).
func TestDBRelated_PGPlaceholdersAndTenantStrip(t *testing.T) {
	db, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { db.Close() }) //nolint:errcheck

	_, err = db.ExecContext(context.Background(), `
		CREATE TABLE products (
			id INTEGER PRIMARY KEY,
			name TEXT NOT NULL,
			brand_id INTEGER NOT NULL,
			tenant_id TEXT NOT NULL
		);
		INSERT INTO products VALUES (1, 'A Widget', 100, 'tenant-a');
		INSERT INTO products VALUES (2, 'B Widget', 100, 'tenant-b');
		INSERT INTO products VALUES (3, 'A Other', 100, 'tenant-a');
	`)
	if err != nil {
		t.Fatal(err)
	}

	// PG-адаптер: $N плейсхолдеры, QuoteIdentifier в кавычках.
	pgAdapter := &pgStrategyAdapter{db: db}
	builder := runtime.NewBuilder(pgAdapter)

	tPK := true
	cfgEntity := config.Entity{
		Name:     "product",
		Table:    "products",
		IDColumn: "id",
		Fields: []config.EntityField{
			{Name: "id", Column: "id", Type: config.FieldTypeInt, PrimaryKey: &tPK},
			{Name: "name", Column: "name", Type: config.FieldTypeString},
			{Name: "brand_id", Column: "brand_id", Type: config.FieldTypeInt},
			{Name: "tenant_id", Column: "tenant_id", Type: config.FieldTypeString},
		},
		Relations: []config.Relation{
			{Field: "brand_id", Kind: config.RelationManyToOne, Table: "brands", LocalFK: "brand_id"},
		},
	}

	ctx := &Context{
		DB:       pgAdapter,
		Adapter:  pgAdapter,
		Builder:  builder,
		Resolver: nil, // related не использует resolver
		URLParam: func(r *http.Request, name string) string { return "" },
		Auth: &config.AuthConfig{
			Strategy: config.AuthStrategyHeader,
			RowFilters: []config.RowFilter{
				{Entity: "product", Where: "tenant_id = :tenant_id"},
			},
		},
		TenantIDFunc: func(r *http.Request) string { return "tenant-a" },
	}

	h := NewRelatedHandler(ctx, cfgEntity)
	req := httptest.NewRequest(http.MethodGet, "/q/related?entity=product&id=100", nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("related should be 200, got %d: %s", w.Code, w.Body.String())
	}

	// 1. PG-плейсхолдеры: id=$1, tenant=$2, limit=$3 — без коллизий.
	queries := pgAdapter.queries
	if len(queries) == 0 {
		t.Fatal("no query captured")
	}
	sqlStr := queries[len(queries)-1]
	if !strings.Contains(sqlStr, "= $1") {
		t.Errorf("id placeholder should be $1, got: %s", sqlStr)
	}
	if !strings.Contains(sqlStr, "tenant_id = $2") {
		t.Errorf("tenant placeholder should be $2, got: %s", sqlStr)
	}
	if !strings.Contains(sqlStr, "LIMIT $3") {
		t.Errorf("limit placeholder should be $3, got: %s", sqlStr)
	}

	// 2. tenant_id НЕ в SELECT-проекции (стрип из JSON-ответа).
	if strings.Contains(sqlStr, "SELECT *") {
		t.Errorf("related must not use SELECT * (would leak tenant_id): %s", sqlStr)
	}
	var results []map[string]any
	if err := json.Unmarshal(w.Body.Bytes(), &results); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	for _, row := range results {
		if _, ok := row["tenant_id"]; ok {
			t.Errorf("tenant_id leaked into related JSON response: %v", row)
		}
	}

	// 3. Изоляция: tenant-a видит только свои строки (1 и 3), не tenant-b (2).
	if len(results) != 2 {
		t.Errorf("tenant-a should see 2 rows for brand_id=100, got %d: %v", len(results), results)
	}
	for _, row := range results {
		if row["name"] == "B Widget" {
			t.Errorf("tenant-a related leaked tenant-b row: %v", row)
		}
	}
}

// TestDBRelated_UnsafeRelationName_Rejected — FK-имя с спецсимволами → 400.
func TestDBRelated_UnsafeRelationName_Rejected(t *testing.T) {
	_, ctx, _ := setupTenantDB(t, "tenant-a")

	tPK := true
	cfgEntity := config.Entity{
		Name:     "product",
		Table:    "products",
		IDColumn: "id",
		Fields: []config.EntityField{
			{Name: "id", Column: "id", Type: config.FieldTypeInt, PrimaryKey: &tPK},
		},
		Relations: []config.Relation{
			{Field: "evil; DROP TABLE", Kind: config.RelationManyToOne, Table: "x", LocalFK: "evil; DROP TABLE"},
		},
	}

	h := NewRelatedHandler(ctx, cfgEntity)
	req := httptest.NewRequest(http.MethodGet, "/q/related?entity=product&id=1&relation=evil%3B%20DROP%20TABLE", nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("unsafe relation name must be 400, got %d: %s", w.Code, w.Body.String())
	}
}

// TestDBRelated_MultipleRelations_RequireExplicit — несколько FK без relation → 400.
func TestDBRelated_MultipleRelations_RequireExplicit(t *testing.T) {
	_, ctx, _ := setupTenantDB(t, "tenant-a")

	tPK := true
	cfgEntity := config.Entity{
		Name:     "product",
		Table:    "products",
		IDColumn: "id",
		Fields: []config.EntityField{
			{Name: "id", Column: "id", Type: config.FieldTypeInt, PrimaryKey: &tPK},
		},
		Relations: []config.Relation{
			{Field: "brand_id", Kind: config.RelationManyToOne, Table: "brands", LocalFK: "brand_id"},
			{Field: "category_id", Kind: config.RelationManyToOne, Table: "categories", LocalFK: "category_id"},
		},
	}

	h := NewRelatedHandler(ctx, cfgEntity)
	req := httptest.NewRequest(http.MethodGet, "/q/related?entity=product&id=1", nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("multiple relations without explicit relation must be 400, got %d: %s", w.Code, w.Body.String())
	}
}
