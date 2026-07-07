package handlers_test

import (
	"context"
	"database/sql"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	_ "modernc.org/sqlite" // pure-Go SQLite driver

	"github.com/agent-tutor/data-service/internal/runtime"
	"github.com/agent-tutor/data-service/internal/runtime/handlers"
	"github.com/agent-tutor/agent-tutor-go/config"
)

// TestListHandler_Success - тестирует успешный запрос списка сущностей
func TestListHandler_Success(t *testing.T) {
	// Создаем тестовую БД с тестовыми данными
	db, _ := sql.Open("sqlite", ":memory:")
	defer db.Close()
	db.SetMaxOpenConns(1)
	
	// Создаем таблицу и добавляем тестовые данные
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

	// Создаем адаптер
	adapter := &testAdapter{db: db}

	// Создаем сущности и резолвер через NewEntityResolver
	customerEntity := runtime.Entity{
		Name:    "customer",
		Table:   "customers",
		IDColumn: "id",
		Fields: []runtime.EntityField{
			{Name: "id", Column: "id", Type: "int", PrimaryKey: true},
			{Name: "name", Column: "name", Type: "string"},
			{Name: "email", Column: "email", Type: "string"},
		},
	}
	resolver, err := runtime.NewEntityResolver([]runtime.Entity{customerEntity})
	if err != nil {
		t.Fatalf("Failed to create entity resolver: %v", err)
	}

	// Создаем билдер
	builder := runtime.NewBuilder(adapter)

	// Создаем контекст
	ctx := &handlers.Context{
		DB:       adapter,
		Adapter:  adapter,
		Builder:  builder,
		Resolver: resolver,
		URLParam: func(r *http.Request, name string) string {
			return ""
		},
		TenantIDFunc: func(r *http.Request) string {
			return ""
		},
	}

	// Создаем обработчик
	h := handlers.ListHandler(ctx, "customer")

	// Выполняем запрос
	req := httptest.NewRequest(http.MethodGet, "/customers", nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)

	// Проверяем ответ
	if w.Code != http.StatusOK {
		t.Errorf("Handler returned wrong status: got %v want %v", w.Code, http.StatusOK)
	}

	// Проверяем, что ответ содержит ожидаемые данные
	body := w.Body.String()
	if !strings.Contains(body, `"id":1`) || !strings.Contains(body, `"name":"John Doe"`) ||
		!strings.Contains(body, `"email":"john@example.com"`) {
		t.Errorf("Response body missing expected data: %s", body)
	}
	if !strings.Contains(body, `"id":2`) || !strings.Contains(body, `"name":"Jane Smith"`) ||
		!strings.Contains(body, `"email":"jane@example.com"`) {
		t.Errorf("Response body missing expected data: %s", body)
	}
}

// TestListHandler_EntityNotFound - тестирует обработку случая, когда сущность не найдена
func TestListHandler_EntityNotFound(t *testing.T) {
	// Создаем пустую БД
	db, _ := sql.Open("sqlite", ":memory:")
	defer db.Close()

	// Создаем адаптер
	adapter := &testAdapter{db: db}

	// Создаем пустой резолвер
	resolver, err := runtime.NewEntityResolver([]runtime.Entity{})
	if err != nil {
		t.Fatalf("Failed to create entity resolver: %v", err)
	}

	// Создаем билдер
	builder := runtime.NewBuilder(adapter)

	// Создаем контекст
	ctx := &handlers.Context{
		DB:       adapter,
		Adapter:  adapter,
		Builder:  builder,
		Resolver: resolver,
		URLParam: func(r *http.Request, name string) string {
			return ""
		},
		TenantIDFunc: func(r *http.Request) string {
			return ""
		},
	}

	// Создаем обработчик
	h := handlers.ListHandler(ctx, "nonexistent")

	// Выполняем запрос
	req := httptest.NewRequest(http.MethodGet, "/nonexistent", nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)

	// Проверяем, что получили ошибку 500
	if w.Code != http.StatusInternalServerError {
		t.Errorf("Handler returned wrong status: got %v want %v", w.Code, http.StatusInternalServerError)
	}

	// Проверяем, что в ответе есть информация об ошибке
	body := w.Body.String()
	if !strings.Contains(body, `"error":"config_error"`) {
		t.Errorf("Response should contain config_error: %s", body)
	}
	if !strings.Contains(body, `"message":"entity not found"`) {
		t.Errorf("Response should contain entity not found message: %s", body)
	}
}

