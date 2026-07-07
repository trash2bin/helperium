package handlers_test

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/agent-tutor/data-service/internal/runtime/handlers"
)

// TestRespondJSON — базовый тест RespondJSON
func TestRespondJSON(t *testing.T) {
	w := httptest.NewRecorder()
	handlers.RespondJSON(w, http.StatusOK, map[string]string{"status": "ok"})

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
	if ct := w.Header().Get("Content-Type"); ct != "application/json" {
		t.Errorf("expected application/json, got %s", ct)
	}
	body := strings.TrimSpace(w.Body.String())
	if body != `{"status":"ok"}` {
		t.Errorf("unexpected body: %s", body)
	}
}

// TestRespondJSON_WithStatus — разные status codes
func TestRespondJSON_WithStatus(t *testing.T) {
	tests := []struct {
		status int
		name   string
	}{
		{http.StatusCreated, "created"},
		{http.StatusBadRequest, "bad request"},
		{http.StatusInternalServerError, "server error"},
		{http.StatusNotFound, "not found"},
	}

	for _, tt := range tests {
		w := httptest.NewRecorder()
		handlers.RespondJSON(w, tt.status, map[string]string{"status": tt.name})

		if w.Code != tt.status {
			t.Errorf("%s: expected %d, got %d", tt.name, tt.status, w.Code)
		}
	}
}

// TestRespondJSON_Array — ответ в виде массива
func TestRespondJSON_Array(t *testing.T) {
	w := httptest.NewRecorder()
	handlers.RespondJSON(w, http.StatusOK, []map[string]int{{"count": 1}, {"count": 2}})

	body := strings.TrimSpace(w.Body.String())
	if !strings.HasPrefix(body, "[") || !strings.HasSuffix(body, "]") {
		t.Errorf("expected JSON array, got: %s", body)
	}
}

// TestRespondJSON_NilBody — nil body → "null"
func TestRespondJSON_NilBody(t *testing.T) {
	w := httptest.NewRecorder()
	handlers.RespondJSON(w, http.StatusOK, nil)

	body := strings.TrimSpace(w.Body.String())
	if body != "null\n" && body != "null" {
		t.Errorf("expected null for nil body, got: %s", body)
	}
}

// TestRespondError — формат ошибки
func TestRespondError(t *testing.T) {
	w := httptest.NewRecorder()
	handlers.RespondError(w, http.StatusBadRequest, "bad_request", "invalid input")

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}

	var resp map[string]string
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("failed to parse response: %v", err)
	}
	if resp["error"] != "bad_request" {
		t.Errorf("expected error=bad_request, got %s", resp["error"])
	}
	if resp["message"] != "invalid input" {
		t.Errorf("expected message=invalid input, got %s", resp["message"])
	}
}

// TestRespondError_DifferentStatuses — ошибки с разными статусами
func TestRespondError_DifferentStatuses(t *testing.T) {
	statuses := []struct {
		code    int
		message string
	}{
		{http.StatusNotFound, "not found"},
		{http.StatusInternalServerError, "server error"},
		{http.StatusForbidden, "forbidden"},
		{http.StatusConflict, "conflict"},
	}

	for _, s := range statuses {
		w := httptest.NewRecorder()
		handlers.RespondError(w, s.code, "error_"+s.message, s.message)
		if w.Code != s.code {
			t.Errorf("expected %d, got %d", s.code, w.Code)
		}
	}
}

// TestRespondJSON_ContentTypeAlways — Content-Type всегда application/json
func TestRespondJSON_ContentTypeAlways(t *testing.T) {
	w := httptest.NewRecorder()
	handlers.RespondJSON(w, http.StatusOK, "plain string")
	if ct := w.Header().Get("Content-Type"); ct != "application/json" {
		t.Errorf("expected application/json, got %s", ct)
	}
}
