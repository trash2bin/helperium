package server

import (
	"context"
	"database/sql"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/trash2bin/helperium/helperium-go/config"

	_ "modernc.org/sqlite"
)

// adminTestAdapter — minimal AdapterSubset для admin-тестов.
type adminTestAdapter struct {
	db *sql.DB
}

func (a *adminTestAdapter) QueryContext(ctx context.Context, query string, args ...any) (*sql.Rows, error) {
	return a.db.QueryContext(ctx, query, args...)
}
func (a *adminTestAdapter) PingContext(ctx context.Context) error { return a.db.PingContext(ctx) }
func (a *adminTestAdapter) QuoteIdentifier(name string) string    { return `"` + name + `"` }
func (a *adminTestAdapter) TranslatePlaceholder(index int) string { return "?" }

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

func TestAdminConfigHandler_ReturnsConfigWithoutDSN(t *testing.T) {
	cfg := &config.Config{
		Version: 1,
		DataSource: config.DataSourceConfig{
			Driver: config.DriverSQLite,
			DSN:    "/secret/path/to/db.sqlite",
		},
		Entities: []config.Entity{
			{Name: "student", Table: "students", IDColumn: "id"},
		},
		Endpoints: []config.Endpoint{
			{Method: "GET", Path: "/health", Op: "builtin_health"},
		},
	}

	handler := adminConfigHandler(cfg)
	req := httptest.NewRequest(http.MethodGet, "/admin/config", nil)
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", rec.Code)
	}

	var resp adminConfigResponse
	if err := json.Unmarshal(rec.Body.Bytes(), &resp); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}

	if resp.Driver != config.DriverSQLite {
		t.Errorf("driver = %v, want sqlite", resp.Driver)
	}
	if len(resp.Entities) != 1 || resp.Entities[0].Name != "student" {
		t.Errorf("entities = %v, want [student]", resp.Entities)
	}
}

func TestAdminConfigUpdate_DryRunValidation(t *testing.T) {
	tmpDir, err := os.MkdirTemp("", "admin-test-*")
	if err != nil {
		t.Fatal(err)
	}
	defer os.RemoveAll(tmpDir)

	configFile := filepath.Join(tmpDir, "config.json")
	initial := `{"version": 1, "data_source": {"driver": "sqlite", "dsn": ":memory:"}}`
	if err := os.WriteFile(configFile, []byte(initial), 0644); err != nil {
		t.Fatal(err)
	}

	db := newAdminDB(t)
	defer db.Close() //nolint:errcheck

	adapter := &adminTestAdapter{db: db}
	var atomicRouter atomic.Value
	atomicRouter.Store(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	reloadCalled := false
	adminCtx := &AdminContext{
		ConfigPath:   configFile,
		DB:           adapter,
		Router:       adapter,
		AtomicRouter: &atomicRouter,
		ReloadFn: func(path string) error {
			reloadCalled = true
			return nil
		},
	}

	// Invalid config: missing required fields in data_source
	invalidPayload := `{"version": 1, "data_source": {}}`
	req := httptest.NewRequest(http.MethodPost, "/admin/config", strings.NewReader(invalidPayload))
	rec := httptest.NewRecorder()
	adminConfigUpdateHandler(adminCtx).ServeHTTP(rec, req)

	if rec.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for invalid config, got %d: %s", rec.Code, rec.Body.String())
	}
	if reloadCalled {
		t.Error("reload should NOT be called on invalid config")
	}
}

func TestAdminConfigVersions_Empty(t *testing.T) {
	configFile := filepath.Join(t.TempDir(), "config.json")
	if err := os.WriteFile(configFile, []byte(
		`{"version":1,"data_source":{"driver":"sqlite","dsn":":memory:"}}`), 0644); err != nil {
		t.Fatal(err)
	}

	adminCtx := &AdminContext{ConfigPath: configFile}

	handler := adminConfigVersionsHandler(adminCtx)
	req := httptest.NewRequest(http.MethodGet, "/admin/config/versions", nil)
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", rec.Code)
	}

	var versions []any
	if err := json.Unmarshal(rec.Body.Bytes(), &versions); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if len(versions) != 0 {
		t.Errorf("expected 0 versions, got %d", len(versions))
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

// newAdminDB creates a minimal in-memory SQLite DB for admin tests.
func newAdminDB(t *testing.T) *sql.DB {
	t.Helper()
	db, err := sql.Open("sqlite", ":memory:?_journal_mode=WAL&_foreign_keys=on")
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	db.SetMaxOpenConns(1)
	if _, err := db.ExecContext(t.Context(),
		`CREATE TABLE groups (id TEXT PRIMARY KEY, name TEXT NOT NULL);`); err != nil {
		_ = db.Close()
		t.Fatalf("create table: %v", err)
	}
	t.Cleanup(func() { _ = db.Close() })
	return db
}