// TestListHandler_DBError - тестирует обработку ошибки базы данных
func TestListHandler_DBError(t *testing.T) {
	// Создаем тестовую БД
	db, _ := sql.Open("sqlite", ":memory:")
	defer db.Close()

	// Создаем адаптер который возвращает ошибку
	adapter := &errorAdapter{
		db:      &testAdapter{db: db},
		errFunc: func(context.Context, string, ...any) (*sql.Rows, error) {
			return nil, fmt.Errorf("database error")
		},
	}

	// Создаем сущность и резолвер
	customerEntity := runtime.Entity{
		Name:    "customer",
		Table:   "customers",
		IDColumn: "id",
		Fields: []runtime.EntityField{
			{Name: "id", Column: "id", Type: "int", PrimaryKey: true},
			{Name: "name", Column: "name", Type: "string"},
			{Name: "email", Column: "email", Type: "string"},
		},
	}
	resolver, err := runtime.NewEntityResolver([]runtime.Entity{customerEntity})
	if err != nil {
		t.Fatalf("Failed to create entity resolver: %v", err)
	}

	// Создаем билдер
	builder := runtime.NewBuilder(adapter)

	// Создаем контекст
	ctx := &handlers.Context{
		DB:       adapter,
		Adapter:  adapter,
		Builder:  builder,
		Resolver: resolver,
		URLParam: func(r *http.Request, name string) string {
			return ""
		},
		TenantIDFunc: func(r *http.Request) string {
			return ""
		},
	}

	// Создаем обработчик
	h := handlers.ListHandler(ctx, "customer")

	// Выполняем запрос
	req := httptest.NewRequest(http.MethodGet, "/customers", nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)

	// Проверяем, что получили ошибку 500
	if w.Code != http.StatusInternalServerError {
		t.Errorf("Handler returned wrong status: got %v want %v", w.Code, http.StatusInternalServerError)
	}

	// Проверяем, что в ответе есть информация об ошибке БД
	body := w.Body.String()
	if !strings.Contains(body, `"error":"db_error"`) {
		t.Errorf("Response should contain db_error: %s", body)
	}
	if !strings.Contains(body, `"message":"database error"`) {
		t.Errorf("Response should contain the error message: %s", body)
	}
}

// TestGetByIDHandler_Success - тестирует успешный запрос сущности по ID
func TestGetByIDHandler_Success(t *testing.T) {
	// Создаем тестовую БД с тестовыми данными
	db, _ := sql.Open("sqlite", ":memory:")
	defer db.Close()
	db.SetMaxOpenConns(1)
	
	// Создаем таблицу и добавляем тестовые данные
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

	// Создаем адаптер
	adapter := &testAdapter{db: db}

	// Создаем сущности и резолвер через NewEntityResolver
	customerEntity := runtime.Entity{
		Name:    "customer",
		Table:   "customers",
		IDColumn: "id",
		Fields: []runtime.EntityField{
			{Name: "id", Column: "id", Type: "int", PrimaryKey: true},
			{Name: "name", Column: "name", Type: "string"},
			{Name: "email", Column: "email", Type: "string"},
		},
	}
	resolver, err := runtime.NewEntityResolver([]runtime.Entity{customerEntity})
	if err != nil {
		t.Fatalf("Failed to create entity resolver: %v", err)
	}

	// Создаем билдер
	builder := runtime.NewBuilder(adapter)

	// Создаем контекст
	ctx := &handlers.Context{
		DB:       adapter,
		Adapter:  adapter,
		Builder:  builder,
		Resolver: resolver,
		TenantIDFunc: func(r *http.Request) string {
			return ""
		},
		Auth: &config.AuthConfig{
			Strategy: config.AuthStrategyNone,
		},
	}

	// Создаем обработчик
	h := handlers.GetByIDHandler(ctx, "customer")

	// First request: ID=1
	ctx.URLParam = func(r *http.Request, name string) string {
		if name == "id" {
			return "1"
		}
		return ""
	}
	req := httptest.NewRequest(http.MethodGet, "/customers/1", nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)

	// Проверяем ответ
	if w.Code != http.StatusOK {
		t.Errorf("Handler returned wrong status for ID=1: got %v want %v", w.Code, http.StatusOK)
	}

	// Проверяем, что ответ содержит ожидаемые данные
	body := w.Body.String()
	if !strings.Contains(body, `"id":1`) || !strings.Contains(body, `"name":"John Doe"`) ||
		!strings.Contains(body, `"email":"john@example.com"`) {
		t.Errorf("Response body missing expected data for ID=1: %s", body)
	}

	// Second request: ID=2
	ctx.URLParam = func(r *http.Request, name string) string {
		if name == "id" {
			return "2"
		}
		return ""
	}
	req = httptest.NewRequest(http.MethodGet, "/customers/2", nil)
	w = httptest.NewRecorder()
	h.ServeHTTP(w, req)

	// Проверяем ответ
	if w.Code != http.StatusOK {
		t.Errorf("Handler returned wrong status for ID=2: got %v want %v", w.Code, http.StatusOK)
	}

	// Проверяем, что ответ содержит ожидаемые данные
	body = w.Body.String()
	if !strings.Contains(body, `"id":2`) || !strings.Contains(body, `"name":"Jane Smith"`) ||
		!strings.Contains(body, `"email":"jane@example.com"`) {
		t.Errorf("Response body missing expected data for ID=2: %s", body)
	}
}

