package handlers_test

import (
	"context"
	"database/sql"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	_ "modernc.org/sqlite"

	"github.com/agent-tutor/data-service/internal/runtime"
	"github.com/agent-tutor/data-service/internal/runtime/handlers"
)

// TestHealthHandler_Success — Ping возвращает ok → status: ok, db: ok
func TestHealthHandler_Success(t *testing.T) {
	db, _ := sql.Open("sqlite", ":memory:")
	defer db.Close()

	adapter := &testAdapter{db: db}
	resolver, _ := runtime.NewEntityResolver([]runtime.Entity{})
	builder := runtime.NewBuilder(adapter)

	ctx := &handlers.Context{
		DB:       adapter,
		Adapter:  adapter,
		Builder:  builder,
		Resolver: resolver,
		URLParam: func(_ *http.Request, _ string) string { return "" },
		TenantIDFunc: func(_ *http.Request) string { return "" },
	}

	h := handlers.HealthHandler(ctx)

	req := httptest.NewRequest(http.MethodGet, "/health", nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	body := w.Body.String()
	if !strings.Contains(body, `"status":"ok"`) || !strings.Contains(body, `"db":"ok"`) {
		t.Errorf("expected status:ok, db:ok, got: %s", body)
	}
}

// TestHealthHandler_Degraded — Ping падает → status: degraded, db: error
func TestHealthHandler_Degraded(t *testing.T) {
	adapter := &pingErrorAdapter{err: errors.New("connection refused")}
	resolver, _ := runtime.NewEntityResolver([]runtime.Entity{})
	builder := runtime.NewBuilder(adapter)

	ctx := &handlers.Context{
		DB:       adapter,
		Adapter:  adapter,
		Builder:  builder,
		Resolver: resolver,
		URLParam: func(_ *http.Request, _ string) string { return "" },
		TenantIDFunc: func(_ *http.Request) string { return "" },
	}

	h := handlers.HealthHandler(ctx)

	req := httptest.NewRequest(http.MethodGet, "/health", nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	body := w.Body.String()
	if !strings.Contains(body, `"status":"degraded"`) || !strings.Contains(body, `"db":"error"`) {
		t.Errorf("expected status:degraded, db:error, got: %s", body)
	}
}

// TestHealthHandler_Timeout — контекст с таймаутом, Ping не успевает
func TestHealthHandler_Timeout(t *testing.T) {
	adapter := &pingErrorAdapter{err: context.DeadlineExceeded}
	resolver, _ := runtime.NewEntityResolver([]runtime.Entity{})
	builder := runtime.NewBuilder(adapter)

	ctx := &handlers.Context{
		DB:       adapter,
		Adapter:  adapter,
		Builder:  builder,
		Resolver: resolver,
		URLParam: func(_ *http.Request, _ string) string { return "" },
		TenantIDFunc: func(_ *http.Request) string { return "" },
	}

	h := handlers.HealthHandler(ctx)

	req := httptest.NewRequest(http.MethodGet, "/health", nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	body := w.Body.String()
	if !strings.Contains(body, `"db":"error"`) {
		t.Errorf("expected db:error for timeout, got: %s", body)
	}
}

// pingErrorAdapter — адаптер, у которого PingContext всегда возвращает ошибку
type pingErrorAdapter struct {
	err error
}

func (p *pingErrorAdapter) QueryContext(_ context.Context, _ string, _ ...any) (*sql.Rows, error) {
	return nil, p.err
}

func (p *pingErrorAdapter) QuoteIdentifier(name string) string {
	return `"` + name + `"`
}

func (p *pingErrorAdapter) TranslatePlaceholder(_ int) string {
	return "?"
}

func (p *pingErrorAdapter) PingContext(_ context.Context) error {
	return p.err
}