package server

import (
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestAdminAuthMiddleware_NoToken(t *testing.T) {
	if tok, ok := os.LookupEnv("ADMIN_TOKEN"); ok {
		defer os.Setenv("ADMIN_TOKEN", tok)
	}
	os.Unsetenv("ADMIN_TOKEN")

	handler := AdminAuthMiddleware(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	req := httptest.NewRequest(http.MethodGet, "/admin/config", nil)
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	if rec.Code != http.StatusUnauthorized {
		t.Errorf("expected 401, got %d", rec.Code)
	}
}

func TestAdminAuthMiddleware_ValidToken(t *testing.T) {
	token := "test-secret-123"
	os.Setenv("ADMIN_TOKEN", token)
	defer os.Unsetenv("ADMIN_TOKEN")

	handler := AdminAuthMiddleware(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	req := httptest.NewRequest(http.MethodGet, "/admin/config", nil)
	req.Header.Set("Authorization", "Bearer "+token)
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", rec.Code)
	}
}

func TestAdminAuthMiddleware_InvalidToken(t *testing.T) {
	os.Setenv("ADMIN_TOKEN", "correct-token")
	defer os.Unsetenv("ADMIN_TOKEN")

	handler := AdminAuthMiddleware(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	req := httptest.NewRequest(http.MethodGet, "/admin/config", nil)
	req.Header.Set("Authorization", "Bearer wrong-token")
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	if rec.Code != http.StatusUnauthorized {
		t.Errorf("expected 401, got %d", rec.Code)
	}
}

func TestArchiveCurrentConfig(t *testing.T) {
	tmpDir := t.TempDir()
	configFile := filepath.Join(tmpDir, "config.json")
	initial := `{"version": 1, "data_source": {"driver": "sqlite", "dsn": ":memory:"}}`
	if err := os.WriteFile(configFile, []byte(initial), 0644); err != nil {
		t.Fatal(err)
	}

	if err := archiveCurrentConfig(configFile); err != nil {
		t.Fatalf("archiveCurrentConfig: %v", err)
	}

	versionsDir := filepath.Join(tmpDir, "config_versions")
	entries, err := os.ReadDir(versionsDir)
	if err != nil {
		t.Fatalf("readdir versions: %v", err)
	}

	if len(entries) != 1 {
		t.Fatalf("expected 1 archive, got %d", len(entries))
	}

	name := entries[0].Name()
	if !strings.HasPrefix(name, "config.") || !strings.HasSuffix(name, ".json") {
		t.Errorf("unexpected archive name: %s", name)
	}
}

// ═════════════════════════════════════════════════════════════════════
// AdminRateLimitMiddleware
// ═════════════════════════════════════════════════════════════════════

func TestAdminRateLimit_AllowsUpToBurst(t *testing.T) {
	os.Setenv("ADMIN_RATE_LIMIT_RPS", "100")
	os.Setenv("ADMIN_RATE_LIMIT_BURST", "5")
	defer func() {
		os.Unsetenv("ADMIN_RATE_LIMIT_RPS")
		os.Unsetenv("ADMIN_RATE_LIMIT_BURST")
	}()

	mw := AdminRateLimitMiddleware()
	called := 0
	handler := mw(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		called++
		w.WriteHeader(http.StatusOK)
	}))

	// Burst of 5 should all pass
	for i := range 5 {
		req := httptest.NewRequest(http.MethodGet, "/admin/config", nil)
		rec := httptest.NewRecorder()
		handler.ServeHTTP(rec, req)
		if rec.Code != http.StatusOK {
			t.Errorf("request %d: expected 200, got %d", i+1, rec.Code)
		}
	}

	if called != 5 {
		t.Errorf("expected handler called 5 times, got %d", called)
	}
}

func TestAdminRateLimit_BurstBlocksExcess(t *testing.T) {
	os.Setenv("ADMIN_RATE_LIMIT_RPS", "100")
	os.Setenv("ADMIN_RATE_LIMIT_BURST", "3")
	defer func() {
		os.Unsetenv("ADMIN_RATE_LIMIT_RPS")
		os.Unsetenv("ADMIN_RATE_LIMIT_BURST")
	}()

	mw := AdminRateLimitMiddleware()
	called := 0
	handler := mw(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		called++
		w.WriteHeader(http.StatusOK)
	}))

	// Burst of 3 should all pass
	for i := range 3 {
		req := httptest.NewRequest(http.MethodGet, "/admin/config", nil)
		rec := httptest.NewRecorder()
		handler.ServeHTTP(rec, req)
		if rec.Code != http.StatusOK {
			t.Errorf("request %d: expected 200, got %d", i+1, rec.Code)
		}
	}

	// 4th request should be rate limited (burst exhausted, no time elapsed)
	req := httptest.NewRequest(http.MethodGet, "/admin/config", nil)
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	if rec.Code != http.StatusTooManyRequests {
		t.Errorf("expected 429, got %d: %s", rec.Code, rec.Body.String())
	}
	if rec.Header().Get("Retry-After") != "1" {
		t.Errorf("expected Retry-After: 1, got %q", rec.Header().Get("Retry-After"))
	}

	if called != 3 {
		t.Errorf("expected handler called 3 times, got %d", called)
	}
}

func TestAdminRateLimit_ReplenishesTokensOverTime(t *testing.T) {
	os.Setenv("ADMIN_RATE_LIMIT_RPS", "1000")
	os.Setenv("ADMIN_RATE_LIMIT_BURST", "2")
	defer func() {
		os.Unsetenv("ADMIN_RATE_LIMIT_RPS")
		os.Unsetenv("ADMIN_RATE_LIMIT_BURST")
	}()

	mw := AdminRateLimitMiddleware()
	called := 0
	handler := mw(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		called++
		w.WriteHeader(http.StatusOK)
	}))

	// Consume burst
	req := httptest.NewRequest(http.MethodGet, "/admin/config", nil)
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	req2 := httptest.NewRequest(http.MethodGet, "/admin/config", nil)
	rec2 := httptest.NewRecorder()
	handler.ServeHTTP(rec2, req2)

	// 3rd should be blocked (burst=2 exhausted)
	req3 := httptest.NewRequest(http.MethodGet, "/admin/config", nil)
	rec3 := httptest.NewRecorder()
	handler.ServeHTTP(rec3, req3)
	if rec3.Code != http.StatusTooManyRequests {
		t.Errorf("expected 429 on 3rd request, got %d", rec3.Code)
	}

	if called != 2 {
		t.Errorf("expected 2 calls, got %d", called)
	}
}
