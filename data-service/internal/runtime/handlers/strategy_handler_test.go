package handlers

import (
	"context"
	"database/sql"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	_ "modernc.org/sqlite"

	"github.com/trash2bin/helperium/helperium-go/config"
	"github.com/trash2bin/helperium/data-service/internal/query"
	"github.com/trash2bin/helperium/data-service/internal/runtime"
	"github.com/trash2bin/helperium/data-service/internal/search"
)

// TestNewStrategyHandler_TenantFilterCountCorrect is an integration test
// that exercises NewStrategyHandler with a real SQLite database.
//
// It creates products with a common category across tenants and verifies
// the COUNT (total) correctly reflects only the current tenant's rows.
//
// Before the fix, countSQL was NOT recalculated after tenant filter was
// applied in the Condition-based branch. This leaked data across tenants
// by reporting the total count of ALL matching rows regardless of tenant.
func TestNewStrategyHandler_TenantFilterCountCorrect(t *testing.T) {
	// ─── Setup test DB: same category exists across tenants ──────────
	// This exposes the bug: category='cat1' matches rows from BOTH tenants,
	// but the count should include only the current tenant's rows.
	db, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close() //nolint:errcheck

	_, err = db.ExecContext(context.Background(), `
		CREATE TABLE products (
			id INTEGER PRIMARY KEY,
			name TEXT NOT NULL,
			category TEXT NOT NULL DEFAULT '',
			tenant_id TEXT NOT NULL
		);
		INSERT INTO products VALUES (1, 'TenantA Widget', 'cat1', 'tenant-a');
		INSERT INTO products VALUES (2, 'TenantA Other',  'cat2', 'tenant-a');
		INSERT INTO products VALUES (3, 'TenantB Widget', 'cat1', 'tenant-b');
	`)
	if err != nil {
		t.Fatal(err)
	}

	adapter := &testStrategyAdapter{db: db}

	// ─── Runtime entity (for resolver/builder) ────────────────────────
	runtimeEntity := runtime.Entity{
		Name:     "product",
		Table:    "products",
		IDColumn: "id",
		Fields: []runtime.EntityField{
			{Name: "id", Column: "id", Type: "int", PrimaryKey: true},
			{Name: "name", Column: "name", Type: "string"},
			{Name: "category", Column: "category", Type: "string"},
			{Name: "tenant_id", Column: "tenant_id", Type: "string"},
		},
	}

	resolver, err := runtime.NewEntityResolver([]runtime.Entity{runtimeEntity})
	if err != nil {
		t.Fatal(err)
	}

	builder := runtime.NewBuilder(adapter)

	// ─── Config entity (for strategy.ParseRequest) ────────────────────
	tPK := true
	cfgEntity := config.Entity{
		Name:     "product",
		Table:    "products",
		IDColumn: "id",
		Fields: []config.EntityField{
			{Name: "id", Column: "id", Type: config.FieldTypeInt, PrimaryKey: &tPK},
			{Name: "name", Column: "name", Type: config.FieldTypeString},
			{Name: "category", Column: "category", Type: config.FieldTypeString},
			{Name: "tenant_id", Column: "tenant_id", Type: config.FieldTypeString},
		},
	}

	// ─── FilterStrategy with exact match produces Condition-based plans ──
	// category=cat1 matches rows 1 (tenant-a) and 3 (tenant-b).
	strategy := search.NewFilterStrategy("id", "name")

	// ─── Test tenant-a: should see 1 row (id=1), total=1 ─────────────
	ctxA := &Context{
		DB:      adapter,
		Adapter: adapter,
		Builder: builder,
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
	hA := NewStrategyHandler(ctxA, strategy, "product", cfgEntity)

	reqA := httptest.NewRequest(http.MethodGet, "/products/filter?category=cat1", nil)
	wA := httptest.NewRecorder()
	hA.ServeHTTP(wA, reqA)

	t.Logf("tenant-a response body: %s", wA.Body.String())

	if wA.Code != http.StatusOK {
		t.Fatalf("tenant-a: expected 200, got %d: %s", wA.Code, wA.Body.String())
	}

	var resultA query.SearchResult
	if err := json.Unmarshal(wA.Body.Bytes(), &resultA); err != nil {
		t.Fatalf("tenant-a: failed to parse response: %v, body: %s", err, wA.Body.String())
	}

	// Before the fix: resultA.Total = 2 (count includes tenant-b's row with category=cat1)
	// After the fix:  resultA.Total = 1 (count includes tenant filter)
	if resultA.Total == 2 {
		t.Error("BUG CONFIRMED: countSQL does NOT include tenant filter. " +
			"Total=2 when it should be 1 (only tenant-a's row with category=cat1). " +
			"The bug: countSQL is not recalculated after tenantWhere is applied to Condition-based plans.")
	}

	if resultA.Total != 1 {
		t.Errorf("tenant-a: expected Total=1 (1 row with cat1 in tenant-a), got %d", resultA.Total)
	}

	// ─── Test tenant-b: should see 1 row (id=3), total=1 ─────────────
	ctxB := &Context{
		DB:      adapter,
		Adapter: adapter,
		Builder: builder,
		Resolver: resolver,
		Auth: &config.AuthConfig{
			Strategy: config.AuthStrategyHeader,
			RowFilters: []config.RowFilter{
				{Entity: "product", Where: `"tenant_id" = :tenant_id`},
			},
		},
		TenantIDFunc: func(r *http.Request) string { return "tenant-b" },
		URLParam:     func(r *http.Request, name string) string { return "" },
	}
	hB := NewStrategyHandler(ctxB, strategy, "product", cfgEntity)

	reqB := httptest.NewRequest(http.MethodGet, "/products/filter?category=cat1", nil)
	wB := httptest.NewRecorder()
	hB.ServeHTTP(wB, reqB)

	t.Logf("tenant-b response body: %s", wB.Body.String())

	if wB.Code != http.StatusOK {
		t.Fatalf("tenant-b: expected 200, got %d: %s", wB.Code, wB.Body.String())
	}

	var resultB query.SearchResult
	if err := json.Unmarshal(wB.Body.Bytes(), &resultB); err != nil {
		t.Fatalf("tenant-b: failed to parse response: %v, body: %s", err, wB.Body.String())
	}

	if resultB.Total != 1 {
		t.Errorf("tenant-b: expected Total=1 (1 row with cat1 in tenant-b), got %d", resultB.Total)
	}
}

// TestNewStrategyHandler_TenantFilterNoConditions verifies the non-RawWhere
// branch where plan.Where is empty (the bare FROM + WHERE tenant filter case).
func TestNewStrategyHandler_TenantFilterNoConditions(t *testing.T) {
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
		INSERT INTO products VALUES (2, 'TenantA Other',  'tenant-a');
		INSERT INTO products VALUES (3, 'TenantB Widget', 'tenant-b');
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

	// Filter strategy — Condition-based path (no RawWhere issues)
	strategy := search.NewFilterStrategy("id", "name")

	ctx := &Context{
		DB:      adapter,
		Adapter: adapter,
		Builder: builder,
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

	// Request with filter matching 2 tenant-a rows
	req := httptest.NewRequest(http.MethodGet, "/products/filter?name__like=%25TenantA%25&limit=10", nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)

	t.Logf("no-conditions response body: %s", w.Body.String())

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	var result query.SearchResult
	if err := json.Unmarshal(w.Body.Bytes(), &result); err != nil {
		t.Fatalf("failed to parse response: %v, body: %s", err, w.Body.String())
	}

	if result.Total == 3 {
		t.Error("BUG CONFIRMED: countSQL does NOT include tenant filter. " +
			"Total=3 when it should be 2 (only tenant-a's rows). " +
			"The bug: countSQL is not recalculated after tenantWhere is applied to no-Where plans.")
	}

	if result.Total != 2 {
		t.Errorf("expected Total=2 (tenant-a only), got %d", result.Total)
	}
}

// Unit-level test to verify the countQuery + tenantWhere recombination.
func TestCountSQL_TenantWhereRecalculated(t *testing.T) {
	tests := []struct {
		name        string
		sqlStr      string
		tenantWhere string
		isRawWhere  bool // true = plan.RawWhere != "" (wraps in subquery)
		hasWhere    bool // true = query already has WHERE clause (add AND)
		wantCount   string
	}{
		{
			name:        "condition with existing WHERE clause",
			sqlStr:      `SELECT * FROM "products" WHERE "category" = ?`,
			tenantWhere: `"tenant_id" = ?`,
			isRawWhere:  false,
			hasWhere:    true,
			// After recalculating countSQL from the modified sqlStr:
			wantCount: `SELECT COUNT(*) FROM "products" WHERE "category" = ? AND "tenant_id" = ?`,
		},
		{
			name:        "no conditions — bare FROM",
			sqlStr:      `SELECT * FROM "products"`,
			tenantWhere: `"tenant_id" = ?`,
			isRawWhere:  false,
			hasWhere:    false,
			// After recalculating countSQL from the modified sqlStr:
			wantCount: `SELECT COUNT(*) FROM "products" WHERE "tenant_id" = ?`,
		},
		{
			name:        "RawWhere — subquery",
			sqlStr:      `SELECT * FROM "products" WHERE "name" LIKE ?`,
			tenantWhere: `"tenant_id" = ?`,
			isRawWhere:  true,
			hasWhere:    false,
			wantCount:   `SELECT COUNT(*) FROM (SELECT * FROM "products" WHERE "name" LIKE ?) AS _t WHERE "tenant_id" = ?`,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			// Simulate what the handler does:
			// RawWhere: wrap in subquery + append tenant WHERE
			// Condition with WHERE: append AND tenant
			// No conditions: append WHERE tenant
			var selectWithTenant string
			if tt.isRawWhere {
				selectWithTenant = "SELECT * FROM (" + tt.sqlStr + ") AS _t WHERE " + tt.tenantWhere
			} else if tt.hasWhere {
				selectWithTenant = tt.sqlStr + " AND " + tt.tenantWhere
			} else {
				selectWithTenant = tt.sqlStr + " WHERE " + tt.tenantWhere
			}

			// Simulate BUGGY behaviour: countSQL computed BEFORE tenantWhere
			buggyCountSQL := countQuery(tt.sqlStr)

			// Simulate FIXED behaviour: countSQL recomputed AFTER tenantWhere
			fixedCountSQL := countQuery(selectWithTenant)

			t.Logf("BUGGY countSQL = %q", buggyCountSQL)
			t.Logf("FIXED countSQL = %q", fixedCountSQL)

			if fixedCountSQL != tt.wantCount {
				t.Errorf("FIXED countSQL = %q, want %q", fixedCountSQL, tt.wantCount)
			}

			// BUG assertion: old countSQL doesn't include tenant filter
			if buggyCountSQL == fixedCountSQL && !tt.isRawWhere {
				t.Errorf("BUG: countQuery before and after tenant filter are the same! "+
					"countSQL does NOT include tenant filter.\n  before: %q\n  after:  %q",
					buggyCountSQL, fixedCountSQL)
			}
		})
	}
}

// testStrategyAdapter implements runtime.AdapterSubset with a real *sql.DB.
type testStrategyAdapter struct {
	db *sql.DB
}

func (a *testStrategyAdapter) QueryContext(ctx context.Context, query string, args ...any) (*sql.Rows, error) {
	return a.db.QueryContext(ctx, query, args...)
}

func (a *testStrategyAdapter) PingContext(ctx context.Context) error {
	return a.db.PingContext(ctx)
}

func (a *testStrategyAdapter) QuoteIdentifier(name string) string {
	return `"` + name + `"`
}

func (a *testStrategyAdapter) TranslatePlaceholder(index int) string {
	return "?"
}