// TestGetByIDHandler_NotFound - тестирует запрос несуществующего ID
func TestGetByIDHandler_NotFound(t *testing.T) {
	// Создаем тестовую БД с тестовыми данными
	db, _ := sql.Open("sqlite", ":memory:")
	defer db.Close()
	db.SetMaxOpenConns(1)
	
	// Создаем таблицу и добавляем тестовые данные
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

	// Создаем адаптер
	adapter := &testAdapter{db: db}

	// Создаем сущности и резолвер через NewEntityResolver
	customerEntity := runtime.Entity{
		Name:    "customer",
		Table:   "customers",
		IDColumn: "id",
		Fields: []runtime.EntityField{
			{Name: "id", Column: "id", Type: "int", PrimaryKey: true},
			{Name: "name", Column: "name", Type: "string"},
			{Name: "email", Column: "email", Type: "string"},
		},
	}
	resolver, err := runtime.NewEntityResolver([]runtime.Entity{customerEntity})
	if err != nil {
		t.Fatalf("Failed to create entity resolver: %v", err)
	}

	// Создаем билдер
	builder := runtime.NewBuilder(adapter)

	// Создаем контекст
	ctx := &handlers.Context{
		DB:       adapter,
		Adapter:  adapter,
		Builder:  builder,
		Resolver: resolver,
		TenantIDFunc: func(r *http.Request) string {
			return ""
		},
		Auth: &config.AuthConfig{
			Strategy: config.AuthStrategyNone,
		},
	}

	// Создаем обработчик
	h := handlers.GetByIDHandler(ctx, "customer")

	// Set up URLParam to return "999" for "id"
	ctx.URLParam = func(r *http.Request, name string) string {
		if name == "id" {
			return "999" // Несуществующий ID
		}
		return ""
	}

	// Выполняем запрос для несуществующего ID
	req := httptest.NewRequest(http.MethodGet, "/customers/999", nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)

	// Проверяем, что получили ошибку 404
	if w.Code != http.StatusNotFound {
		t.Errorf("Handler returned wrong status for non-existent ID: got %v want %v", w.Code, http.StatusNotFound)
	}

	// Проверяем, что в ответе есть информация об ошибке
	body := w.Body.String()
	if !strings.Contains(body, `"error":"not_found"`) {
		t.Errorf("Response should contain not_found error: %s", body)
	}
}

// TestGetByIDHandler_EntityNotFound - тестирует обработку случая, когда сущность не найдена
func TestGetByIDHandler_EntityNotFound(t *testing.T) {
	// Создаем пустую БД
	db, _ := sql.Open("sqlite", ":memory:")
	defer db.Close()

	// Создаем адаптер
	adapter := &testAdapter{db: db}

	// Создаем пустой резолвер
	resolver, err := runtime.NewEntityResolver([]runtime.Entity{})
	if err != nil {
		t.Fatalf("Failed to create entity resolver: %v", err)
	}

	// Создаем билдер
	builder := runtime.NewBuilder(adapter)

	// Создаем контекст
	ctx := &handlers.Context{
		DB:       adapter,
		Adapter:  adapter,
		Builder:  builder,
		Resolver: resolver,
		TenantIDFunc: func(r *http.Request) string {
			return ""
		},
		Auth: &config.AuthConfig{
			Strategy: config.AuthStrategyNone,
		},
	}

	// Set up URLParam to return "1" for "id"
	ctx.URLParam = func(r *http.Request, name string) string {
		if name == "id" {
			return "1"
		}
		return ""
	}

	// Создаем обработчик
	h := handlers.GetByIDHandler(ctx, "nonexistent")

	// Выполняем запрос
	req := httptest.NewRequest(http.MethodGet, "/nonexistent/1", nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)

	// Проверяем, что получили ошибку 500
	if w.Code != http.StatusInternalServerError {
		t.Errorf("Handler returned wrong status: got %v want %v", w.Code, http.StatusInternalServerError)
	}

	// Проверяем, что в ответе есть информация об ошибке
	body := w.Body.String()
	if !strings.Contains(body, `"error":"config_error"`) {
		t.Errorf("Response should contain config_error: %s", body)
	}
	if !strings.Contains(body, `"message":"entity not found"`) {
		t.Errorf("Response should contain entity not found message: %s", body)
	}
}

