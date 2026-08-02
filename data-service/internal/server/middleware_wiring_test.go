package server

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	_ "modernc.org/sqlite"

	"github.com/trash2bin/helperium/helperium-go/config"
)

// TestBodyLimitMiddleware_Wired_AdminRouter — BodyLimitMiddleware должен быть
// подключён в BuildAdminRouter: POST /config с телом больше лимита → 413.
// До фикса: middleware не подключён → handler читает тело целиком → не 413.
func TestBodyLimitMiddleware_Wired_AdminRouter(t *testing.T) {
	t.Setenv("ADMIN_TOKEN", "test-token")
	t.Setenv("ADMIN_RATE_LIMIT_RPS", "1000")

	ts := newTenantAdminTestStore(t)
	ts.TenantsDir = t.TempDir()

	// cfg с маленьким лимитом, чтобы тест не зависел от env.
	limitMB := 1 // 1 MB
	cfg := &config.Config{
		Server: &config.ServerConfig{BodyLimitMB: &limitMB},
	}
	router := ts.BuildAdminRouter(nil, "", nil, cfg)
	if router == nil {
		t.Fatal("BuildAdminRouter returned nil")
	}

	// Тело больше 1 MB. BuildAdminRouter монтируется на /admin, внутри роутера пути — /config.
	bigBody := strings.Repeat("a", (1<<20)+1)
	req := httptest.NewRequest(http.MethodPost, "/config", strings.NewReader(bigBody))
	req.Header.Set("X-Tenant-ID", "test-tenant")
	req.Header.Set("Authorization", "Bearer test-token")
	rec := httptest.NewRecorder()
	router.ServeHTTP(rec, req)

	if rec.Code != http.StatusRequestEntityTooLarge {
		t.Errorf("expected 413 from admin router for oversized body, got %d", rec.Code)
	}
}

// TestBodyLimitMiddleware_Wired_TenantRouter — BodyLimitMiddleware подключён в
// NewRouterFromConfig: write-запрос с телом больше лимита → 413.
func TestBodyLimitMiddleware_Wired_TenantRouter(t *testing.T) {
	ts := newTenantAdminTestStore(t)
	ts.TenantsDir = t.TempDir()

	limitMB := 1
	cfg := &config.Config{
		Server: &config.ServerConfig{BodyLimitMB: &limitMB},
	}
	router, err := NewRouterFromConfig(ts, cfg, nil)
	if err != nil {
		t.Fatalf("NewRouterFromConfig: %v", err)
	}

	bigBody := strings.Repeat("b", (1<<20)+1)
	req := httptest.NewRequest(http.MethodPost, "/test-write", strings.NewReader(bigBody))
	req.ContentLength = int64(len(bigBody))
	rec := httptest.NewRecorder()
	router.ServeHTTP(rec, req)

	if rec.Code != http.StatusRequestEntityTooLarge {
		t.Errorf("expected 413 from tenant router for oversized body, got %d", rec.Code)
	}
}

// TestThrottleMiddleware_BlocksAtLimit — ThrottleMiddleware с лимитом 1:
// второй одновременный запрос → 503.
func TestThrottleMiddleware_BlocksAtLimit(t *testing.T) {
	// Первый запрос захватывает единственный слот и блокируется.
	var entered atomic.Int32
	release := make(chan struct{})
	throttled := ThrottleMiddleware(1)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		entered.Add(1) // внутри критической секции (после active.Add)
		<-release      // держим слот занятым
		w.WriteHeader(http.StatusOK)
	}))

	// Запускаем первый запрос в горутине; ждём, пока он войдёт в слот.
	done := make(chan struct{})
	go func() {
		defer close(done)
		req := httptest.NewRequest(http.MethodGet, "/", nil)
		rec := httptest.NewRecorder()
		throttled.ServeHTTP(rec, req)
	}()

	// Ждём, пока первый запрос не войдёт в критическую секцию (слот занят).
	deadline := time.Now().Add(2 * time.Second)
	for entered.Load() == 0 && time.Now().Before(deadline) {
		time.Sleep(5 * time.Millisecond)
	}
	if entered.Load() == 0 {
		t.Fatal("first request never entered throttled handler")
	}

	// Второй запрос — должен получить 503 (слот занят).
	req2 := httptest.NewRequest(http.MethodGet, "/", nil)
	rec2 := httptest.NewRecorder()
	throttled.ServeHTTP(rec2, req2)
	if rec2.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503 when at capacity, got %d", rec2.Code)
	}

	close(release) // освобождаем первый запрос
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("first request did not finish after release")
	}
}
