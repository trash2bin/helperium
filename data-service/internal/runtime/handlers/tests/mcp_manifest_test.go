package handlers_test

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/agent-tutor/agent-tutor-go/config"
	"github.com/agent-tutor/data-service/internal/runtime/handlers"
)

// TestMCPManifestHandler_WithTools — cfg.MCPTools уже заданы
func TestMCPManifestHandler_WithTools(t *testing.T) {
	cfg := &config.Config{
		Endpoints: []config.Endpoint{
			{Path: "/students", Op: "list", Entity: "student", Method: "GET"},
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
			{Name: "list_students", Description: "List all students"},
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
	if !strings.Contains(body, "list_students") {
		t.Errorf("response should contain list_students: %s", body)
	}
	if !strings.Contains(body, `"endpoints"`) {
		t.Errorf("response should contain endpoints: %s", body)
	}
}

// TestMCPManifestHandler_GenerateTools — cfg.MCPTools пуст, генерируем из Endpoints
func TestMCPManifestHandler_GenerateTools(t *testing.T) {
	cfg := &config.Config{
		Endpoints: []config.Endpoint{
			{Path: "/students", Op: "list", Entity: "student", Method: "GET"},
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
	// Должен быть сгенерирован tool из endpoint
	if !strings.Contains(body, "GET") && !strings.Contains(body, "get") {
		t.Errorf("response should contain generated tool: %s", body)
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
