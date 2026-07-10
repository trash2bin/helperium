package server

import (
	"context"
	"database/sql"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/go-chi/chi/v5"
	_ "modernc.org/sqlite"

	"github.com/agent-tutor/agent-tutor-go/config"
	"github.com/agent-tutor/data-service/internal/datasource"
)

// newTenantAdminTestStore creates a TenantStore with one registered tenant for admin tests.
func newTenantAdminTestStore(t *testing.T) *TenantStore {
	t.Helper()
	registry := datasource.NewDefaultRegistry()

	db, err := sql.Open("sqlite", ":memory:?_journal_mode=WAL&_foreign_keys=on")
	if err != nil {
		t.Fatalf("open db: %v", err)
	}
	db.SetMaxOpenConns(1)
	if _, err := db.ExecContext(t.Context(),
		`CREATE TABLE groups (id TEXT PRIMARY KEY, name TEXT NOT NULL)`); err != nil {
		_ = db.Close()
		t.Fatalf("create table: %v", err)
	}

	cfg := &config.Config{
		Version: 1,
		DataSource: config.DataSourceConfig{
			Driver: config.DriverSQLite,
			DSN:    ":memory:",
		},
		Entities: []config.Entity{
			{Name: "group", Table: "groups", IDColumn: "id", Fields: []config.EntityField{
				{Name: "id", Column: "id", Type: config.FieldTypeString},
				{Name: "name", Column: "name", Type: config.FieldTypeString},
			}},
		},
		Endpoints: []config.Endpoint{
			{Method: "GET", Path: "/health", Op: config.OpBuiltinHealth},
			{Method: "GET", Path: "/groups", Op: config.OpList, Entity: "group"},
		},
	}

	ts := NewTenantStore(registry, "")
	ctx, cancel := context.WithTimeout(t.Context(), 5*time.Second)
	defer cancel()

	_, err = ts.AddTenant(ctx, "test-tenant", cfg, "")
	if err != nil {
		t.Fatalf("AddTenant: %v", err)
	}
	return ts
}

func TestTenantAdmin_AddTenant_Success(t *testing.T) {
	ts := newTenantAdminTestStore(t)
	ts.TenantsDir = t.TempDir()

	payload := `{
		"id": "new-tenant",
		"config": {"version": 1, "data_source": {"driver": "sqlite", "dsn": ":memory:"}, "entities": [], "endpoints": []}
	}`
	req := httptest.NewRequest(http.MethodPost, "/admin/tenants", strings.NewReader(payload))
	rec := httptest.NewRecorder()
	ts.adminAddTenantHandler(rec, req)

	if rec.Code != http.StatusCreated {
		t.Errorf("expected 201, got %d: %s", rec.Code, rec.Body.String())
	}
	var body map[string]any
	_ = json.Unmarshal(rec.Body.Bytes(), &body)
	if body["status"] != "created" {
		t.Errorf("expected status=created, got %v", body["status"])
	}
}

func TestTenantAdmin_AddTenant_MissingID(t *testing.T) {
	ts := newTenantAdminTestStore(t)
	payload := `{"config": {"version": 1, "data_source": {"driver": "sqlite", "dsn": ":memory:"}, "entities": [], "endpoints": []}}`
	req := httptest.NewRequest(http.MethodPost, "/admin/tenants", strings.NewReader(payload))
	rec := httptest.NewRecorder()
	ts.adminAddTenantHandler(rec, req)

	if rec.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d: %s", rec.Code, rec.Body.String())
	}
}

func TestTenantAdmin_AddTenant_Duplicate(t *testing.T) {
	ts := newTenantAdminTestStore(t)
	payload := `{
		"id": "test-tenant",
		"config": {"version": 1, "data_source": {"driver": "sqlite", "dsn": ":memory:"}, "entities": [], "endpoints": []}
	}`
	req := httptest.NewRequest(http.MethodPost, "/admin/tenants", strings.NewReader(payload))
	rec := httptest.NewRecorder()
	ts.adminAddTenantHandler(rec, req)

	if rec.Code != http.StatusConflict {
		t.Errorf("expected 409, got %d: %s", rec.Code, rec.Body.String())
	}
}

