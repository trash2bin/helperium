package server_test

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/trash2bin/helperium/data-service/internal/server"
)

// TestSwaggerHandler — возвращает HTML с Swagger UI
func TestSwaggerHandler(t *testing.T) {
	w := httptest.NewRecorder()
	r := httptest.NewRequest(http.MethodGet, "/docs", nil)

	server.SwaggerHandler(w, r)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
	body := w.Body.String()
	if !strings.Contains(body, "swagger") && !strings.Contains(body, "Swagger") {
		t.Errorf("response should contain swagger UI: %s", body[:min(len(body), 200)])
	}
}

// TestNewOpenAPIHandler_NoTenant — без tenant возвращает system spec
func TestNewOpenAPIHandler_NoTenant(t *testing.T) {
	store := server.NewTenantStore(nil, "")
	h := server.NewOpenAPIHandler(store, false)

	w := httptest.NewRecorder()
	r := httptest.NewRequest(http.MethodGet, "/openapi.json", nil)
	h.ServeHTTP(w, r)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
	if ct := w.Header().Get("Content-Type"); ct != "application/json" {
		t.Errorf("expected application/json, got %s", ct)
	}
	if acao := w.Header().Get("Access-Control-Allow-Origin"); acao != "*" {
		t.Errorf("expected Access-Control-Allow-Origin: *, got %s", acao)
	}

	// Проверяем, что это валидный JSON c openapi
	var spec map[string]any
	if err := json.Unmarshal(w.Body.Bytes(), &spec); err != nil {
		t.Fatalf("invalid JSON: %v", err)
	}
	if spec["openapi"] == nil {
		t.Errorf("response missing 'openapi' field: %v", spec)
	}
}

// TestNewOpenAPIHandler_WithTenant — с tenant возвращает spec с эндпоинтами
func TestNewOpenAPIHandler_WithTenant(t *testing.T) {
	store := server.NewTenantStore(nil, "")
	h := server.NewOpenAPIHandler(store, true)

	w := httptest.NewRecorder()
	r := httptest.NewRequest(http.MethodGet, "/openapi.json?tenant=test", nil)
	h.ServeHTTP(w, r)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}

	var spec map[string]any
	if err := json.Unmarshal(w.Body.Bytes(), &spec); err != nil {
		t.Fatalf("invalid JSON: %v", err)
	}
	if spec["openapi"] == nil {
		t.Errorf("response missing 'openapi' field")
	}
}

// TestNewOpenAPIHandler_WithAdmin — с hasAdmin=true включает admin paths
func TestNewOpenAPIHandler_WithAdmin(t *testing.T) {
	store := server.NewTenantStore(nil, "")
	h := server.NewOpenAPIHandler(store, true)

	w := httptest.NewRecorder()
	r := httptest.NewRequest(http.MethodGet, "/openapi.json?tenant=test", nil)
	h.ServeHTTP(w, r)

	var spec map[string]any
	_ = json.Unmarshal(w.Body.Bytes(), &spec)

	paths, ok := spec["paths"].(map[string]any)
	if !ok {
		t.Fatalf("paths is not an object")
	}

	// With hasAdmin=true, there should be system paths like /health
	hasHealth := false
	for p := range paths {
		if strings.Contains(p, "health") {
			hasHealth = true
			break
		}
	}
	if !hasHealth {
		t.Errorf("expected /health path in spec with hasAdmin=true: %v", spec["paths"])
	}
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}

// TestSwaggerHandlerWithTenant — добавляет tenant в URL
func TestSwaggerHandlerWithTenant(t *testing.T) {
	store := server.NewTenantStore(nil, "")
	h := server.SwaggerHandlerWithTenant(store, "test-tenant")

	w := httptest.NewRecorder()
	r := httptest.NewRequest(http.MethodGet, "/docs", nil)
	h.ServeHTTP(w, r)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
	body := w.Body.String()
	if !strings.Contains(body, "swagger") && !strings.Contains(body, "Swagger") {
		t.Errorf("response should contain swagger UI")
	}
}
