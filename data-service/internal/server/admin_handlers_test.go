package server

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/agent-tutor/agent-tutor-go/config"
	"github.com/go-chi/chi/v5"
)

// ── adminConfigReloadHandler ──

func TestAdminConfigReloadHandler_ReloadFnNil(t *testing.T) {
	ctx := &AdminContext{ReloadFn: nil}
	handler := adminConfigReloadHandler(ctx)
	req := httptest.NewRequest(http.MethodPost, "/admin/config/reload", nil)
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	if rec.Code != http.StatusInternalServerError {
		t.Errorf("expected 500, got %d", rec.Code)
	}
	var body map[string]string
	_ = json.Unmarshal(rec.Body.Bytes(), &body)
	if body["error"] != "reload_disabled" {
		t.Errorf("expected reload_disabled error, got %q", body["error"])
	}
}

func TestAdminConfigReloadHandler_Success(t *testing.T) {
	reloadCalled := false
	ctx := &AdminContext{
		ReloadFn: func(path string) error {
			reloadCalled = true
			return nil
		},
	}
	handler := adminConfigReloadHandler(ctx)
	req := httptest.NewRequest(http.MethodPost, "/admin/config/reload", nil)
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", rec.Code, rec.Body.String())
	}
	if !reloadCalled {
		t.Error("reloadFn should have been called")
	}
}

// ── adminPendingToolsHandler ──

func TestAdminPendingToolsHandler_ReadWriteMode(t *testing.T) {
	readOnly := false
	cfg := &config.Config{
		DataSource: config.DataSourceConfig{ReadOnly: &readOnly},
	}
	approved := map[string]bool{}

	handler := adminPendingToolsHandler(cfg, approved)
	req := httptest.NewRequest(http.MethodGet, "/admin/tools/pending", nil)
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", rec.Code)
	}
	var body map[string]any
	_ = json.Unmarshal(rec.Body.Bytes(), &body)
	if body["mode"] != "read_write" {
		t.Errorf("expected mode=read_write, got %v", body["mode"])
	}
}

func TestAdminPendingToolsHandler_ReadOnlyMode_NoWriteEndpoints(t *testing.T) {
	readOnly := true
	cfg := &config.Config{
		DataSource: config.DataSourceConfig{ReadOnly: &readOnly},
		Endpoints: []config.Endpoint{
			{Method: "GET", Path: "/health", Op: config.OpBuiltinHealth},
		},
	}
	approved := map[string]bool{}

	handler := adminPendingToolsHandler(cfg, approved)
	req := httptest.NewRequest(http.MethodGet, "/admin/tools/pending", nil)
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	var body map[string]any
	_ = json.Unmarshal(rec.Body.Bytes(), &body)
	if body["mode"] != "read_only" {
		t.Errorf("expected mode=read_only, got %v", body["mode"])
	}
	tools, _ := body["tools"].([]any)
	if len(tools) != 0 {
		t.Errorf("expected 0 pending tools, got %d", len(tools))
	}
}

func TestAdminPendingToolsHandler_ReadOnlyMode_WithWriteEndpoints(t *testing.T) {
	readOnly := true
	cfg := &config.Config{
		DataSource: config.DataSourceConfig{ReadOnly: &readOnly},
		Endpoints: []config.Endpoint{
			{Method: "GET", Path: "/groups", Op: config.OpList, Entity: "group"},
			{Method: "POST", Path: "/groups", Op: config.OpCustomQuery, QueryID: "create_group"},
		},
	}
	approved := map[string]bool{}

	handler := adminPendingToolsHandler(cfg, approved)
	req := httptest.NewRequest(http.MethodGet, "/admin/tools/pending", nil)
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	var body map[string]any
	_ = json.Unmarshal(rec.Body.Bytes(), &body)
	if body["mode"] != "read_only" {
		t.Errorf("expected mode=read_only, got %v", body["mode"])
	}
	tools, _ := body["tools"].([]any)
	if len(tools) != 1 {
		t.Errorf("expected 1 pending tool, got %d", len(tools))
	}
	pending, _ := body["pending"].(float64)
	if pending != 1 {
		t.Errorf("expected pending=1, got %v", pending)
	}
}

// ── adminApproveToolHandler ──

func TestAdminApproveToolHandler_Success(t *testing.T) {
	readOnly := true
	cfg := &config.Config{
		DataSource: config.DataSourceConfig{ReadOnly: &readOnly},
		Endpoints: []config.Endpoint{
			{Method: "POST", Path: "/groups", Op: config.OpCustomQuery, QueryID: "create_group"},
		},
	}
	approved := map[string]bool{}
	persistCalled := false

	handler := adminApproveToolHandler(cfg, approved, func() error {
		persistCalled = true
		return nil
	})

	rctx := chi.NewRouteContext()
	rctx.URLParams.Add("toolName", "query_create_group")
	req := httptest.NewRequest(http.MethodPost, "/admin/tools/query_create_group/approve", nil)
	req = req.WithContext(context.WithValue(req.Context(), chi.RouteCtxKey, rctx))
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", rec.Code, rec.Body.String())
	}
	if !approved["/groups"] {
		t.Error("expected /groups to be approved")
	}
	if !persistCalled {
		t.Error("expected persistFn to be called")
	}
}

func TestAdminApproveToolHandler_EmptyToolName(t *testing.T) {
	cfg := &config.Config{Endpoints: []config.Endpoint{}}
	approved := map[string]bool{}

	handler := adminApproveToolHandler(cfg, approved, nil)
	req := httptest.NewRequest(http.MethodPost, "/admin/tools//approve", nil)
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	if rec.Code != http.StatusBadRequest {
		t.Errorf("expected 400 empty toolName, got %d: %s", rec.Code, rec.Body.String())
	}
}

func TestAdminApproveToolHandler_ToolNotFound(t *testing.T) {
	cfg := &config.Config{
		Endpoints: []config.Endpoint{
			{Method: "GET", Path: "/health", Op: config.OpBuiltinHealth},
		},
	}
	approved := map[string]bool{}

	handler := adminApproveToolHandler(cfg, approved, nil)
	rctx := chi.NewRouteContext()
	rctx.URLParams.Add("toolName", "nonexistent_tool")
	req := httptest.NewRequest(http.MethodPost, "/admin/tools/nonexistent_tool/approve", nil)
	req = req.WithContext(context.WithValue(req.Context(), chi.RouteCtxKey, rctx))
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	if rec.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d: %s", rec.Code, rec.Body.String())
	}
}

// ── computeOverallStatus ──

func TestComputeOverallStatus(t *testing.T) {
	tests := []struct {
		name   string
		health []TenantHealth
		want   string
	}{
		{"empty", []TenantHealth{}, "unhealthy"},
		{"all healthy", []TenantHealth{{Status: "healthy"}, {Status: "healthy"}}, "healthy"},
		{"all unhealthy", []TenantHealth{{Status: "unhealthy", Error: "err"}}, "unhealthy"},
		{"mixed", []TenantHealth{{Status: "healthy"}, {Status: "unhealthy", Error: "err"}}, "degraded"},
		{"single healthy", []TenantHealth{{Status: "healthy"}}, "healthy"},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			got := computeOverallStatus(tc.health)
			if got != tc.want {
				t.Errorf("computeOverallStatus(%+v) = %q, want %q", tc.health, got, tc.want)
			}
		})
	}
}
