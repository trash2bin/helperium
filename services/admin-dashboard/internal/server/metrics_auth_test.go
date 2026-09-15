package server

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

// Pentest M1: /metrics на admin-dashboard требует валидный bearer-токен —
// метрики отдают tenant-лейблы и счётчики rate-limit (разведка).

func TestMetrics_RequiresAuth_NoToken(t *testing.T) {
	s := New(Options{Addr: ":0", AdminToken: "admin-tok", ViewerToken: "viewer-tok"})
	router := s.Router()

	w := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/metrics", nil)
	router.ServeHTTP(w, req)

	if w.Code != http.StatusUnauthorized {
		t.Errorf("GET /metrics without token = %d, want 401", w.Code)
	}
}

func TestMetrics_RejectsWrongToken(t *testing.T) {
	s := New(Options{Addr: ":0", AdminToken: "admin-tok", ViewerToken: "viewer-tok"})
	router := s.Router()

	w := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/metrics", nil)
	req.Header.Set("Authorization", "Bearer wrong-token")
	router.ServeHTTP(w, req)

	if w.Code != http.StatusUnauthorized {
		t.Errorf("GET /metrics with wrong token = %d, want 401", w.Code)
	}
}

func TestMetrics_AdminToken_Returns200(t *testing.T) {
	s := New(Options{Addr: ":0", AdminToken: "admin-tok", ViewerToken: "viewer-tok"})
	router := s.Router()

	w := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/metrics", nil)
	req.Header.Set("Authorization", "Bearer admin-tok")
	router.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("GET /metrics with admin token = %d, want 200 (body: %s)", w.Code, w.Body.String())
	}
	if w.Body.Len() == 0 {
		t.Fatal("GET /metrics with admin token returned empty body")
	}
}

func TestMetrics_ViewerToken_Returns200(t *testing.T) {
	s := New(Options{Addr: ":0", AdminToken: "admin-tok", ViewerToken: "viewer-tok"})
	router := s.Router()

	w := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/metrics", nil)
	req.Header.Set("Authorization", "Bearer viewer-tok")
	router.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("GET /metrics with viewer token = %d, want 200 (body: %s)", w.Code, w.Body.String())
	}
}
