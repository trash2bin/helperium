package handlers_test

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/trash2bin/helperium/data-service/internal/runtime/handlers"
	"github.com/trash2bin/helperium/helperium-go/config"
)

// TestMCPManifestHandler_WithTools — cfg.MCPTools уже заданы, но регенерация
// из Endpoints имеет приоритет. get_by_id по MCP-политике (Фаза 1) НЕ эмитится
// в манифест, даже если он есть в endpoints.
func TestMCPManifestHandler_WithTools(t *testing.T) {
	cfg := &config.Config{
		Endpoints: []config.Endpoint{
			{Path: "/students", Op: "get_by_id", Entity: "student", Method: "GET"},
		},
		Entities: []config.Entity{
			{
				Name: "student", Table: "students", IDColumn: "id",
				Fields: []config.EntityField{
					{Name: "id", Column: "id", Type: "int", PrimaryKey: boolPtr(true)},
				},
			},
		},
		CustomQueries: map[string]config.CustomQuery{},
		MCPTools: []config.MCPTool{
			{Name: "get_student", Description: "Get student by ID"},
		},
	}

	h := handlers.MCPManifestHandler(cfg)

	req := httptest.NewRequest(http.MethodGet, "/mcp/manifest", nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	body := w.Body.String()
	// MCP-политика: get_* не эмитится в манифест (анти-перебор).
	if strings.Contains(body, "get_student") {
		t.Errorf("get_student must NOT appear in MCP manifest (anti-enumeration policy): %s", body)
	}
	if !strings.Contains(body, `"endpoints"`) {
		t.Errorf("response should contain endpoints: %s", body)
	}
}

// TestMCPManifestHandler_GenerateTools — cfg.MCPTools пуст, генерируем из Endpoints
func TestMCPManifestHandler_GenerateTools(t *testing.T) {
	cfg := &config.Config{
		Endpoints: []config.Endpoint{
			{Path: "/students/{id}", Op: "get_by_id", Entity: "student", Method: "GET"},
			{Path: "/students/grep", Op: "strategy", Strategy: "grep", Entity: "student", Method: "GET"},
		},
		Entities: []config.Entity{
			{
				Name: "student", Table: "students", IDColumn: "id",
				Fields: []config.EntityField{
					{Name: "id", Column: "id", Type: "int", PrimaryKey: boolPtr(true)},
					{Name: "name", Column: "name", Type: "string"},
				},
			},
		},
		CustomQueries: map[string]config.CustomQuery{},
		MCPTools:      nil, // force generation
	}

	h := handlers.MCPManifestHandler(cfg)

	req := httptest.NewRequest(http.MethodGet, "/mcp/manifest", nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	body := w.Body.String()
	if !strings.Contains(body, "mcp_tools") {
		t.Errorf("response should contain mcp_tools: %s", body)
	}
	// Консолидированный тул генерируется (Фаза 2).
	if !strings.Contains(body, "db_search") {
		t.Errorf("response should contain db_search (consolidated tool): %s", body)
	}
	// get_by_id НЕ эмитится (анти-перебор).
	if strings.Contains(body, "get_student") {
		t.Errorf("get_student must NOT appear in MCP manifest (anti-enumeration policy): %s", body)
	}
}

// TestMCPManifestHandler_EmptyEndpoints — без endpoints возвращает пустые структуры
func TestMCPManifestHandler_EmptyEndpoints(t *testing.T) {
	cfg := &config.Config{
		Endpoints:     []config.Endpoint{},
		Entities:      []config.Entity{},
		CustomQueries: map[string]config.CustomQuery{},
		MCPTools:      []config.MCPTool{},
	}

	h := handlers.MCPManifestHandler(cfg)

	req := httptest.NewRequest(http.MethodGet, "/mcp/manifest", nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	body := w.Body.String()
	if !strings.Contains(body, `"entities"`) || !strings.Contains(body, `"mcp_tools"`) {
		t.Errorf("response should contain entities and mcp_tools: %s", body)
	}
}

// TestMCPManifestHandler_NilMCPTools — MCPTools == nil → генерируем
func TestMCPManifestHandler_NilMCPTools(t *testing.T) {
	cfg := &config.Config{
		Endpoints:     []config.Endpoint{},
		Entities:      []config.Entity{},
		CustomQueries: map[string]config.CustomQuery{},
		MCPTools:      nil,
	}

	h := handlers.MCPManifestHandler(cfg)

	req := httptest.NewRequest(http.MethodGet, "/mcp/manifest", nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	body := w.Body.String()
	if !strings.Contains(body, `"mcp_tools"`) {
		t.Errorf("response should contain mcp_tools even when nil: %s", body)
	}
}
