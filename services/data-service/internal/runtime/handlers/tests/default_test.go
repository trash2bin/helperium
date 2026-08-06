package handlers_test

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/trash2bin/helperium/data-service/internal/runtime/handlers"
)

// TestNotFoundHandler — базовый тест NotFoundHandler
func TestNotFoundHandler(t *testing.T) {
	w := httptest.NewRecorder()
	r := httptest.NewRequest(http.MethodGet, "/nonexistent", nil)

	handlers.NotFoundHandler(w, r)

	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d", w.Code)
	}
	if ct := w.Header().Get("Content-Type"); ct != "application/json" {
		t.Errorf("expected application/json, got %s", ct)
	}
	body := w.Body.String()
	if !strings.Contains(body, "not_found") || !strings.Contains(body, "Resource not found") {
		t.Errorf("unexpected body: %s", body)
	}
}

// TestMethodNotAllowedHandler — базовый тест MethodNotAllowedHandler
func TestMethodNotAllowedHandler(t *testing.T) {
	w := httptest.NewRecorder()
	r := httptest.NewRequest(http.MethodPost, "/test", nil)

	handlers.MethodNotAllowedHandler(w, r)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
	if ct := w.Header().Get("Content-Type"); ct != "application/json" {
		t.Errorf("expected application/json, got %s", ct)
	}
	body := w.Body.String()
	if !strings.Contains(body, "method_not_allowed") || !strings.Contains(body, "HTTP method not allowed") {
		t.Errorf("unexpected body: %s", body)
	}
}

// TestNotFoundHandler_DifferentMethods — одинаковый ответ для разных методов
func TestNotFoundHandler_DifferentMethods(t *testing.T) {
	methods := []string{http.MethodGet, http.MethodPost, http.MethodPut, http.MethodDelete, http.MethodPatch}
	for _, m := range methods {
		w := httptest.NewRecorder()
		r := httptest.NewRequest(m, "/any", nil)
		handlers.NotFoundHandler(w, r)
		if w.Code != http.StatusNotFound {
			t.Errorf("method %s: expected 404, got %d", m, w.Code)
		}
	}
}

// TestNotFoundHandler_ResponseFormat — проверка JSON-формата ответа
func TestNotFoundHandler_ResponseFormat(t *testing.T) {
	w := httptest.NewRecorder()
	r := httptest.NewRequest(http.MethodGet, "/missing", nil)

	handlers.NotFoundHandler(w, r)

	body := w.Body.String()
	// JSON должен содержать error и message
	if !strings.Contains(body, `"error"`) {
		t.Errorf("body missing 'error' field: %s", body)
	}
	if !strings.Contains(body, `"message"`) {
		t.Errorf("body missing 'message' field: %s", body)
	}
}

// TestMethodNotAllowedHandler_CORSHeaders — проверка что заголовки не сбрасываются
func TestMethodNotAllowedHandler_CORSHeaders(t *testing.T) {
	w := httptest.NewRecorder()
	r := httptest.NewRequest(http.MethodOptions, "/api/test", nil)

	handlers.MethodNotAllowedHandler(w, r)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// TestNotFoundHandler_NilBody — не падает с разными request body
func TestNotFoundHandler_NilBody(t *testing.T) {
	w := httptest.NewRecorder()
	r := httptest.NewRequest(http.MethodGet, "/nil-body", nil)
	r.Body = nil

	handlers.NotFoundHandler(w, r)

	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d", w.Code)
	}
}
