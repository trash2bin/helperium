package main

import (
	"net/http"
	"net/http/httptest"
	"os"
	"testing"
)

// Pentest M1: /metrics должен быть закрыт тем же Bearer ADMIN_TOKEN,
// что и /admin/* — метрики отдают tenant-лейблы и счётчики rate-limit,
// это разведка для атакующего. Fail-closed: без ADMIN_TOKEN — 401.

func TestMetricsEndpoint_FailClosed_NoTokenConfigured(t *testing.T) {
	if tok, ok := os.LookupEnv("ADMIN_TOKEN"); ok {
		defer os.Setenv("ADMIN_TOKEN", tok)
	}
	os.Unsetenv("ADMIN_TOKEN")

	req := httptest.NewRequest(http.MethodGet, "/metrics", nil)
	rec := httptest.NewRecorder()
	metricsHandler().ServeHTTP(rec, req)

	if rec.Code != http.StatusUnauthorized {
		t.Errorf("GET /metrics without ADMIN_TOKEN = %d, want 401 (fail-closed)", rec.Code)
	}
}

func TestMetricsEndpoint_NoBearer_Returns401(t *testing.T) {
	os.Setenv("ADMIN_TOKEN", "secret-token")
	defer os.Unsetenv("ADMIN_TOKEN")

	req := httptest.NewRequest(http.MethodGet, "/metrics", nil)
	rec := httptest.NewRecorder()
	metricsHandler().ServeHTTP(rec, req)

	if rec.Code != http.StatusUnauthorized {
		t.Errorf("GET /metrics without Authorization = %d, want 401", rec.Code)
	}
}

func TestMetricsEndpoint_WrongToken_Returns401(t *testing.T) {
	os.Setenv("ADMIN_TOKEN", "secret-token")
	defer os.Unsetenv("ADMIN_TOKEN")

	req := httptest.NewRequest(http.MethodGet, "/metrics", nil)
	req.Header.Set("Authorization", "Bearer wrong-token")
	rec := httptest.NewRecorder()
	metricsHandler().ServeHTTP(rec, req)

	if rec.Code != http.StatusUnauthorized {
		t.Errorf("GET /metrics with wrong token = %d, want 401", rec.Code)
	}
}

func TestMetricsEndpoint_ValidToken_Returns200(t *testing.T) {
	os.Setenv("ADMIN_TOKEN", "secret-token")
	defer os.Unsetenv("ADMIN_TOKEN")

	req := httptest.NewRequest(http.MethodGet, "/metrics", nil)
	req.Header.Set("Authorization", "Bearer secret-token")
	rec := httptest.NewRecorder()
	metricsHandler().ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("GET /metrics with valid token = %d, want 200 (body: %s)", rec.Code, rec.Body.String())
	}
	body := rec.Body.String()
	if len(body) == 0 {
		t.Fatal("GET /metrics with valid token returned empty body")
	}
}
