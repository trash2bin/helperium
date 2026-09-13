package server_test

// Pentest M1 defense-in-depth: /metrics в per-tenant роутере
// (endpoint_builder.go) обязан быть закрыт тем же Bearer ADMIN_TOKEN, что и
// /admin/* и топ-левел /metrics в cmd/server/main.go — метрики отдают
// tenant-лейблы и счётчики. Fail-closed: без ADMIN_TOKEN — 401.

import (
	"database/sql"
	"net/http"
	"net/http/httptest"
	"testing"

	_ "modernc.org/sqlite"

	"github.com/trash2bin/helperium/data-service/internal/datasource"
	"github.com/trash2bin/helperium/data-service/internal/server"
	"github.com/trash2bin/helperium/helperium-go/config"
)

// setupMetricsRouter строит per-tenant роутер из минимальной конфигурации
// (одна entity + builtin health) на in-memory SQLite.
func setupMetricsRouter(t *testing.T) http.Handler {
	t.Helper()

	sqlDB, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatalf("open in-memory db: %v", err)
	}
	t.Cleanup(func() { _ = sqlDB.Close() })

	cfg := &config.Config{
		DataSource: config.DataSourceConfig{
			Driver:   "sqlite",
			ReadOnly: boolPtr(true),
		},
		Entities: []config.Entity{
			{
				Name:     "products",
				Table:    "products",
				IDColumn: "id",
				Fields: []config.EntityField{
					{Name: "id", Column: "id", Type: config.FieldTypeInt, Nullable: boolPtr(false), PrimaryKey: boolPtr(true)},
				},
			},
		},
		Endpoints: []config.Endpoint{
			{Method: "GET", Path: "/health", Op: "builtin_health"},
		},
	}

	adapter := &testSQLite{db: sqlDB}
	store := server.NewTenantStore(datasource.NewDefaultRegistry(), "")
	router, err := server.NewRouterFromConfig(store, cfg, adapter)
	if err != nil {
		t.Fatalf("NewRouterFromConfig: %v", err)
	}
	return router
}

func TestPerTenantRouter_Metrics_FailClosed_NoTokenConfigured(t *testing.T) {
	t.Setenv("ADMIN_TOKEN", "")

	router := setupMetricsRouter(t)
	req := httptest.NewRequest(http.MethodGet, "/metrics", nil)
	rec := httptest.NewRecorder()
	router.ServeHTTP(rec, req)

	if rec.Code != http.StatusUnauthorized {
		t.Errorf("per-tenant GET /metrics without ADMIN_TOKEN = %d, want 401 (fail-closed)", rec.Code)
	}
}

func TestPerTenantRouter_Metrics_NoBearer_Returns401(t *testing.T) {
	t.Setenv("ADMIN_TOKEN", "secret-token")

	router := setupMetricsRouter(t)
	req := httptest.NewRequest(http.MethodGet, "/metrics", nil)
	rec := httptest.NewRecorder()
	router.ServeHTTP(rec, req)

	if rec.Code != http.StatusUnauthorized {
		t.Errorf("per-tenant GET /metrics without Authorization = %d, want 401", rec.Code)
	}
}

func TestPerTenantRouter_Metrics_WrongToken_Returns401(t *testing.T) {
	t.Setenv("ADMIN_TOKEN", "secret-token")

	router := setupMetricsRouter(t)
	req := httptest.NewRequest(http.MethodGet, "/metrics", nil)
	req.Header.Set("Authorization", "Bearer wrong-token")
	rec := httptest.NewRecorder()
	router.ServeHTTP(rec, req)

	if rec.Code != http.StatusUnauthorized {
		t.Errorf("per-tenant GET /metrics with wrong token = %d, want 401", rec.Code)
	}
}

func TestPerTenantRouter_Metrics_ValidToken_Returns200(t *testing.T) {
	t.Setenv("ADMIN_TOKEN", "secret-token")

	router := setupMetricsRouter(t)
	req := httptest.NewRequest(http.MethodGet, "/metrics", nil)
	req.Header.Set("Authorization", "Bearer secret-token")
	rec := httptest.NewRecorder()
	router.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("per-tenant GET /metrics with valid token = %d, want 200 (body: %s)", rec.Code, rec.Body.String())
	}
	if rec.Body.String() == "" {
		t.Fatal("per-tenant GET /metrics with valid token returned empty body")
	}
}
