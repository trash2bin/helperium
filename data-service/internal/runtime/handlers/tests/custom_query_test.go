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

	"github.com/agent-tutor/agent-tutor-go/config"
	"github.com/agent-tutor/data-service/internal/runtime"
	"github.com/agent-tutor/data-service/internal/runtime/handlers"
)

// makeCustomQueryContext — helper: создаёт Context с одной custom query
func makeCustomQueryContext(t *testing.T) (*handlers.Context, *testAdapter) {
	t.Helper()

	db, _ := sql.Open("sqlite", ":memory:")
	t.Cleanup(func() { db.Close() })
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
	builder := runtime.NewBuilder(adapter)
	resolver, _ := runtime.NewEntityResolver([]runtime.Entity{})

	ctx := &handlers.Context{
		DB:       adapter,
		Adapter:  adapter,
		Builder:  builder,
		Resolver: resolver,
		CustomQueries: map[string]runtime.CustomQuery{
			"get_by_email": {
				SQL:    "SELECT id, name, email FROM customers WHERE email = ?",
				Params: []string{"email"},
				ResultMapping: map[string]runtime.ResultMappingField{
					"id":    {Type: "int"},
					"name":  {Type: "string"},
					"email": {Type: "string"},
				},
				MaxRows: 10,
			},
			"count_all": {
				SQL:    "SELECT COUNT(*) as cnt FROM customers",
				Params: []string{},
				ResultMapping: map[string]runtime.ResultMappingField{
					"cnt": {Type: "int"},
				},
				MaxRows: 1,
			},
		},
		URLParam: func(_ *http.Request, name string) string {
			return ""
		},
		TenantIDFunc: func(_ *http.Request) string { return "" },
	}

	return ctx, adapter
}

// TestCustomQueryHandler_Success — валидный запрос с query-параметром
func TestCustomQueryHandler_Success(t *testing.T) {
	ctx, _ := makeCustomQueryContext(t)

	h := handlers.CustomQueryHandler(ctx, "get_by_email", []config.EndpointParam{
		{Name: "email", In: config.ParamInQuery, Type: config.ParamTypeString, Required: boolPtr(true)},
	})

	req := httptest.NewRequest(http.MethodGet, "/customers/by-email?email=john@example.com", nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	body := w.Body.String()
	if !strings.Contains(body, "John Doe") {
		t.Errorf("response missing John Doe: %s", body)
	}
}

// TestCustomQueryHandler_NoParams — запрос без параметров (count_all)
func TestCustomQueryHandler_NoParams(t *testing.T) {
	ctx, _ := makeCustomQueryContext(t)

	h := handlers.CustomQueryHandler(ctx, "count_all", []config.EndpointParam{})

	req := httptest.NewRequest(http.MethodGet, "/customers/count", nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	body := w.Body.String()
	if !strings.Contains(body, `"cnt":2`) {
		t.Errorf("response should contain cnt:2: %s", body)
	}
}

// TestCustomQueryHandler_QueryNotFound — несуществующий queryID → 404
func TestCustomQueryHandler_QueryNotFound(t *testing.T) {
	ctx, _ := makeCustomQueryContext(t)

	h := handlers.CustomQueryHandler(ctx, "nonexistent", []config.EndpointParam{})

	req := httptest.NewRequest(http.MethodGet, "/customers/unknown", nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)

	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d: %s", w.Code, w.Body.String())
	}
	if !strings.Contains(w.Body.String(), "query_not_found") {
		t.Errorf("response should contain query_not_found: %s", w.Body.String())
	}
}

// TestCustomQueryHandler_RequiredParamMissing — обязательный параметр отсутствует → 400
func TestCustomQueryHandler_RequiredParamMissing(t *testing.T) {
	ctx, _ := makeCustomQueryContext(t)

	h := handlers.CustomQueryHandler(ctx, "get_by_email", []config.EndpointParam{
		{Name: "email", In: config.ParamInQuery, Type: config.ParamTypeString, Required: boolPtr(true)},
	})

	// Без ?email=
	req := httptest.NewRequest(http.MethodGet, "/customers/by-email", nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d: %s", w.Code, w.Body.String())
	}
	if !strings.Contains(w.Body.String(), "missing_param") {
		t.Errorf("response should contain missing_param: %s", w.Body.String())
	}
}

// TestCustomQueryHandler_PathParam — параметр из URL path (chi.URLParam)
func TestCustomQueryHandler_PathParam(t *testing.T) {
	ctx, _ := makeCustomQueryContext(t)

	// Создаём query с path-параметром
	ctx.CustomQueries["get_by_id_path"] = runtime.CustomQuery{
		SQL:    "SELECT id, name, email FROM customers WHERE id = ?",
		Params: []string{"id"},
		ResultMapping: map[string]runtime.ResultMappingField{
			"id":    {Type: "int"},
			"name":  {Type: "string"},
			"email": {Type: "string"},
		},
		MaxRows: 1,
	}
	ctx.URLParam = func(_ *http.Request, name string) string {
		if name == "id" {
			return "1"
		}
		return ""
	}

	h := handlers.CustomQueryHandler(ctx, "get_by_id_path", []config.EndpointParam{
		{Name: "id", In: config.ParamInPath, Type: config.ParamTypeInt, Required: boolPtr(true)},
	})

	req := httptest.NewRequest(http.MethodGet, "/customers/1", nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	if !strings.Contains(w.Body.String(), "John Doe") {
		t.Errorf("response missing John Doe: %s", w.Body.String())
	}
}

// TestCustomQueryHandler_DBError — ошибка БД → 500 db_error
func TestCustomQueryHandler_DBError(t *testing.T) {
	db, _ := sql.Open("sqlite", ":memory:")
	defer db.Close()
	db.SetMaxOpenConns(1)

	adapter := &errorAdapter{
		db: &testAdapter{db: db},
		errFunc: func(_ context.Context, _ string, _ ...any) (*sql.Rows, error) {
			return nil, fmt.Errorf("database error")
		},
	}

	builder := runtime.NewBuilder(adapter)
	resolver, _ := runtime.NewEntityResolver([]runtime.Entity{})

	ctx := &handlers.Context{
		DB:       adapter,
		Adapter:  adapter,
		Builder:  builder,
		Resolver: resolver,
		CustomQueries: map[string]runtime.CustomQuery{
			"get_all": {
				SQL:     "SELECT 1",
				Params:  []string{},
				MaxRows: 10,
			},
		},
		URLParam:     func(_ *http.Request, _ string) string { return "" },
		TenantIDFunc: func(_ *http.Request) string { return "" },
	}

	h := handlers.CustomQueryHandler(ctx, "get_all", []config.EndpointParam{})

	req := httptest.NewRequest(http.MethodGet, "/customers/all", nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)

	if w.Code != http.StatusInternalServerError {
		t.Errorf("expected 500, got %d: %s", w.Code, w.Body.String())
	}
	if !strings.Contains(w.Body.String(), "db_error") {
		t.Errorf("response should contain db_error: %s", w.Body.String())
	}
}
func boolPtr(b bool) *bool { return &b }