func TestTenantAdmin_RemoveTenant_Success(t *testing.T) {
	ts := newTenantAdminTestStore(t)
	ctx, cancel := context.WithTimeout(t.Context(), 5*time.Second)
	_, err := ts.AddTenant(ctx, "to-remove", &config.Config{
		Version: 1,
		DataSource: config.DataSourceConfig{Driver: config.DriverSQLite, DSN: ":memory:"},
	}, "")
	cancel()
	if err != nil {
		t.Fatalf("AddTenant: %v", err)
	}

	rctx := chi.NewRouteContext()
	rctx.URLParams.Add("id", "to-remove")
	req := httptest.NewRequest(http.MethodDelete, "/admin/tenants/to-remove", nil)
	req = req.WithContext(context.WithValue(req.Context(), chi.RouteCtxKey, rctx))
	rec := httptest.NewRecorder()
	ts.adminRemoveTenantHandler(rec, req)

	if rec.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", rec.Code, rec.Body.String())
	}
	_, ok := ts.GetTenant("to-remove")
	if ok {
		t.Error("tenant should be removed")
	}
}

func TestTenantAdmin_RemoveTenant_NotFound(t *testing.T) {
	ts := newTenantAdminTestStore(t)
	rctx := chi.NewRouteContext()
	rctx.URLParams.Add("id", "nonexistent")
	req := httptest.NewRequest(http.MethodDelete, "/admin/tenants/nonexistent", nil)
	req = req.WithContext(context.WithValue(req.Context(), chi.RouteCtxKey, rctx))
	rec := httptest.NewRecorder()
	ts.adminRemoveTenantHandler(rec, req)

	if rec.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d: %s", rec.Code, rec.Body.String())
	}
}

func TestTenantAdmin_RemoveTenant_EmptyID(t *testing.T) {
	ts := newTenantAdminTestStore(t)
	req := httptest.NewRequest(http.MethodDelete, "/admin/tenants/", nil)
	rec := httptest.NewRecorder()
	ts.adminRemoveTenantHandler(rec, req)

	if rec.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d: %s", rec.Code, rec.Body.String())
	}
}

func TestTenantAdmin_ListTenants(t *testing.T) {
	ts := newTenantAdminTestStore(t)
	req := httptest.NewRequest(http.MethodGet, "/admin/tenants", nil)
	rec := httptest.NewRecorder()
	ts.adminListTenantsHandler(rec, req)

	if rec.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", rec.Code, rec.Body.String())
	}
	var body map[string]any
	_ = json.Unmarshal(rec.Body.Bytes(), &body)
	tenants, _ := body["tenants"].([]any)
	if len(tenants) != 1 {
		t.Errorf("expected 1 tenant, got %d", len(tenants))
	}
}

func TestTenantAdmin_GetTenant_Success(t *testing.T) {
	ts := newTenantAdminTestStore(t)
	rctx := chi.NewRouteContext()
	rctx.URLParams.Add("id", "test-tenant")
	req := httptest.NewRequest(http.MethodGet, "/admin/tenants/test-tenant", nil)
	req = req.WithContext(context.WithValue(req.Context(), chi.RouteCtxKey, rctx))
	rec := httptest.NewRecorder()
	ts.adminGetTenantHandler(rec, req)

	if rec.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", rec.Code, rec.Body.String())
	}
	var body map[string]any
	_ = json.Unmarshal(rec.Body.Bytes(), &body)
	if body["id"] != "test-tenant" {
		t.Errorf("expected id=test-tenant, got %v", body["id"])
	}
}

