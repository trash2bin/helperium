package handlers_test

import (
	"context"
	"database/sql"
	"fmt"
	"math"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	_ "modernc.org/sqlite"

	"github.com/agent-tutor/data-service/internal/runtime"
	"github.com/agent-tutor/data-service/internal/runtime/handlers"
)

// ════════════════════════════════════════════════════════════════
// ID parameter validation tests
// ════════════════════════════════════════════════════════════════

func TestValidateParam_ID_TooLong(t *testing.T) {
	// Create an ID that exceeds MaxIDLength (100 chars)
	longID := strings.Repeat("a", 101)

	db, _, ctx := newValidateTestContext(t, longID)
	defer db.Close() //nolint:errcheck

	handler := handlers.GetByIDHandler(ctx, "customer")
	req := httptest.NewRequest("GET", "/customers/"+longID, nil)
	rec := httptest.NewRecorder()

	handler.ServeHTTP(rec, req)

	if rec.Code != http.StatusBadRequest {
		t.Fatalf("GET /customers/<long-id> = %d, want 400 (validation should block)\nbody: %s", rec.Code, rec.Body.String())
	}
}

func TestValidateParam_ID_Valid(t *testing.T) {
	db, _, ctx := newValidateTestContext(t, "1")
	defer db.Close() //nolint:errcheck

	handler := handlers.GetByIDHandler(ctx, "customer")
	req := httptest.NewRequest("GET", "/customers/1", nil)
	rec := httptest.NewRecorder()

	handler.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("GET /customers/1 = %d, want 200\nbody: %s", rec.Code, rec.Body.String())
	}
}

// ════════════════════════════════════════════════════════════════
// Search value parameter validation tests
// ════════════════════════════════════════════════════════════════

func TestValidateParam_SearchValue_TooLong(t *testing.T) {
	longValue := strings.Repeat("x", 201)

	db, _, ctx := newValidateTestContext(t, "")
	defer db.Close() //nolint:errcheck

	handler := handlers.FindHandler(ctx, "customer", "name", "name")
	req := httptest.NewRequest("GET", "/customers?name="+longValue, nil)
	rec := httptest.NewRecorder()

	handler.ServeHTTP(rec, req)

	if rec.Code != http.StatusBadRequest {
		t.Fatalf("GET /customers?name=<long> = %d, want 400 (validation should block)\nbody: %s", rec.Code, rec.Body.String())
	}
}

func TestValidateParam_SearchValue_Empty_Fallback(t *testing.T) {
	db, _, ctx := newValidateTestContext(t, "")
	defer db.Close() //nolint:errcheck

	handler := handlers.FindHandler(ctx, "customer", "name", "name")
	req := httptest.NewRequest("GET", "/customers", nil)
	rec := httptest.NewRecorder()

	handler.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("GET /customers empty search = %d, want 200 (list fallback)\nbody: %s", rec.Code, rec.Body.String())
	}
}

func TestValidateParam_SearchValue_Valid(t *testing.T) {
	db, _, ctx := newValidateTestContext(t, "")
	defer db.Close() //nolint:errcheck

	handler := handlers.FindHandler(ctx, "customer", "name", "name")
	req := httptest.NewRequest("GET", "/customers?name=John", nil)
	rec := httptest.NewRecorder()

	handler.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("GET /customers?name=John = %d, want 200\nbody: %s", rec.Code, rec.Body.String())
	}
}

// ════════════════════════════════════════════════════════════════
// Limit validation tests
// ════════════════════════════════════════════════════════════════

func TestValidateParam_MaxLimit(t *testing.T) {
	tests := []struct {
		name  string
		limit string
	}{
		{"valid limit 10", "10"},
		{"valid limit 0", "0"},
		{"negative limit", "-1"},
		{"huge limit", fmt.Sprintf("%d", math.MaxInt32)},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			db, _, ctx := newValidateTestContext(t, "")
			defer db.Close() //nolint:errcheck

			handler := handlers.ListHandler(ctx, "customer")
			req := httptest.NewRequest("GET", "/customers?limit="+tt.limit, nil)
			rec := httptest.NewRecorder()

			handler.ServeHTTP(rec, req)

			// List handler must not crash with any limit value
			if rec.Code == http.StatusInternalServerError {
				t.Fatalf("GET /customers?limit=%s = 500, want non-500\nbody: %s", tt.limit, rec.Body.String())
			}
		})
	}
}

// ════════════════════════════════════════════════════════════════
// Helpers
// ════════════════════════════════════════════════════════════════

// newValidateTestContext creates a test DB with customers table and returns
// the handler context ready for testing validation.
// idValue is the expected value for the "id" URL param (used by GetByIDHandler).
func newValidateTestContext(t *testing.T, idValue string) (*sql.DB, *testAdapter, *handlers.Context) {
	t.Helper()

	db, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatalf("sql.Open: %v", err)
	}
	db.SetMaxOpenConns(1)

	_, err = db.ExecContext(context.Background(), `
		CREATE TABLE customers (
			id INTEGER PRIMARY KEY,
			name TEXT NOT NULL,
			email TEXT NOT NULL
		);
	`)
	if err != nil {
		t.Fatalf("create table: %v", err)
	}
	_, err = db.ExecContext(context.Background(), `
		INSERT INTO customers (id, name, email) VALUES
			(1, 'John Doe', 'john@example.com'),
			(2, 'Jane Smith', 'jane@example.com')
	`)
	if err != nil {
		t.Fatalf("insert data: %v", err)
	}

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
	resolver, err := runtime.NewEntityResolver([]runtime.Entity{customerEntity})
	if err != nil {
		t.Fatalf("NewEntityResolver: %v", err)
	}
	builder := runtime.NewBuilder(adapter)

	// URLParam extracts the last path segment as the "id" param
	ctx := &handlers.Context{
		DB:       adapter,
		Adapter:  adapter,
		Builder:  builder,
		Resolver: resolver,
		URLParam: func(_ *http.Request, name string) string {
			if name == "id" {
				return idValue
			}
			return ""
		},
		TenantIDFunc: func(_ *http.Request) string { return "" },
	}

	return db, adapter, ctx
}