// TestGetByIDHandler_DBError - тестирует обработку ошибки базы данных при GetByID
func TestGetByIDHandler_DBError(t *testing.T) {
	// Создаем тестовую БД
	db, _ := sql.Open("sqlite", ":memory:")
	defer db.Close()

	// Создаем адаптер который возвращает ошибку
	adapter := &errorAdapter{
		db:      &testAdapter{db: db},
		errFunc: func(context.Context, string, ...any) (*sql.Rows, error) {
			return nil, fmt.Errorf("database error")
		},
	}

	// Создаем сущность и резолвер
	customerEntity := runtime.Entity{
		Name:    "customer",
		Table:   "customers",
		IDColumn: "id",
		Fields: []runtime.EntityField{
			{Name: "id", Column: "id", Type: "int", PrimaryKey: true},
			{Name: "name", Column: "name", Type: "string"},
			{Name: "email", Column: "email", Type: "string"},
		},
	}
	resolver, err := runtime.NewEntityResolver([]runtime.Entity{customerEntity})
	if err != nil {
		t.Fatalf("Failed to create entity resolver: %v", err)
	}

	// Создаем билдер
	builder := runtime.NewBuilder(adapter)

	// Создаем контекст
	ctx := &handlers.Context{
		DB:       adapter,
		Adapter:  adapter,
		Builder:  builder,
		Resolver: resolver,
		TenantIDFunc: func(r *http.Request) string {
			return ""
		},
		Auth: &config.AuthConfig{
			Strategy: config.AuthStrategyNone,
		},
	}

	// Set up URLParam to return "1" for "id"
	ctx.URLParam = func(r *http.Request, name string) string {
		if name == "id" {
			return "1"
		}
		return ""
	}

	// Создаем обработчик
	h := handlers.GetByIDHandler(ctx, "customer")

	// Выполняем запрос
	req := httptest.NewRequest(http.MethodGet, "/customers/1", nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)

	// Проверяем, что получили ошибку 500
	if w.Code != http.StatusInternalServerError {
		t.Errorf("Handler returned wrong status: got %v want %v", w.Code, http.StatusInternalServerError)
	}

	// Проверяем, что в ответе есть информация об ошибке БД
	body := w.Body.String()
	if !strings.Contains(body, `"error":"db_error"`) {
		t.Errorf("Response should contain db_error: %s", body)
	}
	if !strings.Contains(body, `"message":"database error"`) {
		t.Errorf("Response should contain the error message: %s", body)
	}
}

// testAdapter - обёртка над *sql.DB, реализующая три метода AdapterSubset.
type testAdapter struct {
	db *sql.DB
}

func (a *testAdapter) QueryContext(ctx context.Context, query string, args ...any) (*sql.Rows, error) {
	return a.db.QueryContext(ctx, query, args...)
}

func (a *testAdapter) QuoteIdentifier(name string) string {
	return `"` + name + `"`
}

func (a *testAdapter) TranslatePlaceholder(index int) string {
	// SQLite нативно использует '?'. Index игнорируется.
	return "?"
}

func (a *testAdapter) PingContext(ctx context.Context) error {
	return a.db.PingContext(ctx)
}

// errorAdapter - адаптер который возвращает ошибку при выполнении запросов
type errorAdapter struct {
	db      *testAdapter
	errFunc func(context.Context, string, ...any) (*sql.Rows, error)
}

func (e *errorAdapter) QueryContext(ctx context.Context, query string, args ...any) (*sql.Rows, error) {
	if e.errFunc != nil {
		return e.errFunc(ctx, query, args...)
	}
	return nil, nil
}

func (e *errorAdapter) QuoteIdentifier(name string) string {
	return e.db.QuoteIdentifier(name)
}

func (e *errorAdapter) TranslatePlaceholder(index int) string {
	return e.db.TranslatePlaceholder(index)
}

func (e *errorAdapter) PingContext(ctx context.Context) error {
	return e.db.PingContext(ctx)
}