func TestTenantAdmin_GetTenant_NotFound(t *testing.T) {
	ts := newTenantAdminTestStore(t)
	rctx := chi.NewRouteContext()
	rctx.URLParams.Add("id", "nonexistent")
	req := httptest.NewRequest(http.MethodGet, "/admin/tenants/nonexistent", nil)
	req = req.WithContext(context.WithValue(req.Context(), chi.RouteCtxKey, rctx))
	rec := httptest.NewRecorder()
	ts.adminGetTenantHandler(rec, req)

	if rec.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d: %s", rec.Code, rec.Body.String())
	}
}

func TestTenantAdmin_ConfigHandler_Success(t *testing.T) {
	ts := newTenantAdminTestStore(t)
	req := httptest.NewRequest(http.MethodGet, "/admin/config", nil)
	req.Header.Set("X-Tenant-ID", "test-tenant")
	rec := httptest.NewRecorder()
	ts.adminConfigHandler(rec, req)

	if rec.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", rec.Code, rec.Body.String())
	}
	var body map[string]any
	_ = json.Unmarshal(rec.Body.Bytes(), &body)
	if body["version"].(float64) != 1 {
		t.Errorf("expected version=1, got %v", body["version"])
	}
}

func TestTenantAdmin_ConfigHandler_NoTenant(t *testing.T) {
	ts := newTenantAdminTestStore(t)
	req := httptest.NewRequest(http.MethodGet, "/admin/config", nil)
	rec := httptest.NewRecorder()
	ts.adminConfigHandler(rec, req)
	if rec.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", rec.Code)
	}
}

func TestTenantAdmin_ConfigReload_NoTenant(t *testing.T) {
	ts := newTenantAdminTestStore(t)
	req := httptest.NewRequest(http.MethodPost, "/admin/config/reload", nil)
	rec := httptest.NewRecorder()
	ts.adminConfigReloadHandler(rec, req)
	if rec.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d: %s", rec.Code, rec.Body.String())
	}
}

func TestTenantAdmin_ConfigVersions_Success(t *testing.T) {
	ts := newTenantAdminTestStore(t)

	// Set ConfigPath so versions dir has a parent
	inst, ok := ts.GetTenant("test-tenant")
	if !ok {
		t.Fatal("tenant not found")
	}
	inst.ConfigPath = t.TempDir() + "/config.json"

	req := httptest.NewRequest(http.MethodGet, "/admin/config/versions", nil)
	req.Header.Set("X-Tenant-ID", "test-tenant")
	rec := httptest.NewRecorder()
	ts.adminConfigVersionsHandler(rec, req)

	if rec.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", rec.Code, rec.Body.String())
	}
}

func TestTenantAdmin_ConfigVersions_NoTenant(t *testing.T) {
	ts := newTenantAdminTestStore(t)
	req := httptest.NewRequest(http.MethodGet, "/admin/config/versions", nil)
	rec := httptest.NewRecorder()
	ts.adminConfigVersionsHandler(rec, req)
	if rec.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", rec.Code)
	}
}

func TestTenantAdmin_ConfigVersions_NoConfigPath(t *testing.T) {
	ts := newTenantAdminTestStore(t)
	req := httptest.NewRequest(http.MethodGet, "/admin/config/versions", nil)
	req.Header.Set("X-Tenant-ID", "test-tenant")
	rec := httptest.NewRecorder()
	ts.adminConfigVersionsHandler(rec, req)
	// Empty ConfigPath → versions dir doesn't exist → returns empty array
	if rec.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", rec.Code, rec.Body.String())
	}
}

func TestTenantAdmin_HealthHandler_Healthy(t *testing.T) {
	ts := newTenantAdminTestStore(t)
	req := httptest.NewRequest(http.MethodGet, "/health", nil)
	rec := httptest.NewRecorder()
	ts.multiTenantHealthHandler(rec, req)

	if rec.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", rec.Code, rec.Body.String())
	}
}

func TestTenantAdmin_HealthHandler_Empty(t *testing.T) {
	ts := NewTenantStore(datasource.NewDefaultRegistry(), "")
	req := httptest.NewRequest(http.MethodGet, "/health", nil)
	rec := httptest.NewRecorder()
	ts.multiTenantHealthHandler(rec, req)
	if rec.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503, got %d: %s", rec.Code, rec.Body.String())
	}
}

