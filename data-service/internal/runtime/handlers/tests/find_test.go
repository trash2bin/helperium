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

	"github.com/agent-tutor/data-service/internal/runtime"
	"github.com/agent-tutor/data-service/internal/runtime/handlers"
)

// TestFindHandler_Success — поиск по email, находит одну запись
func TestFindHandler_Success(t *testing.T) {
	db, _ := sql.Open("sqlite", ":memory:")
	defer db.Close() //nolint:errcheck
	db.SetMaxOpenConns(1)

	_, _ = db.ExecContext(context.Background(), `
		CREATE TABLE customers (
			id INTEGER PRIMARY KEY,
			name TEXT NOT NULL,
			email TEXT NOT NULL
		);
	`)
	_, _ = db.ExecContext(context.Background(), `
		INSERT INTO customers (id, name, email) VALUES
			(1, 'John Doe', 'john@example.com'),
			(2, 'Jane Smith', 'jane@example.com')
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

	h := handlers.FindHandler(ctx, "customer", "email", "email")

	req := httptest.NewRequest(http.MethodGet, "/customers/find?email=john@example.com", nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	if !strings.Contains(w.Body.String(), "John Doe") {
		t.Errorf("response missing John Doe: %s", w.Body.String())
	}
}

// TestFindHandler_NotFound — поиск по email, ничего не найдено → 404
func TestFindHandler_NotFound(t *testing.T) {
	db, _ := sql.Open("sqlite", ":memory:")
	defer db.Close() //nolint:errcheck
	db.SetMaxOpenConns(1)

	_, _ = db.ExecContext(context.Background(), `
		CREATE TABLE customers (
			id INTEGER PRIMARY KEY,
			name TEXT NOT NULL,
			email TEXT NOT NULL
		);
	`)
	_, _ = db.ExecContext(context.Background(), `
		INSERT INTO customers (id, name, email) VALUES (1, 'John Doe', 'john@example.com')
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

	h := handlers.FindHandler(ctx, "customer", "email", "email")

	req := httptest.NewRequest(http.MethodGet, "/customers/find?email=nonexistent@example.com", nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)

	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d: %s", w.Code, w.Body.String())
	}
	if !strings.Contains(w.Body.String(), "not_found") {
		t.Errorf("response should contain not_found: %s", w.Body.String())
	}
}

// TestFindHandler_FallbackToList — без search-параметра → список всех записей
func TestFindHandler_FallbackToList(t *testing.T) {
	db, _ := sql.Open("sqlite", ":memory:")
	defer db.Close() //nolint:errcheck
	db.SetMaxOpenConns(1)

	_, _ = db.ExecContext(context.Background(), `
		CREATE TABLE customers (
			id INTEGER PRIMARY KEY,
			name TEXT NOT NULL,
			email TEXT NOT NULL
		);
	`)
	_, _ = db.ExecContext(context.Background(), `
		INSERT INTO customers (id, name, email) VALUES
			(1, 'John Doe', 'john@example.com'),
			(2, 'Jane Smith', 'jane@example.com')
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

	h := handlers.FindHandler(ctx, "customer", "email", "email")

	// No query param → fallback to list
	req := httptest.NewRequest(http.MethodGet, "/customers/find", nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	body := w.Body.String()
	if !strings.Contains(body, "John Doe") || !strings.Contains(body, "Jane Smith") {
		t.Errorf("fallback list missing records: %s", body)
	}
}

// TestFindHandler_EntityNotFound — неверное имя сущности → 500 config_error
func TestFindHandler_EntityNotFound(t *testing.T) {
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

	h := handlers.FindHandler(ctx, "nonexistent", "email", "email")

	req := httptest.NewRequest(http.MethodGet, "/nonexistent/find", nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)

	if w.Code != http.StatusInternalServerError {
		t.Errorf("expected 500, got %d: %s", w.Code, w.Body.String())
	}
	if !strings.Contains(w.Body.String(), "config_error") {
		t.Errorf("response should contain config_error: %s", w.Body.String())
	}
}

// TestFindHandler_DBError — ошибка БД → 500 db_error
func TestFindHandler_DBError(t *testing.T) {
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

	h := handlers.FindHandler(ctx, "customer", "email", "email")

	req := httptest.NewRequest(http.MethodGet, "/customers/find?email=test@test.com", nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)

	if w.Code != http.StatusInternalServerError {
		t.Errorf("expected 500, got %d: %s", w.Code, w.Body.String())
	}
	if !strings.Contains(w.Body.String(), "db_error") {
		t.Errorf("response should contain db_error: %s", w.Body.String())
	}
}