func TestTenantAdmin_PendingTools_NotFound(t *testing.T) {
	ts := newTenantAdminTestStore(t)
	rctx := chi.NewRouteContext()
	rctx.URLParams.Add("id", "nonexistent")
	req := httptest.NewRequest(http.MethodGet, "/admin/tenants/nonexistent/tools/pending", nil)
	req = req.WithContext(context.WithValue(req.Context(), chi.RouteCtxKey, rctx))
	rec := httptest.NewRecorder()
	ts.adminTenantPendingToolsHandler(rec, req)
	if rec.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d", rec.Code)
	}
}

func TestTenantAdmin_PendingTools_Success(t *testing.T) {
	ts := newTenantAdminTestStore(t)
	rctx := chi.NewRouteContext()
	rctx.URLParams.Add("id", "test-tenant")
	req := httptest.NewRequest(http.MethodGet, "/admin/tenants/test-tenant/tools/pending", nil)
	req = req.WithContext(context.WithValue(req.Context(), chi.RouteCtxKey, rctx))
	rec := httptest.NewRecorder()
	ts.adminTenantPendingToolsHandler(rec, req)
	if rec.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", rec.Code, rec.Body.String())
	}
}

func TestTenantAdmin_ApproveTool_NotFound(t *testing.T) {
	ts := newTenantAdminTestStore(t)
	rctx := chi.NewRouteContext()
	rctx.URLParams.Add("id", "nonexistent")
	rctx.URLParams.Add("toolName", "some_tool")
	req := httptest.NewRequest(http.MethodPost, "/admin/tenants/nonexistent/tools/some_tool/approve", nil)
	req = req.WithContext(context.WithValue(req.Context(), chi.RouteCtxKey, rctx))
	rec := httptest.NewRecorder()
	ts.adminTenantApproveToolHandler(rec, req)
	if rec.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d", rec.Code)
	}
}

func TestTenantAdmin_ApproveTool_Success(t *testing.T) {
	ts := newTenantAdminTestStore(t)
	inst, ok := ts.GetTenant("test-tenant")
	if !ok {
		t.Fatal("tenant not found")
	}
	inst.Config.Endpoints = append(inst.Config.Endpoints,
		config.Endpoint{Method: "POST", Path: "/groups", Op: config.OpCustomQuery, QueryID: "create_group"},
	)

	rctx := chi.NewRouteContext()
	rctx.URLParams.Add("id", "test-tenant")
	rctx.URLParams.Add("toolName", "query_create_group")
	req := httptest.NewRequest(http.MethodPost, "/admin/tenants/test-tenant/tools/query_create_group/approve", nil)
	req = req.WithContext(context.WithValue(req.Context(), chi.RouteCtxKey, rctx))
	rec := httptest.NewRecorder()
	ts.adminTenantApproveToolHandler(rec, req)

	if rec.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", rec.Code, rec.Body.String())
	}
	if !inst.ApprovedTools["/groups"] {
		t.Error("expected /groups to be approved")
	}
}

func TestTenantAdmin_BuildAdminRouter(t *testing.T) {
	ts := newTenantAdminTestStore(t)
	router := ts.BuildAdminRouter(nil, "", nil, nil)
	if router == nil {
		t.Fatal("BuildAdminRouter returned nil")
	}
}

func TestTenantAdmin_DiscoverHandler_NoTenant(t *testing.T) {
	ts := newTenantAdminTestStore(t)
	req := httptest.NewRequest(http.MethodGet, "/admin/discover", nil)
	rec := httptest.NewRecorder()
	ts.adminDiscoverHandler(nil)(rec, req)
	if rec.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d: %s", rec.Code, rec.Body.String())
	}
}

func TestTenantAdmin_DiscoverHandler_NilAdapter(t *testing.T) {
	t.Skip("Discover handler requires a non-nil introspect adapter; needs test infrastructure")
}
